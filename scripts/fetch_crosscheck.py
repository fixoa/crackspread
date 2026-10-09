"""Cross-check the whiteboard claims (manual.json) against live data and headlines → crosscheck.json.

For every claim in cfg["crosscheck"] we collect, deterministically and without any model:
* the whiteboard value (resolved from manual.json by its target path),
* the matching PortWatch chokepoint figures (shipping.json), when the claim maps to one,
* up to ``max_mentions`` recent headlines whose title/snippet contains one of the claim's keywords,
  with the numeric phrases found verbatim in that text (e.g. "$1.2 million", "20 million barrels").
Nothing is estimated or merged: the page shows the three side by side with their sources.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import common
from common import log

SOURCE_KEY = "crosscheck"
OUTPUT_FILE = "crosscheck.json"
SCHEMA = "crosscheck"
STALE_KIND = "news"

MAX_MENTIONS = 3
_NUM = re.compile(
    r"(?<![\w.])(?:[$€£]\s?)?\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?(?!\d)\s?"
    r"(?:(?:million|billion|bn|m\b|k\b)(?:\s(?:barrels(?: per day| a day)?|b/d|bpd|tankers?|ships?|vessels?))?"
    r"|%|mb/d|b/d|bpd|barrels(?: per day| a day)?|per day|/day|days?|tankers?|ships?|vessels?)?",
    re.IGNORECASE,
)


def _resolve(manual: dict, target: str):
    """'hormuz_ledger[iran_blockade].delta' / 'shipping.vlcc_day_rate_usd.now' / 'refinery_shock[0].diesel_delta_mbd' → value."""
    cur = manual
    for part in re.findall(r"[^.\[\]]+|\[[^\]]+\]", target):
        if part.startswith("["):
            key = part[1:-1]
            if isinstance(cur, list):
                if key.isdigit():
                    cur = cur[int(key)] if int(key) < len(cur) else None
                else:
                    cur = next((e for e in cur if isinstance(e, dict) and e.get("id") == key), None)
            else:
                cur = None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = None
        if cur is None:
            return None
    return cur


def _numbers(text: str) -> List[str]:
    out = []
    for m in _NUM.finditer(text or ""):
        s = m.group(0).strip()
        if re.search(r"\d", s) and not re.fullmatch(r"\d{4}", s) and s not in out:   # skip bare years
            out.append(s)
    return out[:6]


def _mentions(items: List[dict], keywords: List[str]) -> List[dict]:
    kws = [k.lower() for k in keywords]
    found = []
    for it in items:
        text = "%s %s" % (it.get("title", ""), it.get("snippet", ""))
        low = text.lower()
        if not any(k in low for k in kws):
            continue
        found.append({"title": it.get("title", ""), "source": it.get("source", ""), "link": it.get("link", ""),
                      "published": it.get("published", ""), "numbers": _numbers(text)})
        if len(found) >= MAX_MENTIONS:
            break
    return found


def build(cfg: dict, manual: Optional[dict], news: Optional[dict], shipping: Optional[dict], now: datetime) -> Tuple[dict, dict]:
    claims = cfg.get("crosscheck") or []
    if not claims:
        raise common.FetchError("config has no crosscheck claims")
    items = (news or {}).get("items") or []
    cps = {c.get("id"): c for c in (shipping or {}).get("chokepoints") or []}
    msrc = (manual or {}).get("default_source") or {}
    out = []
    for c in claims:
        wb = _resolve(manual or {}, c.get("target", "")) if manual else None
        pw = cps.get(c.get("portwatch")) if c.get("portwatch") else None
        entry = {
            "id": c["id"], "label": c.get("label", c["id"]), "target": c.get("target", ""), "keywords": list(c.get("keywords", [])),
            "whiteboard": {"value": wb if isinstance(wb, (int, float, str)) else None, "unit": c.get("unit", ""),
                           "source": msrc.get("name", ""), "source_url": msrc.get("url", ""), "as_of": (manual or {}).get("updated_at")},
            "portwatch": ({"chokepoint": pw.get("name"), "tankers_7d": pw.get("tankers_7d"), "tankers_baseline": pw.get("tankers_baseline"),
                           "tankers_change_pct": pw.get("tankers_change_pct"), "latest_date": pw.get("latest_date"), "baseline_year": (shipping or {}).get("baseline_year"),
                           "source": (shipping or {}).get("source"), "source_url": (shipping or {}).get("source_url")} if pw else None),
            "mentions": _mentions(items, c.get("keywords", [])),
        }
        out.append(entry)
    doc = {
        "schema_version": 1, "generated_at": common.iso_utc(now), "as_of": (news or {}).get("as_of") or common.iso_utc(now),
        "fetched_at": common.iso_utc(now), "stale": False,
        "source": "Crackspread cross-check of manual.json against news.json and shipping.json", "source_url": cfg.get("repo_url", "https://github.com/fixoa/crackspread"),
        "news_items_scanned": len(items), "claims": out,
    }
    n = sum(len(e["mentions"]) for e in out)
    return doc, {"claims": len(out), "mentions": n, "with_portwatch": sum(1 for e in out if e["portwatch"])}


def run(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None, session=None, env=None, context=None) -> Tuple[dict, dict]:
    now = now or common.now_utc()
    ctx = context or {}
    manual = ctx.get("manual") or common.read_json(common.DATA / "manual.json")
    doc, info = build(cfg, manual, ctx.get("news"), ctx.get("shipping"), now)
    log("crosscheck: %d claims, %d headline mentions" % (info["claims"], info["mentions"]))
    return doc, info
