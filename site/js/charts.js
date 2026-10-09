/* Crackspread charts: hand-drawn SVG pieces + a thin uPlot wrapper.
 * No text other than what app.js passes in (labels come from i18n/data). */

const NS = 'http://www.w3.org/2000/svg';
const INK = '#1F1F1F';
const RED = '#D7263D';
const BLUE = '#1B4D89';
const GREEN = '#2E8B57';
const MUTED = '#6B6B66'; // same as CSS --muted: used for tick labels and legend text, so it must meet AA on paper

export const COLORS = { INK, RED, BLUE, GREEN, MUTED };

export function svgEl(tag, attrs = {}, children = []) {
  const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined) continue;
    n.setAttribute(k, String(v));
  }
  for (const c of children) {
    if (c === null || c === undefined) continue;
    n.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  }
  return n;
}

export function svgRoot(w, h, label, cls) {
  const s = svgEl('svg', { viewBox: `0 0 ${w} ${h}`, width: '100%', role: 'img', class: cls || 'sketch' });
  if (label) {
    s.setAttribute('aria-label', label);
  } else {
    s.setAttribute('aria-hidden', 'true');
  }
  return s;
}

/* Deterministic pseudo-random so the wobble is stable across re-renders. */
function rng(seed) {
  let s = (seed * 9301 + 49297) % 233280 || 1;
  return () => {
    s = (s * 9301 + 49297) % 233280;
    return s / 233280;
  };
}

function jitter(r, amp) {
  return (r() - 0.5) * 2 * amp;
}

/* A wobbly polyline through the given points, as a path "d" string. */
export function wobbly(points, seed = 1, amp = 1.2, segs = 5) {
  const r = rng(seed);
  let d = '';
  for (let i = 0; i < points.length - 1; i++) {
    const [x1, y1] = points[i];
    const [x2, y2] = points[i + 1];
    if (i === 0) d += `M${(x1 + jitter(r, amp)).toFixed(1)},${(y1 + jitter(r, amp)).toFixed(1)}`;
    for (let k = 1; k <= segs; k++) {
      const t = k / segs;
      const x = x1 + (x2 - x1) * t + (k < segs ? jitter(r, amp) : 0);
      const y = y1 + (y2 - y1) * t + (k < segs ? jitter(r, amp) : 0);
      d += ` L${x.toFixed(1)},${y.toFixed(1)}`;
    }
  }
  return d;
}

/* A sketchy rectangle: two slightly different outlines on top of each other. */
export function sketchRect(x, y, w, h, opts = {}) {
  const { seed = 1, stroke = INK, fill = 'none', width = 2, amp = 1.2 } = opts;
  const g = svgEl('g');
  const pts = [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];
  g.appendChild(svgEl('path', { d: wobbly(pts, seed, amp) + ' Z', fill, stroke, 'stroke-width': width, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
  g.appendChild(svgEl('path', { d: wobbly(pts, seed + 7, amp) + ' Z', fill: 'none', stroke, 'stroke-width': width * 0.6, opacity: 0.45, 'stroke-linejoin': 'round' }));
  return g;
}

/* Marker-style hatch fill for bars (diagonal scribble). */
export function hatch(x, y, w, h, color, seed = 3, gap = 7) {
  const g = svgEl('g', { stroke: color, 'stroke-width': 1.6, 'stroke-linecap': 'round', opacity: 0.85 });
  const r = rng(seed);
  for (let d = -h; d < w; d += gap) {
    const x1 = Math.max(x, x + d);
    const y1 = y + (d < 0 ? -d : 0);
    const x2 = Math.min(x + w, x + d + h);
    const y2 = y + h - Math.max(0, d + h - w);
    if (x2 <= x1) continue;
    g.appendChild(svgEl('line', { x1: x1 + jitter(r, 0.8), y1: y1 + jitter(r, 0.8), x2: x2 + jitter(r, 0.8), y2: y2 + jitter(r, 0.8) }));
  }
  return g;
}

function text(x, y, str, attrs = {}) {
  return svgEl('text', { x, y, ...attrs }, [str]);
}

/* ---------------------------------------------------------------- hero: the balance */
export function balanceScale(container, o) {
  // o: { prod, cons, prodText, consText, prodLabel, consLabel, gapText, gapLabel, status, label }
  const W = 420, H = 300;
  const svg = svgRoot(W, H, o.label);
  const max = Math.max(o.prod || 0, o.cons || 0) * 1.08 || 1;
  const baseY = 232, maxH = 150;
  const barW = 92;
  const lx = 70, rx = W - 70 - barW;
  const hp = Math.max(4, (o.prod / max) * maxH);
  const hc = Math.max(4, (o.cons / max) * maxH);

  // baseline (the table the scale sits on)
  svg.appendChild(svgEl('path', { d: wobbly([[20, baseY + 2], [W - 20, baseY + 2]], 11, 1.5, 12), stroke: INK, 'stroke-width': 3, fill: 'none', 'stroke-linecap': 'round' }));

  // bars
  svg.appendChild(hatch(lx + 4, baseY - hp + 4, barW - 8, hp - 8, BLUE, 21));
  svg.appendChild(sketchRect(lx, baseY - hp, barW, hp, { seed: 5, stroke: BLUE }));
  const consColor = o.status === 'deficit' ? RED : (o.status === 'surplus' ? GREEN : INK);
  svg.appendChild(hatch(rx + 4, baseY - hc + 4, barW - 8, hc - 8, consColor, 31));
  svg.appendChild(sketchRect(rx, baseY - hc, barW, hc, { seed: 9, stroke: consColor }));

  // beam of the scale, tilted toward the heavier side
  const tilt = o.status === 'deficit' ? 5 : (o.status === 'surplus' ? -5 : 0);
  const cx = W / 2, cy = 58;
  const beam = svgEl('g', { transform: `rotate(${tilt} ${cx} ${cy})` });
  beam.appendChild(svgEl('path', { d: wobbly([[cx - 150, cy], [cx + 150, cy]], 41, 1.3, 10), stroke: INK, 'stroke-width': 3, fill: 'none', 'stroke-linecap': 'round' }));
  beam.appendChild(svgEl('path', { d: wobbly([[cx - 150, cy], [cx - 150 + 16, cy - 12]], 42, 0.8, 2), stroke: INK, 'stroke-width': 2.5, fill: 'none' }));
  beam.appendChild(svgEl('path', { d: wobbly([[cx + 150, cy], [cx + 150 - 16, cy - 12]], 43, 0.8, 2), stroke: INK, 'stroke-width': 2.5, fill: 'none' }));
  svg.appendChild(beam);
  // pivot
  svg.appendChild(svgEl('path', { d: wobbly([[cx - 14, cy + 26], [cx, cy + 2], [cx + 14, cy + 26], [cx - 14, cy + 26]], 44, 0.9, 3), stroke: INK, 'stroke-width': 2.5, fill: 'none', 'stroke-linejoin': 'round' }));

  // numbers above the bars
  svg.appendChild(text(lx + barW / 2, baseY - hp - 12, o.prodText, { 'text-anchor': 'middle', class: 'svg-num', fill: BLUE }));
  svg.appendChild(text(rx + barW / 2, baseY - hc - 12, o.consText, { 'text-anchor': 'middle', class: 'svg-num', fill: consColor }));
  // hand labels under the baseline
  svg.appendChild(text(lx + barW / 2, baseY + 30, o.prodLabel, { 'text-anchor': 'middle', class: 'svg-hand' }));
  svg.appendChild(text(rx + barW / 2, baseY + 30, o.consLabel, { 'text-anchor': 'middle', class: 'svg-hand' }));

  // the gap: circled in red between the bars
  const gx = W / 2, gy = 150;
  svg.appendChild(svgEl('path', { d: ellipseSketch(gx, gy, 62, 38, 51), stroke: RED, 'stroke-width': 2.5, fill: 'none' }));
  svg.appendChild(text(gx, gy + 4, o.gapText, { 'text-anchor': 'middle', class: 'svg-num svg-big', fill: RED }));
  svg.appendChild(text(gx, gy + 26, o.gapUnit, { 'text-anchor': 'middle', class: 'svg-hand-sm', fill: RED }));
  container.replaceChildren(svg);
  return svg;
}

export function ellipseSketch(cx, cy, rx, ry, seed = 1) {
  const r = rng(seed);
  let d = '';
  const n = 28;
  for (let i = 0; i <= n + 3; i++) {
    const a = (i / n) * Math.PI * 2 - 0.4;
    const x = cx + Math.cos(a) * (rx + jitter(r, 2.2));
    const y = cy + Math.sin(a) * (ry + jitter(r, 2.2));
    d += (i === 0 ? 'M' : ' L') + x.toFixed(1) + ',' + y.toFixed(1);
  }
  return d;
}

/* ---------------------------------------------------------------- crack-o-meter gauge */
export function gauge(container, o) {
  // o: { percentile (0-100|null), cuts [50,75,90,98], level (0-4|null), ticks: [labels at cuts], label }
  const W = 360, H = 215;
  const svg = svgRoot(W, H, o.label);
  const cx = 180, cy = 185, R = 150, r0 = 100;
  const n = 5;
  const colors = [GREEN, GREEN, INK, RED, RED];
  for (let i = 0; i < n; i++) {
    const a0 = Math.PI + (i / n) * Math.PI;
    const a1 = Math.PI + ((i + 1) / n) * Math.PI;
    const path = arcSlice(cx, cy, r0, R, a0 + 0.012, a1 - 0.012);
    const active = o.level !== null && o.level !== undefined && i <= o.level;
    svg.appendChild(svgEl('path', { d: path, fill: active ? colors[i] : 'none', 'fill-opacity': active ? 0.16 + i * 0.1 : 0, stroke: colors[i], 'stroke-width': 2, 'stroke-linejoin': 'round' }));
    if (active) svg.appendChild(clipHatch(cx, cy, r0, R, a0, a1, colors[i], 60 + i));
  }
  // tick labels at the cuts
  (o.cuts || []).forEach((c, i) => {
    const a = Math.PI + ((i + 1) / n) * Math.PI;
    const tx = cx + Math.cos(a) * (R + 14);
    const ty = cy + Math.sin(a) * (R + 14) + 4;
    svg.appendChild(text(tx, ty, String(c), { 'text-anchor': 'middle', class: 'svg-tick', fill: MUTED }));
  });
  svg.appendChild(text(cx - R - 2, cy + 16, '0', { 'text-anchor': 'middle', class: 'svg-tick', fill: MUTED }));
  svg.appendChild(text(cx + R + 2, cy + 16, '100', { 'text-anchor': 'middle', class: 'svg-tick', fill: MUTED }));

  // needle: piecewise-linear mapping of the percentile onto the 5 equal slices
  let frac = null;
  if (typeof o.percentile === 'number') {
    const bounds = [0, ...(o.cuts || [50, 75, 90, 98]), 100];
    for (let i = 0; i < n; i++) {
      if (o.percentile <= bounds[i + 1] || i === n - 1) {
        const span = bounds[i + 1] - bounds[i] || 1;
        frac = (i + Math.min(1, Math.max(0, (o.percentile - bounds[i]) / span))) / n;
        break;
      }
    }
  }
  const needle = svgEl('g', { class: 'needle', transform: `rotate(${frac === null ? -90 : -90 + frac * 180} ${cx} ${cy})` });
  needle.appendChild(svgEl('path', { d: wobbly([[cx, cy + 6], [cx, cy - R + 18]], 77, 0.8, 4), stroke: INK, 'stroke-width': 3.5, 'stroke-linecap': 'round', fill: 'none' }));
  needle.appendChild(svgEl('circle', { cx, cy, r: 7, fill: INK }));
  if (frac === null) needle.setAttribute('opacity', '0.3');
  svg.appendChild(needle);
  container.replaceChildren(svg);
  return svg;
}

function arcSlice(cx, cy, r0, r1, a0, a1) {
  const p = (r, a) => [cx + Math.cos(a) * r, cy + Math.sin(a) * r];
  const [x0, y0] = p(r1, a0), [x1, y1] = p(r1, a1), [x2, y2] = p(r0, a1), [x3, y3] = p(r0, a0);
  return `M${x0.toFixed(1)},${y0.toFixed(1)} A${r1},${r1} 0 0 1 ${x1.toFixed(1)},${y1.toFixed(1)} L${x2.toFixed(1)},${y2.toFixed(1)} A${r0},${r0} 0 0 0 ${x3.toFixed(1)},${y3.toFixed(1)} Z`;
}

function clipHatch(cx, cy, r0, r1, a0, a1, color, seed) {
  const id = 'clip' + seed + Math.round(a0 * 1000);
  const g = svgEl('g');
  const clip = svgEl('clipPath', { id });
  clip.appendChild(svgEl('path', { d: arcSlice(cx, cy, r0, r1, a0, a1) }));
  g.appendChild(clip);
  const h = hatch(cx - r1, cy - r1, r1 * 2, r1, color, seed, 9);
  h.setAttribute('clip-path', `url(#${id})`);
  h.setAttribute('opacity', '0.35');
  g.appendChild(h);
  return g;
}

/* ---------------------------------------------------------------- horizontal bars (countries, products) */
export function hbar(value, max, color, seed = 1) {
  const W = 300, H = 22;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', class: 'hbar', 'aria-hidden': 'true' });
  const w = Math.max(3, (value / (max || 1)) * (W - 4));
  svg.appendChild(hatch(3, 3, w - 2, H - 6, color, seed, 6));
  svg.appendChild(sketchRect(2, 2, w, H - 4, { seed, stroke: color, amp: 0.9 }));
  return svg;
}

/* ---------------------------------------------------------------- shipping route schematic */
export function routeMap(container, o) {
  // o: { labels: {gulf,hormuz,suez,med,mandab,cape,asia,old,now}, before, now, label }
  const W = 520, H = 340;
  const svg = svgRoot(W, H, o.label);
  const land = { fill: '#F1EFE8', stroke: INK, 'stroke-width': 2, 'stroke-linejoin': 'round' };
  // Africa (schematic blob)
  svg.appendChild(svgEl('path', { d: wobbly([[150, 70], [230, 70], [262, 110], [290, 150], [280, 200], [250, 250], [220, 296], [190, 300], [160, 260], [150, 200], [120, 150], [130, 100], [150, 70]], 101, 2.5, 3) + ' Z', ...land }));
  // Arabian peninsula
  svg.appendChild(svgEl('path', { d: wobbly([[300, 60], [360, 70], [392, 100], [380, 140], [340, 160], [312, 138], [296, 100], [300, 60]], 102, 2, 3) + ' Z', ...land }));
  // Europe / Med coast (top)
  svg.appendChild(svgEl('path', { d: wobbly([[60, 18], [460, 18], [460, 46], [300, 52], [160, 56], [60, 46], [60, 18]], 103, 2, 3) + ' Z', ...land }));
  // Asia (right)
  svg.appendChild(svgEl('path', { d: wobbly([[440, 60], [520, 60], [520, 260], [470, 230], [448, 170], [440, 100], [440, 60]], 104, 2, 3) + ' Z', ...land }));

  // Red Sea channel label marker: Bab al-Mandab closed (red X at the mouth)
  const mx = 298, my = 150;
  svg.appendChild(svgEl('path', { d: wobbly([[mx - 9, my - 9], [mx + 9, my + 9]], 105, 0.6, 2), stroke: RED, 'stroke-width': 3.5, 'stroke-linecap': 'round' }));
  svg.appendChild(svgEl('path', { d: wobbly([[mx + 9, my - 9], [mx - 9, my + 9]], 106, 0.6, 2), stroke: RED, 'stroke-width': 3.5, 'stroke-linecap': 'round' }));

  // old route: Gulf -> Hormuz -> Arabian Sea -> Asia (dashed, faded)
  const oldPts = [[360, 118], [396, 150], [430, 190], [470, 200]];
  svg.appendChild(svgEl('path', { d: wobbly(oldPts, 107, 1.2, 4), stroke: MUTED, 'stroke-width': 3, 'stroke-dasharray': '7 6', fill: 'none', 'stroke-linecap': 'round' }));
  svg.appendChild(arrowHead(470, 200, 20, MUTED));

  // new route: Gulf (pipeline across) -> Red Sea -> Suez -> Med -> Atlantic -> Cape -> Asia (solid blue)
  const newPts = [[340, 118], [312, 110], [296, 76], [286, 54], [240, 60], [120, 66], [92, 110], [96, 200], [140, 290], [205, 318], [280, 300], [360, 250], [420, 220], [470, 212]];
  svg.appendChild(svgEl('path', { d: wobbly(newPts, 108, 1.4, 3), stroke: BLUE, 'stroke-width': 3.5, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' }));
  svg.appendChild(arrowHead(470, 212, 20, BLUE));

  // labels
  const L = o.labels || {};
  svg.appendChild(text(352, 100, L.gulf, { class: 'svg-hand', 'text-anchor': 'middle' }));
  svg.appendChild(text(405, 140, L.hormuz, { class: 'svg-hand-sm', 'text-anchor': 'start' }));
  svg.appendChild(text(300, 46, L.suez, { class: 'svg-hand-sm', 'text-anchor': 'middle', fill: BLUE }));
  svg.appendChild(text(200, 44, L.med, { class: 'svg-hand', 'text-anchor': 'middle', fill: BLUE }));
  svg.appendChild(text(mx + 14, my + 22, L.mandab, { class: 'svg-hand-sm', fill: RED }));
  svg.appendChild(text(214, 334, L.cape, { class: 'svg-hand', 'text-anchor': 'middle', fill: BLUE }));
  svg.appendChild(text(482, 150, L.asia, { class: 'svg-hand', 'text-anchor': 'middle' }));
  // legend: old vs new with days
  svg.appendChild(svgEl('path', { d: 'M20,120 L60,120', stroke: MUTED, 'stroke-width': 3, 'stroke-dasharray': '7 6' }));
  svg.appendChild(text(20, 140, `${L.old} · ${o.before}`, { class: 'svg-hand-sm', fill: MUTED }));
  svg.appendChild(svgEl('path', { d: wobbly([[20, 166], [60, 166]], 109, 1, 3), stroke: BLUE, 'stroke-width': 3.5, fill: 'none' }));
  svg.appendChild(text(20, 186, `${L.now} · ${o.now}`, { class: 'svg-hand-sm', fill: BLUE }));
  container.replaceChildren(svg);
  return svg;
}

function arrowHead(x, y, size, color) {
  return svgEl('path', { d: `M${x - size},${y - size * 0.5} L${x},${y} L${x - size},${y + size * 0.5}`, stroke: color, 'stroke-width': 3, fill: 'none', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
}

/* ---------------------------------------------------------------- uPlot: diesel crack history */
export function crackChart(container, o) {
  // o: { dates: ['YYYY-MM-DD'], values: [num|null], stats: {max, max_date, mean_2015_2019}, labels: {series, mean, record}, unitFmt(v) -> string, locale, dateFmt(ts)->string }
  const uPlot = window.uPlot;
  if (!uPlot) throw new Error('uPlot not loaded');
  const xs = o.dates.map((d) => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10)) / 1000);
  const ys = o.values.map((v) => (typeof v === 'number' ? v : null));
  const mean = typeof o.stats.mean_2015_2019 === 'number' ? o.stats.mean_2015_2019 : null;
  const means = mean === null ? ys.map(() => null) : ys.map(() => mean);
  const maxTs = o.stats.max_date ? Date.UTC(+o.stats.max_date.slice(0, 4), +o.stats.max_date.slice(5, 7) - 1, +o.stats.max_date.slice(8, 10)) / 1000 : null;
  const maxV = typeof o.stats.max === 'number' ? o.stats.max : null;
  const last = xs[xs.length - 1];
  const ranges = { '1y': last - 365 * 86400, '5y': last - 5 * 365 * 86400, max: xs[0] };

  const opts = {
    width: Math.max(280, container.clientWidth || 600),
    height: 280,
    cursor: { drag: { x: false, y: false } },
    legend: { show: true },
    scales: { x: { time: true, min: ranges['1y'], max: last } },
    axes: [
      { stroke: INK, grid: { stroke: 'rgba(27,77,137,0.12)', width: 1 }, ticks: { stroke: 'rgba(31,31,31,0.35)' }, font: '12px -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif',
        values: (u, vals) => vals.map((v) => o.dateFmt(v)) },
      { stroke: INK, grid: { stroke: 'rgba(27,77,137,0.12)', width: 1 }, ticks: { stroke: 'rgba(31,31,31,0.35)' }, font: '12px -apple-system, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif',
        size: 56, values: (u, vals) => vals.map((v) => o.unitFmt(v)) },
    ],
    series: [
      { label: o.labels.date, value: (u, v) => (v === null ? '' : o.dateFmt(v, true)) },
      { label: o.labels.series, stroke: BLUE, width: 2, spanGaps: true, value: (u, v) => (v === null || v === undefined ? '–' : o.unitFmt(v)) },
      { label: o.labels.mean, stroke: GREEN, width: 1.5, dash: [6, 5], spanGaps: true, value: (u, v) => (v === null || v === undefined ? '–' : o.unitFmt(v)) },
    ],
    hooks: {
      draw: [
        (u) => {
          if (maxTs === null || maxV === null) return;
          const ctx = u.ctx;
          const x = u.valToPos(maxTs, 'x', true);
          const y = u.valToPos(maxV, 'y', true);
          if (x < u.bbox.left || x > u.bbox.left + u.bbox.width) return;
          ctx.save();
          ctx.strokeStyle = RED;
          ctx.fillStyle = RED;
          ctx.lineWidth = 2.5 * devicePixelRatio;
          ctx.beginPath();
          ctx.arc(x, y, 7 * devicePixelRatio, 0, Math.PI * 2);
          ctx.stroke();
          ctx.font = `${13 * devicePixelRatio}px Caveat, cursive`;
          ctx.textAlign = x > u.bbox.left + u.bbox.width / 2 ? 'right' : 'left';
          ctx.fillText(o.labels.record, x + (ctx.textAlign === 'left' ? 12 : -12) * devicePixelRatio, y - 10 * devicePixelRatio);
          ctx.restore();
        },
      ],
    },
  };
  const u = new uPlot(opts, [xs, ys, means], container);
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => {
    const w = container.clientWidth;
    if (w > 0) u.setSize({ width: w, height: 280 });
  }) : null;
  if (ro) ro.observe(container);
  return {
    plot: u,
    setRange(name) {
      const min = ranges[name] ?? ranges.max;
      u.setScale('x', { min, max: last });
    },
  };
}
