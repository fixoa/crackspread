"""meta.json builder (ARCHITECTURE §3.8).

``build(cfg, run_info, sources)`` turns the per-source status dicts collected by ``update.py``
into the ``meta.json`` document. It carries ``last_success`` over from the previous ``meta.json``
(``run_info["prev_meta"]``) for sources that failed this time, and, for a restricted run
(``--only``), keeps the previous status of the sources that were not run (marked ``skipped``).
Validation and writing are done by ``update.py``.

Runs on Python 3.9 and 3.12. No stdout, no ``sys.exit``.
"""
from __future__ import annotations

import platform
from typing import Any, Dict, Mapping, Optional

import common

__all__ = ["SCHEMA", "OUTPUT_FILE", "build"]

SCHEMA = "meta"
OUTPUT_FILE = "meta.json"


def _prev_sources(prev_meta: Any) -> Dict[str, Any]:
    if isinstance(prev_meta, Mapping):
        sources = prev_meta.get("sources")
        if isinstance(sources, Mapping):
            return dict(sources)
    return {}


def _prev_last_success(prev_sources: Mapping[str, Any], key: str) -> Optional[str]:
    prev = prev_sources.get(key)
    if isinstance(prev, Mapping):
        value = prev.get("last_success")
        if isinstance(value, str) and value:
            return value
    return None


def _normalise_entry(key: str, entry: Any, prev_sources: Mapping[str, Any]) -> dict:
    """A complete source status: ``ok``, ``last_success``, ``error``, ``stale``, ``changed`` plus
    whatever the fetcher reported."""
    if isinstance(entry, Mapping):
        out: dict = dict(entry)
    else:
        out = {"ok": False, "error": "invalid status entry: %r" % (entry,)}
    out["ok"] = bool(out.get("ok", False))
    out.setdefault("error", None)
    out.setdefault("stale", not out["ok"])
    out.setdefault("changed", False)
    if not isinstance(out.get("last_success"), str) or not out.get("last_success"):
        out["last_success"] = _prev_last_success(prev_sources, key)
    return out


def build(cfg: dict, run_info: Mapping[str, Any], sources: Mapping[str, Any]) -> dict:
    """Build the meta.json document.

    ``run_info`` keys: ``last_run`` (ISO UTC; default now), ``run_id``, ``duration_s``,
    ``fixtures``, ``dry_run``, ``python`` (default: this interpreter), ``only`` (list or None) and
    ``prev_meta`` (the previous meta.json document or None).
    """
    prev_sources = _prev_sources(run_info.get("prev_meta"))
    last_run = run_info.get("last_run") or common.iso_utc()
    run_id = run_info.get("run_id") or "%s-000000" % common.parse_iso(last_run).strftime("%Y%m%d-%H%M%S")

    merged: Dict[str, dict] = {}
    for key, entry in sources.items():
        merged[str(key)] = _normalise_entry(str(key), entry, prev_sources)

    only = run_info.get("only")
    if only:
        # Restricted run: the sources that were not touched keep their previous status.
        for key, prev in prev_sources.items():
            if key not in merged and isinstance(prev, Mapping):
                carried = dict(prev)
                carried["ok"] = bool(carried.get("ok", False))
                carried["changed"] = False
                carried["skipped"] = True
                merged[key] = carried

    try:
        duration = round(max(0.0, float(run_info.get("duration_s") or 0.0)), 3)
    except (TypeError, ValueError):
        duration = 0.0

    schedule = cfg.get("update_schedule_utc") if isinstance(cfg, Mapping) else None
    meta: dict = {
        "schema_version": 1,
        "generated_at": last_run,
        "last_run": last_run,
        "run_id": run_id,
        "duration_s": duration,
        "fixtures": bool(run_info.get("fixtures", False)),
        "dry_run": bool(run_info.get("dry_run", False)),
        "python": str(run_info.get("python") or platform.python_version()),
        "schedule": dict(schedule) if isinstance(schedule, Mapping) else {},
        "sources": merged,
    }
    if only:
        meta["only"] = [str(n) for n in only]
    return meta
