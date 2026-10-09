/* Crackspread frontend. Loads config → i18n → meta → data and renders each section independently.
 * Rules: no innerHTML with data, every number through dp(), only http(s) links, all copy from i18n. */
import * as C from './charts.js';

// ------------------------------------------------------------------ state
let cfg = null;
let EN = {};
let LANG = {};
let lang = 'en';
let locale = 'en-US';
let TZ = 'Europe/Vienna';
let meta = null;
let news = null; // shared with the shipping section
const REDUCED = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

const SECTIONS = { hero: 'hero', crack: 'crack', whiteboard: 'wb', countries: 'countries', shipping: 'ship', groceries: 'groc', news: 'news', methodology: 'meth', donate: 'donate' };

// ------------------------------------------------------------------ tiny DOM + i18n helpers
function h(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    n.append(c instanceof Node ? c : String(c));
  }
  return n;
}

/* replaceChildren() stringifies null/false arguments (it would render a literal "null"); this drops them like h() does. */
function fill(el, ...children) {
  el.replaceChildren(...children.flat(Infinity).filter((c) => c !== null && c !== undefined && c !== false));
}

const isHttp = (u) => typeof u === 'string' && /^https?:\/\/\S+$/i.test(u);

/* "https://www.eia.gov/outlooks/steo/" → "eia.gov/outlooks/steo" (link label for a source home page). */
const hostLabel = (u) => String(u).replace(/^https?:\/\/(www\.)?/i, '').replace(/\/$/, '');

function link(href, label, cls) {
  if (!isHttp(href)) return h('span', { class: cls }, label);
  return h('a', { href, target: '_blank', rel: 'noopener noreferrer', class: cls }, label);
}

function t(key, vars) {
  let s = LANG[key];
  if (s === undefined || s === '') s = EN[key];
  if (s === undefined) s = key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined ? m : String(vars[k])));
  return s;
}

/* Like t() but placeholders may be DOM nodes. Returns a DocumentFragment. */
function tf(key, vars = {}) {
  const s = t(key);
  const frag = document.createDocumentFragment();
  const re = /\{(\w+)\}/g;
  let last = 0;
  let m;
  while ((m = re.exec(s))) {
    frag.append(s.slice(last, m.index));
    const v = vars[m[1]];
    frag.append(v instanceof Node ? v : v === undefined ? m[0] : String(v));
    last = re.lastIndex;
  }
  frag.append(s.slice(last));
  return frag;
}

function byId(id) {
  return document.getElementById(id);
}

function setText(id, value) {
  const el = byId(id);
  if (el) el.textContent = value;
}

// ------------------------------------------------------------------ formatting
const fmt = {
  num(v, max = 2, min = 0) {
    if (typeof v !== 'number' || !isFinite(v)) return '–';
    return new Intl.NumberFormat(locale, { maximumFractionDigits: max, minimumFractionDigits: Math.min(min, max) }).format(v);
  },
  money(v, digits = 2) {
    if (typeof v !== 'number' || !isFinite(v)) return '–';
    return '$' + fmt.num(v, digits, digits);
  },
  signed(v, max = 2) {
    if (typeof v !== 'number' || !isFinite(v)) return '–';
    const s = fmt.num(Math.abs(v), max);
    return v < 0 ? '−' + s : '+' + s;
  },
  pct(v, max = 1) {
    return fmt.num(v, max) + '%';
  },
  // 'YYYY-MM-DD' → Oct 6, 2026 (no timezone shift); ISO timestamps → date + time in the display zone; 'YYYY-MM' → October 2026
  date(s, withTime = false) {
    if (typeof s !== 'string') return '–';
    if (/^\d{4}-\d{2}$/.test(s)) return fmt.month(s);
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
      return new Intl.DateTimeFormat(locale, { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(s + 'T00:00:00Z'));
    }
    const d = new Date(s);
    if (isNaN(d)) return s;
    const o = { timeZone: TZ, year: 'numeric', month: 'short', day: 'numeric' };
    if (withTime) Object.assign(o, { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
    return new Intl.DateTimeFormat(locale, o).format(d);
  },
  // 'YYYY-MM-DD' → "2 Oct 2026": day-first regardless of locale (the brief quotes the credit in this form)
  dateDayFirst(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return fmt.date(s);
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(s + 'T00:00:00Z'));
  },
  time(d) {
    const dt = d instanceof Date ? d : new Date(d);
    if (isNaN(dt)) return '–';
    return new Intl.DateTimeFormat(locale, { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(dt);
  },
  month(s) {
    const d = new Date(s + '-01T00:00:00Z');
    if (isNaN(d)) return s;
    return new Intl.DateTimeFormat(locale, { timeZone: 'UTC', year: 'numeric', month: 'long' }).format(d);
  },
  relTime(s) {
    const d = new Date(s);
    if (isNaN(d)) return '';
    const sec = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (sec < 90) return t('common.reltime.now');
    if (sec < 3600) return t('common.reltime.minutes', { n: Math.round(sec / 60) });
    if (sec < 48 * 3600) return t('common.reltime.hours', { n: Math.round(sec / 3600) });
    return t('common.reltime.days', { n: Math.round(sec / 86400) });
  },
  tzCity() {
    return String(TZ).split('/').pop().replace(/_/g, ' ');
  },
};

/* Value + unit → display string. */
function valueText(v, unit) {
  if (typeof v !== 'number' || !isFinite(v)) return '–';
  switch (unit) {
    case 'USD/bbl': return fmt.money(v, 2) + '/bbl';
    case 'USD/gal': return fmt.money(v, 3) + '/gal';
    case 'USD/day': return fmt.money(v, 0) + '/' + t('common.per_day').replace(/^per\s+/i, '');
    case 'USD': return fmt.money(v, v % 1 ? 2 : 0);
    case 'million barrels per day': case 'mb/d': return fmt.num(v, 2) + ' ' + t('common.mbd');
    case 'thousand barrels per day': case 'kb/d': return fmt.num(v, 1) + ' ' + t('common.kbd');
    case 'days': return t('ship.days', { n: fmt.num(v, 0) });
    case '%': return fmt.pct(v);
    default: return fmt.num(v, 2) + (unit ? ' ' + unit : '');
  }
}

/* Build a datapoint object from loose data (manual.json, balance.json). */
function mk(value, unit, as_of, source, source_url, stale = false, extra = {}) {
  return { value, unit, as_of, source, source_url, stale: !!stale, ...extra };
}

function staleBadge(dp) {
  return h('span', { class: 'badge stale', title: t('common.stale_title', { date: fmt.date(dp.as_of) }) }, t('common.stale'));
}

/* Source small print: "FRED · as of Oct 6, 2026" with the source linked when it has an http(s) URL. */
function srcLine(dp) {
  const src = isHttp(dp.source_url) ? link(dp.source_url, dp.source || dp.source_url) : h('span', {}, dp.source || '');
  return h('small', { class: 'src' }, tf('common.asof', { source: src, date: fmt.date(dp.as_of) }));
}

/* THE number helper. Every figure on the page goes through here. */
function dp(d, o = {}) {
  const stale = !!(d && (d.stale || o.fileStale));
  const wrap = h('span', { class: 'dp' + (o.big ? ' big' : '') + (o.inline ? ' inline' : '') + (stale ? ' is-stale' : '') + (o.cls ? ' ' + o.cls : '') });
  if (o.label) wrap.append(h('span', { class: 'dp-label' }, o.label));
  const text = o.text !== undefined ? o.text : valueText(d ? d.value : null, d ? d.unit : '');
  const data = h('data', { class: 'num', value: d && typeof d.value === 'number' ? String(d.value) : '' }, text);
  wrap.append(data);
  if (stale) wrap.append(' ', staleBadge(d));
  if (d && !o.noSrc) wrap.append(srcLine(d));
  return wrap;
}

function unavailableNote() {
  return h('p', { class: 'unavailable hand', role: 'status' }, t('common.unavailable'));
}

function sectionBody(id) {
  return byId(id + '-body');
}

// ------------------------------------------------------------------ loading
async function loadJSON(path, init) {
  const r = await fetch(path, init);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

function pickLang() {
  const q = new URLSearchParams(location.search).get('lang');
  const avail = Array.isArray(cfg.languages_available) ? cfg.languages_available : ['en'];
  if (q && avail.includes(q)) return q;
  return avail.includes(cfg.language) ? cfg.language : 'en';
}

function nextRunAfter(d) {
  const sch = cfg.update_schedule_utc || { hours: [3, 10, 17], minute: 17 };
  const cands = [];
  for (const off of [0, 1]) {
    for (const hh of sch.hours || []) {
      const c = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + off, hh, sch.minute || 0));
      if (c > d) cands.push(c);
    }
  }
  cands.sort((a, b) => a - b);
  return cands[0] || null;
}

function updatedLine() {
  if (!meta || !meta.last_run) return null;
  const last = new Date(meta.last_run);
  if (isNaN(last)) return null;
  const next = nextRunAfter(last);
  return t('news.updated', { time: fmt.time(last), tz: fmt.tzCity(), next: next ? fmt.time(next) : '–' });
}

// ------------------------------------------------------------------ static copy
function renderStatic() {
  document.title = cfg.site_name || t('site.name');
  setText('brand-name', cfg.site_name || t('site.name'));
  setText('skip-link', t('common.skip'));
  for (const [sec, key] of Object.entries(SECTIONS)) {
    setText(sec + '-title', t(key + '.title'));
    const sub = byId(sec + '-sub');
    if (sub) sub.textContent = t(key + '.sub');
  }
  const u = updatedLine();
  const mh = byId('masthead-updated');
  if (mh && u) mh.textContent = u;
  const nav = byId('lang-nav');
  if (nav && cfg.show_language_toggle) {
    nav.hidden = false;
    nav.setAttribute('aria-label', t('common.lang.label'));
    for (const l of cfg.languages_available || ['en']) {
      const a = h('a', { href: '?lang=' + l, class: 'chip' + (l === lang ? ' on' : ''), 'aria-current': l === lang ? 'true' : null }, t('common.lang.' + l));
      nav.append(a, ' ');
    }
  }
}

// ------------------------------------------------------------------ 9.1 hero
function renderHero(balance) {
  const body = sectionBody('hero');
  const cur = balance.current || (balance.months || []).find((m) => m.period === balance.current_month);
  if (!cur) throw new Error('balance: no current month');
  const n = Math.round(cur.consumption);
  setText('hero-title', t('hero.title', { n: fmt.num(n, 0) }));
  const status = balance.status || 'balanced';
  const asOf = balance.steo_release || balance.as_of;
  const unit = balance.unit || 'million barrels per day';
  const dProd = mk(cur.production, unit, asOf, balance.source, balance.source_url, balance.stale);
  const dCons = mk(cur.consumption, unit, asOf, balance.source, balance.source_url, balance.stale);
  // stock_draw is EIA T3_STCHANGE_WORLD: positive = inventories drawn (shortage), negative = inventories built
  // (surplus). The shortage/surplus rows pair their directional label with the magnitude; the balanced row and
  // the beam figure show the signed change in storage (+ = added). <data value> always carries the number shown.
  const draw = typeof cur.stock_draw === 'number' ? cur.stock_draw : null;
  const gapValue = draw === null ? null : status === 'balanced' ? -draw : Math.abs(draw);
  const gapText = draw === null ? '–' : status === 'balanced' ? fmt.signed(-draw, 2) : fmt.num(Math.abs(draw), 2);
  const beamText = draw === null ? '–' : status === 'balanced' ? fmt.signed(-draw, 2) : (status === 'surplus' ? '+' : '−') + fmt.num(Math.abs(draw), 2);
  const dGap = mk(gapValue, unit, asOf, balance.source, balance.source_url, balance.stale);
  const gapLabel = t('hero.gap.' + status);

  const visual = h('div', { class: 'hero-visual' });
  C.balanceScale(visual, {
    prod: cur.production, cons: cur.consumption, status,
    prodText: fmt.num(cur.production, 1), consText: fmt.num(cur.consumption, 1),
    prodLabel: t('hero.production'), consLabel: t('hero.consumption'),
    gapText: beamText,
    gapUnit: t('common.mbd'), label: t('hero.scale_caption'),
  });
  visual.append(h('p', { class: 'hand caption' }, t('hero.scale_caption')));

  const facts = h('div', { class: 'hero-facts' });
  const badge = h('span', { class: 'badge status ' + status, title: t('hero.tip.' + status) }, t('hero.badge.' + status));
  facts.append(h('p', { class: 'status-line' }, badge, ' ', h('span', { class: 'hand tip' }, t('hero.tip.' + status))));
  facts.append(h('p', { class: 'hand month-label' },
    t(cur.period === balance.current_month ? 'hero.month_estimate' : 'hero.month_forecast', { month: fmt.month(cur.period) }),
    balance.release_date_estimated ? ' · ' + t('hero.release_estimated') : null));
  facts.append(h('div', { class: 'dp-row' },
    dp(dProd, { label: t('hero.production') }),
    dp(dCons, { label: t('hero.consumption') }),
    dp(dGap, { label: gapLabel, cls: 'gap ' + status, text: gapText + ' ' + t('common.mbd') }),
  ));
  if (balance.quote) {
    const bq = h('blockquote', { class: 'quote' }, h('p', {}, '“' + balance.quote + '”'),
      h('footer', {}, link(balance.quote_url || balance.source_url, t('hero.quote_source', { edition: balance.steo_edition || '' })), ' · ', fmt.date(asOf),
        balance.next_release ? h('span', {}, ' · ', t('hero.next_release', { date: fmt.date(balance.next_release) })) : null));
    facts.append(bq);
  }
  body.replaceChildren(h('div', { class: 'hero-grid' }, visual, facts));
}

// ------------------------------------------------------------------ 9.2 crack-o-meter
function renderCrack(prices) {
  const body = sectionBody('crack');
  const L = prices.latest || {};
  const st = (prices.stats && prices.stats.diesel_crack) || {};
  const fileStale = !!prices.stale;
  const labels = (cfg.crack_levels && cfg.crack_levels.labels) || [];
  const cuts = (cfg.crack_levels && cfg.crack_levels.percentile_cuts) || [50, 75, 90, 98];
  const levelLabel = (i) => (EN['crack.level.' + i] !== undefined ? t('crack.level.' + i) : labels[i] || String(i));
  // Source for the percentile/legend statistics (prices.stats.diesel_crack) and the history series: the same FRED
  // series as the latest diesel-crack datapoint; as_of = the statistics' date resp. the newest history date.
  const dc = L.diesel_crack || {};
  const statSrc = { source: dc.source || prices.source, source_url: dc.source_url || prices.source_url, as_of: st.as_of || dc.as_of || prices.as_of, stale: fileStale || !!dc.stale };
  const srcNote = (d) => { const s = srcLine(d); if (d.stale) s.append(' ', staleBadge(d)); return s; };
  const num = (v, digits) => h('data', { class: 'num', value: String(v) }, fmt.num(v, digits));

  const explain = h('p', { class: 'explain' }, t('crack.explain'));

  // gauge column
  const gcol = h('div', { class: 'gauge-col' });
  const gwrap = h('div', { class: 'gauge-wrap' });
  C.gauge(gwrap, { percentile: st.percentile_now, cuts, level: st.level, label: t('crack.gauge_label') });
  gcol.append(gwrap);
  const lvl = typeof st.level === 'number' ? st.level : null;
  gcol.append(h('p', { class: 'level-label marker' }, lvl === null ? '–' : levelLabel(lvl)));
  if (typeof st.percentile_now === 'number') {
    gcol.append(h('p', { class: 'hand' }, tf('crack.percentile', { pct: num(st.percentile_now, 1), start: fmt.date(st.history_start || (prices.history && prices.history.dates && prices.history.dates[0]) || '') }), srcNote(statSrc)));
  }
  const ol = h('ol', { class: 'levels' });
  for (let i = 0; i < 5; i++) ol.append(h('li', { class: i === lvl ? 'current' : '', 'aria-current': i === lvl ? 'true' : null }, levelLabel(i)));
  gcol.append(ol);
  gcol.append(dp(L.diesel_crack, { big: true, label: t('crack.diesel'), fileStale }));

  // values column
  const vcol = h('div', { class: 'crack-values' });
  const items = [['gasoline_crack', 'crack.gasoline'], ['jet_crack', 'crack.jet'], ['crack_321', 'crack.c321']];
  for (const [k, key] of items) if (L[k]) vcol.append(dp(L[k], { label: t(key), fileStale }));
  const crude = h('div', { class: 'dp-row small' });
  if (L.brent) crude.append(dp(L.brent, { label: 'Brent', fileStale }));
  if (L.wti) crude.append(dp(L.wti, { label: 'WTI', fileStale }));
  vcol.append(crude);
  const sp = prices.stats && prices.stats.brent_wti_spread;
  if (sp && typeof sp.value === 'number') {
    vcol.append(h('p', { class: 'hand note' }, t('crack.spread_fact', { v: fmt.num(sp.value, 2), mean: fmt.num(sp.mean_2015_2019, 1) }),
      ' ', L.brent_wti_spread ? srcLine(L.brent_wti_spread) : null));
  }

  // chart block
  const chart = h('div', { class: 'chart-block' });
  const hist = prices.history || {};
  if (Array.isArray(hist.dates) && Array.isArray(hist.diesel_crack) && hist.dates.length) {
    chart.append(h('h3', { class: 'marker-sm' }, t('crack.chart_title', { start: fmt.date(st.history_start || hist.dates[0]) })));
    const toggle = h('div', { class: 'chips', role: 'group', 'aria-label': t('crack.range_label') });
    const plotEl = h('div', { class: 'uplot-host' });
    chart.append(toggle, plotEl);
    const histSrc = { ...statSrc, as_of: hist.dates[hist.dates.length - 1] };
    const legend = h('p', { class: 'hand legend' });
    if (typeof st.max === 'number') legend.append(h('span', { class: 'lg red' }, tf('crack.chart_peak', { date: fmt.date(st.max_date), v: num(st.max, 1) })), ' ');
    if (typeof st.mean_2015_2019 === 'number') legend.append(h('span', { class: 'lg green' }, tf('crack.chart_mean', { v: num(st.mean_2015_2019, 1) })), ' ');
    if (typeof st.mean_prev_year === 'number') legend.append(h('span', { class: 'lg' }, tf('crack.chart_prev_mean', { year: st.prev_year, v: num(st.mean_prev_year, 1) })));
    legend.append(srcNote(histSrc));
    chart.append(legend);
    // text alternative
    const det = h('details', { class: 'showdata' }, h('summary', {}, t('crack.showdata')));
    const N = 60;
    const tbl = h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('crack.table.date')), h('th', { scope: 'col' }, t('crack.table.value')))));
    const tb = h('tbody');
    const start = Math.max(0, hist.dates.length - N);
    for (let i = hist.dates.length - 1; i >= start; i--) {
      const v = hist.diesel_crack[i];
      tb.append(h('tr', {}, h('td', {}, fmt.date(hist.dates[i])), h('td', { class: 'num' }, h('data', { value: typeof v === 'number' ? String(v) : '' }, typeof v === 'number' ? fmt.num(v, 2, 2) : '–'))));
    }
    tbl.append(tb);
    det.append(h('p', { class: 'src' }, t('crack.table.note', { n: Math.min(N, hist.dates.length) })), tbl, srcNote(histSrc));
    chart.append(det);

    let api = null;
    const ranges = ['1y', '5y', 'max'];
    const btns = ranges.map((r) => h('button', { type: 'button', class: 'chip', 'aria-pressed': r === 'max' ? 'true' : 'false', onclick: () => {
      btns.forEach((b) => b.setAttribute('aria-pressed', b.dataset.range === r ? 'true' : 'false'));
      if (api) api.setRange(r);
    } }, t('crack.range.' + r)));
    btns.forEach((b, i) => { b.dataset.range = ranges[i]; toggle.append(b); });
    // uPlot needs the host in the DOM to measure; defer until appended
    queueMicrotask(() => {
      try {
        api = C.crackChart(plotEl, {
          dates: hist.dates, values: hist.diesel_crack, stats: st,
          labels: { date: t('crack.table.date'), series: t('crack.diesel'), mean: t('crack.series_mean'), record: t('crack.chart_peak', { date: fmt.date(st.max_date), v: fmt.num(st.max, 1) }) },
          unitFmt: (v) => '$' + fmt.num(v, 0),
          dateFmt: (ts, full) => new Intl.DateTimeFormat(locale, full ? { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' } : { timeZone: 'UTC', year: 'numeric', month: 'short' }).format(new Date(ts * 1000)),
        });
        api.setRange('max');
      } catch (e) {
        console.error('crack chart', e);
        plotEl.replaceChildren(unavailableNote());
      }
    });
  }

  body.replaceChildren(explain, h('div', { class: 'cols-2 crack-grid' }, gcol, vcol), chart, h('p', { class: 'footnote hand' }, t('crack.footnote')));
}

// ------------------------------------------------------------------ proposals (per news reports)
function proposalsFor(proposals, target) {
  if (!proposals || !Array.isArray(proposals.proposals)) return [];
  return proposals.proposals.filter((p) => p.target === target);
}

function proposalNote(p) {
  const unit = p.unit || (/vlcc/.test(p.target) ? 'USD/day' : /voyage/.test(p.target) ? 'days' : /cost/.test(p.target) ? 'USD/bbl' : 'mb/d');
  const src = link(p.source_url, p.source_title || (() => { try { return new URL(p.source_url).hostname; } catch (e) { return p.source_url; } })());
  return h('small', { class: 'proposal' }, tf('common.proposal', { value: valueText(p.proposed_value, unit), source: src, date: fmt.date(p.published || '') }));
}

function proposalNotes(proposals, target) {
  return proposalsFor(proposals, target).map(proposalNote);
}

// ------------------------------------------------------------------ 9.3 whiteboard
function manualSrc(manual, entry = {}) {
  const ds = manual.default_source || {};
  return { source: entry.source || ds.name || manual.source, source_url: entry.url || ds.url || manual.source_url, as_of: entry.as_of || manual.updated_at || manual.as_of, stale: !!manual.stale };
}

function manualLabel(manual, text) {
  const s = manualSrc(manual);
  return h('p', { class: 'wb-label' }, h('span', { class: 'tag ink' }, text || manual.ledger_label), ' ', link(s.source_url, t('wb.video')), ' · ', fmt.date(s.as_of), manual.stale ? [' ', staleBadge(s)] : null);
}

function renderWhiteboard(manual, balance, proposals) {
  const body = sectionBody('whiteboard');
  const wrap = h('div');
  wrap.append(manualLabel(manual));
  const w = manual.world || {};
  const worldNotes = [...proposalNotes(proposals, 'world.production_mbd'), ...proposalNotes(proposals, 'world.consumption_mbd')];
  const worldVars = { p: fmt.num(w.production_mbd, 1), c: fmt.num(w.consumption_mbd, 1), note: w.note || '' };
  wrap.append(h('p', { class: 'hand' }, t(w.note ? 'wb.world' : 'wb.world_plain', worldVars), worldNotes.length ? h('span', { class: 'notes' }, worldNotes) : null));

  const tbl = h('table', { class: 'ledger' }, h('caption', { class: 'sr-only' }, manual.ledger_label));
  tbl.append(h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, ''), h('th', { scope: 'col', class: 'num' }, t('common.mbd')))));
  const tb = h('tbody');
  const rows = [];
  (manual.hormuz_ledger || []).forEach((e, i) => {
    const struck = e.status === 'knocked_out' || e.counted === false;
    const tr = h('tr', { class: (e.type ? e.type + ' ' : '') + (struck ? 'struck ' : ''), style: `--i:${i}` });
    const s = manualSrc(manual, e);
    const labelCell = h('th', { scope: 'row' }, h('span', { class: 'lbl hand' }, e.label));
    if (struck) {
      labelCell.append(h('span', { class: 'status-note hand red' }, t('wb.saudi_note', { delta: fmt.signed(e.delta, 1) })));
      if (e.status_note) labelCell.append(h('small', { class: 'src' }, e.status_note));
    }
    if (e.note) labelCell.append(h('small', { class: 'src' }, e.note));
    if (e.source || e.url) labelCell.append(srcLine(s));
    const notes = proposalNotes(proposals, `hormuz_ledger[${e.id}].delta`);
    if (notes.length) labelCell.append(h('span', { class: 'notes' }, notes));
    let txt = '–';
    let val = null;
    if (typeof e.delta === 'number') { txt = fmt.signed(e.delta, 1); val = e.delta; }
    else if (typeof e.value === 'number') { txt = '= ' + fmt.signed(e.value, 1).replace(/^\+/, ''); val = e.value; }
    const cell = h('td', { class: 'num' }, h('data', { value: val === null ? '' : String(val) }, txt), struck ? h('span', { class: 'sr-only' }, ' (' + t('wb.not_counted') + ')') : null);
    tr.append(labelCell, cell);
    tb.append(tr);
    rows.push(tr);
  });
  tbl.append(tb);
  const total = (manual.hormuz_ledger || []).find((e) => e.type === 'total');
  const ms = manualSrc(manual, total || {});
  const ledgerSrc = h('p', { class: 'ledger-src' }, srcLine(ms));

  // Official view
  const off = h('aside', { class: 'box official', 'aria-labelledby': 'wb-official-title' }, h('h3', { id: 'wb-official-title', class: 'marker-sm' }, t('wb.official.title')));
  let q = null;
  if (balance && Array.isArray(balance.quarters)) {
    const done = balance.quarters.filter((x) => !x.is_forecast && typeof x.stock_draw === 'number');
    q = done.length ? done[done.length - 1] : null;
  }
  const wbVal = total && typeof total.value === 'number' ? Math.abs(total.value) : null;
  if (q && wbVal !== null) {
    off.append(h('p', { class: 'hand big-note' }, t('wb.official.text', { eia: fmt.num(q.stock_draw, 1), wb: fmt.num(wbVal, 1) })));
  }
  const unit = (balance && balance.unit) || 'million barrels per day';
  const row = h('div', { class: 'dp-row' });
  if (q) row.append(dp(mk(q.stock_draw, unit, balance.steo_release || balance.as_of, balance.source, balance.source_url, balance.stale), { label: t('wb.official.quarter', { quarter: q.period }) }));
  else row.append(unavailableNote());
  if (total) row.append(dp(mk(total.value, 'mb/d', ms.as_of, ms.source, ms.source_url, ms.stale), { label: t('wb.official.ledger'), text: fmt.signed(total.value, 1) + ' ' + t('common.mbd') }));
  off.append(row);

  const cols = h('div', { class: 'cols-2 wb-grid' }, h('div', { class: 'ledger-wrap' }, tbl, ledgerSrc), off);
  wrap.append(cols);

  // line-by-line drawing, unless the visitor prefers reduced motion
  const draw = () => rows.forEach((r, i) => setTimeout(() => r.classList.add('drawn'), REDUCED ? 0 : 160 * i + 80));
  if (REDUCED || typeof IntersectionObserver === 'undefined') {
    rows.forEach((r) => r.classList.add('drawn'));
  } else {
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) { draw(); io.disconnect(); } }, { threshold: 0.2 });
    io.observe(tbl);
    wrap.append(h('p', {}, h('button', { type: 'button', class: 'chip', onclick: () => { rows.forEach((r) => r.classList.remove('drawn')); requestAnimationFrame(() => setTimeout(draw, 50)); } }, t('wb.replay'))));
  }
  body.replaceChildren(wrap);
}

// ------------------------------------------------------------------ 9.4 countries
function renderCountries(countries) {
  const body = sectionBody('countries');
  const unit = countries.unit || 'thousand barrels per day';
  const unitShort = t('common.kbd');
  const sets = { producers: countries.producers, consumers: countries.consumers };
  const listHost = h('div', { class: 'ranking' });
  const chips = h('div', { class: 'chips', role: 'group', 'aria-label': t('countries.toggle_label') });
  const btns = {};
  const show = (kind) => {
    for (const k of Object.keys(btns)) btns[k].setAttribute('aria-pressed', k === kind ? 'true' : 'false');
    const r = sets[kind];
    listHost.replaceChildren();
    if (!r || !Array.isArray(r.rows) || !r.rows.length) { listHost.append(unavailableNote()); return; }
    const rows = r.rows;
    const max = Math.max(...rows.map((x) => x.value), typeof r.rest_of_world === 'number' ? r.rest_of_world : 0);
    const ol = h('ol', { class: 'bars' });
    rows.forEach((row, i) => {
      ol.append(h('li', {}, h('span', { class: 'c-name' }, row.name), C.hbar(row.value, max, kind === 'producers' ? C.COLORS.BLUE : C.COLORS.RED, 200 + i),
        h('data', { class: 'num', value: String(row.value) }, fmt.num(row.value, 0))));
    });
    if (typeof r.rest_of_world === 'number') {
      ol.append(h('li', { class: 'rest' }, h('span', { class: 'c-name' }, t('countries.rest')), C.hbar(r.rest_of_world, max, C.COLORS.MUTED, 299),
        h('data', { class: 'num', value: String(r.rest_of_world) }, fmt.num(r.rest_of_world, 0))));
    }
    listHost.append(ol);
    const d = mk(r.world_total, unit, r.as_of || countries.as_of, r.source || countries.source, r.source_url || countries.source_url, r.stale || countries.stale);
    listHost.append(h('p', { class: 'hand' }, t('countries.year_note', { year: r.year ?? '–', unit: unitShort }), r.note ? h('small', { class: 'src' }, r.note) : null));
    if (typeof r.world_total === 'number') listHost.append(dp(d, { label: t('countries.world_total', { v: '', unit: '' }).trim() }));
    else listHost.append(srcLine(d));
    if (countries.via === 'jodi') listHost.append(h('p', { class: 'hand red' }, t('countries.via_jodi')));
  };
  for (const kind of ['producers', 'consumers']) {
    btns[kind] = h('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', onclick: () => show(kind) }, t('countries.' + kind));
    chips.append(btns[kind]);
  }
  const wrap = h('div', {}, chips, listHost);
  const mc = countries.monthly_crude;
  if (mc && Array.isArray(mc.rows) && mc.rows.length) {
    const line = h('p', { class: 'jodi' }, h('span', { class: 'hand' }, t('countries.jodi', { month: fmt.month(mc.latest_month || ''), unit: mc.unit || unit })), ' ');
    mc.rows.forEach((r, i) => line.append(i ? ' · ' : '', h('span', { class: 'jodi-item' }, r.name + ' ', h('data', { class: 'num', value: String(r.value) }, fmt.num(r.value, 0)))));
    line.append(srcLine(mk(null, '', mc.latest_month, mc.source, mc.source_url, mc.stale || countries.stale)));
    if (mc.stale || countries.stale) line.append(' ', staleBadge({ as_of: mc.latest_month }));
    wrap.append(line);
  }
  wrap.append(h('p', { class: 'footnote hand' }, t('countries.footnote')));
  body.replaceChildren(wrap);
  show('producers');
}

// ------------------------------------------------------------------ 9.5 shipping
function renderShipping(manual, proposals) {
  const body = sectionBody('shipping');
  const sh = manual.shipping || {};
  const s = manualSrc(manual);
  const wrap = h('div');
  wrap.append(manualLabel(manual, t('ship.label')));
  if (sh.route_note) wrap.append(h('p', { class: 'hand' }, sh.route_note));

  const map = h('div', { class: 'route' });
  C.routeMap(map, {
    labels: { gulf: t('ship.map.gulf'), hormuz: t('ship.map.hormuz'), suez: t('ship.map.suez'), med: t('ship.map.med'), mandab: t('ship.map.mandab'), cape: t('ship.map.cape'), asia: t('ship.map.asia'), old: t('ship.map.old'), now: t('ship.map.new') },
    before: t('ship.days', { n: fmt.num(sh.voyage_days_before, 0) }), now: t('ship.days', { n: fmt.num(sh.voyage_days_now, 0) }), label: t('ship.sub'),
  });
  const days = h('div', { class: 'days' },
    dp(mk(sh.voyage_days_before, 'days', s.as_of, s.source, s.source_url, s.stale), { label: t('ship.before'), big: true }),
    h('img', { src: './img/arrow.svg', alt: '→', class: 'arrow', width: 64, height: 32 }),
    h('span', { class: 'now-wrap' }, dp(mk(sh.voyage_days_now, 'days', s.as_of, s.source, s.source_url, s.stale), { label: t('ship.now'), big: true, cls: 'red' }), ...proposalNotes(proposals, 'shipping.voyage_days_now')),
  );
  const left = h('div', {}, map, h('p', { class: 'hand caption' }, t('ship.voyage')), days);

  // tankers
  const fleet = h('div', { class: 'fleet', role: 'img', 'aria-label': t('ship.lost', { n: sh.tankers_effectively_lost || '' }) });
  for (let i = 0; i < 3; i++) fleet.append(h('img', { src: './img/tanker.svg', alt: '', class: 'tanker' + (i > 0 ? ' faded' : ''), width: 120, height: 48 }));
  const tank = h('div', { class: 'tankers' }, fleet, h('p', { class: 'hand' }, t('ship.tankers')),
    sh.tankers_effectively_lost ? h('p', { class: 'num-line' }, h('data', { class: 'num', value: '' }, sh.tankers_effectively_lost), ' ', h('span', { class: 'hand' }, t('ship.lost', { n: '' }).replace(/^\s*/, '')), srcLine(s)) : null);

  // day rate
  const vr = sh.vlcc_day_rate_usd || {};
  const rate = h('div', { class: 'box' }, h('h3', { class: 'marker-sm' }, t('ship.vlcc.title')), h('p', { class: 'hand' }, t('ship.dayrate')));
  const rrow = h('div', { class: 'dp-row' });
  for (const k of ['pre_war', 'pandemic_peak', 'now']) {
    if (typeof vr[k] === 'number') rrow.append(dp(mk(vr[k], 'USD/day', s.as_of, s.source, s.source_url, s.stale), { label: t('ship.vlcc.' + k), cls: k === 'now' ? 'red' : '' }));
  }
  rate.append(rrow, ...proposalNotes(proposals, 'shipping.vlcc_day_rate_usd.now'));
  const cost = sh.shipping_cost_per_bbl_usd || {};
  const crow = h('div', { class: 'dp-row' });
  if (typeof cost.before === 'number') crow.append(dp(mk(cost.before, 'USD/bbl', s.as_of, s.source, s.source_url, s.stale), { label: t('ship.before') }));
  if (typeof cost.now === 'number') crow.append(dp(mk(cost.now, 'USD/bbl', s.as_of, s.source, s.source_url, s.stale), { label: t('ship.now'), cls: 'red' }));
  rate.append(h('h3', { class: 'marker-sm' }, t('ship.cost.title')), crow, ...proposalNotes(proposals, 'shipping.shipping_cost_per_bbl_usd.now'));

  const right = h('div', {}, tank, rate);
  wrap.append(h('div', { class: 'cols-2 ship-grid' }, left, right));

  // shipping headlines
  if (news && Array.isArray(news.items)) {
    const items = news.items.filter((i) => Array.isArray(i.topics) && i.topics.includes('shipping')).slice(0, 5);
    if (items.length) {
      const ul = h('ul', { class: 'headlines compact' });
      items.forEach((i) => ul.append(newsItem(i)));
      wrap.append(h('h3', { class: 'marker-sm' }, t('ship.news'), news.stale ? [' ', staleBadge(news)] : null), ul);
    }
  }
  body.replaceChildren(wrap);
}

// ------------------------------------------------------------------ 9.6 groceries
function renderGroceries(manual, prices, proposals) {
  const body = sectionBody('groceries');
  const s = manualSrc(manual);
  const wrap = h('div');
  wrap.append(manualLabel(manual, t('groc.label')));

  // product mix
  const pm = manual.products_mbd || {};
  const keys = ['diesel', 'gasoline', 'jet', 'other'];
  const colors = { diesel: C.COLORS.RED, gasoline: C.COLORS.BLUE, jet: C.COLORS.GREEN, other: C.COLORS.MUTED };
  const max = Math.max(...keys.map((k) => pm[k] || 0));
  const mix = h('div', { class: 'mix' }, h('h3', { class: 'marker-sm' }, t('groc.products')));
  const ol = h('ol', { class: 'bars' });
  keys.forEach((k, i) => { if (typeof pm[k] === 'number') ol.append(h('li', { class: k }, h('span', { class: 'c-name' }, t('groc.product.' + k)), C.hbar(pm[k], max, colors[k], 300 + i), h('data', { class: 'num', value: String(pm[k]) }, fmt.num(pm[k], 0) + ' ' + t('common.mbd')))); });
  mix.append(ol, srcLine(s), h('p', { class: 'hand' }, t('groc.diesel')));

  // refinery shock tally
  const shock = h('div', { class: 'shock' }, h('h3', { class: 'marker-sm' }, t('groc.shock')));
  const tbl = h('table', { class: 'ledger small' });
  const tb = h('tbody');
  (manual.refinery_shock || []).forEach((e, i) => {
    const es = manualSrc(manual, e);
    const th = h('th', { scope: 'row' }, h('span', { class: 'lbl hand' }, e.label));
    if (e.source || e.url) th.append(srcLine(es));
    const notes = proposalNotes(proposals, `refinery_shock[${i}].diesel_delta_mbd`);
    if (notes.length) th.append(h('span', { class: 'notes' }, notes));
    tb.append(h('tr', { class: (e.type || '') + ' drawn' }, th, h('td', { class: 'num red' }, h('data', { value: String(e.diesel_delta_mbd) }, fmt.signed(e.diesel_delta_mbd, 1)))));
  });
  tbl.append(tb);
  shock.append(tbl, srcLine(s));

  // diesel math
  const dm = manual.diesel_math || {};
  const math = h('div', { class: 'box math' }, h('h3', { class: 'marker-sm' }, t('groc.math_title')));
  if (dm.last_year && dm.now) {
    const mt = h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', { scope: 'col' }, t('groc.math.last_year')), h('th', { scope: 'col' }, t('groc.math.now')))));
    const mb = h('tbody');
    for (const k of ['oil', 'crack', 'diesel']) {
      mb.append(h('tr', { class: k === 'diesel' ? 'total' : '' }, h('th', { scope: 'row' }, t('groc.math.' + k)),
        h('td', { class: 'num' }, h('data', { value: String(dm.last_year[k]) }, fmt.money(dm.last_year[k], 0))),
        h('td', { class: 'num' + (k === 'diesel' ? ' red' : '') }, h('data', { value: String(dm.now[k]) }, fmt.money(dm.now[k], 0)))));
    }
    mt.append(mb);
    math.append(mt);
    // The percentages are Fisher's own stated figures (manual.json diesel_math.stated_rise_pct, same source as
    // the note below), not a frontend derivation from the table.
    const rise = dm.stated_rise_pct || {};
    if (typeof rise.oil === 'number' && typeof rise.diesel === 'number') {
      const pct = (v) => h('data', { class: 'num', value: String(v) }, fmt.num(v, 0));
      math.append(h('p', { class: 'hand big-note' }, tf('groc.math', { oil: pct(rise.oil), diesel: pct(rise.diesel) })));
    }
    if (dm.note) math.append(h('small', { class: 'src' }, dm.note));
    math.append(srcLine(s));
  }

  // real pump prices
  const retail = h('div', { class: 'retail' }, h('h3', { class: 'marker-sm' }, t('groc.retail')));
  if (prices && prices.latest && (prices.latest.retail_diesel_us || prices.latest.retail_gasoline_us)) {
    const row = h('div', { class: 'dp-row' });
    if (prices.latest.retail_diesel_us) row.append(dp(prices.latest.retail_diesel_us, { label: t('groc.retail.diesel'), fileStale: prices.stale, big: true }));
    if (prices.latest.retail_gasoline_us) row.append(dp(prices.latest.retail_gasoline_us, { label: t('groc.retail.gasoline'), fileStale: prices.stale, big: true }));
    retail.append(row);
  } else {
    retail.append(unavailableNote());
  }

  // impacts
  const imp = h('div', { class: 'impacts' }, h('h3', { class: 'marker-sm' }, t('groc.impacts')));
  const ul = h('ul', { class: 'stickies' });
  (manual.impacts || []).forEach((x, i) => {
    const xs = manualSrc(manual, x);
    ul.append(h('li', { class: 'sticky', style: `--r:${((i * 7) % 5) - 2}deg` }, h('span', { class: 'marker-sm' }, x.sector), h('span', { class: 'hand' }, x.effect), (x.source || x.url) ? srcLine(xs) : null));
  });
  imp.append(ul, srcLine(s));

  wrap.append(h('div', { class: 'cols-2 groc-grid' }, h('div', {}, mix, shock), h('div', {}, math, retail)), imp);
  body.replaceChildren(wrap);
}

// ------------------------------------------------------------------ 9.7 what changed today
function newsItem(i) {
  const li = h('li', { class: 'headline' });
  li.append(link(i.link, i.title, 'title'));
  const metaLine = h('span', { class: 'meta' }, i.source || '', ' · ', h('time', { datetime: i.published, title: fmt.date(i.published, true) }, fmt.relTime(i.published)));
  li.append(metaLine);
  if (i.snippet && i.snippet.trim() && i.snippet.trim() !== i.title.trim()) li.append(h('p', { class: 'snippet' }, i.snippet));
  if (Array.isArray(i.topics) && i.topics.length) li.append(h('span', { class: 'topics' }, i.topics.map((tp) => h('span', { class: 'tag' }, t('news.topic.' + tp)))));
  return li;
}

function renderNews(summary, newsDoc) {
  const body = sectionBody('news');
  const wrap = h('div');
  const u = updatedLine();
  if (u) wrap.append(h('p', { class: 'hand updated' }, u));

  // summary box
  if (summary) {
    const box = h('div', { class: 'box summary' + (summary.stale ? ' is-stale' : '') });
    if (summary.fallback) box.append(h('p', { class: 'hand fallback' }, t('news.fallback')));
    box.append(h('h3', { class: 'marker' }, summary.headline || '', summary.stale ? [' ', staleBadge(summary)] : null));
    const ul = h('ul', { class: 'changes' });
    const byLink = new Map(((newsDoc && newsDoc.items) || []).map((i) => [i.link, i]));
    (summary.what_changed || []).forEach((c) => {
      // The rule-based summary ends each bullet with "(Publisher)"; the cite link below already carries that name.
      let text = String(c.text || '');
      for (const srcUrl of c.sources || []) {
        const item = byLink.get(srcUrl);
        const suffix = item && item.source ? ` (${item.source})` : '';
        if (suffix && text.endsWith(suffix)) { text = text.slice(0, -suffix.length); break; }
      }
      const li = h('li', {}, text, ' ');
      (c.sources || []).forEach((srcUrl, k) => {
        const item = byLink.get(srcUrl);
        const label = item ? item.source || t('news.source_n', { n: k + 1 }) : t('news.source_n', { n: k + 1 });
        li.append(link(srcUrl, label, 'cite'), ' ');
      });
      ul.append(li);
    });
    box.append(ul);
    if (Array.isArray(summary.quips) && summary.quips.length) box.append(h('p', { class: 'hand quips' }, summary.quips.join('  ')));
    box.append(h('small', { class: 'src' }, summary.provider === 'none' || summary.fallback ? t('news.provider_none') : t('news.provider', { provider: [summary.provider, summary.model].filter(Boolean).join(' ') }), ' · ', t('news.summary_asof', { time: fmt.date(summary.generated_at || summary.as_of, true) })));
    wrap.append(box);
  } else {
    wrap.append(unavailableNote());
  }

  // headlines with topic filter
  if (newsDoc && Array.isArray(newsDoc.items)) {
    const items = newsDoc.items;
    const order = Object.keys(cfg.news_topics || {});
    const present = new Set(items.flatMap((i) => i.topics || []));
    const topics = [...order.filter((x) => present.has(x)), ...[...present].filter((x) => !order.includes(x))];
    const hdr = h('h3', { class: 'marker-sm' }, t('news.headlines'), newsDoc.stale ? [' ', staleBadge(newsDoc)] : null);
    const count = h('p', { class: 'hand' }, t('news.count', { n: items.length }));
    const chips = h('div', { class: 'chips', role: 'group', 'aria-label': t('news.filter_label') });
    const list = h('ul', { class: 'headlines' });
    const empty = h('p', { class: 'hand', hidden: true }, t('news.empty'));
    let current = 'all';
    const btns = [];
    const apply = () => {
      btns.forEach((b) => b.setAttribute('aria-pressed', b.dataset.topic === current ? 'true' : 'false'));
      list.replaceChildren();
      const sel = items.filter((i) => current === 'all' || (i.topics || []).includes(current));
      sel.forEach((i) => list.append(newsItem(i)));
      empty.hidden = sel.length > 0;
    };
    for (const tp of ['all', ...topics]) {
      const b = h('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', onclick: () => { current = tp; apply(); } }, tp === 'all' ? t('news.filter.all') : t('news.topic.' + tp));
      b.dataset.topic = tp;
      btns.push(b);
      chips.append(b);
    }
    wrap.append(hdr, count, chips, list, empty);
    apply();
    const failed = (newsDoc.feeds || []).filter((f) => f && f.ok === false).map((f) => f.id);
    if (failed.length) wrap.append(h('p', { class: 'src' }, t('news.feeds_failed', { list: failed.join(', ') })));
  } else if (summary) {
    wrap.append(unavailableNote());
  }
  body.replaceChildren(wrap);
}

// ------------------------------------------------------------------ methodology
function renderMethodology(prices, manual) {
  const body = sectionBody('methodology');
  const wrap = h('div', { class: 'meth' });
  const ul = h('ul', { class: 'formulas' });
  for (const k of ['diesel', 'gasoline', 'jet', '321']) ul.append(h('li', { class: 'num' }, t('meth.formula.' + k)));
  wrap.append(h('h3', { class: 'marker-sm' }, t('meth.formulas_title')), ul);
  if (prices && prices.latest) {
    const ids = Object.values(prices.latest).map((d) => d && d.series_id).filter(Boolean);
    if (ids.length) wrap.append(h('p', { class: 'src' }, t('meth.series', { ids: ids.join(', ') })));
  }
  const st = prices && prices.stats && prices.stats.diesel_crack;
  const fisher = manual && manual.diesel_math && manual.diesel_math.now && manual.diesel_math.now.crack;
  if (st && typeof fisher === 'number' && prices.latest && prices.latest.diesel_crack) {
    wrap.append(h('p', {}, t('meth.benchmark', { fisher: fmt.num(fisher, 0), ours: fmt.num(prices.latest.diesel_crack.value, 2), max: fmt.num(st.max, 1), max_date: fmt.date(st.max_date) })));
  }
  wrap.append(h('p', {}, t('meth.steo')));
  const sch = cfg.update_schedule_utc || {};
  const now = new Date();
  const times = (sch.hours || []).map((hh) => fmt.time(new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hh, sch.minute || 0)))).join(', ');
  wrap.append(h('p', {}, t('meth.updates', { times, tz: fmt.tzCity() })));
  const sa = cfg.stale_after_hours || {};
  wrap.append(h('p', {}, t('meth.stale', { prices: sa.prices ?? '–', steo: sa.steo ?? '–', news: sa.news ?? '–' })));
  if (prices && prices.history && prices.history.resolution_note) {
    const m = /(\d{4}-\d{2}-\d{2})/.exec(prices.history.resolution_note);
    wrap.append(h('p', {}, t('meth.history', { date: m ? fmt.date(m[1]) : '–', start: fmt.date((st && st.history_start) || (prices.history.dates && prices.history.dates[0]) || '') })));
  }
  wrap.append(h('p', { class: 'hand' }, t('meth.sources_note')));
  body.replaceChildren(wrap);
}

// ------------------------------------------------------------------ 9.8 donate + 9.9 footer
function renderDonate() {
  const sec = byId('donate');
  if (!sec) return;
  if (!isHttp(cfg.donate_url)) { sec.hidden = true; return; }
  sec.hidden = false;
  let v = Number(cfg.donate_cta_variant);
  if (!Number.isInteger(v) || v < 0 || v > 3) v = 0;
  const body = sectionBody('donate');
  const sub = t(`donate.${v}.sub`);
  const btn = h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer', class: 'btn' }, t(`donate.${v}.button`));
  fill(body,
    h('p', { class: 'marker donate-headline' }, t(`donate.${v}.headline`)),
    sub && sub !== `donate.${v}.sub` ? h('p', { class: 'hand' }, sub) : null,
    h('p', { class: 'cta' }, btn, cfg.donate_provider_label ? h('small', { class: 'src' }, t('donate.provider', { provider: cfg.donate_provider_label })) : null),
    h('p', { class: 'src' }, t('donate.fineprint')),
  );
}

function renderFooter() {
  const body = byId('footer-body');
  const insp = cfg.inspiration || {};
  const homes = cfg.source_links || {};
  // Each licence line plus links to the source's home (URLs from config.source_links / config.inspiration).
  const srcLi = (key, urls) => h('li', {}, t('footer.src.' + key), urls.filter(isHttp).map((u) => [' ', link(u, hostLabel(u))]));
  const srcs = h('ul', { class: 'sources' },
    srcLi('fred', [homes.fred]),
    srcLi('eia', [homes.eia_steo, homes.eia_international]),
    srcLi('jodi', [homes.jodi]),
    srcLi('news', []),
    srcLi('manual', [insp.url]),
    srcLi('fonts', []),
  );
  const nav = h('p', { class: 'footer-nav' }, h('a', { href: '#methodology' }, t('footer.methodology')), ' · ', link(cfg.repo_url, t('footer.repo')),
    isHttp(cfg.donate_url) ? [' · ', h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer' }, t('footer.support'))] : null);
  const credit = h('p', { class: 'hand credit' }, tf('footer.credit', { author: insp.author || '', title: link(insp.url, insp.title || ''), date: insp.date ? fmt.dateDayFirst(insp.date) : '' }));
  const last = meta && meta.last_run ? h('p', { class: 'src' }, t('footer.last_update', { time: fmt.date(meta.last_run, true), tz: fmt.tzCity() })) : null;
  fill(body, h('h2', { class: 'marker-sm' }, t('footer.sources')), srcs, h('p', { class: 'disclaimer' }, t('footer.disclaimer')), credit, nav, last, h('p', { class: 'src' }, t('footer.nocookies')));
}

// ------------------------------------------------------------------ orchestration
function safe(name, fn) {
  try {
    fn();
  } catch (e) {
    console.error(`[crackspread] ${name}:`, e);
    const body = sectionBody(name);
    if (body) body.replaceChildren(unavailableNote());
  }
}

async function main() {
  cfg = await loadJSON('./config.json');
  lang = pickLang();
  TZ = cfg.timezone_display || 'Europe/Vienna';
  EN = await loadJSON('./i18n/en.json');
  LANG = lang === 'en' ? EN : await loadJSON(`./i18n/${lang}.json`).catch(() => ({}));
  // A requested language whose file is still an all-empty stub shows English for every string, so the number/date
  // locale and <html lang> follow the strings actually rendered rather than the request.
  if (lang !== 'en' && !Object.values(LANG).some((v) => typeof v === 'string' && v !== '')) { lang = 'en'; LANG = EN; }
  locale = lang === 'de' ? 'de-AT' : 'en-US';
  document.documentElement.lang = lang;
  meta = await loadJSON('./data/meta.json', { cache: 'no-cache' }).catch((e) => { console.warn('[crackspread] meta:', e); return null; });

  renderStatic();
  renderDonate();
  renderFooter();

  // Data URLs are versioned with the run id from meta.json (which is always revalidated), so a new run
  // is fetched fresh and an unchanged one comes from cache. Without meta, revalidate every file.
  const names = ['prices', 'balance', 'countries', 'news', 'summary', 'proposals', 'manual'];
  const ver = meta && typeof meta.run_id === 'string' ? '?v=' + encodeURIComponent(meta.run_id) : '';
  const results = await Promise.allSettled(names.map((n) => loadJSON(`./data/${n}.json${ver}`, ver ? undefined : { cache: 'no-cache' })));
  const D = {};
  names.forEach((n, i) => {
    if (results[i].status === 'fulfilled' && results[i].value && typeof results[i].value === 'object') D[n] = results[i].value;
    else console.warn(`[crackspread] ${n}.json unavailable:`, results[i].reason);
  });
  news = D.news || null;

  safe('hero', () => { if (!D.balance) throw new Error('no balance'); renderHero(D.balance); });
  if (!D.balance) setText('hero-title', t('hero.title_nodata'));
  safe('crack', () => { if (!D.prices) throw new Error('no prices'); renderCrack(D.prices); });
  safe('whiteboard', () => { if (!D.manual) throw new Error('no manual'); renderWhiteboard(D.manual, D.balance, D.proposals); });
  safe('countries', () => { if (!D.countries) throw new Error('no countries'); renderCountries(D.countries); });
  safe('shipping', () => { if (!D.manual) throw new Error('no manual'); renderShipping(D.manual, D.proposals); });
  safe('groceries', () => { if (!D.manual) throw new Error('no manual'); renderGroceries(D.manual, D.prices, D.proposals); });
  safe('news', () => { if (!D.summary && !D.news) throw new Error('no summary/news'); renderNews(D.summary, D.news); });
  safe('methodology', () => renderMethodology(D.prices, D.manual));
}

main().catch((e) => {
  console.error('[crackspread] fatal:', e);
  const main = byId('main');
  if (main) main.prepend(h('p', { class: 'unavailable hand', role: 'alert' }, (EN && EN['common.unavailable']) || 'Data temporarily unavailable.'));
});
