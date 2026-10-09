# Crackspread: Build-Brief & Projektdoku (für Claude / Claude Code / Claude Cowork)

> Stand: 8. Oktober 2026 · Owner: Philipp (Wien, GitHub `fixoa`) · Repo: `fixoa/crackspread` · Live: https://fixoa.github.io/crackspread/
> Alle Datenquellen unten wurden am 8. Okt. 2026 per curl getestet (Status-Tabelle in §5).

---

## 0. Prompt zum Einfügen (Philipp kopiert das in Claude)

```text
Lies die Datei CLAUDE.md (= CRACKSPREAD_BRIEF.md) im Repo komplett und bau die Website "Crackspread" danach von Anfang bis Ende.
Frag mich nicht nach Plänen oder Zwischenschritten. Triff sinnvolle Entscheidungen selbst und dokumentier sie kurz im README.
Halte dich strikt an die Datenregeln: keine erfundenen Zahlen, jede Zahl mit Quelle und "as of"-Zeit, ausgefallene Feeds als "stale" markieren.
Bau Fetch-Skript, JSON-Daten, Frontend, Tests und GitHub-Actions-Workflow (3x täglich + manuell) und mach einen ersten echten Lauf.
Committe und pushe auf main im Repo fixoa/crackspread.
Wenn etwas nur ich erledigen kann (Pages aktivieren, Secrets, Spendenlink), gib mir am Ende eine kurze Checkliste mit genauen Klicks.
Am Ende meldest du: Live-URL, was funktioniert, was "stale" oder offen ist.
```

---

## 1. Worum geht's (kurz)

**Crackspread** ist eine kostenlose, werbefreie, spendenfinanzierte Website. Sie erklärt die Weltöl-Lage so einfach wie ein Whiteboard:
Wie viel Öl wird gefördert, wie viel verbraucht, von wem, gibt es gerade einen Mangel oder Überschuss, was machen die **Crack Spreads** (Raffineriemargen, v. a. Diesel), und was heißt das für LKW, Essen und Flüge.

- Inspiriert von Max Fishers YouTube-Video **"The oil apocalypse is here"** (2. Okt. 2026, https://www.youtube.com/watch?v=OETnuwwsv9U), in dem er die globale Ölbilanz auf ein Whiteboard zeichnet. Wir **verlinken und nennen** ihn als Inspiration. Wir **kopieren nichts** aus dem Video (keine Frames, keine Transkript-Passagen, kein Thumbnail-Hotlinking).
- Die Seite **aktualisiert sich 3x täglich** automatisch: Daten holen → JSON schreiben → KI-Kurzfassung „What changed today“ → committen → GitHub Pages neu deployen.
- **Humor in den Texten, Ernst bei den Zahlen.** Das Wortspiel steckt im Namen. Der Running Gag stammt aus dem Video: Analysten sagen mit ernster Miene „crack spreads are widening“. *„No, you don't have to blur it, it's just a chart.“* / *„This is a clean show.“*

### Nicht verhandelbar (Hard Rules)
1. **Keine erfundenen Daten.** Jede angezeigte Zahl kommt aus einer Datei in `data/` und hat `source`, `source_url` und `as_of`.
2. **Jede Zahl im UI zeigt Quelle + „as of“-Zeit** (Tooltip oder Kleingedrucktes).
3. **Feed-Ausfall:** letzten bekannten Wert behalten und sichtbar als **stale** markieren (Badge + Datum). Niemals leer überschreiben, niemals schätzen.
4. **Offizielle Zahlen zuerst** (EIA). Fishers Whiteboard-Zahlen sind ein **separates, klar gelabeltes Explainer-Modul**: „Whiteboard (Max Fisher's estimate, mid/late Sept 2026, not live data)“.
5. **Die KI erfindet keine Zahlen.** Sie bekommt nur abgerufene Schlagzeilen/Snippets und Zahlen, muss Links zitieren und JSON nach Schema liefern. Schlägt die Validierung fehl, bleibt die alte Zusammenfassung stehen.
6. Kostenlos, kein Server, keine Werbung, kein Tracking, keine Paywall, keine Cookies.

---

## 2. Sprache & Konfiguration

- **Dieses Brief ist Deutsch** (für Philipp). **Website-Texte sind standardmäßig Englisch.**
- Sprache ist **noch nicht final** → als Config-Wert `language` umsetzen (`"en"` Default). Alle UI-Strings in `site/i18n/en.json` auslagern; `site/i18n/de.json` als Stub anlegen (gleiche Keys, Werte vorerst leer bzw. englischer Fallback). Ein DE-Toggle kommt später; die Struktur muss ihn jetzt schon tragen (Fallback: fehlender DE-Key → EN).
- Zentrale Config: **`site/config.json`**. Frontend und Python lesen beide diese Datei.

```json
{
  "site_name": "Crackspread",
  "site_url": "https://fixoa.github.io/crackspread/",
  "language": "en",
  "languages_available": ["en", "de"],
  "show_language_toggle": false,
  "timezone_display": "Europe/Vienna",
  "donate_url": "",
  "donate_provider_label": "",
  "donate_cta_variant": 0,
  "ai_provider": "gemini",
  "ai_model": "",
  "stale_after_hours": { "prices": 96, "steo": 1080, "news": 24, "country": 9000 },
  "news_max_items": 40,
  "inspiration": {
    "title": "The oil apocalypse is here",
    "author": "Max Fisher",
    "url": "https://www.youtube.com/watch?v=OETnuwwsv9U",
    "date": "2026-10-02"
  }
}
```

`donate_url` = **DONATE_URL** (siehe §9). Ist der Wert leer, wird die Spendensektion komplett ausgeblendet (keine toten Buttons).

---

## 3. Architektur (gratis, ohne Server)

```
GitHub Actions (cron 3x/Tag + workflow_dispatch)
  └─ python scripts/update.py
       ├─ fetch_prices.py   → data/prices.json      (FRED CSV, ohne Key)
       ├─ fetch_balance.py  → data/balance.json     (EIA API v2 STEO; Fallback STEO_m.xlsx ohne Key)
       ├─ fetch_country.py  → data/countries.json   (EIA International API; Fallback JODI CSV)
       ├─ fetch_news.py     → data/news.json        (RSS: Google News + Fachfeeds)
       ├─ summarize.py      → data/summary.json     (LLM, schema-validiert; sonst alter Stand)
       └─ build_meta.py     → data/meta.json        (Laufzeit, Status pro Quelle, stale-Flags)
  └─ git commit "data: update YYYY-MM-DD HH:MM UTC" (nur wenn sich was geändert hat)
  └─ actions/upload-pages-artifact + actions/deploy-pages  (Ordner site/ inkl. site/data/)
GitHub Pages (statisch) → site/index.html lädt data/*.json per fetch()
```

**Wichtig:** Ein Commit mit `GITHUB_TOKEN` löst **keinen** weiteren Workflow aus. Deshalb macht **derselbe Workflow** auch das Pages-Deployment (Pages-Quelle = „GitHub Actions“). Zusätzlich deployt ein Push auf `main` (z. B. wenn Philipp `manual.json` editiert) über denselben Workflow (`on: push`, mit `paths`-Filter).

### 3.1 KI-Provider: WICHTIGE KORREKTUR
**GitHub Models ist abgeschaltet.** Laut GitHub-Doku wurde GitHub Models am **30. Juli 2026 vollständig eingestellt** (Inference API, Katalog, Playground, BYOK). Geprüft am 8.10.2026: https://docs.github.com/en/github-models/quickstart → „GitHub Models has been retired.“ Also **nicht** `permissions: models: read` + `GITHUB_TOKEN` verwenden.

Provider daher **austauschbar** über `ai_provider` in der Config und Secrets:

| `ai_provider` | Secret | Kosten | Hinweis |
|---|---|---|---|
| `gemini` (Default) | `GEMINI_API_KEY` (Google AI Studio) | Free Tier vorhanden (laut Pricing-Seite, Stand 10/2026); im Free Tier werden Eingaben zur Produktverbesserung genutzt, was hier okay ist, weil wir nur öffentliche Schlagzeilen schicken | Modellname nicht hart codieren, sondern `ai_model` aus der Config; Default im Code = aktuelles „Flash“-Modell aus der Gemini-Doku |
| `anthropic` | `ANTHROPIC_API_KEY` | Pay-as-you-go, mit kleinem Modell (Haiku-Klasse) bei 3 Läufen/Tag ein paar Cent pro Monat | `ai_model` konfigurierbar |
| `none` | – | 0 € | **Regelbasierter Fallback ohne LLM** (Template aus den Zahlen-Deltas + Top-Schlagzeilen). Wird automatisch benutzt, wenn kein Key gesetzt ist oder der Provider ausfällt. |

Die Seite muss **auch ganz ohne KI-Key** voll funktionieren (`none`). KI ist ein Nice-to-have obendrauf.

---

## 4. Repo-Struktur

```
crackspread/
├── CLAUDE.md                     # dieses Dokument (identisch mit CRACKSPREAD_BRIEF.md)
├── README.md                     # Kurzbeschreibung, lokal starten, Datenquellen, Lizenzhinweise
├── LICENSE                       # MIT für Code; Daten gehören den jeweiligen Quellen
├── requirements.txt
├── .github/workflows/update.yml  # cron + dispatch + push → fetch → commit → deploy
├── scripts/
│   ├── update.py                 # Orchestrator, CLI: --dry-run, --only <source>, --fixtures, --no-ai
│   ├── common.py                 # HTTP (Timeout, Retry, User-Agent), atomic JSON write, stale-Logik, Zeit
│   ├── fetch_prices.py           # FRED → prices.json (+ Crack-Spread-Berechnung)
│   ├── fetch_balance.py          # EIA STEO → balance.json
│   ├── fetch_country.py          # EIA International / JODI → countries.json
│   ├── fetch_news.py             # RSS → news.json (dedupe, Filter, Kürzung)
│   ├── summarize.py              # LLM-Abstraktion (gemini | anthropic | none), Prompt, Validierung
│   ├── build_meta.py             # meta.json (Status je Quelle)
│   └── schemas/                  # JSON-Schemas (*.schema.json) für alle data-Dateien
├── site/
│   ├── index.html
│   ├── config.json
│   ├── css/style.css
│   ├── js/app.js                 # lädt JSON, rendert Sektionen, stale-Badges, i18n
│   ├── js/charts.js              # uPlot- oder Chart.js-Wrapper bzw. eigenes SVG
│   ├── js/vendor/                # lokal eingebundene Lib (kein CDN-Zwang), z. B. uPlot.min.js
│   ├── i18n/en.json
│   ├── i18n/de.json              # Stub
│   ├── img/                      # eigene SVG-Doodles (selbst gezeichnet/generiert, KEINE Video-Frames)
│   └── data/                     # ← die vom Workflow geschriebenen JSONs liegen HIER (werden mit deployt)
│       ├── prices.json
│       ├── balance.json
│       ├── countries.json
│       ├── news.json
│       ├── summary.json
│       ├── manual.json           # „Whiteboard ledger“, von Hand gepflegt
│       ├── proposals.json        # KI-Vorschläge für manual.json (nur mit Zitat, nie automatisch übernommen)
│       └── meta.json
└── tests/
    ├── fixtures/                 # gespeicherte Beispiel-Antworten (CSV, JSON, RSS, xlsx-Auszug)
    ├── test_prices.py            # Crack-Formeln, fehlende Werte ".", Lag
    ├── test_stale.py             # Feed fällt aus → alter Wert bleibt + stale=true
    ├── test_news.py              # Dedupe, Snippet-Länge, Datumsparsing
    └── test_summary.py           # Schema-Validierung, Zahlen-Guard, Fallback
```

(Daten liegen unter `site/data/`, damit das Pages-Artefakt einfach `site/` ist. Im Text unten heißt das kurz `data/`.)

---

## 5. Datenquellen (am 8.10.2026 verifiziert)

User-Agent für alle Requests: `crackspread-bot/1.0 (+https://github.com/fixoa/crackspread)`.
⚠️ **FRED blockt Browser-User-Agents** (`Mozilla/5.0` → Verbindungsabbruch, HTTP 000). Mit dem Bot-UA oder dem curl/requests-Default klappt es (HTTP 200).

### 5.1 Status-Tabelle

| Quelle | URL / ID | Test 8.10.26 | Key? | Frequenz / Lag | Nutzung |
|---|---|---|---|---|---|
| FRED Brent | `https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU` | ✅ 200, letzter Wert 2026-10-06: 125.44 $/bbl | nein | täglich (Werktage), ~2 Tage Lag | Preise, Crack-Basis |
| FRED WTI | `…?id=DCOILWTICO` | ✅ 2026-10-06: 96.24 | nein | täglich, ~2 T. | 3-2-1-Crack |
| FRED NY Harbor ULSD | `…?id=DDFUELNYH` ($/gal) | ✅ 2026-10-06: 4.713 | nein | täglich, ~2 T. | Diesel-Crack |
| FRED NY Harbor Gasoline (conv.) | `…?id=DGASNYH` ($/gal) | ✅ 2026-10-06: 3.396 | nein | täglich, ~2 T. | Benzin-Crack |
| FRED Jet Fuel US Gulf | `…?id=DJFUELUSGULF` ($/gal) | ✅ 2026-10-06: 4.342 | nein | täglich, ~2 T. | Jet-Crack (Flüge-Sektion) |
| FRED NYH Heating Oil | `…?id=DHOILNYH` | ✅ 2026-10-06: 4.503 | nein | täglich | optional |
| FRED US Retail Diesel / Gasoline | `…?id=GASDESW`, `…?id=GASREGW` ($/gal) | ✅ 2026-10-05: 6.199 / 4.354 | nein | wöchentlich (Mo) | „Why your groceries care“ |
| EIA API v2 STEO | `https://api.eia.gov/v2/steo/data/?frequency=monthly&data[0]=value&facets[seriesId][]=PAPR_WORLD&api_key=…` | ✅ 200 (mit `DEMO_KEY` getestet) | **ja**, gratis (`EIA_API_KEY`); `DEMO_KEY` geht, aber Limit nur ~10 Req/h → nur für lokale Tests | monatlich (STEO erscheint ~Anfang Monat; aktuell: Oct 2026, 1.10.2026) | Hero-Bilanz |
| EIA STEO Excel (ohne Key) | `https://www.eia.gov/outlooks/steo/xls/STEO_m.xlsx`, Blatt `3atab` (Zeilen-IDs in Spalte A, z. B. `papr_world`) | ✅ 200, 1.1 MB, „October 2026“ | nein | monatlich | **Fallback**, falls kein Key/API down |
| EIA STEO Global Oil (Text) | `https://www.eia.gov/outlooks/steo/report/global_oil.php` | ✅ 200; Text: „global oil inventories fell by an average of 1.9 million b/d in 3Q26 and will fall an additional 0.7 million b/d on average in 4Q26“ | nein | monatlich | Link + Zitat im Hero |
| EIA International API | `https://api.eia.gov/v2/international/data/?frequency=annual&data[0]=value&facets[activityId][]=1&facets[productId][]=53&facets[unit][]=TBPD&start=2024` | ✅ 200, Daten bis 2025 (Produktion getestet; Konsum = `activityId=2`, gleiche Struktur, **beim Bau prüfen**) | ja (EIA-Key) | jährlich (+ teils monatlich) | „Who pumps, who guzzles“ |
| JODI Oil (primär) | `https://www.jodidata.org/_resources/files/downloads/oil-data/annual-csv/primary/primaryyear2026.csv` | ✅ 200, 5.5 MB, Monate bis 2026-07 | nein | monatlich, ~2–3 Monate Lag | Fallback/monatliche Rohöl-Produktion pro Land (`CRUDEOIL,INDPROD,KBD`); viele Lücken (`-`, z. B. RU) |
| JODI Oil (sekundär = Produkte) | `…/annual-csv/secondary/secondaryyear2026.csv` | ✅ 200 (12.6 MB, nur Header geprüft) | nein | monatlich | optional: Diesel/Gasoil-Nachfrage je Land |
| Our World in Data | `https://ourworldindata.org/grapher/oil-production-by-country.csv`, `…/oil-consumption-by-country.csv` (+ `.metadata.json`) | ✅ 200; **Einheit TWh**, nicht Barrel; Jahre bis 2025; Quelle Energy Institute Statistical Review 2026; nächstes Update 2027-06-30 | nein | jährlich | nur Kontext/Langzeit-Chart; Umrechnung ≈ 620 TWh/Jahr pro 1 mb/d, als „approx.“ kennzeichnen. Lizenz CC BY 4.0, Zitierpflicht |
| OPEC MOMR | `https://www.opec.org/opec_web/en/publications/338.htm` | ✅ 200 (HTML) | – | monatlich, PDF | **nur Link** |
| Google News RSS | `https://news.google.com/rss/search?q=<query>+when:1d&hl=en-US&gl=US&ceid=US:en` | ✅ alle 7 Queries liefern Items: oil supply (100), Strait of Hormuz (100), "crack spread" (7), diesel shortage (31), Bab al-Mandab (35), refinery attack (46), tanker rates VLCC (7) | nein | laufend | Newsliste + KI-Input. Links sind news.google.com-Redirects; das ist okay |
| oilprice.com | `https://oilprice.com/rss/main` | ✅ 15 Items, aktuell | nein | laufend | News |
| Rigzone | `https://www.rigzone.com/news/rss/rigzone_latest.aspx` | ✅ 20 Items | nein | laufend | News |
| gCaptain (Shipping) | `https://gcaptain.com/feed/` | ✅ 12 Items | nein | laufend | „Boats are slow“ |
| Splash247 (Shipping) | `https://splash247.com/feed/` | ✅ 10 Items | nein | laufend | Shipping |
| Hellenic Shipping News | `https://www.hellenicshippingnews.com/feed/` | ✅ 20 Items | nein | laufend | Tankerraten-News |
| Offshore Energy | `https://www.offshore-energy.biz/feed/` | ✅ 50 Items | nein | laufend | optional |
| EIA Today in Energy | `https://www.eia.gov/rss/todayinenergy.xml` | ✅ 14 Items, aktuell (7.10.26) | nein | werktags | News (offiziell) |
| EIA Press | `https://www.eia.gov/rss/press_rss.xml` | ✅ 12 Items | nein | unregelmäßig | News |
| EIA Gasoline & Diesel Update | `https://www.eia.gov/petroleum/gasdiesel/includes/gas_diesel_rss.xml` | ✅ 1 Item (Data for 10/05/26) | nein | wöchentlich | Retail-Preis-Hinweis |
| BBC Business | `https://feeds.bbci.co.uk/news/business/rss.xml` | ✅ 40 Items | nein | laufend | optional, braucht Keyword-Filter |

### 5.2 Getestet und NICHT nutzbar
| Quelle | Ergebnis |
|---|---|
| EIA „This Week in Petroleum“ RSS (`/petroleum/weekly/includes/week_in_petroleum_rss.xml`) | ⚠️ antwortet, aber **veraltet** (letztes Item 29.10.2025). Nicht verwenden, nur den TWIP-Link `https://www.eia.gov/petroleum/weekly/` setzen. Die URL `…/twip_rss.xml` gibt 404. |
| CNBC Energy RSS (`/id/19836768/device/rss/rss.html`) | ❌ „Access Denied“ (Akamai-Block) |
| IEA RSS (`/api/rss/news`) | ❌ 404 |
| GitHub Models (`models.github.ai`) | ❌ seit 30.7.2026 eingestellt (s. §3.1) |
| Tankerraten (VLCC $/Tag), Hormuz-Durchfluss, Pipeline-Status | ❌ keine freie, maschinenlesbare Quelle → `manual.json` (§6.6) |

### 5.3 Crack-Spread-Formeln ($/bbl; 1 bbl = 42 gal)
```
diesel_crack   = DDFUELNYH * 42 - DCOILBRENTEU
gasoline_crack = DGASNYH   * 42 - DCOILBRENTEU
jet_crack      = DJFUELUSGULF * 42 - DCOILBRENTEU
crack_321      = (2 * DGASNYH * 42 + 1 * DDFUELNYH * 42) / 3 - DCOILWTICO
```
- Nur rechnen, wenn **alle** Inputs **am selben Datum** vorhanden sind. FRED markiert fehlende Tage mit `.` (Feiertage); solche Tage überspringen.
- Werte aus den heute geladenen Daten (Kontrolle für Tests, Stand 2026-10-06): Diesel-Crack **72.51**, Benzin-Crack **17.19**, 3-2-1 **64.83** $/bbl. Höchster NYH-Diesel-Crack seit 2006: **116.5 $/bbl am 11.05.2022**; Schnitt 2015–2019 ≈ **16 $/bbl**; Schnitt 2025 ≈ 29 $/bbl.
- ⚠️ **Fishers „~$110 Rekord“ passt nicht zu diesen US-Daten** (NYH vs. Brent ≈ $72). Er meint vermutlich eine andere Benchmark (z. B. europäisches Gasoil oder Singapur). Auf der Seite steht deshalb der FRED/NYH-Wert als offizielle Zahl. Fishers $110 erscheint nur im Whiteboard-Explainer, gelabelt als „his estimate, benchmark not specified“. Ein Methodik-Hinweis erklärt den Unterschied.
- Auffällig in den Daten: Brent–WTI-Spread ≈ $29 (normal ≈ $3–5). Das ist eine echte Zahl und darf als „fun fact“ mit Quelle gezeigt werden.

### 5.4 STEO-Serien (EIA API v2, `route=steo`)
| seriesId | Bedeutung | Einheit | Beispiel (Okt-STEO) |
|---|---|---|---|
| `PAPR_WORLD` | World petroleum & other liquids production | mb/d | 2026-09: 101.30 |
| `PATC_WORLD` | World liquid fuels consumption | mb/d | 2026-09: 104.24 |
| `T3_STCHANGE_WORLD` | Net inventory **withdrawals** (positiv = Lager werden abgebaut = Defizit) | mb/d | 2026-07: −0.27 · 08: 2.97 · 09: 2.94 · 10: 0.67 |
| `PAPR_OPEC`, `PAPR_NONOPEC` | OPEC / Non-OPEC supply | mb/d | 09: 23.85 / 77.45 |
| `BREPUUS`, `WTIPUUS` | Brent/WTI Monatsdurchschnitt (STEO) | $/bbl | 09: 114.16 / 97.31 |

Hinweise:
- Die API liefert historische **und Prognose**-Monate (bis 2027-12). Für jeden Monat im JSON `is_forecast` setzen (Monat ≥ aktueller Monat ⇒ Prognose/Schätzung). Hero zeigt den **aktuellen Monat** mit Label „EIA estimate“.
- Plausibilitätscheck: Ø(Jul–Sep) von `T3_STCHANGE_WORLD` = 1.88 ≈ EIA-Text „1.9 mb/d in 3Q26“. ✔️ Das als Test einbauen (Fixture).
- `value` kommt als **String** → in float casten. API-Sortierung nach `value` ist lexikografisch → **immer clientseitig sortieren**.
- EIA International: Regionen/Aggregate (z. B. `WORL`, `ASOC`, `EU27`, `WP16`, `OPEC`) aus Länder-Rankings **rausfiltern** (nur echte ISO3-Ländercodes; Ausschlussliste im Code). Doppelte Länder-Einträge mit Gruppen-IDs (z. B. `WP16 India`) entfernen.
- Lizenz: EIA-Daten sind US-Public-Domain; Quelle nennen („Source: U.S. Energy Information Administration“).

---

## 6. Datendateien & JSON-Schemas

Gemeinsames Muster für **jeden** Datenpunkt bzw. jede Serie:
```json
{
  "value": 72.51,
  "unit": "USD/bbl",
  "as_of": "2026-10-06",
  "source": "FRED (EIA data), DDFUELNYH & DCOILBRENTEU",
  "source_url": "https://fred.stlouisfed.org/series/DDFUELNYH",
  "fetched_at": "2026-10-08T10:00:12Z",
  "stale": false
}
```
Jedes File hat oben: `"schema_version": 1, "generated_at": "<ISO UTC>"`. Alle Schemas liegen als JSON Schema (Draft 2020-12) in `scripts/schemas/`. `update.py` validiert **vor** dem Schreiben. Ungültig ⇒ nicht schreiben, alte Datei behalten, Fehler in `meta.json`.

### 6.1 `prices.json`
```json
{
  "schema_version": 1,
  "generated_at": "2026-10-08T10:00:12Z",
  "latest": {
    "brent":          {"value":125.44,"unit":"USD/bbl","as_of":"2026-10-06","source":"FRED DCOILBRENTEU","source_url":"https://fred.stlouisfed.org/series/DCOILBRENTEU","stale":false},
    "wti":            {"...": "same shape"},
    "ulsd_nyh":       {"value":4.713,"unit":"USD/gal", "...": "..."},
    "gasoline_nyh":   {"...": "..."},
    "jet_gulf":       {"...": "..."},
    "retail_diesel_us":   {"...": "weekly"},
    "retail_gasoline_us": {"...": "weekly"},
    "diesel_crack":   {"value":72.51,"unit":"USD/bbl","formula":"DDFUELNYH*42 - DCOILBRENTEU","...":"..."},
    "gasoline_crack": {"...": "..."},
    "jet_crack":      {"...": "..."},
    "crack_321":      {"...": "..."}
  },
  "history": {
    "dates": ["2006-06-14", "..."],
    "diesel_crack": [ "...floats or null..." ],
    "gasoline_crack": [ "..." ],
    "brent": [ "..." ]
  },
  "stats": {
    "diesel_crack": {"max":116.5,"max_date":"2022-05-11","mean_2015_2019":16.0,"percentile_now":"<computed>"}
  }
}
```
History: täglich ab 2006 ist ok (≈ 5 k Punkte, gzip-klein); alternativ wöchentlich downsamplen ab 2015 + die letzten 365 Tage täglich. Ziel: `prices.json` < 300 KB.

### 6.2 `balance.json`
```json
{
  "schema_version": 1,
  "generated_at": "...",
  "source": "EIA Short-Term Energy Outlook (STEO), October 2026",
  "source_url": "https://www.eia.gov/outlooks/steo/report/global_oil.php",
  "steo_release": "2026-10-01",
  "current_month": "2026-10",
  "months": [
    {"period":"2026-09","production":101.30,"consumption":104.24,"stock_draw":2.94,"is_forecast":false},
    {"period":"2026-10","production":102.10,"consumption":102.78,"stock_draw":0.67,"is_forecast":true}
  ],
  "quarters": [{"period":"2026Q3","stock_draw":1.88}],
  "quote": "We estimate that global oil inventories fell by an average of 1.9 million b/d in 3Q26 ...",
  "status": "deficit",
  "stale": false
}
```
`status`: `deficit` wenn `stock_draw > 0.3`, `surplus` wenn `< -0.3`, sonst `balanced` (Schwellen in Config). Monatsquelle = API; bei Fehler `STEO_m.xlsx` (Blatt `3atab`, Zeilen `papr_world`, `patc_world`, Lagerzeile per ID suchen); bei Fehler alter Stand + `stale:true`.

### 6.3 `countries.json`
```json
{
  "schema_version": 1, "generated_at": "...",
  "year": 2025,
  "unit": "thousand barrels/day",
  "source": "U.S. EIA International Energy Data", "source_url": "https://www.eia.gov/international/data/world",
  "producers": [{"iso3":"USA","name":"United States","value":0.0}],
  "consumers": [{"iso3":"USA","name":"United States","value":0.0}],
  "monthly_crude": {"source":"JODI Oil World Database","source_url":"https://www.jodidata.org/oil/","latest_month":"2026-07","rows":[{"iso2":"SA","value":8135.1}]},
  "stale": false
}
```
(Die `0.0` oben sind nur Platzhalter fürs Schema. Echte Werte kommen ausschließlich aus dem Fetch.) Top 10 je Liste, Rest als „Rest of world“.

### 6.4 `news.json`
```json
{
  "schema_version": 1, "generated_at": "...",
  "items": [
    {"id":"sha1(link)","title":"...","source":"Reuters","link":"https://...","published":"2026-10-08T01:25:00Z","snippet":"max 240 chars, HTML entfernt","topics":["hormuz","supply"],"feed":"google:Strait of Hormuz"}
  ]
}
```
Regeln: nur Titel, Quelle, Link, Datum, Kurz-Snippet (≤ 240 Zeichen) speichern, **keine Volltexte**. Dedupe über normalisierten Titel und Link. Nur Items < 48 h. Max `news_max_items`. Keyword-Filter für allgemeine Feeds (oil, crude, diesel, refinery, tanker, Hormuz, OPEC, Brent, LNG, shipping, Red Sea, Bab al-Mandab, jet fuel). Topics per Keyword-Mapping.

Google-News-Queries (Default, in Config erweiterbar): `oil supply`, `Strait of Hormuz`, `"crack spread"`, `diesel shortage`, `Bab al-Mandab`, `refinery attack`, `tanker rates VLCC`, `OPEC output`, `jet fuel prices`, `strategic petroleum reserve`.

### 6.5 `summary.json` (KI-Output)
```json
{
  "schema_version": 1,
  "generated_at": "2026-10-08T10:01:40Z",
  "provider": "gemini",
  "model": "<from config>",
  "headline": "Max 90 chars, dry, factual core",
  "what_changed": [
    {"text":"One sentence. Numbers only if present in input.","sources":["https://..."]}
  ],
  "crack_o_meter": {"level": 3, "label": "Gaping"},
  "quips": ["max 2 short jokes, no numbers"],
  "fallback": false
}
```
- `what_changed`: 3–5 Einträge, **jeder mit ≥ 1 Link**, der in `news.json` vorkommt.
- `crack_o_meter.level` wird **deterministisch im Code** aus dem Diesel-Crack-Perzentil berechnet (0: <50 %, 1: 50–75, 2: 75–90, 3: 90–98, 4: ≥98). Das LLM kann es nicht ändern. Labels: `Hairline`, `Visible`, `Widening (said with a straight face)`, `Gaping`, `Grand Canyon`.

### 6.6 `manual.json`: „Whiteboard ledger“ (von Hand gepflegt)
Für Dinge ohne freien Feed (Hormuz-Flüsse, Pipelines, Tankerraten, Routen). **Seed = Fishers Zahlen, klar gelabelt.**
```json
{
  "schema_version": 1,
  "updated_at": "2026-10-08",
  "ledger_label": "Whiteboard (Max Fisher's estimate, mid/late Sept 2026, not live data)",
  "default_source": {"name":"Max Fisher, 'The oil apocalypse is here' (YouTube, 2 Oct 2026)","url":"https://www.youtube.com/watch?v=OETnuwwsv9U","kind":"estimate"},
  "world": {"production_mbd": 100, "consumption_mbd": 100, "note": "roughly zero slack"},
  "hormuz_ledger": [
    {"id":"hormuz_normal","label":"Strait of Hormuz, normal flow","delta":20,"type":"baseline"},
    {"id":"iran_blockade","label":"Iran blockade (since Feb 2026)","delta":-20},
    {"id":"uae_pipeline","label":"UAE Habshan–Fujairah pipeline","delta":1.5},
    {"id":"sts_oman","label":"Ship-to-ship transfers off Oman","delta":6.5},
    {"id":"saudi_eastwest","label":"Saudi East-West pipeline","delta":5.5,"status":"knocked_out","status_note":"Houthi rockets on Red Sea-end refinery; Iraqi militia drones destroyed 3 pumping stations","counted":false},
    {"id":"region_total","label":"Gulf exports now","value":8,"type":"subtotal"},
    {"id":"spr","label":"Emergency stockpile releases","delta":2.5},
    {"id":"china_cuts","label":"China import cuts (was -5.5, since 1 Sept only -2.5)","delta":-2.5,"note":"China paying ~$135 vs ~$105 world (Shanghai vs WTI futures)"},
    {"id":"net_shortfall","label":"Net shortfall (Fisher)","value":-7,"type":"total"}
  ],
  "shipping": {
    "route_note": "Houthis took Yemen coast in Sept and closed Bab al-Mandab; Asia-bound oil goes Suez → Med → Cape of Good Hope",
    "voyage_days_before": 19, "voyage_days_now": 48,
    "tankers_effectively_lost": "2 of 3",
    "vlcc_day_rate_usd": {"pre_war": 30000, "pandemic_peak": 250000, "now": 1200000},
    "shipping_cost_per_bbl_usd": {"before": 3, "now": 26}
  },
  "products_mbd": {"gasoline": 25, "jet": 8, "diesel": 30, "other": 37},
  "refinery_shock": [
    {"label":"Ukraine strikes 28 of 32 big Russian refineries","diesel_delta_mbd":-1.0},
    {"label":"Iran vs Gulf states refinery war","diesel_delta_mbd":-1.5},
    {"label":"China cuts diesel exports","diesel_delta_mbd":-0.5},
    {"label":"Total: ~3 of 30 mb/d diesel (~10%)","diesel_delta_mbd":-3.0,"type":"total"}
  ],
  "diesel_math": {"last_year": {"oil":65,"crack":20,"diesel":85}, "now": {"oil":100,"crack":110,"diesel":210}, "note":"60% oil rise → ~250% diesel rise (Fisher; benchmark not specified)"},
  "impacts": [
    {"sector":"Trucking","effect":"costs ~x3"},
    {"sector":"Construction & housing","effect":"higher costs"},
    {"sector":"Food","effect":"~+20%, more with farm diesel"},
    {"sector":"Flights","effect":"~+40% fuel bills, airline insolvency risk"},
    {"sector":"Shipping","effect":"~x3"},
    {"sector":"Oil shipping itself","effect":"see tanker rates"}
  ]
}
```
Jeder Eintrag darf eigene `source`/`url`/`as_of` haben (überschreibt `default_source`). Philipp editiert die Datei direkt auf GitHub (Stift-Icon) → Push → Workflow deployt.

### 6.7 `proposals.json`: KI-Vorschläge (nie automatisch übernommen)
```json
{"schema_version":1,"generated_at":"...","proposals":[
  {"target":"shipping.vlcc_day_rate_usd.now","proposed_value":0,"quote":"exact short quote from snippet","source_title":"...","source_url":"https://...","published":"..."}
]}
```
- Die KI darf einen Wert **nur** vorschlagen, wenn die Zahl **wörtlich im Snippet/Titel** steht (Code prüft das per String-Match) und ein Link dabei ist.
- UI zeigt Vorschläge im Whiteboard als Fußnote **„per news reports“** mit Link und Datum, neben dem Ledger-Wert, nie anstelle davon.
- Übernehmen tut nur Philipp (manuell in `manual.json`).

### 6.8 `meta.json`
```json
{"schema_version":1,"last_run":"2026-10-08T10:02:00Z","run_id":"...","sources":{
  "fred":{"ok":true,"last_success":"...","error":null,"stale":false},
  "eia_steo":{"ok":true,"via":"api|xlsx","last_success":"...","stale":false},
  "countries":{"ok":true,"stale":false},
  "news":{"ok":true,"items":38,"feeds_failed":["..."]},
  "summary":{"ok":true,"provider":"gemini","fallback":false}
}}
```

---

## 7. Fehlerbehandlung & Stale-Regeln

- Jede Quelle einzeln in try/except, **Timeout 20 s, 3 Retries mit Backoff**. Eine kaputte Quelle bricht den Lauf **nie** ab.
- Fehler ⇒ alte Datei laden, `stale: true` setzen, `fetched_at` unverändert lassen, Fehlertext in `meta.json`.
- Zusätzlich zeitbasiert stale: `now - as_of > stale_after_hours[<typ>]` (FRED: 96 h, weil Wochenende + 2 Tage Lag; STEO: 45 Tage; News: 24 h; Länder: ~1 Jahr).
- Plausibilitätsgrenzen (sonst verwerfen + stale): Brent/WTI 5–400 $/bbl; ULSD/Gasoline 0.3–15 $/gal; Weltproduktion/-konsum 70–130 mb/d.
- Atomic writes (`tmp` + `os.replace`). JSON immer mit `sort_keys` / stabiler Reihenfolge schreiben, damit Commits nur bei echten Änderungen entstehen.
- Workflow-Exit-Code: 0, solange mindestens ein Deploy möglich ist. Nur bei Totalausfall von Code/Schema ≠ 0.
- Frontend: fehlt eine JSON-Datei oder ist sie kaputt, zeigt die Sektion „Data temporarily unavailable. The chart is fine, it's just shy.“ und keine Fake-Werte.

---

## 8. KI-Zusammenfassung (summarize.py)

**Input an das LLM** (kompakt, < 8 k Tokens): aktuelle Zahlen aus `prices.json`/`balance.json` (inkl. Veränderung zum letzten Lauf), max. 30 News-Items (Titel, Quelle, Link, Datum, Snippet). **Nichts sonst**: kein Internetzugriff, kein Tool-Use.

**System-Prompt (Englisch, so übernehmen):**
```text
You write the "What changed today" box for Crackspread, a site that explains the world oil market simply.
Use ONLY the facts, numbers and headlines provided in the input JSON. Never invent, estimate, round into new figures, or recall numbers from memory.
Every numeric figure you write must appear verbatim in the input. If unsure, omit the number.
Every bullet must cite at least one link from the input "news" list.
Tone: dry, understated, smart. Humor only in "headline" and "quips"; never joke about people suffering from shortages, war victims or job losses. No drug jokes beyond the site's name. Keep it clean.
Output ONLY valid JSON matching this schema: {"headline": str<=90, "what_changed": [{"text": str<=220, "sources": [url,...]}] (3-5 items), "quips": [str<=120] (0-2 items, no numbers)}.
```
**Validierung im Code (alle müssen bestehen, sonst Fallback):**
1. JSON parsebar + Schema-valide (`jsonschema`).
2. Jede Quelle-URL ∈ Links aus `news.json`.
3. **Zahlen-Guard:** alle Zahlen per Regex aus `headline` + `what_changed` extrahieren; jede muss (normalisiert, ±0.01) im Input vorkommen. Sonst verwerfen.
4. Längen-/Anzahl-Limits.
5. Fehler ⇒ 1 Retry mit Fehlermeldung an das Modell ⇒ dann Provider `none` (Regel-Template: „Diesel crack {Δ} since last update; top stories: …“ nur aus echten Daten) ⇒ wenn auch das scheitert: alte `summary.json` behalten.

Provider-Abstraktion: `def complete(system:str, user:str) -> str` pro Provider (Gemini REST `generateContent` mit JSON-Response-Mode bzw. Anthropic Messages API). Model-ID aus Config, keine SDK-Pflicht (`requests` reicht).

---

## 9. Seitenaufbau, Sektionen & Microcopy (EN)

Design: **Whiteboard-Look.** Off-white Hintergrund (#FBFAF7) mit ganz leichtem Raster, Marker-Farben (Schwarz #1F1F1F, Rot #D7263D, Blau #1B4D89, Grün #2E8B57). Überschriften in **Permanent Marker** (Google Fonts, lokal einbinden oder `display=swap`), Hand-Notizen in **Caveat**, alle **Zahlen in einer klaren Sans** (z. B. Inter / System-UI, `font-variant-numeric: tabular-nums`). Mobile-first, eine Spalte, ab 900 px zwei Spalten wo sinnvoll. Leichte „gezeichnete“ SVG-Unterstreichungen. Animationen nur mit `prefers-reduced-motion: no-preference`.

Jede Zahl: `<data value="72.51">$72.51/bbl</data>` + Kleingedrucktes „FRED · as of Oct 6, 2026“ + ggf. rotes `STALE`-Badge.

### 9.1 Hero: „The Balance“
- Daten: `balance.json` (aktueller Monat): Production vs. Consumption (mb/d), Gap = Stock draw, Badge `SHORTAGE` / `SURPLUS` / `BALANCED`, EIA-Zitat (1.9 mb/d in 3Q26), „as of“.
- Darstellung: zwei handgezeichnete Balken (Waage-Metapher) + große Gap-Zahl.
- Microcopy:
  - H1: **„The world drinks ~102 million barrels of oil a day. Here's whether there's enough.“** (die Zahl wird aus den Daten eingesetzt, nicht hart codiert)
  - Sub: *„Updated three times a day. Explained like a whiteboard. Crack spreads included. This is a clean show.“*
  - Badge-Tooltip Shortage: *„Inventories are being drained. Not a vibe, a measurement.“*
  - Badge-Tooltip Surplus: *„More oil than thirst. Enjoy it while it lasts.“*

### 9.2 „Crack-o-meter“
- Daten: `prices.json`: Diesel-, Benzin-, Jet-Crack, 3-2-1; Chart Diesel-Crack seit 2006 (mit Markern: 2022-Peak 116.5, Schnitt 2015–19), Toggle 1Y / 5Y / Max.
- Gauge (Halbkreis-SVG) mit Level 0–4 aus §6.5.
- Microcopy:
  - Titel: **„Crack-o-meter“**
  - Sub: *„Analysts say 'crack spreads are widening' with a straight face. We'll show you why.“*
  - Erklärung: *„A crack spread is what refiners earn for 'cracking' crude into diesel or gasoline. Product price minus crude price. That's it. No, you don't have to blur it, it's just a chart.“*
  - Gauge-Labels: `Hairline` · `Visible` · `Widening (said with a straight face)` · `Gaping` · `Grand Canyon`
  - Methodik-Footnote: *„NY Harbor ULSD minus Brent. Other benchmarks (Europe, Singapore) can read higher.“*

### 9.3 „The Whiteboard“
- Daten: `manual.json` → `hormuz_ledger` als animierte +/- Strichliste (Zeile für Zeile „aufgezeichnet“, Zwischensumme, durchgestrichene Saudi-Pipeline, Endsumme −7). Daneben Kasten **„Official view“** mit EIA-Stock-Draw zum Vergleich.
- Pflicht-Label oben: „Whiteboard (Max Fisher's estimate, mid/late Sept 2026, not live data)“ + Link zum Video.
- `proposals.json`-Hinweise als Fußnoten „per news reports“.
- Microcopy:
  - Titel: **„The Whiteboard“**
  - Sub: *„One marker, one strait, twenty million barrels. Let's do the math.“*
  - Unter der Saudi-Pipeline: *„Was +5.5. Then drones happened.“*
  - Vergleichsbox: *„The official count (EIA) says inventories fell ~1.9 mb/d last quarter. The whiteboard says ~7. Both are trying their best.“* (Zahlen aus Daten einsetzen)

### 9.4 „Who pumps, who guzzles“
- Daten: `countries.json`: Top-10-Producer und -Consumer (horizontale Balken; optional einfache SVG-Weltkarte, kein Mapbox). Toggle Producers/Consumers. JODI-Monatswert als Zusatzzeile, wenn vorhanden.
- Microcopy:
  - Titel: **„Who pumps, who guzzles“**
  - Sub: *„Some countries are oil wells with flags. Others are gas stations with armies. Most are just thirsty.“*
  - Fußnote: *„Annual data, because countries don't text us daily.“*

### 9.5 „Boats are slow“
- Daten: `manual.json.shipping` (gelabelt „per Fisher / news reports“) + Shipping-News (gCaptain, Splash247, Hellenic, Google „tanker rates VLCC“).
- Visual: schematische SVG-Route Golf → Suez → Mittelmeer → Kap der Guten Hoffnung → Asien vs. alte Route; „19 days → 48 days“; 3 Tanker-Icons, 2 verblassen.
- Microcopy:
  - Titel: **„Boats are slow“**
  - Sub: *„When the shortcut closes, oil takes the scenic route. All the way around Africa.“*
  - Bei den Tankern: *„Same ships, longer trips. Two out of three effectively vanish. Not literally. Please don't call the coast guard.“*
  - Day rate: *„A supertanker used to rent for about a nice used car per day. Now it's a small house. Per day.“* (darunter die echten Zahlen aus manual.json)

### 9.6 „Why your groceries care“
- Daten: Produkt-Mix (`manual.json.products_mbd`, Donut/Balken), Refinery-Shock-Tally, Diesel-Mathe (Fisher, gelabelt), echte US-Retail-Diesel/Benzin-Preise (FRED `GASDESW`/`GASREGW`, wöchentlich), Impact-Karten (Trucking, Food, Flights, Construction, Shipping).
- Microcopy:
  - Titel: **„Why your groceries care“**
  - Sub: *„Almost everything you buy rode a diesel truck at some point. Usually several.“*
  - Diesel-Erklärung: *„Diesel is the stubborn one. Trucks, tractors, cranes, ships, backup generators. You can't just skip it.“*
  - Mathe-Box: *„Oil up 60%. Diesel up 250%. Math is not having a good year either.“*
  - Ton-Hinweis: Hier **keine** Witze über Menschen, die sich Essen oder Heizen nicht leisten können.

### 9.7 „What changed today“
- Daten: `summary.json` (Headline, 3–5 Bullets mit Quellen-Links, Quips) + `news.json` (Liste: Titel → externer Link `rel="noopener"`, Quelle, relative Zeit, Topic-Chips, Filter nach Topic).
- Zeitstempel: „Updated 12:00 Vienna time · next update ~19:00“.
- Wenn `fallback:true`: kleiner Hinweis *„Our summarizer is on a coffee break. Here are the raw headlines.“*
- Microcopy:
  - Titel: **„What changed today“**
  - Sub: *„We read the oil news three times a day so you don't have to. You're welcome. Sources below, as always.“*

### 9.8 Spenden: „Fuel the humans“ (direkt über dem Footer, **nicht im Hero**)
- Nur sichtbar, wenn `config.donate_url` gesetzt ist. Ein einziger Button, provider-agnostisch: funktioniert mit Buy Me a Coffee, Ko-fi, GitHub Sponsors oder PayPal.me (einfach die URL). Kein eingebettetes Widget, kein Tracking-Pixel, kein Third-Party-Script, `rel="noopener"`. Kein Paywall, keine Ads, kein Nag-Popup.
- Text (Variante per `donate_cta_variant`, 0 = Default):
  - **0 (Default):** Headline *„Buy me a cup of liquid gold that fuels humans.“* · Sub: *„Coffee: the only crude worth refining. Crackspread is free, ad-free and tracker-free. If it saved you an hour of doom-scrolling, top up the tank.“* · Button: **„Refine a coffee ☕“**
  - **1:** *„This site runs on two fuels: public data and coffee. Only one of them is free.“* · Button: **„Fill 'er up“**
  - **2:** *„Help keep the crack spread wide and the coffee spread wider.“* · Button: **„Widen my coffee spread“**
  - **3:** *„No ads, no paywall, no barrels. Just a tip jar with excellent margins.“* · Button: **„Drop a barrel (of coffee)“**
- Kleingedruckt: *„Donations go to a person, not a hedge fund. They don't change what the data says.“*
- Zusätzlich ein kleiner Footer-Link „☕ Support“ → gleiche URL (auch nur, wenn gesetzt).

### 9.9 Footer
- Quellenliste mit Links und Lizenzhinweisen (EIA/FRED: Public Domain, Quelle nennen; FRED-Hinweis „Source: FRED, Federal Reserve Bank of St. Louis; data: U.S. EIA“; OWID: CC BY 4.0; JODI: Quelle nennen; News: Rechte bei den Publishern, wir verlinken nur).
- **Methodology**: kurze Seite/Abschnitt mit Crack-Formeln, Benchmark-Hinweis, STEO = Schätzung/Prognose, Update-Zeiten, Stale-Regel.
- **Disclaimer:** *„Not financial advice. Not trading advice. Not even dinner-party advice. Numbers come from the sources listed; jokes come from us.“*
- **Credit:** *„Inspired by Max Fisher's whiteboard in 'The oil apocalypse is here' (YouTube, 2 Oct 2026).“* + Link. Kein Embed nötig. Falls doch: nur `youtube-nocookie.com`-Embed per Klick (Zwei-Klick-Lösung), kein Autoload.
- „Last update: … (Vienna time)“ aus `meta.json`, Link zum GitHub-Repo, Link „☕ Support“.

---

## 10. Ton & Brand-Guide (Humor)

- **Trocken, klug, unterschwellig.** Wie ein Analyst, der gerade so ernst bleibt. Pointe max. 1 pro Absatz.
- **Das Wortspiel bleibt beim Namen.** „Crack“ = Raffinerie-Cracking. **Keine Drogen-Bildsprache** (keine Pfeifen, Pulver, Spritzen, „Dealer“-Witze). Running Gag erlaubt: „this is a clean show“, „no need to blur it, it's just a chart“, „said with a straight face“.
- **Nie nach unten treten:** keine Witze über Menschen in Mangel, Kriegsopfer, Arbeitslose, Länder als Ganzes. Ziel des Humors sind Charts, Jargon, Schiffe, die Absurdität von Zahlen und wir selbst.
- **Zahlen sind nie die Pointe.** Kein Runden für den Gag, keine Übertreibung von Werten. Humor nur in Copy/Microcopy.
- Keine politischen Seitenhiebe auf Personen oder Parteien. Kriegsparteien neutral benennen, so wie die Quelle es tut.
- Englisch: kurze Sätze, aktive Verben, keine Emojis außer ☕ beim Spenden und ggf. Pfeilen.

---

## 11. Frontend-Technik

- **Kein Build-Step.** Plain HTML + CSS + Vanilla-JS-ES-Module. Chart-Lib: **uPlot** (≈ 50 KB, schnell) lokal in `site/js/vendor/`; Gauge, Whiteboard, Routen als eigenes SVG.
- Performance: Lighthouse mobile ≥ 90 in Performance, Accessibility, Best Practices, SEO. Fonts mit `font-display: swap`, nur 2 Webfonts.
- A11y: semantische Sektionen mit `aria-labelledby`, Charts mit Textalternative (Tabelle `<details>` „Show data“), Kontrast AA, Tastaturbedienung, `prefers-reduced-motion`.
- Alle Pfade **relativ** (`./data/prices.json`), weil die Seite unter `/crackspread/` läuft.
- Zeiten im Browser mit `Intl.DateTimeFormat` in `Europe/Vienna` (aus Config) anzeigen, intern UTC.
- Meta: OG-Tags (Titel, Beschreibung, eigenes OG-Bild `img/og.png` im Whiteboard-Stil), Favicon (Marker-Tropfen).
- Kein Analytics, keine Cookies, kein Third-Party-JS. Optional später: Plausible/GoatCounter nur nach Philipps Entscheidung, nicht jetzt.

---

## 12. GitHub Actions Workflow (Skeleton)

Cron in **UTC** (GitHub-Cron kennt keine Sommerzeit):
- Ziel 05:00 / 12:00 / 19:00 Wien.
- Sommerzeit (CEST, UTC+2, bis 25.10.2026): `0 3,10,17 * * *`.
- Winterzeit (CET, UTC+1, ab 25.10.2026): dieselbe Zeile ergibt 04:00 / 11:00 / 18:00 Wien. **Das ist okay**, nicht jedes halbe Jahr umstellen. (Wer exakt will: im Winter `0 4,11,18 * * *`.) Cron-Läufe können 5–30 Min. Verspätung haben; Minute bewusst nicht auf `:00`, z. B. `17 3,10,17 * * *`, weil zur vollen Stunde viel Last ist.
- Hinweis: In öffentlichen Repos werden Schedules nach 60 Tagen ohne Repo-Aktivität deaktiviert. Die Daten-Commits zählen normalerweise als Aktivität. Trotzdem im README erwähnen.

```yaml
name: update-and-deploy

on:
  schedule:
    - cron: "17 3,10,17 * * *"   # ≈ 05:17/12:17/19:17 Vienna (CEST); 04:17/11:17/18:17 in winter (CET)
  workflow_dispatch:
    inputs:
      no_ai:
        description: "Skip AI summary"
        type: boolean
        default: false
  push:
    branches: [main]
    paths: ["site/**", "scripts/**", ".github/workflows/**"]

permissions:
  contents: write     # commit data
  pages: write        # deploy pages
  id-token: write     # required by deploy-pages

concurrency:
  group: crackspread
  cancel-in-progress: false

jobs:
  update:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v5          # Claude: use the current major versions
      - uses: actions/setup-python@v6
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Fetch data
        if: github.event_name != 'push'
        env:
          EIA_API_KEY: ${{ secrets.EIA_API_KEY }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
        run: python scripts/update.py ${{ inputs.no_ai && '--no-ai' || '' }}
      - name: Test
        run: pytest -q
      - name: Commit data
        if: github.event_name != 'push'
        run: |
          git config user.name  "crackspread-bot"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add site/data
          git diff --cached --quiet || git commit -m "data: update $(date -u +'%Y-%m-%d %H:%M') UTC"
          git pull --rebase --autostash && git push
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v4
        with:
          path: site

  deploy:
    needs: update
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - id: deployment
        uses: actions/deploy-pages@v4
```
Fehlt `EIA_API_KEY`: STEO über die Excel-Datei, Länderdaten über JODI. Fehlt ein KI-Key: Provider `none`. Der Workflow läuft immer durch.

---

## 13. Python

`requirements.txt`:
```
requests>=2.32
feedparser>=6.0
openpyxl>=3.1
jsonschema>=4.23
python-dateutil>=2.9
pytest>=8
```
Kein pandas nötig (CSV mit `csv`-Modul). Python 3.12.

CLI von `scripts/update.py`:
```
python scripts/update.py                 # alles, schreibt site/data/*.json
python scripts/update.py --dry-run       # alles holen, validieren, Diff ausgeben, NICHTS schreiben
python scripts/update.py --fixtures      # offline mit tests/fixtures/*, deterministisch
python scripts/update.py --only prices   # nur eine Quelle (prices|balance|countries|news|summary)
python scripts/update.py --no-ai         # Summary mit Provider "none"
```

---

## 14. Lokal laufen lassen

```bash
git clone https://github.com/fixoa/crackspread && cd crackspread
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export EIA_API_KEY=DEMO_KEY            # oder echter Key; DEMO_KEY ist stark limitiert
python scripts/update.py --fixtures     # offline-Test
python scripts/update.py --no-ai        # echte Daten ohne KI
pytest -q
python -m http.server 8000 -d site      # → http://localhost:8000
```

---

## 15. Testing

- `tests/fixtures/`: echte, gekürzte Snapshots (FRED-CSV inkl. `.`-Lücken, EIA-STEO-JSON, `3atab`-Auszug, JODI-Auszug, 2–3 RSS-Feeds, eine gültige und mehrere **ungültige** LLM-Antworten: erfundene Zahl, fremder Link, kaputtes JSON).
- Pflicht-Tests:
  1. Crack-Formeln → Fixture 2026-10-06 ergibt 72.51 / 17.19 / 64.83 (±0.01).
  2. Q3-Stock-Draw-Mittel aus Fixture ≈ 1.88.
  3. Feed wirft Exception → alter Wert bleibt, `stale:true`, Lauf endet mit Exit 0.
  4. Zeitbasiertes Stale (as_of älter als Schwelle).
  5. LLM-Antwort mit erfundener Zahl/fremdem Link → verworfen → Fallback.
  6. Alle `site/data/*.json` validieren gegen ihre Schemas.
  7. News-Dedupe + Snippet ≤ 240 Zeichen + keine HTML-Tags.
- Frontend: einmal manuell mit `--fixtures`-Daten und einmal mit absichtlich gelöschter `prices.json` prüfen (Sektion degradiert sauber).

---

## 16. Was Philipp tun muss (kurz)

1. **Repo anlegen** (falls noch nicht): github.com → New repository → Name `crackspread`, **Public**, ohne Template. (Claude pusht den Code.)
2. **Actions dürfen schreiben:** Repo → *Settings → Actions → General → Workflow permissions* → **„Read and write permissions“** → Save.
3. **Pages einschalten:** *Settings → Pages → Build and deployment → Source:* **„GitHub Actions“**. (Nicht „Deploy from a branch“, weil unser Workflow selbst deployt.)
4. **EIA-Key (gratis, empfohlen, 2 Min.):** https://www.eia.gov/opendata/register.php → Name + E-Mail → Key kommt per Mail. Dann *Settings → Secrets and variables → Actions → New repository secret* → Name `EIA_API_KEY`, Wert = Key.
5. **KI-Key (optional):** Gratis-Variante: Google AI Studio → API-Key erstellen → Secret `GEMINI_API_KEY`. Oder Anthropic-Key → Secret `ANTHROPIC_API_KEY` und in `site/config.json` `"ai_provider": "anthropic"`. Ohne Key läuft die Seite trotzdem (einfache Zusammenfassung ohne KI).
6. **Spendenlink:** Konto bei *einem* Anbieter anlegen (Buy Me a Coffee, Ko-fi, GitHub Sponsors oder PayPal.me), Profil-Link kopieren, in `site/config.json` bei `"donate_url"` einfügen (auf GitHub: Datei öffnen → Stift → Commit). Optional `"donate_provider_label": "Ko-fi"`. Leer lassen = Spendenbox unsichtbar.
7. **Erster Lauf:** Tab *Actions → update-and-deploy → Run workflow*. Nach ~2–3 Min. ist https://fixoa.github.io/crackspread/ live.
8. **Whiteboard pflegen (wenn du willst):** `site/data/manual.json` auf GitHub bearbeiten. Vorschläge der KI stehen in `site/data/proposals.json`.

---

## 17. Definition of Done / Abnahme-Checkliste

- [ ] Repo `fixoa/crackspread` public, Code auf `main`, README mit Quellen und Lokal-Anleitung, `CLAUDE.md` im Root.
- [ ] https://fixoa.github.io/crackspread/ lädt mobil in < 2 s (4G), keine Konsolenfehler, Lighthouse mobile ≥ 90 (alle 4 Kategorien).
- [ ] Workflow läuft per Cron (3x/Tag) **und** per „Run workflow“; Daten-Commit nur bei Änderung; Pages deployt im selben Lauf.
- [ ] Alle Sektionen 9.1–9.9 vorhanden, mit EN-Microcopy aus `i18n/en.json`; `de.json`-Stub und `language`-Config vorhanden.
- [ ] **Jede** Zahl zeigt Quelle + „as of“; keine hart codierten Daten im HTML/JS.
- [ ] Hero zeigt EIA-STEO-Bilanz des aktuellen Monats inkl. Label „estimate/forecast“ und EIA-Zitat.
- [ ] Crack-o-meter-Werte stimmen mit FRED-Formeln überein (Test grün); Benchmark-Hinweis zu Fishers $110 sichtbar.
- [ ] Whiteboard klar gelabelt als „Max Fisher's estimate, not live data“, Link zum Video; „Official view“ daneben.
- [ ] Feed-Ausfall simuliert → letzter Wert + STALE-Badge, Seite bricht nicht.
- [ ] KI-Summary schema-valide, jede Aussage mit Link, Zahlen-Guard aktiv; ohne Key funktioniert der Fallback `none`.
- [ ] KI-Vorschläge landen nur in `proposals.json`, nie automatisch in `manual.json`.
- [ ] Spendensektion über dem Footer + Footer-Link, gesteuert über `donate_url`; leer ⇒ ausgeblendet; kein Tracking, keine Ads, keine Paywall.
- [ ] Footer: Quellen + Lizenzen, Methodology, Disclaimer, Max-Fisher-Credit, „Last update“ in Wiener Zeit.
- [ ] Keine Drogen-Bildsprache, keine Witze auf Kosten Betroffener (kurzer Copy-Review).
- [ ] `pytest` grün; `--dry-run` und `--fixtures` funktionieren.
- [ ] Abschlussbericht an Philipp: Live-URL, Status je Quelle, offene To-dos (z. B. Secrets, Spendenlink).

---

## 18. Bekannte Stolpersteine (für Claude)

- FRED: Browser-UA wird geblockt → eigenen Bot-UA setzen. Werte `.` = fehlend.
- EIA API: `value` ist String; Sortierung nach value lexikografisch; `DEMO_KEY` ~10 Requests/h. Mehrere STEO-Serien in **einem** Request holen (mehrere `facets[seriesId][]`), um Limits zu schonen. URLs mit `[]` nicht durch curl-Globbing jagen (`curl -g`) bzw. sauber via `requests` params bauen.
- STEO-Werte des laufenden Monats sind **Schätzungen**; Revisionen jeden Monat sind normal (ältere Monate ändern sich).
- OWID-Oil-Charts sind in **TWh** (Energie), nicht Barrel → nur als Kontext, deutlich als „approx.“ markieren oder weglassen.
- JODI hat Lücken (`-`) und Lag; nie Lücken als 0 interpretieren.
- Google News liefert Redirect-Links; Quelle steht im `<source>`-Tag. `when:1d` im Query hält es frisch.
- GitHub Models ist eingestellt (30.7.2026). Nicht einbauen.
- GITHUB_TOKEN-Commits triggern keine anderen Workflows → Deploy im selben Workflow.
- Pages läuft unter Unterpfad `/crackspread/` → nur relative Pfade.
