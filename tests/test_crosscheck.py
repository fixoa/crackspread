"""fetch_crosscheck: whiteboard claims next to PortWatch figures and headline mentions, deterministically."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

import common
import fetch_crosscheck as fc

NOW = datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc)

MANUAL = {
    "updated_at": "2026-10-08",
    "default_source": {"name": "Max Fisher", "url": "https://www.youtube.com/watch?v=OETnuwwsv9U"},
    "hormuz_ledger": [{"id": "iran_blockade", "delta": -20}, {"id": "region_total", "value": 8, "type": "subtotal"}],
    "shipping": {"voyage_days_now": 48, "vlcc_day_rate_usd": {"now": 1200000}, "route_note": "closed"},
    "refinery_shock": [{"label": "x", "diesel_delta_mbd": -1.0}],
}
NEWS = {"as_of": "2026-10-08T09:00:00Z", "items": [
    {"title": "Tanker traffic through the Strait of Hormuz falls to 2 ships a day", "source": "Reuters", "link": "https://example.com/a", "published": "2026-10-08T08:00:00Z", "snippet": "Down from about 48 tankers in 2025."},
    {"title": "VLCC day rates hit $1.5 million as owners cash in", "source": "gCaptain", "link": "https://example.com/b", "published": "2026-10-08T07:00:00Z", "snippet": ""},
    {"title": "Spring weather forecast", "source": "BBC", "link": "https://example.com/c", "published": "2026-10-08T06:00:00Z", "snippet": "no oil here"},
]}
SHIP = {"baseline_year": 2025, "source": "IMF PortWatch", "source_url": "https://portwatch.imf.org/",
        "chokepoints": [{"id": "hormuz", "name": "Strait of Hormuz", "tankers_7d": 0.9, "tankers_baseline": 48.2, "tankers_change_pct": -98, "latest_date": "2026-10-04"}]}


def test_resolve_paths():
    assert fc._resolve(MANUAL, "hormuz_ledger[iran_blockade].delta") == -20
    assert fc._resolve(MANUAL, "hormuz_ledger[region_total].value") == 8
    assert fc._resolve(MANUAL, "shipping.vlcc_day_rate_usd.now") == 1200000
    assert fc._resolve(MANUAL, "refinery_shock[0].diesel_delta_mbd") == -1.0
    assert fc._resolve(MANUAL, "refinery_shock[9].diesel_delta_mbd") is None
    assert fc._resolve(MANUAL, "hormuz_ledger[nope].delta") is None


def test_numbers_are_verbatim_phrases_and_skip_years():
    nums = fc._numbers("VLCC rates hit $1.5 million in 2026; 20 million barrels, 48 days, 97.9%")
    assert "$1.5 million" in nums and "20 million barrels" in nums and "48 days" in nums and "97.9%" in nums
    assert "2026" not in nums


def test_build_matches_keywords_and_portwatch(cfg):
    doc, info = fc.build(cfg, MANUAL, NEWS, SHIP, NOW)
    common.validate(doc, "crosscheck")
    by = {c["id"]: c for c in doc["claims"]}
    h = by["hormuz_flow"]
    assert h["whiteboard"]["value"] == -20 and h["portwatch"]["tankers_7d"] == 0.9 and h["portwatch"]["tankers_baseline"] == 48.2
    assert [m["link"] for m in h["mentions"]] == ["https://example.com/a"]
    assert "2 ships" in h["mentions"][0]["numbers"] and "48 tankers" in h["mentions"][0]["numbers"]
    v = by["vlcc"]
    assert v["whiteboard"]["value"] == 1200000 and v["mentions"][0]["source"] == "gCaptain" and "$1.5 million" in v["mentions"][0]["numbers"]
    assert by["spr"]["mentions"] == [], "'spr' must not match 'Spring'"
    assert info["claims"] == len(cfg["crosscheck"]) and info["with_portwatch"] >= 1


def test_missing_context_gives_empty_but_valid(cfg):
    doc, info = fc.build(cfg, MANUAL, None, None, NOW)
    common.validate(doc, "crosscheck")
    assert all(c["mentions"] == [] and c["portwatch"] is None for c in doc["claims"])
    assert doc["news_items_scanned"] == 0


def test_run_uses_context_and_fixture_news(cfg):
    doc, info = fc.run(cfg, None, fixtures=True, now=NOW, context={"manual": MANUAL, "news": NEWS, "shipping": SHIP})
    common.validate(doc, "crosscheck")
    assert info["mentions"] >= 2


def test_no_claims_configured_raises():
    with pytest.raises(common.FetchError):
        fc.build({"crosscheck": []}, MANUAL, NEWS, SHIP, NOW)
