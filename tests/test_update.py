"""End-to-end tests for scripts/update.py with fake fetcher modules (ARCHITECTURE §4, §7).

The real fetchers are never imported here: every fetcher module name is injected into
``sys.modules`` as a fake that records its calls and returns a schema-valid document. The
documents use real observations (FRED 2026-10-06, EIA STEO October 2026, EIA International,
JODI 2026-07, EIA "Today in Energy" items) taken from the saved raw downloads and the brief —
nothing is invented. ``test_stale.py`` reuses the helpers below.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pytest

import build_meta
import common
import update

FIXTURE_NOW = update.FIXTURE_NOW                       # 2026-10-08T10:00:00Z
NOW_DT = datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)
REPO_URL = "https://github.com/fixoa/crackspread"

# Real EIA "Today in Energy" items (tests/fixtures/rss/eia_tie.xml, 2026-10-08 download).
NEWS_LINK_1 = "https://www.eia.gov/todayinenergy/detail.php?id=68264"
NEWS_LINK_2 = "https://www.eia.gov/todayinenergy/detail.php?id=68245"


# ------------------------------------------------------------------------- document builders

def _dp(value: Any, unit: str, as_of: str, series: str, fetched_at: str, **extra: Any) -> dict:
    return common.datapoint(value, unit, as_of, "FRED %s" % series,
                            "https://fred.stlouisfed.org/series/%s" % series,
                            fetched_at=fetched_at, series_id=series, **extra)


def prices_doc(now_iso: str = FIXTURE_NOW, as_of: str = "2026-10-06", brent: float = 125.44) -> dict:
    """prices.json with the real FRED observations for 2026-10-06 (brief §5.1/§5.3)."""
    f = now_iso
    latest = {
        "brent": _dp(brent, "USD/bbl", as_of, "DCOILBRENTEU", f),
        "wti": _dp(96.24, "USD/bbl", as_of, "DCOILWTICO", f),
        "ulsd_nyh": _dp(4.713, "USD/gal", as_of, "DDFUELNYH", f),
        "gasoline_nyh": _dp(3.396, "USD/gal", as_of, "DGASNYH", f),
        "jet_gulf": _dp(4.342, "USD/gal", as_of, "DJFUELUSGULF", f),
        "heating_oil_nyh": _dp(4.503, "USD/gal", as_of, "DHOILNYH", f),
        "retail_diesel_us": _dp(6.199, "USD/gal", "2026-10-05", "GASDESW", f, stale_kind="weekly"),
        "retail_gasoline_us": _dp(4.354, "USD/gal", "2026-10-05", "GASREGW", f, stale_kind="weekly"),
        "diesel_crack": _dp(72.51, "USD/bbl", as_of, "DDFUELNYH", f, formula="DDFUELNYH*42 - DCOILBRENTEU",
                            inputs={"DDFUELNYH": 4.713, "DCOILBRENTEU": 125.44}),
        "gasoline_crack": _dp(17.19, "USD/bbl", as_of, "DGASNYH", f, formula="DGASNYH*42 - DCOILBRENTEU",
                              inputs={"DGASNYH": 3.396, "DCOILBRENTEU": 125.44}),
        "jet_crack": _dp(56.92, "USD/bbl", as_of, "DJFUELUSGULF", f, formula="DJFUELUSGULF*42 - DCOILBRENTEU",
                         inputs={"DJFUELUSGULF": 4.342, "DCOILBRENTEU": 125.44}),
        "crack_321": _dp(64.83, "USD/bbl", as_of, "DCOILWTICO", f,
                         formula="(2*DGASNYH*42 + DDFUELNYH*42)/3 - DCOILWTICO",
                         inputs={"DGASNYH": 3.396, "DDFUELNYH": 4.713, "DCOILWTICO": 96.24}),
        "brent_wti_spread": _dp(29.2, "USD/bbl", as_of, "DCOILBRENTEU", f, formula="DCOILBRENTEU - DCOILWTICO",
                                inputs={"DCOILBRENTEU": 125.44, "DCOILWTICO": 96.24}),
    }
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": as_of,
        "fetched_at": now_iso,
        "stale": False,
        "source": "FRED (Federal Reserve Bank of St. Louis), data: U.S. EIA",
        "source_url": "https://fred.stlouisfed.org/",
        "latest": latest,
        "history": {   # 2026-10-05 and 2026-10-06 rows of the raw FRED CSVs
            "dates": ["2026-10-05", "2026-10-06"],
            "diesel_crack": [68.87, 72.51],
            "gasoline_crack": [13.85, 17.19],
            "jet_crack": [57.19, 56.92],
            "crack_321": [61.57, 64.83],
            "brent": [125.51, brent],
            "wti": [96.13, 96.24],
            "resolution_note": "daily (test excerpt)",
        },
        "stats": {
            "diesel_crack": {"max": 116.5, "max_date": "2022-05-11", "mean_2015_2019": 16.0,
                             "percentile_now": None, "level": None, "level_label": None,
                             "history_start": "2006-06-14", "n": None},
            "brent_wti_spread": {"value": 29.2, "as_of": as_of, "mean_2015_2019": None},
        },
    }


def balance_doc(now_iso: str = FIXTURE_NOW) -> dict:
    """balance.json from the saved STEO API response (October 2026 edition)."""
    sep = {"period": "2026-09", "production": 101.3, "consumption": 104.24, "stock_draw": 2.94,
           "opec": 23.85, "nonopec": 77.45, "brent": 114.16, "wti": 97.31, "is_forecast": False}
    octo = {"period": "2026-10", "production": 102.1, "consumption": 102.78, "stock_draw": 0.67,
            "opec": 23.97, "nonopec": 78.14, "brent": 111.0, "wti": 100.0, "is_forecast": True}
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": "2026-10-06",
        "fetched_at": now_iso,
        "stale": False,
        "source": "EIA Short-Term Energy Outlook (STEO), October 2026",
        "source_url": "https://www.eia.gov/outlooks/steo/report/global_oil.php",
        "via": "api",
        "steo_release": "2026-10-06",
        "next_release": "2026-11-10",
        "steo_edition": "October 2026",
        "current_month": "2026-10",
        "months": [sep, octo],
        "quarters": [{"period": "2026Q3", "stock_draw": 1.88, "is_forecast": False}],
        "current": dict(octo),
        "quote": "global oil inventories fell by an average of 1.9 million b/d in 3Q26 and will fall an "
                 "additional 0.7 million b/d on average in 4Q26",
        "quote_url": "https://www.eia.gov/outlooks/steo/report/global_oil.php",
        "status": "deficit",
        "unit": "million barrels per day",
    }


def countries_doc(now_iso: str = FIXTURE_NOW) -> dict:
    """countries.json from the saved EIA International responses and the JODI CSV."""
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": "2026-10-08",
        "fetched_at": now_iso,
        "stale": False,
        "source": "U.S. EIA International Energy Data",
        "source_url": "https://www.eia.gov/international/data/world",
        "via": "eia_api",
        "unit": "thousand barrels per day",
        "producers": {"year": 2025, "source": "U.S. EIA International Energy Data",
                      "source_url": "https://www.eia.gov/international/data/world",
                      "rows": [{"iso3": "USA", "name": "United States", "value": 23730.6},
                               {"iso3": "SAU", "name": "Saudi Arabia", "value": 11214.0},
                               {"iso3": "RUS", "name": "Russia", "value": 10534.5}],
                      "rest_of_world": 60822.8, "world_total": 106301.9,
                      "note": "Total petroleum and other liquids"},
        "consumers": {"year": 2024, "source": "U.S. EIA International Energy Data",
                      "source_url": "https://www.eia.gov/international/data/world",
                      "rows": [{"iso3": "USA", "name": "United States", "value": 20463.7},
                               {"iso3": "CHN", "name": "China", "value": 16370.5},
                               {"iso3": "IND", "name": "India", "value": 5598.9}],
                      "rest_of_world": 60677.1, "world_total": 103110.2,
                      "note": "Total petroleum and other liquids"},
        "monthly_crude": {"source": "JODI Oil World Database", "source_url": "https://www.jodidata.org/oil/",
                          "latest_month": "2026-07", "unit": "thousand barrels per day",
                          "rows": [{"iso2": "US", "name": "United States", "value": 13817.4},
                                   {"iso2": "SA", "name": "Saudi Arabia", "value": 8135.1}]},
    }


def news_doc(now_iso: str = FIXTURE_NOW) -> dict:
    """news.json with two real EIA 'Today in Energy' items."""
    items = [
        {"id": common.sha1(NEWS_LINK_1), "title": "Mixed outlook for energy expenditures this winter",
         "source": "EIA Today in Energy", "link": NEWS_LINK_1, "published": "2026-10-07T14:00:00Z",
         "snippet": "We expect energy expenditures this winter to vary because of diverging trends in energy "
                    "prices among fuels and regional variation in forecast temperatures.",
         "topics": ["official", "prices"], "feed": "eia_tie"},
        {"id": common.sha1(NEWS_LINK_2),
         "title": "Crude oil prices and refinery margins generally increased throughout the third quarter",
         "source": "EIA Today in Energy", "link": NEWS_LINK_2, "published": "2026-10-05T14:00:00Z",
         "snippet": "Petroleum markets in the third quarter of 2026 (3Q26) were characterized by increasing "
                    "prices for crude oil and petroleum products amid persistent conflict in the Middle East.",
         "topics": ["official", "refining", "prices"], "feed": "eia_tie"},
    ]
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": "2026-10-07T14:00:00Z",
        "fetched_at": now_iso,
        "stale": False,
        "source": "Google News RSS + publisher feeds",
        "source_url": "https://news.google.com/rss",
        "items": items,
        "feeds": [{"id": "eia_tie", "ok": True, "items": 2, "error": None}],
    }


def summary_doc(now_iso: str = FIXTURE_NOW) -> dict:
    """summary.json in rule-based form (provider none); bullets cite the news links above."""
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": now_iso,
        "fetched_at": now_iso,
        "stale": False,
        "source": "Crackspread rule-based summary",
        "source_url": REPO_URL,
        "provider": "none",
        "model": "",
        "fallback": True,
        "headline": "Summarizer on a coffee break: raw EIA headlines below",
        "what_changed": [
            {"text": "EIA: crude oil prices and refinery margins generally increased throughout the third quarter.",
             "sources": [NEWS_LINK_2]},
            {"text": "EIA expects a mixed outlook for energy expenditures this winter.", "sources": [NEWS_LINK_1]},
            {"text": "No AI provider configured; this box lists the newest official headlines only.",
             "sources": [NEWS_LINK_1, NEWS_LINK_2]},
        ],
        "quips": ["No, you don't have to blur it, it's just a chart."],
        "crack_o_meter": {"level": None, "label": None, "percentile": None, "value": 72.51,
                          "unit": "USD/bbl", "as_of": "2026-10-06"},
        "deltas": {},
        "inputs_digest": None,
    }


def proposals_doc(now_iso: str = FIXTURE_NOW) -> dict:
    return {
        "schema_version": 1,
        "generated_at": now_iso,
        "as_of": now_iso,
        "fetched_at": now_iso,
        "stale": False,
        "source": "Crackspread proposals (test)",
        "source_url": REPO_URL,
        "provider": "none",
        "model": "",
        "proposals": [],
    }


DOC_BUILDERS: Dict[str, Callable[[str], dict]] = {
    "prices": prices_doc,
    "balance": balance_doc,
    "countries": countries_doc,
    "news": news_doc,
    "summary": summary_doc,
    "proposals": proposals_doc,
}

FILE_OF = {"prices": "prices.json", "balance": "balance.json", "countries": "countries.json",
           "news": "news.json", "summary": "summary.json", "proposals": "proposals.json"}
META_KEY_OF = {"prices": "fred", "balance": "eia_steo", "countries": "countries",
               "news": "news", "summary": "summary", "proposals": "proposals"}


# ----------------------------------------------------------------------------- fake fetchers

class FakeFetchers:
    """Fake ``fetch_*``/``summarize`` modules injected into ``sys.modules``.

    * ``calls``: one dict per call (name, cfg, old, fixtures, now, env, context).
    * ``docs[name]``: builder ``f(now_iso) -> doc`` or a ready dict (deep-copied per call).
    * ``infos[name]``: the info dict returned next to the doc.
    * ``failures[name]``: an exception instance the fake raises instead of returning.
    """

    def __init__(self) -> None:
        self.calls: list = []
        self.docs: Dict[str, Any] = dict(DOC_BUILDERS)
        self.infos: Dict[str, dict] = {"prices": {"via": "fred"}, "balance": {"via": "api"},
                                       "countries": {"via": "eia_api"}, "news": {"items": 2, "feeds_failed": []},
                                       "summary": {"provider": "none", "model": "", "fallback": True},
                                       "proposals": {"provider": "none", "model": "", "count": 0}}
        self.failures: Dict[str, BaseException] = {}
        self.modules: Dict[str, types.ModuleType] = {}
        self._build_modules()

    def calls_for(self, name: str) -> list:
        return [c for c in self.calls if c["name"] == name]

    def _runner(self, name: str):
        def run(cfg, old, *, fixtures=False, now=None, session=None, env=None, context=None):
            self.calls.append({"name": name, "cfg": cfg, "old": old, "fixtures": fixtures, "now": now,
                               "env": env, "context": context})
            failure = self.failures.get(name)
            if failure is not None:
                raise failure
            builder = self.docs[name]
            now_iso = common.iso_utc(now) if now is not None else common.iso_utc()
            doc = builder(now_iso) if callable(builder) else copy.deepcopy(builder)
            return doc, dict(self.infos.get(name, {}))
        return run

    def _build_modules(self) -> None:
        def module(modname: str, **attrs: Any) -> types.ModuleType:
            mod = types.ModuleType(modname)
            for k, v in attrs.items():
                setattr(mod, k, v)
            return mod

        self.modules["fetch_prices"] = module("fetch_prices", SOURCE_KEY="fred", OUTPUT_FILE="prices.json",
                                              SCHEMA="prices", STALE_KIND="prices", run=self._runner("prices"))
        self.modules["fetch_balance"] = module("fetch_balance", SOURCE_KEY="eia_steo", OUTPUT_FILE="balance.json",
                                               SCHEMA="balance", STALE_KIND="steo", run=self._runner("balance"))
        self.modules["fetch_country"] = module("fetch_country", SOURCE_KEY="countries", OUTPUT_FILE="countries.json",
                                               SCHEMA="countries", STALE_KIND="country", run=self._runner("countries"))
        self.modules["fetch_news"] = module("fetch_news", SOURCE_KEY="news", OUTPUT_FILE="news.json",
                                            SCHEMA="news", STALE_KIND="news", run=self._runner("news"))
        self.modules["summarize"] = module("summarize", SOURCE_KEY="summary", OUTPUT_FILE="summary.json",
                                           SCHEMA="summary", STALE_KIND="summary",
                                           PROPOSALS_SOURCE_KEY="proposals", PROPOSALS_OUTPUT_FILE="proposals.json",
                                           PROPOSALS_SCHEMA="proposals",
                                           run=self._runner("summary"), run_proposals=self._runner("proposals"))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "FakeFetchers":
        for modname, mod in self.modules.items():
            monkeypatch.setitem(sys.modules, modname, mod)
        return self


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch) -> FakeFetchers:
    """All six fetcher modules replaced by fakes; ``CRACKSPREAD_NOW`` unset so ``--fixtures``
    has to provide the deterministic clock itself."""
    monkeypatch.delenv("CRACKSPREAD_NOW", raising=False)
    monkeypatch.delenv("CRACKSPREAD_LLM_FIXTURE", raising=False)
    return FakeFetchers().install(monkeypatch)


def run_cli(*args: str, data_dir: Path) -> int:
    return update.main([*args, "--data-dir", str(data_dir)])


def read(data_dir: Path, name: str) -> dict:
    with open(data_dir / name, "r", encoding="utf-8") as fh:
        return json.load(fh)


def files_in(data_dir: Path) -> set:
    return {p.name for p in data_dir.iterdir()}


# ---------------------------------------------------------------------------------- tests

def test_fixtures_end_to_end(fakes: FakeFetchers, tmp_data_dir: Path):
    rc = run_cli("--fixtures", data_dir=tmp_data_dir)
    assert rc == 0
    assert "CRACKSPREAD_NOW" not in os.environ, "--fixtures must not leak CRACKSPREAD_NOW past the run"

    # every data file written and schema-valid
    for name, filename in FILE_OF.items():
        doc = read(tmp_data_dir, filename)
        common.validate(doc, name)
        assert doc["stale"] is False, name
    assert read(tmp_data_dir, "prices.json")["latest"]["diesel_crack"]["value"] == 72.51

    # meta written, valid, deterministic clock from --fixtures
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    assert meta["fixtures"] is True and meta["dry_run"] is False
    assert meta["last_run"] == FIXTURE_NOW and meta["generated_at"] == FIXTURE_NOW
    assert meta["run_id"].startswith("20261008-100000-")
    assert meta["schedule"] == {"hours": [3, 10, 17], "minute": 17}
    assert meta["python"] == ".".join(str(v) for v in sys.version_info[:3])
    assert set(meta["sources"]) == set(META_KEY_OF.values())
    for key, entry in meta["sources"].items():
        assert entry["ok"] is True and entry["error"] is None, key
        assert entry["last_success"] == FIXTURE_NOW and entry["changed"] is True and entry["stale"] is False
    assert meta["sources"]["fred"]["via"] == "fred"
    assert meta["sources"]["news"]["items"] == 2

    # fetchers called in pipeline order with fixtures=True and the deterministic now
    order = [c["name"] for c in fakes.calls]
    assert order == ["prices", "balance", "countries", "news", "summary"]   # proposals: AI off → not called
    for call in fakes.calls:
        assert call["fixtures"] is True
        assert call["now"] == NOW_DT
        assert call["old"] is None            # empty data dir
    # context passing: the summarizer sees the documents of this run
    ctx = fakes.calls_for("summary")[0]["context"]
    assert ctx["prices"]["latest"]["brent"]["value"] == 125.44
    assert ctx["balance"]["current_month"] == "2026-10" and ctx["news"]["items"][0]["link"] == NEWS_LINK_1
    assert "manual" in ctx   # hand-maintained ledger is handed over too (None if absent)

    # proposals: ai_provider=gemini but no key in fixtures mode → empty document by update.py
    props = read(tmp_data_dir, "proposals.json")
    assert props["proposals"] == [] and props["provider"] == "none"
    assert meta["sources"]["proposals"]["count"] == 0 and meta["sources"]["proposals"]["provider"] == "none"


def test_run_proposals_called_when_ai_available(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    # fixtures mode: the LLM fixture variable stands in for a key
    monkeypatch.setenv("CRACKSPREAD_LLM_FIXTURE", "valid.json")
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    assert [c["name"] for c in fakes.calls][-2:] == ["summary", "proposals"]
    assert read(tmp_data_dir, "proposals.json")["source"] == "Crackspread proposals (test)"

    # live mode with an explicit env mapping holding the provider key
    fakes.calls.clear()
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    opts = update.RunOptions(data_dir=str(tmp_data_dir), env={"GEMINI_API_KEY": "test-key-not-real"})
    assert update.run_update(opts) == 0
    assert "proposals" in [c["name"] for c in fakes.calls]
    assert fakes.calls_for("proposals")[0]["env"] == {"GEMINI_API_KEY": "test-key-not-real"}

    # live mode without a key → skipped again
    fakes.calls.clear()
    assert update.run_update(update.RunOptions(data_dir=str(tmp_data_dir), env={})) == 0
    assert "proposals" not in [c["name"] for c in fakes.calls]


def test_no_ai_forces_provider_none(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_LLM_FIXTURE", "valid.json")   # would enable proposals without --no-ai
    assert run_cli("--fixtures", "--no-ai", data_dir=tmp_data_dir) == 0
    for call in fakes.calls:
        assert call["cfg"]["ai_provider"] == "none"
    assert "proposals" not in [c["name"] for c in fakes.calls]
    assert read(tmp_data_dir, "proposals.json")["proposals"] == []
    assert common.load_config()["ai_provider"] == "gemini", "--no-ai must not touch the shared config"


def test_dry_run_writes_nothing(fakes: FakeFetchers, tmp_data_dir: Path, capsys):
    assert run_cli("--fixtures", "--dry-run", data_dir=tmp_data_dir) == 0
    assert files_in(tmp_data_dir) == set(), "dry-run must not write anything, not even meta.json"
    out = capsys.readouterr().out
    assert "would write prices.json (changed): <new file>" in out
    assert "would write meta.json" in out
    assert len(fakes.calls) == 5   # everything was still fetched (AI off → proposals not called)
    ctx = fakes.calls_for("summary")[0]["context"]
    assert ctx["prices"]["latest"]["brent"]["value"] == 125.44, "context is passed even in dry-run"


def test_dry_run_reports_changed_keys(fakes: FakeFetchers, tmp_data_dir: Path, capsys):
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    before = {name: (tmp_data_dir / name).read_bytes() for name in files_in(tmp_data_dir)}
    capsys.readouterr()

    fakes.docs["prices"] = lambda now_iso: prices_doc(now_iso, brent=125.51)   # 2026-10-05 observation
    assert run_cli("--fixtures", "--dry-run", data_dir=tmp_data_dir) == 0
    out = capsys.readouterr().out
    prices_line = [l for l in out.splitlines() if l.startswith("would write prices.json")][0]
    assert "(changed)" in prices_line and "latest" in prices_line and "history" in prices_line
    assert "stats" not in prices_line.split(":", 1)[1]
    assert "would write balance.json (unchanged)" in out
    assert "would write news.json (unchanged)" in out
    assert {name: (tmp_data_dir / name).read_bytes() for name in files_in(tmp_data_dir)} == before


def test_only_restricts_sources(fakes: FakeFetchers, tmp_data_dir: Path):
    assert run_cli("--fixtures", "--only", "news,prices", data_dir=tmp_data_dir) == 0
    assert files_in(tmp_data_dir) == {"prices.json", "news.json", "meta.json"}
    assert [c["name"] for c in fakes.calls] == ["prices", "news"]   # pipeline order, not CLI order
    meta = read(tmp_data_dir, "meta.json")
    assert set(meta["sources"]) == {"fred", "news"}
    assert meta["only"] == ["prices", "news"]

    # a later restricted run keeps the untouched sources' previous status (marked skipped)
    fakes.calls.clear()
    assert run_cli("--fixtures", "--only", "balance", data_dir=tmp_data_dir) == 0
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    assert set(meta["sources"]) == {"fred", "news", "eia_steo"}
    assert meta["sources"]["fred"]["skipped"] is True and meta["sources"]["fred"]["changed"] is False
    assert "skipped" not in meta["sources"]["eia_steo"]


def test_only_rejects_unknown_source(fakes: FakeFetchers, tmp_data_dir: Path):
    assert run_cli("--fixtures", "--only", "prices,bogus", data_dir=tmp_data_dir) == 2
    assert files_in(tmp_data_dir) == set()
    assert fakes.calls == []


def test_parse_only():
    assert update.parse_only(None) is None
    assert update.parse_only([]) is None
    assert update.parse_only(["news,prices", "News"]) == ["prices", "news"]
    assert update.parse_only(["summary", "balance,countries"]) == ["balance", "countries", "summary"]
    with pytest.raises(ValueError, match="unknown source"):
        update.parse_only(["prices,fred"])


def test_fetcher_import_error_is_a_failure_not_a_crash(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fetch_balance", None)   # importlib raises ImportError for None entries
    rc = run_cli("--fixtures", data_dir=tmp_data_dir)
    assert rc == 0
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    entry = meta["sources"]["eia_steo"]
    assert entry["ok"] is False and entry["stale"] is True and entry["changed"] is False
    assert entry["error"].startswith(("ImportError", "ModuleNotFoundError"))
    assert "fetch_balance" in entry["error"]
    assert entry["last_success"] is None          # no previous meta.json to carry from
    assert not (tmp_data_dir / "balance.json").exists()
    assert all(meta["sources"][k]["ok"] for k in ("fred", "countries", "news", "summary", "proposals"))
    assert fakes.calls_for("summary")[0]["context"]["balance"] is None


def test_fetcher_without_run_or_with_bad_shape_is_a_failure(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    broken = types.ModuleType("fetch_news")
    broken.SOURCE_KEY = "news"
    monkeypatch.setitem(sys.modules, "fetch_news", broken)         # no run()
    fakes.docs["countries"] = {"schema_version": 1}                   # invalid document → ValidationError

    def bad_shape(cfg, old, **kw):
        return {"not": "a tuple"}
    fakes.modules["fetch_prices"].run = bad_shape

    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    meta = read(tmp_data_dir, "meta.json")
    assert meta["sources"]["news"]["error"].startswith("AttributeError")
    assert meta["sources"]["countries"]["error"].startswith("ValidationError")
    assert meta["sources"]["fred"]["error"].startswith("TypeError")
    assert files_in(tmp_data_dir) == {"balance.json", "summary.json", "proposals.json", "meta.json"}
    assert meta["sources"]["summary"]["ok"] is True and meta["sources"]["eia_steo"]["ok"] is True


def test_unchanged_content_keeps_old_file(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    """write_json_if_changed: a document that differs only in timestamps is not rewritten."""
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T03:17:00Z")
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    first = {name: (tmp_data_dir / name).read_bytes() for name in FILE_OF.values()}
    assert read(tmp_data_dir, "prices.json")["fetched_at"] == "2026-10-08T03:17:00Z"

    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T10:17:00Z")   # next run: new generated_at/fetched_at only
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    meta = read(tmp_data_dir, "meta.json")
    assert meta["last_run"] == "2026-10-08T10:17:00Z"
    for name, filename in FILE_OF.items():
        if name in ("summary",):
            continue   # the fake summary module stamps as_of = now; the real one is byte-stable
                       # (test_cli_subprocess_fixtures_runs_are_byte_stable)
        assert (tmp_data_dir / filename).read_bytes() == first[filename], filename
        assert meta["sources"][META_KEY_OF[name]]["changed"] is False, name
    assert read(tmp_data_dir, "prices.json")["fetched_at"] == "2026-10-08T03:17:00Z"
    assert meta["sources"]["summary"]["changed"] is True
    # second run saw the first run's output as `old`
    assert fakes.calls_for("prices")[1]["old"]["fetched_at"] == "2026-10-08T03:17:00Z"

    # real content change → rewritten
    fakes.docs["prices"] = lambda now_iso: prices_doc(now_iso, brent=125.51)
    monkeypatch.setenv("CRACKSPREAD_NOW", "2026-10-08T17:17:00Z")
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    prices = read(tmp_data_dir, "prices.json")
    assert prices["latest"]["brent"]["value"] == 125.51 and prices["fetched_at"] == "2026-10-08T17:17:00Z"
    assert read(tmp_data_dir, "meta.json")["sources"]["fred"]["changed"] is True


def test_meta_always_written_even_if_every_source_fails(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    monkeypatch.setenv("CRACKSPREAD_NOW", FIXTURE_NOW)
    for name in DOC_BUILDERS:
        fakes.failures[name] = RuntimeError("down: %s" % name)
    monkeypatch.setenv("CRACKSPREAD_LLM_FIXTURE", "valid.json")   # so run_proposals is attempted (and fails) too
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 0
    assert files_in(tmp_data_dir) == {"meta.json"}
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    assert all(not e["ok"] for e in meta["sources"].values())
    assert meta["sources"]["proposals"]["error"] == "RuntimeError: down: proposals"


def test_invalid_config_exits_2(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    bad = common.load_config()
    bad["ai_provider"] = "openai"
    monkeypatch.setattr(common, "load_config", lambda path=None: copy.deepcopy(bad))
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 2
    assert files_in(tmp_data_dir) == set()
    assert fakes.calls == []


def test_orchestrator_crash_exits_2(fakes: FakeFetchers, tmp_data_dir: Path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("meta builder exploded")
    monkeypatch.setattr(build_meta, "build", boom)
    assert run_cli("--fixtures", data_dir=tmp_data_dir) == 2
    assert "meta.json" not in files_in(tmp_data_dir)


def test_build_meta_shape(cfg):
    run_info = {"last_run": FIXTURE_NOW, "run_id": "20261008-100000-abc123", "duration_s": 1.23456,
                "fixtures": True, "dry_run": False, "python": "3.12.1", "only": None, "prev_meta": None}
    sources = {"fred": {"ok": True, "last_success": FIXTURE_NOW, "error": None, "stale": False, "changed": True,
                        "via": "fred"},
               "news": {"ok": False, "error": "FetchError: x", "last_success": None, "stale": True, "changed": False}}
    meta = build_meta.build(cfg, run_info, sources)
    common.validate(meta, "meta")
    assert meta["duration_s"] == 1.235 and meta["python"] == "3.12.1" and meta["fixtures"] is True
    assert meta["schedule"] == cfg["update_schedule_utc"]
    assert meta["sources"]["fred"]["via"] == "fred"
    assert meta["sources"]["news"]["last_success"] is None
    assert "only" not in meta


def test_cli_subprocess_no_ai_proposals_only(tmp_data_dir: Path):
    """The real CLI (no fakes): with the AI off the proposals document needs no fetcher module at
    all, stdout stays clean, meta.json is written, exit code 0. --dry-run prints and writes nothing."""
    env = dict(os.environ, CRACKSPREAD_NOW=FIXTURE_NOW)
    env.pop("GEMINI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    script = str(common.ROOT / "scripts" / "update.py")
    base = [sys.executable, script, "--no-ai", "--only", "proposals", "--data-dir", str(tmp_data_dir)]

    dry = subprocess.run(base + ["--dry-run"], cwd=str(common.ROOT), env=env, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    assert "would write proposals.json (changed): <new file>" in dry.stdout
    assert "would write meta.json" in dry.stdout
    assert files_in(tmp_data_dir) == set()

    real = subprocess.run(base, cwd=str(common.ROOT), env=env, capture_output=True, text=True)
    assert real.returncode == 0, real.stderr
    assert real.stdout == "", "library/CLI must keep stdout clean outside --dry-run"
    assert "proposals: ok via proposals (changed) as_of=%s" % FIXTURE_NOW in real.stderr
    assert files_in(tmp_data_dir) == {"proposals.json", "meta.json"}
    props = read(tmp_data_dir, "proposals.json")
    common.validate(props, "proposals")
    assert props["proposals"] == [] and props["provider"] == "none" and props["stale"] is False
    meta = read(tmp_data_dir, "meta.json")
    common.validate(meta, "meta")
    assert meta["sources"]["proposals"] == {"ok": True, "last_success": FIXTURE_NOW, "error": None, "stale": False,
                                            "changed": True, "provider": "none", "model": "", "count": 0,
                                            "reason": "AI disabled (provider none or no API key)"}

    # a second run at a later time: the empty document is byte-stable (as_of ignored for it)
    env["CRACKSPREAD_NOW"] = "2026-10-08T17:17:00Z"
    before = (tmp_data_dir / "proposals.json").read_bytes()
    again = subprocess.run(base, cwd=str(common.ROOT), env=env, capture_output=True, text=True)
    assert again.returncode == 0, again.stderr
    assert (tmp_data_dir / "proposals.json").read_bytes() == before
    assert read(tmp_data_dir, "meta.json")["sources"]["proposals"]["changed"] is False


def test_cli_subprocess_fixtures_runs_are_byte_stable(tmp_data_dir: Path):
    """Real modules, `--fixtures --no-ai` three times at different clock times: once the deltas
    have settled (run 2 sees run 1 as `old`), nothing changes any more — summary.json included
    (brief §3/§7/§17: a data commit only when something really changed)."""
    env = dict(os.environ)
    env.pop("GEMINI_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    base = [sys.executable, str(common.ROOT / "scripts" / "update.py"), "--fixtures", "--no-ai", "--data-dir", str(tmp_data_dir)]
    for stamp in ("2026-10-08T03:17:00Z", "2026-10-08T10:17:00Z"):
        env["CRACKSPREAD_NOW"] = stamp
        run = subprocess.run(base, cwd=str(common.ROOT), env=env, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
    settled = {p.name: p.read_bytes() for p in tmp_data_dir.iterdir() if p.name != "meta.json"}
    assert set(settled) == set(FILE_OF.values())
    env["CRACKSPREAD_NOW"] = "2026-10-08T17:17:00Z"
    third = subprocess.run(base, cwd=str(common.ROOT), env=env, capture_output=True, text=True)
    assert third.returncode == 0, third.stderr
    for name, data in settled.items():
        assert (tmp_data_dir / name).read_bytes() == data, name
    meta = read(tmp_data_dir, "meta.json")
    assert meta["last_run"] == "2026-10-08T17:17:00Z"
    assert all(entry["changed"] is False for entry in meta["sources"].values()), meta["sources"]
    summary = read(tmp_data_dir, "summary.json")
    # written by run 2 (deltas settled there: run 1 had no previous value), untouched by run 3
    assert summary["as_of"] == read(tmp_data_dir, "news.json")["as_of"] and summary["generated_at"] == "2026-10-08T10:17:00Z"
