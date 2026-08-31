#!/usr/bin/env python3
"""Generate the app logo/favicon as SVG (+ PNG/ICO renders) from computed geometry.

Concept: two opposing arcs (the "debate" between agents arguing bull vs bear)
orbit a candlestick pair (the "trading" outcome). Everything is computed from
angles/radii in Python rather than hand-drawn coordinates.

Usage: python3 scripts/generate_logo.py
Requires: rsvg-convert (brew install librsvg) for PNG/ICO rendering.
"""
import math
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIZE = 64
CX, CY, R = 32.0, 26.5, 16.5

BG_TOP = "#0b0e17"
BG_BOTTOM = "#141a2b"
CYAN = "#5fd4e8"
PURPLE = "#b58cf0"
GREEN = "#4ade80"
RED = "#f87171"


def polar(cx, cy, r, deg):
    rad = math.radians(deg)
    return cx + r * math.cos(rad), cy - r * math.sin(rad)


def arc_path(cx, cy, r, start_deg, end_deg):
    x1, y1 = polar(cx, cy, r, start_deg)
    x2, y2 = polar(cx, cy, r, end_deg)
    large_arc = 1 if abs(end_deg - start_deg) > 180 else 0
    # SVG y-axis is flipped vs. our math convention -> sweep=0 draws CCW on screen
    return f"M {x1:.2f} {y1:.2f} A {r:.2f} {r:.2f} 0 {large_arc} 0 {x2:.2f} {y2:.2f}"


def candle(x, wick_top, wick_bottom, body_top, body_bottom, width, color):
    cx = x + width / 2
    return f"""
  <line x1="{cx:.2f}" y1="{wick_top:.2f}" x2="{cx:.2f}" y2="{wick_bottom:.2f}" stroke="{color}" stroke-width="1.4" stroke-linecap="round"/>
  <rect x="{x:.2f}" y="{body_top:.2f}" width="{width:.2f}" height="{body_bottom - body_top:.2f}" rx="1" fill="{color}"/>"""


ARC_STROKE = 3.4


def assert_clear_of_arc(x, wick_top, wick_bottom, width, margin=1.5):
    """Fail loudly if any corner of a candle would touch the arc ring."""
    inner_edge = R - ARC_STROKE / 2 - margin
    corners = [(x, wick_top), (x + width, wick_top), (x, wick_bottom), (x + width, wick_bottom)]
    for cx, cy in corners:
        dist = math.hypot(cx - CX, cy - CY)
        assert dist <= inner_edge, f"candle corner ({cx},{cy}) at r={dist:.2f} crosses arc (limit {inner_edge:.2f})"


def build_svg():
    gap = 16  # degrees of empty space at each end of an arc, so the two don't touch
    arc1_start, arc1_end = 20 + gap / 2, 200 - gap / 2
    arc2_start, arc2_end = 200 + gap / 2, 380 - gap / 2

    body = []
    body.append(f'<path d="{arc_path(CX, CY, R, arc1_start, arc1_end)}" fill="none" stroke="{CYAN}" stroke-width="{ARC_STROKE}" stroke-linecap="round"/>')
    body.append(f'<path d="{arc_path(CX, CY, R, arc2_start, arc2_end)}" fill="none" stroke="{PURPLE}" stroke-width="{ARC_STROKE}" stroke-linecap="round"/>')

    # Candlestick pair nested well inside the arc ring (the "resolution" of the
    # debate) -- shrunk and centered so no corner crosses into the arc stroke.
    width = 4.2
    green = dict(x=CX - 4 - width / 2, wick_top=CY - 9, wick_bottom=CY + 9, body_top=CY - 5, body_bottom=CY - 0.5, width=width, color=GREEN)
    red = dict(x=CX + 4 - width / 2, wick_top=CY - 9, wick_bottom=CY + 9, body_top=CY + 0.5, body_bottom=CY + 5, width=width, color=RED)
    for c in (green, red):
        assert_clear_of_arc(c["x"], c["wick_top"], c["wick_bottom"], c["width"])
    body.append(candle(**green))
    body.append(candle(**red))

    inner = "\n  ".join(body)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="{BG_TOP}"/>
      <stop offset="1" stop-color="{BG_BOTTOM}"/>
    </linearGradient>
  </defs>
  <rect width="{SIZE}" height="{SIZE}" rx="12" fill="url(#bg)"/>
  {inner}
</svg>
"""


def main():
    svg = build_svg()
    svg_path = ROOT / "web" / "app" / "icon.svg"
    svg_path.write_text(svg)
    print(f"wrote {svg_path}")

    png_sizes = [16, 32, 48, 180, 512]
    png_paths = []
    for size in png_sizes:
        out = ROOT / "scripts" / f"_icon_{size}.png"
        subprocess.run(
            ["rsvg-convert", "-w", str(size), "-h", str(size), str(svg_path), "-o", str(out)],
            check=True,
        )
        png_paths.append(out)
        print(f"rendered {out}")

    from PIL import Image

    ico_frames = [Image.open(p) for p in png_paths if p.stem.endswith(("16", "32", "48"))]
    ico_path = ROOT / "web" / "app" / "favicon.ico"
    ico_frames[0].save(ico_path, format="ICO", sizes=[(16, 16), (32, 32), (48, 48)], append_images=ico_frames[1:])
    print(f"wrote {ico_path}")

    apple_touch = ROOT / "web" / "public" / "apple-touch-icon.png"
    apple_touch.parent.mkdir(parents=True, exist_ok=True)
    for p in png_paths:
        if p.stem.endswith("180"):
            p.replace(apple_touch)
            print(f"wrote {apple_touch}")

    logo_png = ROOT / "web" / "public" / "logo.png"
    for p in png_paths:
        if p.stem.endswith("512"):
            p.replace(logo_png)
            print(f"wrote {logo_png}")

    for p in png_paths:
        if p.exists():
            p.unlink()


if __name__ == "__main__":
    main()
