"""Offline tests for scripts/fetch_country.py (ARCHITECTURE §3.3, §7).

Reference numbers come from the saved EIA/JODI responses in tests/fixtures (real data from
2026-10-08), never from memory: USA 2025 production 23730.6 kb/d, China 2024 consumption
16370.5 kb/d, Saudi Arabia JODI 2026-07 crude 8135.1 kb/d.
"""
from __future__ import annotations

import io
import json
from datetime import timedelta

import pytest

import common
import fetch_country as fc

REGION_IDS = {"WORL", "OPEC", "EU27", "WP16", "ASOC", "OECD", "NOEC", "PERG", "EURA", "MIDE"}
PSEUDO_COUNTRY_IDS = {"DEUW", "HITZ", "NLDA", "USIQ"}   # type "c" in the API but not ISO3
JODI_FIXTURE = common.FIXTURES / "jodi" / "primary_excerpt.csv"
JODI_HEADER = "REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE"


# ------------------------------------------------------------------------------ test helpers

def _eia_fixture(kind: str) -> dict:
    name = "intl_prod.json" if kind == "producers" else "intl_cons.json"
    with open(common.FIXTURES / "eia" / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _jodi_csv(rows, header: str = JODI_HEADER) -> io.StringIO:
    """A tiny JODI-shaped CSV: rows are (area, month, value) for CRUDEOIL/INDPROD/KBD."""
    lines = [header] + ["%s,%s,CRUDEOIL,INDPROD,KBD,%s,1" % (a, m, v) for a, m, v in rows]
    return io.StringIO("\r\n".join(lines) + "\r\n")


def _month_with_countries(month: str, n: int, start: int = 100):
    return [("%s%s" % (chr(65 + i // 26), chr(65 + i % 26)), month, "%d.0" % (start + i)) for i in range(n)]


class _Resp:
    """Minimal stand-in for requests.Response: ``json()``, streamed ``iter_lines()``, ``close()``."""

    def __init__(self, *, payload=None, path=None):
        self._payload = payload
        self._path = path
        self.closed = False

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload

    def iter_lines(self, chunk_size=None, **_kw):
        with open(self._path, "rb") as fh:
            for line in fh:
                yield line.rstrip(b"\r\n")

    def close(self):
        self.closed = True


def _stub_http(monkeypatch, handler):
    """Replace common.http_get with ``handler(url, kwargs)``; returns the recorded call list."""
    calls = []

    def fake_http_get(url, **kwargs):
        calls.append((url, kwargs))
        return handler(url, kwargs)

    monkeypatch.setattr(common, "http_get", fake_http_get)
    return calls


def _serve(url, kwargs, *, eia="fixture", jodi="fixture"):
    """Shared handler: EIA from the fixture JSON (or an error), JODI from the fixture CSV."""
    if url.startswith("https://api.eia.gov/"):
        if isinstance(eia, Exception):
            raise eia
        if eia == "fixture":
            kind = "producers" if kwargs["params"]["facets[activityId][]"] == "1" else "consumers"
            return _Resp(payload=_eia_fixture(kind))
        return _Resp(payload=eia)
    if "jodidata.org" in url:
        if isinstance(jodi, Exception):
            raise jodi
        if callable(jodi):
            return jodi(url)
        return _Resp(path=JODI_FIXTURE)
    raise AssertionError("unexpected URL %s" % url)


@pytest.fixture
def eia_doc(cfg, now):
    """The fixture-mode document (via eia_api) and its info."""
    doc, info = fc.run(cfg, None, fixtures=True, now=now, env={})
    return doc, info


# ------------------------------------------------------------------------- contract & shape

def test_module_constants():
    assert (fc.SOURCE_KEY, fc.OUTPUT_FILE, fc.SCHEMA, fc.STALE_KIND) == ("countries", "countries.json", "countries", "country")


def test_fixture_run_validates_against_schema(eia_doc, now):
    doc, info = eia_doc
    common.validate(doc, "countries")
    assert doc["via"] == "eia_api" and info["via"] == "eia_api"
    assert doc["schema_version"] == 1
    assert doc["generated_at"] == doc["fetched_at"] == common.iso_utc(now)
    assert doc["as_of"] == "2026-10-08"            # fetch date of the annual data (§3)
    assert doc["stale"] is False
    assert doc["unit"] == "thousand barrels per day"
    assert doc["source"] == "U.S. EIA International Energy Data"
    assert doc["source_url"].startswith("https://www.eia.gov/")
    assert doc["year"] == doc["producers"]["year"]
    assert info["partial"] == []
    assert (info["producers_year"], info["consumers_year"], info["jodi_month"]) == (2025, 2024, "2026-07")


def test_fixture_mode_is_byte_stable(cfg, now):
    a, _ = fc.run(cfg, None, fixtures=True, now=now, env={})
    b, _ = fc.run(cfg, None, fixtures=True, now=now, env={})
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ------------------------------------------------------------------------- EIA: filtering

def test_region_ids_never_appear_in_rows(eia_doc):
    doc, _ = eia_doc
    for key in ("producers", "consumers"):
        ids = [row["iso3"] for row in doc[key]["rows"]]
        assert len(ids) == 10
        assert not REGION_IDS & set(ids)
        assert not PSEUDO_COUNTRY_IDS & set(ids)
        assert all(fc.ISO3_RE.match(i) for i in ids)
        assert all(row["name"] for row in doc[key]["rows"])


def test_region_and_pseudo_ids_filtered_in_every_year():
    """Not just the top 10: the whole parsed country table is free of regions and group ids."""
    for kind in ("producers", "consumers"):
        by_year = fc.parse_eia_payload(_eia_fixture(kind))
        raw_ids = {row["countryRegionId"] for row in _eia_fixture(kind)["response"]["data"]}
        assert REGION_IDS <= raw_ids   # the raw response does contain them; the parser must drop them
        for year, entry in by_year.items():
            ids = [c["iso3"] for c in entry["countries"]]
            assert len(ids) == len(set(ids)), "duplicate country in %s %s" % (kind, year)
            assert not (REGION_IDS | PSEUDO_COUNTRY_IDS) & set(ids)
            assert all(fc.ISO3_RE.match(i) for i in ids)
            # WP16 "India" must not shadow IND: India appears once, as IND
            assert sum(1 for c in entry["countries"] if c["name"] == "India") <= 1


def test_world_row_is_used_for_world_total_not_as_a_country():
    by_year = fc.parse_eia_payload(_eia_fixture("producers"))
    assert by_year["2025"]["world"] == pytest.approx(106301.9, abs=0.1)
    assert "WORL" not in {c["iso3"] for c in by_year["2025"]["countries"]}


def test_non_numeric_values_skipped_in_fixture():
    by_year = fc.parse_eia_payload(_eia_fixture("consumers"))
    ids_2024 = {c["iso3"] for c in by_year["2024"]["countries"]}
    # GLP/GUF/MTQ/REU carry the value "ie" in the saved response
    assert not {"GLP", "GUF", "MTQ", "REU"} & ids_2024
    assert by_year["2024"]["skipped"] == 4
    assert all(isinstance(c["value"], float) for c in by_year["2024"]["countries"])


@pytest.mark.parametrize("bad", ["ie", "--", "NA", None, "", " ", "-", "nan", "inf", True])
def test_non_numeric_values_skipped_synthetic(bad):
    payload = {"response": {"data": [
        {"period": "2025", "countryRegionId": "AAA", "countryRegionName": "A", "countryRegionTypeId": "c", "value": bad},
        {"period": "2025", "countryRegionId": "BBB", "countryRegionName": "B", "countryRegionTypeId": "c",
         "value": "1059.43206690212183975342465753424657534"},
        {"period": "2025", "countryRegionId": "WORL", "countryRegionName": "World", "countryRegionTypeId": "r", "value": "5"},
    ]}}
    entry = fc.parse_eia_payload(payload)["2025"]
    assert [c["iso3"] for c in entry["countries"]] == ["BBB"]
    assert entry["countries"][0]["value"] == pytest.approx(1059.432067)
    assert entry["skipped"] == 1
    assert entry["world"] == 5.0


def test_parse_eia_payload_rejects_error_payloads():
    with pytest.raises(ValueError):
        fc.parse_eia_payload({"error": "invalid api key"})
    with pytest.raises(ValueError):
        fc.parse_eia_payload({"response": {"warnings": ["no data"]}})
    with pytest.raises(ValueError):
        fc.parse_eia_payload("not a dict")


# --------------------------------------------------------------------- EIA: year selection

def test_producers_year_2025_usa_first_sau_second(eia_doc):
    doc, _ = eia_doc
    p = doc["producers"]
    assert p["year"] == 2025
    assert p["rows"][0]["iso3"] == "USA" and p["rows"][0]["name"] == "United States"
    assert p["rows"][0]["value"] == pytest.approx(23730.6, abs=0.1)
    assert p["rows"][1]["iso3"] == "SAU"
    assert p["world_total"] == pytest.approx(106301.9, abs=0.1)
    assert p["country_count"] >= 150
    assert p["note"] == "Total petroleum and other liquids"
    assert p["source"] == "U.S. EIA International Energy Data"
    assert p["stale"] is False
    values = [r["value"] for r in p["rows"]]
    assert values == sorted(values, reverse=True)


def test_consumers_year_2024_usa_then_chn(eia_doc):
    """2025 consumption is incomplete (36 rows, no WORL) -> the ranking must use 2024."""
    doc, _ = eia_doc
    c = doc["consumers"]
    assert c["year"] == 2024
    assert c["rows"][0]["iso3"] == "USA"
    assert c["rows"][1]["iso3"] == "CHN" and c["rows"][1]["name"] == "China"
    assert c["rows"][1]["value"] == pytest.approx(16370.5, abs=0.1)
    assert c["world_total"] == pytest.approx(103110.2, abs=0.1)
    assert c["note"] == "Petroleum and other liquids"
    by_year = fc.parse_eia_payload(_eia_fixture("consumers"))
    assert by_year["2025"]["world"] is None and len(by_year["2025"]["countries"]) < 150


def test_select_year_rules():
    big = [{"iso3": "A%02d" % i, "name": "x", "value": 1.0} for i in range(150)]
    by_year = {
        "2025": {"world": None, "countries": big},          # no WORL row
        "2024": {"world": 10.0, "countries": big[:149]},    # too few countries
        "2023": {"world": 10.0, "countries": big},          # first usable
        "2022": {"world": 10.0, "countries": big},
    }
    assert fc.select_year(by_year) == "2023"
    assert fc.select_year({}) is None
    assert fc.build_eia_ranking({"2025": {"world": None, "countries": big}}, "producers",
                                fetched_at="2026-10-08T10:00:00Z", as_of="2026-10-08") is None


# ---------------------------------------------------------------------- EIA: arithmetic

def test_rest_of_world_non_negative_and_sums_to_world(eia_doc):
    doc, _ = eia_doc
    for key in ("producers", "consumers"):
        r = doc[key]
        assert r["rest_of_world"] >= 0
        assert sum(row["value"] for row in r["rows"]) + r["rest_of_world"] == pytest.approx(r["world_total"], abs=0.05)
        assert r["rest_of_world"] < r["world_total"]


def test_values_rounded_to_one_decimal(eia_doc):
    doc, _ = eia_doc
    seen = []
    for key in ("producers", "consumers"):
        seen += [row["value"] for row in doc[key]["rows"]] + [doc[key]["world_total"], doc[key]["rest_of_world"]]
    seen += [row["value"] for row in doc["monthly_crude"]["rows"]]
    assert all(round(v, 1) == v for v in seen)


# ------------------------------------------------------------------------------------ JODI

def test_jodi_gaps_never_become_zero():
    with open(JODI_FIXTURE, "r", newline="", encoding="utf-8") as fh:
        table = fc.parse_jodi(fh)
    # Russia is "-" in every month of the fixture: it must be absent, not 0
    assert all("RU" not in table[m] for m in table)
    # every "-" cell is dropped (not kept as 0): kept + gaps == all CRUDEOIL/INDPROD/KBD rows
    with open(JODI_FIXTURE, "r", encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines()[1:] if ",CRUDEOIL,INDPROD,KBD," in ln]
    gaps = sum(1 for ln in lines if ln.split(",")[5] == "-")
    kept = sum(len(table[m]) for m in table)
    assert gaps > 0 and kept + gaps == len(lines) == 672
    # the only zeros in the table are cells that literally say 0 (a reported zero is real data)
    literal_zeros = sum(1 for ln in lines if ln.split(",")[5] != "-" and float(ln.split(",")[5]) == 0.0)
    assert sum(1 for m in table for v in table[m].values() if v == 0.0) == literal_zeros
    # synthetic: "-" and "x" are gaps, a real "0.0000" is a value
    synthetic = fc.parse_jodi(_jodi_csv([("AA", "2026-07", "-"), ("BB", "2026-07", "x"), ("CC", "2026-07", "0.0000"),
                                         ("DD", "2026-07", "12.5000")]))
    assert synthetic == {"2026-07": {"CC": 0.0, "DD": 12.5}}


def test_jodi_only_crude_indprod_kbd_rows_count():
    csv_text = "\r\n".join([
        JODI_HEADER,
        "SA,2026-07,CRUDEOIL,INDPROD,KBD,8135.0968,3",
        "SA,2026-07,CRUDEOIL,INDPROD,KBBL,252188.0,3",      # other unit
        "SA,2026-07,CRUDEOIL,CLOSTLV,KBD,7000.0,3",         # other flow
        "SA,2026-07,NGL,INDPROD,KBD,1000.0,3",              # other product
        "SA,2026-7,CRUDEOIL,INDPROD,KBD,1.0,3",             # malformed month
        "SAU,2026-07,CRUDEOIL,INDPROD,KBD,1.0,3",           # not an iso2 area
        "short,row",
    ]) + "\r\n"
    assert fc.parse_jodi(io.StringIO(csv_text)) == {"2026-07": {"SA": 8135.0968}}


def test_jodi_latest_month_2026_07_and_saudi_present(eia_doc):
    doc, _ = eia_doc
    m = doc["monthly_crude"]
    assert m["latest_month"] == "2026-07"
    assert m["source"] == "JODI Oil World Database"
    assert m["unit"] == "thousand barrels per day"
    assert len(m["rows"]) == 10
    assert m["country_count"] >= 30
    by_iso2 = {row["iso2"]: row for row in m["rows"]}
    assert "SA" in by_iso2
    assert by_iso2["SA"]["value"] == pytest.approx(8135.1, abs=0.1)
    assert by_iso2["SA"]["name"] == "Saudi Arabia" and by_iso2["SA"]["iso3"] == "SAU"
    assert m["rows"][0]["iso2"] == "US"
    assert "RU" not in by_iso2
    assert all(fc.ISO2_RE.match(row["iso2"]) and row["name"] for row in m["rows"])


def test_jodi_latest_month_requires_30_countries():
    rows = _month_with_countries("2026-06", 35) + _month_with_countries("2026-07", 5)
    table = fc.parse_jodi(_jodi_csv(rows))
    assert fc.jodi_latest_month(table) == "2026-06"
    block = fc.build_monthly_crude(table, fetched_at="2026-10-08T10:00:00Z", as_of="2026-10-08")
    assert block["latest_month"] == "2026-06" and len(block["rows"]) == 10
    assert fc.jodi_latest_month({"2026-07": {"AA": 1.0}}) is None
    assert fc.build_monthly_crude({}, fetched_at="2026-10-08T10:00:00Z", as_of="2026-10-08") is None


def test_jodi_header_handling():
    with pytest.raises(ValueError):
        fc.parse_jodi(io.StringIO("A,B,C\r\n1,2,3\r\n"))
    with pytest.raises(ValueError):
        fc.parse_jodi(io.StringIO(""))
    # column order does not matter, a BOM on the header is tolerated
    shuffled = "﻿OBS_VALUE,REF_AREA,UNIT_MEASURE,TIME_PERIOD,FLOW_BREAKDOWN,ENERGY_PRODUCT\r\n" \
               "8135.0968,SA,KBD,2026-07,INDPROD,CRUDEOIL\r\n"
    assert fc.parse_jodi(io.StringIO(shuffled)) == {"2026-07": {"SA": 8135.0968}}


def test_jodi_unknown_iso2_falls_back_to_code():
    row = fc.jodi_row("ZZ", 12.34)
    assert row == {"iso2": "ZZ", "name": "ZZ", "value": 12.3}
    assert fc.jodi_row("SA", 1.0)["name"] == "Saudi Arabia"


# ------------------------------------------------------------------------- fallback (jodi)

def test_fallback_via_jodi_carries_old_consumers_as_stale(cfg, now, eia_doc):
    old, _ = eia_doc
    later = now + timedelta(days=1)
    doc, info = fc.run(cfg, old, fixtures=True, now=later, env={"CRACKSPREAD_COUNTRIES_VIA": "jodi"})
    common.validate(doc, "countries")
    assert doc["via"] == "jodi" and info["via"] == "jodi"
    assert doc["source"] == "JODI Oil World Database"
    # producers come from JODI's latest month
    p = doc["producers"]
    assert p["source"] == "JODI Oil World Database"
    assert "JODI" in p["note"] and "crude" in p["note"].lower()
    assert p["year"] == 2026 and p["period"] == "2026-07"
    assert p["rows"][0]["iso2"] == "US" and p["rows"][0]["iso3"] == "USA"
    assert p["rows"][0]["value"] == pytest.approx(13817.4, abs=0.1)
    assert p["world_total"] is None and p["rest_of_world"] is None
    assert p["stale"] is False
    # consumers are the old ranking, marked stale, old timestamps untouched
    c = doc["consumers"]
    assert c["stale"] is True
    assert c["rows"] == old["consumers"]["rows"]
    assert c["year"] == 2024
    assert c["fetched_at"] == old["consumers"]["fetched_at"] == common.iso_utc(now)
    assert doc["fetched_at"] == common.iso_utc(later)
    assert "consumers" in info["partial"]
    assert "jodi" in info["eia_error"].lower()
    # the monthly block is fresh
    assert doc["monthly_crude"]["stale"] is False and doc["monthly_crude"]["latest_month"] == "2026-07"
    # top-level stale is left to update.py's time rule; the fetch itself succeeded
    assert doc["stale"] is False


def test_fallback_via_jodi_without_old_gives_empty_consumers(cfg, now):
    doc, info = fc.run(cfg, None, fixtures=True, now=now, env={"CRACKSPREAD_COUNTRIES_VIA": "jodi"})
    common.validate(doc, "countries")
    assert doc["via"] == "jodi"
    assert doc["consumers"]["year"] is None and doc["consumers"]["rows"] == []
    assert doc["consumers"]["as_of"] is None
    assert info["partial"] == []
    assert info["consumers_year"] is None


def test_fallback_ignores_old_without_rows(cfg, now):
    old = {"consumers": {"year": None, "rows": []}, "monthly_crude": None}
    doc, info = fc.run(cfg, old, fixtures=True, now=now, env={"CRACKSPREAD_COUNTRIES_VIA": "jodi"})
    assert doc["consumers"]["rows"] == [] and info["partial"] == []


def test_jodi_failure_carries_old_monthly_block(cfg, now, eia_doc, monkeypatch):
    old, _ = eia_doc

    def boom(*_a, **_k):
        raise common.FetchError("HTTP 503 after 3 attempts")

    monkeypatch.setattr(fc, "_load_jodi", boom)
    doc, info = fc.run(cfg, old, fixtures=True, now=now + timedelta(hours=7), env={})
    common.validate(doc, "countries")
    assert doc["via"] == "eia_api"
    assert doc["monthly_crude"]["stale"] is True
    assert doc["monthly_crude"]["rows"] == old["monthly_crude"]["rows"]
    assert doc["monthly_crude"]["fetched_at"] == old["monthly_crude"]["fetched_at"]
    assert info["partial"] == ["monthly_crude"]
    assert "503" in info["jodi_error"]
    # without an old file the block is simply null
    doc2, info2 = fc.run(cfg, None, fixtures=True, now=now, env={})
    common.validate(doc2, "countries")
    assert doc2["monthly_crude"] is None and info2["partial"] == []


# ---------------------------------------------------------------- live paths (stubbed HTTP)

def test_live_without_key_never_calls_eia(cfg, now, monkeypatch):
    calls = _stub_http(monkeypatch, lambda url, kw: _serve(url, kw))
    doc, info = fc.run(cfg, None, fixtures=False, now=now, env={})
    common.validate(doc, "countries")
    assert doc["via"] == "jodi"
    assert not any("api.eia.gov" in url for url, _ in calls)
    assert "EIA_API_KEY" in info["eia_error"]
    jodi_calls = [(url, kw) for url, kw in calls if "jodidata.org" in url]
    assert len(jodi_calls) == 1
    assert jodi_calls[0][0].endswith("primaryyear2026.csv")   # this year's file first
    assert jodi_calls[0][1].get("stream") is True             # streamed, not loaded at once
    assert doc["monthly_crude"]["latest_month"] == "2026-07"
    assert doc["producers"]["rows"][0]["iso2"] == "US"


def test_live_eia_request_params_and_key_handling(cfg, now, monkeypatch):
    key = "SECRET-KEY-123"
    calls = _stub_http(monkeypatch, lambda url, kw: _serve(url, kw))
    doc, info = fc.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": key})
    common.validate(doc, "countries")
    assert doc["via"] == "eia_api"
    eia_calls = [(url, kw) for url, kw in calls if "api.eia.gov" in url]
    assert len(eia_calls) == 2
    assert all(url == "https://api.eia.gov/v2/international/data/" for url, _ in eia_calls)
    prod, cons = (kw["params"] for _, kw in eia_calls)
    assert (prod["facets[activityId][]"], prod["facets[productId][]"]) == ("1", "53")
    assert (cons["facets[activityId][]"], cons["facets[productId][]"]) == ("2", "5")
    for p in (prod, cons):
        assert p["frequency"] == "annual" and p["data[0]"] == "value" and p["facets[unit][]"] == "TBPD"
        assert p["start"] == "2023"            # current year (2026) - 3
        assert p["api_key"] == key             # passed as a param, never in the URL
    assert key not in json.dumps(doc) and key not in json.dumps(info)
    assert doc["producers"]["rows"][0]["value"] == pytest.approx(23730.6, abs=0.1)
    assert doc["consumers"]["year"] == 2024


def test_live_eia_http_error_falls_back_to_jodi(cfg, now, monkeypatch):
    err = common.FetchError("HTTP 403 for https://api.eia.gov/v2/international/data/?api_key=***")
    calls = _stub_http(monkeypatch, lambda url, kw: _serve(url, kw, eia=err))
    doc, info = fc.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "k"})
    common.validate(doc, "countries")
    assert doc["via"] == "jodi"
    assert "403" in info["eia_error"]
    assert sum(1 for url, _ in calls if "api.eia.gov" in url) == 1   # second request skipped (quota)
    assert "EIA International API unavailable" in doc["producers"]["note"]


def test_live_eia_error_payload_falls_back(cfg, now, monkeypatch):
    key = "abcdef0123456789abcdef0123456789abcdef01"
    _stub_http(monkeypatch, lambda url, kw: _serve(url, kw, eia={"error": "invalid api key %s" % key}))
    doc, info = fc.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": key})
    assert doc["via"] == "jodi" and "invalid api key" in info["eia_error"]
    assert key not in info["eia_error"]   # a key echoed by the API is masked before it reaches meta.json


def test_live_consumers_failure_only_keeps_eia_producers(cfg, now, eia_doc, monkeypatch):
    old, _ = eia_doc

    def handler(url, kw):
        if "api.eia.gov" in url and kw["params"]["facets[activityId][]"] == "2":
            raise common.FetchError("HTTP 500 after 3 attempts")
        return _serve(url, kw)

    _stub_http(monkeypatch, handler)
    doc, info = fc.run(cfg, old, fixtures=False, now=now, env={"EIA_API_KEY": "k"})
    common.validate(doc, "countries")
    assert doc["via"] == "eia_api"
    assert doc["producers"]["year"] == 2025 and doc["producers"]["stale"] is False
    assert doc["consumers"]["stale"] is True and doc["consumers"]["rows"] == old["consumers"]["rows"]
    assert info["partial"] == ["consumers"]


def test_live_jodi_falls_back_to_previous_year_file(cfg, now, monkeypatch):
    def jodi(url):
        if url.endswith("2026.csv"):
            raise common.FetchError("HTTP 404 for %s" % url)
        return _Resp(path=JODI_FIXTURE)

    calls = _stub_http(monkeypatch, lambda url, kw: _serve(url, kw, jodi=jodi))
    doc, _ = fc.run(cfg, None, fixtures=False, now=now, env={})
    urls = [url for url, _ in calls if "jodidata.org" in url]
    assert [u[-8:] for u in urls] == ["2026.csv", "2025.csv"]
    assert doc["monthly_crude"]["latest_month"] == "2026-07"


def test_everything_failing_raises(cfg, now, monkeypatch):
    _stub_http(monkeypatch, lambda url, kw: _serve(url, kw, eia=common.FetchError("HTTP 429"),
                                                   jodi=common.FetchError("HTTP 503 after 3 attempts")))
    with pytest.raises(Exception) as excinfo:
        fc.run(cfg, None, fixtures=False, now=now, env={"EIA_API_KEY": "k"})
    assert "429" in str(excinfo.value) and "503" in str(excinfo.value)


def test_fixture_mode_never_touches_network(cfg, now, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("network call in fixtures mode")

    monkeypatch.setattr(common, "http_get", boom)
    doc, _ = fc.run(cfg, None, fixtures=True, now=now, env={"EIA_API_KEY": "would-be-used-live"})
    assert doc["via"] == "eia_api"
