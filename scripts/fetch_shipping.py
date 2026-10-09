"""IMF PortWatch → shipping.json: daily tanker transits through the chokepoints that matter for Gulf oil.

PortWatch (https://portwatch.imf.org/, CC BY 4.0) publishes AIS-derived daily vessel counts per
chokepoint through an ArcGIS feature service. We take five chokepoints, average the last
``window_days`` days and compare with the previous calendar year's daily average. Nothing is
estimated: a day without data is simply absent from the average.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import common
from common import FIXTURES, PlausibilityError, log

SOURCE_KEY = "shipping"
OUTPUT_FILE = "shipping.json"
SCHEMA = "shipping"
STALE_KIND = "shipping"

SOURCE = "IMF PortWatch, daily chokepoint transits (AIS)"
SOURCE_URL = "https://portwatch.imf.org/"
API = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
FIELDS = "date,portid,portname,n_tanker,n_total,capacity_tanker,capacity"
PAGE = 1000
WINDOW_DAYS = 7

#: id → (PortWatch portid, display name)
CHOKEPOINTS = (
    ("hormuz", "chokepoint6", "Strait of Hormuz"),
    ("bab_el_mandeb", "chokepoint4", "Bab el-Mandeb"),
    ("suez", "chokepoint1", "Suez Canal"),
    ("cape", "chokepoint7", "Cape of Good Hope"),
    ("malacca", "chokepoint5", "Malacca Strait"),
)
_BY_PORTID = {p: (i, n) for i, p, n in CHOKEPOINTS}


def _fetch_rows(start: str, session=None) -> List[dict]:
    """All rows for the five chokepoints since ``start`` (YYYY-MM-DD), following the server's paging."""
    ids = ", ".join("'%s'" % p for _, p, _ in CHOKEPOINTS)
    where = "portid IN (%s) AND date >= DATE '%s'" % (ids, start)
    rows: List[dict] = []
    offset = 0
    while True:
        params = {"where": where, "outFields": FIELDS, "orderByFields": "date ASC", "resultOffset": offset,
                  "resultRecordCount": PAGE, "returnGeometry": "false", "f": "json"}
        r = common.http_get(API, params=params, session=session)
        doc = r.json()
        if "error" in doc:
            raise common.FetchError("PortWatch: %s" % json.dumps(doc["error"])[:200])
        feats = doc.get("features") or []
        rows.extend(f.get("attributes", {}) for f in feats)
        if not doc.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
        if offset > 50000:
            raise common.FetchError("PortWatch: paging did not terminate")
    return rows


def _num(v, limit: float = 5000.0) -> Optional[float]:
    """Vessel counts (≤ ``limit`` per day) or, with a large limit, capacities in dwt. Negative → implausible."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f < 0 or f > limit:
        raise PlausibilityError("PortWatch value out of range: %r" % v)
    return f


def _mean(vals: List[float]) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(statistics.mean(vals), 1) if vals else None


def _week_key(d: str) -> str:
    y, w, _ = datetime.strptime(d, "%Y-%m-%d").isocalendar()
    return "%04d-W%02d" % (y, w)


def build(rows: List[dict], now: datetime, fetched_at: str, baseline_year: int) -> Tuple[dict, dict]:
    per: Dict[str, List[dict]] = {}
    for r in rows:
        pid = r.get("portid")
        if pid not in _BY_PORTID or not isinstance(r.get("date"), str):
            continue
        per.setdefault(pid, []).append(r)
    if not per:
        raise common.FetchError("PortWatch returned no rows for the chokepoints")
    latest_all = max(r["date"] for rs in per.values() for r in rs)
    cps = []
    for cid, pid, name in CHOKEPOINTS:
        rs = sorted(per.get(pid, []), key=lambda x: x["date"])
        if not rs:
            cps.append({"id": cid, "portid": pid, "name": name, "latest_date": None, "tankers_7d": None, "tankers_baseline": None,
                        "tankers_change_pct": None, "total_7d": None, "total_baseline": None, "capacity_tanker_7d": None, "series": {"weeks": [], "tankers": []}})
            continue
        latest = rs[-1]["date"]
        cutoff = (datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS - 1)).strftime("%Y-%m-%d")
        win = [r for r in rs if r["date"] >= cutoff]
        base = [r for r in rs if r["date"].startswith(str(baseline_year))]
        t7 = _mean([_num(r.get("n_tanker")) for r in win])
        tb = _mean([_num(r.get("n_tanker")) for r in base])
        change = round((t7 - tb) / tb * 100, 0) if (t7 is not None and tb) else None
        weeks: Dict[str, List[float]] = {}
        for r in rs:
            v = _num(r.get("n_tanker"))
            if v is not None:
                weeks.setdefault(_week_key(r["date"]), []).append(v)
        wk = sorted(weeks)
        cps.append({
            "id": cid, "portid": pid, "name": name, "latest_date": latest, "window_days": WINDOW_DAYS,
            "tankers_7d": t7, "tankers_baseline": tb, "tankers_change_pct": change,
            "total_7d": _mean([_num(r.get("n_total")) for r in win]), "total_baseline": _mean([_num(r.get("n_total")) for r in base]),
            "capacity_tanker_7d": _mean([_num(r.get("capacity_tanker"), 1e10) for r in win]),
            "series": {"weeks": wk, "tankers": [round(statistics.mean(weeks[w]), 1) for w in wk]},
        })
    doc = {
        "schema_version": 1, "generated_at": common.iso_utc(now), "as_of": latest_all, "fetched_at": fetched_at, "stale": False,
        "source": SOURCE, "source_url": SOURCE_URL, "licence": "CC BY 4.0", "unit": "vessels per day",
        "window_days": WINDOW_DAYS, "baseline_year": baseline_year, "chokepoints": cps,
    }
    return doc, {"via": "portwatch", "rows": len(rows), "latest": latest_all}


def run(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None, session=None, env=None, context=None) -> Tuple[dict, dict]:
    now = now or common.now_utc()
    baseline_year = now.year - 1
    start = "%d-01-01" % baseline_year
    if fixtures:
        with open(FIXTURES / "portwatch" / "chokepoints.json", "r", encoding="utf-8") as fh:
            rows = json.load(fh)
    else:
        rows = _fetch_rows(start, session=session)
    log("shipping: %d PortWatch rows since %s" % (len(rows), start))
    return build(rows, now, common.iso_utc(now), baseline_year)
