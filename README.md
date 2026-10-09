# Crackspread

**Live:** https://fixoa.github.io/crackspread/

Crackspread is a free, ad-free, donation-funded website that explains the world oil market the way
you would on a whiteboard: how much oil is pumped, how much is burned, by whom, whether there is a
shortage or a surplus right now, what the **crack spreads** (refinery margins, diesel above all) are
doing, and what that means for trucks, groceries and flights.

It updates itself three times a day: fetch the data, write JSON, write a short "What changed today"
box, commit, redeploy. No server, no tracking, no cookies, no paywall.

The idea comes from Max Fisher's YouTube video
[*The oil apocalypse is here*](https://www.youtube.com/watch?v=OETnuwwsv9U) (2 Oct 2026), in which
he draws the global oil balance on a whiteboard. We link to him and credit him as the inspiration;
nothing from the video is copied. His whiteboard figures appear on the site only in a clearly
labelled explainer ("Max Fisher's estimate, not live data"), never as live data. The running gag is
his too: analysts say "crack spreads are widening" with a straight face. No, you don't have to blur
it, it's just a chart. **This is a clean show.**

Hard rules: no invented numbers; every figure shows its source and an "as of" time; a feed that
fails keeps its last value and is marked **stale**; official figures (EIA) first; the AI never
invents numbers and must cite links. Not financial advice. Not even dinner-party advice.

## Architecture in 10 lines

```
GitHub Actions (cron 3x/day UTC + workflow_dispatch + push to main)
  └─ python scripts/update.py                      orchestrator: per-source try/except, stale logic, meta
       ├─ fetch_prices.py   → site/data/prices.json     FRED CSV (no key) + crack-spread maths
       ├─ fetch_balance.py  → site/data/balance.json    EIA STEO API (EIA_API_KEY) or STEO_m.xlsx fallback
       ├─ fetch_country.py  → site/data/countries.json  EIA International API or JODI CSV fallback
       ├─ fetch_shipping.py → site/data/shipping.json   IMF PortWatch daily chokepoint transits (ArcGIS REST)
       ├─ fetch_news.py     → site/data/news.json       Google News RSS + publisher feeds
       ├─ summarize.py      → site/data/summary.json    LLM (gemini | anthropic) or rule-based "none"
       │                    → site/data/proposals.json  AI suggestions for manual.json, never auto-applied
       └─ build_meta.py     → site/data/meta.json       run id, status and stale flag per source
  └─ git commit "data: update YYYY-MM-DD HH:MM UTC" (only when a data file changed) → Pages deploy of site/
```

The site itself is plain HTML, CSS and vanilla JS: `site/index.html` loads `site/data/*.json` with
`fetch()` and renders every section independently, so one broken file never takes the page down.

## Run it locally

A stock macOS `python3` (3.9) is enough; CI runs 3.12 and the code supports both.

```bash
git clone https://github.com/fixoa/crackspread && cd crackspread
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export EIA_API_KEY=DEMO_KEY            # or a real key; DEMO_KEY allows only ~10 requests per hour
python scripts/update.py --fixtures     # offline, deterministic run from tests/fixtures/
python scripts/update.py --no-ai        # real data, rule-based summary (no AI key needed)
pytest -q
python -m http.server 8000 -d site      # → http://localhost:8000
```

`scripts/update.py` flags: `--dry-run` (fetch, validate, print what would change, write nothing),
`--only prices,news` (any of `prices|balance|countries|news|summary|proposals`), `--fixtures`,
`--no-ai`, `--data-dir PATH`. Without `EIA_API_KEY` the STEO comes from the public Excel file and
the country data from JODI. Tests never touch the network.

`requirements-dev.txt` adds Pillow for `tools/make_og.py`, which re-renders `site/img/og.png` from
the fonts in `site/fonts/`; it is not needed to run the fetcher or the tests, and CI does not install it.

## Data sources

| Source | Used for | Key | Licence / credit |
|---|---|---|---|
| [FRED](https://fred.stlouisfed.org/) series `DCOILBRENTEU`, `DCOILWTICO`, `DDFUELNYH`, `DGASNYH`, `DJFUELUSGULF`, `DHOILNYH`, `GASDESW`, `GASREGW` | crude and product prices, crack spreads, US retail diesel/gasoline | no | Public domain. "Source: FRED, Federal Reserve Bank of St. Louis; data: U.S. EIA" |
| [EIA Short-Term Energy Outlook](https://www.eia.gov/outlooks/steo/) (API v2 `steo`, fallback `STEO_m.xlsx`, quote from `global_oil.php`) | world production, consumption, inventory draw (the hero balance) | optional (`EIA_API_KEY`) | U.S. public domain. "Source: U.S. Energy Information Administration" |
| [EIA International Energy Data](https://www.eia.gov/international/data/world) | top producers and consumers by country | yes (`EIA_API_KEY`) | U.S. public domain, credit the EIA |
| [JODI Oil World Database](https://www.jodidata.org/oil/) | monthly crude production by country, fallback for the country ranking | no | Credit JODI |
| [IMF PortWatch](https://portwatch.imf.org/) | daily tanker/vessel transits through Hormuz, Bab el-Mandeb, Suez, the Cape and Malacca (AIS-based), 7-day average vs the previous year | no | CC BY 4.0, credit IMF PortWatch |
| [Our World in Data](https://ourworldindata.org/) | **not used** — candidate for a long-run context chart only (unit is TWh, not barrels, so it would need an "approx." conversion); nothing from it is in any schema or on the page | no | CC BY 4.0, attribution required if it is ever added |
| [OPEC MOMR](https://www.opec.org/opec_web/en/publications/338.htm) | link only | – | – |
| Google News RSS, OilPrice.com, Rigzone, gCaptain, Splash247, Hellenic Shipping News, Offshore Energy, EIA Today in Energy, EIA Press, EIA Gasoline & Diesel Update, BBC Business | headlines (title, source, link, date, snippet ≤ 240 chars; no full texts) | no | All rights remain with the publishers; we only link |
| `site/data/manual.json` | the "Whiteboard ledger": Hormuz flows, tanker rates, routes, things without a free feed | – | Hand-maintained; seeded with Max Fisher's estimates, labelled as such |

All requests use the User-Agent `crackspread-bot/1.0 (+https://github.com/fixoa/crackspread)`,
a 20 s timeout and bounded retries with backoff (3 attempts in total) plus a body-size bound per download. EIA/FRED data are U.S. public domain; the code is MIT
(see `LICENSE`, which also notes that the data files belong to their sources).

## AI summary providers

`site/config.json` → `ai_provider`:

| Provider | Secret | Cost | Notes |
|---|---|---|---|
| `gemini` (default) | `GEMINI_API_KEY` (Google AI Studio) | free tier | model from `ai_model`, default a current Flash model |
| `anthropic` | `ANTHROPIC_API_KEY` | pay-as-you-go, cents per month at 3 runs/day | model from `ai_model` |
| `none` | – | 0 | **rule-based fallback**: a template built only from the real deltas and the top headlines |

The model only ever sees the fetched numbers and headlines (no web access, no tools), must cite
links from `news.json`, and its answer is validated (schema, links, a number guard that rejects any
figure not present verbatim in the input). If validation fails twice the run falls back to `none`;
if even that fails the previous `summary.json` stays. Without any key the site works fully with
`none`; `--no-ai` forces it for one run. The AI's suggestions for the whiteboard go to
`proposals.json` only and are never applied automatically.

## Schedule

The workflow runs at `17 3,10,17 * * *` UTC (≈ 05:17, 12:17, 19:17 Vienna in summer; one hour
earlier in winter, which is fine) and on manual dispatch (with an optional "Skip AI summary" box).
A push to `main` that touches `site/**`, `scripts/**` or the workflow re-tests and redeploys without
fetching. The commit and the Pages deployment happen in the same workflow because a commit made
with `GITHUB_TOKEN` does not trigger other workflows.

**Note:** on public repositories GitHub disables scheduled workflows after 60 days without
repository activity. The data commits normally count as activity; should the schedule ever stop,
re-enable it under *Actions → update-and-deploy → Enable workflow*.

## Decisions

Short decisions worth knowing; later changes should extend this list.

- **Python 3.9 and 3.12** are both supported (stock macOS `python3` locally, 3.12 in CI): no `match`,
  no `X | Y` at runtime, `datetime.timezone.utc`, `from __future__ import annotations` everywhere.
- **Commits use the GitHub noreply identity** (`crackspread-bot <41898282+github-actions[bot]@users.noreply.github.com>`),
  message `data: update YYYY-MM-DD HH:MM UTC`.
- **Data files are rewritten only when their content changed.** `common.write_json_if_changed`
  compares with `generated_at`, `fetched_at`, `run_id` and `last_run` stripped; an unchanged file
  keeps its old timestamps, so re-runs are byte-stable and commits only happen for real changes.
- **`meta.json` is deployed from the working tree.** It changes every run (run id, duration), so the
  workflow commits only when something other than `site/data/meta.json` changed; otherwise it
  unstages and still deploys the working tree, including the fresh `meta.json`.
- **A broken source never aborts a run.** `update.py` wraps each source in its own try/except,
  keeps the old file, marks it stale (`fetched_at` untouched) and records the error and the carried
  `last_success` in `meta.json`. Exit code 0 unless the config/schemas cannot be loaded or the
  orchestrator itself crashes (exit 2).
- **Fetchers are loaded by name** (`importlib`, table in `scripts/update.py`), so a missing or
  broken fetcher module counts as that source failing, not as a crash, and tests inject fakes via
  `sys.modules`.
- **`proposals.json` when the AI is off** (provider `none`, no key, or `--fixtures` without
  `CRACKSPREAD_LLM_FIXTURE`): `update.py` writes an empty proposals document itself; for that
  document `as_of` is also ignored by the change check so it stays byte-stable between runs.
- **`--only` runs** keep the previous `meta.json` status of the sources that were not run (marked
  `"skipped": true`), and the context handed to later sources is pre-filled from the files on disk
  (plus `manual.json`), so `--only summary` works on its own.
- **`--fixtures`** sets `CRACKSPREAD_NOW=2026-10-08T10:00:00Z` for the duration of the run only
  (unless already set) and never touches the network.
- **CI runs `pytest` before and after the fetch**: the first pass catches code regressions before
  any API quota is spent, the second validates the freshly written data files against their schemas.
- **`GITHUB_TOKEN` scopes are granted per job** (no workflow-level `permissions` block): `update`
  gets `contents: write` only (the data commit); `deploy` gets `pages: write` + `id-token: write`
  (+ `contents: read`) and runs `configure-pages` and `deploy-pages`. The job that runs
  `pip install` and parses 20 third-party feeds can therefore neither mint an OIDC token nor
  deploy, and the deploy job cannot push.
- The EIA `DEMO_KEY` (≈ 10 requests/hour) is never used in tests; tests run offline on fixtures.
- `update.py` passes `env=os.environ` (or the mapping given to `RunOptions.env`) to every fetcher, and the context handed to fetchers is pre-filled from the files on disk for all six sources plus `manual.json`, then replaced per source as the run progresses (in `--dry-run` the in-memory new document is used).
- `update.py` fills `count` (proposals) and `items` (news) in the meta entry when the fetcher's info omits them; info keys override the base keys.
- The workflow's `no_ai` dispatch input is passed through an env var and a shell `if`, never interpolated as an expression into the command.
- Test fixtures use only real observations (FRED 2026-10-06 rows, STEO October 2026 API values, EIA International 2024/2025 rows, JODI 2026-07, two real EIA Today-in-Energy items); unknown derived figures are left null rather than invented.
- **All HTTP goes through `common`:** `common.http_get` (GET) and `common.http_post` (POST with a JSON body, 60 s timeout, 2 attempts, retries also on 429, masked error texts with a short excerpt of the API's error body); `summarize.py`'s LLM calls use the latter, so no module imports `requests` itself and the test guard blocks both.
- **`tests/test_frontend.py`** keeps `site/` consistent without a browser: en/de i18n key parity, every literal `t()` key exists, the brief's microcopy is present, asset references resolve, fonts are local with `font-display: swap`, no `innerHTML`, sections are labelled and in the brief's order, `robots.txt` hides the raw JSON.
- **Initial data files** (the first commit): every file in `site/data/` except the hand-maintained
  `manual.json` is the output of `update.py --fixtures --no-ai` on 2026-10-08, i.e. built offline from
  the raw downloads saved that day (FRED CSVs, the EIA STEO and International API responses, JODI,
  seven RSS feeds). Every number and headline in them is real; only the timestamps are the fixture
  clock (`generated_at`/`fetched_at` = `2026-10-08T10:00:00Z`, `meta.json` says `"fixtures": true`),
  and the rule-based summary cites the committed `news.json`. No AI key was used and the EIA
  `DEMO_KEY` quota was reserved for the first real run. The first workflow run (checklist step 7)
  fetches everything live under your own keys; note that an unchanged file keeps its old timestamps
  by design (`write_json_if_changed`), so the synthetic clock on a file disappears with its first
  content change (prices and news change on every run; `balance.json`/`countries.json` wait for the
  next STEO or EIA update) unless the files are deleted before that run.
- Integration check on 2026-10-08: `update.py --fixtures --no-ai` is byte-stable from the second run on (only `meta.json` and, once, the summary deltas change), a live `--dry-run --no-ai` without `EIA_API_KEY` hit FRED, the STEO xlsx, global_oil.php, JODI and all 20 feeds (31 requests, none to api.eia.gov) with every source ok, and a simulated FRED outage kept `prices.json` with every datapoint `stale: true`, `fetched_at` untouched and exit code 0.

### Prices (`fetch_prices.py`)

- FRED's current `fredgraph.csv` format uses the header `observation_date,<ID>` and EMPTY fields for missing days (the raw downloads contain no literal `.`); `parse_fred_csv` treats both `.` and empty as missing and accepts the legacy `DATE,<ID>` header. Fixtures keep the real rows exactly as downloaded (from 2006-06-01 on, 572 KB total).
- Rounding: every $ value (crude, products, cracks, spread, stats) is rounded to 3 decimals, lossless for FRED's 2-/3-decimal inputs and byte-stable (the diesel crack is stored as 72.506; the brief's 72.51 is the 2-decimal display); `percentile_now` is rounded to 1 decimal and the level is derived from that stored rounded value so the JSON is self-consistent.
- Plausibility (crude 5–400 $/bbl, products 0.3–15 $/gal) is checked on the latest observation only; a violation discards that series for the whole run (incl. its history contribution) and carries the old sub-item forward as stale. History values are deliberately not range-checked because WTI really closed negative in April 2020.
- If any of the five daily chart inputs (Brent, WTI, ULSD, gasoline, jet) fails, `history` and `stats` are carried forward wholesale from `old` (flagged `"stale": true` inside and listed as `history`/`stats` in `info["partial"]`) instead of publishing null columns; without an old document they are built from what was fetched, with null columns for the missing series.
- Weekly downsampling picks, per ISO week before now−365 d, the last trading day among those with the most series present, so a US-holiday row where only Brent trades (e.g. 2026-07-03) does not blank the week's crack point. The history axis is floored at 2006-06-14 (first ULSD observation) in live mode too, although the crude series go back to 1986.
- Carried-forward latest items get `"carried_forward": true`, keep their old `fetched_at` and get `stale=true`. A failed series with no previous value becomes a value-null datapoint (`as_of` = run date, `stale=true`, note) so the schema-required shape of `latest` is kept; the run raises `FetchError` only when none of the 8 series could be fetched.
- Weekly retail series (`GASDESW`/`GASREGW`) carry `as_of` = the Monday survey date, `stale_kind="weekly"` (config threshold 264 h, honoured by `common.apply_time_stale`) and an explanatory note.
- Stats are computed for all four cracks (max, max_date, mean_2015_2019, mean_prev_year, prev_year, percentile_now, level, level_label, history_start, n, plus value/as_of/note for `summarize.py`'s crack-o-meter) and for `brent_wti_spread` (value, as_of, mean_2015_2019, mean_prev_year, prev_year, n). `info = {"via": "fred", "partial": [...]}` plus `"errors": {key: message}` when something failed.
- Real-data finding: the current diesel crack (72.506 $/bbl, 2026-10-06) is at the 97.9th percentile of daily values since 2006-06-14 (n=5046) → level 3 "Gaping", just below the 98 cut; 2015–2019 mean 15.991, 2025 mean 28.87, max 116.538 on 2022-05-11; Brent–WTI spread 29.2 vs a 2015–2019 mean of 4.183. Tests assert the brief's reference numbers (72.51/17.19/64.83 ±0.01, 116.5, ≈16, ≈29) but do not pin the level, which is whatever the data says.
- History: 1266 points (weekly 2006-06-15 → 2025-10-03, daily from 2025-10-06), `resolution_note` "weekly before 2025-10-06, daily after" plus a machine-readable `daily_from`; the daily window is anchored to the newest observation (2026-10-06 − 365 d), not to the clock, so the file is byte-stable on days without new FRED data; `prices.json` is 105 KB (< 300 KB).

### Balance (`fetch_balance.py`)

- `steo_release` = the "Release Date:" of `global_oil.php` (2026-10-06), not the xlsx "Forecast date:" (2026-10-01, the forecast-completed date); the latter is only the fallback when the page fails. Both dates are kept: `forecast_completed` is an extra nullable field.
- If the page fails on the API path, `steo_release` is estimated as the first day of the current month with `info.release_date_estimated=true`, `release_date_estimated: true` in balance.json itself (the hero's month line then says "release date estimated") and `steo_edition=null` (no edition name is guessed from an estimate); on the xlsx path the sheet's "Forecast date:" cell and "October 2026" header are used instead. The xlsx download is bounded to 25 MB and the `3atab` sheet is read up to 2,000 rows (it has ~100).
- `steo_edition` is derived from the release month ("October 2026"); the xlsx header is used when available. `source` = "EIA Short-Term Energy Outlook (STEO), <edition>".
- Brent/WTI are null on the xlsx path: sheet `3atab` has no price rows and the contract limits the fallback to that sheet.
- All months (2024-01 onward) are rounded to 2 decimals (mb/d and STEO monthly prices; −0.0 normalised to 0.0); quarter means are computed from the unrounded values of complete quarters only. Quarter `is_forecast` = any member month is a forecast.
- Plausibility 70–130 mb/d is enforced on every month's production/consumption (not just the current month) and raises `PlausibilityError`, so `update.py` keeps the old file as stale.
- `build_doc` raises `ValueError` when the STEO data does not cover the current month or the current month has no stock-change value (nothing usable for the hero); nothing is carried forward from `old` because the whole document is rebuilt every run.
- In fixtures mode env `CRACKSPREAD_STEO_VIA=xlsx` selects the xlsx fixture; env defaults to `os.environ`, so `update.py --fixtures` can exercise both paths.
- The quote parser treats block-level tags as the only paragraph boundaries, so source line breaks inside a `<p>` and entities/tags are tolerated; it returns the first sentence containing "global oil inventories" (the fixture page contains the phrase twice; the first is the 1.9 mb/d sentence).
- The single permitted live fetch of `STEO_m.xlsx` + `global_oil.php` (no key, api.eia.gov guarded) produced numbers, quote and dates identical to the fixtures.
- Extra fields beyond the contract (schema allows them): `data_url`, `forecast_completed`, `series_ids`, `notes`. info keys: `via`, `release_date_estimated`, `quote_found`, `months`, plus `api_error`/`page_error` when a fallback happened (API key redacted).

### Countries (`fetch_country.py`)

- Year selection per ranking = newest year that has a numeric WORL row AND ≥ 150 numeric country rows; a country row is `countryRegionTypeId == "c"` AND id matching `^[A-Z]{3}$` (drops WORL/OPEC/EU27/WP16/ASOC and the type-"c" pseudo-countries DEUW/HITZ/NLDA/USIQ); non-numeric values ("ie", "--", "NA", None, NaN) are skipped and counted. With the 2026-10-08 data this gives producers 2025 (USA 23730.6, SAU 11214.0, world 106301.9) and consumers 2024 (USA 20463.7, CHN 16370.5, world 103110.2); 2025 consumption is rejected (36 rows, no WORL).
- `rest_of_world` is computed from the rounded published numbers (world_total − sum of the 10 rounded rows), so `sum(rows) + rest_of_world == world_total` holds exactly inside the file; all kb/d values are rounded to 1 decimal for byte-stable re-runs.
- EIA: two requests (production activityId=1/productId=53, consumption 2/5), unit TBPD, frequency annual, start = now.year − 3, length=5000, api_key passed via params (never in the URL). If the first EIA request fails the second is skipped to save DEMO_KEY quota. HTTP errors, non-JSON bodies and `{"error": ...}` payloads all become `FetchError` → JODI fallback; a key echoed in an error text is masked before it reaches meta.json.
- JODI: the CSV is parsed streaming with `csv.reader` over `resp.iter_lines` (64 KiB chunks) or an open fixture file; only CRUDEOIL/INDPROD/KBD rows with an ISO2 area and YYYY-MM period are kept. "-" (and "x") are dropped, never 0; a literal "0.0000" is kept as a real reported zero. `latest_month` = newest TIME_PERIOD with ≥ 30 numeric countries (2026-07 with 47 in the fixture). Live mode tries `primaryyear<YYYY>.csv` first and then `<YYYY-1>`, because the new year's file is missing/empty until ~March.
- iso2 → (iso3, name) is a built-in table of ~110 ISO 3166 codes (all JODI reporters + major producers, EIA spelling e.g. "Turkiye"); JODI rows therefore carry iso2, iso3 and name; unknown codes use the iso2 code as the name.
- Fallback `via="jodi"` (no `EIA_API_KEY`, any EIA error, no usable year, or `CRACKSPREAD_COUNTRIES_VIA=jodi`, honoured in fixtures AND live mode): producers = JODI latest-month top 10 with year=2026, period="2026-07", world_total/rest_of_world = null and note "crude oil only, monthly, JODI (...)"; consumers = deepcopy of `old["consumers"]` with stale=true and carry_reason (old as_of/fetched_at untouched, listed in `info["partial"]`) or `{"year": null, "rows": []}`. If only the consumption request fails, via stays "eia_api" and consumers are carried the same way. If JODI fails, `monthly_crude` is carried from old (stale, partial) or null. Both sources dead → raises `FetchError` so `update.py` keeps the old file.
- `as_of` (top level and per ranking) = fetch date, as the contract pins it; each ranking and the monthly block carry their own as_of/fetched_at/stale so `common.mark_all_stale` and the frontend can flag them individually. The fetcher never sets top-level stale=true itself (a successful JODI fallback is a valid operating mode); staleness of carried parts lives on the sub-objects and in `info.partial`.
- Additive fields beyond the contract shape (schema has additionalProperties: true): top-level `year`, per-ranking `country_count`/`as_of`/`fetched_at`/`stale`/`carry_reason`, `producers.period` in jodi mode. info = {via, partial, producers_year, consumers_year, jodi_month, eia_error?, jodi_error?}.
- Fixtures: `intl_prod.json` and `intl_cons.json` are byte-identical copies of the saved responses (cmp-verified); `jodi/primary_excerpt.csv` is the header plus the 672 CRUDEOIL/INDPROD/KBD rows copied byte-for-byte (CRLF, 292 "-" gaps kept). The streaming parser gives the identical table on the full 5.5 MB raw file (0.06 s).

### News (`fetch_news.py`)

- Google News items: the publisher comes from the `<source>` element and is stored in `source`; the trailing " - <Publisher>" is stripped from the stored title when it matches that source exactly (also for sources containing "|" or "-"). Publisher feeds use the feed name as `source`.
- Google descriptions are only `<a>Title</a> <font>Publisher</font>`; a snippet that merely repeats the title (+ publisher) is dropped, so Google items have an empty snippet rather than a duplicated headline.
- Dedupe key = normalised title (publisher suffix removed, lowercase, accents/punctuation stripped) and the normalised link. Among duplicates the EARLIEST-published copy wins (usually the original report, e.g. Reuters rather than a syndicating paper); topics of the dropped copies are merged into it.
- Links are normalised before hashing and stored normalised: scheme/host lowercased, fragment removed, tracking params stripped (utm_*, at_* as used by BBC, fbclid, gclid, mc_cid, mc_eid, igshid); everything else (Google's `?oc=5`) is kept verbatim. `id = sha1(normalised link)`.
- Keyword filter (feeds with `"filter": true`) and topic mapping are word-boundary aware with a small suffix tolerance (tanker→tankers, iran→iranian, refiner→refineries): "oil" no longer matches "turmoil"/"spoil". "$" and other symbol keywords skip the boundary on that side. The filter looks at the title plus the FULL stripped description, not the 240-char cut (an EIA story that only mentions "heating oil" at the end of its summary is kept).
- Snippets: html.parser-based stripper (block tags become spaces, script/style dropped) + `html.unescape` (handles double-encoded entities) + whitespace/NBSP collapse, cut at ≤ 240 chars on a word boundary with "…". Titles go through the same stripper.
- Dates: feedparser's published_parsed/updated_parsed/created_parsed (UTC structs) first, then dateutil on the raw strings; otherwise `published = fetched_at` and `date_estimated = true`. EIA's "EST" stamps become UTC (07 Oct 09:00 EST → 14:00Z).
- Age window: only items older than `cfg.news_max_age_hours` are dropped; items with a timestamp after `now` are kept (fixture feeds were captured ~80 min after the fixed fixture `now`, so `as_of` in fixture mode is 11:07:46Z, i.e. slightly "in the future", harmless, not stale).
- A feed whose body is not a feed at all (parser error AND zero entries, e.g. an HTML error page or the saved "Redirecting..." body of offshore-energy) counts as failed; a feed with parser warnings but entries is ok (logged). The HTTP content-type is NOT forwarded to feedparser (it produced spurious "not an XML media type" warnings); encoding comes from the XML declaration / byte sniffing.
- Feed status entries carry an extra `kept` count (entries that passed filter + age window, pre-dedupe) next to `items` (raw entries). info carries items, feeds_failed, feeds_ok, candidates and a dropped{unusable, filtered, too_old, duplicates, capped} breakdown; the document carries `window_hours`. All allowed by the schemas (additionalProperties true).
- `old`, `env`, `context` are accepted but unused: failed feeds are simply omitted (nothing is carried forward); when every feed fails `run()` raises `common.FetchError` so `update.py` keeps the old file as stale. No feeds configured → `ValueError`.
- Topics are emitted in `cfg.news_topics` key order (byte-stable output); feed-level topics are appended; "official" only ever comes from feed-level topics (its keyword list is empty).
- Fixture mode is config-driven: only the queries/feed ids that have a file in `tests/fixtures/rss/` are used (3 Google queries + oilprice, gcaptain, eia_tie, bbc_business). The fixture files are byte-for-byte copies of the raw downloads except `bbc_business.xml`, trimmed to 15 real items (13 current incl. oil-related and unrelated ones, plus two old oil/diesel items from 23 Sep and 2 Oct for the age cut-off tests). Total 348 KB.
- Tests inject fake feeds by monkeypatching `common.http_get` (the module always calls it via the module attribute, as conftest requires); fixture-mode runs are also asserted to make no `http_get` call.

### Summary and proposals (`summarize.py`)

- Default models: Gemini `gemini-3.8-flash` (checked 2026-10-08 at https://ai.google.dev/gemini-api/docs/models; 2.5 generation listed there as capacity-limited) and Anthropic `claude-haiku-5-5` (checked 2026-10-08 at https://platform.claude.com/docs/en/about-claude/model-deprecations; `claude-haiku-4-5` may retire from 2026-10-15). Both via plain `requests` REST, no SDK; Gemini uses `generationConfig.responseMimeType=application/json`, `maxOutputTokens` 8192 (Gemini 3 counts its thought tokens against the cap), `thinkingConfig.thinkingLevel=low` and the default temperature (Google advises against lowering it on Gemini 3; a `gemini-2*` model in `ai_model` gets the old `temperature 0.2` and no thinking config instead), key in the `x-goog-api-key` header; Anthropic uses the Messages API with `anthropic-version: 2023-06-01`, no assistant prefill. Keys travel only in headers, never in URLs or logs. When a model gets retired, set `ai_model` in `site/config.json` to the current one.
- Provider resolution (`summarize.select_provider`): ai_provider none / `--no-ai` / missing key / unknown provider / provider exception / rejected reply all end in provider "none" with `fallback=true`, so the frontend's "summarizer is on a coffee break" hint appears whenever the text is rule-based. info carries provider, model, fallback, attempts and a reason (disabled | no_key | fixtures | fixture_missing | no_news | rejected: <error>).
- Validation chain = JSON parse (tolerates ```json fences / prose around the object) → whitespace normalisation (headline, texts, URLs, quips stripped; empty quips dropped) → inline reply schema (3–5 bullets, lengths, quips digit-free) → link whitelist (exact match against the input news links) → number guard → explicit limits. Number guard: every number in headline + bullet texts (URLs stripped) must match a number of the input within ±0.01, so 72.51 for a stored 72.506 passes but 72.6 does not; a figure is seen whatever precedes it (".5%", "approx.998", "x3", "1e6"); magnitude words count both ways ("1.9 million" = 1.9 and 1900000); "mb/d"/"b/d" are units, not magnitudes; the input whitelist excludes link/url/id fields (Google News URLs are digit soup); ISO dates and timestamps on either side count only as their year (so the `published`/`as_of` stamps never whitelist day/hour/minute components such as "45 tankers"), and the day of a month mention ("Oct 6") is a date, not a figure. One retry with the validator error appended to the user message (an empty provider reply — e.g. Gemini's output budget spent on thinking — is re-asked once unchanged), then the rule-based fallback, then raise only if the fallback has fewer than 3 headlines.
- Model input (< 8k tokens): site name, generated_at, prices.latest (value/unit/as_of + prev/prev_as_of/delta), diesel-crack stats, crack_o_meter, STEO current + previous month, last 2 quarters, status and quote, and ≤ 30 news items (title, source, link, published, snippet); items are dropped from the end until the compact JSON is ≤ 28,000 chars. `inputs_digest` = sha1 of that input serialised without its `generated_at` stamp, so an unchanged input gives an unchanged digest.
- Deltas are computed in code for all 13 `prices.latest` series plus steo_production/steo_consumption/steo_stock_draw: prev from `context["prices_old"]` when a caller supplies it, otherwise from the previous summary.json `deltas[key].now` (the path `update.py` exercises); nothing to compare → prev=null. crack_o_meter is copied from `prices.stats.diesel_crack` (level recomputed from the percentile only if the stats lack it); the model's own crack_o_meter (valid.json deliberately claims level 0) is ignored.
- Rule-based fallback: headline = diesel-crack delta since last update ("unchanged" for |Δ| < 0.005, "no fresh FRED data, last value …" when the diesel crack is stale, or the current value if there is no prev), formatted like the rest of the page (`$72.51/bbl (as of 2026-10-06)`); bullets = newest headlines as plain titles (the frontend labels each cite link with its publisher), at most 2 per publisher — lifted when fewer than 3 publishers are available — each with its own link, no quips. Fewer than 3 usable headlines → the fallback raises and `update.py` keeps the previous summary.json as stale (`summary.schema.json` pins `minItems: 3`). The fallback output passes the same validator as a model reply (tested). If the input has no news items the model is not called at all (it could not cite a link) and the fallback is used.
- Proposals (`run_proposals`): second call with a short English system prompt listing the allowed targets built from manual.json (`context["manual"]` or `site/data/manual.json`; hormuz_ledger rows without a `delta` key such as subtotals are excluded) and the same news list. Code guard: target must match the schema regex and exist in the allowed list, source_url ∈ input news links, quote must be a verbatim substring of that item's title or snippet, and the proposed number (value or its absolute value, ±0.01; ledger deltas are signed by convention while articles are not) must occur in the quote. source_title, published, feed and unit are filled by code from the matched item, never from the model. Fixture mode honours `CRACKSPREAD_LLM_PROPOSALS_FIXTURE`, falling back to `CRACKSPREAD_LLM_FIXTURE`.
- Fixtures: `input_example.json` is a real run context {prices, prices_old, balance, news} generated from the raw downloads (FRED 2026-10-06 vs 2026-10-05: diesel crack 72.506 ← 68.866, Δ 3.64; percentile 97.92 → level 3 "Gaping"; max 116.538 on 2022-05-11; STEO Oct-2026 102.10/102.78/0.67, 3Q26 mean 1.88; the verbatim EIA quote; 14 real headlines with their links/ids/snippets). valid/invalid_number/invalid_link/broken/proposals_valid are derived from it, so every link and number in them is real; the only invented items are the deliberately foreign URL https://example.com/... and the invented figure $112.
- summary.json takes `as_of` from its inputs (newest of prices/balance/news `as_of`) and proposals.json from the news, and the digest ignores the run stamp, so an unchanged input gives a byte-identical file and no data commit (tested end-to-end: `test_cli_subprocess_fixtures_runs_are_byte_stable`). The run after a price change still rewrites summary.json once (the "since last update" delta settles to 0), which is a real content change.

### Frontend (`site/`)

- 2026-10-09 redesign: the whiteboard look (grid paper, marker/hand fonts, sketchy SVGs, stickies, gauge) was replaced by a plain single-column page: one system sans typeface (Permanent Marker survives only in the wordmark), white background, a table of contents under the masthead, and one device for every figure — a label/value row with its source and date underneath (`row()` in `app.js`). Copy was rewritten in plain language; the brief's running gags remain only in the crack-level labels, the donate texts and the disclaimer.
- The shipping map (`img/route-map.svg`) is generated by `tools/make_map.py` from real coastlines (world.geo.json, public domain) with the direct Hormuz route and the Suez–Cape detour drawn on top; the voyage days in its legend come from `manual.json`. Regenerate with `python tools/make_map.py <countries.geo.json>`.
- Tanker traffic (`fetch_shipping.py`): IMF PortWatch's `Daily_Chokepoints_Data` feature service is queried for five chokepoints since 1 January of the previous year (paged by `resultOffset`); the frontend shows the last 7 days' daily tanker average against the previous year's daily average. Days without data are absent from the averages, never zero. Stale after 240 h (PortWatch publishes with a few days' lag).
- Earlier notes below describe the first whiteboard build where they conflict with the above.

- Chart library: uPlot 1.6.32 (IIFE, classic defer script loaded before the ES module) is used only for the diesel-crack history (1Y/5Y/Max, 2015–19 mean as dashed series, 2022 record drawn in a draw hook so it shows even though 2022-05-11 is not a weekly sample point, `<details>` "Show data" table with the last 60 points). Everything else (balance scale, half-circle gauge, country/product bars, Hormuz ledger, shipping route schematic) is inline SVG built in `charts.js` with a seeded wobble so re-renders are stable.
- OG image: `site/img/og.png` is rendered by `tools/make_og.py` with Pillow (dev-only, declared in `requirements-dev.txt`; CI never installs it) from the woff2 fonts the site already ships in `site/fonts/` (Pillow's FreeType reads WOFF2, and the Caveat file carries the weight axis), so nothing outside the repository is needed. Static whiteboard style without any data numbers, 85 KB; the wobble is seeded, so `python tools/make_og.py` reproduces the committed file byte for byte.
- Data loading/caching: `meta.json` is fetched with `cache: 'no-cache'`; data files are fetched as `./data/<name>.json?v=<meta.run_id>` so a new run is fetched fresh and an unchanged one comes from cache (a plain fetch was served from the heuristic cache in dev). Without meta all data files use no-cache. GitHub Pages ignores the query string.
- Gauge: the percentile is mapped piecewise-linearly onto five equal slices with the config percentile cuts (50/75/90/98) as tick labels, so the top levels stay readable; the five level labels are shown as a list with the current one ticked. Needle transition is CSS only and disabled under `prefers-reduced-motion`.
- Hero: the beam tilts a fixed 5° toward the heavier side (direction only, never proportional) and the bars rise from zero; the H1 number is `Math.round(balance.current.consumption)`. Gap convention: `stock_draw` is EIA `T3_STCHANGE_WORLD` (positive = inventories drawn). The SHORTAGE/SURPLUS row pairs its directional label ("drawn from" / "added to storage every day") with the magnitude, the BALANCED row and the beam figure show the signed change in storage (+ = added, i.e. −stock_draw), and `<data value>` always equals the number displayed.
- Whiteboard: ledger rows are drawn line by line via IntersectionObserver + stagger with a "Draw it again" button; skipped entirely under `prefers-reduced-motion`. Rows with status `knocked_out` or `counted:false` are struck through and get "Was {delta}. Then drones happened." from data. "Official view" uses the latest non-forecast quarter in `balance.quarters` (2026Q3 → 1.88) vs |net_shortfall.value| (7). Ledger units are assumed to be mb/d (manual.json carries none).
- Diesel-math box: "Oil up {oil}%. Diesel up {diesel}%." prints Fisher's own stated figures from `manual.diesel_math.stated_rise_pct` (60 / 250, the same claim as the note "60% oil rise → ~250% diesel rise" shown beneath it), never a frontend derivation from the $65→$100 / $85→$210 table (which would read 54 % / 147 % and contradict the note on the same card). Nothing in the frontend computes percentages.
- Crack-o-meter statistics (record, 2015–19 and previous-year averages, percentile) are `<data>` elements with a FRED source line (as of the stats date); the history chart, its legend and the "Show data" table carry their own source line (as of the newest history date) and a STALE badge when prices.json is stale.
- Proposals: rendered only as "per news reports: value (source, date)" footnotes under the matching ledger row, VLCC rate, voyage days, cost per barrel, refinery_shock row or world line, never replacing ledger values.
- Fisher's $110 benchmark note lives in Methodology and is templated from data ({fisher} = manual.diesel_math.now.crack, {ours}/{max}/{max_date} from prices.json).
- "Updated HH:MM Vienna time · next update ~HH:MM": next = first slot of `config.update_schedule_utc` after `meta.last_run`; both formatted with Intl in `config.timezone_display`; the city name is derived from the timezone id.
- i18n: flat keys, `t()`/`tf()` fall back de → en → key; language picked from `?lang=` (no storage) and the toggle is hidden unless `config.show_language_toggle`; `de.json` has identical keys with empty strings. While a language file is an all-empty stub the page is treated as English throughout (number/date locale and `<html lang>` follow the strings actually shown, so `?lang=de` does not produce "−0,67 mb/d" inside English sentences). Gauge labels exist both in `en.json` (`crack.level.N`) and `config.crack_levels.labels` and are verified identical.
- Footer: the sources & licences lines link to the source home pages from `config.source_links` (FRED, EIA STEO, EIA International, JODI) and to the video for the whiteboard line; the Max Fisher credit date is formatted day-first ("2 Oct 2026", as in the brief) independent of the display locale.
- News summary bullets: the rule-based fallback ends each bullet with "(Publisher)"; the frontend drops that suffix when the cite link right after it already carries the same publisher name.
- Colours: `--muted` (#6B6B66) and the text `--green` (#277A4B) are darker than the brief's marker palette so that small text reaches WCAG AA 4.5:1 on the paper and the tinted official box (the brief's #2E8B57 stays for chart fills/strokes); `test_frontend.py` computes the ratios.
- `404.html` is the one page with root-absolute links (`/crackspread/`): GitHub Pages serves it at the requested URL without redirecting, so `./` would point back into the missing path.
- Microcopy added beyond brief §9 (all in `en.json`): site.description, hero.title_nodata, hero.tip.balanced, hero.gap.*, "Pumped"/"Drunk" bar labels, scale caption, crack percentile/legend/range/table strings and the Brent–WTI "Side note" fun fact, wb.official.quarter/ledger, wb.replay, countries toggle/rest/world total/JODI strings, shipping map/VLCC/cost/voyage labels, groceries headings, news chip/provider/feeds_failed/count strings, the Methodology paragraphs (meth.*), footer source/licence lines, common.stale_title, relative-time strings. Static HTML carries the site name, description, canonical and OG tags (crawlers don't run JS) and 404.html two lines; `app.js` has one last-resort "Data temporarily unavailable." used only if `en.json` itself fails to load.
- `robots.txt` disallows `/crackspread/data/` so raw JSON is not indexed; `sitemap.xml` lists the single page and is referenced from robots.txt.
- Brief §9.9 lists an OWID licence line; omitted because no OWID data appears in any schema or the UI.

## Philipp's checklist

Things only the repository owner can do, with the exact clicks:

1. **Create the repository** (if not done): github.com → *New repository* → name `crackspread`,
   **Public**, no template. The code is pushed to `main`.
2. **Let Actions write:** repo → *Settings → Actions → General → Workflow permissions* →
   **Read and write permissions** → *Save*.
3. **Turn on Pages:** *Settings → Pages → Build and deployment → Source:* **GitHub Actions**
   (not "Deploy from a branch", because the workflow deploys itself).
4. **EIA key (free, recommended, 2 minutes):** https://www.eia.gov/opendata/register.php → name +
   e-mail → the key arrives by mail. Then *Settings → Secrets and variables → Actions → New
   repository secret* → name `EIA_API_KEY`, value = the key.
5. **AI key (optional):** free: Google AI Studio → create API key → secret `GEMINI_API_KEY`.
   Or an Anthropic key → secret `ANTHROPIC_API_KEY` and `"ai_provider": "anthropic"` in
   `site/config.json`. Without a key the site still works (rule-based summary).
6. **Donation link:** create an account with *one* provider (Buy Me a Coffee, Ko-fi, GitHub Sponsors
   or PayPal.me), copy the profile link, paste it into `site/config.json` as `"donate_url"` (on
   GitHub: open the file → pencil icon → commit). Optional `"donate_provider_label": "Ko-fi"`.
   Empty = the donation box stays hidden.
7. **First run:** *Actions → update-and-deploy → Run workflow*. After about 2–3 minutes
   https://fixoa.github.io/crackspread/ is live. This run is the one that replaces the data files
   of the initial commit with a fetch under your own keys (see "Initial data files" under Decisions).
8. **Maintain the whiteboard (optional):** edit `site/data/manual.json` on GitHub. The AI's
   suggestions are in `site/data/proposals.json`; only you move them into `manual.json`.
9. **Licence holder:** `LICENSE` names the copyright holder as `Philipp (fixoa)`, the only name the
   brief gives. If you want your full name there, edit line 3 of `LICENSE` (a one-line change).

## Licence

Code: MIT (see `LICENSE`). Data in `site/data/` belong to their sources (EIA/FRED public domain,
JODI, news publishers) and are not covered by the MIT licence.
