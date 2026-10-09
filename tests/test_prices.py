"""Tests for scripts/fetch_prices.py (offline; FRED fixtures in tests/fixtures/fred).

Every reference number below is a real FRED observation (or derived from one) — see the brief,
§5.1/§5.3: 2026-10-06 Brent 125.44, WTI 96.24, ULSD 4.713, gasoline 3.396, jet 4.342 → diesel crack
72.51, gasoline crack 17.19, 3-2-1 64.83; peak diesel crack 116.5 on 2022-05-11.
"""
from __future__ import annotations

import copy
import os
from datetime import date, datetime, timedelta, timezone

import jsonschema
import pytest

import common
import fetch_prices as fp

AS_OF = "2026-10-06"
RETAIL_AS_OF = "2026-10-05"
FETCHED_OLD = "2026-10-07T03:17:05Z"


@pytest.fixture(scope="module")
def fixture_doc(cfg_module, now_module):
    """One fixture-mode run shared by the read-only tests."""
    doc, info = fp.run(cfg_module, None, fixtures=True, now=now_module)
    return doc, info


@pytest.fixture(scope="module")
def cfg_module():
    return common.load_config()


@pytest.fixture(scope="module")
def now_module():
    return datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)   # same instant as conftest's ``now``


def _fixture_text(fixtures_dir, series_id):
    return (fixtures_dir / "fred" / ("%s.csv" % series_id)).read_text(encoding="utf-8")


class _Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


def _serve_fixtures(monkeypatch, fixtures_dir, failing=(), transform=None):
    """Replace common.http_get with a stub that serves the fixture CSVs (and fails for ``failing``)."""
    calls = []

    def fake_http_get(url, *, params=None, **kwargs):
        series_id = (params or {}).get("id")
        calls.append((url, series_id, kwargs))
        assert url == fp.FRED_CSV_URL
        if series_id in failing:
            raise common.FetchError("HTTP 503 after 3 attempts for %s?id=%s" % (url, series_id))
        text = _fixture_text(fixtures_dir, series_id)
        if transform is not None:
            text = transform(series_id, text)
        return _Resp(text)

    monkeypatch.setattr(common, "http_get", fake_http_get)
    return calls


# ------------------------------------------------------------------------------- CSV parsing

def test_parse_fred_csv_handles_both_missing_markers_and_headers():
    text = "observation_date,DDFUELNYH\n2026-10-02,4.674\n2026-10-05,.\n2026-10-06,4.713\n"
    assert fp.parse_fred_csv(text, "DDFUELNYH") == [("2026-10-02", 4.674), ("2026-10-05", None), ("2026-10-06", 4.713)]
    # the current fredgraph.csv format leaves the field empty instead of "."; old header style "DATE"
    legacy = "﻿DATE,DCOILBRENTEU\r\n2026-10-05,125.51\r\n2026-10-06,\r\n"
    assert fp.parse_fred_csv(legacy, "DCOILBRENTEU") == [("2026-10-05", 125.51), ("2026-10-06", None)]
    assert fp.values_of(fp.parse_fred_csv(legacy)) == {"2026-10-05": 125.51}
    assert fp.latest_observation(fp.parse_fred_csv(legacy)) == ("2026-10-05", 125.51)


@pytest.mark.parametrize("bad", [
    "",
    "<html><body>blocked</body></html>",
    "observation_date,DCOILWTICO\n2026-10-06,96.24\n",         # wrong series
    "observation_date,DCOILBRENTEU\n",                          # no observations
    "observation_date,DCOILBRENTEU\n06.10.2026,125.44\n",       # bad date
])
def test_parse_fred_csv_rejects_garbage(bad):
    with pytest.raises(ValueError):
        fp.parse_fred_csv(bad, "DCOILBRENTEU")


def test_fixture_files_are_real_and_trimmed(fixtures_dir):
    for key, spec in fp.SERIES:
        obs = fp.parse_fred_csv(_fixture_text(fixtures_dir, spec["id"]), spec["id"])
        assert obs[0][0] >= "2006-06-01"
        assert obs[-1][0] == (RETAIL_AS_OF if spec["freq"] == "weekly" else AS_OF)
    total = sum(os.path.getsize(fixtures_dir / "fred" / ("%s.csv" % spec["id"])) for _, spec in fp.SERIES)
    assert total < 700 * 1024
    # the gaps are kept: 2026-07-03 (US holiday) has Brent but no NY Harbor products
    ulsd = dict(fp.parse_fred_csv(_fixture_text(fixtures_dir, "DDFUELNYH")))
    brent = dict(fp.parse_fred_csv(_fixture_text(fixtures_dir, "DCOILBRENTEU")))
    assert "2026-07-03" in ulsd and ulsd["2026-07-03"] is None
    assert brent["2026-07-03"] is not None


# --------------------------------------------------------------------------------- formulas

def test_crack_formulas_on_2026_10_06(fixture_doc):
    doc, info = fixture_doc
    latest = doc["latest"]
    assert latest["diesel_crack"]["value"] == pytest.approx(72.51, abs=0.01)
    assert latest["gasoline_crack"]["value"] == pytest.approx(17.19, abs=0.01)
    assert latest["crack_321"]["value"] == pytest.approx(64.83, abs=0.01)
    assert latest["jet_crack"]["value"] == pytest.approx(4.342 * 42 - 125.44, abs=0.001)
    assert latest["brent_wti_spread"]["value"] == pytest.approx(125.44 - 96.24, abs=0.001)
    for key in ("diesel_crack", "gasoline_crack", "jet_crack", "crack_321", "brent_wti_spread"):
        dp = latest[key]
        assert dp["as_of"] == AS_OF and dp["unit"] == "USD/bbl" and dp["stale"] is False
        assert dp["formula"] and dp["inputs"]
    assert latest["diesel_crack"]["formula"] == "DDFUELNYH*42 - DCOILBRENTEU"
    assert latest["diesel_crack"]["inputs"] == {"DDFUELNYH": 4.713, "DCOILBRENTEU": 125.44}
    assert latest["crack_321"]["inputs"] == {"DGASNYH": 3.396, "DDFUELNYH": 4.713, "DCOILWTICO": 96.24}
    assert info["partial"] == [] and info["via"] == "fred"


def test_latest_series_values_and_as_of(fixture_doc):
    doc, _ = fixture_doc
    latest = doc["latest"]
    expected = {"brent": (125.44, "USD/bbl", AS_OF), "wti": (96.24, "USD/bbl", AS_OF),
                "ulsd_nyh": (4.713, "USD/gal", AS_OF), "gasoline_nyh": (3.396, "USD/gal", AS_OF),
                "jet_gulf": (4.342, "USD/gal", AS_OF), "heating_oil_nyh": (4.503, "USD/gal", AS_OF),
                "retail_diesel_us": (6.199, "USD/gal", RETAIL_AS_OF), "retail_gasoline_us": (4.354, "USD/gal", RETAIL_AS_OF)}
    for key, (value, unit, as_of) in expected.items():
        dp = latest[key]
        assert dp["value"] == pytest.approx(value, abs=0.0005), key
        assert dp["unit"] == unit and dp["as_of"] == as_of and dp["stale"] is False
        assert dp["series_id"] == fp.SERIES_BY_KEY[key]["id"]
        assert dp["source_url"] == "https://fred.stlouisfed.org/series/%s" % dp["series_id"]
        assert dp["fetched_at"] == "2026-10-08T10:00:00Z"
    # the weekly retail series keep their own Monday date and the weekly stale rule
    assert latest["retail_diesel_us"]["stale_kind"] == "weekly"
    assert latest["retail_gasoline_us"]["stale_kind"] == "weekly"
    assert "stale_kind" not in latest["brent"]
    assert doc["as_of"] == AS_OF
    assert doc["fetched_at"] == doc["generated_at"] == "2026-10-08T10:00:00Z"
    assert doc["stale"] is False


def test_missing_input_yields_no_crack(fixtures_dir, fixture_doc):
    # synthetic gap built from real rows: ULSD "." on 2026-10-05 → no diesel crack that day
    ulsd = fp.parse_fred_csv("observation_date,DDFUELNYH\n2026-10-02,4.674\n2026-10-05,.\n2026-10-06,4.713\n")
    brent = fp.parse_fred_csv("observation_date,DCOILBRENTEU\n2026-10-02,135.51\n2026-10-05,125.51\n2026-10-06,125.44\n")
    values = {"DDFUELNYH": fp.values_of(ulsd), "DCOILBRENTEU": fp.values_of(brent)}
    spec = fp.CRACKS_BY_KEY["diesel_crack"]
    cracks = fp.compute_crack(values, spec["inputs"], spec["fn"])
    assert sorted(cracks) == ["2026-10-02", "2026-10-06"]
    assert cracks["2026-10-06"] == pytest.approx(72.51, abs=0.01)
    assert cracks["2026-10-02"] == pytest.approx(4.674 * 42 - 135.51, abs=0.001)
    # an input series that is entirely missing → no cracks at all
    assert fp.compute_crack({"DDFUELNYH": values["DDFUELNYH"]}, spec["inputs"], spec["fn"]) == {}

    # the real gap in the fixtures: 2026-07-03 has Brent but no products → crack is null that day
    doc, _ = fixture_doc
    hist = doc["history"]
    idx = hist["dates"].index("2026-07-03")
    assert hist["brent"][idx] is not None
    assert hist["diesel_crack"][idx] is None and hist["gasoline_crack"][idx] is None
    assert hist["jet_crack"][idx] is None and hist["crack_321"][idx] is None


# ------------------------------------------------------------------------------------ stats

def test_stats_diesel_crack(fixture_doc, cfg_module):
    doc, _ = fixture_doc
    st = doc["stats"]["diesel_crack"]
    assert st["max"] == pytest.approx(116.5, abs=0.05)
    assert st["max_date"] == "2022-05-11"
    assert st["history_start"] == "2006-06-14"
    assert st["n"] > 4500
    assert st["mean_2015_2019"] == pytest.approx(16.0, abs=1.5)       # brief: ≈ 16 $/bbl
    assert st["prev_year"] == 2025
    assert st["mean_prev_year"] == pytest.approx(29.0, abs=1.5)       # brief: 2025 ≈ 29 $/bbl
    assert 0 <= st["percentile_now"] <= 100
    assert st["value"] == pytest.approx(72.51, abs=0.01) and st["as_of"] == AS_OF
    labels = cfg_module["crack_levels"]["labels"]
    cuts = cfg_module["crack_levels"]["percentile_cuts"]
    assert st["level"] == fp.level_for_percentile(st["percentile_now"], cuts)
    assert st["level_label"] == labels[st["level"]]
    # 72.5 $/bbl is far above the 2015–2019 mean (~16) but below the 2022 spike: the data put it
    # in the upper tiers; the exact tier is whatever the real daily distribution says.
    assert st["percentile_now"] >= cuts[2] and st["level"] >= 3
    # the other cracks get the same block; the spread gets value/as_of/mean_2015_2019
    for key in ("gasoline_crack", "jet_crack", "crack_321"):
        assert 0 <= doc["stats"][key]["percentile_now"] <= 100
        assert doc["stats"][key]["max_date"] >= "2006-06-14"
    spread = doc["stats"]["brent_wti_spread"]
    assert spread["value"] == pytest.approx(29.2, abs=0.001) and spread["as_of"] == AS_OF
    assert 0 < spread["mean_2015_2019"] < 10                           # brief: normal ≈ 3–5 $/bbl


@pytest.mark.parametrize("percentile,level", [
    (0, 0), (49.9, 0), (50, 1), (74.9, 1), (75, 2), (89.9, 2), (90, 3), (97.9, 3), (98, 4), (100, 4),
])
def test_level_mapping_at_cut_boundaries(cfg, percentile, level):
    labels, cuts = fp.crack_levels(cfg)
    assert cuts == [50, 75, 90, 98]
    assert fp.level_for_percentile(percentile, cuts) == level
    assert labels[level] == cfg["crack_levels"]["labels"][level]
    assert fp.level_for_percentile(None, cuts) is None


def test_percentile_definition():
    assert fp.percentile_of([1.0, 2.0, 3.0, 4.0], 4.0) == 100.0
    assert fp.percentile_of([1.0, 2.0, 3.0, 4.0], 2.0) == 50.0
    assert fp.percentile_of([1.0, 2.0, 3.0, 4.0], 0.5) == 0.0
    with pytest.raises(ValueError):
        fp.percentile_of([], 1.0)


def test_crack_levels_config_validation(cfg):
    bad = copy.deepcopy(cfg)
    bad["crack_levels"]["percentile_cuts"] = [50, 75]
    with pytest.raises(ValueError):
        fp.crack_levels(bad)
    assert fp.crack_levels({}) == (fp.DEFAULT_LEVEL_LABELS, [50.0, 75.0, 90.0, 98.0])


# ---------------------------------------------------------------------------------- history

def test_history_is_aligned_strictly_increasing_and_downsampled(fixture_doc, now_module):
    doc, _ = fixture_doc
    hist = doc["history"]
    dates = hist["dates"]
    n = len(dates)
    for key in ("diesel_crack", "gasoline_crack", "jet_crack", "crack_321", "brent", "wti"):
        assert len(hist[key]) == n, key
        assert all(v is None or isinstance(v, float) for v in hist[key])
    assert all(a < b for a, b in zip(dates, dates[1:]))
    # the first weekly point is the last trading day of the ISO week that contains 2006-06-14
    assert dates[0] >= "2006-06-14"
    assert date.fromisoformat(dates[0]).isocalendar()[:2] == date(2006, 6, 14).isocalendar()[:2]
    assert dates[-1] == AS_OF
    # the daily window is anchored to the newest observation, not to the clock (byte-stable on
    # days without FRED data): 2026-10-06 - 365 d, whatever `now` is
    cutoff = (date.fromisoformat(AS_OF) - timedelta(days=365)).isoformat()
    assert cutoff == "2025-10-06" and now_module.date().isoformat() != AS_OF
    assert hist["resolution_note"] == "weekly before %s, daily after" % cutoff
    assert hist["daily_from"] == cutoff
    weekly = [d for d in dates if d < cutoff]
    daily = [d for d in dates if d >= cutoff]
    weeks = [date.fromisoformat(d).isocalendar()[:2] for d in weekly]
    assert len(set(weeks)) == len(weeks)                 # one point per ISO week
    assert 900 < len(weekly) < 1100                      # ~19 years of weeks
    assert 240 <= len(daily) <= 262                      # every trading day of the last year
    assert "2026-10-05" in daily and "2026-10-02" in daily
    assert hist["diesel_crack"][-1] == pytest.approx(72.51, abs=0.01)
    assert hist["brent"][-1] == pytest.approx(125.44) and hist["wti"][-1] == pytest.approx(96.24)
    # the 2022 peak survives downsampling (it was a Wednesday; the week's last trading day is kept)
    peak_week = date(2022, 5, 11).isocalendar()[:2]
    assert peak_week in weeks


def test_weekly_pick_prefers_last_day_with_full_data(fixtures_dir):
    """The week of 2026-07-03 (US holiday, a Friday): Brent has a value, the NY Harbor products
    do not. The weekly point must be Thursday 2026-07-02, where the crack exists, not the Friday."""
    brent = fp.values_of(fp.parse_fred_csv(_fixture_text(fixtures_dir, "DCOILBRENTEU")))
    ulsd = fp.values_of(fp.parse_fred_csv(_fixture_text(fixtures_dir, "DDFUELNYH")))
    week = [d for d in ("2026-06-29", "2026-06-30", "2026-07-01", "2026-07-02", "2026-07-03")]
    spec = fp.CRACKS_BY_KEY["diesel_crack"]
    cracks = fp.compute_crack({"DCOILBRENTEU": brent, "DDFUELNYH": ulsd}, spec["inputs"], spec["fn"])
    cols = {"brent": {d: brent[d] for d in week if d in brent},
            "diesel_crack": {d: cracks[d] for d in week if d in cracks}}
    assert "2026-07-03" in cols["brent"] and "2026-07-03" not in cols["diesel_crack"]
    # a later observation pushes that week into the weekly region (the window follows the data,
    # not the clock, so `now` is irrelevant)
    cols["brent"]["2027-12-31"] = brent["2026-07-03"]
    hist = fp.build_history(cols, common.parse_iso("2026-07-04T00:00:00Z"))
    assert hist["dates"] == ["2026-07-02", "2027-12-31"]
    assert hist["daily_from"] == "2026-12-31"
    assert hist["diesel_crack"] == [pytest.approx(cracks["2026-07-02"]), None]
    assert hist["brent"] == [pytest.approx(brent["2026-07-02"]), pytest.approx(brent["2026-07-03"])]


def test_history_window_does_not_move_with_the_clock(fixture_doc, cfg_module):
    """Two runs on identical FRED data at different dates produce identical history/stats."""
    doc, _ = fixture_doc
    later, _ = fp.run(cfg_module, None, fixtures=True, now=datetime(2026, 10, 11, 10, 0, 0, tzinfo=timezone.utc))
    assert later["history"] == doc["history"]
    assert common.strip_keys(later, common.DEFAULT_IGNORE) == common.strip_keys(doc, common.DEFAULT_IGNORE)


# ------------------------------------------------------------------------- failure handling

def test_series_failure_carries_old_item_forward(monkeypatch, fixtures_dir, cfg, now, fixture_doc):
    old = copy.deepcopy(fixture_doc[0])
    old["latest"]["retail_diesel_us"]["fetched_at"] = FETCHED_OLD
    calls = _serve_fixtures(monkeypatch, fixtures_dir, failing={"GASDESW"})

    doc, info = fp.run(cfg, old, fixtures=False, now=now)

    assert len(calls) == len(fp.SERIES)
    assert all(kw.get("session") is None for _, _, kw in calls)
    assert info["partial"] == ["retail_diesel_us"]
    assert "FetchError" in info["errors"]["retail_diesel_us"]
    carried = doc["latest"]["retail_diesel_us"]
    assert carried["stale"] is True and carried["carried_forward"] is True
    assert carried["value"] == pytest.approx(6.199) and carried["as_of"] == RETAIL_AS_OF
    assert carried["fetched_at"] == FETCHED_OLD                       # untouched
    # everything else is fresh
    for key, dp in doc["latest"].items():
        if key != "retail_diesel_us":
            assert dp["stale"] is False and dp["fetched_at"] == "2026-10-08T10:00:00Z", key
    assert doc["as_of"] == AS_OF and doc["stale"] is False
    assert "stale" not in doc["history"]
    common.validate(doc, "prices")


def test_crude_failure_cascades_to_cracks_and_history(monkeypatch, fixtures_dir, cfg, now, fixture_doc):
    old = copy.deepcopy(fixture_doc[0])
    _serve_fixtures(monkeypatch, fixtures_dir, failing={"DCOILBRENTEU"})

    doc, info = fp.run(cfg, old, fixtures=False, now=now)

    assert set(info["partial"]) == {"brent", "diesel_crack", "gasoline_crack", "jet_crack", "brent_wti_spread",
                                    "history", "stats"}
    for key in ("brent", "diesel_crack", "gasoline_crack", "jet_crack", "brent_wti_spread"):
        assert doc["latest"][key]["stale"] is True and doc["latest"][key]["carried_forward"] is True
        assert doc["latest"][key]["value"] == old["latest"][key]["value"]
    assert doc["latest"]["crack_321"]["stale"] is False                # WTI-based, unaffected
    assert doc["latest"]["crack_321"]["value"] == pytest.approx(64.83, abs=0.01)
    assert doc["history"]["dates"] == old["history"]["dates"] and doc["history"]["stale"] is True
    assert doc["stats"]["diesel_crack"]["max"] == pytest.approx(116.5, abs=0.05)
    assert doc["stats"]["diesel_crack"]["stale"] is True and doc["stats"]["stale"] is True
    assert "input series missing" in info["errors"]["diesel_crack"]
    assert doc["as_of"] == AS_OF
    common.validate(doc, "prices")


def test_failure_without_old_keeps_shape(monkeypatch, fixtures_dir, cfg, now):
    _serve_fixtures(monkeypatch, fixtures_dir, failing={"GASREGW", "DJFUELUSGULF"})
    doc, info = fp.run(cfg, None, fixtures=False, now=now)
    assert set(info["partial"]) == {"retail_gasoline_us", "jet_gulf", "jet_crack"}
    for key in ("retail_gasoline_us", "jet_gulf", "jet_crack"):
        dp = doc["latest"][key]
        assert dp["value"] is None and dp["stale"] is True and "carried_forward" not in dp
    assert doc["latest"]["retail_gasoline_us"]["stale_kind"] == "weekly"
    assert doc["latest"]["diesel_crack"]["value"] == pytest.approx(72.51, abs=0.01)
    # history built from what is there: jet column all null, the others populated
    assert all(v is None for v in doc["history"]["jet_crack"])
    assert any(v is not None for v in doc["history"]["diesel_crack"])
    assert doc["stats"]["jet_crack"]["percentile_now"] is None and doc["stats"]["jet_crack"]["level"] is None
    common.validate(doc, "prices")


def test_implausible_latest_value_discards_series(monkeypatch, fixtures_dir, cfg, now, fixture_doc):
    old = copy.deepcopy(fixture_doc[0])

    def corrupt(series_id, text):
        if series_id == "DCOILWTICO":
            return text.replace("2026-10-06,96.24", "2026-10-06,9624.0")   # 9624 $/bbl is not a price
        return text

    _serve_fixtures(monkeypatch, fixtures_dir, transform=corrupt)
    doc, info = fp.run(cfg, old, fixtures=False, now=now)
    assert set(info["partial"]) == {"wti", "crack_321", "brent_wti_spread", "history", "stats"}
    assert "PlausibilityError" in info["errors"]["wti"]
    assert doc["latest"]["wti"]["value"] == pytest.approx(96.24) and doc["latest"]["wti"]["stale"] is True
    assert doc["latest"]["brent"]["stale"] is False
    common.validate(doc, "prices")


def test_total_failure_raises(monkeypatch, fixtures_dir, cfg, now, fixture_doc):
    _serve_fixtures(monkeypatch, fixtures_dir, failing={spec["id"] for _, spec in fp.SERIES})
    with pytest.raises(common.FetchError, match="none of the 8 series"):
        fp.run(cfg, copy.deepcopy(fixture_doc[0]), fixtures=False, now=now)


def test_fixtures_mode_never_touches_the_network(cfg, now):
    # conftest's guard turns any http_get into AssertionError("network call in test")
    doc, _ = fp.run(cfg, None, fixtures=True, now=now)
    assert doc["latest"]["brent"]["value"] == pytest.approx(125.44)


# ------------------------------------------------------------------------- schema / output

def test_output_validates_and_is_small(fixture_doc, cfg, now, tmp_data_dir, capsys):
    doc = copy.deepcopy(fixture_doc[0])
    common.validate(doc, "prices")
    common.apply_time_stale(doc, fp.STALE_KIND, cfg, now)
    assert doc["stale"] is False
    assert doc["latest"]["brent"]["stale"] is False                  # 58 h < 96 h
    assert doc["latest"]["retail_diesel_us"]["stale"] is False       # 82 h < 264 h (weekly rule)
    common.validate(doc, "prices")
    path = tmp_data_dir / fp.OUTPUT_FILE
    common.write_json_atomic(path, doc)
    assert os.path.getsize(path) < 300 * 1024
    assert capsys.readouterr().out == ""                             # stdout stays clean


def test_run_is_deterministic_and_quiet(cfg, now, capsys):
    a, _ = fp.run(cfg, None, fixtures=True, now=now)
    b, _ = fp.run(cfg, None, fixtures=True, now=now)
    captured = capsys.readouterr()
    assert captured.out == ""                                        # library code never prints to stdout
    assert "prices:" in captured.err                                 # ... it logs via common.log
    assert a == b
    assert common.strip_keys(a, common.DEFAULT_IGNORE) == common.strip_keys(b, common.DEFAULT_IGNORE)


def test_module_contract_constants():
    assert (fp.SOURCE_KEY, fp.OUTPUT_FILE, fp.SCHEMA, fp.STALE_KIND) == ("fred", "prices.json", "prices", "prices")
    assert [k for k, _ in fp.SERIES] == ["brent", "wti", "ulsd_nyh", "gasoline_nyh", "jet_gulf", "heating_oil_nyh",
                                         "retail_diesel_us", "retail_gasoline_us"]
    assert [k for k, _ in fp.CRACKS] == ["diesel_crack", "gasoline_crack", "jet_crack", "crack_321"]


def test_schema_rejects_doc_without_required_latest_key(fixture_doc):
    doc = copy.deepcopy(fixture_doc[0])
    del doc["latest"]["heating_oil_nyh"]
    with pytest.raises(jsonschema.ValidationError):
        common.validate(doc, "prices")
