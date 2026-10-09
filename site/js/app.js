/* Crackspread frontend. Loads config → i18n → meta → data and renders each section independently.
 * Rules: no innerHTML with data, every number through row()/dp(), only http(s) links, all copy from i18n. */
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

const SECTIONS = { hero: 'hero', crack: 'crack', whiteboard: 'wb', countries: 'countries', shipping: 'ship', groceries: 'groc', news: 'news', methodology: 'meth', donate: 'donate' };
const TOC = ['hero', 'crack', 'whiteboard', 'countries', 'shipping', 'groceries', 'news', 'methodology'];

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

/* Value + unit → display string. */
function valueText(v, unit) {
  if (typeof v !== 'number' || !isFinite(v)) return '–';
  switch (unit) {
    case 'USD/bbl': return fmt.money(v, 2) + '/bbl';
    case 'USD/gal': return fmt.money(v, 3) + '/gal';
    case 'USD/day': return fmt.money(v, 0) + '/day';
    case 'USD': return fmt.money(v, v % 1 ? 2 : 0);
    case 'million barrels per day': case 'mb/d': return fmt.num(v, 2) + ' ' + t('common.mbd');
    case 'thousand barrels per day': case 'kb/d': return fmt.num(v, 0) + ' ' + t('common.kbd');
    case 'days': return t('ship.days', { n: fmt.num(v, 0) });
    case '%': return fmt.pct(v);
    default: return fmt.num(v, 2) + (unit ? ' ' + unit : '');
  }
}

/* Build a datapoint object from loose data (manual.json, balance.json). */
function mk(value, unit, as_of, source, source_url, stale = false, extra = {}) {
  return { value, unit, as_of, source, source_url, stale: !!stale, ...extra };
}

function staleBadge(d) {
  return h('span', { class: 'badge', title: t('common.stale_title', { date: fmt.date(d.as_of) }) }, t('common.stale'));
}

/* Source small print: "FRED, as of Oct 6, 2026" with the source linked when it has an http(s) URL. */
function srcLine(d, extra) {
  const src = isHttp(d.source_url) ? link(d.source_url, d.source || d.source_url) : h('span', {}, d.source || '');
  return h('small', { class: 'src' }, tf('common.asof', { source: src, date: fmt.date(d.as_of) }), extra ? [' ', extra] : null);
}

function dataEl(d, text) {
  return h('data', { class: 'num', value: d && typeof d.value === 'number' ? String(d.value) : '' }, text !== undefined ? text : valueText(d ? d.value : null, d ? d.unit : ''));
}

/* THE number device: a label/value row with the source underneath. Every figure on the page goes through here. */
function row(label, d, o = {}) {
  const stale = !!(d && (d.stale || o.fileStale));
  const li = h('li', { class: 'row' + (o.cls ? ' ' + o.cls : '') + (o.big ? ' big' : '') + (stale ? ' is-stale' : '') });
  const lab = h('span', { class: 'label' }, label);
  if (o.sub) lab.append(h('span', { class: 'sub' + (o.subRed ? ' red' : '') }, o.sub));
  if (o.notes) lab.append(...o.notes);
  li.append(lab);
  li.append(h('span', { class: 'value' }, dataEl(d, o.text), stale ? staleBadge(d) : null));
  if (d && !o.noSrc) li.append(srcLine(d));
  return li;
}

const rows = (cls, ...items) => h('ul', { class: 'rows' + (cls ? ' ' + cls : '') }, items);
const unavailableNote = () => h('p', { class: 'unavailable', role: 'status' }, t('common.unavailable'));
const sectionBody = (id) => byId(id + '-body');
const block = (title, ...children) => h('div', { class: 'block' }, title ? h('h3', {}, title) : null, children);

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
  for (const [sec, key] of Object.entries(SECTIONS)) {
    setText(sec + '-title', t(key + '.title'));
    const lede = byId(sec + '-lede');
    if (lede && EN[key + '.lede'] !== undefined) lede.textContent = t(key + '.lede');
  }
  const u = updatedLine();
  const mh = byId('masthead-updated');
  if (mh && u) mh.textContent = u;
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
function renderHero(balance) {
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
  const storageText = draw === null ? '–' : fmt.signed(-draw, 2) + ' ' + t('common.mbd');
  const list = rows('',
    h('li', { class: 'row' }, h('span', { class: 'label' }, t(cur.period === balance.current_month ? 'hero.month_estimate' : 'hero.month_forecast', { month: fmt.month(cur.period) })),
      h('span', { class: 'value' }, h('span', { class: 'status ' + status }, t('hero.status.' + status)))),
    row(t('hero.production'), mkB(cur.production), { big: true }),
    row(t('hero.consumption'), mkB(cur.consumption), { big: true }),
    row(t('hero.storage'), mkB(draw === null ? null : -draw), { big: true, text: storageText, cls: status === 'deficit' ? 'red' : status === 'surplus' ? 'green' : '' }),
  );

  const chart = h('div', { class: 'months' });
  const months = (balance.months || []).filter((m) => m.period >= '2025-01' && m.period <= (balance.current_month || '9999').slice(0, 4) + '-12');
  C.monthBars(chart, { months, label: t('hero.chart_label'), fmtMonth: (p) => fmt.month(p, true), legend: { production: t('hero.chart.production'), consumption: t('hero.chart.consumption'), forecast: t('hero.chart.forecast') } });

  const kick = h('p', { class: 'kicker' });
  if (balance.quote) kick.append('“' + balance.quote + '” ');
  kick.append(link(balance.quote_url || balance.source_url, t('hero.quote_source', { edition: balance.steo_edition || '' })), ', ', fmt.date(asOf), '.');
  if (balance.next_release) kick.append(' ' + t('hero.next_release', { date: fmt.date(balance.next_release) }) + '.');
  if (balance.release_date_estimated) kick.append(' (' + t('hero.release_estimated') + ')');
  fill(body, list, chart, kick);
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

  const list = rows('',
    row(t('crack.diesel'), L.diesel_crack, { big: true, fileStale, cls: 'red' }),
    L.gasoline_crack ? row(t('crack.gasoline'), L.gasoline_crack, { fileStale }) : null,
    L.jet_crack ? row(t('crack.jet'), L.jet_crack, { fileStale }) : null,
    L.crack_321 ? row(t('crack.c321'), L.crack_321, { fileStale }) : null,
    L.brent ? row(t('crack.brent'), L.brent, { fileStale, cls: 'muted' }) : null,
    L.wti ? row(t('crack.wti'), L.wti, { fileStale, cls: 'muted' }) : null,
  );

  // percentile scale
  const scale = h('div', { class: 'scale' });
  if (typeof st.percentile_now === 'number') {
    scale.append(h('p', { class: 'levelline' }, t('crack.diesel') + ': ', h('span', { class: 'lvl' }, lvl === null ? '–' : levelLabel(lvl)), statSrc.stale ? staleBadge(statSrc) : null));
    const bar = h('div', { class: 'scale-bar', role: 'img', 'aria-label': t('crack.scale_label', { start: fmt.date(start) }) });
    cuts.forEach((c) => bar.append(h('span', { class: 'cut', style: `left:${c}%` })));
    bar.append(h('span', { class: 'mark', style: `left:${Math.min(100, Math.max(0, st.percentile_now))}%` }));
    scale.append(bar);
    scale.append(h('div', { class: 'scale-labels' }, h('span', {}, '0'), h('span', {}, '50'), h('span', {}, '100')));
    const ol = h('ol', { class: 'scale-levels' });
    for (let i = 0; i < 5; i++) ol.append(h('li', { class: i === lvl ? 'current' : '', 'aria-current': i === lvl ? 'true' : null }, levelLabel(i)));
    scale.append(ol);
    scale.append(h('p', { class: 'note' }, tf('crack.percentile', { pct: h('data', { class: 'num', value: String(st.percentile_now) }, fmt.num(st.percentile_now, 1)), start: fmt.date(start) })), srcLine(statSrc));
  }

  // chart
  const chart = h('div', { class: 'chart-block' });
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
    chart.append(h('div', { class: 'chart-head' }, h('h3', {}, t('crack.chart_title', { start: fmt.date(start) })), seg), plotEl);
    const legend = h('p', { class: 'legend' });
    if (typeof st.max === 'number') legend.append(h('span', { class: 'red' }, t('crack.chart_peak', { date: fmt.date(st.max_date), v: fmt.num(st.max, 1) })));
    if (typeof st.mean_2015_2019 === 'number') legend.append(h('span', { class: 'green' }, t('crack.chart_mean', { v: fmt.num(st.mean_2015_2019, 1) })));
    if (typeof st.mean_prev_year === 'number') legend.append(h('span', {}, t('crack.chart_prev_mean', { year: st.prev_year, v: fmt.num(st.mean_prev_year, 1) })));
    chart.append(legend, srcLine(histSrc, histSrc.stale ? staleBadge(histSrc) : null));
    const det = h('details', { class: 'showdata' }, h('summary', {}, t('common.showdata')));
    const N = 60;
    const tb = h('tbody');
    const first = Math.max(0, hist.dates.length - N);
    for (let i = hist.dates.length - 1; i >= first; i--) {
      const v = hist.diesel_crack[i];
      tb.append(h('tr', {}, h('td', {}, fmt.date(hist.dates[i])), h('td', { class: 'num' }, h('data', { value: typeof v === 'number' ? String(v) : '' }, typeof v === 'number' ? fmt.num(v, 2, 2) : '–'))));
    }
    det.append(h('p', { class: 'src' }, t('crack.table.note', { n: Math.min(N, hist.dates.length) })),
      h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', { scope: 'col' }, t('crack.table.date')), h('th', { scope: 'col', class: 'num' }, t('crack.table.value')))), tb));
    chart.append(det);
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

  const notes = h('div', { class: 'block' });
  const sp = prices.stats && prices.stats.brent_wti_spread;
  if (sp && typeof sp.value === 'number') {
    notes.append(h('p', { class: 'note' }, t('crack.spread_fact', { v: fmt.num(sp.value, 2), mean: fmt.num(sp.mean_2015_2019, 1) }), ' ', L.brent_wti_spread ? srcLine(L.brent_wti_spread) : null));
  }
  notes.append(h('p', { class: 'small' }, t('crack.footnote')));
  fill(body, h('div', { class: 'cols' }, list, scale), chart, notes);
}

// ------------------------------------------------------------------ proposals (per news reports)
function proposalNotes(proposals, target) {
  if (!proposals || !Array.isArray(proposals.proposals)) return [];
  return proposals.proposals.filter((p) => p.target === target).map((p) => {
    const unit = p.unit || (/vlcc/.test(p.target) ? 'USD/day' : /voyage/.test(p.target) ? 'days' : /cost/.test(p.target) ? 'USD/bbl' : 'mb/d');
    const src = link(p.source_url, p.source_title || hostLabel(p.source_url));
    return h('small', { class: 'proposal' }, tf('common.proposal', { value: valueText(p.proposed_value, unit), source: src, date: fmt.date(p.published || '') }));
  });
}

// ------------------------------------------------------------------ 9.3 whiteboard
function manualSrc(manual, entry = {}) {
  const ds = manual.default_source || {};
  return { source: entry.source || ds.name || manual.source, source_url: entry.url || ds.url || manual.source_url, as_of: entry.as_of || manual.updated_at || manual.as_of, stale: !!manual.stale };
}

function manualLabel(manual, text) {
  const s = manualSrc(manual);
  return h('p', { class: 'small' }, text || manual.ledger_label, '. ', link(s.source_url, t('wb.video')), ', ', fmt.date(s.as_of), '.', manual.stale ? [' ', staleBadge(s)] : null);
}

function renderWhiteboard(manual, balance, proposals) {
  const body = sectionBody('whiteboard');
  const w = manual.world || {};
  const worldNotes = [...proposalNotes(proposals, 'world.production_mbd'), ...proposalNotes(proposals, 'world.consumption_mbd')];
  const intro = h('p', { class: 'note' }, t(w.note ? 'wb.world' : 'wb.world_plain', { p: fmt.num(w.production_mbd, 0), c: fmt.num(w.consumption_mbd, 0), note: w.note || '' }), worldNotes);

  const list = h('ul', { class: 'rows' });
  (manual.hormuz_ledger || []).forEach((e) => {
    const struck = e.status === 'knocked_out' || e.counted === false;
    const s = manualSrc(manual, e);
    let txt = '–';
    let val = null;
    if (typeof e.delta === 'number') { txt = fmt.signed(e.delta, 1); val = e.delta; }
    else if (typeof e.value === 'number') { txt = fmt.signed(e.value, 1).replace(/^\+/, ''); val = e.value; }
    const sub = struck ? t('wb.saudi_note', { delta: fmt.signed(e.delta, 1) }) + (e.status_note ? ' ' + e.status_note : '') : e.note || null;
    const li = row(e.label, mk(val, 'mb/d', s.as_of, s.source, s.source_url, s.stale), { text: txt, cls: (e.type || '') + (struck ? ' struck' : '') + (e.type === 'total' ? ' red' : ''), sub, subRed: struck, noSrc: !(e.source || e.url), notes: proposalNotes(proposals, `hormuz_ledger[${e.id}].delta`) });
    if (struck) li.querySelector('.value').append(h('span', { class: 'sr-only' }, ' (' + t('wb.not_counted') + ')'));
    list.append(li);
  });
  const total = (manual.hormuz_ledger || []).find((e) => e.type === 'total');
  const ms = manualSrc(manual, total || {});

  // official comparison
  let q = null;
  if (balance && Array.isArray(balance.quarters)) {
    const done = balance.quarters.filter((x) => !x.is_forecast && typeof x.stock_draw === 'number');
    q = done.length ? done[done.length - 1] : null;
  }
  const off = h('div', { class: 'block' }, h('h3', {}, t('wb.official.title')));
  if (q && total && typeof total.value === 'number') {
    off.append(h('p', { class: 'note' }, t('wb.official.text', { eia: fmt.num(q.stock_draw, 1), quarter: q.period, wb: fmt.signed(total.value, 1) })));
  }
  const unit = (balance && balance.unit) || 'million barrels per day';
  off.append(rows('',
    q ? row(t('wb.official.quarter', { quarter: q.period }), mk(q.stock_draw, unit, balance.steo_release || balance.as_of, balance.source, balance.source_url, balance.stale)) : h('li', { class: 'row' }, unavailableNote()),
    total ? row(t('wb.official.ledger'), mk(total.value, 'mb/d', ms.as_of, ms.source, ms.source_url, ms.stale), { text: fmt.signed(total.value, 1) + ' ' + t('common.mbd'), cls: 'red' }) : null,
  ));
  fill(body, manualLabel(manual), intro, h('div', { class: 'block' }, list, h('p', { class: 'src' }, srcLine(ms))), off);
}

// ------------------------------------------------------------------ 9.4 countries
function renderCountries(countries) {
  const body = sectionBody('countries');
  const unit = countries.unit || 'thousand barrels per day';
  const sets = { producers: countries.producers, consumers: countries.consumers };
  const listHost = h('div');
  const chips = h('div', { class: 'seg', role: 'group', 'aria-label': t('countries.toggle_label') });
  const btns = {};
  const show = (kind) => {
    for (const k of Object.keys(btns)) btns[k].setAttribute('aria-pressed', k === kind ? 'true' : 'false');
    const r = sets[kind];
    listHost.replaceChildren();
    if (!r || !Array.isArray(r.rows) || !r.rows.length) { listHost.append(unavailableNote()); return; }
    const max = Math.max(...r.rows.map((x) => x.value), typeof r.rest_of_world === 'number' ? r.rest_of_world : 0);
    const ol = h('ol', { class: 'bars' });
    r.rows.forEach((x) => ol.append(h('li', {}, h('span', { class: 'c-name' }, x.name), C.hbar(x.value, max, kind === 'producers' ? C.COLORS.BLUE : C.COLORS.RED), h('data', { class: 'num', value: String(x.value) }, fmt.num(x.value, 0)))));
    if (typeof r.rest_of_world === 'number') ol.append(h('li', { class: 'rest' }, h('span', { class: 'c-name' }, t('countries.rest')), C.hbar(r.rest_of_world, max, C.COLORS.MUTED), h('data', { class: 'num', value: String(r.rest_of_world) }, fmt.num(r.rest_of_world, 0))));
    listHost.append(ol);
    const d = mk(r.world_total, unit, r.as_of || countries.as_of, r.source || countries.source, r.source_url || countries.source_url, r.stale || countries.stale);
    listHost.append(rows('', typeof r.world_total === 'number' ? row(t('countries.world_total'), d, { cls: 'total' }) : h('li', { class: 'row' }, srcLine(d))));
    listHost.append(h('p', { class: 'small' }, t('countries.year_note', { year: r.year ?? '–' }), r.note ? ' ' + r.note + '.' : ''));
    if (countries.via === 'jodi') listHost.append(h('p', { class: 'note' }, t('countries.via_jodi')));
  };
  for (const kind of ['producers', 'consumers']) {
    btns[kind] = h('button', { type: 'button', 'aria-pressed': 'false', onclick: () => show(kind) }, t('countries.' + kind));
    chips.append(btns[kind]);
  }
  const wrap = h('div', {}, chips, h('div', { class: 'block' }, listHost));
  const mc = countries.monthly_crude;
  if (mc && Array.isArray(mc.rows) && mc.rows.length) {
    const ul = h('ul', { class: 'rows' });
    mc.rows.forEach((r) => ul.append(row(r.name, mk(r.value, mc.unit || unit, mc.latest_month, mc.source, mc.source_url, mc.stale || countries.stale), { noSrc: true, text: fmt.num(r.value, 0) })));
    wrap.append(block(t('countries.jodi', { month: fmt.month(mc.latest_month || ''), unit: t('common.kbd') }), ul, h('p', { class: 'src' }, srcLine(mk(null, '', mc.latest_month, mc.source, mc.source_url, mc.stale || countries.stale), (mc.stale || countries.stale) ? staleBadge({ as_of: mc.latest_month }) : null))));
  }
  fill(body, wrap);
  show('producers');
}

// ------------------------------------------------------------------ 9.5 shipping
/* IMF PortWatch: tanker transits per day through the chokepoints, last week vs the previous year's average. */
function chokepointBlock(ship) {
  if (!ship || !Array.isArray(ship.chokepoints) || !ship.chokepoints.length) return null;
  const ul = h('ul', { class: 'rows' });
  for (const c of ship.chokepoints) {
    if (typeof c.tankers_7d !== 'number') continue;
    const d = mk(c.tankers_7d, '', c.latest_date || ship.as_of, ship.source, ship.source_url, ship.stale);
    const base = typeof c.tankers_baseline === 'number' ? t('ship.choke.baseline', { year: ship.baseline_year, n: fmt.num(c.tankers_baseline, 0) }) : null;
    const chg = typeof c.tankers_change_pct === 'number' ? ' (' + fmt.signed(c.tankers_change_pct, 0) + '%)' : '';
    ul.append(row(c.name, d, { text: fmt.num(c.tankers_7d, 1) + chg, sub: base, cls: typeof c.tankers_change_pct === 'number' && c.tankers_change_pct <= -30 ? 'red' : '' }));
  }
  return block(t('ship.choke.title', { days: ship.window_days || 7 }), h('p', { class: 'note' }, t('ship.choke.lede', { year: ship.baseline_year })), ul,
    h('p', { class: 'src' }, srcLine(mk(null, '', ship.as_of, ship.source, ship.source_url, ship.stale), ship.stale ? staleBadge(ship) : null), ' ', t('ship.choke.licence')));
}

function renderShipping(manual, proposals, ship) {
  const body = sectionBody('shipping');
  const sh = manual.shipping || {};
  const s = manualSrc(manual);
  const mkS = (v, unit) => mk(v, unit, s.as_of, s.source, s.source_url, s.stale);
  const map = h('figure', { class: 'map' }, h('img', { src: './img/route-map.svg', alt: t('ship.lede'), width: 960, height: 560 }), h('figcaption', { class: 'small' }, sh.route_note || ''));
  const list = rows('',
    row(t('ship.voyage') + ', ' + t('ship.before').toLowerCase(), mkS(sh.voyage_days_before, 'days')),
    row(t('ship.voyage') + ', ' + t('ship.now').toLowerCase(), mkS(sh.voyage_days_now, 'days'), { cls: 'red', notes: proposalNotes(proposals, 'shipping.voyage_days_now') }),
    sh.tankers_effectively_lost ? row(t('ship.lost'), mkS(null, ''), { text: sh.tankers_effectively_lost }) : null,
  );
  const vr = sh.vlcc_day_rate_usd || {};
  const rate = rows('', ...['pre_war', 'pandemic_peak', 'now'].map((k) => (typeof vr[k] === 'number' ? row(t('ship.vlcc.' + k), mkS(vr[k], 'USD/day'), { cls: k === 'now' ? 'red' : '', notes: k === 'now' ? proposalNotes(proposals, 'shipping.vlcc_day_rate_usd.now') : null }) : null)));
  const cost = sh.shipping_cost_per_bbl_usd || {};
  const costs = rows('',
    typeof cost.before === 'number' ? row(t('ship.before'), mkS(cost.before, 'USD/bbl')) : null,
    typeof cost.now === 'number' ? row(t('ship.now'), mkS(cost.now, 'USD/bbl'), { cls: 'red', notes: proposalNotes(proposals, 'shipping.shipping_cost_per_bbl_usd.now') }) : null,
  );
  const wrap = h('div', {}, chokepointBlock(ship), map, manualLabel(manual, t('ship.label')), h('div', { class: 'cols' }, block(t('ship.voyage'), list), h('div', {}, block(t('ship.vlcc.title'), rate), block(t('ship.cost.title'), costs))));
  if (news && Array.isArray(news.items)) {
    const items = news.items.filter((i) => Array.isArray(i.topics) && i.topics.includes('shipping')).slice(0, 5);
    if (items.length) {
      const ul = h('ul', { class: 'headlines compact' });
      items.forEach((i) => ul.append(newsItem(i)));
      wrap.append(block([t('ship.news'), news.stale ? staleBadge(news) : null], ul));
    }
  }
  fill(body, wrap);
}

// ------------------------------------------------------------------ 9.6 diesel
function renderGroceries(manual, prices, proposals) {
  const body = sectionBody('groceries');
  const s = manualSrc(manual);
  const mkS = (v, unit) => mk(v, unit, s.as_of, s.source, s.source_url, s.stale);

  // real pump prices first: the only live figures in this section
  const retail = h('div', { class: 'block' }, h('h3', {}, t('groc.retail')));
  if (prices && prices.latest && (prices.latest.retail_diesel_us || prices.latest.retail_gasoline_us)) {
    retail.append(rows('',
      prices.latest.retail_diesel_us ? row(t('groc.retail.diesel'), prices.latest.retail_diesel_us, { fileStale: prices.stale, big: true, cls: 'red' }) : null,
      prices.latest.retail_gasoline_us ? row(t('groc.retail.gasoline'), prices.latest.retail_gasoline_us, { fileStale: prices.stale, big: true }) : null));
  } else {
    retail.append(unavailableNote());
  }

  // product mix
  const pm = manual.products_mbd || {};
  const keys = ['diesel', 'gasoline', 'jet', 'other'];
  const colors = { diesel: C.COLORS.RED, gasoline: C.COLORS.BLUE, jet: C.COLORS.GREEN, other: C.COLORS.MUTED };
  const max = Math.max(...keys.map((k) => pm[k] || 0));
  const ol = h('ol', { class: 'bars' });
  keys.forEach((k) => { if (typeof pm[k] === 'number') ol.append(h('li', { class: k }, h('span', { class: 'c-name' }, t('groc.product.' + k)), C.hbar(pm[k], max, colors[k]), h('data', { class: 'num', value: String(pm[k]) }, fmt.num(pm[k], 0) + ' ' + t('common.mbd')))); });
  const mix = block(t('groc.products'), ol, h('p', { class: 'src' }, srcLine(s)));

  // refinery shock
  const shockRows = h('ul', { class: 'rows' });
  (manual.refinery_shock || []).forEach((e, i) => {
    const es = manualSrc(manual, e);
    shockRows.append(row(e.label, mk(e.diesel_delta_mbd, 'mb/d', es.as_of, es.source, es.source_url, es.stale), { text: fmt.signed(e.diesel_delta_mbd, 1) + ' ' + t('common.mbd'), cls: (e.type || '') + ' red', noSrc: !(e.source || e.url), notes: proposalNotes(proposals, `refinery_shock[${i}].diesel_delta_mbd`) }));
  });
  const shock = block(t('groc.shock'), shockRows, h('p', { class: 'src' }, srcLine(s)));

  // diesel math (Fisher's own figures)
  const dm = manual.diesel_math || {};
  const math = h('div', { class: 'block' }, h('h3', {}, t('groc.math_title')));
  if (dm.last_year && dm.now) {
    const mb = h('tbody');
    for (const k of ['oil', 'crack', 'diesel']) {
      mb.append(h('tr', { class: k === 'diesel' ? 'total' : '' }, h('th', { scope: 'row' }, t('groc.math.' + k)),
        h('td', { class: 'num' }, h('data', { value: String(dm.last_year[k]) }, fmt.money(dm.last_year[k], 0))),
        h('td', { class: 'num' + (k === 'diesel' ? ' red' : '') }, h('data', { value: String(dm.now[k]) }, fmt.money(dm.now[k], 0)))));
    }
    math.append(h('table', { class: 'data-table' }, h('thead', {}, h('tr', {}, h('th', {}, ''), h('th', { scope: 'col', class: 'num' }, t('groc.math.last_year')), h('th', { scope: 'col', class: 'num' }, t('groc.math.now')))), mb));
    // The percentages are Fisher's own stated figures (manual.json diesel_math.stated_rise_pct), not a frontend derivation.
    const rise = dm.stated_rise_pct || {};
    if (typeof rise.oil === 'number' && typeof rise.diesel === 'number') {
      const pct = (v) => h('data', { class: 'num', value: String(v) }, fmt.num(v, 0));
      math.append(h('p', { class: 'note' }, tf('groc.math', { oil: pct(rise.oil), diesel: pct(rise.diesel) })));
    }
    if (dm.note) math.append(h('p', { class: 'small' }, dm.note));
    math.append(h('p', { class: 'src' }, srcLine(s)));
  }

  // impacts
  const imp = h('ul', { class: 'rows' });
  (manual.impacts || []).forEach((x) => imp.append(row(x.sector, mkS(null, ''), { text: x.effect, noSrc: true })));
  const impacts = block(t('groc.impacts'), imp, h('p', { class: 'src' }, srcLine(s)));

  fill(body, retail, manualLabel(manual, t('groc.label')), h('div', { class: 'cols' }, h('div', {}, mix, shock), h('div', {}, math, impacts)));
}

// ------------------------------------------------------------------ 9.7 today
function newsItem(i) {
  const li = h('li', { class: 'headline' });
  li.append(link(i.link, i.title, 'title'));
  li.append(h('span', { class: 'meta' }, i.source || '', ', ', h('time', { datetime: i.published, title: fmt.date(i.published, true) }, fmt.relTime(i.published))));
  return li;
}

function renderNews(summary, newsDoc) {
  const body = sectionBody('news');
  const wrap = h('div');
  if (summary) {
    const box = h('div', { class: 'summary' + (summary.stale ? ' is-stale' : '') });
    if (summary.fallback) box.append(h('p', { class: 'fallback' }, t('news.fallback')));
    box.append(h('h3', {}, summary.headline || '', summary.stale ? staleBadge(summary) : null));
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
    box.append(ul);
    if (Array.isArray(summary.quips) && summary.quips.length) box.append(h('p', { class: 'quips' }, summary.quips.join(' ')));
    box.append(h('p', { class: 'src' }, summary.provider === 'none' || summary.fallback ? t('news.provider_none') : t('news.provider', { provider: [summary.provider, summary.model].filter(Boolean).join(' ') }), ', ', t('news.summary_asof', { time: fmt.date(summary.generated_at || summary.as_of, true) })));
    wrap.append(box);
  } else {
    wrap.append(unavailableNote());
  }

  if (newsDoc && Array.isArray(newsDoc.items)) {
    const items = newsDoc.items;
    const order = Object.keys(cfg.news_topics || {});
    const present = new Set(items.flatMap((i) => i.topics || []));
    const topics = [...order.filter((x) => present.has(x)), ...[...present].filter((x) => !order.includes(x))];
    const chips = h('div', { class: 'chips', role: 'group', 'aria-label': t('news.filter_label') });
    const list = h('ul', { class: 'headlines' });
    const empty = h('p', { class: 'note', hidden: true }, t('news.empty'));
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
    wrap.append(block([t('news.headlines'), newsDoc.stale ? staleBadge(newsDoc) : null], h('p', { class: 'small' }, t('news.count', { n: items.length })), chips, list, empty));
    apply();
    const failed = (newsDoc.feeds || []).filter((f) => f && f.ok === false).map((f) => f.id);
    if (failed.length) wrap.append(h('p', { class: 'src' }, t('news.feeds_failed', { list: failed.join(', ') })));
  } else if (summary) {
    wrap.append(unavailableNote());
  }
  fill(body, wrap);
}

// ------------------------------------------------------------------ method
function renderMethodology(prices, manual) {
  const body = sectionBody('methodology');
  const wrap = h('div', { class: 'meth' });
  const ul = h('ul', { class: 'formulas' });
  for (const k of ['diesel', 'gasoline', 'jet', '321']) ul.append(h('li', { class: 'num' }, t('meth.formula.' + k)));
  wrap.append(h('h3', {}, t('meth.formulas_title')), ul);
  if (prices && prices.latest) {
    const ids = Object.values(prices.latest).map((d) => d && d.series_id).filter(Boolean);
    if (ids.length) wrap.append(h('p', { class: 'small' }, t('meth.series', { ids: ids.join(', ') })));
  }
  const st = prices && prices.stats && prices.stats.diesel_crack;
  const fisher = manual && manual.diesel_math && manual.diesel_math.now && manual.diesel_math.now.crack;
  if (st && typeof fisher === 'number' && prices.latest && prices.latest.diesel_crack) {
    wrap.append(h('p', {}, t('meth.benchmark', { fisher: fmt.num(fisher, 0), ours: fmt.num(prices.latest.diesel_crack.value, 2), max: fmt.num(st.max, 1), max_date: fmt.date(st.max_date) })));
  }
  wrap.append(h('p', {}, t('meth.steo')));
  wrap.append(h('p', {}, t('meth.shipping', { days: 7 })));
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
  fill(body, wrap);
}

// ------------------------------------------------------------------ donate + footer
function renderDonate() {
  const sec = byId('donate');
  if (!sec) return;
  if (!isHttp(cfg.donate_url)) { sec.hidden = true; return; }
  sec.hidden = false;
  let v = Number(cfg.donate_cta_variant);
  if (!Number.isInteger(v) || v < 0 || v > 3) v = 0;
  const sub = t(`donate.${v}.sub`);
  fill(sectionBody('donate'),
    h('p', { class: 'lede' }, t(`donate.${v}.headline`)),
    sub && sub !== `donate.${v}.sub` ? h('p', { class: 'note' }, sub) : null,
    h('p', { class: 'cta' }, h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer', class: 'btn' }, t(`donate.${v}.button`)), cfg.donate_provider_label ? [' ', h('small', { class: 'src' }, t('donate.provider', { provider: cfg.donate_provider_label }))] : null),
    h('p', { class: 'small' }, t('donate.fineprint')),
  );
}

function renderFooter() {
  const body = byId('footer-body');
  const insp = cfg.inspiration || {};
  const homes = cfg.source_links || {};
  const srcLi = (key, urls) => h('li', {}, t('footer.src.' + key), urls.filter(isHttp).map((u) => [' ', link(u, hostLabel(u))]));
  const srcs = h('ul', { class: 'sources' }, srcLi('fred', [homes.fred]), srcLi('eia', [homes.eia_steo, homes.eia_international]), srcLi('jodi', [homes.jodi]), srcLi('portwatch', [homes.portwatch]), srcLi('news', []), srcLi('manual', [insp.url]), srcLi('fonts', []));
  const nav = h('p', { class: 'footer-nav' }, h('a', { href: '#methodology' }, t('footer.methodology')), ' · ', link(cfg.repo_url, t('footer.repo')),
    isHttp(cfg.donate_url) ? [' · ', h('a', { href: cfg.donate_url, target: '_blank', rel: 'noopener noreferrer' }, t('footer.support'))] : null);
  const credit = h('p', {}, tf('footer.credit', { author: insp.author || '', title: link(insp.url, insp.title || ''), date: insp.date ? fmt.dateDayFirst(insp.date) : '' }));
  const last = meta && meta.last_run ? h('p', { class: 'small' }, t('footer.last_update', { time: fmt.date(meta.last_run, true), tz: fmt.tzCity() })) : null;
  fill(body, h('h2', {}, t('footer.sources')), srcs, h('p', { class: 'disclaimer' }, t('footer.disclaimer')), credit, nav, last, h('p', { class: 'small' }, t('footer.nocookies')));
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
  if (lang !== 'en' && !Object.values(LANG).some((v) => typeof v === 'string' && v !== '')) { lang = 'en'; LANG = EN; }
  locale = lang === 'de' ? 'de-AT' : 'en-US';
  document.documentElement.lang = lang;
  meta = await loadJSON('./data/meta.json', { cache: 'no-cache' }).catch((e) => { console.warn('[crackspread] meta:', e); return null; });

  renderStatic();
  renderDonate();
  renderFooter();

  const names = ['prices', 'balance', 'countries', 'shipping', 'news', 'summary', 'proposals', 'manual'];
  const ver = meta && typeof meta.run_id === 'string' ? '?v=' + encodeURIComponent(meta.run_id) : '';
  const results = await Promise.allSettled(names.map((n) => loadJSON(`./data/${n}.json${ver}`, ver ? undefined : { cache: 'no-cache' })));
  const D = {};
  names.forEach((n, i) => {
    if (results[i].status === 'fulfilled' && results[i].value && typeof results[i].value === 'object') D[n] = results[i].value;
    else console.warn(`[crackspread] ${n}.json unavailable:`, results[i].reason);
  });
  news = D.news || null;

  safe('hero', () => { if (!D.balance) throw new Error('no balance'); renderHero(D.balance); });
  if (!D.balance) setText('hero-lede', t('hero.lede_nodata'));
  safe('crack', () => { if (!D.prices) throw new Error('no prices'); renderCrack(D.prices); });
  safe('whiteboard', () => { if (!D.manual) throw new Error('no manual'); renderWhiteboard(D.manual, D.balance, D.proposals); });
  safe('countries', () => { if (!D.countries) throw new Error('no countries'); renderCountries(D.countries); });
  safe('shipping', () => { if (!D.manual) throw new Error('no manual'); renderShipping(D.manual, D.proposals, D.shipping); });
  safe('groceries', () => { if (!D.manual) throw new Error('no manual'); renderGroceries(D.manual, D.prices, D.proposals); });
  safe('news', () => { if (!D.summary && !D.news) throw new Error('no summary/news'); renderNews(D.summary, D.news); });
  safe('methodology', () => renderMethodology(D.prices, D.manual));
}

main().catch((e) => {
  console.error('[crackspread] fatal:', e);
  const el = byId('main');
  if (el) el.prepend(h('p', { class: 'unavailable', role: 'alert' }, (EN && EN['common.unavailable']) || 'Data unavailable.'));
});
