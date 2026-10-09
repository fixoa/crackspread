"""Crackspread summarizer: "What changed today" (summary.json) and whiteboard proposals.

Contract: ARCHITECTURE.md §2 (fetcher interface), §3.5 (summary.json), §3.6 (proposals.json);
brief §3.1 (providers), §6.5/§6.7 (shapes), §8 (prompt + validation chain).

Flow of :func:`run`:

1. ``build_input`` turns the run context (prices, balance, news) into one compact JSON document
   (< 8k tokens) with deltas against the previous run.  ``crack_o_meter`` and ``deltas`` are
   computed here, in code; the model never touches them.
2. A provider (``gemini`` | ``anthropic`` | ``none``) is selected from ``cfg["ai_provider"]``,
   the API keys in ``env`` and the fixtures flag.  Anything that prevents a model call (no key,
   ``--no-ai``, an exception) downgrades to ``none``.
3. The reply goes through the validation chain: JSON → schema → link whitelist → number guard →
   length/count limits → quips without digits.  One retry with the error text appended, then the
   rule-based fallback (``fallback=True``, ``provider="none"``).  If even that has nothing to
   work with, :func:`run` raises and ``update.py`` keeps the old file.

Runs on Python 3.9 and 3.12.  No stdout; diagnostics via ``common.log``.  No ``sys.exit``.
"""
from __future__ import annotations

import copy
import json
import os
import re
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError, best_match

import common

__all__ = [
    "SOURCE_KEY", "OUTPUT_FILE", "SCHEMA", "STALE_KIND",
    "PROPOSALS_SOURCE_KEY", "PROPOSALS_OUTPUT_FILE", "PROPOSALS_SCHEMA",
    "SYSTEM_PROMPT", "PROPOSALS_SYSTEM_PROMPT", "DEFAULT_GEMINI_MODEL", "DEFAULT_ANTHROPIC_MODEL",
    "run", "run_proposals", "build_input", "build_proposals_input",
    "validate_reply", "parse_reply", "normalise_number", "extract_numbers", "numbers_in_input",
    "check_numbers", "crack_level", "crack_o_meter_from_prices", "compute_deltas",
    "rule_based_summary", "guard_proposals", "allowed_targets",
    "complete_gemini", "complete_anthropic", "select_provider", "ValidationFailure", "EmptyReply",
]

# ------------------------------------------------------------------------------ constants

SOURCE_KEY = "summary"
OUTPUT_FILE = "summary.json"
SCHEMA = "summary"
STALE_KIND = "summary"

PROPOSALS_SOURCE_KEY = "proposals"
PROPOSALS_OUTPUT_FILE = "proposals.json"
PROPOSALS_SCHEMA = "proposals"

#: Default Gemini model when ``cfg["ai_model"]`` is empty. Checked on 2026-10-08 against
#: https://ai.google.dev/gemini-api/docs/models : "Gemini 3.8 Flash" (``gemini-3.8-flash``) is the
#: current general-purpose Flash model; the 2.5 generation is listed as capacity-limited.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
#: Default Anthropic model: the current Haiku-class model. Checked on 2026-10-08 against
#: https://platform.claude.com/docs/en/about-claude/model-deprecations : ``claude-haiku-5-5`` is
#: active (retirement not sooner than 2027-10-07), while ``claude-haiku-4-5`` resolves to
#: ``claude-haiku-4-5-20251001`` whose retirement may come as early as 2026-10-15. Pay-as-you-go;
#: a few cents per month at three runs a day. Override via ``cfg["ai_model"]``.
DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
ANTHROPIC_VERSION = "2023-06-01"

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

PROVIDERS = ("gemini", "anthropic", "none")
KEY_ENV = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}

LLM_FIXTURE_DIR = common.FIXTURES / "llm"
FIXTURE_ENV = "CRACKSPREAD_LLM_FIXTURE"
PROPOSALS_FIXTURE_ENV = "CRACKSPREAD_LLM_PROPOSALS_FIXTURE"

MAX_NEWS_ITEMS = 30
MAX_INPUT_CHARS = 28000        # ~7k tokens at 4 chars/token; keeps the input under 8k tokens
MAX_HEADLINE = 90
MAX_TEXT = 220
MAX_QUIP = 120
MIN_BULLETS, MAX_BULLETS = 3, 5
MAX_QUIPS = 2
NUMBER_TOLERANCE = 0.01
HTTP_TIMEOUT = 60
#: Output cap for both providers. Gemini 3 models think by default and the cap includes the
#: thought tokens (https://ai.google.dev/gemini-api/docs/thinking: "including thought tokens";
#: on overflow the reply comes back truncated or empty), so it must leave ample room beyond the
#: ~600-token JSON reply. For Anthropic ``max_tokens`` is a ceiling, not a cost.
MAX_OUTPUT_TOKENS = 8192
#: Thinking depth requested from Gemini 3 models: "low" is plenty for a 3-5 bullet JSON summary
#: and keeps the thought tokens far below MAX_OUTPUT_TOKENS.
GEMINI_THINKING_LEVEL = "low"

#: Brief §8, verbatim.
SYSTEM_PROMPT = (
    'You write the "What changed today" box for Crackspread, a site that explains the world oil market simply.\n'
    "Use ONLY the facts, numbers and headlines provided in the input JSON. Never invent, estimate, round into new figures, or recall numbers from memory.\n"
    "Every numeric figure you write must appear verbatim in the input. If unsure, omit the number.\n"
    'Every bullet must cite at least one link from the input "news" list.\n'
    'Tone: dry, understated, smart. Humor only in "headline" and "quips"; never joke about people suffering from shortages, war victims or job losses. No drug jokes beyond the site\'s name. Keep it clean.\n'
    'Output ONLY valid JSON matching this schema: {"headline": str<=90, "what_changed": [{"text": str<=220, "sources": [url,...]}] (3-5 items), "quips": [str<=120] (0-2 items, no numbers)}.'
)

PROPOSALS_SYSTEM_PROMPT = (
    "You extract numeric updates for a hand-maintained whiteboard ledger about the world oil market.\n"
    "The input JSON lists the allowed targets (with their current values and units) and recent news items "
    "(title, snippet, link).\n"
    "Propose a new value for a target ONLY when the number is stated verbatim in that item's title or snippet. "
    '"quote" must be an exact, contiguous substring of the title or snippet and must contain the number. '
    '"source_url" must be that item\'s link. Never compute, convert, estimate or combine numbers. '
    "Prefer no proposal over a doubtful one; an empty list is fine.\n"
    'Output ONLY valid JSON: {"proposals": [{"target": str, "proposed_value": number, "unit": str, "quote": str, "source_url": str}]}'
)

#: Numeric leaves of manual.json that may be proposed (ARCHITECTURE §3.6) and their units.
STATIC_TARGETS = {
    "shipping.voyage_days_now": "days",
    "shipping.vlcc_day_rate_usd.now": "USD/day",
    "shipping.shipping_cost_per_bbl_usd.now": "USD/bbl",
    "world.production_mbd": "mb/d",
    "world.consumption_mbd": "mb/d",
}
TARGET_RE = re.compile(
    r"^(shipping\.voyage_days_now|shipping\.vlcc_day_rate_usd\.now|shipping\.shipping_cost_per_bbl_usd\.now"
    r"|hormuz_ledger\[[a-z0-9_]+\]\.delta|world\.production_mbd|world\.consumption_mbd"
    r"|refinery_shock\[\d+\]\.diesel_delta_mbd)$"
)

#: Series of prices.latest that go into the model input and the deltas.
PRICE_KEYS = (
    "brent", "wti", "ulsd_nyh", "gasoline_nyh", "jet_gulf", "heating_oil_nyh",
    "retail_diesel_us", "retail_gasoline_us",
    "diesel_crack", "gasoline_crack", "jet_crack", "crack_321", "brent_wti_spread",
)
BALANCE_KEYS = ("production", "consumption", "stock_draw")

REPLY_SCHEMA = {
    "type": "object",
    "required": ["headline", "what_changed"],
    "properties": {
        "headline": {"type": "string", "minLength": 1, "maxLength": MAX_HEADLINE},
        "what_changed": {
            "type": "array", "minItems": MIN_BULLETS, "maxItems": MAX_BULLETS,
            "items": {
                "type": "object", "required": ["text", "sources"],
                "properties": {
                    "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEXT},
                    "sources": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^https?://\\S+$"}},
                },
            },
        },
        "quips": {"type": "array", "maxItems": MAX_QUIPS, "items": {"type": "string", "maxLength": MAX_QUIP, "pattern": "^[^0-9]*$"}},
    },
}
_REPLY_VALIDATOR = Draft202012Validator(REPLY_SCHEMA)

PROPOSALS_REPLY_SCHEMA = {
    "type": "object",
    "required": ["proposals"],
    "properties": {"proposals": {"type": "array", "items": {"type": "object"}}},
}
_PROPOSALS_REPLY_VALIDATOR = Draft202012Validator(PROPOSALS_REPLY_SCHEMA)


class ValidationFailure(ValueError):
    """The model reply did not pass the validation chain."""


class EmptyReply(ValidationFailure):
    """The provider answered without any text (e.g. Gemini spent the output budget on thinking
    and hit MAX_TOKENS). Not a network failure: worth one retry with the same prompt."""


Completer = Callable[[str, str], str]


# ------------------------------------------------------------------------- number handling

_MAGNITUDE = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}
# A number: optional sign, optional currency, digits (with optional thousands groups and
# decimals), optional percent, optional magnitude word. The look-behinds only keep us from
# starting inside another number ("72.51" is one token, never 72 and 51; "2026-10-06" yields
# 2026, 10 and 6, never -10); a sign counts only when it does not follow a word character or a
# dot ("EU-27" → 27, "down -0.27" → -0.27). Letters and dots before a figure do not hide it:
# ".5%" (→ 0.5: a leading-dot decimal at a word boundary), "at...998.77", "approx.998" (→ 998),
# "x3", "1e6" are all seen (brief §8 step 3).
_NUM_RE = re.compile(
    r"(?<!\d)(?<!\d\.)"
    r"(?:(?<![\w.])(?P<sign>[-−+])\s?)?"
    r"(?:US\$|USD\s?|[$€£])?\s?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|(?<![\w.])\.\d+)"
    r"(?P<pct>\s?%)?"
    r"(?:\s*(?P<mag>million|billion|thousand|bn|mn|mm|k|m|b)\b(?!\s*/))?",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\d")
# ISO dates and timestamps ("2026-10", "2026-10-06", "2026-10-08T10:46:00Z", "… 10:46:00+02:00").
_ISO_DATE_RE = re.compile(
    r"(?<!\d)(\d{4})-\d{2}(?:-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?)?(?!\d)"
)
# "Oct 6", "October 6th", "6 Oct", "6th of October": the day of a month mention (capitalised
# month names only, so "may 5" in prose is still a figure).
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_MONTH_DAY_RE = re.compile(
    r"\b%s\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?\b|\b(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?%s\b" % (_MONTH, _MONTH)
)
_LETTER_RE = re.compile(r"[^\W\d_]")


def _collapse_dates(text: str) -> str:
    """Replace ISO dates and timestamps by their year. Day, month, hour, minute and second
    components are not figures (ARCHITECTURE §3.5 exempts years only), so neither the whitelist
    nor the check may see them: without this every ``published``/``as_of`` stamp in the input
    would whitelist most integers below 60 ("45 tankers", "8%")."""
    return _ISO_DATE_RE.sub(r"\1", text)


def _month_day_spans(text: str) -> set:
    """Character spans of day numbers that belong to a month mention ("Oct 6")."""
    spans = set()
    for m in _MONTH_DAY_RE.finditer(text):
        group = "d1" if m.group("d1") else "d2"
        if 1 <= int(m.group(group)) <= 31:
            spans.add(m.span(group))
    return spans


def _token_values(m: "re.Match[str]") -> Tuple[float, Optional[float]]:
    raw = float(m.group("num").replace(",", ""))
    if m.group("sign") in ("-", "−"):
        raw = -raw
    mag = m.group("mag")
    expanded = raw * _MAGNITUDE[mag.lower()] if mag else None
    return raw, expanded


def extract_numbers(text: str, *, strip_urls: bool = True) -> List[float]:
    """All numbers in ``text`` as floats. A magnitude word ("1.9 million", "$1.2m", "2bn")
    contributes both the raw value (1.9) and the expanded one (1900000). "mb/d" and "b/d" are
    units, not magnitudes ("2.94 mb/d" → 2.94). Thousands separators and %, $ are stripped.
    ISO dates/timestamps count only as their year ("2026-10-06" → 2026)."""
    if strip_urls:
        text = _URL_RE.sub(" ", text)
    text = _collapse_dates(text)
    out: List[float] = []
    for m in _NUM_RE.finditer(text):
        raw, expanded = _token_values(m)
        out.append(raw)
        if expanded is not None:
            out.append(expanded)
    return out


def normalise_number(text: str) -> Optional[float]:
    """The single number a short string denotes, magnitude applied: "$1.2m" → 1200000.0,
    "1,200,000" → 1200000.0, "2.94 mb/d" → 2.94, "1.9 million" → 1900000.0, "5.4%" → 5.4.
    ``None`` when the string holds no number."""
    m = _NUM_RE.search(text or "")
    if not m:
        return None
    raw, expanded = _token_values(m)
    return expanded if expanded is not None else raw


def _numbers_match(a: float, b: float, tol: float = NUMBER_TOLERANCE) -> bool:
    return abs(a - b) <= tol + 1e-9 * abs(b)


def _walk_numbers(node: Any, drop_keys: Iterable[str]) -> Iterable[float]:
    """Numbers from a JSON structure: numeric values as-is, strings via :func:`extract_numbers`."""
    drop = set(drop_keys)
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, str):
        for n in extract_numbers(node):
            yield n
    elif isinstance(node, dict):
        for k, v in node.items():
            if k in drop:
                continue
            for n in _walk_numbers(v, drop):
                yield n
    elif isinstance(node, (list, tuple)):
        for v in node:
            for n in _walk_numbers(v, drop):
                yield n


def numbers_in_input(input_doc: dict) -> List[float]:
    """The whitelist of numbers the model may use: every number anywhere in the input except
    inside links (Google News URLs are full of digits). Dates and timestamps (``published``,
    ``as_of``, ``generated_at`` …) contribute only their year, see :func:`_collapse_dates`."""
    seen: List[float] = []
    for n in _walk_numbers(input_doc, drop_keys=("link", "source_url", "quote_url", "id")):
        seen.append(n)
    return seen


def check_numbers(texts: Iterable[str], allowed: Iterable[float]) -> List[float]:
    """Numbers in ``texts`` that do not occur in ``allowed`` (±0.01). Empty list = guard passed.
    A token with a magnitude word passes if either its raw or its expanded value is allowed.
    ISO dates count as their year only, and the day of a month mention ("Oct 6", "6 October")
    is a date, not a figure."""
    allowed_list = list(allowed)
    bad: List[float] = []
    for text in texts:
        clean = _collapse_dates(_URL_RE.sub(" ", text or ""))
        day_spans = _month_day_spans(clean)
        for m in _NUM_RE.finditer(clean):
            if (m.span("num") in day_spans and not m.group("sign") and not m.group("pct")
                    and not m.group("mag") and "." not in m.group("num")):
                continue
            raw, expanded = _token_values(m)
            candidates = [raw] if expanded is None else [raw, expanded]
            if not any(_numbers_match(c, a) for c in candidates for a in allowed_list):
                bad.append(expanded if expanded is not None else raw)
    return bad


# ------------------------------------------------------------------------------ crack-o-meter

def crack_level(percentile: Optional[float], cfg: dict) -> Tuple[Optional[int], Optional[str]]:
    """Level 0–4 and label from the diesel-crack percentile via ``cfg["crack_levels"]``."""
    if percentile is None:
        return None, None
    levels = cfg.get("crack_levels") or {}
    cuts = levels.get("percentile_cuts") or [50, 75, 90, 98]
    labels = levels.get("labels") or ["Hairline", "Visible", "Widening (said with a straight face)", "Gaping", "Grand Canyon"]
    level = sum(1 for c in cuts if float(percentile) >= float(c))
    level = min(level, len(labels) - 1)
    return level, labels[level]


def crack_o_meter_from_prices(prices: Optional[dict], cfg: dict) -> dict:
    """``crack_o_meter`` block, copied by code from ``prices.stats.diesel_crack`` (never from the
    model). Falls back to recomputing the level from the percentile; all-null if no prices."""
    stats = ((prices or {}).get("stats") or {}).get("diesel_crack") or {}
    latest = ((prices or {}).get("latest") or {}).get("diesel_crack") or {}
    percentile = stats.get("percentile_now")
    level = stats.get("level")
    label = stats.get("level_label")
    if level is None or label is None:
        level, label = crack_level(percentile, cfg)
    return {
        "level": level,
        "label": label,
        "percentile": percentile,
        "value": latest.get("value"),
        "unit": latest.get("unit", "USD/bbl"),
        "as_of": latest.get("as_of") or (prices or {}).get("as_of"),
        "stale": bool(latest.get("stale", (prices or {}).get("stale", False))),
    }


# ------------------------------------------------------------------------------------ deltas

def _num(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _delta_entry(now: Optional[float], prev: Optional[float], as_of: Optional[str], prev_as_of: Optional[str], unit: str) -> dict:
    delta = round(now - prev, 3) if (now is not None and prev is not None) else None
    return {"now": now, "prev": prev, "delta": delta, "as_of": as_of, "prev_as_of": prev_as_of, "unit": unit}


def compute_deltas(context: Optional[dict], old: Optional[dict]) -> Dict[str, dict]:
    """Current values vs the previous run. ``prev`` comes from ``context["prices_old"]`` when
    present, otherwise from the previous summary's ``deltas[key]["now"]``; nothing to compare →
    ``prev=null``. Keys: every series in :data:`PRICE_KEYS` plus ``steo_<production|consumption|
    stock_draw>`` for the STEO current month."""
    context = context or {}
    old_deltas = (old or {}).get("deltas") if isinstance(old, dict) else None
    old_deltas = old_deltas if isinstance(old_deltas, dict) else {}
    prices_old = context.get("prices_old") if isinstance(context.get("prices_old"), dict) else None
    out: Dict[str, dict] = {}

    latest = ((context.get("prices") or {}).get("latest") or {})
    for key in PRICE_KEYS:
        dp = latest.get(key)
        if not isinstance(dp, dict):
            continue
        now = _num(dp.get("value"))
        as_of = dp.get("as_of")
        unit = dp.get("unit", "")
        prev = prev_as_of = None
        if prices_old is not None:
            odp = (prices_old.get("latest") or {}).get(key)
            if isinstance(odp, dict):
                prev, prev_as_of = _num(odp.get("value")), odp.get("as_of")
        elif isinstance(old_deltas.get(key), dict):
            prev, prev_as_of = _num(old_deltas[key].get("now")), old_deltas[key].get("as_of")
        out[key] = _delta_entry(now, prev, as_of, prev_as_of, unit)

    balance = context.get("balance") or {}
    current = balance.get("current") if isinstance(balance.get("current"), dict) else None
    if current:
        as_of = balance.get("steo_release") or balance.get("as_of")
        unit = balance.get("unit", "million barrels per day")
        for key in BALANCE_KEYS:
            k = "steo_" + key
            now = _num(current.get(key))
            prev = prev_as_of = None
            if isinstance(old_deltas.get(k), dict):
                prev, prev_as_of = _num(old_deltas[k].get("now")), old_deltas[k].get("as_of")
            entry = _delta_entry(now, prev, as_of, prev_as_of, unit)
            entry["period"] = current.get("period")
            out[k] = entry
    return out


# ------------------------------------------------------------------------------- input builder

def _news_items(context: Optional[dict], limit: int = MAX_NEWS_ITEMS) -> List[dict]:
    items = ((context or {}).get("news") or {}).get("items") or []
    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("link") or not it.get("title"):
            continue
        out.append({
            "title": str(it.get("title", "")),
            "source": str(it.get("source", "")),
            "link": str(it.get("link")),
            "published": it.get("published"),
            "snippet": str(it.get("snippet", "")),
        })
        if len(out) >= limit:
            break
    return out


def _serialise(doc: dict) -> str:
    return json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_input(context: Optional[dict], cfg: dict, now: Optional[datetime] = None,
                old: Optional[dict] = None) -> Tuple[dict, str]:
    """The compact JSON the model sees (as dict and as the exact string sent). Contains the site
    name, the latest prices with deltas, the crack-o-meter, the STEO balance and ≤ 30 news items
    (title, source, link, published, snippet). News is trimmed from the end until the serialised
    input is under :data:`MAX_INPUT_CHARS`."""
    context = context or {}
    now = now or common.now_utc()
    prices = context.get("prices") or {}
    balance = context.get("balance") or {}
    deltas = compute_deltas(context, old)

    price_block = {}
    for key in PRICE_KEYS:
        dp = (prices.get("latest") or {}).get(key)
        if not isinstance(dp, dict):
            continue
        d = deltas.get(key, {})
        price_block[key] = {
            "value": dp.get("value"), "unit": dp.get("unit"), "as_of": dp.get("as_of"),
            "prev": d.get("prev"), "prev_as_of": d.get("prev_as_of"), "delta": d.get("delta"),
            "stale": bool(dp.get("stale", False)),
        }
        if dp.get("formula"):
            price_block[key]["formula"] = dp["formula"]

    stats = (prices.get("stats") or {}).get("diesel_crack") or {}
    months = balance.get("months") or []
    current_period = balance.get("current_month")
    prev_month = None
    for i, m in enumerate(months):
        if m.get("period") == current_period and i > 0:
            prev_month = months[i - 1]
    balance_block = {
        "source": balance.get("source"), "steo_edition": balance.get("steo_edition"),
        "steo_release": balance.get("steo_release"), "unit": balance.get("unit"),
        "current_month": current_period, "current": balance.get("current"), "previous_month": prev_month,
        "quarters": (balance.get("quarters") or [])[-2:], "status": balance.get("status"),
        "quote": balance.get("quote"), "quote_url": balance.get("quote_url"),
        "deltas": {k[len("steo_"):]: v for k, v in deltas.items() if k.startswith("steo_")},
        "stale": bool(balance.get("stale", False)),
    } if balance else None

    doc = {
        "site": cfg.get("site_name", "Crackspread"),
        "generated_at": common.iso_utc(now),
        "prices": {"as_of": prices.get("as_of"), "source": prices.get("source"), "latest": price_block,
                   "diesel_crack_stats": {k: stats.get(k) for k in ("max", "max_date", "mean_2015_2019", "mean_prev_year", "prev_year", "percentile_now", "level", "level_label")}}
        if prices else None,
        "crack_o_meter": crack_o_meter_from_prices(prices or None, cfg),
        "balance": balance_block,
        "news": _news_items(context),
    }
    text = _serialise(doc)
    while len(text) > MAX_INPUT_CHARS and doc["news"]:
        doc["news"].pop()
        text = _serialise(doc)
    return doc, text


# ------------------------------------------------------------------------------- validation

def parse_reply(text: str) -> dict:
    """Parse the model reply as a JSON object; tolerates ```json fences and prose around the
    object. Raises :class:`ValidationFailure`."""
    if not isinstance(text, str) or not text.strip():
        raise ValidationFailure("empty reply")
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        obj = json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start < 0 or end <= start:
            raise ValidationFailure("reply is not valid JSON")
        try:
            obj = json.loads(t[start:end + 1])
        except ValueError as exc:
            raise ValidationFailure("reply is not valid JSON: %s" % _short(str(exc), 120))
    if not isinstance(obj, dict):
        raise ValidationFailure("reply is not a JSON object")
    return obj


def _clean_reply(reply: dict) -> dict:
    """Whitespace normalisation *before* the schema check: strip the headline, every bullet text,
    every source URL and every quip, drop quips that are empty afterwards. Only well-typed values
    are touched; anything else is left for the schema to report. Without this a ``text`` of
    ``"   "`` passes the reply schema (minLength 1) and only fails the document schema later."""
    out = dict(reply)
    if isinstance(out.get("headline"), str):
        out["headline"] = out["headline"].strip()
    if isinstance(out.get("what_changed"), list):
        bullets = []
        for bullet in out["what_changed"]:
            if isinstance(bullet, dict):
                bullet = dict(bullet)
                if isinstance(bullet.get("text"), str):
                    bullet["text"] = bullet["text"].strip()
                if isinstance(bullet.get("sources"), list):
                    bullet["sources"] = [u.strip() if isinstance(u, str) else u for u in bullet["sources"]]
            bullets.append(bullet)
        out["what_changed"] = bullets
    if isinstance(out.get("quips"), list):
        quips = [q.strip() if isinstance(q, str) else q for q in out["quips"]]
        out["quips"] = [q for q in quips if q != ""]
    return out


def validate_reply(reply: dict, input_doc: dict) -> dict:
    """Run the validation chain on a parsed reply; return the cleaned reply
    (``headline``, ``what_changed``, ``quips``) or raise :class:`ValidationFailure`."""
    if not isinstance(reply, dict):
        raise ValidationFailure("reply is not a JSON object")
    reply = dict(reply)
    if reply.get("quips") is None:
        reply["quips"] = []
    reply = _clean_reply(reply)
    # 1. schema (types, counts, lengths, quips without digits)
    err = best_match(_REPLY_VALIDATOR.iter_errors(reply))
    if err is not None:
        path = "/".join(str(p) for p in err.absolute_path) or "reply"
        raise ValidationFailure("schema: %s at %s" % (err.message, path))
    # 2. link whitelist
    allowed_links = {it["link"] for it in input_doc.get("news") or []}
    for i, bullet in enumerate(reply["what_changed"]):
        for url in bullet["sources"]:
            if url.strip() not in allowed_links:
                raise ValidationFailure("what_changed[%d] cites a link that is not in the input news: %s" % (i, url))
    # 3. number guard
    texts = [reply["headline"]] + [b["text"] for b in reply["what_changed"]]
    bad = check_numbers(texts, numbers_in_input(input_doc))
    if bad:
        shown = ", ".join("%g" % b for b in bad[:5])
        raise ValidationFailure("number guard: figure(s) not present in the input: %s" % shown)
    # 4. explicit length/count limits (belt and braces beyond the schema)
    if len(reply["headline"]) > MAX_HEADLINE:
        raise ValidationFailure("headline longer than %d chars" % MAX_HEADLINE)
    if not MIN_BULLETS <= len(reply["what_changed"]) <= MAX_BULLETS:
        raise ValidationFailure("what_changed must have %d-%d items" % (MIN_BULLETS, MAX_BULLETS))
    for b in reply["what_changed"]:
        if len(b["text"]) > MAX_TEXT:
            raise ValidationFailure("what_changed text longer than %d chars" % MAX_TEXT)
    if len(reply["quips"]) > MAX_QUIPS:
        raise ValidationFailure("more than %d quips" % MAX_QUIPS)
    # 5. quips without digits
    for q in reply["quips"]:
        if _DIGIT_RE.search(q):
            raise ValidationFailure("quip contains digits: %r" % q)
        if len(q) > MAX_QUIP:
            raise ValidationFailure("quip longer than %d chars" % MAX_QUIP)
    return {
        "headline": reply["headline"].strip(),
        "what_changed": [{"text": b["text"].strip(), "sources": [u.strip() for u in b["sources"]]} for b in reply["what_changed"]],
        "quips": [q.strip() for q in reply["quips"]],
    }


# ------------------------------------------------------------------------------ fallback

def _fmt(v: Optional[float]) -> str:
    return "%.2f" % v if v is not None else "n/a"


def rule_based_summary(input_doc: dict, deltas: Dict[str, dict]) -> dict:
    """Template summary from real data only: the diesel-crack delta in the headline and the top
    headlines (each with its own link) as bullets — plain titles, the frontend labels every cite
    link with its publisher. A stale diesel crack (fetch failed or FRED too old) is said so, never
    reported as "unchanged". Raises ``RuntimeError`` when fewer than :data:`MIN_BULLETS` headlines
    are available (brief §6.5: 3–5 bullets, else keep the old summary)."""
    dc = deltas.get("diesel_crack") or {}
    now, delta, as_of = dc.get("now"), dc.get("delta"), dc.get("as_of")
    latest = ((input_doc.get("prices") or {}).get("latest") or {})
    stale = bool((latest.get("diesel_crack") or {}).get("stale"))
    if now is not None and stale:
        headline = "Diesel crack: no fresh FRED data, last value $%s/bbl (as of %s)" % (_fmt(now), as_of)
    elif now is not None and delta is not None and abs(delta) < 0.005:
        headline = "Diesel crack unchanged since last update, $%s/bbl (as of %s)" % (_fmt(now), as_of)
    elif now is not None and delta is not None:
        headline = "Diesel crack %+.2f $/bbl since last update, now $%s/bbl (as of %s)" % (delta, _fmt(now), as_of)
    elif now is not None:
        headline = "Diesel crack at $%s/bbl (as of %s); no AI summary, raw headlines below" % (_fmt(now), as_of)
    else:
        headline = "No fresh price data; no AI summary, raw headlines below"
    headline = headline[:MAX_HEADLINE]

    items = [it for it in input_doc.get("news") or [] if it.get("title") and it.get("link")]
    picked: List[dict] = []
    skipped: List[dict] = []
    per_source: Dict[str, int] = {}
    for it in items:                       # newest first, at most two per publisher
        src = it.get("source") or ""
        if per_source.get(src, 0) >= 2:
            skipped.append(it)
            continue
        per_source[src] = per_source.get(src, 0) + 1
        picked.append(it)
        if len(picked) >= MAX_BULLETS:
            break
    for it in skipped:                     # fewer than three publishers: top up regardless
        if len(picked) >= MIN_BULLETS:
            break
        picked.append(it)
    if len(picked) < MIN_BULLETS:
        raise RuntimeError("rule-based fallback needs %d headlines, has %d" % (MIN_BULLETS, len(picked)))

    bullets: List[dict] = []
    for it in picked:
        text = str(it["title"]).strip()
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT - 1].rstrip() + "…"
        bullets.append({"text": text, "sources": [it["link"]]})
    return {"headline": headline, "what_changed": bullets, "quips": []}


# ------------------------------------------------------------------------------ providers

_SECRET_RE = re.compile(r"(?i)\b(api_key|apikey|key|token|secret)=([^&\s'\")]+)")


def _short(text: str, limit: int = 200) -> str:
    """Whitespace-collapsed, credential-masked, truncated text for logs and error messages."""
    text = " ".join(_SECRET_RE.sub(r"\1=***", text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _http_post(url: str, payload: dict, headers: dict, *, timeout: float = HTTP_TIMEOUT,
               session: Any = None, attempts: int = 2, backoff: float = 2.0) -> dict:
    """POST JSON through :func:`common.http_post` (UA, one retry on connection errors, timeouts,
    429 and 5xx, credential masking) and return the parsed JSON body; raises
    :class:`common.FetchError` otherwise. Keys travel in headers only, so the URL in any error
    text is safe to log."""
    resp = common.http_post(url, json=payload, headers=headers, timeout=timeout,
                            retries=attempts, backoff=backoff, session=session)
    try:
        return resp.json()
    except ValueError as exc:
        raise common.FetchError("non-JSON response from %s" % _short(url, 160)) from exc


def gemini_generation_config(model: str) -> dict:
    """``generationConfig`` for :func:`complete_gemini`. Gemini 3 models think by default and
    count the thought tokens against ``maxOutputTokens``, so the cap is generous and the thinking
    level is pinned low; Google advises against lowering the temperature on Gemini 3 ("may lead
    to … looping or degraded performance"), and JSON mode plus the validator constrain the output
    anyway. Legacy 2.x models know no ``thinkingLevel`` and keep the old low temperature."""
    config: Dict[str, Any] = {"responseMimeType": "application/json", "maxOutputTokens": MAX_OUTPUT_TOKENS}
    if model.lower().startswith("gemini-2"):
        config["temperature"] = 0.2
    else:
        config["thinkingConfig"] = {"thinkingLevel": GEMINI_THINKING_LEVEL}
    return config


def complete_gemini(system: str, user: str, *, model: str, api_key: str, session: Any = None,
                    timeout: float = HTTP_TIMEOUT) -> str:
    """Gemini REST ``generateContent`` in JSON response mode. Key via ``x-goog-api-key`` header.
    An answer without text (output budget exhausted, ``finishReason=MAX_TOKENS``) raises
    :class:`EmptyReply` so that :func:`_ask_with_retry` tries once more."""
    url = GEMINI_URL.format(model=model)
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": gemini_generation_config(model),
    }
    data = _http_post(url, payload, {"x-goog-api-key": api_key}, timeout=timeout, session=session)
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise common.FetchError("gemini blocked the prompt: %s" % feedback["blockReason"])
    candidates = data.get("candidates") or []
    if not candidates:
        raise common.FetchError("gemini returned no candidates")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text.strip():
        raise EmptyReply("gemini returned empty text (finishReason=%s)" % cand.get("finishReason"))
    return text


def complete_anthropic(system: str, user: str, *, model: str, api_key: str, session: Any = None,
                       timeout: float = HTTP_TIMEOUT) -> str:
    """Anthropic Messages API via plain HTTP (no SDK). No assistant prefill (removed on current
    models); the system prompt asks for JSON only and the validator does the rest."""
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION}
    data = _http_post(ANTHROPIC_URL, payload, headers, timeout=timeout, session=session)
    if data.get("type") == "error":
        raise common.FetchError("anthropic error: %s" % _short(json.dumps(data.get("error")), 300))
    stop = data.get("stop_reason")
    if stop == "refusal":
        raise common.FetchError("anthropic refused the request (stop_reason=refusal)")
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    if not text.strip():
        raise EmptyReply("anthropic returned no text (stop_reason=%s)" % stop)
    return text


def _fixture_completer(name: str) -> Completer:
    """Return the text of ``tests/fixtures/llm/<name>`` as the model reply (no network)."""
    safe = os.path.basename(str(name))
    path = LLM_FIXTURE_DIR / safe
    if not path.is_file():
        raise FileNotFoundError("LLM fixture not found: %s" % path)
    text = path.read_text(encoding="utf-8")

    def complete(system: str, user: str) -> str:
        return text

    return complete


def _default_model(provider: str) -> str:
    return DEFAULT_GEMINI_MODEL if provider == "gemini" else (DEFAULT_ANTHROPIC_MODEL if provider == "anthropic" else "")


def select_provider(cfg: dict, env: Optional[Mapping[str, str]], *, fixtures: bool = False,
                    session: Any = None, fixture_env: str = FIXTURE_ENV) -> Tuple[str, str, Optional[Completer], Optional[str]]:
    """Resolve ``(provider, model, complete, reason)``. ``complete`` is ``None`` for provider
    ``none``; ``reason`` says why no model will be called (``disabled``, ``no_key``,
    ``unknown_provider``, ``fixtures``, ``fixture_missing``)."""
    env = env if env is not None else os.environ
    provider = str(cfg.get("ai_provider") or "none").strip().lower()
    model = str(cfg.get("ai_model") or "").strip()
    if provider not in PROVIDERS:
        common.log("summary: unknown ai_provider %r → none" % provider)
        return "none", "", None, "unknown_provider"
    if provider == "none":
        return "none", "", None, "disabled"
    model = model or _default_model(provider)
    if fixtures:
        name = (env.get(fixture_env) or env.get(FIXTURE_ENV) or "").strip()
        if not name:
            return "none", "", None, "fixtures"
        try:
            return provider, model, _fixture_completer(name), None
        except OSError as exc:
            common.log("summary: %s → none" % exc)
            return "none", "", None, "fixture_missing"
    key = (env.get(KEY_ENV[provider]) or "").strip()
    if not key:
        common.log("summary: %s not set → provider none" % KEY_ENV[provider])
        return "none", "", None, "no_key"
    if provider == "gemini":
        def complete(system: str, user: str) -> str:
            return complete_gemini(system, user, model=model, api_key=key, session=session)
    else:
        def complete(system: str, user: str) -> str:
            return complete_anthropic(system, user, model=model, api_key=key, session=session)
    return provider, model, complete, None


# ------------------------------------------------------------------------------------- run

def _envelope(now: datetime, cfg: dict, source: str, as_of: Optional[str] = None) -> dict:
    """Common envelope. ``as_of`` is the data's reference time (the newest input ``as_of``), not
    the run stamp, so an unchanged input yields an unchanged document; it falls back to the run
    stamp only when no input carries an ``as_of``."""
    stamp = common.iso_utc(now)
    return {
        "schema_version": 1, "generated_at": stamp, "as_of": as_of or stamp, "fetched_at": stamp, "stale": False,
        "source": source, "source_url": cfg.get("repo_url") or cfg.get("site_url") or "https://github.com/fixoa/crackspread",
    }


def _input_as_of(context: Optional[dict], names: Iterable[str] = ("prices", "balance", "news")) -> Optional[str]:
    """The newest ``as_of`` among the context documents the summary is built from (ISO strings
    compare lexicographically; a timestamp of a day sorts after that day's date)."""
    stamps = []
    for name in names:
        doc = (context or {}).get(name)
        value = doc.get("as_of") if isinstance(doc, dict) else None
        if isinstance(value, str) and value:
            stamps.append(value)
    return max(stamps) if stamps else None


def _ask_with_retry(complete: Completer, system: str, user: str, validator: Callable[[str], Any],
                    label: str = "summary") -> Tuple[Any, int, Optional[str]]:
    """Call the model, validate; on failure retry once with the error appended (an empty reply is
    re-asked with the unchanged prompt). Returns ``(result, attempts, None)`` or
    ``(None, attempts, last_error)``."""
    message = user
    last_error: Optional[str] = None
    for attempt in (1, 2):
        try:
            reply = complete(system, message)
            return validator(reply), attempt, None
        except (ValidationFailure, common.FetchError, ValueError, KeyError, TypeError) as exc:
            last_error = "%s: %s" % (type(exc).__name__, _short(str(exc), 300))
            common.log("%s: attempt %d rejected: %s" % (label, attempt, last_error))
            if isinstance(exc, common.FetchError):
                break  # network/provider failure: no point re-sending the same prompt
            if isinstance(exc, EmptyReply):
                message = user  # nothing to correct: the model produced no text at all
            else:
                message = (user + "\n\nYour previous reply was rejected by the validator: " + str(exc)
                           + "\nReply again with ONLY valid JSON that fixes this. Use only numbers and links from the input.")
    return None, attempt, last_error


def _summary_doc(now: datetime, cfg: dict, as_of: Optional[str], source: str, provider: str, model: str,
                 fallback: bool, body: dict, crack: dict, deltas: Dict[str, dict], digest: str) -> dict:
    doc = _envelope(now, cfg, source, as_of)
    doc.update({
        "provider": provider, "model": model, "fallback": bool(fallback),
        "headline": body["headline"], "what_changed": body["what_changed"], "quips": body["quips"],
        "crack_o_meter": crack, "deltas": deltas, "inputs_digest": digest,
    })
    return doc


def run(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None,
        session: Any = None, env: Optional[Mapping[str, str]] = None,
        context: Optional[dict] = None) -> Tuple[dict, dict]:
    """Build summary.json (see module docstring). Returns ``(doc, info)``; raises only when even
    the rule-based fallback has no data."""
    now = now or common.now_utc()
    context = context or {}
    input_doc, input_text = build_input(context, cfg, now, old)
    deltas = compute_deltas(context, old)
    # The digest ignores the run stamp so that an identical input gives an identical document
    # (brief §3/§7: commits only on real changes); everything else of the exact input is hashed.
    digest = common.sha1(_serialise(common.strip_keys(input_doc, ("generated_at",))))
    as_of = _input_as_of(context)
    crack = crack_o_meter_from_prices(context.get("prices"), cfg)

    provider, model, complete, reason = select_provider(cfg, env, fixtures=fixtures, session=session)
    info: Dict[str, Any] = {"provider": provider, "model": model, "fallback": True, "attempts": 0, "reason": reason}
    body: Optional[dict] = None

    if complete is not None:
        if not input_doc["news"]:
            common.log("summary: no news items in the input → rule-based fallback")
            info["reason"] = "no_news"
        else:
            def _validate(text: str) -> dict:
                return validate_reply(parse_reply(text), input_doc)

            try:
                body, attempts, error = _ask_with_retry(complete, SYSTEM_PROMPT, input_text, _validate)
            except Exception as exc:  # noqa: BLE001  (any provider failure downgrades to none)
                body, attempts, error = None, 1, "%s: %s" % (type(exc).__name__, _short(str(exc), 300))
                common.log("summary: provider %s failed: %s" % (provider, error))
            info["attempts"] = attempts
            if body is None:
                info["reason"] = "rejected: %s" % error
                common.log("summary: %s reply rejected after %d attempt(s) → rule-based fallback" % (provider, attempts))

    if body is not None:
        info["fallback"] = False
        source = "Crackspread summarizer (%s %s)" % (provider, model)
        doc = _summary_doc(now, cfg, as_of, source, provider, model, False, body, crack, deltas, digest)
        try:
            common.validate(doc, SCHEMA)
        except ValidationError as exc:
            # Model content that slipped past validate_reply but not the document schema counts
            # as a rejected reply: fall back instead of letting update.py keep the old file.
            info["reason"] = "rejected: schema: %s" % _short(exc.message, 200)
            common.log("summary: %s reply fails the document schema (%s) → rule-based fallback" % (provider, info["reason"]))
            body = None
    if body is None:
        body = rule_based_summary(input_doc, deltas)
        provider, model = "none", ""
        info["provider"], info["model"], info["fallback"] = provider, model, True
        source = "Crackspread rule-based summary (no AI)"
        doc = _summary_doc(now, cfg, as_of, source, provider, model, True, body, crack, deltas, digest)
        common.validate(doc, SCHEMA)
    common.log("summary: provider=%s model=%s fallback=%s bullets=%d" % (provider, model or "-", doc["fallback"], len(doc["what_changed"])))
    return doc, info


# -------------------------------------------------------------------------------- proposals

def _manual_doc(context: Optional[dict]) -> Optional[dict]:
    manual = (context or {}).get("manual")
    if isinstance(manual, dict):
        return manual
    return common.read_json(common.DATA / "manual.json")


def allowed_targets(manual: Optional[dict]) -> Dict[str, dict]:
    """``{target: {"unit", "label", "current_value"}}`` for every numeric leaf of manual.json
    that may be proposed. Without a manual document the static targets are returned."""
    out: Dict[str, dict] = {}
    manual = manual or {}

    def leaf(path: Tuple[str, ...]) -> Any:
        node: Any = manual
        for p in path:
            node = node.get(p) if isinstance(node, dict) else None
        return node

    out["shipping.voyage_days_now"] = {"unit": "days", "label": "Voyage days Gulf → Asia now", "current_value": leaf(("shipping", "voyage_days_now"))}
    out["shipping.vlcc_day_rate_usd.now"] = {"unit": "USD/day", "label": "VLCC day rate now", "current_value": leaf(("shipping", "vlcc_day_rate_usd", "now"))}
    out["shipping.shipping_cost_per_bbl_usd.now"] = {"unit": "USD/bbl", "label": "Shipping cost per barrel now", "current_value": leaf(("shipping", "shipping_cost_per_bbl_usd", "now"))}
    out["world.production_mbd"] = {"unit": "mb/d", "label": "World production", "current_value": leaf(("world", "production_mbd"))}
    out["world.consumption_mbd"] = {"unit": "mb/d", "label": "World consumption", "current_value": leaf(("world", "consumption_mbd"))}
    for row in manual.get("hormuz_ledger") or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str) and re.match(r"^[a-z0-9_]+$", row["id"]) and "delta" in row:
            out["hormuz_ledger[%s].delta" % row["id"]] = {"unit": "mb/d", "label": row.get("label", row["id"]), "current_value": row.get("delta")}
    for i, row in enumerate(manual.get("refinery_shock") or []):
        if isinstance(row, dict) and "diesel_delta_mbd" in row:
            out["refinery_shock[%d].diesel_delta_mbd" % i] = {"unit": "mb/d", "label": row.get("label", "refinery shock %d" % i), "current_value": row.get("diesel_delta_mbd")}
    return out


def build_proposals_input(context: Optional[dict], manual: Optional[dict]) -> Tuple[dict, str]:
    targets = allowed_targets(manual)
    doc = {
        "targets": [{"target": t, **meta} for t, meta in targets.items()],
        "news": _news_items(context),
    }
    text = _serialise(doc)
    while len(text) > MAX_INPUT_CHARS and doc["news"]:
        doc["news"].pop()
        text = _serialise(doc)
    return doc, text


def _quote_values(quote: str, unit: str) -> List[float]:
    """The one value each number token of a quote stands for, given the target's unit: for
    USD/day targets a magnitude word is expanded ("$1 million a day" → 1000000, never 1); for
    mb/d, days and USD/bbl targets the raw figure counts ("16.5 million barrels per day" → 16.5,
    never 16500000). One canonical value per token, so a wrong magnitude cannot slip through."""
    out: List[float] = []
    for m in _NUM_RE.finditer(_collapse_dates(quote)):
        raw, expanded = _token_values(m)
        out.append(expanded if (unit == "USD/day" and expanded is not None) else raw)
    return out


def guard_proposals(raw: Any, input_doc: dict, targets: Mapping[str, dict],
                    items: Optional[Iterable[dict]] = None) -> List[dict]:
    """Strict code guard (ARCHITECTURE §3.6): keep a proposal only if the target is allowed, the
    URL is a news link from the input, the quote is a verbatim substring of that item's title or
    snippet that contains at least one letter, and the proposed number equals the quote's figure
    in the target's unit (see :func:`_quote_values`). Everything else is dropped.
    ``items`` (default: the input's news list) supplies title/snippet/published/feed."""
    if isinstance(raw, dict):
        raw = raw.get("proposals")
    if not isinstance(raw, list):
        return []
    allowed_links = {it["link"] for it in input_doc.get("news") or []}
    by_link = {it["link"]: it for it in (items if items is not None else input_doc.get("news") or [])
               if isinstance(it, dict) and it.get("link") in allowed_links}
    kept: List[dict] = []
    seen = set()
    for p in raw:
        if not isinstance(p, dict):
            continue
        target = p.get("target")
        url = str(p.get("source_url") or "").strip()
        quote = p.get("quote")
        value = _num(p.get("proposed_value"))
        if not isinstance(target, str) or not TARGET_RE.match(target) or target not in targets:
            common.log("proposals: dropped (target not allowed): %r" % (target,))
            continue
        if value is None:
            common.log("proposals: dropped (no numeric value) for %s" % target)
            continue
        item = by_link.get(url)
        if item is None:
            common.log("proposals: dropped (link not in news) for %s" % target)
            continue
        if not isinstance(quote, str) or not quote.strip():
            common.log("proposals: dropped (empty quote) for %s" % target)
            continue
        quote = quote.strip()
        if quote not in item["title"] and quote not in item["snippet"]:
            common.log("proposals: dropped (quote not verbatim) for %s" % target)
            continue
        if not _LETTER_RE.search(quote):
            common.log("proposals: dropped (quote is a bare number) for %s" % target)
            continue
        found = _quote_values(quote, targets[target].get("unit") or "")
        if not any(_numbers_match(value, n) or _numbers_match(abs(value), n) for n in found):
            common.log("proposals: dropped (number %s not in quote) for %s" % (value, target))
            continue
        key = (target, value, url)
        if key in seen:
            continue
        seen.add(key)
        kept.append({
            "target": target,
            "proposed_value": int(value) if float(value).is_integer() else value,
            "unit": targets[target].get("unit") or str(p.get("unit") or ""),
            "quote": quote,
            "source_title": item["title"],
            "source_url": url,
            "published": item.get("published"),
            "feed": str(item.get("feed") or ""),
        })
    return kept


def run_proposals(cfg: dict, old: Optional[dict], *, fixtures: bool = False, now: Optional[datetime] = None,
                  session: Any = None, env: Optional[Mapping[str, str]] = None,
                  context: Optional[dict] = None) -> Tuple[dict, dict]:
    """Second model call (only with a provider and key) asking for ledger proposals; the code
    guard keeps only verbatim-quoted numbers. An empty list is a perfectly fine result."""
    now = now or common.now_utc()
    context = context or {}
    manual = _manual_doc(context)
    targets = allowed_targets(manual)
    input_doc, input_text = build_proposals_input(context, manual)
    env = env if env is not None else os.environ
    fixture_env = PROPOSALS_FIXTURE_ENV if env.get(PROPOSALS_FIXTURE_ENV) else FIXTURE_ENV
    provider, model, complete, reason = select_provider(cfg, env, fixtures=fixtures, session=session, fixture_env=fixture_env)
    info: Dict[str, Any] = {"provider": provider, "model": model, "count": 0, "attempts": 0, "reason": reason}
    proposals: List[dict] = []

    if complete is not None and input_doc["news"]:
        def _validate(text: str) -> List[dict]:
            obj = parse_reply(text)
            err = best_match(_PROPOSALS_REPLY_VALIDATOR.iter_errors(obj))
            if err is not None:
                raise ValidationFailure("schema: %s" % err.message)
            return guard_proposals(obj, input_doc, targets, items=((context.get("news") or {}).get("items") or []))

        try:
            result, attempts, error = _ask_with_retry(complete, PROPOSALS_SYSTEM_PROMPT, input_text, _validate, label="proposals")
        except Exception as exc:  # noqa: BLE001
            result, attempts, error = None, 1, "%s: %s" % (type(exc).__name__, _short(str(exc), 300))
            common.log("proposals: provider %s failed: %s" % (provider, error))
        info["attempts"] = attempts
        if result is None:
            info["reason"] = "rejected: %s" % error
        else:
            proposals = result
    elif complete is not None:
        info["reason"] = "no_news"

    if complete is None:
        provider, model = "none", ""
    doc = _envelope(now, cfg, "Crackspread proposals (%s)" % (provider if provider != "none" else "no AI"),
                    _input_as_of(context, ("news",)))
    doc.update({"provider": provider, "model": model, "proposals": proposals, "inputs_digest": common.sha1(input_text)})
    info["count"] = len(proposals)
    common.validate(doc, PROPOSALS_SCHEMA)
    common.log("proposals: provider=%s count=%d" % (provider, len(proposals)))
    return doc, info
