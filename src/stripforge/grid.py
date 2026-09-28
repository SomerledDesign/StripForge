# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""2.54 mm hole grid and per-footprint snapping with a tolerance (Sketch.md §4.2).

Holes are indexed ``(col, row)`` from 0, with col 0/row 0 the top-left hole as seen from the
component side, in KiCad's board frame (x right, y down).

Human-readable hole labels (Kevin's decision): copper strips run horizontally, strips (rows) are
letters ``A..Z, AA, AB, ..., ZZ`` (spreadsheet style) and holes along a strip (columns) are numbered
from 1. The top-left hole is ``A1``; the 30 x 25 TPI fixture ends at ``Y30``. See
:func:`hole_label` and :func:`parse_hole`.

Snapping itself never moves anything. Each THT pad is mapped to its nearest hole, and the
footprint *snaps* when every pad is within ``tol`` of its hole. The offsets are reported so
slightly off-pitch parts (2.50 mm capacitors, the Littelfuse 395) are visible. Every footprint
also gets the rigid translation that minimises its worst per-axis pad offset (``shift_nm``);
``analyze.apply_best_fit`` and ``stripforge snap`` apply it (M2).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import PITCH_NM
from .board import Board, Footprint, Outline

ON_GRID_NM = 5_000  # offsets at or below this are treated as exactly on grid in reports

MAX_ROWS = 26 + 26 * 26  # A..Z then AA..ZZ


def row_label(row: int) -> str:
    """Strip letter for a 0-based row: 0 -> ``A``, 25 -> ``Z``, 26 -> ``AA``, 701 -> ``ZZ``.

    Rows outside ``0..701`` have no letter; they are written ``row<n>`` (``row-1``) so that
    off-board positions can still be reported.
    """
    if 0 <= row < 26:
        return chr(ord("A") + row)
    if 26 <= row < MAX_ROWS:
        hi, lo = divmod(row - 26, 26)
        return chr(ord("A") + hi) + chr(ord("A") + lo)
    return f"row{row}"


def parse_row_label(text: str) -> int:
    """Inverse of :func:`row_label` for ``A..ZZ`` (case-insensitive)."""
    t = text.strip().upper()
    if re.fullmatch(r"[A-Z]", t):
        return ord(t) - ord("A")
    if re.fullmatch(r"[A-Z]{2}", t):
        return 26 + (ord(t[0]) - ord("A")) * 26 + (ord(t[1]) - ord("A"))
    raise ValueError(f"not a strip letter: {text!r} (expected A..Z or AA..ZZ)")


def hole_label(row: int, col: int) -> str:
    """``A1``-style label for a 0-based (row, col): strip letter, then hole number from 1.

    A row with no letter (above ``A`` or past ``ZZ``) gives ``row-1.4`` (row -1, hole 4).
    """
    if 0 <= row < MAX_ROWS:
        return f"{row_label(row)}{col + 1}"
    return f"{row_label(row)}.{col + 1}"


_HOLE_RE = re.compile(r"\s*([A-Za-z]{1,2})\s*(\d+)\s*")


def parse_hole(text: str) -> Node:
    """Parse an ``A1``-style label (``Y30``, ``aa7``) into a 0-based :class:`Node`."""
    m = _HOLE_RE.fullmatch(text)
    if not m or int(m[2]) < 1:
        raise ValueError(f"not a hole label: {text!r} (expected e.g. A1, K12, AB3)")
    return Node(row=parse_row_label(m[1]), col=int(m[2]) - 1)


@dataclass(frozen=True, order=True)
class Node:
    row: int
    col: int

    @property
    def label(self) -> str:
        return hole_label(self.row, self.col)

    def __str__(self) -> str:
        return self.label


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
    def last(self) -> Node:
        """The bottom-right hole."""
        return Node(row=self.rows - 1, col=self.cols - 1)

    @property
    def span_label(self) -> str:
        """``A1-Y30`` for the 30 x 25 fixture."""
        return f"{Node(0, 0).label}-{self.last.label}"

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
    near: Node | None = None  # nearest grid position, even when it lies off the board

    @property
    def dev_nm(self) -> int:
        return round(math.hypot(self.dx_nm, self.dy_nm))

    @property
    def where(self) -> str:
        """Hole label, or ``off board (near Z31)`` for a pad outside the grid."""
        if self.node is not None:
            return self.node.label
        return f"off board (near {self.near.label})" if self.near is not None else "off board"


@dataclass(frozen=True)
class SlotJob:
    """A hole to file into a short slot along the strip so an off-pitch pad fits (slotted parts)."""

    ref: str
    pad: str
    hole: Node
    toward: Node  # the neighbouring hole the slot points at
    length_nm: int  # how far to elongate the hole (the pad's x offset)
    dy_nm: int = 0  # residual offset across the strip (within tolerance)

    @property
    def text(self) -> str:
        return f"file hole {self.hole.label} toward {self.toward.label} by {self.length_nm / 1e6:.3f} mm"


@dataclass
class SnapResult:
    ref: str
    pads: list[PadSnap] = field(default_factory=list)
    max_dev_nm: int = 0
    accepted: bool = True
    reason: str = ""
    # Rigid translation (nm) that would minimise the worst per-axis offset, and the resulting
    # worst offset. Applied by analyze.apply_best_fit / `stripforge snap` (not for slotted parts).
    shift_nm: tuple[int, int] = (0, 0)
    max_dev_after_shift_nm: int = 0
    skipped_pads: list[str] = field(default_factory=list)  # SMD/connect pads: out of scope in v0
    slotted: bool = False  # listed in the config's ``slotted``; never moved by apply_shifts
    slots: list[SlotJob] = field(default_factory=list)  # holes to elongate for this part

    @property
    def worst_pad(self) -> PadSnap | None:
        return max(self.pads, key=lambda p: p.dev_nm, default=None)

    @property
    def off_board_pads(self) -> list[PadSnap]:
        return [p for p in self.pads if p.node is None]


def snap_footprint(fp: Footprint, grid: Grid, tol_nm: int, slot_max_nm: int | None = None) -> SnapResult:
    """Map each THT pad of ``fp`` to its nearest hole and accept if all are within ``tol_nm``.

    With ``slot_max_nm`` (a slotted part), a pad further off than ``tol_nm`` is still accepted when
    it is off along the strip only: ``|dx| <= slot_max_nm`` and ``|dy| <= tol_nm``. Each such pad
    becomes a :class:`SlotJob` (file the hole toward the pad).
    """
    res = SnapResult(ref=fp.ref, slotted=slot_max_nm is not None)
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
                near=node,
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
    off_board = res.off_board_pads
    if off_board:
        res.accepted = False
        where = ", ".join(f"{p.number} (near {p.near.label})" for p in off_board if p.near is not None)
        res.reason = (
            f"off board: {len(off_board)} of {len(res.pads)} pad(s) fall outside the "
            f"{grid.cols}x{grid.rows} grid ({grid.span_label}): pad {where}"
        )
    elif slot_max_nm is not None and all(
        p.dev_nm <= tol_nm or (abs(p.dx_nm) <= slot_max_nm and abs(p.dy_nm) <= tol_nm) for p in res.pads
    ):
        for p in res.pads:
            if p.dev_nm > tol_nm and p.node is not None:
                step = 1 if p.dx_nm > 0 else -1
                toward = Node(row=p.node.row, col=p.node.col + step)
                res.slots.append(SlotJob(fp.ref, p.number, p.node, toward, abs(p.dx_nm), p.dy_nm))
    elif res.max_dev_nm > tol_nm:
        res.accepted = False
        worst = res.worst_pad
        assert worst is not None
        res.reason = (
            f"pad {worst.number} is {worst.dev_nm / 1e6:.3f} mm from the nearest hole "
            f"(tolerance {tol_nm / 1e6:.3f} mm)"
        )
        if slot_max_nm is None and all(abs(p.dy_nm) <= tol_nm for p in res.pads):
            res.reason += (
                "; its pads are off only along the strip, so if the part has slotted or off-pitch pins "
                f'list it in the config (slotted = ["{fp.ref}"] in stripboard.toml)'
            )
        if slot_max_nm is not None:
            res.reason += (
                f"; as a slotted part a pad may be up to {slot_max_nm / 1e6:.3f} mm off along the strip "
                f"and {tol_nm / 1e6:.3f} mm across it"
            )
    if res.accepted and res.pads and not off_board:
        nodes = [p.node for p in res.pads]
        dup = {n for n in nodes if nodes.count(n) > 1}
        if dup:
            # Two pads of one footprint in one hole: tolerated only if they share a net
            # (the conflict check in strips.assign_holes decides).
            res.reason = f"several pads share hole(s) {', '.join(str(n) for n in sorted(dup))}"
    return res


def snap_board(
    board: Board,
    grid: Grid,
    tol_nm: int,
    skip_refs: list[str] | tuple[str, ...] = (),
    slot_max_nm: dict[str, int] | None = None,
) -> list[SnapResult]:
    """Snap every footprint not in ``skip_refs``; ``slot_max_nm`` maps slotted refs to their allowance."""
    slots = slot_max_nm or {}
    return [
        snap_footprint(fp, grid, tol_nm, slots.get(fp.ref))
        for fp in board.footprints
        if fp.ref not in skip_refs
    ]
