"""Render site/img/route-map.svg: real coastlines (world.geo.json, public domain) with the old Gulf→Asia
tanker route and the detour via Suez and the Cape of Good Hope.

Usage: python tools/make_map.py <countries.geo.json> [--out site/img/route-map.svg]
The day figures in the legend come from site/data/manual.json (shipping.voyage_days_*), nothing is typed in.
Equirectangular projection; fine for a schematic at this scale.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LON0, LON1, LAT0, LAT1 = -28.0, 125.0, -46.0, 52.0
W, H = 960, 560

INK, MUTED, RED, BLUE, LAND, SEA, RULE = "#161616", "#6B6B66", "#C8102E", "#1B4D89", "#E9E7E1", "#FFFFFF", "#D6D4CE"
FONT = "-apple-system, BlinkMacSystemFont, Inter, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif"

OLD_ROUTE = [(50.2, 26.4), (54.5, 25.0), (56.6, 26.2), (58.5, 24.5), (63.0, 21.0), (70.0, 12.0), (78.0, 5.5), (84.0, 5.0),
             (95.0, 5.0), (100.5, 3.0), (103.6, 1.4), (108.0, 6.0), (114.0, 15.0), (120.0, 22.0)]
NEW_ROUTE = [(38.1, 24.1), (35.5, 27.0), (33.0, 29.0), (32.4, 30.5), (32.3, 31.6), (28.0, 33.0), (18.0, 35.5), (10.5, 37.8),
             (0.0, 36.5), (-5.8, 35.9), (-11.0, 32.0), (-17.5, 22.0), (-18.5, 12.0), (-12.0, 2.0), (-2.0, -8.0), (6.0, -20.0),
             (12.0, -31.0), (17.5, -36.0), (24.0, -36.5), (36.0, -33.0), (50.0, -25.0), (66.0, -10.0), (78.0, 3.0),
             (95.0, 5.0), (100.5, 3.0), (103.6, 1.4), (108.0, 6.0), (114.0, 15.0), (120.0, 22.0)]
LABELS = [  # (lon, lat, text, anchor, colour)
    (50.5, 29.5, "Gulf", "middle", INK), (57.5, 28.3, "Hormuz", "start", RED), (44.5, 11.4, "Bab al-Mandab", "start", RED),
    (32.0, 33.2, "Suez", "end", BLUE), (14.0, 40.0, "Mediterranean", "middle", BLUE), (-7.0, 38.3, "Gibraltar", "end", BLUE),
    (18.5, -40.5, "Cape of Good Hope", "middle", BLUE), (103.0, -2.5, "Singapore", "middle", INK), (116.0, 26.5, "East Asia", "start", INK),
    (-5.0, 0.0, "Atlantic", "middle", MUTED), (72.0, -14.0, "Indian Ocean", "middle", MUTED), (20.0, 12.0, "Africa", "middle", MUTED),
    (48.0, 22.0, "Arabia", "middle", MUTED), (79.0, 20.0, "India", "middle", MUTED),
]
CLOSED = [(56.4, 26.5), (43.4, 12.6)]  # Hormuz, Bab al-Mandab


def proj(lon: float, lat: float) -> tuple[float, float]:
    x = (lon - LON0) / (LON1 - LON0) * W
    y = (LAT1 - lat) / (LAT1 - LAT0) * H
    return round(x, 1), round(y, 1)


def ring_path(ring) -> str:
    pts = [proj(lon, lat) for lon, lat in ring]
    if len(pts) < 3:
        return ""
    return "M" + " L".join(f"{x},{y}" for x, y in pts) + " Z"


def land_paths(geo: dict) -> str:
    out = []
    for f in geo["features"]:
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            for ring in poly:
                lons = [p[0] for p in ring]
                lats = [p[1] for p in ring]
                if max(lons) < LON0 - 5 or min(lons) > LON1 + 5 or max(lats) < LAT0 - 5 or min(lats) > LAT1 + 5:
                    continue
                d = ring_path(ring)
                if d:
                    out.append(d)
    return " ".join(out)


def route_path(points) -> str:
    pts = [proj(*p) for p in points]
    return "M" + " L".join(f"{x},{y}" for x, y in pts)


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("geojson")
    ap.add_argument("--out", default=str(ROOT / "site" / "img" / "route-map.svg"))
    a = ap.parse_args()
    geo = json.load(open(a.geojson, encoding="utf-8"))
    manual = json.load(open(ROOT / "site" / "data" / "manual.json", encoding="utf-8"))
    sh = manual.get("shipping", {})
    before, now = sh.get("voyage_days_before"), sh.get("voyage_days_now")

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" role="img" aria-labelledby="t">',
             '<title id="t">Tanker routes from the Gulf to Asia: the direct route through Hormuz and the detour via Suez, the Atlantic and the Cape of Good Hope</title>',
             f'<rect width="{W}" height="{H}" fill="{SEA}"/>',
             f'<path d="{land_paths(geo)}" fill="{LAND}" stroke="{RULE}" stroke-width="0.6" fill-rule="evenodd"/>',
             f'<path d="{route_path(OLD_ROUTE)}" fill="none" stroke="{MUTED}" stroke-width="2.5" stroke-dasharray="6 5" stroke-linecap="round" stroke-linejoin="round"/>',
             f'<path d="{route_path(NEW_ROUTE)}" fill="none" stroke="{BLUE}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>']
    for lon, lat in CLOSED:
        x, y = proj(lon, lat)
        parts.append(f'<g stroke="{RED}" stroke-width="2.5" stroke-linecap="round"><line x1="{x-5}" y1="{y-5}" x2="{x+5}" y2="{y+5}"/><line x1="{x+5}" y1="{y-5}" x2="{x-5}" y2="{y+5}"/></g>')
    for lon, lat, text, anchor, colour in LABELS:
        x, y = proj(lon, lat)
        parts.append(f'<text x="{x}" y="{y}" text-anchor="{anchor}" fill="{colour}" font-family="{FONT}" font-size="13">{esc(text)}</text>')
    # legend
    lx, ly = 24, H - 54
    parts.append(f'<rect x="{lx-10}" y="{ly-18}" width="300" height="48" fill="{SEA}" opacity="0.92"/>')
    parts.append(f'<line x1="{lx}" y1="{ly}" x2="{lx+36}" y2="{ly}" stroke="{MUTED}" stroke-width="2.5" stroke-dasharray="6 5"/>')
    parts.append(f'<text x="{lx+46}" y="{ly+4}" fill="{MUTED}" font-family="{FONT}" font-size="13">Direct route through Hormuz, about {before} days</text>')
    parts.append(f'<line x1="{lx}" y1="{ly+22}" x2="{lx+36}" y2="{ly+22}" stroke="{BLUE}" stroke-width="2.5"/>')
    parts.append(f'<text x="{lx+46}" y="{ly+26}" fill="{BLUE}" font-family="{FONT}" font-size="13">Detour via Suez and the Cape, about {now} days</text>')
    parts.append(f'<text x="{W-12}" y="{H-10}" text-anchor="end" fill="{MUTED}" font-family="{FONT}" font-size="10">Coastlines: world.geo.json (public domain). Routes schematic.</text>')
    parts.append("</svg>")
    Path(a.out).write_text("\n".join(parts), encoding="utf-8")
    print(a.out, Path(a.out).stat().st_size, "bytes")


if __name__ == "__main__":
    main()
