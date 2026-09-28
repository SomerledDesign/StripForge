# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Strip model (Sketch.md §4.3).

Each grid row is one horizontal strip made of ``cols - 1`` hole-to-hole *segments*; segment ``c``
joins hole ``c`` to hole ``c + 1``. On the board each present segment becomes one B.Cu track from
hole centre to hole centre. A hole cut removes the hole's copper (both neighbouring segments);
a knife cut removes one segment.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .grid import Grid, Node, SnapResult, hole_label, row_label


def pairs(items: list) -> list[tuple]:
    """Consecutive pairs of ``items`` (``itertools.pairwise`` needs Python 3.10; KiCad's is 3.9)."""
    return [(items[i], items[i + 1]) for i in range(len(items) - 1)]


@dataclass
class Strip:
    row: int
    cols: int
    present: list[bool] = field(default_factory=list)  # segment c joins (row,c)-(row,c+1)
    dead_holes: set[int] = field(default_factory=set)  # holes consumed by hole cuts

    def __post_init__(self) -> None:
        if not self.present:
            self.present = [True] * max(self.cols - 1, 0)

    def cut_hole(self, col: int) -> None:
        self.dead_holes.add(col)
        if col > 0:
            self.present[col - 1] = False
        if col < self.cols - 1:
            self.present[col] = False

    def cut_knife(self, col: int) -> None:
        """Cut the copper between hole ``col`` and hole ``col + 1``."""
        self.present[col] = False

    def runs(self) -> list[tuple[int, int]]:
        """Maximal runs of connected live holes, as inclusive ``(col_start, col_end)``."""
        out: list[tuple[int, int]] = []
        c = 0
        while c < self.cols:
            if c in self.dead_holes:
                c += 1
                continue
            start = c
            while c < self.cols - 1 and self.present[c] and (c + 1) not in self.dead_holes:
                c += 1
            out.append((start, c))
            c += 1
        return out


@dataclass
class Piece:
    """A connected run of strip copper after cutting."""

    row: int
    col_start: int
    col_end: int
    nets: tuple[str, ...] = ()  # distinct pad nets on the piece; more than one is a short
    pads: tuple[str, ...] = ()  # pad labels ("R1.2") on the piece

    @property
    def net(self) -> str | None:
        return self.nets[0] if len(self.nets) == 1 else None

    @property
    def holes(self) -> int:
        return self.col_end - self.col_start + 1

    @property
    def label(self) -> str:
        """``K3-K7`` (or ``K3`` for a one-hole piece)."""
        a, b = hole_label(self.row, self.col_start), hole_label(self.row, self.col_end)
        return a if a == b else f"{a}-{b}"

    @property
    def strip(self) -> str:
        return row_label(self.row)


@dataclass(frozen=True)
class Occupant:
    ref: str
    pad: str
    net: str | None

    @property
    def label(self) -> str:
        return f"{self.ref}.{self.pad}"


@dataclass
class HoleMap:
    """Which pads sit in which hole, and the net each occupied hole carries."""

    occupants: dict[Node, list[Occupant]] = field(default_factory=dict)
    nets: dict[Node, str | None] = field(default_factory=dict)  # occupied holes only
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Empty holes that must not be cut: the neighbour a slot job files toward.
    reserved: set[Node] = field(default_factory=set)
    # (row, segment) pairs a slot runs along; knife cuts go elsewhere when they can.
    slot_segments: set[tuple[int, int]] = field(default_factory=set)

    def is_free(self, node: Node) -> bool:
        return node not in self.occupants and node not in self.reserved


def build_strips(grid: Grid) -> list[Strip]:
    return [Strip(row=r, cols=grid.cols) for r in range(grid.rows)]


def assign_holes(snaps: list[SnapResult]) -> HoleMap:
    """Place the pads of every snapped footprint into holes and give each hole its pad's net.

    Pads of rejected footprints have no hole and are reported as conflicts. Two different nets in
    one hole is a conflict (that hole's net is left unset); the same net twice is a warning.
    """
    hm = HoleMap()
    occ: dict[Node, list[Occupant]] = defaultdict(list)
    for snap in snaps:
        for ps in snap.pads:
            if not snap.accepted or ps.node is None:
                hm.conflicts.append(f"pad {snap.ref}.{ps.number} has no hole ({snap.reason})")
                continue
            occ[ps.node].append(Occupant(snap.ref, ps.number, ps.net))
        if snap.accepted:
            for job in snap.slots:
                hm.reserved.add(job.toward)
                hm.slot_segments.add((job.hole.row, min(job.hole.col, job.toward.col)))
    for node in sorted(occ):
        who = occ[node]
        hm.occupants[node] = who
        nets = sorted({o.net for o in who if o.net})
        if len(nets) > 1:
            pads = ", ".join(f"{o.label} [{o.net}]" for o in who)
            hm.conflicts.append(f"hole {node}: different nets in one hole: {pads}")
            hm.nets[node] = None
            continue
        hm.nets[node] = nets[0] if nets else None
        if len(who) > 1:
            labels = ", ".join(o.label for o in who)
            hm.warnings.append(f"hole {node}: {len(who)} pads share one hole: {labels}")
    return hm
