/* Crackspread frontend. Loads config → i18n → meta → data and renders each section independently.
 * Rules: no innerHTML with data, every number through stat()/row()/mini() with <data value>, only http(s) links, all copy from i18n. */
import * as C from './charts.js';

// ------------------------------------------------------------------ state
let cfg = null;
let EN = {};
let LANG = {};
let lang = 'en';
let locale = 'en-US';
let TZ = 'Europe/Vienna';
let meta = null;

const SECTIONS = { hero: 'hero', crack: 'crack', whiteboard: 'wb', countries: 'countries', shipping: 'ship', groceries: 'groc', news: 'news', methodology: 'meth', donate: 'donate' };
const TOC = ['hero', 'crack', 'whiteboard', 'countries', 'shipping', 'groceries', 'news'];

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

const byId = (id) => document.getElementById(id);
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
  dateDayFirst(s) {
    if (typeof s !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(s)) return fmt.date(s);
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', year: 'numeric', month: 'short', day: 'numeric' }).format(new Date(s + 'T00:00:00Z'));
  },
  time(d) {
    const dt = d instanceof Date ? d : new Date(d);
    if (isNaN(dt)) return '–';
    return new Intl.DateTimeFormat(locale, { timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(dt);
  },
  month(s, short = false) {
    const d = new Date(s + '-01T00:00:00Z');
    if (isNaN(d)) return s;
    return new Intl.DateTimeFormat(locale, { timeZone: 'UTC', year: short ? '2-digit' : 'numeric', month: short ? 'short' : 'long' }).format(d);
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

/* Value + unit → [number text, unit text]. */
function valueParts(v, unit) {
  if (typeof v !== 'number' || !isFinite(v)) return ['–', ''];
  switch (unit) {
    case 'USD/bbl': return [fmt.money(v, 2), '/bbl'];
    case 'USD/gal': return [fmt.money(v, 3), '/gal'];
    case 'USD/day': return [fmt.money(v, 0), '/day'];
    case 'USD': return [fmt.money(v, v % 1 ? 2 : 0), ''];
    case 'million barrels per day': case 'mb/d': return [fmt.num(v, 2), t('common.mbd')];
    case 'thousand barrels per day': case 'kb/d': return [fmt.num(v, 0), t('common.kbd')];
    case 'days': return [fmt.num(v, 0), t('ship.days_unit')];
    case '%': return [fmt.pct(v), ''];
    default: return [fmt.num(v, 2), unit || ''];
  }
}
const valueText = (v, unit) => valueParts(v, unit).filter(Boolean).join(' ');

/* Build a datapoint object from loose data (manual.json, balance.json). */
function mk(value, unit, as_of, source, source_url, stale = false, extra = {}) {
  return { value, unit, as_of, source, source_url, stale: !!stale, ...extra };
}

function staleBadge(d) {
  return h('span', { class: 'badge', title: t('common.stale_title', { date: fmt.date(d.as_of) }) }, t('common.stale'));
}

/* Long source names shortened for the small print; the full name stays in the link title. */
function shortSource(name) {
  const n = String(name || '');
  if (/Short-Term Energy Outlook/i.test(n)) return 'EIA STEO';
  if (/International Energy/i.test(n)) return 'EIA';
  if (/^FRED/i.test(n)) return 'FRED';
  if (/PortWatch/i.test(n)) return 'IMF PortWatch';
  if (/JODI/i.test(n)) return 'JODI';
  if (/Max Fisher/i.test(n)) return 'Whiteboard';
  return n.length > 40 ? n.slice(0, 38) + '…' : n;
}

/* "FRED, Oct 6, 2026" with the source linked when it has an http(s) URL. */
function srcText(d, extra) {
  const label = shortSource(d.source || d.source_url);
  const src = isHttp(d.source_url) ? h('a', { href: d.source_url, target: '_blank', rel: 'noopener noreferrer', title: d.source || '' }, label) : h('span', { title: d.source || '' }, label);
  return h('span', { class: 'src' }, tf('common.asof', { source: src, date: fmt.date(d.as_of) }), extra ? [' ', extra] : null);
}

function dataEl(d, text) {
  return h('data', { class: 'num', value: d && typeof d.value === 'number' ? String(d.value) : '' }, text);
}

// ------------------------------------------------------------------ tile components
function tile(o = {}, ...children) {
  const el = h('article', { class: 'tile' + (o.span ? ' s' + o.span : '') + (o.flat ? ' flat' : '') + (o.cls ? ' ' + o.cls : '') });
  if (o.title || o.right) el.append(h('div', { class: 'tile-h' }, h('span', {}, o.title || ''), o.right ? h('span', { class: 'right' }, o.right) : null));
  el.append(...children.flat(Infinity).filter(Boolean));
  if (o.foot) el.append(h('div', { class: 'foot' }, o.foot));
  return el;
}

/* A stat tile: label, big value (+unit), optional sub line, source in the foot. */
function stat(label, d, o = {}) {
  const stale = !!(d && (d.stale || o.fileStale));
  const [num, unit] = o.text !== undefined ? [o.text, o.unit || ''] : valueParts(d ? d.value : null, d ? d.unit : '');
  const value = h('div', { class: 'value' }, dataEl(d, num), unit ? h('span', { class: 'unit' }, unit) : null, stale ? staleBadge(d) : null, o.delta ? h('span', { class: 'delta ' + (o.deltaCls || '') }, o.delta) : null);
  return tile({ span: o.span || 3, title: label, right: o.right, cls: 'stat' + (o.cls ? ' ' + o.cls : '') + (stale ? ' is-stale' : ''), foot: d && !o.noSrc ? srcText(d) : o.foot },
    value, o.sub ? h('div', { class: 'sub' }, o.sub) : null, ...(o.extra || []));
}

/* A compact figure inside a .minis grid. */
function mini(label, d, o = {}) {
  const stale = !!(d && (d.stale || o.fileStale));
  const [num, unit] = o.text !== undefined ? [o.text, o.unit || ''] : valueParts(d ? d.value : null, d ? d.unit : '');
  return h('div', { class: 'mini' + (o.cls ? ' ' + o.cls : '') },
    h('div', { class: 'k' }, label),
    h('div', { class: 'v' }, dataEl(d, num), unit ? h('span', { class: 'unit' }, unit) : null, stale ? staleBadge(d) : null),
    o.d ? h('div', { class: 'd' + (o.dCls ? ' ' + o.dCls : '') }, o.d) : null, ...(o.notes || []));
}

/* A ledger row inside .rows. */
function row(label, d, o = {}) {
  const stale = !!(d && (d.stale || o.fileStale));
  const li = h('li', { class: 'row' + (o.cls ? ' ' + o.cls : '') + (stale ? ' is-stale' : '') });
  const lab = h('span', { class: 'label' }, label);
  if (o.sub) lab.append(h('span', { class: 'sub' }, o.sub));
  if (o.notes) lab.append(...o.notes);
  li.append(lab, h('span', { class: 'value' }, dataEl(d, o.text !== undefined ? o.text : valueText(d ? d.value : null, d ? d.unit : '')), stale ? staleBadge(d) : null));
  return li;
}

const rows = (...items) => h('ul', { class: 'rows' }, items);
const unavailable = (span) => tile({ span: span || 12, flat: true }, h('p', { class: 'unavailable', role: 'status' }, t('common.unavailable')));
const sectionBody = (id) => byId(id + '-body');

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
  return t('common.updated', { time: fmt.time(last), tz: fmt.tzCity(), next: next ? fmt.time(next) : '–' });
}

// ------------------------------------------------------------------ static copy
function renderStatic() {
  document.title = cfg.site_name || t('site.name');
  setText('brand-name', cfg.site_name || t('site.name'));
  setText('skip-link', t('common.skip'));
  for (const [sec, key] of Object.entries(SECTIONS)) setText(sec + '-title', t(key + '.title'));
  const u = updatedLine();
  if (u) setText('masthead-updated', u);
  const toc = byId('toc');
  if (toc) fill(toc, TOC.map((sec) => h('a', { href: '#' + sec }, t('nav.' + sec))));
  const nav = byId('lang-nav');
  if (nav && cfg.show_language_toggle) {
    nav.hidden = false;
    nav.setAttribute('aria-label', t('common.lang.label'));
    for (const l of cfg.languages_available || ['en']) {
      nav.append(h('a', { href: '?lang=' + l, class: 'chip' + (l === lang ? ' on' : ''), 'aria-current': l === lang ? 'true' : null }, t('common.lang.' + l)), ' ');
    }
  }
}

// ------------------------------------------------------------------ 9.1 hero: the balance
function renderHero(balance, countries) {
  const body = sectionBody('hero');
  const cur = balance.current || (balance.months || []).find((m) => m.period === balance.current_month);
  if (!cur) throw new Error('balance: no current month');
  const status = balance.status || 'balanced';
  const asOf = balance.steo_release || balance.as_of;
  const unit = balance.unit || 'million barrels per day';
  // stock_draw is EIA T3_STCHANGE_WORLD: positive = inventories drawn (shortage), negative = inventories built (surplus).
  const draw = typeof cur.stock_draw === 'number' ? cur.stock_draw : null;
  const g = draw === null ? '–' : fmt.num(Math.abs(draw), 2);
  setText('hero-lede', t('hero.lede.' + status, { p: fmt.num(cur.production, 1), c: fmt.num(cur.consumption, 1), g }));
  const mkB = (v) => mk(v, unit, asOf, balance.source, balance.source_url, balance.stale);
  const monthLabel = t(cur.period === balance.current_month ? 'hero.month_estimate' : 'hero.month_forecast', { month: fmt.month(cur.period) });

  const chart = h('div', { class: 'months' });
  const months = (balance.months || []).filter((m) => m.period >= String(new Date().getUTCFullYear() - 1) + '-01' && m.period <= (balance.current_month || '9999').slice(0, 4) + '-12');
  C.monthBars(chart, { months, label: t('hero.chart_label'), fmtMonth: (p) => fmt.month(p, true), legend: { production: t('hero.chart.production'), consumption: t('hero.chart.consumption'), forecast: t('hero.chart.forecast') } });

  // the simple picture: oil in, oil out, where it is used (consumption shares from the annual country data)
  const flow = h('div', { class: 'flow' });
  const cons = countries && countries.consumers;
  const consumers = [];
  let restShare = 0;
  if (cons && Array.isArray(cons.rows) && typeof cons.world_total === 'number' && cons.world_total > 0) {
    cons.rows.slice(0, 6).forEach((r) => consumers.push({ name: r.name, share: (r.value / cons.world_total) * 100 }));
    restShare = Math.max(0, 100 - consumers.reduce((a, c) => a + c.share, 0));
  }
  C.flowChart(flow, {
    supply: cur.production, demand: cur.consumption, gap: draw, supplyText: fmt.num(cur.production, 1) + ' ' + t('common.mbd'), demandText: fmt.num(cur.consumption, 1) + ' ' + t('common.mbd'),
    gapText: draw === null ? '' : t(draw > 0 ? 'hero.flow.gap_out' : 'hero.flow.gap_in', { g: fmt.num(Math.abs(draw), 2) }),
    consumers, restShare, restLabel: t('countries.rest'), labels: { supply: t('hero.production'), demand: t('hero.consumption'), used: t('hero.flow.used', { year: cons && cons.year ? cons.year : '' }) }, title: t('hero.flow_title'),
  });
  const flowFoot = [srcText(mkB(null))];
  if (cons && cons.world_total) flowFoot.push(' · ', srcText(mk(null, '', cons.as_of || countries.as_of, cons.source || countries.source, cons.source_url || countries.source_url, cons.stale)), ' ', t('hero.flow.shares_note', { year: cons.year }));

  fill(body,
    stat(t('hero.production'), mkB(cur.production)),
    stat(t('hero.consumption'), mkB(cur.consumption)),
    stat(t('hero.storage'), mkB(draw === null ? null : -draw), { text: draw === null ? '–' : fmt.signed(-draw, 2), unit: t('common.mbd'), cls: status === 'deficit' ? 'red' : status === 'surplus' ? 'green' : '' }),
    tile({ span: 3, title: t('hero.status_title'), right: monthLabel, foot: [t('hero.quote_source', { edition: balance.steo_edition || '' }), balance.next_release ? '. ' + t('hero.next_release', { date: fmt.date(balance.next_release) }) : null, balance.release_date_estimated ? ' (' + t('hero.release_estimated') + ')' : null] },
      h('div', { class: 'value' }, h('span', { class: 'status ' + status }, t('hero.status.' + status))),
      h('div', { class: 'sub' }, t('hero.tip.' + status))),
    tile({ span: 8, title: t('hero.flow_title'), foot: flowFoot }, flow, h('details', { class: 'showdata' }, h('summary', {}, t('hero.show_months')), chart)),
    tile({ span: 4, flat: true, title: t('hero.eia_says'), foot: link(balance.quote_url || balance.source_url, t('hero.quote_source', { edition: balance.steo_edition || '' })) },
      h('p', { class: 'note' }, balance.quote ? '“' + balance.quote + '”' : '–')),
  );
}

// ------------------------------------------------------------------ 9.2 crack spreads
function renderCrack(prices) {
  const body = sectionBody('crack');
  const L = prices.latest || {};
  const st = (prices.stats && prices.stats.diesel_crack) || {};
  const fileStale = !!prices.stale;
  const labels = (cfg.crack_levels && cfg.crack_levels.labels) || [];
  const cuts = (cfg.crack_levels && cfg.crack_levels.percentile_cuts) || [50, 75, 90, 98];
  const levelLabel = (i) => (EN['crack.level.' + i] !== undefined ? t('crack.level.' + i) : labels[i] || String(i));
  const dc = L.diesel_crack || {};
  const statSrc = { source: dc.source || prices.source, source_url: dc.source_url || prices.source_url, as_of: st.as_of || dc.as_of || prices.as_of, stale: fileStale || !!dc.stale || !!st.stale };
  const start = st.history_start || (prices.history && prices.history.dates && prices.history.dates[0]) || '';
  const lvl = typeof st.level === 'number' ? st.level : null;

  // diesel tile with the percentile scale
  const scale = [];
  if (typeof st.percentile_now === 'number') {
    const bar = h('div', { class: 'scale-bar', role: 'img', 'aria-label': t('crack.scale_label', { start: fmt.date(start) }) });
    cuts.forEach((c) => bar.append(h('span', { class: 'cut', style: `left:${c}%` })));
    bar.append(h('span', { class: 'mark', style: `left:${Math.min(100, Math.max(0, st.percentile_now))}%` }));
    const ol = h('ol', { class: 'scale-levels' });
    for (let i = 0; i < 5; i++) ol.append(h('li', { class: i === lvl ? 'current' : '', 'aria-current': i === lvl ? 'true' : null }, levelLabel(i)));
    scale.push(h('div', { class: 'sub' }, tf('crack.percentile', { pct: h('data', { class: 'num', value: String(st.percentile_now) }, fmt.num(st.percentile_now, 1)), start: fmt.date(start).slice(-4) }), statSrc.stale ? staleBadge(statSrc) : null), bar, ol);
  }
  const diesel = stat(t('crack.diesel'), L.diesel_crack, { span: 4, cls: 'red', fileStale, right: lvl === null ? null : levelLabel(lvl), extra: scale });

  const others = tile({ span: 8, title: t('crack.others'), foot: [srcText({ ...statSrc, as_of: dc.as_of || statSrc.as_of }), ' ', t('crack.footnote')] },
    h('div', { class: 'minis' },
      L.gasoline_crack ? mini(t('crack.gasoline'), L.gasoline_crack, { fileStale }) : null,
      L.jet_crack ? mini(t('crack.jet'), L.jet_crack, { fileStale }) : null,
      L.crack_321 ? mini(t('crack.c321'), L.crack_321, { fileStale }) : null,
      L.brent ? mini(t('crack.brent'), L.brent, { fileStale }) : null,
      L.wti ? mini(t('crack.wti'), L.wti, { fileStale }) : null,
      L.brent_wti_spread ? mini(t('crack.spread'), L.brent_wti_spread, { fileStale, d: prices.stats && prices.stats.brent_wti_spread && typeof prices.stats.brent_wti_spread.mean_2015_2019 === 'number' ? t('crack.spread_mean', { v: fmt.num(prices.stats.brent_wti_spread.mean_2015_2019, 1) }) : null }) : null,
    ));

  // chart tile
  let chart = null;
  const hist = prices.history || {};
  if (Array.isArray(hist.dates) && Array.isArray(hist.diesel_crack) && hist.dates.length) {
    const histSrc = { ...statSrc, as_of: hist.dates[hist.dates.length - 1], stale: fileStale || !!hist.stale || !!dc.stale };
    let api = null;
    const ranges = ['1y', '5y', 'max'];
    const seg = h('div', { class: 'seg', role: 'group', 'aria-label': t('crack.range_label') });
    const btns = ranges.map((r) => h('button', { type: 'button', 'aria-pressed': r === 'max' ? 'true' : 'false', onclick: () => {
      btns.forEach((b) => b.setAttribute('aria-pressed', b.dataset.range === r ? 'true' : 'false'));
      if (api) api.setRange(r);
    } }, t('crack.range.' + r)));
    btns.forEach((b, i) => { b.dataset.range = ranges[i]; seg.append(b); });
    const plotEl = h('div', { class: 'uplot-host' });
    const legend = h('div', { class: 'legend' });
    if (typeof st.max === 'number') legend.append(h('span', { class: 'red' }, t('crack.chart_peak', { date: fmt.date(st.max_date), v: fmt.num(st.max, 1) })));
    if (typeof st.mean_2015_2019 === 'number') legend.append(h('span', { class: 'green' }, t('crack.chart_mean', { v: fmt.num(st.mean_2015_2019, 1) })));
    if (typeof st.mean_prev_year === 'number') legend.append(h('span', {}, t('crack.chart_prev_mean', { year: st.prev_year, v: fmt.num(st.mean_prev_year, 1) })));
    const det = h('details', { class: 'showdata' }, h('summary', {}, t('common.showdata')));
    const N = 60;
    const tb = h('tbody');
    const first = Math.max(0, hist.dates.length - N);
    for (let i = hist.dates.length - 1; i >= first; i--) {
      const v = hist.diesel_crack[i];
      tb.append(h('tr', {}, h('td', {}, fmt.date(hist.dates[i])), h('td', { class: 'num' }, h('data', { value: typeof v === 'number' ? String(v) : '' }, typeof v === 'number' ? fmt.num(v, 2, 2) : '–'))));
    }
    det.append(h('p', { class: 'small' }, t('crack.table.note', { n: Math.min(N, hist.dates.length) })),
      h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('crack.table.date')), h('th', { scope: 'col', class: 'num' }, t('crack.table.value')))), tb));
    chart = tile({ span: 12, title: t('crack.chart_title', { start: fmt.date(start).slice(-4) }), right: seg, foot: srcText(histSrc, histSrc.stale ? staleBadge(histSrc) : null) }, plotEl, legend, det);
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
        plotEl.replaceChildren(h('p', { class: 'unavailable' }, t('common.unavailable')));
      }
    });
  }
  fill(body, diesel, others, chart);
}

// ------------------------------------------------------------------ proposals (per news reports)
function proposalNotes(proposals, target) {
  if (!proposals || !Array.isArray(proposals.proposals)) return [];
  return proposals.proposals.filter((p) => p.target === target).map((p) => {
    const unit = p.unit || (/vlcc/.test(p.target) ? 'USD/day' : /voyage/.test(p.target) ? 'days' : /cost/.test(p.target) ? 'USD/bbl' : 'mb/d');
    return h('small', { class: 'proposal' }, tf('common.proposal', { value: valueText(p.proposed_value, unit), source: link(p.source_url, p.source_title || hostLabel(p.source_url)), date: fmt.date(p.published || '') }));
  });
}

// ------------------------------------------------------------------ 9.3 whiteboard
function manualSrc(manual, entry = {}) {
  const ds = manual.default_source || {};
  return { source: entry.source || ds.name || manual.source, source_url: entry.url || ds.url || manual.source_url, as_of: entry.as_of || manual.updated_at || manual.as_of, stale: !!manual.stale };
}

/* Whiteboard claims next to PortWatch figures and headline mentions (crosscheck.json, deterministic). */
function crosscheckTile(xc) {
  if (!xc || !Array.isArray(xc.claims) || !xc.claims.length) return null;
  const tb = h('tbody');
  for (const c of xc.claims) {
    const wb = c.whiteboard || {};
    const wbText = typeof wb.value === 'number' ? valueText(wb.value, wb.unit || '') : typeof wb.value === 'string' ? (wb.value.length > 90 ? wb.value.slice(0, 88) + '…' : wb.value) : '–';
    const claim = h('td', { class: 'claim' }, h('b', {}, c.label), h('span', { class: 'wbv' }, typeof wb.value === 'number' ? dataEl({ value: wb.value }, wbText) : wbText));
    const pw = c.portwatch;
    const data = pw && typeof pw.tankers_7d === 'number'
      ? h('td', {}, t('xc.pw', { name: pw.chokepoint || '', now: fmt.num(pw.tankers_7d, 1), year: pw.baseline_year || '', base: fmt.num(pw.tankers_baseline, 0), chg: typeof pw.tankers_change_pct === 'number' ? fmt.signed(pw.tankers_change_pct, 0) + '%' : '–' }),
        h('span', { class: 'm s' }, srcText({ source: pw.source, source_url: pw.source_url, as_of: pw.latest_date })))
      : h('td', { class: 'none' }, t('xc.none'));
    const news = h('td', { class: c.mentions.length ? '' : 'none' });
    if (!c.mentions.length) news.append(t('xc.nonews'));
    c.mentions.forEach((m) => news.append(h('span', { class: 'm' }, link(m.link, m.title), m.numbers.length ? h('span', { class: 'n' }, ' [' + m.numbers.slice(0, 3).join(', ') + ']') : null, h('span', { class: 's' }, ' ' + (m.source || '') + ', ' + fmt.relTime(m.published)))));
    tb.append(h('tr', {}, claim, data, news));
  }
  return tile({ span: 12, title: t('xc.title'), right: t('xc.lede'), foot: [srcText({ source: t('news.title'), source_url: '', as_of: xc.as_of }), xc.stale ? staleBadge(xc) : null] },
    h('table', { class: 'xc' }, h('thead', {}, h('tr', {}, h('th', {}, t('xc.col.claim')), h('th', {}, t('xc.col.data')), h('th', {}, t('xc.col.news')))), tb));
}

function renderWhiteboard(manual, balance, proposals, xc) {
  const body = sectionBody('whiteboard');
  const s = manualSrc(manual);
  const w = manual.world || {};
  const list = h('ul', { class: 'rows' });
  (manual.hormuz_ledger || []).forEach((e) => {
    const struck = e.status === 'knocked_out' || e.counted === false;
    const es = manualSrc(manual, e);
    let txt = '–';
    let val = null;
    if (typeof e.delta === 'number') { txt = fmt.signed(e.delta, 1); val = e.delta; }
    else if (typeof e.value === 'number') { txt = fmt.signed(e.value, 1).replace(/^\+/, ''); val = e.value; }
    const sub = struck ? t('wb.saudi_note', { delta: fmt.signed(e.delta, 1) }) : e.note || null;
    const li = row(e.label, mk(val, 'mb/d', es.as_of, es.source, es.source_url, es.stale), { text: txt, cls: (e.type || '') + (struck ? ' struck' : '') + (e.type === 'total' ? ' red' : ''), sub, notes: proposalNotes(proposals, `hormuz_ledger[${e.id}].delta`) });
    if (struck) li.querySelector('.value').append(h('span', { class: 'sr-only' }, ' (' + t('wb.not_counted') + ')'));
    list.append(li);
  });
  const total = (manual.hormuz_ledger || []).find((e) => e.type === 'total');
  let q = null;
  if (balance && Array.isArray(balance.quarters)) {
    const done = balance.quarters.filter((x) => !x.is_forecast && typeof x.stock_draw === 'number');
    q = done.length ? done[done.length - 1] : null;
  }
  const unit = (balance && balance.unit) || 'million barrels per day';
  fill(body,
    tile({ span: 7, title: manual.ledger_label, right: t('common.mbd'), foot: [srcText(s), ' ', link(s.source_url, t('wb.video'))] },
      h('p', { class: 'note' }, t(w.note ? 'wb.world' : 'wb.world_plain', { p: fmt.num(w.production_mbd, 0), c: fmt.num(w.consumption_mbd, 0), note: w.note || '' }), ...proposalNotes(proposals, 'world.production_mbd'), ...proposalNotes(proposals, 'world.consumption_mbd')),
      list),
    tile({ span: 5, flat: true, title: t('wb.official.title'), foot: q ? srcText(mk(null, '', balance.steo_release || balance.as_of, balance.source, balance.source_url, balance.stale)) : null },
      h('div', { class: 'minis' },
        q ? mini(t('wb.official.quarter', { quarter: q.period }), mk(q.stock_draw, unit, balance.steo_release || balance.as_of, balance.source, balance.source_url, balance.stale)) : h('p', { class: 'unavailable' }, t('common.unavailable')),
        total ? mini(t('wb.official.ledger'), mk(total.value, 'mb/d', s.as_of, s.source, s.source_url, s.stale), { text: fmt.signed(total.value, 1), unit: t('common.mbd'), cls: 'red' }) : null),
      q && total && typeof total.value === 'number' ? h('p', { class: 'note' }, t('wb.official.text', { eia: fmt.num(q.stock_draw, 1), quarter: q.period, wb: fmt.signed(total.value, 1) })) : null),
    crosscheckTile(xc),
  );
}

// ------------------------------------------------------------------ 9.4 countries
function renderCountries(countries) {
  const body = sectionBody('countries');
  const unit = countries.unit || 'thousand barrels per day';
  const rankTile = (kind) => {
    const r = countries[kind];
    if (!r || !Array.isArray(r.rows) || !r.rows.length) return unavailable(6);
    const max = Math.max(...r.rows.map((x) => x.value), typeof r.rest_of_world === 'number' ? r.rest_of_world : 0);
    const ol = h('ol', { class: 'bars' });
    r.rows.forEach((x) => ol.append(h('li', {}, h('span', { class: 'c-name' }, x.name), C.hbar(x.value, max, kind === 'producers' ? C.COLORS.BLUE : C.COLORS.RED), h('data', { class: 'num', value: String(x.value) }, fmt.num(x.value, 0)))));
    if (typeof r.rest_of_world === 'number') ol.append(h('li', { class: 'rest' }, h('span', { class: 'c-name' }, t('countries.rest')), C.hbar(r.rest_of_world, max, C.COLORS.MUTED), h('data', { class: 'num', value: String(r.rest_of_world) }, fmt.num(r.rest_of_world, 0))));
    const d = mk(r.world_total, unit, r.as_of || countries.as_of, r.source || countries.source, r.source_url || countries.source_url, r.stale || countries.stale);
    const foot = [srcText(d), ' ', t('countries.year_note', { year: r.year ?? '–' })];
    if (countries.via === 'jodi') foot.push(' ', t('countries.via_jodi'));
    const mc = countries.monthly_crude;
    if (kind === 'producers' && mc && Array.isArray(mc.rows) && mc.rows.length) {
      foot.push(h('br'), t('countries.jodi', { month: fmt.month(mc.latest_month || '', true), unit: t('common.kbd') }), ': ',
        mc.rows.slice(0, 5).map((x, i) => [i ? ' · ' : '', x.name + ' ', h('data', { class: 'num', value: String(x.value) }, fmt.num(x.value, 0))]), (mc.stale || countries.stale) ? staleBadge({ as_of: mc.latest_month }) : null);
    }
    return tile({ span: 6, title: t('countries.' + kind), right: t('countries.unit_year', { year: r.year ?? '–' }), foot }, ol,
      typeof r.world_total === 'number' ? rows(row(t('countries.world_total'), d, { cls: 'total', text: fmt.num(r.world_total, 0) })) : null);
  };
  fill(body, rankTile('producers'), rankTile('consumers'));
}

// ------------------------------------------------------------------ 9.5 shipping
function renderShipping(manual, proposals, ship) {
  const body = sectionBody('shipping');
  const sh = manual.shipping || {};
  const s = manualSrc(manual);
  const mkS = (v, unit) => mk(v, unit, s.as_of, s.source, s.source_url, s.stale);
  const out = [];
  if (ship && Array.isArray(ship.chokepoints) && ship.chokepoints.some((c) => typeof c.tankers_7d === 'number')) {
    const minis = h('div', { class: 'minis' });
    for (const c of ship.chokepoints) {
      if (typeof c.tankers_7d !== 'number') continue;
      const d = mk(c.tankers_7d, '', c.latest_date || ship.as_of, ship.source, ship.source_url, ship.stale);
      const chg = typeof c.tankers_change_pct === 'number' ? fmt.signed(c.tankers_change_pct, 0) + '%' : '';
      const base = typeof c.tankers_baseline === 'number' ? t('ship.choke.baseline', { year: ship.baseline_year, n: fmt.num(c.tankers_baseline, 0) }) : '';
      minis.append(mini(c.name, d, { text: fmt.num(c.tankers_7d, 1), unit: t('ship.choke.unit'), d: [chg, base].filter(Boolean).join(' · '), dCls: typeof c.tankers_change_pct === 'number' && c.tankers_change_pct <= -30 ? 'red' : '', cls: typeof c.tankers_change_pct === 'number' && c.tankers_change_pct <= -30 ? 'red' : '' }));
    }
    out.push(tile({ span: 12, title: t('ship.choke.title', { days: ship.window_days || 7 }), right: t('ship.choke.lede', { year: ship.baseline_year }), foot: [srcText(mk(null, '', ship.as_of, ship.source, ship.source_url, ship.stale), ship.stale ? staleBadge(ship) : null), ' ', t('ship.choke.licence')] }, minis));
  }
  const vr = sh.vlcc_day_rate_usd || {};
  const cost = sh.shipping_cost_per_bbl_usd || {};
  out.push(
    tile({ span: 8, cls: 'map', foot: sh.route_note || '' }, h('img', { src: './img/route-map.svg', alt: t('ship.map_alt'), width: 960, height: 560 })),
    tile({ span: 4, flat: true, title: t('ship.fisher_title'), foot: [srcText(s), ' ', t('ship.label')] },
      h('div', { class: 'minis' },
        mini(t('ship.voyage_before'), mkS(sh.voyage_days_before, 'days')),
        mini(t('ship.voyage_now'), mkS(sh.voyage_days_now, 'days'), { cls: 'red', notes: proposalNotes(proposals, 'shipping.voyage_days_now') }),
        sh.tankers_effectively_lost ? mini(t('ship.lost'), mkS(null, ''), { text: sh.tankers_effectively_lost }) : null,
        typeof vr.pre_war === 'number' ? mini(t('ship.vlcc.pre_war'), mkS(vr.pre_war, 'USD/day')) : null,
        typeof vr.now === 'number' ? mini(t('ship.vlcc.now'), mkS(vr.now, 'USD/day'), { cls: 'red', notes: proposalNotes(proposals, 'shipping.vlcc_day_rate_usd.now') }) : null,
        typeof cost.now === 'number' ? mini(t('ship.cost.now'), mkS(cost.now, 'USD/bbl'), { cls: 'red', d: typeof cost.before === 'number' ? t('ship.cost.before_was', { v: fmt.money(cost.before, 0) }) : null, notes: proposalNotes(proposals, 'shipping.shipping_cost_per_bbl_usd.now') }) : null,
      )),
  );
  fill(body, out);
}

// ------------------------------------------------------------------ 9.6 diesel
function renderGroceries(manual, prices, proposals) {
  const body = sectionBody('groceries');
  const s = manualSrc(manual);
  const L = (prices && prices.latest) || {};
  const pm = manual.products_mbd || {};
  const keys = ['diesel', 'gasoline', 'jet', 'other'];
  const colors = { diesel: C.COLORS.RED, gasoline: C.COLORS.BLUE, jet: C.COLORS.GREEN, other: C.COLORS.MUTED };
  const max = Math.max(...keys.map((k) => pm[k] || 0));
  const ol = h('ol', { class: 'bars' });
  keys.forEach((k) => { if (typeof pm[k] === 'number') ol.append(h('li', { class: k }, h('span', { class: 'c-name' }, t('groc.product.' + k)), C.hbar(pm[k], max, colors[k]), h('data', { class: 'num', value: String(pm[k]) }, fmt.num(pm[k], 0)))); });

  const shock = h('ul', { class: 'rows' });
  (manual.refinery_shock || []).forEach((e, i) => {
    const es = manualSrc(manual, e);
    shock.append(row(e.label, mk(e.diesel_delta_mbd, 'mb/d', es.as_of, es.source, es.source_url, es.stale), { text: fmt.signed(e.diesel_delta_mbd, 1), cls: (e.type || '') + ' red', notes: proposalNotes(proposals, `refinery_shock[${i}].diesel_delta_mbd`) }));
  });

  const dm = manual.diesel_math || {};
  const mathKids = [];
  if (dm.last_year && dm.now) {
    const mb = h('tbody');
    for (const k of ['oil', 'crack', 'diesel']) {
      mb.append(h('tr', { class: k === 'diesel' ? 'total' : '' }, h('th', { scope: 'row' }, t('groc.math.' + k)),
        h('td', { class: 'num' }, h('data', { value: String(dm.last_year[k]) }, fmt.money(dm.last_year[k], 0))),
        h('td', { class: 'num' + (k === 'diesel' ? ' red' : '') }, h('data', { value: String(dm.now[k]) }, fmt.money(dm.now[k], 0)))));
    }
    mathKids.push(h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', { scope: 'col', class: 'num' }, t('groc.math.last_year')), h('th', { scope: 'col', class: 'num' }, t('groc.math.now')))), mb));
    // The percentages are Fisher's own stated figures (manual.json diesel_math.stated_rise_pct), not a frontend derivation.
    const rise = dm.stated_rise_pct || {};
    if (typeof rise.oil === 'number' && typeof rise.diesel === 'number') {
      const pct = (v) => h('data', { class: 'num', value: String(v) }, fmt.num(v, 0));
      mathKids.push(h('p', { class: 'note' }, tf('groc.math', { oil: pct(rise.oil), diesel: pct(rise.diesel) })));
    }
  }
  const pills = h('div', { class: 'pills' }, (manual.impacts || []).map((x) => h('span', { class: 'pill' }, x.sector + ': ', h('b', {}, x.effect))));

  fill(body,
    L.retail_diesel_us ? stat(t('groc.retail.diesel'), L.retail_diesel_us, { cls: 'red', fileStale: prices.stale, right: t('groc.retail') }) : unavailable(3),
    L.retail_gasoline_us ? stat(t('groc.retail.gasoline'), L.retail_gasoline_us, { fileStale: prices.stale, right: t('groc.retail') }) : unavailable(3),
    tile({ span: 6, flat: true, title: t('groc.math_title'), right: t('common.usd_bbl'), foot: [srcText(s), ' ', dm.note || ''] }, mathKids),
    tile({ span: 4, title: t('groc.products'), right: t('common.mbd'), foot: [srcText(s), ' ', t('groc.label')] }, ol),
    tile({ span: 4, title: t('groc.shock'), right: t('common.mbd'), foot: srcText(s) }, shock),
    tile({ span: 4, flat: true, title: t('groc.impacts'), foot: srcText(s) }, pills),
  );
}

// ------------------------------------------------------------------ 9.7 today
function newsItem(i) {
  return h('li', { class: 'headline' }, link(i.link, i.title, 'title'),
    h('span', { class: 'meta' }, i.source || '', ', ', h('time', { datetime: i.published, title: fmt.date(i.published, true) }, fmt.relTime(i.published))));
}

function renderNews(summary, newsDoc) {
  const body = sectionBody('news');
  const out = [];
  if (summary) {
    const ul = h('ul', { class: 'changes' });
    const byLink = new Map(((newsDoc && newsDoc.items) || []).map((i) => [i.link, i]));
    (summary.what_changed || []).forEach((c) => {
      const li = h('li', {}, String(c.text || ''), ' ');
      (c.sources || []).forEach((srcUrl, k) => {
        const item = byLink.get(srcUrl);
        li.append(link(srcUrl, (item && item.source) || t('news.source_n', { n: k + 1 }), 'cite'), ' ');
      });
      ul.append(li);
    });
    out.push(tile({ span: 7, cls: 'summary' + (summary.stale ? ' is-stale' : ''), title: t('news.summary_title'), right: t('news.summary_asof', { time: fmt.date(summary.generated_at || summary.as_of, true) }),
      foot: summary.provider === 'none' || summary.fallback ? t('news.provider_none') : t('news.provider', { provider: [summary.provider, summary.model].filter(Boolean).join(' ') }) },
      h('h3', {}, summary.headline || '', summary.stale ? staleBadge(summary) : null), ul,
      Array.isArray(summary.quips) && summary.quips.length ? h('p', { class: 'quips' }, summary.quips.join(' ')) : null,
      summary.fallback ? h('p', { class: 'fallback' }, t('news.fallback')) : null));
  } else {
    out.push(unavailable(7));
  }
  if (newsDoc && Array.isArray(newsDoc.items)) {
    const items = newsDoc.items;
    const order = Object.keys(cfg.news_topics || {});
    const present = new Set(items.flatMap((i) => i.topics || []));
    const topics = [...order.filter((x) => present.has(x)), ...[...present].filter((x) => !order.includes(x))];
    const chips = h('div', { class: 'chips', role: 'group', 'aria-label': t('news.filter_label') });
    const list = h('ul', { class: 'headlines' });
    const empty = h('p', { class: 'small', hidden: true }, t('news.empty'));
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
    const failed = (newsDoc.feeds || []).filter((f) => f && f.ok === false).map((f) => f.id);
    out.push(tile({ span: 5, title: [t('news.headlines'), newsDoc.stale ? staleBadge(newsDoc) : null], right: t('news.count', { n: items.length }), foot: failed.length ? t('news.feeds_failed', { list: failed.join(', ') }) : null }, chips, list, empty));
    apply();
  } else if (summary) {
    out.push(unavailable(5));
  }
  fill(body, out);
}

// ------------------------------------------------------------------ method
function renderMethodology(prices, manual) {
  const body = sectionBody('methodology');
  const wrap = h('div', { class: 'meth' });
  const ul = h('ul', { class: 'formulas' });
  for (const k of ['diesel', 'gasoline', 'jet', '321']) ul.append(h('li', { class: 'num' }, t('meth.formula.' + k)));
  wrap.append(h('p', {}, t('meth.formulas_title')), ul);
  if (prices && prices.latest) {
    const ids = Object.values(prices.latest).map((d) => d && d.series_id).filter(Boolean);
    if (ids.length) wrap.append(h('p', {}, t('meth.series', { ids: ids.join(', ') })));
  }
  const st = prices && prices.stats && prices.stats.diesel_crack;
  const fisher = manual && manual.diesel_math && manual.diesel_math.now && manual.diesel_math.now.crack;
  if (st && typeof fisher === 'number' && prices.latest && prices.latest.diesel_crack) {
    wrap.append(h('p', {}, t('meth.benchmark', { fisher: fmt.num(fisher, 0), ours: fmt.num(prices.latest.diesel_crack.value, 2), max: fmt.num(st.max, 1), max_date: fmt.date(st.max_date) })));
  }
  wrap.append(h('p', {}, t('meth.steo')), h('p', {}, t('meth.shipping', { days: 7 })));
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
  wrap.append(h('p', {}, t('meth.sources_note')));
  fill(body, tile({ span: 12, flat: true }, h('details', { class: 'method' }, h('summary', {}, t('meth.title')), wrap)));
}

// ------------------------------------------------------------------ donate + footer
function renderDonate() {
  const sec = byId('donate');
  if (!sec) return;
  const nav = byId('donate-nav');
  if (!isHttp(cfg.donate_url)) { sec.hidden = true; if (nav) nav.hidden = true; return; }
  sec.hidden = false;
  if (nav) { nav.hidden = false; nav.href = cfg.donate_url; nav.target = '_blank'; nav.rel = 'noopener noreferrer'; nav.textContent = t('donate.nav'); }
  let v = Number(cfg.donate_cta_variant);
  if (!Number.isInteger(v) || v < 0 || v > 3) v = 0;
  const sub = t(`donate.${v}.sub`);
  fill(sectionBody('donate'), tile({ span: 12, flat: true, title: t('donate.title'), foot: [t('donate.fineprint'), cfg.donate_provider_label ? ' ' + t('donate.provider', { provider: cfg.donate_provider_label }) : null] },
    h('p', { class: 'note' }, t(`donate.${v}.headline`), sub && sub !== `donate.${v}.sub` ? ' ' + sub : null),
    h('p', {}, h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer', class: 'btn' }, t(`donate.${v}.button`)))));
}

function renderFooter() {
  const body = byId('footer-body');
  const insp = cfg.inspiration || {};
  const homes = cfg.source_links || {};
  const srcLi = (key, urls) => h('li', {}, t('footer.src.' + key), urls.filter(isHttp).map((u) => [' ', link(u, hostLabel(u))]));
  const srcs = h('ul', { class: 'sources' }, srcLi('fred', [homes.fred]), srcLi('eia', [homes.eia_steo, homes.eia_international]), srcLi('jodi', [homes.jodi]), srcLi('portwatch', [homes.portwatch]), srcLi('news', []), srcLi('manual', [insp.url]), srcLi('fonts', []));
  const nav = h('p', {}, h('a', { href: '#methodology' }, t('footer.methodology')), ' · ', link(cfg.repo_url, t('footer.repo')),
    isHttp(cfg.donate_url) ? [' · ', h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer' }, t('footer.support'))] : null,
    meta && meta.last_run ? [' · ', t('footer.last_update', { time: fmt.date(meta.last_run, true), tz: fmt.tzCity() })] : null, ' · ', t('footer.nocookies'));
  fill(body, h('h2', {}, t('footer.sources')), srcs, h('p', { class: 'disclaimer' }, t('footer.disclaimer')),
    h('p', {}, tf('footer.credit', { author: insp.author || '', title: link(insp.url, insp.title || ''), date: insp.date ? fmt.dateDayFirst(insp.date) : '' })), nav);
}

// ------------------------------------------------------------------ orchestration
function safe(name, fn) {
  try {
    fn();
  } catch (e) {
    console.error(`[crackspread] ${name}:`, e);
    const body = sectionBody(name);
    if (body) fill(body, unavailable(12));
  }
}

async function main() {
  cfg = await loadJSON('./config.json');
  lang = pickLang();
  TZ = undefined; // times are shown in the visitor's own timezone; cfg.timezone_display is for the backend logs only
  EN = await loadJSON('./i18n/en.json');
  LANG = lang === 'en' ? EN : await loadJSON(`./i18n/${lang}.json`).catch(() => ({}));
  if (lang !== 'en' && !Object.values(LANG).some((v) => typeof v === 'string' && v !== '')) { lang = 'en'; LANG = EN; }
  locale = lang === 'de' ? 'de-AT' : 'en-US';
  document.documentElement.lang = lang;
  meta = await loadJSON('./data/meta.json', { cache: 'no-cache' }).catch((e) => { console.warn('[crackspread] meta:', e); return null; });

  renderStatic();
  renderDonate();
  renderFooter();

  const names = ['prices', 'balance', 'countries', 'shipping', 'news', 'crosscheck', 'summary', 'proposals', 'manual'];
  const ver = meta && typeof meta.run_id === 'string' ? '?v=' + encodeURIComponent(meta.run_id) : '';
  const results = await Promise.allSettled(names.map((n) => loadJSON(`./data/${n}.json${ver}`, ver ? undefined : { cache: 'no-cache' })));
  const D = {};
  names.forEach((n, i) => {
    if (results[i].status === 'fulfilled' && results[i].value && typeof results[i].value === 'object') D[n] = results[i].value;
    else console.warn(`[crackspread] ${n}.json unavailable:`, results[i].reason);
  });

  safe('hero', () => { if (!D.balance) throw new Error('no balance'); renderHero(D.balance, D.countries); });
  if (!D.balance) setText('hero-lede', t('hero.lede_nodata'));
  safe('crack', () => { if (!D.prices) throw new Error('no prices'); renderCrack(D.prices); });
  safe('whiteboard', () => { if (!D.manual) throw new Error('no manual'); renderWhiteboard(D.manual, D.balance, D.proposals, D.crosscheck); });
  safe('countries', () => { if (!D.countries) throw new Error('no countries'); renderCountries(D.countries); });
  safe('shipping', () => { if (!D.manual) throw new Error('no manual'); renderShipping(D.manual, D.proposals, D.shipping); });
  safe('groceries', () => { if (!D.manual) throw new Error('no manual'); renderGroceries(D.manual, D.prices || {}, D.proposals); });
  safe('news', () => { if (!D.summary && !D.news) throw new Error('no summary/news'); renderNews(D.summary, D.news); });
  safe('methodology', () => renderMethodology(D.prices, D.manual));
}

main().catch((e) => {
  console.error('[crackspread] fatal:', e);
  const el = byId('main');
  if (el) el.prepend(h('p', { class: 'unavailable', role: 'alert' }, (EN && EN['common.unavailable']) || 'Data unavailable.'));
});
