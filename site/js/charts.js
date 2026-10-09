/* Crackspread charts: plain SVG bars and a thin uPlot wrapper.
 * No text other than what app.js passes in (labels come from i18n/data). */

const NS = 'http://www.w3.org/2000/svg';
const INK = '#161616';
const RED = '#C8102E';
const BLUE = '#1B4D89';
const GREEN = '#1E7A46';
const MUTED = '#6B6B66'; // same as CSS --muted: used for tick labels, so it must meet AA on white
const RULE = '#E3E1DB';

export const COLORS = { INK, RED, BLUE, GREEN, MUTED };
const FONT = '12px -apple-system, BlinkMacSystemFont, Inter, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif';

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

/* ---------------------------------------------------------------- horizontal bar (countries, products) */
export function hbar(value, max, color) {
  const W = 300, H = 14;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', class: 'hbar', 'aria-hidden': 'true' });
  const w = Math.max(2, (value / (max || 1)) * W);
  svg.appendChild(svgEl('rect', { x: 0, y: 2, width: w, height: H - 4, fill: color, rx: 1 }));
  return svg;
}

/* ---------------------------------------------------------------- balance: supply vs demand by month */
export function monthBars(container, o) {
  // o: { months: [{period, production, consumption, is_forecast}], label, fmtMonth(period) -> string, legend: {production, consumption, forecast} }
  const rows = o.months.filter((m) => typeof m.production === 'number' && typeof m.consumption === 'number');
  if (!rows.length) return null;
  const n = rows.length;
  const W = 640, H = 220, padL = 44, padR = 8, padT = 18, padB = 34;
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': o.label });
  const vals = rows.flatMap((m) => [m.production, m.consumption]);
  const lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
  const x = (i) => padL + (i / n) * (W - padL - padR);
  const y = (v) => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const slot = (W - padL - padR) / n;
  const bw = Math.max(2, slot * 0.32);
  // gridlines + axis labels
  for (let v = lo; v <= hi; v += 2) {
    svg.appendChild(svgEl('line', { x1: padL, x2: W - padR, y1: y(v), y2: y(v), stroke: RULE, 'stroke-width': 1 }));
    svg.appendChild(svgEl('text', { x: padL - 6, y: y(v) + 4, 'text-anchor': 'end', fill: MUTED, style: `font: ${FONT}` }, [String(v)]));
  }
  rows.forEach((m, i) => {
    const op = m.is_forecast ? 0.4 : 1;
    svg.appendChild(svgEl('rect', { x: x(i) + slot * 0.15, y: y(m.production), width: bw, height: Math.max(1, y(lo) - y(m.production)), fill: BLUE, opacity: op }));
    svg.appendChild(svgEl('rect', { x: x(i) + slot * 0.15 + bw + 2, y: y(m.consumption), width: bw, height: Math.max(1, y(lo) - y(m.consumption)), fill: RED, opacity: op }));
    if (i % Math.ceil(n / 8) === 0 || i === n - 1) {
      svg.appendChild(svgEl('text', { x: x(i) + slot / 2, y: H - padB + 16, 'text-anchor': 'middle', fill: MUTED, style: `font: ${FONT}` }, [o.fmtMonth(m.period)]));
    }
  });
  // legend
  const lg = svgEl('g', { transform: `translate(${padL}, ${H - 6})` });
  lg.appendChild(svgEl('rect', { x: 0, y: -9, width: 10, height: 10, fill: BLUE }));
  lg.appendChild(svgEl('text', { x: 14, y: 0, fill: MUTED, style: `font: ${FONT}` }, [o.legend.production]));
  lg.appendChild(svgEl('rect', { x: 90, y: -9, width: 10, height: 10, fill: RED }));
  lg.appendChild(svgEl('text', { x: 104, y: 0, fill: MUTED, style: `font: ${FONT}` }, [o.legend.consumption]));
  lg.appendChild(svgEl('rect', { x: 190, y: -9, width: 10, height: 10, fill: INK, opacity: 0.4 }));
  lg.appendChild(svgEl('text', { x: 204, y: 0, fill: MUTED, style: `font: ${FONT}` }, [o.legend.forecast]));
  svg.appendChild(lg);
  container.replaceChildren(svg);
  return svg;
}

/* ---------------------------------------------------------------- uPlot: diesel crack history */
export function crackChart(container, o) {
  // o: { dates: ['YYYY-MM-DD'], values: [num|null], stats: {max, max_date, mean_2015_2019}, labels: {date, series, mean, record}, unitFmt(v) -> string, dateFmt(ts, full) -> string }
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
  const axis = { stroke: MUTED, grid: { stroke: RULE, width: 1 }, ticks: { stroke: RULE }, font: FONT };

  const opts = {
    width: Math.max(280, container.clientWidth || 600),
    height: 260,
    cursor: { drag: { x: false, y: false } },
    legend: { show: false },
    scales: { x: { time: true, min: ranges['1y'], max: last } },
    axes: [
      { ...axis, values: (u, vals) => vals.map((v) => o.dateFmt(v)) },
      { ...axis, size: 52, values: (u, vals) => vals.map((v) => o.unitFmt(v)) },
    ],
    series: [
      { label: o.labels.date, value: (u, v) => (v === null ? '' : o.dateFmt(v, true)) },
      { label: o.labels.series, stroke: INK, width: 1.5, spanGaps: true, value: (u, v) => (v === null || v === undefined ? '–' : o.unitFmt(v)) },
      { label: o.labels.mean, stroke: GREEN, width: 1, dash: [5, 5], spanGaps: true, value: (u, v) => (v === null || v === undefined ? '–' : o.unitFmt(v)) },
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
          ctx.lineWidth = 1.5 * devicePixelRatio;
          ctx.beginPath();
          ctx.arc(x, y, 5 * devicePixelRatio, 0, Math.PI * 2);
          ctx.stroke();
          ctx.font = `${12 * devicePixelRatio}px -apple-system, BlinkMacSystemFont, Inter, Segoe UI, Roboto, sans-serif`;
          ctx.textAlign = x > u.bbox.left + u.bbox.width / 2 ? 'right' : 'left';
          ctx.fillText(o.labels.record, x + (ctx.textAlign === 'left' ? 10 : -10) * devicePixelRatio, y - 8 * devicePixelRatio);
          ctx.restore();
        },
      ],
    },
  };
  const u = new uPlot(opts, [xs, ys, means], container);
  const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(() => {
    const w = container.clientWidth;
    if (w > 0) u.setSize({ width: w, height: 260 });
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
