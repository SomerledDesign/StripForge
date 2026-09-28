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


# A slotted part whose best-fit shift along the strip is more than this is placed lopsided: one
# pad nearer its hole than the other (e.g. BT1 grabbed by pad 1 and snapped to the grid in KiCad).
LOPSIDED_NM = 50_000


@dataclass(frozen=True)
class SlotJob:
    """A hole to file into a short slot along the strip so an off-pitch pad fits (slotted parts)."""

    ref: str
    pad: str
    hole: Node
    toward: Node  # the neighbouring hole the slot points at
    length_nm: int  # the pad centre's offset from the hole centre along the strip
    dy_nm: int = 0  # residual offset across the strip (within tolerance)
    # How far to file the hole edge along the strip so the pad's drill fits: the offset plus half
    # of how much longer than wide an oval drill is (0: same as length_nm, a round drill). For the
    # BH23APC (oval 1.635 x 1.0 mm, 0.3175 mm off) that is 0.3175 + 0.3175 = 0.635 mm (0.025").
    file_nm: int = 0
    inward: bool = True  # the slot points toward the part's centre (the usual case)

    @property
    def file_len_nm(self) -> int:
        return self.file_nm or self.length_nm

    @property
    def amount(self) -> str:
        """``0.025" (0.635 mm)``: the filing distance in inches (how stripboard is sold) and mm."""
        mm = self.file_len_nm / 1e6
        return f'{mm / 25.4:.3f}" ({mm:.3f} mm)'

    @property
    def text(self) -> str:
        where = "toward the part centre" if self.inward else "away from the part centre"
        return f"file hole {self.hole.label} {self.amount} toward {self.toward.label} ({where})"


def slot_file_nm(dx_nm: int, drill_along_nm: int, drill_across_nm: int) -> int:
    """Filing distance for a pad ``dx_nm`` off its hole with a drill of the given extents.

    The stripboard hole is taken to be as wide as the drill's across-strip size, so a round drill
    only needs the hole moved by ``|dx|`` and an oval one another ``(along - across) / 2``.
    """
    return abs(dx_nm) + max(0, drill_along_nm - drill_across_nm) // 2


@dataclass(frozen=True)
class LegBend:
    """A leg to bend onto its hole (a [bend] part): how far, and which way relative to the strip."""

    ref: str
    pad: str
    hole: Node
    dx_nm: int  # along the strip (a slot takes this part for a slotted part)
    dy_nm: int  # across the strip
    bend_nm: int  # the distance to bend: |dy| for a slotted pad, else the full offset

    @property
    def direction(self) -> str:
        return "across the strip" if abs(self.dy_nm) >= abs(self.dx_nm) else "along the strip"

    @property
    def text(self) -> str:
        return f"pad {self.pad} at {self.hole.label}: {bend_text(self.bend_nm)} {self.direction}"


def bend_text(nm: int) -> str:
    """``0.152 mm (6.0 mil)``: stripboard pitch is imperial, so mils help (6 mil = 0.1524 mm)."""
    return f"{nm / 1e6:.3f} mm ({nm / 25_400:.1f} mil)"


def suggest_bend_mm(nm: int) -> float:
    """A [bend] value that covers ``nm``: rounded up to the next 0.01 mm (0.1524 -> 0.16)."""
    return math.ceil(nm / 10_000 - 1e-9) / 100


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
    lopsided: str = ""  # a slotted part off-centre along the strip (a warning), see LOPSIDED_NM
    # The "Beckham tolerance" (bend it like Beckham): a part listed in the config's [bend] table
    # may have legs up to bend_nm off their holes in any direction, because the builder bends
    # them onto the holes. bend_nm is None for other parts; bends lists the pads that need it.
    bend_nm: int | None = None
    bends: list[LegBend] = field(default_factory=list)

    @property
    def worst_pad(self) -> PadSnap | None:
        return max(self.pads, key=lambda p: p.dev_nm, default=None)

    @property
    def off_board_pads(self) -> list[PadSnap]:
        return [p for p in self.pads if p.node is None]


def snap_footprint(
    fp: Footprint, grid: Grid, tol_nm: int, slot_max_nm: int | None = None, bend_nm: int | None = None
) -> SnapResult:
    """Map each THT pad of ``fp`` to its nearest hole and accept if all are within ``tol_nm``.

    With ``slot_max_nm`` (a slotted part), a pad further off than ``tol_nm`` is still accepted when
    it is off along the strip only: ``|dx| <= slot_max_nm`` and ``|dy| <= tol_nm``. Each such pad
    becomes a :class:`SlotJob` (file the hole toward the pad).

    With ``bend_nm`` (the part's "Beckham tolerance" from [bend]: bend it like Beckham), ``bend_nm``
    replaces ``tol_nm`` for this part, in any direction (for a slotted part: across the strip).
    Every pad further off than the board's ``tol_nm`` becomes a :class:`LegBend`.
    """
    res = SnapResult(ref=fp.ref, slotted=slot_max_nm is not None, bend_nm=bend_nm)
    board_tol_nm = tol_nm
    if bend_nm is not None:
        tol_nm = bend_nm
    tht = []
    for pad in fp.pads:
        if not pad.is_tht:
            res.skipped_pads.append(pad.number)
            continue
        tht.append(pad)
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
        centre_x = sum(pd.x_nm for pd in tht) / len(tht)
        for p, pd in zip(res.pads, tht):
            if p.dev_nm > tol_nm and p.node is not None:
                step = 1 if p.dx_nm > 0 else -1
                toward = Node(row=p.node.row, col=p.node.col + step)
                hole_x = pd.x_nm - p.dx_nm
                inward = (centre_x - hole_x) * step > 0
                file_nm = slot_file_nm(p.dx_nm, pd.drill_x_nm, pd.drill_y_nm)
                res.slots.append(
                    SlotJob(fp.ref, p.number, p.node, toward, abs(p.dx_nm), p.dy_nm, file_nm, inward)
                )
        outward = [j for j in res.slots if not j.inward]
        if outward:
            half = grid.pitch_nm // 2
            res.lopsided = (
                f"{fp.ref}: the slot(s) at {', '.join(j.hole.label for j in outward)} point away from the "
                f"part centre (its pads sit outboard of their holes), so it is probably half a pitch off: "
                f"move it {half / 1e6:.3f} mm along the strip (either way) to put its origin on a hole"
            )
        elif res.slots and abs(sx) > LOPSIDED_NM:
            offs = "/".join(f"{abs(p.dx_nm) / 1e6:.3f}" for p in res.pads)
            way = "lower" if sx < 0 else "higher"
            res.lopsided = (
                f"{fp.ref} slot offsets {offs} mm; shift {sx / 1e6:.3f} mm along the strip (toward {way} "
                f"hole numbers) to centre it, so every end hole is filed the same amount"
            )
    elif res.max_dev_nm > tol_nm:
        res.accepted = False
        worst = res.worst_pad
        assert worst is not None
        src = " from [bend]" if bend_nm is not None else ""
        res.reason = (
            f"pad {worst.number} is {worst.dev_nm / 1e6:.3f} mm from the nearest hole "
            f"(tolerance {tol_nm / 1e6:.3f} mm{src})"
        )
        if slot_max_nm is not None:
            res.reason += (
                f"; as a slotted part a pad may be up to {slot_max_nm / 1e6:.3f} mm off along the strip "
                f"and {tol_nm / 1e6:.3f} mm across it"
            )
        across = [p for p in res.pads if abs(p.dy_nm) > tol_nm]
        if res.max_dev_after_shift_nm <= tol_nm and (sx, sy) != (0, 0):
            # the pins match the pitch; the part is just not on the holes
            res.reason += (
                f"; its pins match the hole pitch, so it is just off the grid: move it by "
                f"({sx / 1e6:.3f}, {sy / 1e6:.3f}) mm (KiCad: Move Exactly) to put every pin on a hole"
            )
        elif across:
            # an across-strip miss: legs can often be bent that far (the Beckham tolerance)
            if slot_max_nm is not None:
                need = max(abs(p.dy_nm) for p in res.pads)
            else:
                need = res.max_dev_nm
            miss = max(abs(p.dy_nm) for p in across)
            res.reason += (
                f"; its legs miss the holes across the strip by up to {bend_text(miss)}"
                f", so if they can be bent onto the holes add it to the config's [bend] table "
                f"({fp.ref} = {suggest_bend_mm(need):.2f})"
            )
        elif slot_max_nm is None:
            res.reason += (
                "; its pads are off only along the strip, so if the part has slotted or off-pitch pins "
                f'list it in the config (slotted = ["{fp.ref}"] in stripboard.toml)'
            )
    if res.accepted and bend_nm is not None and not off_board:
        slotted_pads = {j.pad for j in res.slots}
        for p in res.pads:
            amount = abs(p.dy_nm) if p.number in slotted_pads else p.dev_nm
            if amount > board_tol_nm and p.node is not None:
                res.bends.append(LegBend(fp.ref, p.number, p.node, p.dx_nm, p.dy_nm, amount))
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
    bend_nm: dict[str, int] | None = None,
) -> list[SnapResult]:
    """Snap every footprint not in ``skip_refs``; ``slot_max_nm`` maps slotted refs to their
    allowance and ``bend_nm`` maps [bend] refs to their Beckham tolerance."""
    slots = slot_max_nm or {}
    bends = bend_nm or {}
    return [
        snap_footprint(fp, grid, tol_nm, slots.get(fp.ref), bends.get(fp.ref))
        for fp in board.footprints
        if fp.ref not in skip_refs
    ]
