"""Tests for scripts/fetch_news.py (offline; fixture feeds in tests/fixtures/rss/)."""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone

import pytest

import common
import fetch_news

NOW = datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

GOOGLE_HORMUZ_QUERY = "Strait of Hormuz"
GOOGLE_OIL_QUERY = "oil supply"


# ----------------------------------------------------------------------------------- helpers

def _cfg_with(cfg: dict, *, queries=None, feed_ids=None, **overrides) -> dict:
    """A config copy restricted to the given Google queries / feed ids, with overrides applied."""
    c = copy.deepcopy(cfg)
    if queries is not None:
        c["news_queries"] = list(queries)
    if feed_ids is not None:
        c["news_feeds"] = [f for f in c["news_feeds"] if f["id"] in feed_ids]
    c.update(overrides)
    return c


def _titles(doc: dict):
    return [it["title"] for it in doc["items"]]


class _StubResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status_code = status
        self.headers = {"content-type": "application/rss+xml; charset=utf-8"}


def _rss(*items: str) -> bytes:
    body = "".join(items)
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>t</title>'
            '<link>https://example.test/</link><description>d</description>%s</channel></rss>' % body).encode("utf-8")


def _item(title: str, link: str, pub: str = "Thu, 08 Oct 2026 08:00:00 GMT", desc: str = "") -> str:
    pub_el = "<pubDate>%s</pubDate>" % pub if pub else ""
    return "<item><title>%s</title><link>%s</link>%s<description><![CDATA[%s]]></description></item>" % (
        title, link, pub_el, desc)


# ---------------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="module")
def full_cfg() -> dict:
    return common.load_config()


@pytest.fixture(scope="module")
def uncapped(full_cfg: dict):
    """Fixture-mode run with a huge cap so that every surviving item is visible."""
    cfg = copy.deepcopy(full_cfg)
    cfg["news_max_items"] = 10000
    doc, info = fetch_news.run(cfg, None, fixtures=True, now=NOW)
    return doc, info


@pytest.fixture(scope="module")
def default_run(full_cfg: dict):
    """Fixture-mode run with the real config (cap 40, window 48 h)."""
    return fetch_news.run(copy.deepcopy(full_cfg), None, fixtures=True, now=NOW)


# -------------------------------------------------------------------------- fixture-mode run

def test_fixture_mode_uses_the_seven_fixture_feeds(default_run):
    doc, info = default_run
    ids = [f["id"] for f in doc["feeds"]]
    assert ids == ["google:oil supply", "google:Strait of Hormuz", 'google:"crack spread"',
                   "oilprice", "gcaptain", "eia_tie", "bbc_business"]
    assert all(f["ok"] is True and f["error"] is None and f["items"] > 0 for f in doc["feeds"])
    assert info["feeds_failed"] == []
    assert info["items"] == len(doc["items"]) > 0


def test_envelope_and_as_of(default_run):
    doc, _ = default_run
    assert doc["schema_version"] == 1
    assert doc["generated_at"] == doc["fetched_at"] == "2026-10-08T10:00:00Z"
    assert doc["stale"] is False
    assert doc["source"] and doc["source_url"].startswith("https://")
    assert doc["as_of"] == doc["items"][0]["published"]  # newest published


def test_output_validates_against_schema(default_run, uncapped, full_cfg):
    for doc, _ in (default_run, uncapped):
        common.validate(doc, "news")
        stamped = common.apply_time_stale(copy.deepcopy(doc), fetch_news.STALE_KIND, full_cfg, NOW)
        common.validate(stamped, "news")
        assert stamped["stale"] is False  # newest item is well within 24 h of NOW


def test_items_have_contract_shape(uncapped):
    doc, _ = uncapped
    for it in doc["items"]:
        assert set(it) >= {"id", "title", "source", "link", "published", "snippet", "topics", "feed"}
        assert not any(k.startswith("_") for k in it)
        assert re.fullmatch(r"[0-9a-f]{40}", it["id"])
        assert it["id"] == common.sha1(it["link"])
        assert it["link"].startswith(("http://", "https://"))
        assert it["title"].strip() == it["title"] and "\n" not in it["title"]
        assert it["source"]
        assert isinstance(it["topics"], list)


# ------------------------------------------------------------------------------------ dedupe

def test_no_duplicate_titles_or_links(uncapped):
    doc, _ = uncapped
    keys = [fetch_news.normalise_title(it["title"], it["source"]) for it in doc["items"]]
    links = [it["link"] for it in doc["items"]]
    assert len(keys) == len(set(keys))
    assert len(links) == len(set(links))


def test_same_story_in_two_feeds_becomes_one_item(full_cfg):
    # "Attacks on tankers in Hormuz hit highest of any week ..." is in gcaptain.xml and (via
    # several syndicating papers) in the Google "Strait of Hormuz" feed.
    cfg = _cfg_with(full_cfg, queries=[GOOGLE_HORMUZ_QUERY], feed_ids={"gcaptain"}, news_max_items=10000)
    doc, info = fetch_news.run(cfg, None, fixtures=True, now=NOW)
    key = fetch_news.normalise_title("Attacks on Tankers in Hormuz Hit Highest of Any Week Since Start of Iran War, Sources Say")
    hits = [it for it in doc["items"] if fetch_news.normalise_title(it["title"], it["source"]) == key]
    assert len(hits) == 1
    assert info["dropped"]["duplicates"] > 0
    assert "hormuz" in hits[0]["topics"] and "shipping" in hits[0]["topics"]


def test_dedupe_synthetic_title_and_link(full_cfg, monkeypatch):
    feeds = {
        "https://a.test/rss": _rss(
            _item("Oil tankers reroute around the Cape", "https://a.test/1", "Thu, 08 Oct 2026 06:00:00 GMT"),
            _item("Diesel prices climb again", "https://a.test/2", "Thu, 08 Oct 2026 07:00:00 GMT"),
        ),
        "https://b.test/rss": _rss(
            _item("Oil Tankers Reroute Around the Cape - Some Paper", "https://b.test/9", "Thu, 08 Oct 2026 08:00:00 GMT"),
            _item("Diesel prices climb again (updated)", "https://a.test/2?utm_source=rss", "Thu, 08 Oct 2026 09:00:00 GMT"),
        ),
    }
    monkeypatch.setattr(common, "http_get", lambda url, **kw: _StubResponse(feeds[url]))
    cfg = _cfg_with(full_cfg, queries=[])
    cfg["news_feeds"] = [{"id": "a", "name": "A", "url": "https://a.test/rss", "filter": False},
                         {"id": "b", "name": "B", "url": "https://b.test/rss", "filter": False}]
    doc, info = fetch_news.run(cfg, None, now=NOW)
    assert len(doc["items"]) == 2
    assert info["dropped"]["duplicates"] == 2
    # the earliest-published copy of a story wins; tracking params are stripped from links
    assert sorted(it["link"] for it in doc["items"]) == ["https://a.test/1", "https://a.test/2"]
    assert all(it["feed"] == "a" for it in doc["items"])


# ---------------------------------------------------------------------------------- snippets

def test_snippets_are_short_plain_text(uncapped):
    doc, _ = uncapped
    assert any(it["snippet"] for it in doc["items"])  # publisher feeds do carry snippets
    for it in doc["items"]:
        s = it["snippet"]
        assert len(s) <= 240
        assert "<" not in s and ">" not in s
        assert "&amp;" not in s and "&nbsp;" not in s and "&#" not in s
        assert "\n" not in s and "  " not in s and "\xa0" not in s
        assert s == s.strip()
        assert s != it["title"]


def test_google_snippet_that_repeats_the_title_is_dropped(uncapped):
    doc, _ = uncapped
    google = [it for it in doc["items"] if it["feed"].startswith("google:")]
    assert google
    assert all(it["snippet"] == "" for it in google)


def test_long_snippet_is_cut_on_a_word_boundary_with_ellipsis(uncapped):
    doc, _ = uncapped
    long_ones = [it for it in doc["items"] if it["snippet"].endswith("…")]
    assert long_ones  # oilprice.com descriptions are ~500 chars
    for it in long_ones:
        body = it["snippet"][:-1]
        assert len(it["snippet"]) <= 240
        assert not body.endswith(" ")
        assert len(body) > 120


def test_strip_html_and_truncate_helpers():
    raw = ('<p>Brent &amp; WTI rose.<br/>See <a href="x">the chart</a>&nbsp;&nbsp;'
           '<font color="#6f6f6f">Reuters</font></p><script>evil()</script>&amp;amp;done')
    assert fetch_news.strip_html(raw) == "Brent & WTI rose. See the chart Reuters &done"
    text = " ".join(["word%d" % i for i in range(80)])
    cut = fetch_news.truncate_snippet(text, 240)
    assert len(cut) <= 240 and cut.endswith("…")
    assert cut[:-1] == text[: len(cut) - 1] and not cut[:-1].endswith(" ")
    assert fetch_news.truncate_snippet("short text", 240) == "short text"


# -------------------------------------------------------------------------------- dates/sort

def test_published_is_iso_z_and_parseable(uncapped):
    doc, _ = uncapped
    for it in doc["items"]:
        assert TS_RE.match(it["published"]), it["published"]
        common.parse_iso(it["published"])
    # EIA "EST" timestamps are converted to UTC (07 Oct 09:00 EST → 14:00Z)
    eia = [it for it in doc["items"] if it["feed"] == "eia_tie"]
    assert eia and eia[0]["published"] == "2026-10-07T14:00:00Z"


def test_items_sorted_newest_first(uncapped, default_run):
    for doc, _ in (uncapped, default_run):
        stamps = [it["published"] for it in doc["items"]]
        assert stamps == sorted(stamps, reverse=True)


def test_age_cutoff(full_cfg):
    cfg = _cfg_with(full_cfg, queries=[], feed_ids={"eia_tie", "bbc_business"}, news_max_items=10000)
    doc, info = fetch_news.run(cfg, None, fixtures=True, now=NOW)
    titles = _titles(doc)
    assert "Mixed outlook for energy expenditures this winter" in titles          # 20 h old
    assert "Crude oil prices and refinery margins generally increased throughout the third quarter" not in titles  # 68 h
    assert "Watch: Why has UK diesel price hit an all time high?" not in titles   # 137 h, oil-related but old
    assert info["dropped"]["too_old"] > 0
    for it in doc["items"]:
        assert common.hours_since(it["published"], NOW) <= 48
    wide = _cfg_with(cfg, news_max_age_hours=24 * 30)
    doc_wide, _ = fetch_news.run(wide, None, fixtures=True, now=NOW)
    assert "Watch: Why has UK diesel price hit an all time high?" in _titles(doc_wide)
    assert len(doc_wide["items"]) > len(doc["items"])


def test_unparseable_date_falls_back_to_fetched_at(full_cfg, monkeypatch):
    feed = _rss(
        _item("Oil item without a date", "https://c.test/nodate", pub=""),
        _item("Oil item with an odd date", "https://c.test/odd", pub="October 7, 2026 12:00 UTC"),
    )
    monkeypatch.setattr(common, "http_get", lambda url, **kw: _StubResponse(feed))
    cfg = _cfg_with(full_cfg, queries=[])
    cfg["news_feeds"] = [{"id": "c", "name": "C", "url": "https://c.test/rss", "filter": False}]
    doc, _ = fetch_news.run(cfg, None, now=NOW)
    by_link = {it["link"]: it for it in doc["items"]}
    nodate = by_link["https://c.test/nodate"]
    assert nodate["published"] == "2026-10-08T10:00:00Z" and nodate["date_estimated"] is True
    odd = by_link["https://c.test/odd"]
    assert odd["published"] == "2026-10-07T12:00:00Z" and "date_estimated" not in odd


def test_entry_datetime_helper():
    assert fetch_news.entry_datetime({"published": "Wed, 07 Oct 2026 09:00:00 +0200"}) == datetime(2026, 10, 7, 7, 0, tzinfo=timezone.utc)
    assert fetch_news.entry_datetime({"published": "not a date"}) is None
    assert fetch_news.entry_datetime({}) is None


# ---------------------------------------------------------------------------------- limits

def test_max_items_respected(default_run, full_cfg):
    doc, info = default_run
    assert len(doc["items"]) == full_cfg["news_max_items"] == 40
    assert info["dropped"]["capped"] > 0
    small = _cfg_with(full_cfg, news_max_items=5)
    doc5, _ = fetch_news.run(small, None, fixtures=True, now=NOW)
    assert len(doc5["items"]) == 5
    assert doc5["items"] == doc["items"][:5]


# ---------------------------------------------------------------------------------- filter

def test_keyword_filter_on_filtered_feeds(full_cfg):
    cfg = _cfg_with(full_cfg, queries=[], feed_ids={"bbc_business"}, news_max_items=10000)
    doc, info = fetch_news.run(cfg, None, fixtures=True, now=NOW)
    titles = _titles(doc)
    assert "Fuel prices added to Google Maps as petrol and diesel costs soar" in titles
    assert "How oil-rich Alberta's economy might fare if it broke away from Canada" in titles
    assert "Asos hackers took more personal details than first revealed, BBC finds" not in titles
    assert info["dropped"]["filtered"] > 0
    assert doc["feeds"][0]["items"] == 15 and doc["feeds"][0]["kept"] == len(doc["items"])
    # with "filter": false the unrelated item stays
    unfiltered = copy.deepcopy(cfg)
    unfiltered["news_feeds"][0]["filter"] = False
    doc_all, _ = fetch_news.run(unfiltered, None, fixtures=True, now=NOW)
    assert "Asos hackers took more personal details than first revealed, BBC finds" in _titles(doc_all)


def test_keyword_matching_is_word_aware():
    kws = ["oil", "tanker", "$", "red sea", "iran"]
    assert fetch_news.matches_keywords("Oil-rich Alberta", kws)
    assert fetch_news.matches_keywords("seven tankers bought", kws)
    assert fetch_news.matches_keywords("Brent tops $105", kws)
    assert fetch_news.matches_keywords("Red  Sea transits", kws)
    assert fetch_news.matches_keywords("Iranian attacks", kws)
    assert not fetch_news.matches_keywords("market turmoil and spoiled milk", kws)
    assert not fetch_news.matches_keywords("", kws)


# ----------------------------------------------------------------------------------- topics

def test_topic_mapping(uncapped, full_cfg):
    doc, _ = uncapped
    hormuz_items = [it for it in doc["items"] if "hormuz" in it["title"].lower()]
    assert hormuz_items
    assert all("hormuz" in it["topics"] for it in hormuz_items)
    assert all("supply" in it["topics"] for it in doc["items"] if it["feed"] == "oilprice")     # feed-level topic
    assert all("official" in it["topics"] for it in doc["items"] if it["feed"] == "eia_tie")
    assert all("shipping" in it["topics"] for it in doc["items"] if it["feed"] == "gcaptain")
    order = list(full_cfg["news_topics"])
    for it in doc["items"]:
        assert it["topics"] == sorted(it["topics"], key=order.index)
        assert len(it["topics"]) == len(set(it["topics"]))
    assert fetch_news.topics_for("Diesel crack spread widens", full_cfg["news_topics"]) == ["diesel", "refining"]


# ---------------------------------------------------------------------------- google items

def test_google_items_use_source_element_and_redirect_link(uncapped):
    doc, _ = uncapped
    reuters = [it for it in doc["items"]
               if it["title"] == "Oil jumps as Middle East supply concerns persist amid shipping attacks"]
    assert len(reuters) == 1
    it = reuters[0]
    assert it["source"] == "Reuters"
    assert it["feed"] == "google:oil supply"
    assert it["link"].startswith("https://news.google.com/rss/articles/") and it["link"].endswith("?oc=5")
    assert it["published"] == "2026-10-08T01:25:00Z"
    assert not any(t.endswith(" - Reuters") for t in _titles(doc))


def test_publisher_suffix_stripping_and_dedupe_key():
    strip = fetch_news.strip_publisher_suffix
    assert strip("Oil jumps as supply concerns persist - Reuters", "Reuters") == "Oil jumps as supply concerns persist"
    # sources with punctuation (pipes, dashes) are still recognised when they match exactly
    odd = "Euronext Markets: Real-time Stock Market Data | live"
    assert strip("Oil jumps as supply concerns persist - " + odd, odd) == "Oil jumps as supply concerns persist"
    assert strip("Oil jumps - as supply concerns persist", "Reuters") == "Oil jumps - as supply concerns persist"
    assert strip("Plain headline", None) == "Plain headline"
    key = fetch_news.normalise_title
    assert key("Oil jumps as supply concerns persist - Reuters", "Reuters") == key("Oil Jumps as Supply Concerns Persist - " + odd, odd)
    assert key("Oil jumps as supply concerns persist - CNA") == key("Oil jumps, as supply concerns persist!")
    assert key("Russia’s fuel crisis") == key("Russia's fuel crisis")


def test_google_feed_url_encoding():
    url = fetch_news.google_feed_url('"crack spread"')
    assert url == "https://news.google.com/rss/search?q=%22crack+spread%22+when:1d&hl=en-US&gl=US&ceid=US:en"
    assert fetch_news.google_feed_url("Strait of Hormuz").startswith("https://news.google.com/rss/search?q=Strait+of+Hormuz+when:1d&")


def test_feed_specs_follow_config(full_cfg):
    specs = fetch_news.feed_specs(full_cfg)
    assert len(specs) == len(full_cfg["news_queries"]) + len(full_cfg["news_feeds"])
    assert specs[0]["id"] == "google:oil supply" and specs[0]["filter"] is False
    bbc = [s for s in specs if s["id"] == "bbc_business"][0]
    assert bbc["filter"] is True and bbc["url"] == "https://feeds.bbci.co.uk/news/business/rss.xml"
    assert len(fetch_news.feed_specs(full_cfg, fixtures=True)) == 7


# --------------------------------------------------------------------------------- failures

def test_all_feeds_failing_raises(full_cfg, monkeypatch):
    def boom(url, **kw):
        raise common.FetchError("HTTP 503 for %s" % url)
    monkeypatch.setattr(common, "http_get", boom)
    cfg = _cfg_with(full_cfg, queries=["oil supply"], feed_ids={"oilprice"})
    with pytest.raises(Exception):
        fetch_news.run(cfg, None, now=NOW)


def test_one_feed_failing_is_reported_and_rest_returned(full_cfg, monkeypatch, fixtures_dir):
    good = (fixtures_dir / "rss" / "oilprice.xml").read_bytes()

    def fake_get(url, **kw):
        if "oilprice.com" in url:
            return _StubResponse(good)
        if "news.google.com" in url:
            return _StubResponse(b"Redirecting...\n")   # not a feed → parse failure
        raise common.FetchError("HTTP 500 after 3 attempts for %s" % url)

    monkeypatch.setattr(common, "http_get", fake_get)
    cfg = _cfg_with(full_cfg, queries=["oil supply"], feed_ids={"oilprice", "gcaptain"})
    doc, info = fetch_news.run(cfg, None, now=NOW)
    assert sorted(info["feeds_failed"]) == ["gcaptain", "google:oil supply"]
    status = {f["id"]: f for f in doc["feeds"]}
    assert status["oilprice"]["ok"] is True and status["oilprice"]["error"] is None
    assert status["gcaptain"]["ok"] is False and "HTTP 500" in status["gcaptain"]["error"]
    assert status["google:oil supply"]["ok"] is False and status["google:oil supply"]["error"]
    assert doc["items"] and all(it["feed"] == "oilprice" for it in doc["items"])
    common.validate(doc, "news")


def test_fixture_mode_never_touches_network(full_cfg, monkeypatch):
    calls = []

    def spy(*a, **kw):
        calls.append(a)
        raise AssertionError("network call in fixture mode")

    monkeypatch.setattr(common, "http_get", spy)
    fetch_news.run(copy.deepcopy(full_cfg), None, fixtures=True, now=NOW)
    assert calls == []


def test_no_feeds_configured_raises(full_cfg):
    cfg = _cfg_with(full_cfg, queries=[])
    cfg["news_feeds"] = []
    with pytest.raises(ValueError):
        fetch_news.run(cfg, None, fixtures=True, now=NOW)


def test_malformed_entry_is_dropped_not_the_feed(full_cfg, monkeypatch):
    """One entry whose link makes urlsplit raise (unbalanced IPv6 bracket) is counted as unusable;
    the other entries of the feed and the feed status survive (brief §7, ARCHITECTURE §3.4)."""
    feed = _rss(
        _item("Brent crude climbs on Hormuz tanker attacks", "https://example.test/a"),
        _item("Diesel cracks widen again", "http://[::1/x"),
        _item("Refinery margins hold near record", "https://example.test/c"),
    )
    cfg = _cfg_with(full_cfg, queries=[], feed_ids=["oilprice"])
    monkeypatch.setattr(common, "http_get", lambda url, **kw: _StubResponse(feed))
    doc, info = fetch_news.run(cfg, None, fixtures=False, now=NOW)
    assert _titles(doc) == ["Brent crude climbs on Hormuz tanker attacks", "Refinery margins hold near record"]
    assert doc["feeds"] == [{"id": "oilprice", "ok": True, "items": 3, "kept": 2, "error": None}]
    assert info["feeds_failed"] == [] and info["dropped"]["unusable"] == 1
    common.validate(doc, "news")


# -------------------------------------------------------------------------------- contract

def test_module_constants():
    assert fetch_news.SOURCE_KEY == "news"
    assert fetch_news.OUTPUT_FILE == "news.json"
    assert fetch_news.SCHEMA == "news"
    assert fetch_news.STALE_KIND == "news"
