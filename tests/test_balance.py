"""Offline tests for scripts/fetch_balance.py (ARCHITECTURE §3.2, §7; brief §15).

Reference numbers are the real October-2026 STEO values saved in tests/fixtures/eia/ (brief §5.4), never
invented. Synthetic values appear only where the brief asks for threshold tests.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest

import common
import fetch_balance as fb

FIX = common.FIXTURES / "eia"

# Brief §5.4 / §6.2 (October 2026 STEO)
Q3_2026_STOCK_DRAW = 1.88
OCT_2026 = {"production": 102.10, "consumption": 102.78, "stock_draw": 0.67}
SEP_2026 = {"production": 101.30, "consumption": 104.24, "stock_draw": 2.94, "opec": 23.85, "nonopec": 77.45,
            "brent": 114.16, "wti": 97.31}


# ------------------------------------------------------------------------------- helpers

@pytest.fixture
def api_doc(cfg, now):
    doc, info = fb.run(cfg, None, fixtures=True, now=now, env={})
    return doc, info


@pytest.fixture
def xlsx_doc(cfg, now):
    doc, info = fb.run(cfg, None, fixtures=True, now=now, env={"CRACKSPREAD_STEO_VIA": "xlsx"})
    return doc, info


def _month(doc, period):
    entries = [m for m in doc["months"] if m["period"] == period]
    assert len(entries) == 1, period
    return entries[0]


def _quarter(doc, period):
    entries = [q for q in doc["quarters"] if q["period"] == period]
    assert len(entries) == 1, period
    return entries[0]


class _Resp:
    """Minimal stand-in for requests.Response (content/text/json)."""

    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.encoding = "utf-8"

    @property
    def text(self):
        return self.content.decode("utf-8")

    def json(self):
        return json.loads(self.content.decode("utf-8"))


def _install_fake_http(monkeypatch, calls, *, api="fail", page="ok", xlsx="ok"):
    """Replace common.http_get: api.eia.gov fails (403) or serves the fixture; the xlsx and the page come
    from the fixtures or fail, as configured. Every call is recorded in ``calls``."""

    def fake(url, *, params=None, session=None, **kwargs):
        calls.append((url, params))
        if "api.eia.gov" in url:
            if api == "fail":
                raise common.FetchError("HTTP 403 for https://api.eia.gov/v2/steo/data/?api_key=***")
            return _Resp((FIX / "steo_api.json").read_bytes())
        if url == fb.XLSX_URL:
            if xlsx == "fail":
                raise common.FetchError("HTTP 503 after 3 attempts for " + url)
            return _Resp((FIX / "steo_3atab.xlsx").read_bytes())
        if url == fb.PAGE_URL:
            if page == "fail":
                raise common.FetchError("HTTP 500 after 3 attempts for " + url)
            return _Resp((FIX / "global_oil.html").read_bytes())
        raise AssertionError("unexpected URL in test: %s" % url)

    monkeypatch.setattr(common, "http_get", fake)


# ------------------------------------------------------------------------------ contract

def test_module_constants():
    assert fb.SOURCE_KEY == "eia_steo"
    assert fb.OUTPUT_FILE == "balance.json"
    assert fb.SCHEMA == "balance"
    assert fb.STALE_KIND == "steo"
    assert fb.API_SERIES_IDS == ("PAPR_WORLD", "PATC_WORLD", "T3_STCHANGE_WORLD", "PAPR_OPEC", "PAPR_NONOPEC",
                                 "BREPUUS", "WTIPUUS")


# ------------------------------------------------------------------------------- API path

def test_q3_2026_stock_draw_mean_matches_eia_text(api_doc):
    doc, _ = api_doc
    draws = [_month(doc, p)["stock_draw"] for p in ("2026-07", "2026-08", "2026-09")]
    mean = sum(draws) / 3
    assert mean == pytest.approx(Q3_2026_STOCK_DRAW, abs=0.02)          # EIA text: "1.9 million b/d in 3Q26"
    q3 = _quarter(doc, "2026Q3")
    assert q3["stock_draw"] == pytest.approx(mean, abs=0.01)
    assert q3["is_forecast"] is False
    assert q3["production"] is not None and q3["consumption"] is not None


def test_current_month_values_october_2026(api_doc):
    doc, info = api_doc
    assert info["via"] == "api" and doc["via"] == "api"
    assert doc["current_month"] == "2026-10"
    cur = doc["current"]
    assert cur["period"] == "2026-10"
    for key, expected in OCT_2026.items():
        assert cur[key] == pytest.approx(expected, abs=0.01), key
    assert cur == _month(doc, "2026-10")
    sep = _month(doc, "2026-09")
    for key, expected in SEP_2026.items():
        assert sep[key] == pytest.approx(expected, abs=0.01), key


def test_months_sorted_unique_and_complete(api_doc):
    doc, info = api_doc
    periods = [m["period"] for m in doc["months"]]
    assert periods == sorted(periods) and len(periods) == len(set(periods))
    assert periods[0] == "2024-01" and periods[-1] == "2027-12" and len(periods) == 48
    assert info["months"] == 48
    for m in doc["months"]:
        assert set(m) >= {"period", "production", "consumption", "stock_draw", "opec", "nonopec", "brent", "wti", "is_forecast"}
        for key in ("production", "consumption", "stock_draw", "opec", "nonopec", "brent", "wti"):
            assert isinstance(m[key], float), (m["period"], key)
            assert m[key] == round(m[key], 2)


def test_is_forecast_flag(api_doc):
    doc, _ = api_doc
    assert _month(doc, "2026-09")["is_forecast"] is False
    assert _month(doc, "2026-10")["is_forecast"] is True
    assert all(m["is_forecast"] is (m["period"] >= "2026-10") for m in doc["months"])
    assert _quarter(doc, "2026Q3")["is_forecast"] is False
    assert _quarter(doc, "2026Q4")["is_forecast"] is True
    assert [q["period"] for q in doc["quarters"]] == sorted(q["period"] for q in doc["quarters"])
    assert doc["quarters"][0]["period"] == "2024Q1" and doc["quarters"][-1]["period"] == "2027Q4"


def test_status_deficit_at_0_67(api_doc, cfg):
    doc, _ = api_doc
    assert doc["current"]["stock_draw"] == pytest.approx(0.67, abs=0.01)
    assert doc["status"] == "deficit"
    assert cfg["balance_thresholds"] == {"deficit": 0.3, "surplus": -0.3}


@pytest.mark.parametrize("value, expected", [
    (0.67, "deficit"), (0.31, "deficit"), (0.3, "balanced"), (0.0, "balanced"), (-0.3, "balanced"),
    (-0.31, "surplus"), (-2.5, "surplus"),
])
def test_classify_status_thresholds(cfg, value, expected):
    assert fb.classify_status(value, cfg) == expected


def test_classify_status_uses_config_thresholds():
    assert fb.classify_status(0.4, {"balance_thresholds": {"deficit": 0.5, "surplus": -0.5}}) == "balanced"
    assert fb.classify_status(0.4, {}) == "deficit"  # defaults from the brief
    with pytest.raises(ValueError):
        fb.classify_status(None, {})


def test_status_balanced_and_surplus_with_synthetic_current_month(cfg, now):
    """The real October value is a deficit; override just the current month to exercise the other states."""
    with open(FIX / "steo_api.json", "r", encoding="utf-8") as fh:
        series = fb.parse_api_payload(json.load(fh))
    for synthetic, expected in ((0.0, "balanced"), (-0.5, "surplus"), (0.29, "balanced")):
        s = copy.deepcopy(series)
        s["stock_draw"]["2026-10"] = synthetic
        doc, _ = fb.build_doc(s, cfg, now, via="api")
        assert doc["status"] == expected, synthetic
        assert doc["current"]["stock_draw"] == pytest.approx(synthetic, abs=0.001)


def test_quote_and_release_dates_from_page(api_doc):
    doc, info = api_doc
    assert doc["quote"] is not None
    assert "1.9 million b/d in 3Q26" in doc["quote"]
    assert doc["quote"].startswith("We estimate that global oil inventories fell")
    assert doc["quote"].endswith("0.7 million b/d on average in 4Q26.")
    assert "<" not in doc["quote"] and "&" not in doc["quote"] and "  " not in doc["quote"]
    assert doc["quote_url"] == "https://www.eia.gov/outlooks/steo/report/global_oil.php"
    assert doc["steo_release"] == "2026-10-06"
    assert doc["as_of"] == "2026-10-06"
    assert doc["next_release"] == "2026-11-10"
    assert doc["forecast_completed"] == "2026-10-01"
    assert doc["steo_edition"] == "October 2026"
    assert doc["source"] == "EIA Short-Term Energy Outlook (STEO), October 2026"
    assert doc["source_url"] == doc["quote_url"]
    assert info["release_date_estimated"] is False and info["quote_found"] is True


def test_envelope_and_schema(api_doc, xlsx_doc, cfg, now):
    for doc, _ in (api_doc, xlsx_doc):
        common.validate(doc, "balance")
        assert doc["schema_version"] == 1
        assert doc["generated_at"] == "2026-10-08T10:00:00Z" == doc["fetched_at"]
        assert doc["unit"] == "million barrels per day"
        assert doc["stale"] is False
        fresh = common.apply_time_stale(copy.deepcopy(doc), fb.STALE_KIND, cfg, now)
        assert fresh["stale"] is False
        later = common.apply_time_stale(copy.deepcopy(doc), fb.STALE_KIND, cfg, now + timedelta(days=60))
        assert later["stale"] is True   # 45-day STEO threshold
        common.validate(later, "balance")


def test_schema_rejects_broken_doc(api_doc):
    doc, _ = api_doc
    broken = copy.deepcopy(doc)
    broken["status"] = "shortage"
    with pytest.raises(jsonschema.ValidationError):
        common.validate(broken, "balance")
    broken = copy.deepcopy(doc)
    del broken["current_month"]
    with pytest.raises(jsonschema.ValidationError):
        common.validate(broken, "balance")


# ------------------------------------------------------------------------------ xlsx path

def test_xlsx_path_matches_api_path(api_doc, xlsx_doc):
    (api, _), (xlsx, info) = api_doc, xlsx_doc
    assert info["via"] == "xlsx" and xlsx["via"] == "xlsx"
    assert xlsx["data_url"] == fb.XLSX_URL and api["data_url"] == fb.API_URL
    for period in ("2026-09", "2026-10", "2024-01", "2027-12"):
        a, x = _month(api, period), _month(xlsx, period)
        for key in ("production", "consumption", "stock_draw", "opec", "nonopec"):
            assert x[key] == pytest.approx(a[key], abs=0.01), (period, key)
        assert x["is_forecast"] == a["is_forecast"]
        assert x["brent"] is None and x["wti"] is None   # not in sheet 3atab
    sep = _month(xlsx, "2026-09")
    for key in ("production", "consumption", "stock_draw"):
        assert sep[key] == pytest.approx(SEP_2026[key], abs=0.01), key
    assert [m["period"] for m in xlsx["months"]] == [m["period"] for m in api["months"]]
    assert _quarter(xlsx, "2026Q3")["stock_draw"] == pytest.approx(Q3_2026_STOCK_DRAW, abs=0.02)
    assert xlsx["status"] == api["status"] == "deficit"
    assert xlsx["steo_release"] == "2026-10-06" and xlsx["next_release"] == "2026-11-10"
    assert xlsx["steo_edition"] == "October 2026"
    assert xlsx["quote"] == api["quote"]


def test_parse_xlsx_forecast_date_and_edition():
    parsed = fb.parse_xlsx(FIX / "steo_3atab.xlsx")
    assert parsed["forecast_date"] == "2026-10-01"       # "Forecast date:" cell → Thursday, October 1, 2026
    assert parsed["edition"] == "October 2026"
    series = parsed["series"]
    assert series["production"]["2026-09"] == pytest.approx(101.30, abs=0.01)
    assert series["consumption"]["2026-09"] == pytest.approx(104.24, abs=0.01)
    assert series["stock_draw"]["2026-07"] == pytest.approx(-0.27, abs=0.01)
    assert series["brent"] == {} and series["wti"] == {}
    assert min(series["production"]) == "2022-01" and max(series["production"]) == "2027-12"


def test_parse_3atab_rows_layout_tolerance():
    header = ("Forecast date:", None, 2025, None, None, None, None, None, None, None, None, None, None, None,
              2026, None, None, None, None, None, None, None, None, None, None, None, None)
    months = ("Tuesday, October 6, 2026", None) + ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec") * 2
    rows = [
        ("Table of Contents", "Table 3a.", None),
        (None, "U.S. Energy Information Administration | Short-Term Energy Outlook - October 2026"),
        header, months,
        (None, "Production"),
        ("papr_world", "World total") + tuple(100.0 + i for i in range(24)),
        ("papr_world", "duplicate row must be ignored") + tuple(0.0 for _ in range(24)),
        ("patc_world", "World total") + tuple(101.0 + i for i in range(24)),
        ("t3_stchange_world", "World total") + tuple([0.5] * 23 + ["-"]),     # short row + missing marker
    ]
    parsed = fb.parse_3atab_rows(rows)
    s = parsed["series"]
    assert s["production"]["2025-01"] == 100.0 and s["production"]["2026-12"] == 123.0
    assert s["consumption"]["2026-01"] == 113.0
    assert s["stock_draw"]["2026-12"] is None and s["stock_draw"]["2026-11"] == 0.5
    assert parsed["forecast_date"] == "2026-10-06"
    assert parsed["edition"] == "October 2026"
    with pytest.raises(ValueError, match="rows not found"):
        fb.parse_3atab_rows(rows[:6])     # consumption + stock change rows missing
    with pytest.raises(ValueError, match="month header"):
        fb.parse_3atab_rows(rows[:3])


# ------------------------------------------------------------------------- live-path logic

def test_api_error_falls_back_to_xlsx(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="fail")
    doc, info = fb.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "DEMO_KEY"})
    urls = [u for u, _ in calls]
    assert urls == [fb.API_URL, fb.XLSX_URL, fb.PAGE_URL]
    params = dict((k, v) for k, v in calls[0][1] if k != "facets[seriesId][]")
    facets = [v for k, v in calls[0][1] if k == "facets[seriesId][]"]
    assert facets == list(fb.API_SERIES_IDS) and len(facets) == 7
    assert params["start"] == "2024-01" and params["length"] == 5000 and params["frequency"] == "monthly"
    assert params["data[0]"] == "value" and params["api_key"] == "DEMO_KEY"
    assert doc["via"] == "xlsx" and info["via"] == "xlsx"
    assert info["api_error"].startswith("FetchError") and "403" in info["api_error"]
    assert "DEMO_KEY" not in info["api_error"]
    assert doc["current"]["stock_draw"] == pytest.approx(0.67, abs=0.01)
    assert doc["steo_release"] == "2026-10-06" and doc["quote"] and "1.9 million b/d" in doc["quote"]
    common.validate(doc, "balance")


def test_api_success_through_stubbed_http(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="ok")
    doc, info = fb.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "DEMO_KEY"})
    assert [u for u, _ in calls] == [fb.API_URL, fb.PAGE_URL]
    assert doc["via"] == "api" and "api_error" not in info
    assert _month(doc, "2026-09")["brent"] == pytest.approx(114.16, abs=0.01)


def test_without_key_api_is_never_called(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="fail")
    for env in ({}, {"EIA_API_KEY": ""}, {"EIA_API_KEY": "   "}):
        calls.clear()
        doc, info = fb.run(cfg, None, fixtures=False, now=now, env=env)
        assert all("api.eia.gov" not in u for u, _ in calls)
        assert [u for u, _ in calls] == [fb.XLSX_URL, fb.PAGE_URL]
        assert doc["via"] == "xlsx" and "api_error" not in info


def test_page_failure_xlsx_uses_forecast_date_cell(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="fail", page="fail")
    doc, info = fb.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "DEMO_KEY"})
    assert doc["via"] == "xlsx"
    assert doc["quote"] is None and doc["next_release"] is None and doc["forecast_completed"] is None
    assert doc["steo_release"] == "2026-10-01" == doc["as_of"]          # "Forecast date:" cell of the sheet
    assert info["release_date_estimated"] is False and doc["release_date_estimated"] is False
    assert info["quote_found"] is False and info["page_error"].startswith("FetchError")
    assert doc["steo_edition"] == "October 2026"                        # from the sheet header
    common.validate(doc, "balance")


def test_page_failure_api_estimates_release_date(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="ok", page="fail")
    doc, info = fb.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "DEMO_KEY"})
    assert doc["via"] == "api" and doc["quote"] is None
    assert doc["steo_release"] == "2026-10-01"                          # first day of the current month
    assert info["release_date_estimated"] is True
    assert doc["release_date_estimated"] is True                        # flagged in the document itself (brief §7)
    assert doc["steo_edition"] is None and doc["source"] == "EIA Short-Term Energy Outlook (STEO)"
    assert doc["status"] == "deficit"
    common.validate(doc, "balance")


def test_everything_down_raises(monkeypatch, cfg, now):
    calls = []
    _install_fake_http(monkeypatch, calls, api="fail", xlsx="fail", page="ok")
    with pytest.raises(common.FetchError):
        fb.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "DEMO_KEY"})


def test_fixtures_mode_never_touches_network(cfg, now):
    # conftest's no_network guard makes common.http_get raise AssertionError; both fixture paths must pass.
    fb.run(cfg, None, fixtures=True, now=now, env={})
    fb.run(cfg, None, fixtures=True, now=now, env={"CRACKSPREAD_STEO_VIA": "xlsx"})


def test_fixture_mode_reads_env_var(monkeypatch, cfg, now):
    monkeypatch.setenv("CRACKSPREAD_STEO_VIA", "xlsx")
    doc, _ = fb.run(cfg, None, fixtures=True, now=now)
    assert doc["via"] == "xlsx"
    monkeypatch.delenv("CRACKSPREAD_STEO_VIA")
    doc, _ = fb.run(cfg, None, fixtures=True, now=now)
    assert doc["via"] == "api"


# ------------------------------------------------------------------------------- parsing

def test_parse_api_payload_strings_sorting_and_gaps():
    payload = {"response": {"total": "4", "data": [
        {"period": "2026-10", "seriesId": "T3_STCHANGE_WORLD", "value": ".670910404"},
        {"period": "2026-09", "seriesId": "T3_STCHANGE_WORLD", "value": "2.937625701"},
        {"period": "2026-07", "seriesId": "T3_STCHANGE_WORLD", "value": "-.265254417"},
        {"period": "2026-08", "seriesId": "PAPR_WORLD", "value": "."},        # EIA missing marker
        {"period": "2026-08", "seriesId": "NOT_A_SERIES", "value": "1"},
        {"period": "2026", "seriesId": "PAPR_WORLD", "value": "100"},         # wrong frequency → ignored
    ]}}
    series = fb.parse_api_payload(payload)
    assert series["stock_draw"] == {"2026-10": pytest.approx(0.670910404), "2026-09": pytest.approx(2.937625701),
                                    "2026-07": pytest.approx(-0.265254417)}
    assert series["production"] == {"2026-08": None}
    assert series["brent"] == {}
    with pytest.raises(common.FetchError, match="EIA API error"):
        fb.parse_api_payload({"error": "Invalid api_key", "code": 403})
    with pytest.raises(ValueError):
        fb.parse_api_payload({"response": {"data": "nope"}})
    with pytest.raises(ValueError):
        fb.parse_api_payload({"response": {"data": []}})


def test_build_doc_sorts_client_side_and_rounds(cfg, now):
    with open(FIX / "steo_api.json", "r", encoding="utf-8") as fh:
        rows = json.load(fh)["response"]["data"]
    rows.sort(key=lambda r: r["value"])          # the API's lexicographic value sort
    series = fb.parse_api_payload({"response": {"data": rows}})
    doc, _ = fb.build_doc(series, cfg, now, via="api")
    periods = [m["period"] for m in doc["months"]]
    assert periods == sorted(periods) and periods[0] == "2024-01"
    assert _month(doc, "2026-10")["stock_draw"] == 0.67


def test_plausibility_range_raises(cfg, now):
    with open(FIX / "steo_api.json", "r", encoding="utf-8") as fh:
        series = fb.parse_api_payload(json.load(fh))
    for field, bad in (("production", 150.0), ("consumption", 60.0)):
        s = copy.deepcopy(series)
        s[field]["2026-10"] = bad
        with pytest.raises(common.PlausibilityError, match=field):
            fb.build_doc(s, cfg, now, via="api")


def test_build_doc_requires_current_month(cfg):
    with open(FIX / "steo_api.json", "r", encoding="utf-8") as fh:
        series = fb.parse_api_payload(json.load(fh))
    with pytest.raises(ValueError, match="current month"):
        fb.build_doc(series, cfg, datetime(2028, 3, 1, tzinfo=timezone.utc), via="api")


def test_incomplete_quarter_is_skipped(cfg, now):
    with open(FIX / "steo_api.json", "r", encoding="utf-8") as fh:
        series = fb.parse_api_payload(json.load(fh))
    for field in fb.FIELDS:
        series[field].pop("2027-12", None)
    doc, _ = fb.build_doc(series, cfg, now, via="api")
    assert doc["months"][-1]["period"] == "2027-11"
    assert doc["quarters"][-1]["period"] == "2027Q3"


def test_quote_parser_tolerates_tags_entities_whitespace():
    html = ("<html><body><style>p{color:red}</style><p>Intro sentence with the U.S. abbreviation.</p>\n"
            "<p>First.  We\n  estimate that <b>global&nbsp;oil inventories</b> fell by 1.9 million b/d in 3Q26 "
            "and will fall&amp;more.&nbsp;Next sentence mentions global oil inventories again.</p></body></html>")
    assert fb.extract_quote(html) == "We estimate that global oil inventories fell by 1.9 million b/d in 3Q26 and will fall&more."
    assert fb.extract_quote("<p>Nothing relevant here.</p>") is None
    assert fb.extract_quote("<p>Prices in the U.S. and global oil inventories fell in 2026.</p>") == \
        "Prices in the U.S. and global oil inventories fell in 2026."


def test_page_date_parser_tolerates_markup():
    html = ("<p><strong>Release&nbsp;Date:</strong>\n\t October 6, 2026 &nbsp;|&nbsp; <strong>Forecast Completed:</strong> "
            "October 1, 2026 | <strong>Next Release Date:</strong> <span>November 10, 2026</span></p>")
    parsed = fb.parse_page(html)
    assert parsed["release_date"] == "2026-10-06"
    assert parsed["next_release"] == "2026-11-10"
    assert parsed["forecast_completed"] == "2026-10-01"
    assert parsed["quote"] is None
    assert fb.parse_page("<p>Next Release Date: Nov 10, 2026</p>")["release_date"] is None
    assert fb.parse_page("<p>Release Date: Oct 6, 2026</p>")["release_date"] == "2026-10-06"


def test_fixture_page_has_two_phrase_occurrences_and_first_wins():
    html = (FIX / "global_oil.html").read_text(encoding="utf-8")
    assert html.count("global oil inventories") == 2
    assert fb.extract_quote(html).startswith("We estimate that")


def test_to_float_and_rounding():
    assert fb._to_float(".67") == pytest.approx(0.67)
    assert fb._to_float("-.27") == pytest.approx(-0.27)
    assert fb._to_float("1,234.5") == 1234.5
    assert fb._to_float(None) is None and fb._to_float(".") is None and fb._to_float("NA") is None
    assert fb._to_float(True) is None and fb._to_float("abc") is None and fb._to_float(float("nan")) is None
    assert fb._round(-0.001, "mbd") == 0.0 and str(fb._round(-0.001, "mbd")) == "0.0"
    assert fb._round(102.1046596, "mbd") == 102.1
    assert fb._round(None, "mbd") is None
