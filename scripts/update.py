#!/usr/bin/env python3
"""Crackspread orchestrator (ARCHITECTURE §4).

CLI::

    python scripts/update.py                   # fetch everything, write site/data/*.json + meta.json
    python scripts/update.py --dry-run         # fetch + validate + print a diff summary, write nothing
    python scripts/update.py --only prices,news
    python scripts/update.py --fixtures        # offline from tests/fixtures/, CRACKSPREAD_NOW defaults to 2026-10-08T10:00:00Z
    python scripts/update.py --no-ai           # AI provider "none" for this run (rule-based summary)
    python scripts/update.py --data-dir /tmp/d # write somewhere else (tests)

Per source, in order prices → balance → countries → news → summary → proposals:

* the fetcher module is loaded **by name** with :mod:`importlib` (table :data:`SOURCES`), so tests can
  inject fakes through ``sys.modules`` and a missing or broken module counts as *that source
  failing*, never as a crash of the run;
* a success is time-stale-checked, schema-validated and written only if its content changed;
* a failure keeps the old file, marks it stale (``fetched_at`` untouched) and records the error in
  ``meta.json`` together with the ``last_success`` carried over from the previous ``meta.json``.

``meta.json`` is always written (except with ``--dry-run``). Exit code 0 unless the config or the
schemas cannot be loaded or an exception escapes the orchestrator itself (exit 2).

Runs on Python 3.9 and 3.12. Library code never prints to stdout (``common.log`` → stderr); the
``--dry-run`` report is CLI output and goes to stdout on purpose.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import common
import build_meta
from common import log

__all__ = [
    "FIXTURE_NOW", "SOURCES", "SOURCE_NAMES", "SourceSpec", "RunOptions",
    "run_update", "main", "build_parser", "parse_only",
]

#: ``CRACKSPREAD_NOW`` used by ``--fixtures`` when the variable is not already set (ARCHITECTURE §0).
FIXTURE_NOW = "2026-10-08T10:00:00Z"

#: Environment variable that holds the API key per AI provider (brief §3.1).
AI_KEY_VARS = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

#: Keys ignored when deciding whether a file changed (mirrors ``common.DEFAULT_IGNORE``).
_IGNORE = tuple(common.DEFAULT_IGNORE)


@dataclass(frozen=True)
class SourceSpec:
    """One row of the source table: how to load the fetcher and the contract defaults used when
    the module cannot be imported (the meta key must exist even then)."""

    name: str          # CLI name, context key and file stem (prices, balance, ...)
    module: str        # importable module name (flat, scripts/ on sys.path)
    func: str          # callable in the module: run | run_proposals
    prefix: str        # "" or "PROPOSALS_" for the module constants (ARCHITECTURE §2)
    source_key: str    # default meta.json["sources"] key
    output_file: str   # default file in the data dir
    schema: str        # default schema name
    stale_kind: str    # default key in cfg["stale_after_hours"]


SOURCES: Tuple[SourceSpec, ...] = (
    SourceSpec("prices", "fetch_prices", "run", "", "fred", "prices.json", "prices", "prices"),
    SourceSpec("balance", "fetch_balance", "run", "", "eia_steo", "balance.json", "balance", "steo"),
    SourceSpec("countries", "fetch_country", "run", "", "countries", "countries.json", "countries", "country"),
    SourceSpec("news", "fetch_news", "run", "", "news", "news.json", "news", "news"),
    SourceSpec("summary", "summarize", "run", "", "summary", "summary.json", "summary", "summary"),
    SourceSpec("proposals", "summarize", "run_proposals", "PROPOSALS_", "proposals", "proposals.json", "proposals", "summary"),
)
SOURCE_NAMES: Tuple[str, ...] = tuple(s.name for s in SOURCES)
_SPEC_BY_NAME: Dict[str, SourceSpec] = {s.name: s for s in SOURCES}


@dataclass
class RunOptions:
    """Resolved CLI options (also the programmatic entry point for tests)."""

    dry_run: bool = False
    only: Optional[Sequence[str]] = None
    fixtures: bool = False
    no_ai: bool = False
    data_dir: Optional[str] = None
    env: Optional[Mapping[str, str]] = None   # API keys; defaults to os.environ


# ------------------------------------------------------------------------------------ helpers

def parse_only(values: Optional[Iterable[str]]) -> Optional[List[str]]:
    """Normalise ``--only`` (repeatable, comma-separated) into an ordered list of unique source
    names in pipeline order. ``None``/empty → ``None`` (= all sources). Raises ``ValueError`` for
    unknown names."""
    if not values:
        return None
    wanted: List[str] = []
    for chunk in values:
        for token in str(chunk).split(","):
            token = token.strip().lower()
            if not token:
                continue
            if token not in _SPEC_BY_NAME:
                raise ValueError("unknown source %r (expected one of: %s)" % (token, ", ".join(SOURCE_NAMES)))
            if token not in wanted:
                wanted.append(token)
    if not wanted:
        return None
    return [name for name in SOURCE_NAMES if name in wanted]


def _module_const(mod: Any, spec: SourceSpec, const: str, default: str) -> str:
    """``mod.<PREFIX><const>`` if it is a non-empty string, else ``default``."""
    value = getattr(mod, spec.prefix + const, None)
    return value if isinstance(value, str) and value else default


def _ai_enabled(cfg: dict, env: Mapping[str, str], fixtures: bool) -> bool:
    """Whether ``summarize.run_proposals`` should be called (ARCHITECTURE §2): provider is not
    ``none`` and a key is present. In fixtures mode "a key" means ``CRACKSPREAD_LLM_FIXTURE`` (no
    network is allowed there, a real key would be pointless)."""
    provider = str(cfg.get("ai_provider") or "none").lower()
    if provider == "none":
        return False
    if fixtures:
        return bool(env.get("CRACKSPREAD_LLM_FIXTURE"))
    key_var = AI_KEY_VARS.get(provider)
    return bool(key_var and env.get(key_var))


def _empty_proposals(cfg: dict, now: datetime) -> dict:
    """The document written when the AI is disabled: no proposals, full envelope (§3.6)."""
    stamp = common.iso_utc(now)
    return {
        "schema_version": 1,
        "generated_at": stamp,
        "as_of": stamp,
        "fetched_at": stamp,
        "stale": False,
        "source": "Crackspread (AI disabled: provider none or no API key)",
        "source_url": cfg.get("repo_url") or cfg.get("site_url") or "https://github.com/fixoa/crackspread",
        "provider": "none",
        "model": "",
        "proposals": [],
    }


def _unpack(result: Any, spec: SourceSpec) -> Tuple[dict, dict]:
    """Check the ``(doc, info)`` shape returned by a fetcher."""
    if not isinstance(result, tuple) or len(result) != 2:
        raise TypeError("%s.%s must return (doc, info), got %s" % (spec.module, spec.func, type(result).__name__))
    doc, info = result
    if not isinstance(doc, dict):
        raise TypeError("%s.%s returned a %s instead of a document dict" % (spec.module, spec.func, type(doc).__name__))
    if info is None:
        info = {}
    if not isinstance(info, dict):
        raise TypeError("%s.%s returned a %s instead of an info dict" % (spec.module, spec.func, type(info).__name__))
    return doc, dict(info)


def _augment_info(name: str, doc: dict, info: dict) -> None:
    """Fill the counters meta.json shows (§3.8) when the fetcher did not."""
    if name == "proposals":
        info.setdefault("count", len(doc.get("proposals") or []))
    elif name == "news":
        info.setdefault("items", len(doc.get("items") or []))


def _normalise(doc: Any) -> Any:
    """What the document looks like after a JSON round trip (tuples → lists, keys → str)."""
    return json.loads(json.dumps(doc, sort_keys=True, ensure_ascii=False))


def _diff(old: Any, doc: dict, ignore: Iterable[str]) -> Tuple[bool, List[str]]:
    """``(changed, top-level keys that differ)`` with the ignored keys stripped recursively, the
    same comparison :func:`common.write_json_if_changed` makes."""
    new = common.strip_keys(_normalise(doc), ignore)
    if not isinstance(old, dict):
        return True, ["<new file>"]
    prev = common.strip_keys(old, ignore)
    keys = sorted(set(prev) | set(new))
    differing = [k for k in keys if prev.get(k) != new.get(k)]
    return bool(differing), differing


def _flags(opts: RunOptions) -> str:
    parts = []
    if opts.fixtures:
        parts.append("fixtures")
    if opts.dry_run:
        parts.append("dry-run")
    if opts.no_ai:
        parts.append("no-ai")
    if opts.only:
        parts.append("only=%s" % ",".join(opts.only))
    return ", ".join(parts) if parts else "live"


# ------------------------------------------------------------------------------------ core

def _process_source(
    spec: SourceSpec,
    cfg: dict,
    now: datetime,
    env: Mapping[str, str],
    context: dict,
    data_dir: Path,
    prev_sources: Mapping[str, Any],
    opts: RunOptions,
) -> Tuple[str, dict]:
    """Run one source per the §4 pseudo-code. Returns ``(meta key, meta entry)``. Never raises."""
    key = spec.source_key
    path = data_dir / spec.output_file
    schema = spec.schema
    stale_kind = spec.stale_kind
    old = common.read_json(path)
    try:
        # ARCHITECTURE §2: without an AI provider/key the proposals document is written by
        # update.py itself, so the summarizer module is not even needed for it.
        ai_off = spec.name == "proposals" and not _ai_enabled(cfg, env, opts.fixtures)
        mod = None if ai_off else importlib.import_module(spec.module)   # ImportError → this source fails, not the run
        if mod is not None:
            key = _module_const(mod, spec, "SOURCE_KEY", key)
            output_file = _module_const(mod, spec, "OUTPUT_FILE", spec.output_file)
            schema = _module_const(mod, spec, "SCHEMA", schema)
            stale_kind = _module_const(mod, spec, "STALE_KIND", stale_kind)
            if output_file != spec.output_file:
                path = data_dir / output_file
                old = common.read_json(path)

        ignore: Tuple[str, ...] = _IGNORE
        if ai_off:
            doc = _empty_proposals(cfg, now)
            info: dict = {"provider": "none", "model": "", "count": 0,
                          "reason": "AI disabled (provider none or no API key)"}
            ignore = _IGNORE + ("as_of",)   # the empty document is byte-stable between runs
        else:
            fn = getattr(mod, spec.func, None)
            if not callable(fn):
                raise AttributeError("module %r has no callable %r" % (spec.module, spec.func))
            result = fn(cfg, old, fixtures=opts.fixtures, now=now, env=env, context=context)
            doc, info = _unpack(result, spec)

        common.apply_time_stale(doc, stale_kind, cfg, now)
        common.validate(doc, schema)
        _augment_info(spec.name, doc, info)

        if opts.dry_run:
            changed, differing = _diff(old, doc, ignore)
            detail = (": " + ", ".join(differing)) if changed else ""
            print("would write %s (%s)%s" % (path.name, "changed" if changed else "unchanged", detail))
            context[spec.name] = doc
        else:
            changed = common.write_json_if_changed(path, doc, ignore=ignore)
            context[spec.name] = common.read_json(path) or doc

        entry = {
            "ok": True,
            "last_success": common.iso_utc(now),
            "error": None,
            "stale": bool(doc.get("stale", False)),
            "changed": bool(changed),
        }
        entry.update(info)
        log("%s: ok via %s (%s) as_of=%s%s" % (
            spec.name, info.get("via") or key, "changed" if changed else "unchanged",
            doc.get("as_of"), " STALE" if entry["stale"] else ""))
        return key, entry

    except Exception as exc:  # noqa: BLE001  — a broken source never aborts the run
        error = ("%s: %s" % (type(exc).__name__, exc))[:500]
        kept = "kept old (stale)" if old is not None else "no old file"
        log("%s: FAILED %s → %s" % (spec.name, error, kept))
        log(traceback.format_exc().rstrip())
        if old is not None:
            common.mark_all_stale(old)                     # fetched_at untouched
            if opts.dry_run:
                print("would keep %s and mark it stale" % path.name)
            else:
                common.write_json_atomic(path, old)
        context[spec.name] = old
        prev = prev_sources.get(key) if isinstance(prev_sources, Mapping) else None
        last_success = prev.get("last_success") if isinstance(prev, Mapping) else None
        entry = {
            "ok": False,
            "error": error,
            "last_success": last_success if isinstance(last_success, str) else None,
            "stale": True,
            "changed": False,
        }
        return key, entry


def _run(opts: RunOptions, env: Mapping[str, str], started: float) -> int:
    # 1. Config, schemas, clock. Any problem here is fatal (exit 2): nothing can be deployed sanely.
    try:
        cfg = common.load_config()
        common.validate(cfg, "config")
        if opts.no_ai:
            cfg["ai_provider"] = "none"
        selected = [_SPEC_BY_NAME[n] for n in (opts.only or SOURCE_NAMES)]
        for spec in selected:
            common.load_schema(spec.schema)
        common.load_schema("meta")
        now = common.now_utc()
    except Exception as exc:  # noqa: BLE001
        log("fatal: cannot start: %s: %s" % (type(exc).__name__, exc))
        log(traceback.format_exc().rstrip())
        return 2

    data_dir = Path(opts.data_dir).expanduser() if opts.data_dir else common.DATA
    meta_path = data_dir / build_meta.OUTPUT_FILE
    prev_meta = common.read_json(meta_path)
    if not isinstance(prev_meta, dict):
        prev_meta = None
    prev_sources = prev_meta.get("sources") if prev_meta and isinstance(prev_meta.get("sources"), dict) else {}
    run_id = "%s-%s" % (now.strftime("%Y%m%d-%H%M%S"), os.urandom(3).hex())
    log("run %s start [%s] data_dir=%s now=%s" % (run_id, _flags(opts), data_dir, common.iso_utc(now)))

    # 2. Context: what is on disk now (so --only summary still sees prices/balance/news) plus the
    #    hand-maintained manual.json; each successful source replaces its own entry.
    context: Dict[str, Any] = {}
    for spec in SOURCES:
        context[spec.name] = common.read_json(data_dir / spec.output_file)
    context["manual"] = common.read_json(data_dir / "manual.json")

    # 3. Sources, in pipeline order.
    sources_meta: Dict[str, dict] = {}
    for spec in selected:
        key, entry = _process_source(spec, cfg, now, env, context, data_dir, prev_sources, opts)
        sources_meta[key] = entry

    # 4. meta.json: always written, except in --dry-run.
    run_info = {
        "last_run": common.iso_utc(now),
        "run_id": run_id,
        "duration_s": round(time.monotonic() - started, 3),
        "fixtures": bool(opts.fixtures),
        "dry_run": bool(opts.dry_run),
        "python": platform.python_version(),
        "only": list(opts.only) if opts.only else None,
        "prev_meta": prev_meta,
    }
    meta = build_meta.build(cfg, run_info, sources_meta)
    common.validate(meta, build_meta.SCHEMA)
    if opts.dry_run:
        print("would write %s (not written in --dry-run)" % meta_path.name)
    else:
        common.write_json_atomic(meta_path, meta)

    failed = [k for k, v in sources_meta.items() if not v.get("ok")]
    log("run %s done in %.1fs: %d ok, %d failed%s" % (
        run_id, run_info["duration_s"], len(sources_meta) - len(failed), len(failed),
        (" (%s)" % ", ".join(failed)) if failed else ""))
    return 0


def run_update(opts: RunOptions) -> int:
    """Run the pipeline with resolved options; returns the process exit code (0 or 2)."""
    started = time.monotonic()
    env: Mapping[str, str] = opts.env if opts.env is not None else os.environ
    set_now = False
    if opts.fixtures and not os.environ.get("CRACKSPREAD_NOW", "").strip():
        os.environ["CRACKSPREAD_NOW"] = FIXTURE_NOW   # only for the duration of this run
        set_now = True
    try:
        return _run(opts, env, started)
    except Exception as exc:  # noqa: BLE001  — nothing may escape the orchestrator
        log("fatal: orchestrator crashed: %s: %s" % (type(exc).__name__, exc))
        log(traceback.format_exc().rstrip())
        return 2
    finally:
        if set_now:
            os.environ.pop("CRACKSPREAD_NOW", None)


# ------------------------------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="update.py",
        description="Crackspread data pipeline: fetch sources, write site/data/*.json and meta.json.",
    )
    p.add_argument("--dry-run", action="store_true",
                   help="fetch + validate + print a diff summary; write nothing (not even meta.json)")
    p.add_argument("--only", action="append", metavar="NAME[,NAME...]",
                   help="restrict to these sources: %s (repeatable)" % "|".join(SOURCE_NAMES))
    p.add_argument("--fixtures", action="store_true",
                   help="offline: read tests/fixtures/ instead of the network; CRACKSPREAD_NOW defaults to %s" % FIXTURE_NOW)
    p.add_argument("--no-ai", dest="no_ai", action="store_true",
                   help='use AI provider "none" for this run (rule-based summary, no proposals)')
    p.add_argument("--data-dir", metavar="PATH", help="data directory (default: site/data)")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        only = parse_only(args.only)
    except ValueError as exc:
        log("error: %s" % exc)
        return 2
    opts = RunOptions(dry_run=args.dry_run, only=only, fixtures=args.fixtures,
                      no_ai=args.no_ai, data_dir=args.data_dir)
    return run_update(opts)


if __name__ == "__main__":
    import sys

    sys.exit(main())
