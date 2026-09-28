# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Regenerate the plugin toolbar icons in plugins/icons/ from resources/icon.png (needs Pillow).

Each action gets the StripForge "S" at 24 and 48 px (48 for high-DPI screens) with a letter badge:
A = Analyze, B = Build strips, D = Run DRC, S = Build sheet.

    python tools/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = [("analyze", "A", (40, 120, 220)), ("build", "B", (30, 150, 60)), ("drc", "D", (210, 40, 40)),
           ("sheet", "S", (120, 60, 180))]  # fmt: skip
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"]


def main() -> None:
    src = Image.open(ROOT / "resources" / "icon.png").convert("RGBA")
    font_path = next((f for f in FONTS if Path(f).exists()), None)
    for key, letter, colour in ACTIONS:
        for size in (24, 48):
            im = src.resize((size, size), Image.LANCZOS)
            d = ImageDraw.Draw(im)
            r = int(size * 0.46)
            x0 = y0 = size - r
            d.ellipse([x0, y0, size - 1, size - 1], fill=(*colour, 255), outline=(255, 255, 255, 255),
                      width=max(1, size // 24))  # fmt: skip
            font = ImageFont.truetype(font_path, int(r * 0.78)) if font_path else ImageFont.load_default()
            left, top, right, bottom = d.textbbox((0, 0), letter, font=font)
            d.text((x0 + (r - (right - left)) / 2 - left, y0 + (r - (bottom - top)) / 2 - top), letter,
                   font=font, fill=(255, 255, 255, 255))  # fmt: skip
            out = ROOT / "plugins" / "icons" / f"{key}-{size}.png"
            im.save(out, optimize=True)
            print(out.relative_to(ROOT))


if __name__ == "__main__":
    main()
