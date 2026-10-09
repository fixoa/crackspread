# Crackspread — Module Contract

This file pins the interfaces between the modules of Crackspread so that they can be built
independently and still fit together. `CLAUDE.md` (the build brief) is the product spec; where the
brief leaves an interface open, **this file decides**. Where the brief and this file disagree on
*product behaviour*, the brief wins; on *interfaces and file shapes*, this file wins.

## 0. Runtime targets and conventions

- **Python 3.9 and 3.12.** The local machine has only a stock `python3` 3.9; CI runs 3.12. Code must
  work on both: put `from __future__ import annotations` at the top of every module, no `match`,
  no `X | Y` in runtime positions (`isinstance`, defaults), no `datetime.UTC` (use
  `datetime.timezone.utc`), no `tomllib`, no `typing.Self`. `zoneinfo` is fine.
- Dependencies: only `requests`, `feedparser`, `openpyxl`, `jsonschema`, `python-dateutil`,
  `pytest` (see `requirements.txt`). No pandas. Standard library otherwise.
- Local venv at `.venv/` (already created, deps installed). Run everything from the repo root:
  `.venv/bin/python scripts/update.py …`, `.venv/bin/pytest -q`.
- **Flat imports.** `scripts/` is not a package. `python scripts/update.py` puts `scripts/` on
  `sys.path[0]`, so modules import each other as top-level names: `import common`,
  `from fetch_prices import run`. `tests/conftest.py` inserts `<root>/scripts` into `sys.path` so
  tests use the same names. Never write `from scripts.common import …`.
- All HTTP goes through `common.http_get` (User-Agent, timeout 20 s, bounded retries with
  backoff: `retries` = **3 attempts in total**, i.e. the brief's "3 retries" are read as three
  tries, which keeps a dead source under ~65 s per URL; optional `max_bytes` body bound) and,
  for the LLM providers, `common.http_post` (same semantics, JSON body, retries also on 429). No
  module calls `requests` directly. In fixtures mode nothing may touch the network.
- **Never call the EIA API during development/tests.** `DEMO_KEY` is limited to ~10 requests per
  hour and the real run at the end needs that quota. Use the saved responses (see §6) as fixtures.
  Running `update.py --dry-run` is allowed only with `EIA_API_KEY` unset (then STEO comes from the
  xlsx and countries from JODI), or with `--only prices` / `--only news`. Two env switches help:
  `CRACKSPREAD_COUNTRIES_VIA=jodi` forces the JODI fallback in fixtures **and** live mode (never
  touches api.eia.gov), `CRACKSPREAD_STEO_VIA=xlsx` selects the xlsx path in fixtures mode.
- Paths: `common.ROOT`, `common.SITE = ROOT/"site"`, `common.DATA = SITE/"data"`,
  `common.SCHEMAS = ROOT/"scripts"/"schemas"`, `common.FIXTURES = ROOT/"tests"/"fixtures"`.
- Time: internal UTC everywhere. `common.now_utc()` returns an aware datetime; it honours the env
  var `CRACKSPREAD_NOW` (ISO-8601 UTC, e.g. `2026-10-08T10:00:00Z`) so runs are deterministic.
  `--fixtures` sets `CRACKSPREAD_NOW=2026-10-08T10:00:00Z` unless the variable is already set.
  Timestamps are written as `YYYY-MM-DDTHH:MM:SSZ`; dates as `YYYY-MM-DD`.
- JSON on disk: `json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False)` + trailing newline,
  written atomically (`<file>.tmp` + `os.replace`). Numbers rounded to a sensible precision
  (prices 3 decimals, mb/d 2 decimals, kb/d 1 decimal) so re-runs are byte-stable.

## 1. `scripts/common.py` (foundation; other modules MUST NOT edit it — report needed changes)

```python
UA = "crackspread-bot/1.0 (+https://github.com/fixoa/crackspread)"
ROOT, SITE, DATA, SCHEMAS, FIXTURES            # pathlib.Path constants (see §0)

class FetchError(Exception): ...                # network/HTTP failure after retries
class PlausibilityError(ValueError): ...        # value outside allowed range

def now_utc() -> datetime                        # aware UTC, honours CRACKSPREAD_NOW
def iso_utc(dt: datetime | None = None) -> str   # "2026-10-08T10:00:12Z" (dt defaults to now_utc())
def parse_iso(s: str) -> datetime                # accepts "YYYY-MM-DD" (→ 00:00Z) and ISO datetimes, any tz → UTC
def hours_since(as_of: str, now: datetime | None = None) -> float

def load_config() -> dict                        # site/config.json (cached)
def http_get(url, *, params=None, timeout=20, retries=3, backoff=1.5, headers=None,
             session=None, stream=False, max_bytes=None) -> requests.Response
    # sets UA, raises FetchError after retries on connection errors, timeouts and 5xx;
    # raises FetchError immediately on 4xx (no retry); never returns a non-2xx response.
    # retries = attempts in total (3 → 1.5 s, 3 s backoff). max_bytes: the body is read in
    # chunks and FetchError is raised (no retry) once it exceeds the bound or Content-Length
    # announces more; .content/.text/.iter_lines() then serve the buffered body. The fetchers
    # use 5 MB for FRED CSVs and RSS feeds, 25 MB for STEO_m.xlsx and the JODI CSV.
def http_post(url, *, json=None, headers=None, timeout=60, retries=2, backoff=2.0,
              session=None) -> requests.Response
    # POST with a JSON body; same UA/masking/FetchError rules as http_get, but also retries on 429
    # (LLM rate limits). Used by summarize.py; keys travel in headers only.

def read_json(path) -> dict | None               # None if missing or unparsable (logs a warning)
def write_json_atomic(path, obj) -> None
def write_json_if_changed(path, obj, ignore=("generated_at", "fetched_at", "run_id", "last_run")) -> bool
    # Compares against the existing file with the ignored keys stripped recursively.
    # If equal: does NOT write (keeps the old file, including its old timestamps), returns False.
    # If different: writes obj as-is, returns True.
def strip_keys(obj, keys) -> obj                 # recursive copy without the given keys (used above)

def load_schema(name: str) -> dict               # scripts/schemas/<name>.schema.json
def validate(obj, schema_name: str) -> None      # jsonschema Draft 2020-12; raises jsonschema.ValidationError

def datapoint(value, unit, as_of, source, source_url, *, fetched_at=None, stale=False, **extra) -> dict
    # {"value","unit","as_of","source","source_url","fetched_at","stale", **extra}; value may be None
def ensure_range(value: float, lo: float, hi: float, name: str) -> float   # raises PlausibilityError

def stale_threshold_hours(kind: str, cfg: dict) -> float   # cfg["stale_after_hours"][kind]
def is_stale(as_of: str | None, kind: str, cfg: dict, now=None) -> bool   # None → True
def apply_time_stale(doc: dict, kind: str, cfg: dict, now=None) -> dict
    # Sets doc["stale"] = doc.get("stale", False) or is_stale(doc.get("as_of"), kind, ...).
    # Additionally, for every dict anywhere under doc["latest"] (if present) that has an "as_of"
    # key, sets its "stale" the same way (prices.json datapoints). Returns doc (mutated).
def mark_all_stale(doc: dict) -> dict            # sets "stale": True on the top level and every dict that has "as_of"

def sha1(s: str) -> str                           # hex digest
def slug(s: str) -> str                           # lowercase, [a-z0-9-]
def log(msg: str) -> None                         # prints "[HH:MM:SS] msg" to stderr
```

`site/config.json` (read by Python **and** the frontend):

```json
{
  "site_name": "Crackspread",
  "site_url": "https://fixoa.github.io/crackspread/",
  "repo_url": "https://github.com/fixoa/crackspread",
  "language": "en",
  "languages_available": ["en", "de"],
  "show_language_toggle": false,
  "timezone_display": "Europe/Vienna",
  "donate_url": "",
  "donate_provider_label": "",
  "donate_cta_variant": 0,
  "ai_provider": "gemini",
  "ai_model": "",
  "stale_after_hours": { "prices": 96, "steo": 1080, "news": 24, "country": 9000, "summary": 48, "weekly": 264 },
  "balance_thresholds": { "deficit": 0.3, "surplus": -0.3 },
  "news_max_items": 40,
  "news_max_age_hours": 48,
  "news_queries": ["oil supply", "Strait of Hormuz", "\"crack spread\"", "diesel shortage", "Bab al-Mandab",
                   "refinery attack", "tanker rates VLCC", "OPEC output", "jet fuel prices", "strategic petroleum reserve"],
  "news_feeds": [
    {"id": "oilprice", "name": "OilPrice.com", "url": "https://oilprice.com/rss/main", "filter": false, "topics": ["supply"]},
    {"id": "rigzone", "name": "Rigzone", "url": "https://www.rigzone.com/news/rss/rigzone_latest.aspx", "filter": false},
    {"id": "gcaptain", "name": "gCaptain", "url": "https://gcaptain.com/feed/", "filter": true, "topics": ["shipping"]},
    {"id": "splash247", "name": "Splash247", "url": "https://splash247.com/feed/", "filter": true, "topics": ["shipping"]},
    {"id": "hellenic", "name": "Hellenic Shipping News", "url": "https://www.hellenicshippingnews.com/feed/", "filter": true, "topics": ["shipping"]},
    {"id": "offshore_energy", "name": "Offshore Energy", "url": "https://www.offshore-energy.biz/feed/", "filter": true},
    {"id": "eia_tie", "name": "EIA Today in Energy", "url": "https://www.eia.gov/rss/todayinenergy.xml", "filter": true, "topics": ["official"]},
    {"id": "eia_press", "name": "EIA Press", "url": "https://www.eia.gov/rss/press_rss.xml", "filter": true, "topics": ["official"]},
    {"id": "eia_gasdiesel", "name": "EIA Gasoline & Diesel Update", "url": "https://www.eia.gov/petroleum/gasdiesel/includes/gas_diesel_rss.xml", "filter": false, "topics": ["official", "retail"]},
    {"id": "bbc_business", "name": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "filter": true}
  ],
  "news_keywords": ["oil", "crude", "diesel", "gasoil", "refinery", "refineries", "tanker", "hormuz", "opec", "brent", "wti",
                    "lng", "shipping", "red sea", "bab al-mandab", "bab el-mandeb", "jet fuel", "petroleum", "gasoline", "barrel", "vlcc", "suez"],
  "news_topics": {
    "hormuz": ["hormuz", "iran", "gulf"], "supply": ["supply", "output", "production", "opec", "spr", "stockpile", "reserve"],
    "diesel": ["diesel", "gasoil", "distillate"], "refining": ["refiner", "refinery", "refineries", "crack spread", "margin"],
    "shipping": ["tanker", "vlcc", "shipping", "freight", "suez", "red sea", "mandab", "mandeb", "cape"],
    "prices": ["price", "brent", "wti", "futures", "$"], "jet": ["jet fuel", "airline", "aviation", "kerosene"],
    "official": [], "retail": ["pump", "retail", "gas station", "gallon"]
  },
  "update_schedule_utc": { "hours": [3, 10, 17], "minute": 17 },
  "crack_levels": { "labels": ["Hairline", "Visible", "Widening (said with a straight face)", "Gaping", "Grand Canyon"],
                    "percentile_cuts": [50, 75, 90, 98] },
  "inspiration": { "title": "The oil apocalypse is here", "author": "Max Fisher",
                   "url": "https://www.youtube.com/watch?v=OETnuwwsv9U", "date": "2026-10-02" }
}
```

The foundation also writes `scripts/schemas/config.schema.json` and `update.py` validates the config
at start-up.

## 2. Fetcher contract (`fetch_prices.py`, `fetch_balance.py`, `fetch_country.py`, `fetch_news.py`, `summarize.py`)

Every fetcher module defines these module constants and one function:

```python
SOURCE_KEY = "fred"          # key in meta.json["sources"]: fred | eia_steo | countries | news | summary | proposals
OUTPUT_FILE = "prices.json"  # file in site/data/
SCHEMA = "prices"            # scripts/schemas/<SCHEMA>.schema.json
STALE_KIND = "prices"        # key in cfg["stale_after_hours"]: prices | steo | country | news | summary

def run(cfg: dict, old: dict | None, *, fixtures: bool = False, now: "datetime | None" = None,
        session=None, env: "Mapping[str, str] | None" = None, context: "dict | None" = None) -> "tuple[dict, dict]":
    """Build and return (doc, info).

    doc  — the complete new document for OUTPUT_FILE (schema_version, generated_at, as_of, fetched_at,
           stale, ... per §3). NOT validated and NOT written here; update.py does both.
    info — small dict copied into meta.json["sources"][SOURCE_KEY]: e.g. {"via": "api"},
           {"items": 38, "feeds_failed": ["bbc_business"]}, {"provider": "none", "fallback": True},
           {"partial": ["retail_diesel_us"]}.
    old  — the previously written document (or None). A fetcher MAY carry individual sub-items
           forward from `old` when only part of the fetch failed (keep their old fetched_at,
           set their "stale": True, list them in info["partial"]). If nothing usable could be
           fetched, RAISE (any Exception) — update.py then keeps the old file and marks it stale.
    fixtures — read from tests/fixtures/ instead of the network (see §6). No network at all.
    now  — aware UTC datetime (defaults to common.now_utc()).
    env  — mapping for API keys (defaults to os.environ): EIA_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY.
    context — other documents from the same run, keyed by file name without extension
           ({"prices": {...}, "balance": {...}, "news": {...}}); used by summarize.py. May be None.
    """
```

`summarize.py` additionally exposes `run_proposals(cfg, old, *, fixtures, now, env, context) -> (doc, info)`
with `SOURCE_KEY="proposals"`, `OUTPUT_FILE="proposals.json"`, `SCHEMA="proposals"`, `STALE_KIND="summary"`
(constants `PROPOSALS_SOURCE_KEY`, `PROPOSALS_OUTPUT_FILE`, `PROPOSALS_SCHEMA`). `update.py` calls
`run()` then `run_proposals()` (the latter only when `ai_provider != "none"` and a key is present;
otherwise it writes `{"proposals": []}`).

Fetchers never print to stdout; they use `common.log`. They never call `sys.exit`.

`summarize.run` with provider `none` (config, `--no-ai`, missing key, provider failure or a
rejected reply) is the rule-based path; `--fixtures` without `CRACKSPREAD_LLM_FIXTURE` exercises
the same path. `info["reason"]` is a free-text diagnostic (`disabled | no_key | fixtures |
fixture_missing | no_news | rejected: <error>`); `meta.schema.json` allows extra keys.

## 3. Data files (`site/data/*.json`)

Common envelope on **every** file (also manual.json and proposals.json):

```json
{ "schema_version": 1, "generated_at": "2026-10-08T10:00:12Z", "as_of": "2026-10-06",
  "fetched_at": "2026-10-08T10:00:12Z", "stale": false, "source": "…", "source_url": "https://…" }
```

`as_of` is the data's reference time (prices: newest observation date; balance: STEO release
date; countries: fetch date of the annual data; news: newest `published`; summary: the newest
`as_of` of its inputs prices/balance/news; proposals: the news `as_of`; manual: `updated_at`;
summary/proposals fall back to `generated_at` only when no input carries an `as_of`). Time-based
staleness is computed by `update.py` from `as_of` via `common.apply_time_stale`. An unchanged
input therefore yields an unchanged `summary.json`/`proposals.json` (brief §3/§7: a data commit
only when something really changed); the empty proposals document written by `update.py` when
the AI is off additionally ignores `as_of` in its change check.

Every number shown in the UI comes from a **datapoint** object:
`{"value": 72.51, "unit": "USD/bbl", "as_of": "2026-10-06", "source": "FRED (EIA data), DDFUELNYH & DCOILBRENTEU", "source_url": "https://…", "fetched_at": "…", "stale": false}`
(plus optional `series_id`, `formula`, `inputs`, `note`).

### 3.1 prices.json

```
latest: { brent, wti, ulsd_nyh, gasoline_nyh, jet_gulf, heating_oil_nyh, retail_diesel_us, retail_gasoline_us,
          diesel_crack, gasoline_crack, jet_crack, crack_321, brent_wti_spread }   # each a datapoint; crude $/bbl, products $/gal, cracks $/bbl
   cracks carry "formula" (string from the brief) and "inputs" {"DDFUELNYH": 4.713, "DCOILBRENTEU": 125.44}
history: { "dates": [...], "diesel_crack": [...], "gasoline_crack": [...], "jet_crack": [...], "crack_321": [...],
           "brent": [...], "wti": [...] }   # aligned arrays, float|null; weekly (last trading day of each ISO week)
                                          # from 2006-06-14 up to 365 days before the newest observation, daily after
                                          # (anchored to the data, not the clock: byte-stable without new FRED data)
         + "resolution_note": "weekly before <date>, daily after", "daily_from": "<date>"
stats: { "diesel_crack": { "max": 116.5, "max_date": "2022-05-11", "mean_2015_2019": 16.0, "mean_prev_year": 29.0,
                           "prev_year": 2025, "percentile_now": 99.3, "level": 4, "level_label": "Grand Canyon",
                           "history_start": "2006-06-14", "n": 5100 },
         "brent_wti_spread": { "value": 29.2, "as_of": "2026-10-06", "mean_2015_2019": 3.9 } }
```
Cracks are computed only for dates where **all** inputs exist (FRED `.` **or an empty field** =
missing → skip; the current `fredgraph.csv` format has the header `observation_date,<ID>` and empty
fields, the legacy `DATE,<ID>` header with `.` is accepted too). The weekly retail series carry
`stale_kind: "weekly"` (`cfg.stale_after_hours.weekly`, 264 h) and `as_of` = the Monday survey date. Percentile
= share of daily history values (since 2006-06-14, full daily resolution, before downsampling) that
are ≤ the current value, in percent. Level from `cfg["crack_levels"]["percentile_cuts"]`:
`<50 → 0, 50–75 → 1, 75–90 → 2, 90–98 → 3, ≥98 → 4`. Plausibility: crude 5–400 $/bbl, products
0.3–15 $/gal; a violation raises `PlausibilityError` for that series (sub-item carried forward from
`old` as stale, listed in `info["partial"]`).

### 3.2 balance.json

```
source, source_url ("https://www.eia.gov/outlooks/steo/report/global_oil.php"), via: "api"|"xlsx",
steo_release: "2026-10-06", next_release: "2026-11-10"|null, steo_edition: "October 2026",
current_month: "2026-10",
months: [ { "period": "2026-09", "production": 101.30, "consumption": 104.24, "stock_draw": 2.94,
            "opec": 23.85, "nonopec": 77.45, "brent": 114.16, "wti": 97.31, "is_forecast": false }, … ]   # all months from 2024-01 to the end of the forecast, sorted by period; numbers float|null
quarters: [ { "period": "2026Q3", "stock_draw": 1.88, "production": …, "consumption": …, "is_forecast": false }, … ]   # means of complete quarters
current: { …the entry of current_month… },
quote: "…the sentence containing 'global oil inventories' from global_oil.php…" | null, quote_url: "https://www.eia.gov/outlooks/steo/report/global_oil.php",
status: "deficit"|"surplus"|"balanced",   # from current.stock_draw and cfg["balance_thresholds"]
unit: "million barrels per day"
```
`is_forecast` = period ≥ current month (UTC). STEO `value` strings → float; sort client-side.
Plausibility: production/consumption 70–130 mb/d (else raise). API path: one request with all seven
`facets[seriesId][]`, `start=2024-01`, `length=5000`. xlsx fallback: sheet `3atab`, column A ids
`papr_world`, `patc_world`, `t3_stchange_world`, `papr_opec`, `papr_nonopec` (first matching row),
months from the row with month names + the row above it holding the year at each January column;
`steo_release` from the "Forecast date:" cell or, if parsing fails, the first day of the current month,
with `info["release_date_estimated"] = True` **and** `release_date_estimated: true` in the document
itself (the frontend appends "release date estimated" to the hero's month line; brief §7: never an
unflagged estimate). The quote and the release dates come from global_oil.php (`Release Date:` /
`Next Release Date:` labels); if that page fails, `quote=null`.

### 3.3 countries.json

```
via: "eia_api"|"jodi",
unit: "thousand barrels per day",
producers: { "year": 2025, "source": "U.S. EIA International Energy Data", "source_url": "https://www.eia.gov/international/data/world",
             "rows": [ {"iso3": "USA", "name": "United States", "value": 23730.6}, … top 10 … ],
             "rest_of_world": 32000.0, "world_total": 106301.9, "note": "Total petroleum and other liquids" },
consumers: { same shape; "year": 2024 — use the latest year for which ≥ 150 country rows and a WORL row exist },
monthly_crude: { "source": "JODI Oil World Database", "source_url": "https://www.jodidata.org/oil/", "latest_month": "2026-07",
                 "unit": "thousand barrels per day", "rows": [ {"iso2": "SA", "name": "Saudi Arabia", "value": 8135.1}, … top 10 … ] } | null
```
EIA: production = `activityId=1, productId=53`; consumption = `activityId=2, productId=5` (53 returns
nothing for consumption!); `unit=TBPD`; filter `countryRegionTypeId == "c"` **and** reject ids that are
not 3-letter A–Z (regions like WORL, OPEC, EU27, WP16 are type "r"); values may be non-numeric
(`"ie"`, `"--"`, `"NA"`, `None`) → skip that row. JODI primary CSV columns
`REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE`; use
`CRUDEOIL,INDPROD,KBD`, `-` = missing (never 0), latest month = the newest TIME_PERIOD that has ≥ 30
countries. Name mapping iso2→name: small built-in dict for the top ~40 producers; unknown → iso2.
If the EIA API is unavailable (no key, 403, 429, error): `via="jodi"`, `producers` = JODI latest-month
crude production (note says "crude oil only, monthly, JODI"), `consumers` = carried from `old` (stale)
or `{"year": null, "rows": [], …}`.
**Staleness is per sub-object:** `producers`, `consumers` and `monthly_crude` each carry their own
`as_of`/`fetched_at`/`stale` (plus `carry_reason` when carried). In the `via="jodi"` path the
top-level `stale` stays `false` while `consumers.stale` is `true` and `meta.sources.countries.partial`
lists it; the frontend renders the STALE badge from the sub-object flags (OR-ed with the top-level
flag). Additive fields (allowed by the schema): top-level `year`, per-ranking `country_count`,
`producers.period` in jodi mode.

### 3.4 news.json

```
items: [ { "id": "<sha1 of normalised link>", "title": "…", "source": "Reuters", "link": "https://…",
           "published": "2026-10-08T01:25:00Z", "snippet": "≤ 240 chars, no HTML", "topics": ["hormuz", "supply"],
           "feed": "google:Strait of Hormuz" }, … ]   # newest first, ≤ cfg.news_max_items, ≤ cfg.news_max_age_hours old
feeds: [ { "id": "google:oil supply", "ok": true, "items": 57, "error": null }, … ]
```
Google News: `https://news.google.com/rss/search?q=<query>+when:1d&hl=en-US&gl=US&ceid=US:en`;
publisher from the `<source>` element; the link is a news.google.com redirect (keep it). Dedupe on
normalised title (lowercase, punctuation stripped, publisher suffix " - Reuters" removed) and on link;
among duplicates the earliest-published copy wins and the topics of the dropped copies are merged.
Links are normalised before hashing and stored normalised (scheme/host lowercased, fragment removed,
tracking params `utm_*`, `at_*`, `fbclid`, `gclid`, `mc_cid`, `mc_eid`, `igshid` stripped), so
`link` may differ slightly from the raw feed link. Keyword filter (`cfg.news_keywords`,
case-insensitive, word-boundary aware) is applied to the title plus the **full** stripped description
(the stored snippet is the 240-char cut of that text) and applies to feeds with `"filter": true`.
Topics via `cfg.news_topics`; feed-level `topics` are added. Snippets: strip HTML, unescape entities,
collapse whitespace, cut at 240 chars on a word boundary with "…". Items without a parseable date get
`published = fetched_at` and `"date_estimated": true`. Feed status objects additionally carry `kept`
(entries that passed filter + age window, pre-dedupe). Isolation is per entry as well as per feed: an
entry whose title/link/date cannot be processed (e.g. a link `urlsplit` rejects) is counted under
`dropped.unusable` and logged, the rest of the feed is kept.

### 3.5 summary.json

```
provider: "gemini"|"anthropic"|"none", model: "…"|"", fallback: bool,
headline: "≤ 90 chars", what_changed: [ {"text": "≤ 220 chars", "sources": ["https://…"]} ] (3–5), quips: ["…"] (0–2, no digits),
crack_o_meter: { "level": 3, "label": "Gaping", "percentile": 95.2, "value": 72.51, "unit": "USD/bbl", "as_of": "2026-10-06" },   # copied by CODE from prices.stats
deltas: { "diesel_crack": {"now": 72.51, "prev": 70.1, "delta": 2.41, "as_of": "…", "prev_as_of": "…"}, "brent": {…}, … },   # computed by code from context vs old prices
inputs_digest: "sha1 of the input JSON sent to the model, serialised without its generated_at stamp"
```
Validation (all must pass, else retry once with the error text, then provider `none`, then keep `old`):
whitespace normalisation (headline, texts, URLs and quips stripped, empty quips dropped) before the
JSON + schema check; every `sources` URL ∈ links of the news items in the input; number guard (every
number in headline + what_changed texts, normalised, must appear in the input within ±0.01 — a figure
is seen whatever precedes it (".5%", "approx.998", "x3"); ISO dates/timestamps on either side count
only as their year, so `published`/`as_of` stamps never whitelist day/hour/minute components, and
the day of a month mention ("Oct 6") is a date, not a figure; the site name counts as present if it
is in the input); length/count limits; quips contain no digits. An empty provider reply (Gemini
output budget spent on thinking) is re-asked once with the unchanged prompt. The rule-based
fallback builds 3–5 bullets only from real data (deltas + top headlines, each with its link, plain
titles since the frontend labels the links) and sets `fallback=true`, `provider="none"`; a stale
diesel crack is reported as "no fresh FRED data", never as "unchanged". With fewer than 3 usable
headlines the fallback raises, so `update.py` keeps the previous summary (stale) rather than
writing a thin one; `summary.schema.json` pins `minItems: 3`.

### 3.6 proposals.json

```
proposals: [ { "target": "shipping.vlcc_day_rate_usd.now", "proposed_value": 1500000, "unit": "USD/day",
               "quote": "exact substring of the snippet/title", "source_title": "…", "source_url": "https://…",
               "published": "…", "feed": "…" } ]   # may be empty
```
Allowed targets: the numeric leaves of `manual.json` (`shipping.voyage_days_now`,
`shipping.vlcc_day_rate_usd.now`, `shipping.shipping_cost_per_bbl_usd.now`, `hormuz_ledger[<id>].delta`,
`world.production_mbd`, `world.consumption_mbd`, `refinery_shock[*].diesel_delta_mbd`). Code checks that
`quote` is a verbatim substring of that item's title or snippet, contains at least one letter (a bare
digit is no quote) and contains the proposed number in the target's unit — one canonical value per
token: for USD/day targets a magnitude word is expanded ("1.2 million" / "1,200,000" / "$1.2m" →
1200000, never 1.2), for mb/d, days and USD/bbl targets the raw figure counts ("16.5 million barrels
per day" → 16.5, never 16500000); otherwise the proposal is dropped.

### 3.7 manual.json

Exactly the structure of brief §6.6 plus the common envelope (`schema_version`, `generated_at` =
`updated_at`, `as_of` = `updated_at`, `stale: false`, `source` = `default_source.name`,
`source_url` = `default_source.url`). Hand-edited; never written by scripts. Ledger `delta`/`value`
figures and `refinery_shock[*].diesel_delta_mbd` carry no `unit` field; they are mb/d by convention
and the frontend renders them as such.

### 3.8 meta.json

```
last_run, run_id ("<YYYYMMDD-HHMMSS>-<6 hex>"), duration_s, fixtures: bool, dry_run: bool, python: "3.12.x",
schedule: cfg.update_schedule_utc,
sources: { "fred": {"ok": true, "last_success": "…", "error": null, "stale": false, "changed": true, "partial": []},
           "eia_steo": {"ok": true, "via": "api", …}, "countries": {"ok": true, "via": "eia_api", …},
           "news": {"ok": true, "items": 38, "feeds_failed": ["bbc_business"], …},
           "summary": {"ok": true, "provider": "gemini", "model": "…", "fallback": false, …},
           "proposals": {"ok": true, "count": 0, …} }
```
`last_success` is carried over from the previous meta.json when a source fails. `stale` for a
source = the written document's top-level `stale`.

## 4. `scripts/update.py` (orchestrator)

CLI: `--dry-run` (fetch + validate + print a diff summary, write nothing — not even meta.json),
`--only <name>[,<name>…]` (prices|balance|countries|news|summary|proposals), `--fixtures`, `--no-ai`
(forces provider `none`), `--data-dir <path>` (default `site/data`, for tests).

Per source, in order prices → balance → countries → news → summary → proposals:

```
old = read_json(path)
try:
    doc, info = module.run(cfg, old, fixtures=…, now=now, context=context)
    apply_time_stale(doc, STALE_KIND, cfg, now)
    validate(doc, SCHEMA)
    changed = write_json_if_changed(path, doc)            # skipped in --dry-run
    context[name] = read_json(path) or doc
    meta.sources[key] = {ok: True, last_success: iso(now), error: None, stale: doc["stale"], changed, **info}
except Exception as e:                                    # a broken source never aborts the run
    log the traceback;
    if old is not None: mark_all_stale(old); write_json_atomic(path, old)   # fetched_at untouched; skipped in --dry-run
    context[name] = old
    meta.sources[key] = {ok: False, error: f"{type(e).__name__}: {e}"[:500], last_success: prev_meta.sources[key].last_success, stale: True, changed: False}
```
Then `build_meta.build(cfg, run_info, sources) -> dict`, validated against `meta.schema.json`, written
(always, unless dry-run). Exit code 0 unless the config/schemas cannot be loaded or an exception
escapes the orchestrator itself (exit 2). Each run logs one line per source:
`[10:00:14] prices: ok via fred (changed) as_of=2026-10-06` / `[…] balance: FAILED FetchError: … → kept old (stale)`.

## 5. Frontend contract

- Files: `site/index.html`, `site/css/style.css`, `site/js/app.js` (ES module), `site/js/charts.js`,
  `site/js/vendor/uPlot.iife.min.js` + `uPlot.min.css` (v1.6.32, MIT), `site/fonts/*.woff2` (local
  Permanent Marker — Apache 2.0, Caveat — OFL; license files alongside), `site/i18n/en.json`,
  `site/i18n/de.json`, `site/img/*.svg`, `site/img/og.png`, `site/favicon.svg`, `site/robots.txt`.
- Loads `./config.json`, `./i18n/<lang>.json` (+ `en.json` as fallback), then `./data/meta.json`
  (`cache: "no-cache"`) and the data files as `./data/<name>.json?v=<meta.run_id>` (a new run is
  fetched fresh, an unchanged one may come from cache; GitHub Pages ignores the query string; without
  meta all data files use `no-cache`). Each section renders independently; a missing/invalid file
  shows the section's "Data temporarily unavailable. The chart is fine, it's just shy." notice (from
  i18n) and never a placeholder number.
- Every number: `<data value="72.51">$72.51/bbl</data>` plus `<small class="src">FRED · as of Oct 6, 2026</small>`
  and a `<span class="badge stale">STALE</span>` when the datapoint, its sub-object (e.g.
  `countries.consumers.stale`) or its file is stale. Helper
  `fmt.num/money/date/relTime` in `app.js`; dates via `Intl.DateTimeFormat` in `config.timezone_display`.
- **No `innerHTML` with data.** Build nodes with `createElement`/`textContent`. External links get
  `target="_blank" rel="noopener noreferrer"`; only `http(s)` URLs are rendered as links.
- i18n: flat keys (`hero.title`, `crack.sub`, …) with `{placeholders}`; `t(key, vars)` falls back to
  `en.json`, then to the key itself. All microcopy from brief §9 lives in `en.json`, nothing in HTML/JS.
- No third-party requests at runtime except links the user clicks. No cookies, no storage, no analytics.

## 6. Fixtures and raw data

Raw downloads from 2026-10-08 live outside the repo in the session scratchpad
(`/private/tmp/claude-501/-Users-philippjovanoski-Desktop-crackspread/23cb6e8c-f27a-4d87-bd11-dcfaddac6404/scratchpad/raw/`):
`fred/<SERIES>.csv` (all 8 series, full history), `eia/STEO_m.xlsx`, `eia/global_oil.html`,
`eia/steo_api.json` (7 series, 2024-01…), `eia/intl_prod.json`, `eia/intl_cons.json`,
`eia/facet_activity.json`, `eia/facet_product.json`, `jodi_primary2026.csv` (5.5 MB),
`rss/*.xml` (14 feeds). Vendor files in `…/scratchpad/vendor/` (uPlot, fonts, licenses).

Committed fixtures (`tests/fixtures/`, keep the total under ~1.5 MB):

```
fred/DCOILBRENTEU.csv, DCOILWTICO.csv, DDFUELNYH.csv, DGASNYH.csv, DJFUELUSGULF.csv, DHOILNYH.csv, GASDESW.csv, GASREGW.csv
     (real rows from 2006-06-01 onward, including the empty-field gaps of the current FRED format and the 2022-05-11 peak; last row 2026-10-06 / 2026-10-05)
eia/steo_api.json          (the saved API response, unchanged)
eia/steo_3atab.xlsx        (a workbook with only the 3atab sheet, rows 0–40 and the 5 needed id rows, generated with openpyxl)
eia/global_oil.html        (trimmed to the <body> paragraphs containing the quote and the Release Date lines)
eia/intl_prod.json, eia/intl_cons.json   (saved responses, unchanged)
jodi/primary_excerpt.csv   (header + all CRUDEOIL/INDPROD/KBD rows for 2026-01…2026-07, nothing else)
rss/google_oil_supply.xml, rss/google_hormuz.xml, rss/google_crack_spread.xml, rss/oilprice.xml, rss/gcaptain.xml, rss/eia_tie.xml, rss/bbc_business.xml
llm/valid.json, llm/invalid_number.json, llm/invalid_link.json, llm/broken.txt, llm/proposals_valid.json
llm/input_example.json     (a real run context {prices, prices_old, balance, news} generated from the raw data; 23 KB)
```
In fixtures mode each fetcher reads exactly these files (`FIXTURES / "fred" / f"{series}.csv"`, …).
`fetch_news` in fixtures mode uses only the fixture feeds listed above (Google queries "oil supply",
"Strait of Hormuz", "\"crack spread\"" and the four named feeds). `summarize` in fixtures mode uses
provider `none` unless `env["CRACKSPREAD_LLM_FIXTURE"]` names a file in `llm/`, whose content is
returned as the model's reply (tests use this to exercise the validator).

## 7. Tests (pytest, offline, < 20 s)

`tests/conftest.py`: adds `scripts/` to `sys.path`, provides fixtures `cfg` (loaded config),
`fixtures_dir`, `tmp_data_dir` (empty tmp dir), `now` (2026-10-08T10:00:00Z), and a `no_network`
autouse fixture that monkeypatches `common.http_get` (plus `http_post`, `requests`, `urllib` and
`socket.connect`) to raise `AssertionError("network call in test")` unless a test opts in with the
`allow_network` marker (never used in CI). The block is installed once per session (so module-scoped
fixtures that run the fetchers are covered) and re-asserted per test.

Required tests (brief §15) and where they live: `test_prices.py` (formulas 72.51/17.19/64.83 ±0.01 on
2026-10-06, "." gaps skipped, lag/as_of, percentile/level, max 116.5 on 2022-05-11),
`test_balance.py` (Q3-2026 mean ≈ 1.88 ±0.02, is_forecast, status thresholds, xlsx fallback),
`test_country.py` (region filtering, non-numeric values, consumption year selection, JODI "-"),
`test_news.py` (dedupe, snippet ≤ 240 and no tags, date parsing, keyword filter, age cut-off),
`test_summary.py` (schema, foreign link rejected, invented number rejected, broken JSON → fallback,
quips without digits, crack_o_meter from code, proposals substring guard),
`test_stale.py` (fetcher raises → old file kept with stale=true, fetched_at unchanged, exit 0;
time-based stale; meta records the error), `test_schemas.py` (every `site/data/*.json` that exists
validates, which after a `--fixtures` or real run covers every fetcher's output; config validates;
CI runs pytest again after the fetch), `test_update.py` (`--fixtures` end-to-end into a tmp dir,
`--dry-run` writes nothing, `--only`), `test_frontend.py` (static checks of `site/`: i18n key parity
en/de, every literal `t()` key exists, asset references resolve, no `innerHTML`, section order,
brief microcopy present).

### 3.9 shipping.json (added 2026-10-09)

`fetch_shipping.py` (SOURCE_KEY `shipping`, STALE_KIND `shipping`, 240 h) — IMF PortWatch daily chokepoint transits
(ArcGIS feature service `Daily_Chokepoints_Data`, CC BY 4.0). Envelope + `unit`, `window_days` (7), `baseline_year`
(previous calendar year) and `chokepoints[]` = `{id, portid, name, latest_date, tankers_7d, tankers_baseline,
tankers_change_pct, total_7d, total_baseline, capacity_tanker_7d, series: {weeks[], tankers[]}}` for hormuz,
bab_el_mandeb, suez, cape, malacca. Missing data → null, never 0. Fixture: `tests/fixtures/portwatch/chokepoints.json`
(rows for the five chokepoints since 2025-01-01). Runs after countries, before news.
