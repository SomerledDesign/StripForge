#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Write the straight Link_P 3D models (3dmodels/StripForge.3dshapes).

One VRML file per ``Link_P*`` footprint: a round wire, 0.032 in (0.8128 mm) in diameter, from pad
centre to pad centre. KiCad's 3D viewer uses these. Diagonal links and cut markers have no model.

The wire runs along model -Y because KiCad maps model +Y onto the footprint's -Y, and pad 2 is at
footprint +Y. The wire sits on the board (its centre is one radius above z = 0).

Output is deterministic. Re-run:

    python3 footprints/gen_3dmodels.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

PITCH = 2.54
LINK_SPANS = range(1, 33)
WIRE_DIAMETER_MM = 0.8128  # 0.032 in


def link_name(k: int) -> str:
    return f"Link_P{k * PITCH:.2f}"


def wrl_text(name: str, length_mm: float) -> str:
    radius = WIRE_DIAMETER_MM / 2
    return (
        "#VRML V2.0 utf8\n"
        f"# {name}: straight {WIRE_DIAMETER_MM:.4f} mm (0.032 in) wire, "
        f"{length_mm:.2f} mm pad centre to pad centre.\n"
        "Transform {\n"
        f"  translation 0 {-length_mm / 2:.4f} {radius:.4f}\n"
        "  children [\n"
        "    Shape {\n"
        "      appearance Appearance {\n"
        "        material Material {\n"
        "          diffuseColor 0.75 0.75 0.78\n"
        "          specularColor 0.9 0.9 0.9\n"
        "          shininess 0.6\n"
        "        }\n"
        "      }\n"
        "      geometry Cylinder {\n"
        f"        radius {radius:.4f}\n"
        f"        height {length_mm:.4f}\n"
        "      }\n"
        "    }\n"
        "  ]\n"
        "}\n"
    )


def generate(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for k in LINK_SPANS:
        name = link_name(k)
        path = out / f"{name}.wrl"
        path.write_text(wrl_text(name, k * PITCH), encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=here.parent / "3dmodels" / "StripForge.3dshapes")
    args = ap.parse_args()
    for path in generate(args.out):
        print(path)


if __name__ == "__main__":
    main()
