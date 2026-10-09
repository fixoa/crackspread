"""Crackspread: who pumps, who guzzles -> ``site/data/countries.json``.

Primary source: EIA International Energy Data API v2 (annual, thousand barrels per day):
production ``activityId=1, productId=53`` (total petroleum and other liquids) and consumption
``activityId=2, productId=5`` (petroleum and other liquids; 53 returns nothing for consumption).
Secondary source: the JODI Oil primary CSV (monthly crude production per country, ``-`` = gap).

Fallback (no ``EIA_API_KEY``, HTTP 403/429, any other EIA error, or
``CRACKSPREAD_COUNTRIES_VIA=jodi``): ``via="jodi"`` with producers from the latest JODI month and
consumers carried from the previous file (marked stale) or left empty.

Contract: ARCHITECTURE.md §2 (fetcher interface) and §3.3 (file shape). Flat import (``import
common``), no stdout, no ``sys.exit``, runs on Python 3.9 and 3.12.
"""
from __future__ import annotations

import copy
import csv
import math
import os
import re
from datetime import datetime
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

import common

SOURCE_KEY = "countries"
OUTPUT_FILE = "countries.json"
SCHEMA = "countries"
STALE_KIND = "country"

EIA_URL = "https://api.eia.gov/v2/international/data/"
EIA_SOURCE = "U.S. EIA International Energy Data"
EIA_SOURCE_URL = "https://www.eia.gov/international/data/world"
#: The JODI "primary" (crude) file is split per calendar year; {year} is substituted at run time.
JODI_URL_TEMPLATE = (
    "https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/primaryyear{year}.csv"
)
JODI_SOURCE = "JODI Oil World Database"
JODI_SOURCE_URL = "https://www.jodidata.org/oil/"
UNIT = "thousand barrels per day"

#: EIA facets per ranking. ``note`` is only the fallback when the response carries no productName.
EIA_SERIES: Dict[str, Dict[str, str]] = {
    "producers": {"activityId": "1", "productId": "53", "note": "Total petroleum and other liquids"},
    "consumers": {"activityId": "2", "productId": "5", "note": "Petroleum and other liquids"},
}
EIA_UNIT_FACET = "TBPD"
EIA_START_YEARS_BACK = 3          # start = current year - 3
EIA_PAGE_LENGTH = 5000            # API maximum per request; 3 years x ~250 rows fits in one page
MIN_COUNTRY_ROWS = 150            # a year is usable only with >= this many numeric country rows ...
WORLD_ID = "WORL"                 # ... and a numeric world row
JODI_MIN_COUNTRIES = 30           # a JODI month counts only with >= this many reporting countries
MAX_JODI_BYTES = 25 * 1024 * 1024  # the primary CSV is ~5.5 MB (secondary ~12.6 MB)
TOP_N = 10

JODI_PRODUCT = "CRUDEOIL"
JODI_FLOW = "INDPROD"
JODI_UNIT = "KBD"
JODI_COLUMNS = ("REF_AREA", "TIME_PERIOD", "ENERGY_PRODUCT", "FLOW_BREAKDOWN", "UNIT_MEASURE", "OBS_VALUE")

FIXTURE_EIA_PROD = ("eia", "intl_prod.json")
FIXTURE_EIA_CONS = ("eia", "intl_cons.json")
FIXTURE_JODI = ("jodi", "primary_excerpt.csv")

ISO3_RE = re.compile(r"^[A-Z]{3}$")
ISO2_RE = re.compile(r"^[A-Z]{2}$")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: ISO 3166 alpha-2 -> (alpha-3, display name) for the countries that report crude production to
#: JODI plus the other major producers. Names follow the EIA spelling where the two differ
#: ("Turkiye", "South Korea"). Unknown codes fall back to the alpha-2 code as the name.
JODI_COUNTRIES: Dict[str, Tuple[str, str]] = {
    "AE": ("ARE", "United Arab Emirates"), "AL": ("ALB", "Albania"), "AM": ("ARM", "Armenia"),
    "AO": ("AGO", "Angola"), "AR": ("ARG", "Argentina"), "AT": ("AUT", "Austria"),
    "AU": ("AUS", "Australia"), "AZ": ("AZE", "Azerbaijan"), "BD": ("BGD", "Bangladesh"),
    "BE": ("BEL", "Belgium"), "BG": ("BGR", "Bulgaria"), "BH": ("BHR", "Bahrain"),
    "BM": ("BMU", "Bermuda"), "BN": ("BRN", "Brunei"), "BO": ("BOL", "Bolivia"),
    "BR": ("BRA", "Brazil"), "BY": ("BLR", "Belarus"), "CA": ("CAN", "Canada"),
    "CD": ("COD", "Congo (Kinshasa)"), "CG": ("COG", "Congo (Brazzaville)"), "CH": ("CHE", "Switzerland"),
    "CL": ("CHL", "Chile"), "CM": ("CMR", "Cameroon"), "CN": ("CHN", "China"),
    "CO": ("COL", "Colombia"), "CU": ("CUB", "Cuba"), "CY": ("CYP", "Cyprus"),
    "CZ": ("CZE", "Czechia"), "DE": ("DEU", "Germany"), "DK": ("DNK", "Denmark"),
    "DZ": ("DZA", "Algeria"), "EC": ("ECU", "Ecuador"), "EE": ("EST", "Estonia"),
    "EG": ("EGY", "Egypt"), "ES": ("ESP", "Spain"), "FI": ("FIN", "Finland"),
    "FR": ("FRA", "France"), "GA": ("GAB", "Gabon"), "GB": ("GBR", "United Kingdom"),
    "GE": ("GEO", "Georgia"), "GH": ("GHA", "Ghana"), "GM": ("GMB", "Gambia"),
    "GQ": ("GNQ", "Equatorial Guinea"), "GR": ("GRC", "Greece"), "HK": ("HKG", "Hong Kong"),
    "HR": ("HRV", "Croatia"), "HU": ("HUN", "Hungary"), "ID": ("IDN", "Indonesia"),
    "IE": ("IRL", "Ireland"), "IN": ("IND", "India"), "IQ": ("IRQ", "Iraq"),
    "IR": ("IRN", "Iran"), "IS": ("ISL", "Iceland"), "IT": ("ITA", "Italy"),
    "JP": ("JPN", "Japan"), "KR": ("KOR", "South Korea"), "KW": ("KWT", "Kuwait"),
    "KZ": ("KAZ", "Kazakhstan"), "LT": ("LTU", "Lithuania"), "LU": ("LUX", "Luxembourg"),
    "LV": ("LVA", "Latvia"), "LY": ("LBY", "Libya"), "MA": ("MAR", "Morocco"),
    "MD": ("MDA", "Moldova"), "MK": ("MKD", "North Macedonia"), "MM": ("MMR", "Myanmar"),
    "MN": ("MNG", "Mongolia"), "MT": ("MLT", "Malta"), "MU": ("MUS", "Mauritius"),
    "MX": ("MEX", "Mexico"), "MY": ("MYS", "Malaysia"), "NE": ("NER", "Niger"),
    "NG": ("NGA", "Nigeria"), "NL": ("NLD", "Netherlands"), "NO": ("NOR", "Norway"),
    "NP": ("NPL", "Nepal"), "NZ": ("NZL", "New Zealand"), "OM": ("OMN", "Oman"),
    "PE": ("PER", "Peru"), "PG": ("PNG", "Papua New Guinea"), "PH": ("PHL", "Philippines"),
    "PK": ("PAK", "Pakistan"), "PL": ("POL", "Poland"), "PT": ("PRT", "Portugal"),
    "QA": ("QAT", "Qatar"), "RO": ("ROU", "Romania"), "RS": ("SRB", "Serbia"),
    "RU": ("RUS", "Russia"), "SA": ("SAU", "Saudi Arabia"), "SD": ("SDN", "Sudan"),
    "SE": ("SWE", "Sweden"), "SG": ("SGP", "Singapore"), "SI": ("SVN", "Slovenia"),
    "SK": ("SVK", "Slovakia"), "SS": ("SSD", "South Sudan"), "SY": ("SYR", "Syria"),
    "SZ": ("SWZ", "Eswatini"), "TD": ("TCD", "Chad"), "TH": ("THA", "Thailand"),
    "TJ": ("TJK", "Tajikistan"), "TM": ("TKM", "Turkmenistan"), "TN": ("TUN", "Tunisia"),
    "TR": ("TUR", "Turkiye"), "TT": ("TTO", "Trinidad and Tobago"), "TW": ("TWN", "Taiwan"),
    "UA": ("UKR", "Ukraine"), "US": ("USA", "United States"), "UZ": ("UZB", "Uzbekistan"),
    "VE": ("VEN", "Venezuela"), "VN": ("VNM", "Vietnam"), "YE": ("YEM", "Yemen"),
    "ZA": ("ZAF", "South Africa"),
}


# ----------------------------------------------------------------------------------- helpers

def _to_float(value: Any) -> Optional[float]:
    """``float(value)`` or ``None`` for anything non-numeric (``"ie"``, ``"--"``, ``"NA"``, ``"-"``,
    ``None``, booleans, NaN, infinities)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _kbd(value: float) -> float:
    """Round kb/d to 1 decimal (ARCHITECTURE §0) so re-runs are byte-stable."""
    return round(float(value), 1)


def _empty_ranking(reason: str, *, fetched_at: str, source: str = EIA_SOURCE,
                   source_url: str = EIA_SOURCE_URL) -> dict:
    return {
        "year": None, "source": source, "source_url": source_url, "unit": UNIT, "rows": [],
        "rest_of_world": None, "world_total": None, "note": "no data: %s" % reason,
        "as_of": None, "fetched_at": fetched_at, "stale": False,
    }


def _carry_ranking(old: Optional[dict], key: str, reason: str, *, fetched_at: str) -> Tuple[dict, bool]:
    """Previous ranking from ``old[key]`` marked stale (old timestamps kept), or an empty ranking.
    Returns ``(ranking, carried)``."""
    prev = old.get(key) if isinstance(old, dict) else None
    if isinstance(prev, dict) and isinstance(prev.get("rows"), list) and prev["rows"]:
        carried = copy.deepcopy(prev)
        carried["stale"] = True
        carried["carry_reason"] = reason
        return carried, True
    return _empty_ranking(reason, fetched_at=fetched_at), False


# ---------------------------------------------------------------------------------------- EIA

def parse_eia_payload(payload: Any) -> Dict[str, Dict[str, Any]]:
    """Group an EIA International API v2 response by year.

    Returns ``{year: {"world": float|None, "countries": [{"iso3", "name", "value"}, ...],
    "product_name": str|None, "skipped": int}}`` where ``countries`` holds only rows with
    ``countryRegionTypeId == "c"`` **and** a 3-letter A-Z id (drops regions such as WORL/OPEC/EU27
    and the WP16-style group ids, plus pseudo-countries like DEUW/HITZ) and a numeric value
    (``"ie"``, ``"--"``, ``"NA"``, ``None`` are skipped and counted in ``skipped``).
    Raises ``ValueError`` when the payload has no ``response.data`` list.
    """
    if not isinstance(payload, dict):
        raise ValueError("EIA payload is not a JSON object")
    response = payload.get("response")
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, list):
        err = payload.get("error") if payload.get("error") else (
            response.get("warnings") if isinstance(response, dict) else None)
        raise ValueError("EIA payload has no response.data (%s)" % (err or "unknown reason"))
    by_year: Dict[str, Dict[str, Any]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        year = str(row.get("period") or "").strip()[:4]
        if not year.isdigit():
            continue
        entry = by_year.setdefault(year, {"world": None, "countries": [], "product_name": None, "skipped": 0})
        cid = str(row.get("countryRegionId") or "").strip()
        value = _to_float(row.get("value"))
        if entry["product_name"] is None and row.get("productName"):
            entry["product_name"] = str(row["productName"])
        if cid == WORLD_ID:
            if value is not None:
                entry["world"] = value
            else:
                entry["skipped"] += 1
            continue
        if row.get("countryRegionTypeId") != "c" or not ISO3_RE.match(cid):
            continue
        if value is None:
            entry["skipped"] += 1
            continue
        name = str(row.get("countryRegionName") or cid).strip() or cid
        entry["countries"].append({"iso3": cid, "name": name, "value": value})
    return by_year


def select_year(by_year: Mapping[str, Mapping[str, Any]], min_rows: int = MIN_COUNTRY_ROWS) -> Optional[str]:
    """Newest year that has a numeric WORL row and at least ``min_rows`` numeric country rows."""
    for year in sorted(by_year, reverse=True):
        entry = by_year[year]
        if entry.get("world") is not None and len(entry.get("countries") or []) >= min_rows:
            return year
    return None


def build_eia_ranking(by_year: Mapping[str, Mapping[str, Any]], kind: str, *, fetched_at: str,
                      as_of: str) -> Optional[dict]:
    """Top-10 ranking for the newest usable year, or ``None`` if no year qualifies.

    ``rest_of_world`` is computed from the *rounded* numbers in the file so that
    ``sum(rows) + rest_of_world == world_total`` holds exactly for what is published.
    """
    year = select_year(by_year)
    if year is None:
        return None
    entry = by_year[year]
    ranked = sorted(entry["countries"], key=lambda r: (-r["value"], r["iso3"]))
    rows = [{"iso3": r["iso3"], "name": r["name"], "value": _kbd(r["value"])} for r in ranked[:TOP_N]]
    world_total = _kbd(entry["world"])
    rest_of_world = _kbd(world_total - sum(r["value"] for r in rows))
    return {
        "year": int(year),
        "source": EIA_SOURCE,
        "source_url": EIA_SOURCE_URL,
        "unit": UNIT,
        "rows": rows,
        "rest_of_world": rest_of_world,
        "world_total": world_total,
        "note": entry.get("product_name") or EIA_SERIES[kind]["note"],
        "country_count": len(ranked),
        "as_of": as_of,
        "fetched_at": fetched_at,
        "stale": False,
    }


def eia_params(kind: str, api_key: str, now: datetime) -> Dict[str, str]:
    """Query parameters for one EIA International request (built as ``params`` so ``requests``
    encodes the ``[]`` facets and the key never appears in a logged URL)."""
    spec = EIA_SERIES[kind]
    return {
        "api_key": api_key,
        "frequency": "annual",
        "data[0]": "value",
        "facets[activityId][]": spec["activityId"],
        "facets[productId][]": spec["productId"],
        "facets[unit][]": EIA_UNIT_FACET,
        "start": str(now.year - EIA_START_YEARS_BACK),
        "length": str(EIA_PAGE_LENGTH),
    }


def _mask(text: str, secret: str) -> str:
    """Hide ``secret`` in an error text that may end up in meta.json or the Actions log.
    Secrets shorter than 8 characters are not real API keys and would only mangle the text."""
    return text.replace(secret, "***") if secret and len(secret) >= 8 else text


def _fetch_eia_payload(kind: str, api_key: str, now: datetime, session: Any) -> Dict[str, Dict[str, Any]]:
    """One live EIA request for ``kind`` -> ``parse_eia_payload`` result. Raises ``FetchError``."""
    resp = common.http_get(EIA_URL, params=eia_params(kind, api_key, now), session=session)
    try:
        try:
            payload = resp.json()
        except ValueError as exc:
            raise common.FetchError("EIA international %s: response is not JSON: %s"
                                    % (kind, _mask(str(exc)[:160], api_key))) from exc
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            close()
    try:
        by_year = parse_eia_payload(payload)
    except ValueError as exc:
        raise common.FetchError("EIA international %s: %s" % (kind, _mask(str(exc), api_key))) from exc
    response = payload.get("response") if isinstance(payload, dict) else None
    if isinstance(response, dict):
        total = _to_float(response.get("total"))
        got = len(response.get("data") or [])
        if total is not None and total > got:
            common.log("countries: EIA %s returned %d of %d rows (not paging)" % (kind, got, int(total)))
    return by_year


def _load_eia_fixture(kind: str) -> Dict[str, Dict[str, Any]]:
    parts = FIXTURE_EIA_PROD if kind == "producers" else FIXTURE_EIA_CONS
    path = common.FIXTURES.joinpath(*parts)
    payload = common.read_json(path)
    if payload is None:
        raise common.FetchError("fixture missing or unreadable: %s" % path)
    return parse_eia_payload(payload)


# --------------------------------------------------------------------------------------- JODI

def parse_jodi(lines: Iterable[str]) -> Dict[str, Dict[str, float]]:
    """Stream a JODI primary CSV -> ``{"YYYY-MM": {iso2: kb/d}}`` for CRUDEOIL/INDPROD/KBD rows.

    Works on any iterable of text lines (an open file or decoded HTTP chunks), keeps only the
    matching rows in memory. ``-`` (not reported) and any other non-numeric OBS_VALUE leaves the
    country out of that month; it is never turned into 0. Raises ``ValueError`` on a header that
    lacks the expected columns.
    """
    reader = csv.reader(lines)
    header = next(reader, None)
    if header is None:
        raise ValueError("JODI CSV is empty")
    names = [h.strip().lstrip("﻿").upper() for h in header]
    try:
        idx = {col: names.index(col) for col in JODI_COLUMNS}
    except ValueError:
        raise ValueError("JODI CSV: unexpected header %r" % (names,))
    i_area, i_period = idx["REF_AREA"], idx["TIME_PERIOD"]
    i_prod, i_flow, i_unit, i_val = idx["ENERGY_PRODUCT"], idx["FLOW_BREAKDOWN"], idx["UNIT_MEASURE"], idx["OBS_VALUE"]
    width = max(idx.values()) + 1
    table: Dict[str, Dict[str, float]] = {}
    for row in reader:
        if len(row) < width:
            continue
        if row[i_prod].strip() != JODI_PRODUCT or row[i_flow].strip() != JODI_FLOW or row[i_unit].strip() != JODI_UNIT:
            continue
        area = row[i_area].strip().upper()
        month = row[i_period].strip()
        if not ISO2_RE.match(area) or not MONTH_RE.match(month):
            continue
        value = _to_float(row[i_val])
        if value is None:
            continue  # "-" gap: the country simply does not report that month
        table.setdefault(month, {})[area] = value
    return table


def jodi_latest_month(table: Mapping[str, Mapping[str, float]],
                      min_countries: int = JODI_MIN_COUNTRIES) -> Optional[str]:
    """Newest month with at least ``min_countries`` reporting (numeric) countries."""
    for month in sorted(table, reverse=True):
        if len(table[month]) >= min_countries:
            return month
    return None


def jodi_row(iso2: str, value: float) -> dict:
    row: Dict[str, Any] = {"iso2": iso2}
    known = JODI_COUNTRIES.get(iso2)
    if known is not None:
        row["iso3"] = known[0]
        row["name"] = known[1]
    else:
        row["name"] = iso2
    row["value"] = _kbd(value)
    return row


def build_monthly_crude(table: Mapping[str, Mapping[str, float]], *, fetched_at: str,
                        as_of: str) -> Optional[dict]:
    """``monthly_crude`` block (top 10 of the latest usable month) or ``None``."""
    month = jodi_latest_month(table)
    if month is None:
        return None
    ranked = sorted(table[month].items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "source": JODI_SOURCE,
        "source_url": JODI_SOURCE_URL,
        "latest_month": month,
        "unit": UNIT,
        "rows": [jodi_row(iso2, value) for iso2, value in ranked[:TOP_N]],
        "note": "Crude oil only (JODI CRUDEOIL/INDPROD), monthly; countries that did not report "
                "this month are omitted, gaps are never counted as zero",
        "country_count": len(ranked),
        "as_of": as_of,
        "fetched_at": fetched_at,
        "stale": False,
    }


def _decoded_lines(resp: Any) -> Iterator[str]:
    """Text lines of a streamed response without loading the body into memory."""
    for raw in resp.iter_lines(chunk_size=65536):
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8-sig", errors="replace")
        yield raw


def _load_jodi(fixtures: bool, now: datetime, session: Any) -> Dict[str, Dict[str, float]]:
    """JODI month table from the fixture or from the live CSV (this year's file, then last
    year's while the new one is still empty). Raises ``FetchError`` when nothing usable loads."""
    if fixtures:
        path = common.FIXTURES.joinpath(*FIXTURE_JODI)
        if not path.exists():
            raise common.FetchError("fixture missing: %s" % path)
        with open(path, "r", newline="", encoding="utf-8") as fh:
            return parse_jodi(fh)
    errors: List[str] = []
    for year in (now.year, now.year - 1):
        url = JODI_URL_TEMPLATE.format(year=year)
        try:
            resp = common.http_get(url, session=session, stream=True, max_bytes=MAX_JODI_BYTES)
        except common.FetchError as exc:
            errors.append(str(exc))
            continue
        try:
            table = parse_jodi(_decoded_lines(resp))
        except ValueError as exc:
            errors.append("%s: %s" % (url, exc))
            continue
        finally:
            close = getattr(resp, "close", None)
            if callable(close):
                close()
        if jodi_latest_month(table) is not None:
            return table
        errors.append("%s: no month with >= %d reporting countries" % (url, JODI_MIN_COUNTRIES))
    raise common.FetchError("JODI primary CSV unusable: " + "; ".join(errors))


# ---------------------------------------------------------------------------------------- run

def run(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None,
        session: Any = None, env: Optional[Mapping[str, str]] = None,
        context: Optional[dict] = None) -> Tuple[dict, dict]:
    """Build ``(doc, info)`` for countries.json (ARCHITECTURE §2/§3.3). Raises when neither the
    EIA API nor JODI yields a producers ranking; ``update.py`` then keeps the old file as stale."""
    del cfg, context  # not needed: thresholds are applied by update.py, no cross-document inputs
    now = now if now is not None else common.now_utc()
    env = os.environ if env is None else env
    old = old if isinstance(old, dict) else None
    fetched_at = common.iso_utc(now)
    as_of = fetched_at[:10]
    info: Dict[str, Any] = {"partial": []}

    # --- JODI (always attempted: monthly block, and the producers fallback) --------------------
    jodi_table: Optional[Dict[str, Dict[str, float]]] = None
    try:
        jodi_table = _load_jodi(fixtures, now, session)
    except Exception as exc:  # noqa: BLE001  (one broken source never aborts the run)
        info["jodi_error"] = ("%s: %s" % (type(exc).__name__, exc))[:300]
        common.log("countries: JODI failed: %s" % info["jodi_error"])
    monthly_crude = build_monthly_crude(jodi_table, fetched_at=fetched_at, as_of=as_of) if jodi_table else None
    if monthly_crude is not None:
        info["jodi_month"] = monthly_crude["latest_month"]
        common.log("countries: JODI %s, %d reporting countries"
                   % (monthly_crude["latest_month"], monthly_crude["country_count"]))

    # --- EIA International ---------------------------------------------------------------------
    eia: Dict[str, Optional[dict]] = {"producers": None, "consumers": None}
    forced = str(env.get("CRACKSPREAD_COUNTRIES_VIA", "") or "").strip().lower() == "jodi"
    api_key = str(env.get("EIA_API_KEY", "") or "").strip()
    if forced:
        info["eia_error"] = "skipped: CRACKSPREAD_COUNTRIES_VIA=jodi"
    elif fixtures:
        for kind in ("producers", "consumers"):
            eia[kind] = build_eia_ranking(_load_eia_fixture(kind), kind, fetched_at=fetched_at, as_of=as_of)
    elif not api_key:
        info["eia_error"] = "skipped: EIA_API_KEY not set"
    else:
        for kind in ("producers", "consumers"):
            try:
                by_year = _fetch_eia_payload(kind, api_key, now, session)
            except Exception as exc:  # noqa: BLE001
                info["eia_error"] = _mask(("%s: %s" % (type(exc).__name__, exc))[:300], api_key)
                common.log("countries: EIA %s failed: %s" % (kind, info["eia_error"]))
                break  # a 403/429 would hit the second request too; save the quota
            eia[kind] = build_eia_ranking(by_year, kind, fetched_at=fetched_at, as_of=as_of)
            if eia[kind] is None:
                info["eia_error"] = "no year with a WORL row and >= %d country rows (%s)" % (MIN_COUNTRY_ROWS, kind)
                common.log("countries: EIA %s: %s" % (kind, info["eia_error"]))
    if "eia_error" in info:
        common.log("countries: EIA International unavailable (%s)" % info["eia_error"])

    # --- producers ------------------------------------------------------------------------------
    if eia["producers"] is not None:
        via = "eia_api"
        producers = eia["producers"]
    else:
        if monthly_crude is None:
            raise common.FetchError(
                "countries: EIA International unavailable (%s) and JODI unusable (%s)"
                % (info.get("eia_error", "no producers year"), info.get("jodi_error", "no usable month")))
        via = "jodi"
        producers = {
            "year": int(monthly_crude["latest_month"][:4]),
            "period": monthly_crude["latest_month"],
            "source": JODI_SOURCE,
            "source_url": JODI_SOURCE_URL,
            "unit": UNIT,
            "rows": copy.deepcopy(monthly_crude["rows"]),
            "rest_of_world": None,
            "world_total": None,
            "note": "crude oil only, monthly, JODI (%s); EIA International API unavailable: %s"
                    % (monthly_crude["latest_month"], info.get("eia_error", "no usable year")),
            "country_count": monthly_crude["country_count"],
            "as_of": as_of,
            "fetched_at": fetched_at,
            "stale": False,
        }

    # --- consumers ------------------------------------------------------------------------------
    if eia["consumers"] is not None:
        consumers = eia["consumers"]
    else:
        reason = info.get("eia_error", "EIA consumption data has no usable year")
        consumers, carried = _carry_ranking(old, "consumers", reason, fetched_at=fetched_at)
        if carried:
            info["partial"].append("consumers")
            common.log("countries: consumers carried from previous file (stale): %s" % reason)
        else:
            common.log("countries: no consumers available (%s)" % reason)

    # --- monthly crude: carry the old block if JODI failed this run -----------------------------
    if monthly_crude is None:
        prev = old.get("monthly_crude") if old else None
        if isinstance(prev, dict) and isinstance(prev.get("rows"), list) and prev["rows"]:
            monthly_crude = copy.deepcopy(prev)
            monthly_crude["stale"] = True
            monthly_crude["carry_reason"] = info.get("jodi_error", "no usable JODI month")
            info["partial"].append("monthly_crude")

    doc = {
        "schema_version": 1,
        "generated_at": fetched_at,
        "as_of": as_of,
        "fetched_at": fetched_at,
        "stale": False,
        "source": EIA_SOURCE if via == "eia_api" else JODI_SOURCE,
        "source_url": EIA_SOURCE_URL if via == "eia_api" else JODI_SOURCE_URL,
        "via": via,
        "unit": UNIT,
        "year": producers["year"],
        "producers": producers,
        "consumers": consumers,
        "monthly_crude": monthly_crude,
    }
    info["via"] = via
    info["producers_year"] = producers["year"]
    info["consumers_year"] = consumers.get("year")
    common.log("countries: via %s, producers %s (%d rows), consumers %s (%d rows), monthly %s"
               % (via, producers["year"], len(producers["rows"]), consumers.get("year"),
                  len(consumers["rows"]), (monthly_crude or {}).get("latest_month")))
    return doc, info
