# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""2.54 mm hole grid and per-footprint snapping with a tolerance (Sketch.md §4.2).

Holes are indexed ``(col, row)`` from 0, with col 0/row 0 the top-left hole as seen from the
component side, in KiCad's board frame (x right, y down).

Snapping in M1 never moves anything. Each THT pad is mapped to its nearest hole, and the
footprint *snaps* when every pad is within ``tol`` of its hole. The offsets are reported so
slightly off-pitch parts (2.50 mm capacitors, the Littelfuse 395) are visible. For a footprint
that does not snap we also report the rigid translation that would minimise its worst pad
offset; applying it is M2's job.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import PITCH_NM
from .board import Board, Footprint, Outline

ON_GRID_NM = 5_000  # offsets at or below this are treated as exactly on grid in reports


@dataclass(frozen=True, order=True)
class Node:
    row: int
    col: int

    def __str__(self) -> str:
        return f"({self.col},{self.row})"


@dataclass(frozen=True)
class Grid:
    origin_x_nm: int  # centre of hole (col 0, row 0)
    origin_y_nm: int
    cols: int
    rows: int
    pitch_nm: int = PITCH_NM

    @classmethod
    def from_outline(cls, outline: Outline, pitch_nm: int = PITCH_NM, inset_nm: int | None = None) -> Grid:
        """Fill a rectangular outline with holes, the first one ``inset`` in from the corner."""
        if inset_nm is None:
            inset_nm = pitch_nm // 2
        x0, y0, x1, y1 = outline
        ox, oy = x0 + inset_nm, y0 + inset_nm
        return cls(ox, oy, *cls._fit(ox, oy, x1 - inset_nm, y1 - inset_nm, pitch_nm), pitch_nm)

    @staticmethod
    def _fit(ox: int, oy: int, last_x: int, last_y: int, pitch_nm: int) -> tuple[int, int]:
        slack = 1_000  # 1 µm, absorbs rounding in the outline coordinates
        cols = (last_x - ox + slack) // pitch_nm + 1
        rows = (last_y - oy + slack) // pitch_nm + 1
        if cols < 1 or rows < 1:
            raise ValueError("board outline is smaller than one hole")
        return int(cols), int(rows)

    def hole_xy(self, node: Node) -> tuple[int, int]:
        return self.origin_x_nm + node.col * self.pitch_nm, self.origin_y_nm + node.row * self.pitch_nm

    def nearest(self, x_nm: int, y_nm: int) -> Node:
        """Nearest grid position; may lie outside the board (check with ``contains``)."""
        col = round((x_nm - self.origin_x_nm) / self.pitch_nm)
        row = round((y_nm - self.origin_y_nm) / self.pitch_nm)
        return Node(row=row, col=col)

    def contains(self, node: Node) -> bool:
        return 0 <= node.col < self.cols and 0 <= node.row < self.rows

    @property
    def nodes(self) -> int:
        return self.cols * self.rows


@dataclass
class PadSnap:
    number: str
    net: str | None
    node: Node | None  # nearest hole, or None if it lies off the board
    dx_nm: int  # pad centre minus hole centre
    dy_nm: int

    @property
    def dev_nm(self) -> int:
        return round(math.hypot(self.dx_nm, self.dy_nm))


@dataclass
class SnapResult:
    ref: str
    pads: list[PadSnap] = field(default_factory=list)
    max_dev_nm: int = 0
    accepted: bool = True
    reason: str = ""
    # Rigid translation (nm) that would minimise the worst per-axis offset, and the resulting
    # worst offset. Informational only; M1 never moves footprints.
    shift_nm: tuple[int, int] = (0, 0)
    max_dev_after_shift_nm: int = 0
    skipped_pads: list[str] = field(default_factory=list)  # SMD/connect pads: out of scope in v0

    @property
    def worst_pad(self) -> PadSnap | None:
        return max(self.pads, key=lambda p: p.dev_nm, default=None)


def snap_footprint(fp: Footprint, grid: Grid, tol_nm: int) -> SnapResult:
    """Map each THT pad of ``fp`` to its nearest hole and accept if all are within ``tol_nm``."""
    res = SnapResult(ref=fp.ref)
    for pad in fp.pads:
        if not pad.is_tht:
            res.skipped_pads.append(pad.number)
            continue
        node = grid.nearest(pad.x_nm, pad.y_nm)
        hx, hy = grid.hole_xy(node)
        res.pads.append(
            PadSnap(
                number=pad.number,
                net=pad.net,
                node=node if grid.contains(node) else None,
                dx_nm=pad.x_nm - hx,
                dy_nm=pad.y_nm - hy,
            )
        )
    if not res.pads:
        res.reason = "no through-hole pads"
        return res
    res.max_dev_nm = max(p.dev_nm for p in res.pads)
    dxs = [p.dx_nm for p in res.pads]
    dys = [p.dy_nm for p in res.pads]
    sx = -(min(dxs) + max(dxs)) // 2
    sy = -(min(dys) + max(dys)) // 2
    res.shift_nm = (sx, sy)
    res.max_dev_after_shift_nm = max(round(math.hypot(p.dx_nm + sx, p.dy_nm + sy)) for p in res.pads)
    off_board = [p.number for p in res.pads if p.node is None]
    if off_board:
        res.accepted = False
        res.reason = f"pad(s) {', '.join(off_board)} fall outside the {grid.cols}x{grid.rows} grid"
    elif res.max_dev_nm > tol_nm:
        res.accepted = False
        worst = res.worst_pad
        assert worst is not None
        res.reason = (
            f"pad {worst.number} is {worst.dev_nm / 1e6:.3f} mm from the nearest hole "
            f"(tolerance {tol_nm / 1e6:.3f} mm)"
        )
    else:
        nodes = [p.node for p in res.pads]
        dup = {n for n in nodes if nodes.count(n) > 1}
        if dup:
            # Two pads of one footprint in one hole: tolerated only if they share a net
            # (the conflict check in strips.assign_holes decides).
            res.reason = f"several pads share hole(s) {', '.join(str(n) for n in sorted(dup))}"
    return res


def snap_board(
    board: Board, grid: Grid, tol_nm: int, skip_refs: list[str] | tuple[str, ...] = ()
) -> list[SnapResult]:
    return [snap_footprint(fp, grid, tol_nm) for fp in board.footprints if fp.ref not in skip_refs]
