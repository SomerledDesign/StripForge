# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Read footprints, pads and the board outline from a .kicad_pcb (KiCad 10 file format).

Coordinates are integer nanometres in KiCad's board frame (x right, y *down*).

Rotation conventions (KiCad file format):

* A footprint's ``(at x y angle)`` angle is counter-clockwise *as seen on screen* (y down). A pad
  at local ``(px, py)`` sits at ``fp + R(angle)·(px, py)`` where, with y pointing down,
  ``R(θ)·(x, y) = (x·cosθ + y·sinθ, −x·sinθ + y·cosθ)``.
* A pad's own ``(at px py angle)`` angle is its *absolute* orientation (footprint angle plus the
  pad's rotation inside the footprint). It turns the pad copper about the pad centre and never
  moves the centre, so it plays no part in the pad's position. The pad's rotation relative to
  its footprint is ``pad_angle − footprint_angle``.
* Footprints flipped to the back are saved already mirrored, so the same transform applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

from .sexpr import atom, find, find_all, head, loads, mm_to_nm

Outline = tuple[int, int, int, int]  # (x_min, y_min, x_max, y_max) in nm


@dataclass(frozen=True)
class Pad:
    ref: str
    number: str
    kind: str  # thru_hole | np_thru_hole | smd | connect
    x_nm: int  # absolute board position of the pad centre
    y_nm: int
    net: str | None
    angle_abs: float = 0.0  # pad orientation as stored in the file (absolute)
    angle_rel: float = 0.0  # pad orientation relative to its footprint
    local_x_nm: int = 0  # position inside the footprint, before footprint rotation
    local_y_nm: int = 0

    @property
    def label(self) -> str:
        return f"{self.ref}.{self.number}"

    @property
    def is_tht(self) -> bool:
        return self.kind in ("thru_hole", "np_thru_hole")


@dataclass
class Footprint:
    ref: str
    lib_id: str
    x_nm: int
    y_nm: int
    angle: float
    layer: str
    pads: list[Pad] = field(default_factory=list)


@dataclass
class Board:
    footprints: list[Footprint]
    outline: Outline | None
    path: str | None = None

    @property
    def pads(self) -> list[Pad]:
        return [p for fp in self.footprints for p in fp.pads]

    @property
    def nets(self) -> list[str]:
        return sorted({p.net for p in self.pads if p.net})

    def footprint(self, ref: str) -> Footprint:
        for fp in self.footprints:
            if fp.ref == ref:
                return fp
        raise KeyError(ref)


def _norm_angle(deg: float) -> float:
    d = math.fmod(deg, 360.0)
    if d < 0:
        d += 360.0
    return 0.0 if math.isclose(d, 360.0) else d


def rotate_nm(x: int, y: int, deg: float) -> tuple[int, int]:
    """Rotate a local offset by a KiCad angle (CCW on screen, y down). Exact for 90° steps."""
    d = _norm_angle(deg)
    for q, res in ((0.0, (x, y)), (90.0, (y, -x)), (180.0, (-x, -y)), (270.0, (-y, x))):
        if math.isclose(d, q, abs_tol=1e-9):
            return res
    r = math.radians(d)
    c, s = math.cos(r), math.sin(r)
    return round(x * c + y * s), round(-x * s + y * c)


def _at(node: list | None) -> tuple[int, int, float]:
    if node is None:
        return 0, 0, 0.0
    x = mm_to_nm(atom(node, 1, "0"))
    y = mm_to_nm(atom(node, 2, "0"))
    a = atom(node, 3)
    return x, y, float(a) if a is not None else 0.0


def _reference(fp: list) -> str:
    for prop in find_all(fp, "property"):  # KiCad 8+
        if atom(prop, 1) == "Reference":
            return atom(prop, 2, "") or ""
    for txt in find_all(fp, "fp_text"):  # KiCad 6/7
        if atom(txt, 1) == "reference":
            return atom(txt, 2, "") or ""
    return ""


def _pad_net(pad: list) -> str | None:
    net = find(pad, "net")
    if net is None:
        return None
    # KiCad 10: (net "name"); KiCad <= 9: (net <code> "name")
    name = atom(net, len(net) - 1)
    return name or None


def _parse_footprint(fp: list) -> Footprint:
    ref = _reference(fp)
    fx, fy, fa = _at(find(fp, "at"))
    layer = atom(find(fp, "layer"), 1, "F.Cu") or "F.Cu"
    out = Footprint(ref=ref, lib_id=atom(fp, 1, "") or "", x_nm=fx, y_nm=fy, angle=fa, layer=layer)
    for pad in find_all(fp, "pad"):
        px, py, pa = _at(find(pad, "at"))
        rx, ry = rotate_nm(px, py, fa)
        out.pads.append(
            Pad(
                ref=ref,
                number=atom(pad, 1, "") or "",
                kind=atom(pad, 2, "") or "",
                x_nm=fx + rx,
                y_nm=fy + ry,
                net=_pad_net(pad),
                angle_abs=_norm_angle(pa),
                angle_rel=_norm_angle(pa - fa),
                local_x_nm=px,
                local_y_nm=py,
            )
        )
    return out


_EDGE_POINT_KEYS = ("start", "mid", "end")


def _edge_points(node: list) -> list[tuple[int, int]]:
    kind = head(node)
    pts: list[tuple[int, int]] = []
    if kind == "gr_circle":
        c, e = find(node, "center"), find(node, "end")
        if c is not None and e is not None:
            cx, cy = mm_to_nm(atom(c, 1)), mm_to_nm(atom(c, 2))
            r = round(math.hypot(mm_to_nm(atom(e, 1)) - cx, mm_to_nm(atom(e, 2)) - cy))
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        return pts
    # gr_line / gr_rect: start+end; gr_arc: start+mid+end (the bbox of those three points is a
    # slight under-estimate for arcs spanning an axis extreme; fine for a rectangular board).
    for key in _EDGE_POINT_KEYS:
        p = find(node, key)
        if p is not None:
            pts.append((mm_to_nm(atom(p, 1)), mm_to_nm(atom(p, 2))))
    poly = find(node, "pts")
    if poly is not None:
        for xy in find_all(poly, "xy"):
            pts.append((mm_to_nm(atom(xy, 1)), mm_to_nm(atom(xy, 2))))
    return pts


def _outline(root: list) -> Outline | None:
    pts: list[tuple[int, int]] = []
    for node in root:
        if not isinstance(node, list) or not (head(node) or "").startswith("gr_"):
            continue
        if atom(find(node, "layer"), 1) != "Edge.Cuts":
            continue
        pts += _edge_points(node)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def parse_board(text: str, path: str | None = None) -> Board:
    root = loads(text)
    if head(root) != "kicad_pcb":
        raise ValueError(f"not a kicad_pcb file (top-level node is {head(root)!r})")
    fps = [_parse_footprint(fp) for fp in find_all(root, "footprint")]
    return Board(footprints=fps, outline=_outline(root), path=path)


def load_board(path: str | Path) -> Board:
    p = Path(path)
    return parse_board(p.read_text(encoding="utf-8"), path=str(p))
