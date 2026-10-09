"""FRED → ``site/data/prices.json``: crude/product prices, crack spreads, history and stats.

Implements ARCHITECTURE §2 / §3.1 and brief §5.1 / §5.3 / §6.1 / §7.

* Eight FRED series are downloaded as CSV (``fredgraph.csv?id=<SERIES>``) through
  :func:`common.http_get`, which sets the bot User-Agent (FRED drops browser UAs). FRED marks
  missing days with ``.``; the current ``fredgraph.csv`` format leaves the field empty instead.
  Both are treated as "no observation".
* Crack spreads ($/bbl, 1 bbl = 42 gal) are computed only on dates where **all** inputs exist.
* Every ``latest`` entry is a datapoint whose ``as_of`` is the observation date of that series
  (the weekly retail series carry their own Monday dates and ``stale_kind = "weekly"``).
* A series that fails (network, parse, plausibility) is carried forward from ``old`` as stale and
  listed in ``info["partial"]``; the run only raises when no series at all could be fetched.
* History is weekly (last trading day of each ISO week) before ``newest observation - 365 d``
  and daily after (anchored to the data so the file only changes when FRED publishes).
  Stats (max, means, percentile, level) use the full daily series since 2006-06-14.

Flat module: ``import common``. No stdout, no ``sys.exit``. Python 3.9 and 3.12.
"""
from __future__ import annotations

import copy
import csv
import io
import math
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import common

SOURCE_KEY = "fred"
OUTPUT_FILE = "prices.json"
SCHEMA = "prices"
STALE_KIND = "prices"

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRED_SERIES_URL = "https://fred.stlouisfed.org/series/{id}"
SOURCE_NAME = "FRED, Federal Reserve Bank of St. Louis; data: U.S. EIA"
SOURCE_URL = "https://fred.stlouisfed.org/"

GAL_PER_BBL = 42
HISTORY_START = "2006-06-14"          # first NY Harbor ULSD observation on FRED
DAILY_WINDOW_DAYS = 365               # daily resolution for the last year, weekly before
PRICE_DECIMALS = 3                    # ARCHITECTURE §0: prices 3 decimals
PERCENTILE_DECIMALS = 1
MISSING_MARKERS = ("", ".")
MAX_CSV_BYTES = 5 * 1024 * 1024       # a full daily FRED history is ~250 KB

CRUDE_RANGE = (5.0, 400.0)            # $/bbl   (brief §7)
PRODUCT_RANGE = (0.3, 15.0)           # $/gal

DEFAULT_LEVEL_LABELS = ["Hairline", "Visible", "Widening (said with a straight face)", "Gaping", "Grand Canyon"]
DEFAULT_PERCENTILE_CUTS = [50, 75, 90, 98]

#: latest-key → series spec, in output order. ``freq`` "weekly" series get ``stale_kind="weekly"``.
SERIES: List[Tuple[str, Dict[str, Any]]] = [
    ("brent",              {"id": "DCOILBRENTEU", "unit": "USD/bbl", "range": CRUDE_RANGE,   "freq": "daily",
                            "name": "Crude Oil Prices: Brent - Europe"}),
    ("wti",                {"id": "DCOILWTICO",   "unit": "USD/bbl", "range": CRUDE_RANGE,   "freq": "daily",
                            "name": "Crude Oil Prices: West Texas Intermediate (WTI) - Cushing, Oklahoma"}),
    ("ulsd_nyh",           {"id": "DDFUELNYH",    "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "daily",
                            "name": "New York Harbor Ultra-Low Sulfur No 2 Diesel Spot Price"}),
    ("gasoline_nyh",       {"id": "DGASNYH",      "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "daily",
                            "name": "New York Harbor Conventional Gasoline Regular Spot Price"}),
    ("jet_gulf",           {"id": "DJFUELUSGULF", "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "daily",
                            "name": "Kerosene-Type Jet Fuel Prices: U.S. Gulf Coast"}),
    ("heating_oil_nyh",    {"id": "DHOILNYH",     "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "daily",
                            "name": "No. 2 Heating Oil Prices: New York Harbor"}),
    ("retail_diesel_us",   {"id": "GASDESW",      "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "weekly",
                            "name": "US Diesel Sales Price (weekly, Monday)"}),
    ("retail_gasoline_us", {"id": "GASREGW",      "unit": "USD/gal", "range": PRODUCT_RANGE, "freq": "weekly",
                            "name": "US Regular All Formulations Gas Price (weekly, Monday)"}),
]
SERIES_BY_KEY: Dict[str, Dict[str, Any]] = dict(SERIES)

#: Crack spreads (brief §5.3). ``inputs`` are FRED series ids; ``fn`` gets {series_id: value}.
CRACKS: List[Tuple[str, Dict[str, Any]]] = [
    ("diesel_crack",   {"formula": "DDFUELNYH*42 - DCOILBRENTEU",
                        "inputs": ("DDFUELNYH", "DCOILBRENTEU"),
                        "fn": lambda v: v["DDFUELNYH"] * GAL_PER_BBL - v["DCOILBRENTEU"],
                        "source_series": "DDFUELNYH"}),
    ("gasoline_crack", {"formula": "DGASNYH*42 - DCOILBRENTEU",
                        "inputs": ("DGASNYH", "DCOILBRENTEU"),
                        "fn": lambda v: v["DGASNYH"] * GAL_PER_BBL - v["DCOILBRENTEU"],
                        "source_series": "DGASNYH"}),
    ("jet_crack",      {"formula": "DJFUELUSGULF*42 - DCOILBRENTEU",
                        "inputs": ("DJFUELUSGULF", "DCOILBRENTEU"),
                        "fn": lambda v: v["DJFUELUSGULF"] * GAL_PER_BBL - v["DCOILBRENTEU"],
                        "source_series": "DJFUELUSGULF"}),
    ("crack_321",      {"formula": "(2*DGASNYH*42 + 1*DDFUELNYH*42)/3 - DCOILWTICO",
                        "inputs": ("DGASNYH", "DDFUELNYH", "DCOILWTICO"),
                        "fn": lambda v: (2 * v["DGASNYH"] * GAL_PER_BBL + 1 * v["DDFUELNYH"] * GAL_PER_BBL) / 3
                        - v["DCOILWTICO"],
                        "source_series": "DGASNYH"}),
]
CRACKS_BY_KEY: Dict[str, Dict[str, Any]] = dict(CRACKS)

SPREAD_KEY = "brent_wti_spread"
SPREAD_FORMULA = "DCOILBRENTEU - DCOILWTICO"
SPREAD_INPUTS = ("DCOILBRENTEU", "DCOILWTICO")

#: Series whose history/stats columns live in ``history``; if any of these fails, history and
#: stats are carried forward from ``old`` (see :func:`run`).
HISTORY_COLUMNS = ["diesel_crack", "gasoline_crack", "jet_crack", "crack_321", "brent", "wti"]
HISTORY_INPUT_SERIES = ("DCOILBRENTEU", "DCOILWTICO", "DDFUELNYH", "DGASNYH", "DJFUELUSGULF")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEADER_DATE_COLUMNS = ("observation_date", "date")

Observations = List[Tuple[str, Optional[float]]]
Values = Dict[str, float]                       # date → value (non-missing only)


# ------------------------------------------------------------------------------- CSV parsing

def parse_fred_csv(text: str, series_id: Optional[str] = None) -> Observations:
    """Parse a FRED ``fredgraph.csv`` body into ``[(YYYY-MM-DD, value|None), …]`` sorted by date.

    Accepts both header styles (``observation_date,<ID>`` and ``DATE,<ID>``). ``.`` and empty
    fields (FRED's two ways of marking a missing observation) become ``None``; so does anything
    non-numeric. Raises ``ValueError`` when the body is not a FRED CSV for ``series_id``
    (e.g. an HTML error page) or holds no observations at all.
    """
    body = text.lstrip("﻿")
    reader = csv.reader(io.StringIO(body))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("FRED CSV is empty")
    cols = [c.strip() for c in header]
    if len(cols) < 2 or cols[0].lower() not in _HEADER_DATE_COLUMNS:
        raise ValueError("not a FRED CSV (header %r)" % (",".join(cols)[:80],))
    if series_id is not None and cols[1] != series_id:
        raise ValueError("FRED CSV is for %r, expected %r" % (cols[1], series_id))

    by_date: Dict[str, Optional[float]] = {}
    for row in reader:
        if not row or not row[0].strip():
            continue
        day = row[0].strip()
        if not _DATE_RE.match(day):
            raise ValueError("FRED CSV: bad date %r" % day[:40])
        raw = row[1].strip() if len(row) > 1 else ""
        by_date[day] = _parse_value(raw)
    if not by_date:
        raise ValueError("FRED CSV for %s holds no observations" % (series_id or cols[1]))
    return sorted(by_date.items())


def _parse_value(raw: str) -> Optional[float]:
    if raw in MISSING_MARKERS:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def values_of(obs: Observations) -> Values:
    """``{date: value}`` for the non-missing observations only."""
    return {d: v for d, v in obs if v is not None}


def latest_observation(obs: Observations) -> Tuple[str, float]:
    """The newest non-missing ``(date, value)``. Raises ``ValueError`` if there is none."""
    for day, value in reversed(obs):
        if value is not None:
            return day, value
    raise ValueError("series has no non-missing observation")


# ------------------------------------------------------------------------------------ fetch

def fetch_series_text(series_id: str, *, fixtures: bool = False, session: Any = None) -> str:
    """CSV body for ``series_id``: from ``tests/fixtures/fred/<id>.csv`` in fixtures mode,
    otherwise from FRED via :func:`common.http_get` (bot UA, timeout/retries from common)."""
    if fixtures:
        path = common.FIXTURES / "fred" / ("%s.csv" % series_id)
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    resp = common.http_get(FRED_CSV_URL, params={"id": series_id}, session=session, max_bytes=MAX_CSV_BYTES)
    return resp.text


def fetch_observations(series_id: str, *, fixtures: bool = False, session: Any = None) -> Observations:
    return parse_fred_csv(fetch_series_text(series_id, fixtures=fixtures, session=session), series_id)


# ----------------------------------------------------------------------------- computations

def _round(value: Optional[float], decimals: int = PRICE_DECIMALS) -> Optional[float]:
    if value is None:
        return None
    return float(round(value, decimals))


def compute_crack(values: Mapping[str, Values], inputs: Sequence[str], fn: Callable[[Mapping[str, float]], float],
                  start: str = HISTORY_START) -> Values:
    """``{date: crack}`` for every date ≥ ``start`` on which **all** input series have a value.
    Missing inputs (FRED ``.``/empty) simply yield no entry for that date."""
    if any(sid not in values for sid in inputs):
        return {}
    common_dates = set(values[inputs[0]])
    for sid in inputs[1:]:
        common_dates &= set(values[sid])
    out: Values = {}
    for day in sorted(common_dates):
        if day < start:
            continue
        out[day] = _round(fn({sid: values[sid][day] for sid in inputs}))
    return out


def compute_all_cracks(values: Mapping[str, Values]) -> Dict[str, Values]:
    return {key: compute_crack(values, spec["inputs"], spec["fn"]) for key, spec in CRACKS}


def level_for_percentile(percentile: Optional[float], cuts: Sequence[float]) -> Optional[int]:
    """0..len(cuts): number of cuts that are ≤ percentile (``<50 → 0, 50–75 → 1, …, ≥98 → 4``)."""
    if percentile is None:
        return None
    return sum(1 for cut in cuts if percentile >= cut)


def crack_levels(cfg: Mapping[str, Any]) -> Tuple[List[str], List[float]]:
    levels = cfg.get("crack_levels") if isinstance(cfg, dict) else None
    labels = list(levels.get("labels", DEFAULT_LEVEL_LABELS)) if isinstance(levels, dict) else list(DEFAULT_LEVEL_LABELS)
    cuts = list(levels.get("percentile_cuts", DEFAULT_PERCENTILE_CUTS)) if isinstance(levels, dict) \
        else list(DEFAULT_PERCENTILE_CUTS)
    if len(labels) != len(cuts) + 1:
        raise ValueError("crack_levels: need len(labels) == len(percentile_cuts) + 1")
    return labels, [float(c) for c in cuts]


def percentile_of(values: Sequence[float], current: float) -> float:
    """Share of ``values`` that are ≤ ``current``, in percent (0..100)."""
    if not values:
        raise ValueError("percentile of an empty series")
    return 100.0 * sum(1 for v in values if v <= current) / len(values)


def _mean(values: Sequence[float]) -> Optional[float]:
    return (sum(values) / len(values)) if values else None


def _mean_between(series: Values, start: str, end: str) -> Optional[float]:
    return _mean([v for d, v in series.items() if start <= d <= end])


def compute_stats(series: Values, now: datetime, cfg: Mapping[str, Any], *, with_level: bool = True) -> Dict[str, Any]:
    """Stats for one daily crack series (full daily resolution): max/max_date, mean 2015–2019,
    mean of the previous calendar year, percentile of the newest value, level + label."""
    labels, cuts = crack_levels(cfg)
    prev_year = now.year - 1
    if not series:
        stats: Dict[str, Any] = {"max": None, "max_date": None, "mean_2015_2019": None, "mean_prev_year": None,
                                 "prev_year": prev_year, "percentile_now": None, "value": None, "as_of": None,
                                 "history_start": None, "n": 0}
        if with_level:
            stats.update(level=None, level_label=None)
        return stats
    dates = sorted(series)
    as_of = dates[-1]
    current = series[as_of]
    max_date, max_value = dates[0], series[dates[0]]
    for day in dates:                       # highest value; the first occurrence wins a tie
        if series[day] > max_value:
            max_date, max_value = day, series[day]
    percentile = _round(percentile_of([series[d] for d in dates], current), PERCENTILE_DECIMALS)
    stats = {
        "max": _round(max_value),
        "max_date": max_date,
        "mean_2015_2019": _round(_mean_between(series, "2015-01-01", "2019-12-31")),
        "mean_prev_year": _round(_mean_between(series, "%d-01-01" % prev_year, "%d-12-31" % prev_year)),
        "prev_year": prev_year,
        "percentile_now": percentile,
        "value": _round(current),
        "as_of": as_of,
        "history_start": dates[0],
        "n": len(dates),
        "note": "Daily values since %s; percentile_now = share of days with a value <= the current one." % dates[0],
    }
    if with_level:
        level = level_for_percentile(percentile, cuts)
        stats["level"] = level
        stats["level_label"] = labels[level] if level is not None else None
    return stats


def build_history(columns: Mapping[str, Values], now: datetime) -> Dict[str, Any]:
    """Aligned arrays: weekly (last trading day of each ISO week, preferring the day with the most
    series present) before ``<newest observation> - 365 d``, daily from then on. The window is
    anchored to the data, not to the clock, so prices.json stays byte-stable on days without a
    new FRED observation (weekends, holidays); ``now`` only anchors an empty series. Dates start
    at HISTORY_START."""
    all_dates = set()
    for col in columns.values():
        all_dates.update(col)
    dates = sorted(d for d in all_dates if d >= HISTORY_START)
    anchor = date.fromisoformat(dates[-1]) if dates else now.date()
    cutoff = (anchor - timedelta(days=DAILY_WINDOW_DAYS)).isoformat()

    def present(day: str) -> int:
        return sum(1 for col in columns.values() if day in col)

    picked: List[str] = []
    week_key = None
    best_day, best_count = None, -1
    for day in dates:
        if day >= cutoff:
            break
        wk = date.fromisoformat(day).isocalendar()[:2]
        if wk != week_key:
            if best_day is not None:
                picked.append(best_day)
            week_key, best_day, best_count = wk, day, present(day)
            continue
        count = present(day)
        if count >= best_count:            # "last available trading day" with full data wins
            best_day, best_count = day, count
    if best_day is not None:
        picked.append(best_day)
    picked.extend(d for d in dates if d >= cutoff)

    history: Dict[str, Any] = {"dates": picked}
    for name in HISTORY_COLUMNS:
        col = columns.get(name, {})
        history[name] = [_round(col.get(d)) for d in picked]
    history["resolution_note"] = "weekly before %s, daily after" % cutoff
    history["daily_from"] = cutoff
    return history


# -------------------------------------------------------------------------------- datapoints

def _series_url(series_id: str) -> str:
    return FRED_SERIES_URL.format(id=series_id)


def series_datapoint(key: str, obs: Observations, fetched_at: str) -> Dict[str, Any]:
    """Latest datapoint of one FRED series; raises PlausibilityError outside the allowed range."""
    spec = SERIES_BY_KEY[key]
    as_of, raw = latest_observation(obs)
    lo, hi = spec["range"]
    value = common.ensure_range(raw, lo, hi, spec["id"])
    extra: Dict[str, Any] = {"series_id": spec["id"]}
    if spec["freq"] == "weekly":
        extra["stale_kind"] = "weekly"
        extra["note"] = "Weekly average, dated by the Monday of the survey week."
    return common.datapoint(_round(value), spec["unit"], as_of, "FRED %s" % spec["id"], _series_url(spec["id"]),
                            fetched_at=fetched_at, stale=False, **extra)


def crack_datapoint(key: str, values: Mapping[str, Values], fetched_at: str) -> Dict[str, Any]:
    """Latest crack spread: computed on the newest date where all inputs exist."""
    spec = CRACKS_BY_KEY[key]
    series = compute_crack(values, spec["inputs"], spec["fn"])
    if not series:
        raise ValueError("%s: no date on which all inputs exist" % key)
    as_of = max(series)
    inputs = {sid: _round(values[sid][as_of]) for sid in spec["inputs"]}
    return common.datapoint(series[as_of], "USD/bbl", as_of, "FRED (EIA data), %s" % " & ".join(spec["inputs"]),
                            _series_url(spec["source_series"]), fetched_at=fetched_at, stale=False,
                            formula=spec["formula"], inputs=inputs)


def _spread_fn(v: Mapping[str, float]) -> float:
    return v["DCOILBRENTEU"] - v["DCOILWTICO"]


def spread_datapoint(values: Mapping[str, Values], fetched_at: str) -> Dict[str, Any]:
    series = compute_crack(values, SPREAD_INPUTS, _spread_fn)
    if not series:
        raise ValueError("%s: no common date" % SPREAD_KEY)
    as_of = max(series)
    inputs = {sid: _round(values[sid][as_of]) for sid in SPREAD_INPUTS}
    return common.datapoint(series[as_of], "USD/bbl", as_of, "FRED (EIA data), DCOILBRENTEU & DCOILWTICO",
                            _series_url("DCOILBRENTEU"), fetched_at=fetched_at, stale=False,
                            formula=SPREAD_FORMULA, inputs=inputs)


def _carry_forward(old: Optional[dict], key: str, unit: str, now: datetime, reason: str) -> Dict[str, Any]:
    """The previous run's ``latest[key]`` marked stale (fetched_at untouched); if there is none,
    a value-less datapoint so that the document keeps its shape."""
    prev = (old or {}).get("latest") if isinstance(old, dict) else None
    item = prev.get(key) if isinstance(prev, dict) else None
    if isinstance(item, dict) and isinstance(item.get("as_of"), str) and item.get("as_of"):
        dp = copy.deepcopy(item)
        dp["stale"] = True
        dp["carried_forward"] = True
        return dp
    spec = SERIES_BY_KEY.get(key)
    extra: Dict[str, Any] = {"note": "fetch failed and no previous value is available"}
    if spec is not None:
        extra["series_id"] = spec["id"]
        if spec["freq"] == "weekly":
            extra["stale_kind"] = "weekly"
        source, url = "FRED %s" % spec["id"], _series_url(spec["id"])
    else:
        source, url = SOURCE_NAME, SOURCE_URL
    common.log("prices: %s unavailable (%s); no previous value to carry forward" % (key, reason))
    return common.datapoint(None, unit, now.strftime("%Y-%m-%d"), source, url, fetched_at=common.iso_utc(now),
                            stale=True, **extra)


# --------------------------------------------------------------------------------------- run

def run(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None,
        session: Any = None, env: Optional[Mapping[str, str]] = None,
        context: Optional[dict] = None) -> Tuple[dict, dict]:
    """Build ``(doc, info)`` for prices.json (ARCHITECTURE §2). Not validated/written here.

    Per-series failures (FetchError, PlausibilityError, parse errors) carry the old sub-item
    forward as stale and are listed in ``info["partial"]`` (with the error text in
    ``info["errors"]``). Raises ``common.FetchError`` only if **no** series could be fetched.
    """
    del env, context  # not needed for FRED (no key); accepted for the common fetcher signature
    now = common.parse_iso(now) if now is not None else common.now_utc()
    fetched_at = common.iso_utc(now)

    observations: Dict[str, Observations] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    partial: List[str] = []
    errors: Dict[str, str] = {}

    for key, spec in SERIES:
        try:
            obs = fetch_observations(spec["id"], fixtures=fixtures, session=session)
            latest[key] = series_datapoint(key, obs, fetched_at)
        except Exception as exc:  # noqa: BLE001  — one broken series never aborts the run
            reason = "%s: %s" % (type(exc).__name__, exc)
            common.log("prices: %s (%s) failed: %s" % (key, spec["id"], reason[:300]))
            errors[key] = reason[:300]
            latest[key] = _carry_forward(old, key, spec["unit"], now, reason)
            partial.append(key)
            continue
        observations[spec["id"]] = obs

    if not observations:
        raise common.FetchError("FRED: none of the %d series could be fetched: %s"
                                % (len(SERIES), "; ".join("%s=%s" % kv for kv in errors.items())))

    values: Dict[str, Values] = {sid: values_of(obs) for sid, obs in observations.items()}

    # derived latest values: cracks and the Brent–WTI spread
    for key, spec in CRACKS:
        try:
            latest[key] = crack_datapoint(key, values, fetched_at)
        except Exception as exc:  # noqa: BLE001
            reason = "%s: %s" % (type(exc).__name__, exc)
            if any(sid not in values for sid in spec["inputs"]):
                reason = "input series missing (%s)" % ", ".join(sid for sid in spec["inputs"] if sid not in values)
            errors[key] = reason[:300]
            latest[key] = _carry_forward(old, key, "USD/bbl", now, reason)
            partial.append(key)
    try:
        latest[SPREAD_KEY] = spread_datapoint(values, fetched_at)
    except Exception as exc:  # noqa: BLE001
        reason = "%s: %s" % (type(exc).__name__, exc)
        errors[SPREAD_KEY] = reason[:300]
        latest[SPREAD_KEY] = _carry_forward(old, SPREAD_KEY, "USD/bbl", now, reason)
        partial.append(SPREAD_KEY)

    # history + stats from the full daily series; carried forward if a chart input is missing
    history_inputs_ok = all(sid in values for sid in HISTORY_INPUT_SERIES)
    old_history = old.get("history") if isinstance(old, dict) else None
    old_stats = old.get("stats") if isinstance(old, dict) else None
    if history_inputs_ok or not (isinstance(old_history, dict) and isinstance(old_history.get("dates"), list)):
        cracks = compute_all_cracks(values)
        columns: Dict[str, Values] = dict(cracks)
        columns["brent"] = {d: v for d, v in values.get("DCOILBRENTEU", {}).items() if d >= HISTORY_START}
        columns["wti"] = {d: v for d, v in values.get("DCOILWTICO", {}).items() if d >= HISTORY_START}
        history = build_history(columns, now)
        stats: Dict[str, Any] = {key: compute_stats(cracks.get(key, {}), now, cfg) for key, _ in CRACKS}
        spread_series = compute_crack(values, SPREAD_INPUTS, _spread_fn)
        spread_stats = compute_stats(spread_series, now, cfg, with_level=False)
        stats[SPREAD_KEY] = {"value": spread_stats["value"], "as_of": spread_stats["as_of"],
                             "mean_2015_2019": spread_stats["mean_2015_2019"],
                             "mean_prev_year": spread_stats["mean_prev_year"], "prev_year": spread_stats["prev_year"],
                             "n": spread_stats["n"]}
        if not history_inputs_ok:
            common.log("prices: history built without %s (no previous history to carry forward)"
                       % ", ".join(sid for sid in HISTORY_INPUT_SERIES if sid not in values))
    else:
        history = copy.deepcopy(old_history)
        history["stale"] = True
        stats = copy.deepcopy(old_stats) if isinstance(old_stats, dict) else {}
        stats["stale"] = True
        for block in stats.values():
            if isinstance(block, dict):
                block["stale"] = True
        partial.extend(["history", "stats"])
        common.log("prices: history/stats carried forward (missing %s)"
                   % ", ".join(sid for sid in HISTORY_INPUT_SERIES if sid not in values))
    if "diesel_crack" not in stats:
        stats["diesel_crack"] = compute_stats({}, now, cfg)

    fresh_as_of = [dp["as_of"] for key, dp in latest.items() if key not in partial and dp.get("as_of")]
    doc = {
        "schema_version": 1,
        "generated_at": fetched_at,
        "as_of": max(fresh_as_of),
        "fetched_at": fetched_at,
        "stale": False,
        "source": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "latest": latest,
        "history": history,
        "stats": stats,
    }
    info: Dict[str, Any] = {"via": "fred", "partial": partial}
    if errors:
        info["errors"] = errors
    common.log("prices: %d/%d series fetched, as_of=%s%s"
               % (len(observations), len(SERIES), doc["as_of"], (", partial=%s" % partial) if partial else ""))
    return doc, info
