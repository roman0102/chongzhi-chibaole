"""A very hungry, vector-only quota pig. Canvas coordinates are 280 x 240."""

from __future__ import annotations

import math
from typing import Any


def draw_pet(canvas: Any, remaining: float | None, phase: float,
             feeding: bool = False, palette: dict | None = None) -> None:
    """Redraw only items tagged ``pet``; phase is animation time in seconds.

    The five known states are plump (80+), full (50+), peckish (20+),
    skinny (above zero), and empty. Unknown quota stays pleasantly sleepy.
    Optional palette keys: body, outline, accent (six-digit hex colours).
    """
    canvas.delete("pet")
    colours = {"body": "#F5B8BC", "outline": "#573A34", "accent": "#D98454"}
    colours.update(palette or {})
    ink, pink, accent = (colours[k] for k in ("outline", "body", "accent"))
    cream, bowl_colour = "#FFF5DD", "#86B7AD"

    def tint(colour: str, target: str, amount: float) -> str:
        try:
            rgb = [round(int(colour[i:i+2], 16) * (1-amount) +
                         int(target[i:i+2], 16) * amount) for i in (1, 3, 5)]
            return "#" + "".join(f"{v:02x}" for v in rgb)
        except (TypeError, ValueError):
            return colour

    pale = tint(pink, "#FFFFFF", .34)
    blush = tint(pink, "#CC717A", .45)
    hoof = tint(ink, "#FFFFFF", .24)
    common = {"tags": ("pet",)}

    def oval(box, fill, width=3, outline=ink):
        return canvas.create_oval(*box, fill=fill, outline=outline, width=width, **common)

    def poly(points, fill, width=3, smooth=True, outline=ink):
        return canvas.create_polygon(*points, fill=fill, outline=outline, width=width,
                                     smooth=smooth, splinesteps=24, **common)

    def line(points, width=3, colour=ink, smooth=True):
        return canvas.create_line(*points, fill=colour, width=width, smooth=smooth,
                                  splinesteps=24, capstyle="round", joinstyle="round",
                                  **common)

    def star(x, y, size, colour=accent):
        points = []
        for n in range(8):
            radius = size if n % 2 == 0 else size * .32
            angle = n * math.pi / 4
            points.extend((x + math.cos(angle) * radius,
                           y + math.sin(angle) * radius))
        poly(points, colour, width=0, smooth=False)

    unknown = remaining is None
    if not unknown:
        try:
            amount = float(remaining)
            unknown = not math.isfinite(amount)
        except (ValueError, TypeError):
            unknown = True
    if unknown:
        amount, level = 50.0, 2
    else:
        amount = max(0.0, min(100.0, amount))
        level = 4 if amount >= 80 else 3 if amount >= 50 else 2 if amount >= 20 else 1 if amount > 0 else 0
    breath = math.sin(phase * 2.1) * (1.5 if level > 1 else .8)
    bounce = abs(math.sin(phase * 6)) * 3 if feeding else 0
    dy = breath - bounce
    cx = 140.0
    half = [27, 38, 59, 77, 94][level]
    if unknown:
        half = 55
    top, bottom = 102 + dy, 207 + dy * .25

    # Grounding shadow and a corkscrew tail, both kept behind the silhouette.
    oval((59, 207, 224, 219), "#DBCFC4", width=0, outline="")
    tx = min(248, cx + half + 12)
    line((cx + half - 7, 159 + dy, tx, 163 + dy,
          tx + 8, 149 + dy, tx - 4, 142 + dy,
          tx - 11, 154 + dy, tx + 1, 155 + dy), width=4, colour=ink)
    line((cx + half - 7, 159 + dy, tx, 163 + dy,
          tx + 8, 149 + dy, tx - 4, 142 + dy,
          tx - 11, 154 + dy, tx + 1, 155 + dy), width=1.8, colour=pink)

    # Body control points change topology visually: pear → barrel → tiny waist.
    waist = [13, 23, 48, 73, 94][level]
    if unknown:
        waist = 46
    poly((cx - 22, top, cx - half * .78, top + 13,
          cx - half, top + 41, cx - waist, bottom - 20,
          cx - half * .65, bottom, cx, bottom + 4,
          cx + half * .65, bottom, cx + waist, bottom - 20,
          cx + half, top + 41, cx + half * .78, top + 13,
          cx + 22, top), pink, width=3.5)
    if level >= 2:
        oval((cx-half*.59, 133+dy, cx+half*.59, 202+dy*.3), pale, 0, "")
        line((136, 176+dy, 140, 179+dy, 144, 176+dy), width=2, colour=blush)
    elif not unknown:
        # Curved rib marks read as comic hunger, without becoming anatomical.
        for row in range(3):
            yy = 132 + row * 11 + dy
            span = 15 if level == 0 else 21
            line((cx-span, yy, cx-7, yy+4, cx-3, yy+3), 2.4, blush)
            line((cx+span, yy, cx+7, yy+4, cx+3, yy+3), 2.4, blush)
        if level == 0:
            line((140, 128+dy, 140, 163+dy), 2, blush)

    # Little cloven feet keep the same visual identity at every weight.
    leg_gap = [18, 23, 35, 45, 54][level]
    for x in (cx-leg_gap, cx+leg_gap):
        oval((x-14, 194+dy*.3, x+14, 214+dy*.15), hoof, 3)
        line((x, 204+dy*.2, x, 212+dy*.15), 2)

    # Oversized head; cheeks grow with the belly, ears stay recognisable.
    head_w = [43, 46, 51, 57, 63][level]
    hy = 78 + dy
    ear_sway = math.sin(phase * 2.1) * 1.2
    poly((101, hy-20, 88, 28+dy, 109+ear_sway, 31+dy, 123, hy-20), pink)
    poly((157, hy-21, 174-ear_sway, 30+dy, 191, 29+dy, 181, hy-16), pink)
    poly((103, hy-24, 96, 36+dy, 108, 39+dy, 115, hy-24), blush, 0)
    poly((168, hy-24, 177, 38+dy, 183, 36+dy, 178, hy-19), blush, 0)
    poly((140, hy-34, 140-head_w*.8, hy-32, 140-head_w, hy-3,
          140-head_w*.96, hy+26, 117, hy+44, 140, hy+47,
          163, hy+44, 140+head_w*.96, hy+26, 140+head_w, hy-3,
          140+head_w*.8, hy-32), pink, 3.5)
    # The absurd little forelock.
    line((132, hy-31, 131, hy-40, 139, hy-35, 143, hy-42,
          150, hy-32), 2.5)
    oval((102, hy+9, 116, hy+18), blush, 0, "")
    oval((165, hy+9, 179, hy+18), blush, 0, "")
    blink = phase % 5.3 > 5.13
    for ex in (119, 161):
        if feeding:
            line((ex-5, hy+1, ex, hy-5, ex+5, hy+1), 3)
        elif blink or unknown:
            line((ex-5, hy, ex, hy+2, ex+5, hy), 2.5)
            if unknown:
                line((ex-6, hy-7, ex+4, hy-8), 1.6, blush)
        elif level == 0:
            oval((ex-3, hy-3, ex+3, hy+5), ink, 0, "")
            line((ex-6, hy+10, ex+5, hy+11), 1.8, blush)
        else:
            oval((ex-3.5, hy-4, ex+3.5, hy+4), ink, 0, "")
            oval((ex-1.5, hy-3, ex, hy-1.5), cream, 0, "")
    oval((119, hy+8, 162, hy+34), blush, 2.6)
    oval((129, hy+17, 134, hy+25), ink, 0, "")
    oval((147, hy+17, 152, hy+25), ink, 0, "")
    if feeding:
        oval((135, hy+36, 147, hy+44), ink, 0, "")
    elif level == 0:
        line((136, hy+41, 140, hy+38, 145, hy+41), 2)
    else:
        line((135, hy+37, 140, hy+40, 146, hy+37), 2)

    # One bowl, increasingly empty. Arms cradle it rather than covering ribs.
    bowl_y = 183 - (4 if level == 0 else 0) + dy*.4
    for side in (-1, 1):
        x = cx + side * (half*.66)
        line((x, 144+dy, x+side*7, 164+dy,
              cx+side*37, bowl_y+6), 12, ink)
        line((x, 144+dy, x+side*7, 164+dy,
              cx+side*37, bowl_y+6), 6, pink)
    poly((100, bowl_y, 180, bowl_y, 173, bowl_y+24,
          157, bowl_y+32, 122, bowl_y+32, 107, bowl_y+24), bowl_colour, 3)
    oval((99, bowl_y-8, 181, bowl_y+10), cream, 3)
    oval((108, bowl_y-3, 172, bowl_y+6), "#CFB99E", 0, "")
    if amount > 0 and not unknown:
        for i in range([0, 1, 4, 6, 9][level]):
            xx = 119 + (i % 5)*10 + (i//5)*3
            yy = bowl_y - 2 - (i//5)*5
            oval((xx-5, yy-4, xx+5, yy+4), accent, 1.3)
    line((118, bowl_y+17, 124, bowl_y+22), 2, cream)
    if feeding:
        for i in range(3):
            angle = phase*2.8 + i*2.1
            xx = 140 + math.cos(angle)*72
            yy = 52 + math.sin(angle)*15
            star(xx, yy, 5 + i*.5)
        fly = (phase * .9) % 1
        xx, yy = 194 - fly*36, 117 - math.sin(fly*math.pi)*25
        oval((xx-5, yy-4, xx+5, yy+4), accent, 1.5)
