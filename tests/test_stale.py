"""Stale handling in scripts/update.py and build_meta.py (brief §7, ARCHITECTURE §4):

* a fetcher that raises → the old file is kept byte-identical except for its stale flags,
  ``fetched_at`` unchanged, the error and the carried ``last_success`` land in meta.json, exit 0;
* time-based stale: a fetcher returning an old ``as_of`` → the written document is stale;
* ``build_meta.build`` carries ``last_success`` over from the previous meta.json.

Uses the fake fetcher modules from ``test_update.py``; the real fetchers are never imported.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import build_meta
import common
import update
from test_update import (  # noqa: F401  (fixture `fakes` is re-exported for pytest)
    FIXTURE_NOW, FakeFetchers, fakes, files_in, prices_doc, read, run_cli,
)

PREV_RUN = "2026-10-07T17:17:30Z"


def _prev_meta(cfg: dict, **overrides: dict) -> dict:
    """A previous meta.json as the last run would have written it."""
    sources = {
        "fred": {"ok": True, "last_success": PREV_RUN, "error": None, "stale": False, "changed": True, "via": "fred"},
        "eia_steo": {"ok": True, "last_success": "2026-10-06T10:17:30Z", "error": None, "stale": False,
                     "changed": False, "via": "api"},
        "news": {"ok": False, "last_success": "2026-10-07T10:17:30Z", "error": "FetchError: HTTP 503", "stale": True,
                 "changed": False},
    }
    for key, entry in overrides.items():
        sources[key] = entry
    return build_meta.build(cfg, {"last_run": PREV_RUN, "run_id": "20261007-171730-0ddba1", "duration_s": 9.5,
                                  "fixtures": False, "dry_run": False, "python": "3.12.1", "prev_meta": None},
                            sources)


def _lines_that_differ(a: bytes, b: bytes) -> list:
    """Symmetric difference of the two files' lines (order-insensitive, as both are sorted JSON)."""
    la, lb = a.decode("utf-8").splitlines(), b.decode("utf-8").splitlines()
    return [l for l in la if l not in lb] + [l for l in lb if l not in la]


def test_fetcher_raises_keeps_old_file_marked_stale(fakes: FakeFetchers, tmp_data_dir: Path, cfg, monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    old = prices_doc(PREV_RUN)                       # written by yesterday evening's run
    common.write_json_atomic(tmp_data_dir / "prices.json", old)
    common.write_json_atomic(tmp_data_dir / "meta.json", _prev_meta(cfg))
    old_bytes = (tmp_data_dir / "prices.json").read_bytes()

    fakes.failures["prices"] = common.FetchError("HTTP 503 after 3 attempts for https://fred.stlouisfed.org/graph/fredgraph.csv")
    rc = run_cli(data_dir=tmp_data_dir)
    assert rc == 0, "a broken source never aborts the run"

    # the old document, byte-identical except for the stale flags
    kept = read(tmp_data_dir, "prices.json")
    assert kept == common.mark_all_stale(copy.deepcopy(old))
    common.validate(kept, "prices")
    changed_lines = _lines_that_differ(old_bytes, (tmp_data_dir / "prices.json").read_bytes())
    assert changed_lines and all('"stale"' in line for line in changed_lines), changed_lines
    assert kept["stale"] is True
    assert all(dp["stale"] is True for dp in kept["latest"].values())
    assert kept["fetched_at"] == PREV_RUN and kept["generated_at"] == PREV_RUN
    assert all(dp["fetched_at"] == PREV_RUN for dp in kept["latest"].values())
    assert kept["latest"]["brent"]["value"] == 125.44           # values untouched, never emptied

    # meta.json: error text, carried last_success, stale, not changed; the rest of the run is fine
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    fred = meta["sources"]["fred"]
    assert fred["ok"] is False
    assert fred["error"] == "FetchError: HTTP 503 after 3 attempts for https://fred.stlouisfed.org/graph/fredgraph.csv"
    assert fred["last_success"] == PREV_RUN
    assert fred["stale"] is True and fred["changed"] is False
    assert meta["last_run"] == FIXTURE_NOW and meta["run_id"] != "20261007-171730-0ddba1"
    for key in ("eia_steo", "countries", "news", "summary", "proposals"):
        assert meta["sources"][key]["ok"] is True, key
        assert meta["sources"][key]["last_success"] == FIXTURE_NOW
    # the news source failed last time but succeeded now → its last_success is this run
    # later sources received the stale old document as context
    ctx = fakes.calls_for("summary")[0]["context"]
    assert ctx["prices"]["stale"] is True and ctx["prices"]["latest"]["diesel_crack"]["value"] == 72.51


def test_fetcher_raises_without_old_file(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    fakes.failures["balance"] = common.FetchError("STEO unreachable")
    assert run_cli(data_dir=tmp_data_dir) == 0
    assert not (tmp_data_dir / "balance.json").exists(), "nothing to keep, nothing invented"
    entry = read(tmp_data_dir, "meta.json")["sources"]["eia_steo"]
    assert entry == {"ok": False, "error": "FetchError: STEO unreachable", "last_success": None,
                     "stale": True, "changed": False}
    assert fakes.calls_for("summary")[0]["context"]["balance"] is None


def test_failure_in_dry_run_touches_nothing(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch, capsys):
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    common.write_json_atomic(tmp_data_dir / "prices.json", prices_doc(PREV_RUN))
    before = (tmp_data_dir / "prices.json").read_bytes()
    fakes.failures["prices"] = common.PlausibilityError("brent: 999.0 outside plausible range 5..400")
    assert run_cli("--dry-run", data_dir=tmp_data_dir) == 0
    assert (tmp_data_dir / "prices.json").read_bytes() == before
    assert files_in(tmp_data_dir) == {"prices.json"}
    assert "would keep prices.json and mark it stale" in capsys.readouterr().out


def test_time_based_stale(fakes: FakeFetchers, tmp_data_dir: Path, cfg, monkeypatch):
    """prices: as_of 2026-09-20 is 18 days before now (threshold 96 h) → stale, but ok."""
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    assert common.stale_threshold_hours("prices", cfg) == 96
    fakes.docs["prices"] = lambda now_iso: prices_doc(now_iso, as_of="2026-09-20")
    assert run_cli(data_dir=tmp_data_dir) == 0

    prices = read(tmp_data_dir, "prices.json")
    common.validate(prices, "prices")
    assert prices["stale"] is True
    assert prices["latest"]["brent"]["stale"] is True and prices["latest"]["diesel_crack"]["stale"] is True
    # the weekly retail series (as_of 2026-10-05, own threshold "weekly" = 264 h) is still fresh
    assert prices["latest"]["retail_diesel_us"]["stale"] is False
    assert prices["fetched_at"] == FIXTURE_NOW                   # it was fetched fine, just old data

    meta = read(tmp_data_dir, "meta.json")
    assert meta["sources"]["fred"] == {"ok": True, "last_success": FIXTURE_NOW, "error": None, "stale": True,
                                       "changed": True, "via": "fred"}
    # a fresh as_of is not stale
    for key in ("eia_steo", "countries", "news", "summary"):
        assert meta["sources"][key]["stale"] is False, key


def test_time_based_stale_other_kinds(fakes: FakeFetchers, tmp_data_dir: Path, cfg, monkeypatch):
    """news: newest item older than 24 h → stale; balance: 2026-10-06 release within 45 days → fresh."""
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-09T10:00:00Z")   # 44 h after the newest news item
    assert run_cli(data_dir=tmp_data_dir) == 0
    assert read(tmp_data_dir, "news.json")["stale"] is True
    assert read(tmp_data_dir, "balance.json")["stale"] is False
    assert read(tmp_data_dir, "countries.json")["stale"] is False
    meta = read(tmp_data_dir, "meta.json")
    assert meta["sources"]["news"]["stale"] is True and meta["sources"]["news"]["ok"] is True
    assert meta["sources"]["eia_steo"]["stale"] is False


def test_stale_flag_from_fetcher_is_kept(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    """A fetcher may carry sub-items forward as stale (partial failure); update.py keeps that."""
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)

    def partial(now_iso: str) -> dict:
        doc = prices_doc(now_iso)
        doc["latest"]["retail_diesel_us"]["stale"] = True
        doc["latest"]["retail_diesel_us"]["fetched_at"] = PREV_RUN
        return doc
    fakes.docs["prices"] = partial
    fakes.infos["prices"] = {"via": "fred", "partial": ["retail_diesel_us"]}
    assert run_cli(data_dir=tmp_data_dir) == 0
    prices = read(tmp_data_dir, "prices.json")
    assert prices["stale"] is False                                 # the document itself is fresh
    assert prices["latest"]["retail_diesel_us"]["stale"] is True   # the carried item stays stale
    assert prices["latest"]["retail_diesel_us"]["fetched_at"] == PREV_RUN
    assert read(tmp_data_dir, "meta.json")["sources"]["fred"]["partial"] == ["retail_diesel_us"]


# ------------------------------------------------------------------------------ build_meta

def test_build_meta_carries_last_success(cfg):
    prev = _prev_meta(cfg)
    run_info = {"last_run": FIXTURE_NOW, "run_id": "20261008-100000-abcdef", "duration_s": 2.0,
                "fixtures": False, "dry_run": False, "python": "3.9.6", "only": None, "prev_meta": prev}
    sources = {
        "fred": {"ok": False, "error": "FetchError: boom", "last_success": None, "stale": True, "changed": False},
        "eia_steo": {"ok": True, "last_success": FIXTURE_NOW, "error": None, "stale": False, "changed": True,
                     "via": "xlsx"},
        "news": {"ok": False, "error": "FetchError: still down", "last_success": None, "stale": True,
                 "changed": False},
        "countries": {"ok": False, "error": "KeyError: 'EIA_API_KEY'", "stale": True, "changed": False},
    }
    meta = build_meta.build(cfg, run_info, sources)
    common.validate(meta, "meta")
    assert meta["sources"]["fred"]["last_success"] == PREV_RUN                 # carried over
    assert meta["sources"]["eia_steo"]["last_success"] == FIXTURE_NOW          # own success wins
    assert meta["sources"]["news"]["last_success"] == "2026-10-07T10:17:30Z"   # carried across two failures
    assert meta["sources"]["countries"]["last_success"] is None                # never succeeded, key missing
    assert meta["sources"]["countries"]["error"] == "KeyError: 'EIA_API_KEY'"
    assert meta["last_run"] == FIXTURE_NOW and meta["run_id"] == "20261008-100000-abcdef"
    assert meta["python"] == "3.9.6" and meta["duration_s"] == 2.0
    assert "only" not in meta and set(meta["sources"]) == set(sources)


def test_build_meta_without_previous_meta(cfg):
    for prev in (None, {}, {"sources": "garbage"}, {"sources": {"fred": "garbage"}}):
        meta = build_meta.build(cfg, {"last_run": FIXTURE_NOW, "run_id": "20261008-100000-000000",
                                      "prev_meta": prev},
                                {"fred": {"ok": False, "error": "x"}})
        common.validate(meta, "meta")
        assert meta["sources"]["fred"] == {"ok": False, "error": "x", "last_success": None, "stale": True,
                                           "changed": False}


def test_build_meta_only_run_keeps_untouched_sources(cfg):
    prev = _prev_meta(cfg)
    meta = build_meta.build(cfg, {"last_run": FIXTURE_NOW, "run_id": "20261008-100000-000000",
                                  "only": ["news"], "prev_meta": prev},
                            {"news": {"ok": True, "last_success": FIXTURE_NOW, "error": None, "stale": False,
                                      "changed": True, "items": 12}})
    common.validate(meta, "meta")
    assert meta["only"] == ["news"]
    assert meta["sources"]["news"]["items"] == 12 and "skipped" not in meta["sources"]["news"]
    assert meta["sources"]["fred"]["skipped"] is True and meta["sources"]["fred"]["changed"] is False
    assert meta["sources"]["fred"]["last_success"] == PREV_RUN
    assert meta["sources"]["eia_steo"]["skipped"] is True
