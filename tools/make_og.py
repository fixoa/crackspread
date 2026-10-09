"""Render site/img/og.png (1200x630) in the whiteboard style with Pillow.

The image is static (no data numbers: the page updates three times a day, the share image does not).
Fonts are the repo's own subsets in site/fonts/*.woff2 (Pillow/FreeType reads WOFF2), so the render
is reproducible from the repo alone. Pillow is a dev-only dependency (requirements-dev.txt, not
requirements.txt):

    .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python tools/make_og.py

Layout (checked by asserts at the end so a copy change cannot silently overlap again): brand mark
+ wordmark top left, subtitle lines and the "no ads" stickers in the left column, the running-gag
tagline top right above a chart doodle in the right column.
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "site" / "fonts"
OUT = ROOT / "site" / "img" / "og.png"
W, H = 1200, 630
PAPER, INK, RED, BLUE, GREEN, MUTED, INK_SOFT = "#FBFAF7", "#1F1F1F", "#D7263D", "#1B4D89", "#2E8B57", "#6B6B66", "#55554F"
GRID = (27, 77, 137, 22)
MARGIN = 72


def font(name: str, size: int, weight: int | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(FONTS / name), size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except Exception:  # static font or FreeType without variation support: keep the default weight
            pass
    return f


img = Image.new("RGB", (W, H), PAPER)
d = ImageDraw.Draw(img, "RGBA")
for x in range(0, W, 32):
    d.line([(x, 0), (x, H)], fill=GRID, width=1)
for y in range(0, H, 32):
    d.line([(0, y), (W, y)], fill=GRID, width=1)

marker = font("PermanentMarker-latin.woff2", 104)
marker_s = font("PermanentMarker-latin.woff2", 34)
hand = font("Caveat-latin.woff2", 44, 500)
hand_b = font("Caveat-latin.woff2", 54, 700)
hand_tag = font("Caveat-latin.woff2", 48, 700)
hand_tag_s = font("Caveat-latin.woff2", 38, 500)

rnd = random.Random(7)
boxes: dict[str, tuple[float, float, float, float]] = {}


def wob(points, amp=2.0, segs=6):
    out = []
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        for k in range(segs):
            t = k / segs
            out.append((x1 + (x2 - x1) * t + rnd.uniform(-amp, amp), y1 + (y2 - y1) * t + rnd.uniform(-amp, amp)))
    out.append(points[-1])
    return out


def put(name: str, xy, s: str, f, fill, anchor="ls"):
    """Draw text and remember its bounding box (for the overlap asserts)."""
    d.text(xy, s, font=f, fill=fill, anchor=anchor)
    boxes[name] = d.textbbox(xy, s, font=f, anchor=anchor)


# wordmark top left with its red underline; the favicon's marker drop in the top-right corner
put("title", (MARGIN, 162), "Crackspread", marker, INK)
title_end = boxes["title"][2]
d.line(wob([(MARGIN + 6, 186), (title_end - 4, 182)], 3, 14), fill=RED, width=6, joint="curve")
d.line(wob([(MARGIN + 14, 196), (title_end - 40, 194)], 2, 10), fill=(215, 38, 61, 140), width=3, joint="curve")
boxes["underline"] = (MARGIN, 178, title_end, 200)
dx, dy = 1120, 66
drop = [(dx, dy), (dx - 20, dy + 34), (dx - 22, dy + 58), (dx - 6, dy + 74), (dx + 10, dy + 74), (dx + 24, dy + 58), (dx + 22, dy + 34), (dx, dy)]
d.polygon(drop, fill=RED, outline=INK)
d.line(drop, fill=INK, width=4, joint="curve")
boxes["drop"] = (dx - 22, dy, dx + 24, dy + 74)

# left column: subtitle (two lines so it stays clear of the chart), the clean-show line, stickers
put("sub1", (MARGIN + 2, 262), "The world oil balance,", hand_b, INK_SOFT)
put("sub2", (MARGIN + 2, 318), "explained like a whiteboard.", hand_b, INK_SOFT)
put("clean", (MARGIN + 2, 372), "Crack spreads included. This is a clean show.", hand, MUTED)
put("sticker1", (MARGIN, 500), "no ads · no tracking · free", marker_s, INK)
put("sticker2", (MARGIN + 2, 556), "updated three times a day", hand, MUTED)

# right column: the running gag (below the wordmark row, above the chart) and a chart doodle
# (axes, a line that goes up and gets circled)
right = W - 56
put("tag", (right, 242), "crack spreads are widening", hand_tag, RED, anchor="rs")
put("tag2", (right, 292), "(said with a straight face)", hand_tag_s, MUTED, anchor="rs")
ox, oy, top = 790, 548, 318
d.line(wob([(ox, top), (ox, oy)], 2, 10), fill=INK, width=4)
d.line(wob([(ox, oy), (W - 56, oy)], 2, 10), fill=INK, width=4)
boxes["y_axis"] = (ox - 5, top, ox + 5, oy)
boxes["x_axis"] = (ox, oy - 5, W - 56, oy + 5)
pts = [(808, 500), (846, 488), (884, 502), (922, 478), (960, 486), (998, 462), (1036, 444), (1070, 410), (1100, 380), (1122, 352)]
d.line(wob(pts, 2.5, 4), fill=BLUE, width=6, joint="curve")
for x in range(ox + 10, W - 60, 26):  # the 2015-19 "normal" dashed line
    d.line([(x, 518), (x + 14, 517)], fill=GREEN, width=3)
cx, cy, rx, ry = 1110, 362, 50, 36
circ = []
for i in range(0, 390, 12):
    a = math.radians(i - 30)
    circ.append((cx + math.cos(a) * (rx + rnd.uniform(-3, 3)), cy + math.sin(a) * (ry + rnd.uniform(-3, 3))))
d.line(circ, fill=RED, width=5, joint="curve")
boxes["circle"] = (cx - rx - 4, cy - ry - 4, cx + rx + 4, cy + ry + 4)


def overlaps(a, b, pad=4):
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


names = list(boxes)
TOUCHING_BY_DESIGN = {("y_axis", "x_axis")}  # the two axes meet at the chart's corner
for i, a in enumerate(names):
    for b in names[i + 1:]:
        if (a, b) in TOUCHING_BY_DESIGN:
            continue
        assert not overlaps(boxes[a], boxes[b]), f"{a} overlaps {b}: {boxes[a]} vs {boxes[b]}"
    x0, y0, x1, y1 = boxes[a]
    assert 0 <= x0 and x1 <= W and 0 <= y0 and y1 <= H, f"{a} leaves the canvas: {boxes[a]}"
assert boxes["sub1"][2] < ox and boxes["sub2"][2] < ox and boxes["clean"][2] < ox, "left column runs into the chart axis"
assert boxes["tag"][1] > boxes["underline"][3] + 4, "tagline runs into the wordmark row"
assert boxes["tag"][0] > boxes["sub1"][2] + 24, "tagline runs into the subtitle"

OUT.parent.mkdir(parents=True, exist_ok=True)
img.save(OUT, optimize=True)
print(OUT, OUT.stat().st_size, "bytes")
for name, box in boxes.items():
    print("  %-10s %s" % (name, tuple(int(v) for v in box)))
