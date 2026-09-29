#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Generate the StripForge KiCad 10 footprint library (footprints/StripForge.pretty).

Owner: Mildrew (EE). See footprints/README.md and Sketch.md sections 4.4 and 4.5.

Families:

* ``Link_P<L>``: zero-ohm wire link (ref prefix W, same net on both pins), two THT pads
  k x 2.54 mm apart, pad 2 straight *below* pad 1 (+Y), anchor on pad 1.
  Each pad is ringed by an unfilled circle on User.4 (marker layer; Link_D* have no ring).
* ``Link_D<L>``: the same wire link for an off-pitch diagonal, pads sqrt(dx^2 + dy^2) x 2.54 mm
  apart for whole-hole offsets (dx, dy) that are not a whole number of pitches (1x1 = 3.59 mm,
  1x2 = 5.68 mm, ... up to 32 pitches). The planner rotates it so both pads land on holes.
* ``CUT_Hole``: pad-less, board-only marker for a spot-face (hole) cut, anchored on the hole.
* ``CUT_Knife``: pad-less, board-only marker for a knife cut, anchored midway between two holes.

Strips themselves are NOT footprints: the writer draws them as B.Cu track segments.

Pure standard library; output is deterministic (uuid5), so re-running gives a clean diff.

Usage:
    python3 footprints/gen_footprints.py [--out DIR] [--cut-layer User.1]
"""

from __future__ import annotations

import argparse
import math
import uuid
from pathlib import Path

PITCH = 2.54
FORMAT_VERSION = 20260206  # KiCad 10.0 footprint format (matches stock 10.0.4 libraries)
GENERATOR = "stripforge_gen_footprints"
NS = uuid.UUID("5f0b6b0e-2d7c-4c55-9a55-5354524950f0")  # fixed namespace for uuid5

# Wire link family: spans in pitches. k = 1 (adjacent strips) up to k = 32 (81.28 mm).
LINK_SPANS = range(1, 33)
MAX_PITCHES = 32


def diagonal_squares(max_pitches: int = MAX_PITCHES) -> list[int]:
    """``dx^2 + dy^2`` for whole-hole offsets with ``dx, dy >= 1`` no longer than ``max_pitches``
    whose length is not a whole number of pitches (those use Link_P*)."""
    sq = {
        dx * dx + dy * dy
        for dx in range(1, max_pitches + 1)
        for dy in range(1, max_pitches + 1)
        if dx * dx + dy * dy <= max_pitches * max_pitches
    }
    return sorted(n for n in sq if math.isqrt(n) ** 2 != n)


def diagonal_offsets(n: int) -> list[tuple[int, int]]:
    """The ``(dx, dy)`` offsets, ``dx <= dy``, with ``dx^2 + dy^2 == n``."""
    return [(dx, dy) for dx in range(1, math.isqrt(n) + 1) for dy in range(dx, math.isqrt(n) + 1)
            if dx * dx + dy * dy == n]  # fmt: skip


LINK_DRILL = 1.0  # stripboard holes are 0.94-1.02 mm (BusBoard 0.94, Vero 1.02, generic 1.0)
# Round pad; kept <= strip width (1.8 mm, measured on the X56 board) so it never reaches the next strip.
LINK_PAD = 1.7
LINK_WIRE = 0.6  # ~23 AWG tinned copper wire, drawn on F.Fab
CRT_MARGIN = 0.25  # courtyard clearance beyond pad copper, and beyond the wire between the pads
# Straight links (Link_P*) only: an unfilled ring on a user marker layer around each pad, so
# placed links stand out (Kevin colours User.4 yellow). Radius = pad half-size + 0.25 mm.
LINK_RING_LAYER = "User.4"
LINK_RING_GAP = 0.25
LINK_RING_W = 0.15


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

    def poly(self, pts, layer, w):
        xy = " ".join(f"(xy {fmt(x)} {fmt(y)})" for x, y in pts)
        self.add(
            f"  (fp_poly (pts {xy}) "
            f'(stroke (width {fmt(w)}) (type solid)) (fill no) (layer "{layer}") (uuid "{self.uid()}"))'
        )

    def circle(self, cx, cy, r, layer, w):
        self.add(
            f"  (fp_circle (center {fmt(cx)} {fmt(cy)}) (end {fmt(cx + r)} {fmt(cy)}) "
            f'(stroke (width {fmt(w)}) (type solid)) (fill no) (layer "{layer}") (uuid "{self.uid()}"))'
        )

    def pad(self, num, x, y, size, drill, exact=False):
        # an off-pitch link's pad 2 is written to the nanometre so it lands on the hole when rotated
        ys = f"{y:.6f}".rstrip("0").rstrip(".") if exact else fmt(y)
        self.add(
            f'  (pad "{num}" thru_hole circle (at {fmt(x)} {ys}) (size {fmt(size)} {fmt(size)}) '
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


def diagonal_name(n: int) -> str:
    return f"Link_D{math.sqrt(n) * PITCH:.2f}"


def link_courtyard(length: float) -> list[tuple[float, float]]:
    """Dumbbell outline for a link of ``length`` mm (pad 1 at 0, pad 2 at +Y): a square of pad
    radius + margin round each pad, joined by a neck of wire radius + margin."""
    c = round(LINK_PAD / 2 + CRT_MARGIN, 4)
    w = round(LINK_WIRE / 2 + CRT_MARGIN, 4)
    top, bot = c, round(length - c, 4)
    if bot <= top:  # pads' squares touch or overlap: one rectangle
        return [(-c, -c), (c, -c), (c, length + c), (-c, length + c)]
    return [
        (-c, -c), (c, -c), (c, top), (w, top), (w, bot), (c, bot),
        (c, length + c), (-c, length + c), (-c, bot), (-w, bot), (-w, top), (-c, top),
    ]  # fmt: skip


def make_link(k: int, n: int | None = None) -> tuple[str, str]:
    """Link_P for ``k`` pitches, or (``n`` given) Link_D for an off-pitch diagonal dx^2 + dy^2 = n."""
    if n is None:
        name, length = link_name(k), k * PITCH
        span = f"{k} pitch = {length:.2f} mm, vertical"
        tag = f"P{length:.2f}mm"
    else:
        name, length = diagonal_name(n), math.sqrt(n) * PITCH
        offs = ", ".join(f"{dx}x{dy}" for dx, dy in diagonal_offsets(n))
        span = (
            f"off-pitch diagonal for hole offsets {offs} = {length:.2f} mm, drawn vertical, "
            "rotated on the board"
        )
        tag = f"D{length:.2f}mm diagonal"
    fp = FP(name)
    props = FP(name)
    props._n = 100  # keep property uuids distinct from body uuids
    descr = (
        f"StripForge zero-ohm wire link (W), {span}: pad 2 is {length:.2f} mm "
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
    # Courtyard: a pad-sized square round each pad and, between them, only the wire (0.6 mm) plus
    # the margin, so a link is as narrow as a real wire link and not a pad-wide block.
    fp.poly(link_courtyard(length), "F.CrtYd", 0.05)
    fp.pad("1", 0, 0, LINK_PAD, LINK_DRILL)
    fp.pad("2", 0, length, LINK_PAD, LINK_DRILL, exact=n is not None)
    if n is None:  # straight links only; drawn after the pads so existing uuids are unchanged
        r = LINK_PAD / 2 + LINK_RING_GAP
        fp.circle(0, 0, r, LINK_RING_LAYER, LINK_RING_W)
        fp.circle(0, length, r, LINK_RING_LAYER, LINK_RING_W)
    tags = f"StripForge stripboard wire link jumper zero ohm 0R W {tag}"
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
    items = [make_link(k) for k in LINK_SPANS] + [make_link(0, n) for n in diagonal_squares()]
    items += [make_cut("Hole", cut_layer), make_cut("Knife", cut_layer)]
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
