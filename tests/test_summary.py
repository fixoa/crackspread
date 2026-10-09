"""Tests for scripts/summarize.py (ARCHITECTURE §3.5/§3.6/§7, brief §8/§15).

All offline: the model reply comes from tests/fixtures/llm/* via CRACKSPREAD_LLM_FIXTURE and
the run context from tests/fixtures/llm/input_example.json (real numbers from the raw FRED/EIA
downloads of 2026-10-08, real headlines/links from the saved RSS feeds). The autouse
``no_network`` fixture in conftest.py makes any network attempt fail loudly.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import jsonschema
import pytest

import common
import summarize

LLM = common.FIXTURES / "llm"
#: The real function, captured at import time (the autouse no_network guard replaces the module
#: attribute); provider tests drive it with a stub session.
_REAL_HTTP_POST = common.http_post


# ------------------------------------------------------------------------------ fixtures

@pytest.fixture
def ctx() -> dict:
    """Run context {prices, prices_old, balance, news} built from real raw data."""
    with open(LLM / "input_example.json", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def ai_cfg(cfg: dict) -> dict:
    cfg = dict(cfg)
    cfg["ai_provider"] = "gemini"
    cfg["ai_model"] = ""
    return cfg


def _env(fixture_name: str) -> dict:
    return {summarize.FIXTURE_ENV: fixture_name}


def _run(cfg, ctx, now, fixture_name=None, old=None, **kw):
    env = _env(fixture_name) if fixture_name else {}
    return summarize.run(cfg, old, fixtures=True, now=now, env=env, context=ctx, **kw)


def _news_links(ctx) -> set:
    return {it["link"] for it in ctx["news"]["items"]}


# ------------------------------------------------------------------------- valid reply

def test_valid_reply_accepted_and_schema_valid(ai_cfg, ctx, now):
    doc, info = _run(ai_cfg, ctx, now, "valid.json")
    common.validate(doc, "summary")
    expected = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    assert doc["provider"] == "gemini"
    assert doc["model"] == summarize.DEFAULT_GEMINI_MODEL
    assert doc["fallback"] is False
    assert info["fallback"] is False and info["provider"] == "gemini" and info["attempts"] == 1
    assert doc["headline"] == expected["headline"]
    assert [b["text"] for b in doc["what_changed"]] == [b["text"] for b in expected["what_changed"]]
    assert doc["quips"] == expected["quips"]
    assert 3 <= len(doc["what_changed"]) <= 5
    for bullet in doc["what_changed"]:
        assert bullet["sources"] and set(bullet["sources"]) <= _news_links(ctx)
    assert doc["generated_at"] == common.iso_utc(now)
    # as_of is the data's reference time (newest input as_of), not the run stamp
    assert doc["as_of"] == max(ctx["prices"]["as_of"], ctx["balance"]["as_of"], ctx["news"]["as_of"]) == ctx["news"]["as_of"]
    assert re.fullmatch(r"[0-9a-f]{40}", doc["inputs_digest"])


def test_configured_model_is_used(ai_cfg, ctx, now):
    ai_cfg["ai_model"] = "gemini-test-model"
    doc, _ = _run(ai_cfg, ctx, now, "valid.json")
    assert doc["model"] == "gemini-test-model"


def test_anthropic_default_model(ai_cfg, ctx, now):
    ai_cfg["ai_provider"] = "anthropic"
    doc, _ = _run(ai_cfg, ctx, now, "valid.json")
    assert doc["provider"] == "anthropic"
    # current Haiku per platform.claude.com/docs/en/about-claude/model-deprecations (2026-10-08);
    # claude-haiku-4-5 may retire on 2026-10-15
    assert doc["model"] == summarize.DEFAULT_ANTHROPIC_MODEL == "claude-haiku-5-5"


# -------------------------------------------------------------------- rejected replies

def test_invented_number_rejected_then_fallback(ai_cfg, ctx, now):
    doc, info = _run(ai_cfg, ctx, now, "invalid_number.json")
    common.validate(doc, "summary")
    assert doc["fallback"] is True
    assert doc["provider"] == "none" and doc["model"] == ""
    assert info["provider"] == "none" and info["fallback"] is True
    assert info["attempts"] == 2  # one retry with the error text, then fallback
    assert "number guard" in info["reason"]
    assert "112" in info["reason"]


def test_foreign_link_rejected(ai_cfg, ctx, now):
    doc, info = _run(ai_cfg, ctx, now, "invalid_link.json")
    assert doc["fallback"] is True and doc["provider"] == "none"
    assert "link" in info["reason"]
    for bullet in doc["what_changed"]:
        assert set(bullet["sources"]) <= _news_links(ctx)


def test_broken_json_falls_back(ai_cfg, ctx, now):
    doc, info = _run(ai_cfg, ctx, now, "broken.txt")
    common.validate(doc, "summary")
    assert doc["fallback"] is True and doc["provider"] == "none"
    assert "JSON" in info["reason"]


def test_quip_with_digits_rejected(ai_cfg, ctx, now, tmp_path, monkeypatch):
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    reply["quips"] = ["Three charts walk into a bar; 2 of them are widening."]
    (tmp_path / "quip_digits.json").write_text(json.dumps(reply), encoding="utf-8")
    monkeypatch.setattr(summarize, "LLM_FIXTURE_DIR", tmp_path)
    doc, info = _run(ai_cfg, ctx, now, "quip_digits.json")
    assert doc["fallback"] is True and doc["provider"] == "none"
    assert "quip" in info["reason"].lower() or "digit" in info["reason"].lower() or "pattern" in info["reason"].lower()
    # the validator itself names the problem
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    with pytest.raises(summarize.ValidationFailure):
        summarize.validate_reply(reply, input_doc)


def test_too_few_or_too_many_bullets_rejected(ai_cfg, ctx, now):
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    two = copy.deepcopy(reply)
    two["what_changed"] = two["what_changed"][:2]
    with pytest.raises(summarize.ValidationFailure):
        summarize.validate_reply(two, input_doc)
    six = copy.deepcopy(reply)
    six["what_changed"] = six["what_changed"] + six["what_changed"][:1]
    with pytest.raises(summarize.ValidationFailure):
        summarize.validate_reply(six, input_doc)
    long_head = copy.deepcopy(reply)
    long_head["headline"] = "x" * 91
    with pytest.raises(summarize.ValidationFailure):
        summarize.validate_reply(long_head, input_doc)
    no_source = copy.deepcopy(reply)
    no_source["what_changed"][0]["sources"] = []
    with pytest.raises(summarize.ValidationFailure):
        summarize.validate_reply(no_source, input_doc)


def test_rounded_number_within_tolerance_passes(ai_cfg, ctx, now):
    """72.51 for an input value of 72.506 is fine (±0.01); 72.6 is not."""
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    assert "72.51" in reply["what_changed"][4]["text"]
    summarize.validate_reply(reply, input_doc)
    bad = copy.deepcopy(reply)
    bad["what_changed"][4]["text"] = bad["what_changed"][4]["text"].replace("72.51", "72.6")
    with pytest.raises(summarize.ValidationFailure, match="number guard"):
        summarize.validate_reply(bad, input_doc)


# ------------------------------------------------------------------- crack-o-meter / deltas

def test_crack_o_meter_comes_from_code_not_model(ai_cfg, ctx, now):
    """valid.json claims level 0 "Hairline"; the doc must carry prices.stats (level 3)."""
    expected = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    assert expected["crack_o_meter"]["level"] == 0
    stats = ctx["prices"]["stats"]["diesel_crack"]
    for fixture in ("valid.json", "invalid_number.json", None):
        doc, _ = _run(ai_cfg, ctx, now, fixture)
        com = doc["crack_o_meter"]
        assert com["level"] == stats["level"] == 3
        assert com["label"] == stats["level_label"]
        assert com["percentile"] == stats["percentile_now"]
        assert com["value"] == ctx["prices"]["latest"]["diesel_crack"]["value"]
        assert com["as_of"] == ctx["prices"]["latest"]["diesel_crack"]["as_of"]


def test_crack_level_from_percentile(cfg):
    assert summarize.crack_level(10, cfg) == (0, "Hairline")
    assert summarize.crack_level(50, cfg) == (1, "Visible")
    assert summarize.crack_level(75, cfg)[0] == 2
    assert summarize.crack_level(97.92, cfg) == (3, "Gaping")
    assert summarize.crack_level(98, cfg) == (4, "Grand Canyon")
    assert summarize.crack_level(None, cfg) == (None, None)


def test_deltas_from_prices_old(ai_cfg, ctx, now):
    doc, _ = _run(ai_cfg, ctx, now, "valid.json")
    d = doc["deltas"]["diesel_crack"]
    assert d["now"] == 72.506 and d["prev"] == 68.866
    assert d["delta"] == pytest.approx(3.64, abs=1e-9)
    assert d["as_of"] == "2026-10-06" and d["prev_as_of"] == "2026-10-05"
    assert doc["deltas"]["brent"]["delta"] == pytest.approx(125.44 - 125.51, abs=1e-6)
    assert doc["deltas"]["steo_stock_draw"]["now"] == 0.67
    assert doc["deltas"]["steo_stock_draw"]["prev"] is None  # nothing to compare yet


def test_deltas_from_previous_summary_when_no_prices_old(ai_cfg, ctx, now):
    ctx = dict(ctx)
    ctx.pop("prices_old")
    old = {"deltas": {"diesel_crack": {"now": 70.1, "as_of": "2026-10-02"},
                      "steo_stock_draw": {"now": 2.94, "as_of": "2026-09-09"}}}
    doc, _ = _run(ai_cfg, ctx, now, "valid.json", old=old)
    assert doc["deltas"]["diesel_crack"]["prev"] == 70.1
    assert doc["deltas"]["diesel_crack"]["prev_as_of"] == "2026-10-02"
    assert doc["deltas"]["diesel_crack"]["delta"] == pytest.approx(72.506 - 70.1, abs=1e-6)
    assert doc["deltas"]["steo_stock_draw"]["prev"] == 2.94
    assert doc["deltas"]["steo_stock_draw"]["delta"] == pytest.approx(0.67 - 2.94, abs=1e-6)


def test_fallback_headline_says_unchanged_for_zero_delta(cfg, ctx, now):
    """Second run on identical data (update.py gives no prices_old): prev comes from the previous
    summary and the delta is 0 → wording 'unchanged', still only input numbers."""
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    ctx = dict(ctx)
    ctx.pop("prices_old")
    first, _ = summarize.run(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    second, _ = summarize.run(cfg, first, fixtures=True, now=now, env={}, context=ctx)
    assert second["deltas"]["diesel_crack"]["prev"] == 72.506 and second["deltas"]["diesel_crack"]["delta"] == 0.0
    assert "unchanged" in second["headline"] and "+0.00" not in second["headline"]
    input_doc, _ = summarize.build_input(ctx, cfg, now, old=first)
    summarize.validate_reply({"headline": second["headline"], "what_changed": second["what_changed"], "quips": []}, input_doc)


def test_deltas_prev_null_when_nothing_to_compare(ai_cfg, ctx, now):
    ctx = dict(ctx)
    ctx.pop("prices_old")
    doc, _ = _run(ai_cfg, ctx, now, None, old=None)
    for key, d in doc["deltas"].items():
        assert d["prev"] is None and d["delta"] is None, key
        assert d["now"] is not None


# ------------------------------------------------------------------------- input builder

def test_input_is_compact_and_bounded(ai_cfg, ctx, now):
    big = copy.deepcopy(ctx)
    items = big["news"]["items"]
    big["news"]["items"] = [dict(it, link=it["link"] + "&n=%d" % i, title="%s %d" % (it["title"], i))
                            for i in range(60) for it in items[:1]] + items
    input_doc, text = summarize.build_input(big, ai_cfg, now)
    assert len(input_doc["news"]) <= 30
    assert len(text) <= summarize.MAX_INPUT_CHARS
    assert len(text) / 4 < 8000  # rough token estimate
    assert set(input_doc["news"][0]) == {"title", "source", "link", "published", "snippet"}
    assert json.loads(text) == input_doc
    assert input_doc["site"] == ai_cfg["site_name"]
    assert input_doc["prices"]["latest"]["diesel_crack"]["delta"] == pytest.approx(3.64)
    assert input_doc["balance"]["current"]["stock_draw"] == 0.67
    assert "1.9 million b/d" in input_doc["balance"]["quote"]


def test_system_prompt_is_the_brief_text():
    assert summarize.SYSTEM_PROMPT.startswith('You write the "What changed today" box for Crackspread')
    assert "Every numeric figure you write must appear verbatim in the input." in summarize.SYSTEM_PROMPT
    assert summarize.SYSTEM_PROMPT.endswith('"quips": [str<=120] (0-2 items, no numbers)}.')
    assert "\n" in summarize.SYSTEM_PROMPT and summarize.SYSTEM_PROMPT.count("\n") == 5


# ----------------------------------------------------------------------- rule-based fallback

def test_fallback_cites_only_input_links_and_numbers(cfg, ctx, now):
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"  # what --no-ai passes in
    doc, info = summarize.run(cfg, None, fixtures=False, now=now, env={}, context=ctx)
    common.validate(doc, "summary")
    assert doc["provider"] == "none" and doc["fallback"] is True
    assert info["reason"] == "disabled"
    assert 3 <= len(doc["what_changed"]) <= 5
    links = _news_links(ctx)
    for bullet in doc["what_changed"]:
        assert bullet["sources"] and set(bullet["sources"]) <= links
    assert doc["quips"] == []
    assert "+3.64" in doc["headline"]
    # every number in the fallback text occurs in the input (same guard as for the model)
    input_doc, text = summarize.build_input(ctx, cfg, now)
    summarize.validate_reply({"headline": doc["headline"], "what_changed": doc["what_changed"], "quips": []}, input_doc)
    # and, independently of the module's own regex: each decimal figure appears literally in the input text
    for figure in re.findall(r"\d+\.\d+", doc["headline"]):
        assert figure in text or any(abs(float(figure) - n) <= 0.01 for n in summarize.numbers_in_input(input_doc))


def test_no_key_downgrades_to_none_without_network(ai_cfg, ctx, now):
    doc, info = summarize.run(ai_cfg, None, fixtures=False, now=now, env={}, context=ctx)
    assert doc["provider"] == "none" and doc["fallback"] is True
    assert info["reason"] == "no_key"


def test_provider_exception_downgrades_to_fallback(ai_cfg, ctx, now, monkeypatch):
    def boom(system, user):
        raise RuntimeError("socket exploded")

    monkeypatch.setattr(summarize, "select_provider", lambda *a, **k: ("gemini", "m", boom, None))
    doc, info = summarize.run(ai_cfg, None, fixtures=False, now=now, env={}, context=ctx)
    assert doc["provider"] == "none" and doc["fallback"] is True
    assert "RuntimeError" in info["reason"]


def test_retry_once_with_error_then_accept(ai_cfg, ctx, now, monkeypatch):
    calls = []
    good = (LLM / "valid.json").read_text(encoding="utf-8")

    def flaky(system, user):
        calls.append(user)
        return "not json at all" if len(calls) == 1 else good

    monkeypatch.setattr(summarize, "select_provider", lambda *a, **k: ("gemini", "m", flaky, None))
    doc, info = summarize.run(ai_cfg, None, fixtures=False, now=now, env={}, context=ctx)
    assert doc["fallback"] is False and info["attempts"] == 2
    assert "rejected by the validator" in calls[1] and calls[1].startswith(calls[0])


def test_fallback_without_any_data_raises(cfg, now):
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    with pytest.raises(RuntimeError):
        summarize.run(cfg, None, fixtures=True, now=now, env={}, context={})


def test_fallback_needs_three_headlines(cfg, ctx, now):
    """Brief §6.5: 3–5 bullets. With fewer usable headlines the fallback raises so that update.py
    keeps the previous summary (stale) instead of writing a thin one; with three items from a
    single publisher the per-publisher cap is lifted to reach three."""
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    items = ctx["news"]["items"]
    with pytest.raises(RuntimeError, match="needs 3 headlines"):
        summarize.run(cfg, None, fixtures=True, now=now, env={}, context={"prices": ctx["prices"], "news": {"items": items[:2]}})
    same_publisher = [dict(it, source="One Wire") for it in items[:3]]
    doc, _ = summarize.run(cfg, None, fixtures=True, now=now, env={}, context={"prices": ctx["prices"], "news": {"items": same_publisher}})
    common.validate(doc, "summary")
    assert len(doc["what_changed"]) == 3
    with pytest.raises(jsonschema.ValidationError):     # the schema pins the minimum too
        common.validate(dict(doc, what_changed=doc["what_changed"][:2]), "summary")


def test_fallback_bullets_are_plain_titles(cfg, ctx, now):
    """The frontend labels every cite link with the publisher, so the text carries no '(Publisher)'."""
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    doc, _ = summarize.run(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    titles = {it["title"].strip() for it in ctx["news"]["items"]}
    for bullet in doc["what_changed"]:
        assert bullet["text"] in titles
    assert "$72.51/bbl (as of 2026-10-06)" in doc["headline"]
    assert doc["headline"].startswith("Diesel crack +3.64 $/bbl since last update")


def test_fallback_headline_names_stale_prices(cfg, ctx, now):
    """A carried-forward (stale) diesel crack is reported as such, never as 'unchanged'."""
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    ctx = copy.deepcopy(ctx)
    ctx.pop("prices_old")
    first, _ = summarize.run(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    common.mark_all_stale(ctx["prices"])                       # what update.py does when FRED fails
    second, _ = summarize.run(cfg, first, fixtures=True, now=now, env={}, context=ctx)
    assert second["deltas"]["diesel_crack"]["delta"] == 0.0
    assert "unchanged" not in second["headline"]
    assert second["headline"].startswith("Diesel crack: no fresh FRED data, last value $72.51/bbl (as of 2026-10-06)")
    input_doc, _ = summarize.build_input(ctx, cfg, now, old=first)
    summarize.validate_reply({"headline": second["headline"], "what_changed": second["what_changed"], "quips": []}, input_doc)


def test_summary_without_prices_still_validates(ai_cfg, ctx, now):
    ctx = {"news": ctx["news"], "balance": ctx["balance"]}
    doc, _ = _run(ai_cfg, ctx, now, None)
    common.validate(doc, "summary")
    assert doc["crack_o_meter"]["level"] is None and doc["crack_o_meter"]["label"] is None
    assert "No fresh price data" in doc["headline"]


# ---------------------------------------------------------------------- number normalisation

@pytest.mark.parametrize("text,expected", [
    ("$1.2m", 1200000.0),
    ("1,200,000", 1200000.0),
    ("2.94 mb/d", 2.94),
    ("1.9 million b/d", 1900000.0),
    ("1.9 million", 1900000.0),
    ("$1 million a day", 1000000.0),
    ("5.4%", 5.4),
    ("Worldscale 1,000 in sight", 1000.0),
    ("$2 Billion", 2000000000.0),
    ("0.668M", 668000.0),
    ("-0.27", -0.27),
    ("250,000 barrels a day", 250000.0),
    ("no digits here", None),
])
def test_normalise_number(text, expected):
    assert summarize.normalise_number(text) == expected


def test_extract_numbers_handles_dates_units_and_magnitudes():
    """ISO dates count only as their year (day/month/time components are not figures)."""
    nums = summarize.extract_numbers("as of 2026-10-06 the crack was 72.51 $/bbl, up 3.64; draw 1.9 million b/d in 3Q26")
    assert 2026 in nums and 10 not in nums and 6 not in nums and -10 not in nums
    assert 72.51 in nums and 3.64 in nums and 1.9 in nums and 1900000.0 in nums and 3 in nums
    assert summarize.extract_numbers("https://x.test/id=12345 and 7") == [7]
    assert summarize.extract_numbers("5 km and 2.94 mb/d") == [5, 2.94]
    assert summarize.extract_numbers("published 2026-10-08T08:45:29Z, as of 2026-10") == [2026, 2026]
    # letters/dots before a figure do not hide it; a sign only counts after whitespace
    assert summarize.extract_numbers("down .5% at...998.77 approx.998 x3 EU-27 fell -0.27") == [0.5, 998.77, 998, 3, 27, -0.27]
    assert summarize.extract_numbers("Diesel 72.51 then 1e6") == [72.51, 1, 6]


def test_number_guard_rejects_figures_hidden_by_punctuation(ai_cfg, ctx, now):
    """Regression (number-guard look-behind): '.5%', 'at...998.77', 'approx.998', 'x3' are figures."""
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    # (3 and 5 are real input figures — level 3, "Oil Jumps 5%" — so the probes use 7 and .5)
    for bad_text in ("Brent down .5% on the day.", "Diesel crack at...998.77 $/bbl", "Brent approx.998 per barrel",
                     "Trucking costs are x7 since last year", "VLCC rates topped 1e6 dollars a day",
                     "Stock draw of .75 mb/d, EIA says."):
        probe = copy.deepcopy(reply)
        probe["what_changed"][0]["text"] = bad_text
        with pytest.raises(summarize.ValidationFailure, match="number guard"):
            summarize.validate_reply(probe, input_doc)


def test_number_guard_ignores_timestamp_components(ai_cfg, ctx, now):
    """Regression: the input's published/as_of/generated_at stamps must not whitelist their
    day/hour/minute/second components, only the year."""
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    allowed = summarize.numbers_in_input(input_doc)
    small = sorted({int(n) for n in allowed if float(n).is_integer() and 0 <= n < 120})
    for n in (7, 8, 25, 45, 53):          # occur only inside timestamps of input_example.json
        assert n not in small, n
    assert 2026 in allowed and 2025 in allowed
    assert summarize.check_numbers(["OPEC adds 8 mb/d"], allowed) == [8]
    assert summarize.check_numbers(["OPEC adds 25 million barrels"], allowed) == [25000000]
    assert summarize.check_numbers(["Prices up 8% this week"], allowed) == [8]
    assert summarize.check_numbers(["45 tankers were struck off Qatar"], allowed) == [45]
    assert summarize.check_numbers(["7 tankers were hit in the strait"], allowed) == [7]
    # still fine: real figures, years and dates (ISO or month-name form)
    assert summarize.check_numbers(["Diesel crack at 72.51 $/bbl", "Brent at $105 per barrel"], allowed) == []
    assert summarize.check_numbers(["As of 2026-10-06 nothing moved in 2026"], allowed) == []
    assert summarize.check_numbers(["Brent fell on Oct 6 and again on 7 October 2026"], allowed) == []
    assert summarize.check_numbers(["OPEC may 7 ships"], allowed) == [7]   # lowercase "may" is not a month
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    probe = copy.deepcopy(reply)
    probe["what_changed"][0]["text"] = "Three refineries idle, 45 ships waiting"
    with pytest.raises(summarize.ValidationFailure, match="number guard"):
        summarize.validate_reply(probe, input_doc)


def test_whitespace_only_text_falls_back_cleanly(ai_cfg, ctx, now, tmp_path, monkeypatch):
    """A bullet text of '   ' (or a blank headline) is rejected by validate_reply, so run() takes
    the retry → rule-based path instead of raising a document ValidationError."""
    reply = json.loads((LLM / "valid.json").read_text(encoding="utf-8"))
    blank = copy.deepcopy(reply)
    blank["what_changed"][1]["text"] = "   "
    (tmp_path / "blank_text.json").write_text(json.dumps(blank), encoding="utf-8")
    head = copy.deepcopy(reply)
    head["headline"] = " \n "
    (tmp_path / "blank_head.json").write_text(json.dumps(head), encoding="utf-8")
    monkeypatch.setattr(summarize, "LLM_FIXTURE_DIR", tmp_path)
    for name in ("blank_text.json", "blank_head.json"):
        doc, info = _run(ai_cfg, ctx, now, name)
        common.validate(doc, "summary")
        assert doc["fallback"] is True and doc["provider"] == "none"
        assert info["attempts"] == 2 and "schema" in info["reason"]
    # stripping happens before the schema check; empty quips are dropped rather than rejected
    padded = copy.deepcopy(reply)
    padded["headline"] = "  " + padded["headline"] + " "
    padded["quips"] = ["   ", padded["quips"][0] + " "] if padded["quips"] else ["  "]
    input_doc, _ = summarize.build_input(ctx, ai_cfg, now)
    cleaned = summarize.validate_reply(padded, input_doc)
    assert cleaned["headline"] == reply["headline"]
    assert all(q == q.strip() and q for q in cleaned["quips"])


def test_document_schema_failure_after_validate_reply_falls_back(ai_cfg, ctx, now, monkeypatch):
    """Belt and braces: if model content passes validate_reply but not summary.schema.json, run()
    uses the rule-based fallback instead of raising."""
    good = (LLM / "valid.json").read_text(encoding="utf-8")
    monkeypatch.setattr(summarize, "select_provider", lambda *a, **k: ("gemini", "m", lambda s, u: good, None))
    monkeypatch.setattr(summarize, "validate_reply", lambda reply, input_doc: {"headline": "", "what_changed": reply["what_changed"], "quips": []})
    doc, info = summarize.run(ai_cfg, None, fixtures=False, now=now, env={}, context=ctx)
    common.validate(doc, "summary")
    assert doc["fallback"] is True and doc["provider"] == "none"
    assert info["reason"].startswith("rejected: schema:")


def test_empty_reply_is_retried_with_the_same_prompt(ai_cfg, ctx, now, monkeypatch):
    calls = []
    good = (LLM / "valid.json").read_text(encoding="utf-8")

    def flaky(system, user):
        calls.append(user)
        if len(calls) == 1:
            raise summarize.EmptyReply("gemini returned empty text (finishReason=MAX_TOKENS)")
        return good

    monkeypatch.setattr(summarize, "select_provider", lambda *a, **k: ("gemini", "m", flaky, None))
    doc, info = summarize.run(ai_cfg, None, fixtures=False, now=now, env={}, context=ctx)
    assert doc["fallback"] is False and info["attempts"] == 2
    assert calls[0] == calls[1]   # re-asked unchanged, no "rejected by the validator" text


def test_summary_is_byte_stable_across_runs_with_identical_input(cfg, ctx, now):
    """Brief §3/§7: the same input at a later time yields the same summary.json once the ignored
    stamps are stripped (as_of = input as_of, digest without generated_at)."""
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    later = now.replace(hour=17, minute=17)
    first, _ = summarize.run(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    second, _ = summarize.run(cfg, None, fixtures=True, now=later, env={}, context=ctx)
    assert first["generated_at"] != second["generated_at"]
    assert common.strip_keys(first, common.DEFAULT_IGNORE) == common.strip_keys(second, common.DEFAULT_IGNORE)
    assert first["as_of"] == second["as_of"] == ctx["news"]["as_of"]
    assert first["inputs_digest"] == second["inputs_digest"]
    # proposals: as_of from the news, digest from an input without a run stamp
    p1, _ = summarize.run_proposals(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    p2, _ = summarize.run_proposals(cfg, None, fixtures=True, now=later, env={}, context=ctx)
    assert common.strip_keys(p1, common.DEFAULT_IGNORE) == common.strip_keys(p2, common.DEFAULT_IGNORE)
    assert p1["as_of"] == ctx["news"]["as_of"]


def test_check_numbers_tolerance():
    assert summarize.check_numbers(["72.51 and 1.9 million"], [72.506, 1.9]) == []
    assert summarize.check_numbers(["72.51"], [72.3]) == [72.51]
    assert summarize.check_numbers(["$1.2m"], [1200000]) == []


# ------------------------------------------------------------------------------ proposals

def test_proposals_guard_keeps_verbatim_and_drops_the_rest(ai_cfg, ctx, now):
    env = {summarize.FIXTURE_ENV: "proposals_valid.json"}
    doc, info = summarize.run_proposals(ai_cfg, None, fixtures=True, now=now, env=env, context=ctx)
    common.validate(doc, "proposals")
    raw = json.loads((LLM / "proposals_valid.json").read_text(encoding="utf-8"))["proposals"]
    assert len(raw) == 6
    assert info["count"] == len(doc["proposals"]) == 2
    kept = {(p["target"], p["proposed_value"]) for p in doc["proposals"]}
    assert kept == {("shipping.vlcc_day_rate_usd.now", 1000000), ("hormuz_ledger[hormuz_normal].delta", 16.5)}
    links = _news_links(ctx)
    for p in doc["proposals"]:
        item = next(it for it in ctx["news"]["items"] if it["link"] == p["source_url"])
        assert p["source_url"] in links
        assert p["quote"] in item["title"] or p["quote"] in item["snippet"]
        assert p["source_title"] == item["title"] and p["published"] == item["published"] and p["feed"] == item["feed"]
        assert p["unit"] in ("USD/day", "mb/d")
    # dropped: non-substring quote, number not in quote, foreign URL, disallowed target
    assert all(p["proposed_value"] != 1500000 for p in doc["proposals"])
    assert all(p["proposed_value"] != 18 for p in doc["proposals"])
    assert all(p["target"] != "shipping.voyage_days_before" for p in doc["proposals"])


def test_proposals_guard_is_unit_aware(ctx):
    """One canonical value per quote token: the un-expanded raw of a magnitude token is not a
    USD/day rate, an expanded one is not an mb/d flow, and a bare digit is no quote."""
    input_doc, _ = summarize.build_proposals_input(ctx, common.read_json(common.DATA / "manual.json"))
    targets = summarize.allowed_targets(common.read_json(common.DATA / "manual.json"))
    vlcc = next(it for it in ctx["news"]["items"] if "$1 million a day" in it["title"] + it["snippet"])
    kpler = next(it for it in ctx["news"]["items"] if "16.5 million barrels per day" in it["snippet"])
    one = next(it for it in ctx["news"]["items"] if "1" in it["title"])
    probes = [
        {"target": "shipping.vlcc_day_rate_usd.now", "proposed_value": 1, "quote": "VLCC rates top $1 million a day", "source_url": vlcc["link"]},
        {"target": "hormuz_ledger[hormuz_normal].delta", "proposed_value": 16500000, "quote": "16.5 million barrels per day", "source_url": kpler["link"]},
        {"target": "world.production_mbd", "proposed_value": 1, "quote": "1", "source_url": one["link"]},
    ]
    assert summarize.guard_proposals({"proposals": probes}, input_doc, targets, items=ctx["news"]["items"]) == []
    kept = [
        {"target": "shipping.vlcc_day_rate_usd.now", "proposed_value": 1000000, "quote": "VLCC rates top $1 million a day", "source_url": vlcc["link"]},
        {"target": "hormuz_ledger[hormuz_normal].delta", "proposed_value": 16.5, "quote": "16.5 million barrels per day", "source_url": kpler["link"]},
    ]
    assert [p["proposed_value"] for p in summarize.guard_proposals({"proposals": kept}, input_doc, targets, items=ctx["news"]["items"])] == [1000000, 16.5]


# ------------------------------------------------------------------------------ providers

class _JsonResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def close(self):
        pass


class _PostSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_gemini_request_shape_and_empty_reply(monkeypatch):
    """Gemini 3: thinking on by default and counted against maxOutputTokens → generous cap, low
    thinking level, no lowered temperature; an all-thought candidate is an EmptyReply (retried)."""
    monkeypatch.setattr(common, "http_post", _REAL_HTTP_POST)
    empty = {"candidates": [{"content": {"parts": [{"text": "thinking…", "thought": True}]}, "finishReason": "MAX_TOKENS"}]}
    good = {"candidates": [{"content": {"parts": [{"text": '{"headline": "x"}'}]}, "finishReason": "STOP"}]}
    sess = _PostSession(_JsonResp(empty), _JsonResp(good))
    with pytest.raises(summarize.EmptyReply, match="MAX_TOKENS"):
        summarize.complete_gemini("sys", "user", model=summarize.DEFAULT_GEMINI_MODEL, api_key="K", session=sess)
    assert summarize.complete_gemini("sys", "user", model=summarize.DEFAULT_GEMINI_MODEL, api_key="K", session=sess) == '{"headline": "x"}'
    url, kw = sess.calls[0]
    assert url.endswith("/models/%s:generateContent" % summarize.DEFAULT_GEMINI_MODEL)
    assert kw["headers"]["x-goog-api-key"] == "K" and "key=" not in url
    gen = kw["json"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["maxOutputTokens"] == summarize.MAX_OUTPUT_TOKENS >= 8192
    assert gen["thinkingConfig"] == {"thinkingLevel": "low"} and "temperature" not in gen
    legacy = summarize.gemini_generation_config("gemini-2.5-flash")
    assert "thinkingConfig" not in legacy and legacy["temperature"] == 0.2


def test_anthropic_error_body_is_a_masked_fetch_error(monkeypatch):
    """A 2xx body of type=error becomes a FetchError carrying the API's message (no NameError)."""
    monkeypatch.setattr(common, "http_post", _REAL_HTTP_POST)
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "model not found: nope (key=SECRET123)"}}
    sess = _PostSession(_JsonResp(body))
    with pytest.raises(common.FetchError) as exc:
        summarize.complete_anthropic("sys", "user", model="nope", api_key="K", session=sess)
    assert "model not found" in str(exc.value) and "SECRET123" not in str(exc.value)
    assert sess.calls[0][1]["json"]["max_tokens"] == summarize.MAX_OUTPUT_TOKENS
    with pytest.raises(summarize.EmptyReply):
        summarize.complete_anthropic("sys", "user", model="m", api_key="K", session=_PostSession(_JsonResp({"content": [], "stop_reason": "max_tokens"})))


def test_proposals_none_provider_gives_empty_list(cfg, ctx, now):
    cfg = dict(cfg)
    cfg["ai_provider"] = "none"
    doc, info = summarize.run_proposals(cfg, None, fixtures=True, now=now, env={}, context=ctx)
    common.validate(doc, "proposals")
    assert doc["proposals"] == [] and doc["provider"] == "none" and info["count"] == 0


def test_proposals_summary_reply_yields_empty_list(ai_cfg, ctx, now):
    """A reply without a 'proposals' key (e.g. the summary fixture) is simply no proposals."""
    doc, info = summarize.run_proposals(ai_cfg, None, fixtures=True, now=now, env=_env("valid.json"), context=ctx)
    assert doc["proposals"] == []
    common.validate(doc, "proposals")


def test_allowed_targets_from_manual():
    manual = common.read_json(common.DATA / "manual.json")
    targets = summarize.allowed_targets(manual)
    assert "shipping.vlcc_day_rate_usd.now" in targets and targets["shipping.vlcc_day_rate_usd.now"]["unit"] == "USD/day"
    assert "hormuz_ledger[hormuz_normal].delta" in targets
    assert "hormuz_ledger[region_total].delta" not in targets  # subtotal rows have no delta
    assert "refinery_shock[0].diesel_delta_mbd" in targets
    for t in targets:
        assert summarize.TARGET_RE.match(t), t
    assert set(summarize.allowed_targets(None)) >= set(summarize.STATIC_TARGETS)


def test_proposals_input_bounded(ai_cfg, ctx):
    doc, text = summarize.build_proposals_input(ctx, common.read_json(common.DATA / "manual.json"))
    assert len(text) <= summarize.MAX_INPUT_CHARS and len(doc["news"]) <= 30
    assert any(t["target"] == "world.production_mbd" for t in doc["targets"])


# ---------------------------------------------------------------------------- contract bits

def test_module_constants():
    assert (summarize.SOURCE_KEY, summarize.OUTPUT_FILE, summarize.SCHEMA, summarize.STALE_KIND) == ("summary", "summary.json", "summary", "summary")
    assert (summarize.PROPOSALS_SOURCE_KEY, summarize.PROPOSALS_OUTPUT_FILE, summarize.PROPOSALS_SCHEMA) == ("proposals", "proposals.json", "proposals")


def test_fixture_files_are_consistent_with_input_example(ctx):
    """Every link in the LLM fixtures (except the deliberate foreign ones) is a real input link."""
    links = _news_links(ctx)
    for name in ("valid.json", "invalid_number.json"):
        reply = json.loads((LLM / name).read_text(encoding="utf-8"))
        for b in reply["what_changed"]:
            assert set(b["sources"]) <= links, name
    bad = json.loads((LLM / "invalid_link.json").read_text(encoding="utf-8"))
    foreign = {u for b in bad["what_changed"] for u in b["sources"]} - links
    assert foreign == {"https://example.com/oil-news/hormuz-transits"}
    for it in ctx["news"]["items"]:
        assert len(it["snippet"]) <= 240 and "<" not in it["snippet"]
        assert re.fullmatch(r"[0-9a-f]{40}", it["id"]) and it["id"] == common.sha1(it["link"])


def test_output_stable_across_reruns(ai_cfg, ctx, now):
    a, _ = _run(ai_cfg, ctx, now, "valid.json")
    b, _ = _run(ai_cfg, ctx, now, "valid.json")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
