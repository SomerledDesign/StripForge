# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Placement hints: parts that force cuts by lying along a strip (Sketch.md §4.3).

A part *forces* a cut when two of its own pads with different nets sit on the same strip: at
least one cut has to go between them whatever else is placed there. Typical cases are 2-pin
parts lying along the strips (resistors, capacitors, LEDs) and the IDC header, whose two pin
columns share each strip.

For each such part we also try rotating it 90° (both ways) about the centre of its pads'
bounding box, re-snapping it (a whole-hole shift of up to one pitch each way is allowed so a part
with an odd pin span can land on the grid) and re-running the splitter. The rotation is offered
only if every rotated pad lands within the snap tolerance of a hole, inside the board and on a
hole no other part uses, and the board then needs fewer cuts. The saving is estimated for each
part on its own; savings from several rotations do not simply add up.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .board import Board, rotate_nm
from .config import CutStyle
from .grid import Grid, Node, PadSnap, SnapResult, hole_label, row_label
from .splitter import split
from .strips import assign_holes, build_strips, pairs


@dataclass
class Rotation:
    angle: int  # +90 or -90 (KiCad sense: counter-clockwise on screen)
    shift_nm: tuple[int, int]  # translation applied after rotating, to land on holes
    pads: dict[str, Node]  # pad number -> new hole
    cuts_after: int
    cuts_saved: int
    forced_after: int  # cuts the part itself still forces after rotating
    links_delta: int  # change in the minimum number of links (more pieces per net = more links)

    @property
    def separate_strips(self) -> bool:
        return self.forced_after == 0


@dataclass
class Hint:
    ref: str
    forced_cuts: int
    rows: dict[int, tuple[int, int]]  # row -> (first col, last col) of its pads on that row
    rotation: Rotation | None = None
    rotation_note: str = ""  # why no rotation is offered
    cut_ids: list[str] = field(default_factory=list)  # cuts between its own pads

    @property
    def strips(self) -> str:
        rows = sorted(self.rows)
        if len(rows) == 1:
            return f"strip {row_label(rows[0])}"
        return f"strips {row_label(rows[0])}-{row_label(rows[-1])}"

    @property
    def spans(self) -> str:
        return ", ".join(f"{hole_label(r, a)}-{hole_label(r, b)}" for r, (a, b) in sorted(self.rows.items()))

    @property
    def text(self) -> str:
        n = self.forced_cuts
        s = f"{self.ref} lies along {self.strips} ({self.spans}), forcing {n} cut{'s' if n != 1 else ''}"
        rot = self.rotation
        if rot is not None:
            what = (
                "would put its pins on separate strips"
                if rot.separate_strips
                else f"would cut the cuts it forces to {rot.forced_after}"
            )
            where = ", ".join(f"{self.ref}.{p} {node.label}" for p, node in rot.pads.items())
            saved = rot.cuts_saved
            links = f", links needed {rot.links_delta:+d}" if rot.links_delta else ""
            s += (
                f"; rotating it 90° {what} (est. {saved} cut{'s' if saved != 1 else ''} saved{links}; "
                f"pins to {where})"
            )
        elif self.rotation_note:
            s += f"; {self.rotation_note}"
        return s


def forced_cuts(snap: SnapResult) -> dict[int, int]:
    """Row -> number of cuts forced between this footprint's own pads on that row."""
    by_row: dict[int, list[tuple[int, str | None]]] = defaultdict(list)
    for p in snap.pads:
        if p.node is not None:
            by_row[p.node.row].append((p.node.col, p.net))
    out: dict[int, int] = {}
    for row, pads in by_row.items():
        pads.sort()
        n = sum(1 for (_, a), (_, b) in pairs(pads) if a and b and a != b)
        if n:
            out[row] = n
    return out


def _links(res) -> int:
    return sum(n - 1 for n in res.split_nets.values())


def _evaluate(snaps: list[SnapResult], grid: Grid, style: CutStyle) -> tuple[int, int]:
    holes = assign_holes(snaps)
    res = split(build_strips(grid), holes, style)
    return len(res.cuts), _links(res)


def _try_rotations(
    board: Board, snap: SnapResult, snaps: list[SnapResult], grid: Grid, tol_nm: int, style: CutStyle
) -> tuple[Rotation | None, str]:
    fp = board.footprint(snap.ref)
    tht = [p for p in fp.pads if p.is_tht]
    if not tht:
        return None, ""
    others = [s for s in snaps if s.ref != snap.ref]
    taken = assign_holes(others)
    base_cuts, base_links = _evaluate(snaps, grid, style)
    xs, ys = [p.x_nm for p in tht], [p.y_nm for p in tht]
    cx, cy = (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2
    pitch = grid.pitch_nm
    forced_now = sum(forced_cuts(snap).values())
    best: Rotation | None = None
    best_key: tuple[int, int, int] | None = None
    fits = fewer = False
    for angle in (90, -90):
        rot = [(p, *rotate_nm(p.x_nm - cx, p.y_nm - cy, angle)) for p in tht]
        # align the first pad with the grid, then try whole-hole shifts around that
        x0, y0 = cx + rot[0][1], cy + rot[0][2]
        n0 = grid.nearest(x0, y0)
        hx, hy = grid.hole_xy(n0)
        ax, ay = hx - x0, hy - y0
        for i in (0, -1, 1):
            for j in (0, -1, 1):
                sx, sy = ax + i * pitch, ay + j * pitch
                pads: list[PadSnap] = []
                ok = True
                for p, rx, ry in rot:
                    x, y = cx + rx + sx, cy + ry + sy
                    node = grid.nearest(x, y)
                    nx, ny = grid.hole_xy(node)
                    ps = PadSnap(p.number, p.net, node, x - nx, y - ny, node)
                    if ps.dev_nm > tol_nm or not grid.contains(node) or not taken.is_free(node):
                        ok = False
                        break
                    pads.append(ps)
                if not ok:
                    continue
                fits = True
                trial = SnapResult(ref=snap.ref, pads=pads)
                cuts, links = _evaluate([*others, trial], grid, style)
                cand = Rotation(
                    angle=angle,
                    shift_nm=(sx, sy),
                    pads={p.number: p.node for p in pads if p.node is not None},
                    cuts_after=cuts,
                    cuts_saved=base_cuts - cuts,
                    forced_after=sum(forced_cuts(trial).values()),
                    links_delta=links - base_links,
                )
                if cand.forced_after >= forced_now:
                    continue  # its pins would still share strips: not what the hint is about
                fewer = True
                key = (cand.cuts_saved, -cand.links_delta, -(abs(i) + abs(j)))
                if best_key is None or key > best_key:
                    best, best_key = cand, key
    if best is not None and best.cuts_saved > 0:
        return best, ""
    if not fits:
        return None, "rotating it 90° does not fit on free on-grid holes here"
    if not fewer:
        return None, "rotating it 90° would not put its pins on separate strips"
    return None, "rotating it 90° would not save cuts overall"


def placement_hints(
    board: Board, snaps: list[SnapResult], grid: Grid, tol_nm: int, style: CutStyle, cuts=()
) -> list[Hint]:
    """A :class:`Hint` for every snapped part that forces cuts between its own pads."""
    hints: list[Hint] = []
    for snap in snaps:
        if not snap.accepted:
            continue
        forced = forced_cuts(snap)
        if not forced:
            continue
        rows: dict[int, tuple[int, int]] = {}
        for p in snap.pads:
            if p.node is not None and p.node.row in forced:
                a, b = rows.get(p.node.row, (p.node.col, p.node.col))
                rows[p.node.row] = (min(a, p.node.col), max(b, p.node.col))
        mine = f"{snap.ref}."
        ids = [
            c.id
            for c in cuts
            if all(any(lab.startswith(mine) for lab in side.split("/")) for side in c.between)
        ]
        hint = Hint(snap.ref, sum(forced.values()), rows, cut_ids=ids)
        if snap.slotted:
            hint.rotation_note = "slotted part: not rotated"
        else:
            hint.rotation, hint.rotation_note = _try_rotations(board, snap, snaps, grid, tol_nm, style)
        hints.append(hint)
    return hints


def summary(hints: list[Hint]) -> str:
    forced = sum(h.forced_cuts for h in hints)
    rot = [h for h in hints if h.rotation is not None]
    saved = sum(h.rotation.cuts_saved for h in rot if h.rotation is not None)
    s = f"{len(hints)} part(s) force {forced} cut(s) by lying along the strips"
    if rot:
        s += (
            f"; rotating {len(rot)} of them ({', '.join(h.ref for h in rot)}) would save an estimated "
            f"{saved} cut(s) (each estimated on its own)"
        )
    return s
