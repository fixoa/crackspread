"""RSS → ``news.json`` (ARCHITECTURE §2, §3.4; brief §5.1, §6.4, §18).

Sources are the Google News search feeds built from ``cfg["news_queries"]`` plus the publisher
feeds in ``cfg["news_feeds"]``.  Every feed is fetched and parsed in its own ``try``/``except``;
a broken feed is recorded in the ``feeds`` status list (and in ``info["feeds_failed"]``) and the
others are still returned.  Only when *every* feed fails does :func:`run` raise, so that
``update.py`` keeps the previous file and marks it stale.

Per item we store title, publisher, link, publication time, a short snippet (≤ 240 characters,
HTML removed), topics and the feed it came from — never full texts.  Items are deduplicated on
the normalised title and on the link, filtered by keywords (feeds with ``"filter": true`` only),
limited to ``cfg["news_max_age_hours"]``, sorted newest first and capped at
``cfg["news_max_items"]``.

Fixture mode reads exactly the seven feeds in ``tests/fixtures/rss/`` (three Google queries and
four publisher feeds) and touches no network.  Runs on Python 3.9 and 3.12.
"""
from __future__ import annotations

import html
import re
import unicodedata
import warnings
from datetime import datetime, timezone
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

import feedparser
from dateutil import parser as dateutil_parser

import common

SOURCE_KEY = "news"
OUTPUT_FILE = "news.json"
SCHEMA = "news"
STALE_KIND = "news"

SOURCE_LABEL = "Google News RSS + publisher RSS feeds (each item names its publisher)"
SOURCE_URL = "https://news.google.com/"
GOOGLE_FEED_ID_PREFIX = "google:"
GOOGLE_RSS_URL = "https://news.google.com/rss/search?q={query}+when:1d&hl=en-US&gl=US&ceid=US:en"
GOOGLE_NAME = "Google News"

SNIPPET_MAX = 240
ELLIPSIS = "…"
DEFAULT_MAX_ITEMS = 40
DEFAULT_MAX_AGE_HOURS = 48
MAX_FEED_BYTES = 5 * 1024 * 1024      # the largest real feed is ~350 KB; a bigger body is not a feed

#: Fixture mode: Google query → file and feed id → file under ``tests/fixtures/rss/``.
FIXTURE_GOOGLE = {
    "oil supply": "google_oil_supply.xml",
    "Strait of Hormuz": "google_hormuz.xml",
    '"crack spread"': "google_crack_spread.xml",
}
FIXTURE_FEEDS = {
    "oilprice": "oilprice.xml",
    "gcaptain": "gcaptain.xml",
    "eia_tie": "eia_tie.xml",
    "bbc_business": "bbc_business.xml",
}

_TRACKING_PARAM_PREFIXES = ("utm_", "at_")
_TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid", "igshid"}
_TAG_RE = re.compile(r"<[^>]*>")
_TRAILING_PUBLISHER_RE = re.compile(r"\s+[-–—|]\s+[^-–—|]{1,60}$")
_PUNCT_RE = re.compile(r"[^0-9a-z]+")


# ------------------------------------------------------------------------------- text helpers

class _TextExtractor(HTMLParser):
    """Collects the text of an HTML fragment; block boundaries become spaces, script/style dropped."""

    _BLOCK = {
        "p", "div", "br", "li", "ul", "ol", "tr", "td", "th", "table", "blockquote", "hr",
        "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "header", "footer", "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:  # noqa: D401
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self._BLOCK:
            self.parts.append(" ")

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        if tag in self._BLOCK:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in self._BLOCK:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def collapse_whitespace(text: str) -> str:
    """Collapse any run of Unicode whitespace (incl. NBSP, newlines) to a single space and trim."""
    return " ".join(str(text).split())


def strip_html(text: Optional[str]) -> str:
    """Plain text of an HTML fragment: tags removed, entities unescaped (also double-encoded
    ones such as ``&amp;nbsp;``), whitespace collapsed."""
    if not text:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(str(text))
        parser.close()
        out = "".join(parser.parts)
    except Exception:  # noqa: BLE001  (html.parser is lenient; this is a last resort)
        out = _TAG_RE.sub(" ", str(text))
    out = html.unescape(out)          # handles entities that were encoded twice in the feed
    out = _TAG_RE.sub(" ", out)       # ...and tags that only appeared after that unescape
    return collapse_whitespace(out)


def truncate_snippet(text: str, limit: int = SNIPPET_MAX) -> str:
    """Cut ``text`` to at most ``limit`` characters on a word boundary and append ``…``."""
    text = collapse_whitespace(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    cut = cut.rstrip(" ,;:.!?-–—(“\"'")
    return cut + ELLIPSIS


def _ascii_fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _key_text(text: str) -> str:
    """Lowercase, accents and punctuation stripped, whitespace collapsed (comparison key)."""
    return collapse_whitespace(_PUNCT_RE.sub(" ", _ascii_fold(text).lower()))


def normalise_title(title: str, source: Optional[str] = None) -> str:
    """Dedupe key for a headline: the known publisher suffix (``" - Reuters"``) and, failing
    that, any short trailing ``" - Something"`` segment removed; lowercase; punctuation out."""
    text = collapse_whitespace(title)
    stripped = strip_publisher_suffix(text, source)
    if stripped == text:
        stripped = _TRAILING_PUBLISHER_RE.sub("", text)
    return _key_text(stripped)


def strip_publisher_suffix(title: str, source: Optional[str]) -> str:
    """Remove a trailing ``" - <source>"`` (Google News style) when it names exactly ``source``."""
    if not source:
        return title
    lowered, src = title.lower(), collapse_whitespace(source).lower()
    for sep in (" - ", " – ", " — ", " | "):
        if src and lowered.endswith(sep + src):
            return title[: len(title) - len(sep) - len(src)].rstrip()
    m = _TRAILING_PUBLISHER_RE.search(title)
    if m and _key_text(m.group(0)) == _key_text(source):
        return title[: m.start()].rstrip()
    return title


def normalise_link(link: str) -> str:
    """Trim, lowercase scheme/host, drop the fragment and tracking parameters (``utm_*``,
    ``at_*``, ``fbclid`` …). Everything else (e.g. Google's ``?oc=5``) is kept verbatim."""
    link = str(link).strip()
    parts = urlsplit(link)
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        kept = [
            (k, v) for k, v in pairs
            if not (k.lower().startswith(_TRACKING_PARAM_PREFIXES) or k.lower() in _TRACKING_PARAMS)
        ]
        if len(kept) != len(pairs):
            query = urlencode(kept, doseq=True)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, query, ""))


# --------------------------------------------------------------------------- keyword matching

@lru_cache(maxsize=256)
def _keyword_regex(keywords: Tuple[str, ...]) -> Optional["re.Pattern[str]"]:
    """One case-insensitive regex for a keyword list. Keywords match at word boundaries with an
    optional English suffix (``tanker`` → ``tankers``, ``iran`` → ``iranian``), so ``oil`` does
    not match ``turmoil``; keywords that start/end with a symbol (``$``) skip that boundary."""
    alternatives = []
    for kw in keywords:
        kw = collapse_whitespace(str(kw)).lower()
        if not kw:
            continue
        pattern = re.escape(kw).replace(r"\ ", r"\s+")
        if kw[0].isalnum():
            pattern = r"(?<![0-9a-z])" + pattern
        if kw[-1].isalnum():
            pattern = pattern + r"(?:s|es|ed|ing|ies|ian)?(?![0-9a-z])"
        alternatives.append(pattern)
    if not alternatives:
        return None
    return re.compile("|".join(alternatives), re.IGNORECASE)


def matches_keywords(text: str, keywords: Any) -> bool:
    """``True`` if any of ``keywords`` occurs in ``text`` (word-boundary aware, case-insensitive)."""
    rx = _keyword_regex(tuple(str(k) for k in (keywords or [])))
    return bool(rx and rx.search(text or ""))


def topics_for(text: str, topic_map: Dict[str, Any], extra: Any = None) -> List[str]:
    """Topics whose keyword lists match ``text`` (in ``topic_map`` order) plus ``extra`` topics."""
    found = [name for name, kws in (topic_map or {}).items() if matches_keywords(text, kws)]
    for topic in extra or []:
        if topic and topic not in found:
            found.append(topic)
    return _order_topics(found, topic_map)


def _order_topics(topics: Any, topic_map: Dict[str, Any]) -> List[str]:
    wanted = set(topics or [])
    ordered = [name for name in (topic_map or {}) if name in wanted]
    ordered += sorted(t for t in wanted if t not in (topic_map or {}))
    return ordered


# -------------------------------------------------------------------------------- date parsing

def entry_datetime(entry: Any) -> Optional[datetime]:
    """Publication time of a feedparser entry as an aware UTC datetime, or ``None``.

    Uses feedparser's parsed structs first (``published_parsed`` / ``updated_parsed`` /
    ``created_parsed``, already UTC), then ``dateutil`` on the raw strings."""
    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # feedparser's updated_parsed→published_parsed DeprecationWarning
            st = _get(entry, key)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
            except (TypeError, ValueError):
                continue
    for key in ("published", "updated", "created", "dc_date"):
        raw = _get(entry, key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # dateutil's UnknownTimezoneWarning
                dt = dateutil_parser.parse(raw.strip())
        except (ValueError, OverflowError, TypeError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def _get(entry: Any, key: str, default: Any = None) -> Any:
    try:
        return entry.get(key, default)
    except Exception:  # noqa: BLE001
        return default


# ------------------------------------------------------------------------------- feed specs

def feed_specs(cfg: dict, fixtures: bool = False) -> List[dict]:
    """The feeds to fetch, in config order: Google query feeds first, then ``news_feeds``.
    In fixture mode only the seven feeds with a file in ``tests/fixtures/rss/`` are kept."""
    specs: List[dict] = []
    for query in cfg.get("news_queries") or []:
        query = str(query).strip()
        if not query:
            continue
        spec = {
            "id": GOOGLE_FEED_ID_PREFIX + query,
            "name": GOOGLE_NAME,
            "url": google_feed_url(query),
            "filter": False,
            "topics": [],
            "google": True,
        }
        if fixtures:
            if query not in FIXTURE_GOOGLE:
                continue
            spec["fixture"] = common.FIXTURES / "rss" / FIXTURE_GOOGLE[query]
        specs.append(spec)
    for feed in cfg.get("news_feeds") or []:
        if not isinstance(feed, dict) or not feed.get("id") or not feed.get("url"):
            continue
        spec = {
            "id": str(feed["id"]),
            "name": str(feed.get("name") or feed["id"]),
            "url": str(feed["url"]),
            "filter": bool(feed.get("filter", False)),
            "topics": [str(t) for t in (feed.get("topics") or [])],
            "google": False,
        }
        if fixtures:
            if spec["id"] not in FIXTURE_FEEDS:
                continue
            spec["fixture"] = common.FIXTURES / "rss" / FIXTURE_FEEDS[spec["id"]]
        specs.append(spec)
    return specs


def google_feed_url(query: str) -> str:
    """Google News search feed for ``query`` (URL-encoded) limited to the last day (``when:1d``)."""
    return GOOGLE_RSS_URL.format(query=quote_plus(query))


def _load_feed(spec: dict, session: Any = None) -> bytes:
    """Raw feed bytes from the fixture file or via ``common.http_get`` (which follows redirects)."""
    fixture = spec.get("fixture")
    if fixture is not None:
        with open(fixture, "rb") as fh:
            return fh.read()
    return common.http_get(spec["url"], session=session, max_bytes=MAX_FEED_BYTES).content


def parse_feed(data: bytes) -> Any:
    """``feedparser.parse`` on response bytes (encoding from the XML declaration / byte sniffing).
    Raises ``ValueError`` when the document is not a feed at all (parser error *and* no entries,
    e.g. an HTML error page or a ``Redirecting...`` body)."""
    parsed = feedparser.parse(data)
    if not parsed.entries and getattr(parsed, "bozo", False):
        exc = getattr(parsed, "bozo_exception", None)
        raise ValueError("not a parsable feed: %s" % (exc or "unknown parser error"))
    return parsed


# ------------------------------------------------------------------------------ item building

def build_item(entry: Any, spec: dict, cfg: dict, fetched_at: datetime) -> Optional[dict]:
    """One news item (plus private ``_published`` / ``_key`` fields) from a feedparser entry, or
    ``None`` when the entry has no usable title or http(s) link."""
    title = strip_html(_get(entry, "title") or "")
    link = collapse_whitespace(str(_get(entry, "link") or ""))
    if not title or not link.lower().startswith(("http://", "https://")):
        return None

    source = ""
    if spec.get("google"):
        src = _get(entry, "source")
        if isinstance(src, dict):
            source = collapse_whitespace(str(src.get("title") or ""))
    if not source:
        source = spec["name"]
    if spec.get("google"):
        title = strip_publisher_suffix(title, source)

    published_dt = entry_datetime(entry)
    date_estimated = published_dt is None
    if published_dt is None:
        published_dt = fetched_at

    raw_snippet = _get(entry, "summary") or _get(entry, "description") or ""
    if not raw_snippet:
        content = _get(entry, "content")
        if isinstance(content, list) and content and isinstance(content[0], dict):
            raw_snippet = content[0].get("value") or ""
    full_text = strip_html(raw_snippet)
    snippet = full_text
    if snippet and _snippet_repeats_title(snippet, title, source, _get(entry, "title") or ""):
        snippet = ""
    snippet = truncate_snippet(snippet, SNIPPET_MAX)

    link = normalise_link(link)
    item = {
        "id": common.sha1(link),
        "title": title,
        "source": source,
        "link": link,
        "published": common.iso_utc(published_dt),
        "snippet": snippet,
        "topics": topics_for(title + " " + snippet, cfg.get("news_topics") or {}, spec.get("topics")),
        "feed": spec["id"],
        "_published": published_dt,
        "_key": normalise_title(title, source),
        "_text": title + " " + full_text,   # keyword filter sees the whole description, not the cut snippet
    }
    if date_estimated:
        item["date_estimated"] = True
    return item


def _snippet_repeats_title(snippet: str, title: str, source: str, raw_title: str) -> bool:
    """Google descriptions are just ``<a>Title</a> <font>Publisher</font>``: drop those."""
    key = _key_text(snippet)
    if not key:
        return True
    candidates = {_key_text(title), _key_text(title + " " + source), _key_text(raw_title),
                  _key_text(strip_html(raw_title) + " " + source)}
    return key in candidates


# ------------------------------------------------------------------------------------- run

def run(
    cfg: dict,
    old: Optional[dict],
    *,
    fixtures: bool = False,
    now: Optional[datetime] = None,
    session: Any = None,
    env: Any = None,
    context: Optional[dict] = None,
) -> Tuple[dict, dict]:
    """Fetch all feeds and build ``news.json`` (see module docstring). Returns ``(doc, info)``.

    ``old``, ``env`` and ``context`` are accepted for the fetcher contract and not needed here
    (a failed feed is simply left out; nothing is carried forward). Raises when no feed could be
    fetched at all."""
    now = common.now_utc() if now is None else now
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    max_items = int(cfg.get("news_max_items") or DEFAULT_MAX_ITEMS)
    max_age_hours = float(cfg.get("news_max_age_hours") or DEFAULT_MAX_AGE_HOURS)
    keywords = cfg.get("news_keywords") or []

    specs = feed_specs(cfg, fixtures=fixtures)
    if not specs:
        raise ValueError("news: no feeds configured (news_queries / news_feeds)")

    candidates: List[dict] = []
    feed_status: List[dict] = []
    failed: List[str] = []
    dropped = {"unusable": 0, "filtered": 0, "too_old": 0, "duplicates": 0, "capped": 0}

    for order, spec in enumerate(specs):
        status = {"id": spec["id"], "ok": False, "items": 0, "kept": 0, "error": None}
        try:
            parsed = parse_feed(_load_feed(spec, session=session))
        except Exception as exc:  # noqa: BLE001  (one broken feed never aborts the run)
            status["error"] = ("%s: %s" % (type(exc).__name__, exc))[:300]
            failed.append(spec["id"])
            feed_status.append(status)
            common.log("news: %s FAILED %s" % (spec["id"], status["error"]))
            continue
        if getattr(parsed, "bozo", False):
            common.log("news: %s parsed with warnings: %s" % (spec["id"], getattr(parsed, "bozo_exception", "")))
        status["ok"] = True
        status["items"] = len(parsed.entries)
        for index, entry in enumerate(parsed.entries):
            try:
                item = build_item(entry, spec, cfg, now)
            except Exception as exc:  # noqa: BLE001  (one malformed entry never discards the feed)
                dropped["unusable"] += 1
                common.log("news: %s entry %d unusable: %s: %s" % (spec["id"], index, type(exc).__name__, exc))
                continue
            if item is None:
                dropped["unusable"] += 1
                continue
            if spec["filter"] and not matches_keywords(item["_text"], keywords):
                dropped["filtered"] += 1
                continue
            age_hours = (now - item["_published"]).total_seconds() / 3600.0
            if age_hours > max_age_hours:
                dropped["too_old"] += 1
                continue
            item["_order"] = (order, index)
            candidates.append(item)
            status["kept"] += 1
        feed_status.append(status)
        common.log("news: %s ok, %d entries, %d within %.0f h" % (spec["id"], status["items"], status["kept"], max_age_hours))

    if len(failed) == len(specs):
        raise common.FetchError("news: all %d feeds failed (%s)" % (len(specs), ", ".join(failed)))

    items = _dedupe(candidates, dropped, cfg.get("news_topics") or {})
    items.sort(key=lambda it: (-it["_published"].timestamp(), it["_order"]))
    if len(items) > max_items:
        dropped["capped"] = len(items) - max_items
        items = items[:max_items]
    for it in items:
        for private in ("_published", "_key", "_text", "_order"):
            it.pop(private, None)

    stamp = common.iso_utc(now)
    doc = {
        "schema_version": 1,
        "generated_at": stamp,
        "as_of": items[0]["published"] if items else None,
        "fetched_at": stamp,
        "stale": False,
        "source": SOURCE_LABEL,
        "source_url": SOURCE_URL,
        "window_hours": max_age_hours,
        "items": items,
        "feeds": feed_status,
    }
    info = {
        "items": len(items),
        "feeds_failed": failed,
        "feeds_ok": len(specs) - len(failed),
        "candidates": len(candidates),
        "dropped": dropped,
    }
    common.log("news: %d items from %d/%d feeds (dropped %s)" % (len(items), info["feeds_ok"], len(specs), dropped))
    return doc, info


def _dedupe(candidates: List[dict], dropped: Dict[str, int], topic_map: Dict[str, Any]) -> List[dict]:
    """Keep one item per normalised title and per link. The earliest-published version of a story
    wins (usually the original report); topics of the dropped copies are merged into it."""
    by_title: Dict[str, dict] = {}
    by_link: Dict[str, dict] = {}
    kept: List[dict] = []
    for item in sorted(candidates, key=lambda it: (it["_published"].timestamp(), it["_order"])):
        twin = by_link.get(item["link"]) or by_title.get(item["_key"])
        if twin is not None:
            merged = set(twin["topics"]) | set(item["topics"])
            twin["topics"] = _order_topics(merged, topic_map)
            dropped["duplicates"] += 1
            continue
        by_link[item["link"]] = item
        by_title[item["_key"]] = item
        kept.append(item)
    return kept
