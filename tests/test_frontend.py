"""Static checks of the frontend in ``site/`` (no browser needed).

* i18n: ``de.json`` mirrors the keys of ``en.json`` (stub with empty values), every literal
  ``t('…')``/``tf('…')`` key in ``app.js`` exists, dynamic key families are complete, the gauge
  labels equal ``config.crack_levels.labels`` and every config news topic has a label;
* the brief's microcopy (CLAUDE.md §9) lives in ``en.json``;
* ``index.html``/``style.css`` only reference files that exist, no Google Fonts at runtime,
  ``font-display: swap``; required static files are present; ``og.png`` stays small;
* no ``innerHTML`` in ``app.js``/``charts.js`` (ARCHITECTURE §5); sections carry
  ``aria-labelledby`` and keep the brief's order; ``robots.txt`` keeps the raw JSON out of indexes.

Runs on Python 3.9 and 3.12.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

import common

SITE: Path = common.SITE

# en.json keys whose value may legitimately be empty (donate variants 1–3 have no sub-line).
EMPTY_OK = {"donate.1.sub", "donate.2.sub", "donate.3.sub"}

DYNAMIC_KEYS = {
    "hero.badge.": ["deficit", "surplus", "balanced"],
    "hero.tip.": ["deficit", "surplus", "balanced"],
    "hero.gap.": ["deficit", "surplus", "balanced"],
    "crack.level.": ["0", "1", "2", "3", "4"],
    "crack.range.": ["1y", "5y", "max"],
    "donate.": ["0.headline", "0.sub", "0.button", "1.headline", "1.button",
                "2.headline", "2.button", "3.headline", "3.button"],
    "meth.formula.": ["diesel", "gasoline", "jet", "321"],
    "groc.product.": ["gasoline", "jet", "diesel", "other"],
    "ship.vlcc.": ["pre_war", "pandemic_peak", "now"],
}

# Microcopy pinned by the brief (CLAUDE.md §9): key → the sentence en.json must start with.
BRIEF_MICROCOPY = {
    "hero.sub": "Updated three times a day. Explained like a whiteboard. Crack spreads included. This is a clean show.",
    "crack.sub": "Analysts say 'crack spreads are widening' with a straight face. We'll show you why.",
    "crack.footnote": "NY Harbor ULSD minus Brent. Other benchmarks (Europe, Singapore) can read higher.",
    "wb.sub": "One marker, one strait, twenty million barrels. Let's do the math.",
    "countries.sub": "Some countries are oil wells with flags. Others are gas stations with armies. Most are just thirsty.",
    "countries.footnote": "Annual data, because countries don't text us daily.",
    "ship.sub": "When the shortcut closes, oil takes the scenic route. All the way around Africa.",
    "ship.tankers": "Same ships, longer trips. Two out of three effectively vanish. Not literally. Please don't call the coast guard.",
    "ship.dayrate": "A supertanker used to rent for about a nice used car per day. Now it's a small house. Per day.",
    "groc.sub": "Almost everything you buy rode a diesel truck at some point. Usually several.",
    "groc.diesel": "Diesel is the stubborn one. Trucks, tractors, cranes, ships, backup generators. You can't just skip it.",
    "news.sub": "We read the oil news three times a day so you don't have to. You're welcome. Sources below, as always.",
    "news.fallback": "Our summarizer is on a coffee break. Here are the raw headlines.",
    "donate.0.headline": "Buy me a cup of liquid gold that fuels humans.",
    "donate.0.button": "Refine a coffee ☕",
    "donate.1.button": "Fill 'er up",
    "donate.2.button": "Widen my coffee spread",
    "donate.3.button": "Drop a barrel (of coffee)",
    "donate.fineprint": "Donations go to a person, not a hedge fund. They don't change what the data says.",
    "footer.disclaimer": "Not financial advice. Not trading advice. Not even dinner-party advice. Numbers come from the sources listed; jokes come from us.",
    "common.unavailable": "Data temporarily unavailable. The chart is fine, it's just shy.",
    "footer.src.fred": "Source: FRED, Federal Reserve Bank of St. Louis; data: U.S. EIA",
}

SECTION_ORDER = ["hero", "crack", "whiteboard", "countries", "shipping", "groceries", "news", "methodology", "donate"]

REQUIRED_FILES = [
    "index.html", "404.html", "config.json", "css/style.css", "js/app.js", "js/charts.js",
    "js/vendor/uPlot.iife.min.js", "js/vendor/uPlot.min.css", "js/vendor/LICENSE-uPlot.txt",
    "fonts/PermanentMarker-latin.woff2", "fonts/Caveat-latin.woff2",
    "fonts/LICENSE_PermanentMarker.txt", "fonts/OFL_Caveat.txt",
    "i18n/en.json", "i18n/de.json", "img/og.png", "favicon.svg", "robots.txt", "sitemap.xml", "data/manual.json",
]


def _read(rel: str) -> str:
    with open(SITE / rel, "r", encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def en() -> dict:
    return json.loads(_read("i18n/en.json"))


@pytest.fixture(scope="module")
def de() -> dict:
    return json.loads(_read("i18n/de.json"))


@pytest.fixture(scope="module")
def app_js() -> str:
    return _read("js/app.js")


@pytest.fixture(scope="module")
def html() -> str:
    return _read("index.html")


@pytest.fixture(scope="module")
def css() -> str:
    return _read("css/style.css")


# ------------------------------------------------------------------------------------- i18n

def test_de_stub_mirrors_en_keys(en, de):
    assert set(de) == set(en)
    assert all(v == "" for v in de.values()), "de.json is a stub: every value empty (falls back to en)"


def test_en_values_are_non_empty_strings(en):
    bad = [k for k, v in en.items() if not isinstance(v, str) or (not v and k not in EMPTY_OK)]
    assert bad == []


def test_every_literal_t_key_in_app_js_exists(en, app_js):
    literal = set(re.findall(r"\bt[f]?\(\s*'([a-z0-9_.]+)'\s*[,)]", app_js))
    assert len(literal) > 50, "app.js should take its copy from i18n"
    missing = sorted(k for k in literal if k not in en)
    assert missing == []


@pytest.mark.parametrize("prefix,names", sorted(DYNAMIC_KEYS.items()))
def test_dynamic_key_families_complete(en, prefix, names):
    missing = [prefix + n for n in names if prefix + n not in en]
    assert missing == []


def test_topic_labels_and_gauge_labels_match_config(en, cfg):
    assert all("news.topic." + topic in en for topic in cfg["news_topics"])
    assert [en["crack.level.%d" % i] for i in range(5)] == cfg["crack_levels"]["labels"]


@pytest.mark.parametrize("key,phrase", sorted(BRIEF_MICROCOPY.items()))
def test_brief_microcopy_lives_in_en_json(en, key, phrase):
    assert en.get(key, "").startswith(phrase)


# ----------------------------------------------------------------------------- static assets

def test_index_html_references_resolve(html):
    refs = re.findall(r'(?:href|src)="\./([^"#?]+)', html)
    assert refs, "index.html should reference its local assets with ./ paths"
    missing = [r for r in refs if not (SITE / r).exists()]
    assert missing == []


def test_css_url_references_resolve(css):
    refs = re.findall(r"url\('\.\./([^']+)'\)", css)
    assert refs, "style.css should reference the local fonts/images"
    missing = [r for r in refs if not (SITE / r).exists()]
    assert missing == []


def test_fonts_are_local_with_swap(html, css):
    assert "fonts.googleapis" not in html and "fonts.gstatic" not in css
    assert "font-display: swap" in css


@pytest.mark.parametrize("rel", REQUIRED_FILES)
def test_required_file_present(rel):
    assert (SITE / rel).is_file(), rel


def test_og_image_is_small():
    assert os.path.getsize(SITE / "img/og.png") < 200_000


def test_robots_keeps_raw_json_out_of_indexes():
    robots = _read("robots.txt")
    assert re.search(r"(?im)^Disallow:\s*/crackspread/data/", robots)


def test_robots_sitemap_points_at_a_real_sitemap(cfg):
    robots = _read("robots.txt")
    m = re.search(r"(?im)^Sitemap:\s*(\S+)", robots)
    assert m and m.group(1) == cfg["site_url"] + "sitemap.xml"
    sitemap = _read("sitemap.xml")
    assert "<urlset" in sitemap and "<loc>%s</loc>" % cfg["site_url"] in sitemap


def test_404_page_links_are_root_absolute(cfg):
    """GitHub Pages serves 404.html at the requested URL, so ./ links would resolve against the missing path."""
    prefix = "/" + cfg["site_url"].split("/", 3)[3]          # "/crackspread/"
    page = _read("404.html")
    hrefs = re.findall(r'href="([^"]+)"', page)
    assert hrefs and all(h.startswith(prefix) for h in hrefs), hrefs
    assert 'href="%s"' % prefix in page


# -------------------------------------------------------------------------------- numbers/copy

def test_hero_gap_text_pairs_label_with_magnitude(app_js, en):
    """Shortage/surplus rows show |stock_draw| under a directional label; the signed form is only used for the
    balanced row and the beam. The old sign-flipping expression printed −0.67 under "drawn from storage"."""
    assert "status === 'surplus' ? 1 : -1" not in app_js
    hero = app_js.split("function renderHero", 1)[1].split("function renderCrack", 1)[0]
    assert "Math.abs(draw)" in hero and "fmt.signed(-draw, 2)" in hero
    assert "const dGap = mk(gapValue" in hero, "<data value> must carry the displayed number"
    assert en["hero.gap.deficit"].startswith("drawn") and en["hero.gap.surplus"].startswith("added")


def test_diesel_math_percentages_come_from_manual_json(app_js):
    """The math box prints Fisher's stated figures (manual.json), never a frontend derivation from the table,
    and they agree with the Fisher note printed beneath them."""
    manual = json.loads(_read("data/manual.json"))
    dm = manual["diesel_math"]
    rise = dm["stated_rise_pct"]
    assert isinstance(rise["oil"], (int, float)) and isinstance(rise["diesel"], (int, float))
    assert "%d%%" % rise["oil"] in dm["note"] and "%d%%" % rise["diesel"] in dm["note"]
    groc = app_js.split("function renderGroceries", 1)[1].split("function newsItem", 1)[0]
    assert "stated_rise_pct" in groc
    assert "dm.now.oil / dm.last_year.oil" not in groc and "dm.now.diesel / dm.last_year.diesel" not in groc


def test_needle_pivot_is_not_applied_twice(css):
    """charts.js rotates the needle with rotate(a cx cy); a CSS transform-origin on top would double the offset."""
    rule = re.search(r"\.needle\s*\{([^}]*)\}", css).group(1)
    assert "transform-origin" not in rule and "transform-box" not in rule
    assert re.search(r"rotate\(\$\{[^}]+\}\s+\$\{cx\}\s+\$\{cy\}\)", _read("js/charts.js"))


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _luminance(rgb) -> float:
    def ch(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg, bg) -> float:
    lf, lb = _luminance(fg), _luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_text_colour_tokens_meet_wcag_aa(css):
    """Brief §11: contrast AA. Every colour token used for small text must reach 4.5:1 on the paper and on
    the tinted "official" box; the chart module's MUTED (tick labels) too."""
    tokens = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", css))
    paper = _hex_to_rgb(tokens["paper"])
    # .box.official = rgba(27, 77, 137, 0.04) over the paper
    official = tuple(p * 0.96 + c / 255 * 0.04 for p, c in zip(paper, (27, 77, 137)))
    for name in ("ink", "ink-soft", "muted", "red", "blue", "green"):
        fg = _hex_to_rgb(tokens[name])
        assert _contrast(fg, paper) >= 4.5, (name, tokens[name], _contrast(fg, paper))
    for name in ("ink", "ink-soft", "muted", "blue", "green"):
        fg = _hex_to_rgb(tokens[name])
        assert _contrast(fg, official) >= 4.5, (name, tokens[name], _contrast(fg, official))
    charts = _read("js/charts.js")
    muted = re.search(r"const MUTED = '(#[0-9A-Fa-f]{6})'", charts).group(1)
    assert _contrast(_hex_to_rgb(muted), paper) >= 4.5, muted
    green = re.search(r"const GREEN = '(#[0-9A-Fa-f]{6})'", charts).group(1)
    assert _contrast(_hex_to_rgb(green), paper) >= 3.0, green   # fills/strokes and 20px-bold SVG labels only


# ------------------------------------------------------------------------------ html/js rules

def test_no_innerhtml_with_data(app_js):
    body = app_js.split("\n", 3)[3]   # skip the header comment, which names the rule
    assert "innerHTML" not in body
    assert "innerHTML" not in _read("js/charts.js")


def test_sections_are_labelled_and_in_brief_order(html):
    assert re.search(r'<section id="hero"[^>]*aria-labelledby="hero-title"', html)
    order = re.findall(r'<section id="([a-z]+)"', html)
    assert order == SECTION_ORDER
    for sid in SECTION_ORDER:
        assert re.search(r'<section id="%s"[^>]*aria-labelledby=' % sid, html), sid


def test_no_third_party_runtime_requests(html, app_js, css):
    # Only the site's own absolute URLs (canonical/OG) may appear in the HTML; scripts are local.
    own = re.sub(r"https?://fixoa\.github\.io/crackspread/\S*", "", html)
    assert not re.search(r'<script[^>]+src="https?://', own)
    assert not re.search(r'<link[^>]+href="https?://', own)
    assert not re.search(r'<(img|iframe|source|video|audio|embed|object)\b[^>]*\b(src|data|poster)="https?://', own)
    assert "fetch('http" not in app_js and 'fetch("http' not in app_js
    # Hard Rule 6 also covers CSS (@import / url(https://…)) and resource loads from the JS; links
    # the user clicks (href targets, source home pages) are fine, anything the browser fetches
    # on its own (fetch, img/script/iframe src, beacons, sockets, workers) is not.
    assert "@import" not in css and re.search(r"https?://", css) is None
    loaders = r"\b(fetch|importScripts|sendBeacon|XMLHttpRequest|EventSource|WebSocket|Worker|SharedWorker|Image|Audio)\s*\(\s*['\"`]https?://"
    for name in ("js/app.js", "js/charts.js"):
        js = _read(name)
        assert not re.search(loaders, js), name
        assert not re.search(r"\b(src|poster|srcset|data)\s*[:=]\s*['\"`]https?://", js), name
        assert not re.search(r"\.open\s*\(\s*['\"]\w+['\"]\s*,\s*['\"`]https?://", js), name
