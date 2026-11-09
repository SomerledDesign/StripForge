# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Regenerate letter-badge icons. The toolbar no longer uses them.

0.3.0 ships one toolbar button, StripForge: Dialog, using plugins/icons/dialog-24.png and
dialog-48.png. Those are not letter badges, and this script does not overwrite them. The old
Analyze / Build / DRC / Sheet / Restore badges are not written.

    python tools/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ACTIONS: list[tuple[str, str, tuple[int, int, int]]] = []
TOOLBAR_CROP = (10, 2, 54, 46)  # left, top, right, bottom in the 64 x 64 icon: the S and its traces
FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/Library/Fonts/Arial Bold.ttf"]


def main() -> None:
    src = Image.open(ROOT / "resources" / "icon.png").convert("RGBA")
    if src.size == (64, 64):
        src = src.crop(TOOLBAR_CROP)
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
