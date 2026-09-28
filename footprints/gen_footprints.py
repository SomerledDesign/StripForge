#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Generate the StripForge KiCad 10 footprint library (footprints/StripForge.pretty).

Owner: Mildrew (EE). See footprints/README.md and Sketch.md sections 4.4 and 4.5.

Families:

* ``Link_P<L>``: zero-ohm wire link (ref prefix W, same net on both pins), two THT pads
  k x 2.54 mm apart, pad 2 straight *below* pad 1 (+Y), anchor on pad 1.
* ``CUT_Hole``: pad-less, board-only marker for a spot-face (hole) cut, anchored on the hole.
* ``CUT_Knife``: pad-less, board-only marker for a knife cut, anchored midway between two holes.

Strips themselves are NOT footprints: the writer draws them as B.Cu track segments.

Pure standard library; output is deterministic (uuid5), so re-running gives a clean diff.

Usage:
    python3 footprints/gen_footprints.py [--out DIR] [--cut-layer User.1]
"""

from __future__ import annotations

import argparse
import uuid
from pathlib import Path

PITCH = 2.54
FORMAT_VERSION = 20260206  # KiCad 10.0 footprint format (matches stock 10.0.4 libraries)
GENERATOR = "stripforge_gen_footprints"
NS = uuid.UUID("5f0b6b0e-2d7c-4c55-9a55-5354524950f0")  # fixed namespace for uuid5

# Wire link family: spans in pitches. k = 1 (adjacent strips) up to k = 32 (81.28 mm).
LINK_SPANS = range(1, 33)
LINK_DRILL = 1.0  # stripboard holes are 0.94-1.02 mm (BusBoard 0.94, Vero 1.02, generic 1.0)
# Round pad; kept <= strip width (1.8 mm, measured on the X56 board) so it never reaches the next strip.
LINK_PAD = 1.7
LINK_WIRE = 0.6  # ~23 AWG tinned copper wire, drawn on F.Fab
CRT_MARGIN = 0.25  # courtyard clearance beyond pad copper


def fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class FP:
    def __init__(self, name: str) -> None:
        self.name = name
        self.lines: list[str] = []
        self._n = 0

    def uid(self) -> str:
        self._n += 1
        return str(uuid.uuid5(NS, f"{self.name}/{self._n}"))

    def add(self, s: str) -> None:
        self.lines.append(s)

    def prop(self, key, val, x, y, layer, hide=False, size=1.0, thick=0.15, rot=0):
        h = " (hide yes)" if hide else ""
        self.add(
            f'  (property "{key}" "{val}" (at {fmt(x)} {fmt(y)} {rot}) (layer "{layer}"){h} '
            f'(uuid "{self.uid()}") (effects (font (size {fmt(size)} {fmt(size)}) (thickness {fmt(thick)}))))'
        )

    def text(self, val, x, y, layer, size=1.0, thick=0.15, rot=0):
        self.add(
            f'  (fp_text user "{val}" (at {fmt(x)} {fmt(y)} {rot}) (layer "{layer}") (uuid "{self.uid()}") '
            f"(effects (font (size {fmt(size)} {fmt(size)}) (thickness {fmt(thick)}))))"
        )

    def line(self, x1, y1, x2, y2, layer, w):
        self.add(
            f"  (fp_line (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)}) "
            f'(stroke (width {fmt(w)}) (type solid)) (layer "{layer}") (uuid "{self.uid()}"))'
        )

    def rect(self, x1, y1, x2, y2, layer, w, fill=False):
        f = "yes" if fill else "no"
        self.add(
            f"  (fp_rect (start {fmt(x1)} {fmt(y1)}) (end {fmt(x2)} {fmt(y2)}) "
            f'(stroke (width {fmt(w)}) (type solid)) (fill {f}) (layer "{layer}") (uuid "{self.uid()}"))'
        )

    def circle(self, cx, cy, r, layer, w):
        self.add(
            f"  (fp_circle (center {fmt(cx)} {fmt(cy)}) (end {fmt(cx + r)} {fmt(cy)}) "
            f'(stroke (width {fmt(w)}) (type solid)) (fill no) (layer "{layer}") (uuid "{self.uid()}"))'
        )

    def pad(self, num, x, y, size, drill):
        self.add(
            f'  (pad "{num}" thru_hole circle (at {fmt(x)} {fmt(y)}) (size {fmt(size)} {fmt(size)}) '
            f'(drill {fmt(drill)}) (layers "*.Cu" "*.Mask") (remove_unused_layers no) (uuid "{self.uid()}"))'
        )

    def render(self, descr: str, tags: str, attr: str, body_first: list[str], extra: tuple = ()) -> str:
        head = [
            f'(footprint "{self.name}"',
            f'  (version {FORMAT_VERSION}) (generator "{GENERATOR}") (layer "F.Cu")',
            f'  (descr "{descr}")',
            f'  (tags "{tags}")',
        ]
        return "\n".join(
            head + body_first + [f"  (attr {attr})", *extra] + self.lines + ["  (embedded_fonts no)", ")", ""]
        )


def link_name(k: int) -> str:
    return f"Link_P{k * PITCH:.2f}"


def make_link(k: int) -> tuple[str, str]:
    name = link_name(k)
    length = k * PITCH
    fp = FP(name)
    props = FP(name)
    props._n = 100  # keep property uuids distinct from body uuids
    descr = (
        f"StripForge zero-ohm wire link (W), {k} pitch = {length:.2f} mm, vertical: pad 2 is {length:.2f} mm "
        f"below pad 1 (+Y), anchor on pad 1. Both pins on the same net. "
        f"Drill {LINK_DRILL} mm, pad {LINK_PAD} mm."
    )
    # Texts run along the wire (rotated 90) so they clear the pads even on the 2.54 mm link.
    props.prop("Reference", "REF**", -1.75, length / 2, "F.SilkS", rot=90)
    props.prop("Value", name, 2.6, length / 2, "F.Fab", size=0.8, thick=0.12, rot=90)
    props.prop("Datasheet", "", 0, 0, "F.Fab", hide=True)
    props.prop("Description", descr, 0, 0, "F.Fab", hide=True)
    # Wire body (component side). Fab shows the wire itself pad centre to pad centre.
    fp.line(0, 0, 0, length, "F.Fab", LINK_WIRE)
    fp.text("${REFERENCE}", 1.5, length / 2, "F.Fab", size=0.6, thick=0.09, rot=90)
    # Silk: the visible wire between the pads, kept 0.2 mm off pad copper (none for k = 1).
    y0, y1 = LINK_PAD / 2 + 0.2, length - LINK_PAD / 2 - 0.2
    if y1 - y0 >= 0.3:
        fp.line(0, y0, 0, y1, "F.SilkS", 0.12)
    c = LINK_PAD / 2 + CRT_MARGIN
    fp.rect(-c, -c, c, length + c, "F.CrtYd", 0.05)
    fp.pad("1", 0, 0, LINK_PAD, LINK_DRILL)
    fp.pad("2", 0, length, LINK_PAD, LINK_DRILL)
    tags = f"StripForge stripboard wire link jumper zero ohm 0R W P{length:.2f}mm"
    # The wire joins pads 1 and 2: a jumper pad group tells KiCad's connectivity (ratsnest, DRC
    # unconnected items) that they are one node. Without it a placed link connects nothing.
    jumper = '  (jumper_pad_groups ("1" "2"))'
    return name, fp.render(descr, tags, "through_hole", props.lines, (jumper,))


def make_cut(kind: str, layer: str) -> tuple[str, str]:
    name = f"CUT_{kind}"
    fp = FP(name)
    props = FP(name)
    props._n = 100
    if kind == "Hole":
        descr = (
            "StripForge cut marker: spot-face (hole) cut, anchored on the hole centre. Board-only, no pads, "
            "excluded from BOM and position files. The B.Cu copper gap is the electrical truth; "
            "this is only a marker."
        )
    else:
        descr = (
            "StripForge cut marker: knife cut between two adjacent holes, anchored midway between them "
            "(strip runs along X). Board-only, no pads, excluded from BOM and position files. "
            "The B.Cu copper gap is the electrical truth; this is only a marker."
        )
    props.prop("Reference", "REF**", 0, -2.0, layer, size=0.8, thick=0.12)
    props.prop("Value", name, 0, 2.0, "F.Fab", hide=True, size=0.8, thick=0.12)
    props.prop("Datasheet", "", 0, 0, "F.Fab", hide=True)
    props.prop("Description", descr, 0, 0, "F.Fab", hide=True)
    if kind == "Hole":
        fp.circle(0, 0, 1.2, layer, 0.15)
        d = 0.8
        fp.line(-d, -d, d, d, layer, 0.15)
        fp.line(-d, d, d, -d, layer, 0.15)
    else:
        # A bar across the strip (strip is ~2 mm wide), between the two holes.
        fp.rect(-0.3, -1.2, 0.3, 1.2, layer, 0.1, fill=True)
    tags = f"StripForge stripboard cut marker {kind.lower()} board_only"
    attr = "board_only exclude_from_pos_files exclude_from_bom allow_missing_courtyard"
    return name, fp.render(descr, tags, attr, props.lines)


def generate(out: Path, cut_layer: str) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    items = [make_link(k) for k in LINK_SPANS] + [make_cut("Hole", cut_layer), make_cut("Knife", cut_layer)]
    written = []
    for name, text in items:
        p = out / f"{name}.kicad_mod"
        p.write_text(text, encoding="utf-8")
        written.append(p)
    return written


def main() -> None:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=here / "StripForge.pretty")
    ap.add_argument(
        "--cut-layer", default="User.1", help="canonical layer for cut markers (config cut_marker_layer)"
    )
    a = ap.parse_args()
    for p in generate(a.out, a.cut_layer):
        print(p)


if __name__ == "__main__":
    main()
