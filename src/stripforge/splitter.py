# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Cut placement and net-per-piece assignment (Sketch.md §4.3, §4.5).

For each strip, walk the net-carrying holes in column order. Wherever two consecutive ones carry
different nets, exactly one cut goes between them:

* ``hole`` / ``auto``: a hole cut at the *free* hole strictly between them that is nearest their
  midpoint (ties go to the lower column);
* otherwise (``knife`` style, or no free hole): a knife cut on the middle segment between them.
  In ``auto``/``hole`` style this fallback is reported as a warning.

This gives the minimum number of cuts. After cutting, each connected run of copper is a piece
carrying one net or none. A net found on more than one piece is split and needs wire links (M2).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .config import CutStyle
from .grid import Node, hole_label
from .strips import HoleMap, Piece, Strip, pairs


@dataclass
class Cut:
    id: str  # "X1"...
    row: int
    col: float  # integer = hole cut at that hole; x.5 = knife cut between x and x+1
    style: str  # "hole" | "knife"
    reason: tuple[str, str]  # the two nets separated
    between: tuple[str, str] = ("", "")  # the pads either side, e.g. ("J2.3", "J2.4")

    @property
    def label(self) -> str:
        """``K12`` for a hole cut; ``K12-K13`` for a knife cut between those two holes."""
        c = int(self.col)
        if self.style == "hole":
            return hole_label(self.row, c)
        return f"{hole_label(self.row, c)}-{hole_label(self.row, c + 1)}"

    @property
    def where(self) -> str:
        if self.style == "hole":
            return f"hole {self.label}"
        c = int(self.col)
        return f"between {hole_label(self.row, c)} and {hole_label(self.row, c + 1)}"


@dataclass
class SplitResult:
    cuts: list[Cut] = field(default_factory=list)
    pieces: list[Piece] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def pieces_per_net(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for p in self.pieces:
            for n in p.nets:
                counts[n] += 1
        return dict(sorted(counts.items()))

    @property
    def split_nets(self) -> dict[str, int]:
        """Nets whose pads sit on more than one piece (each needs pieces-1 links at least)."""
        return {n: c for n, c in self.pieces_per_net.items() if c > 1}

    @property
    def multi_net_pieces(self) -> list[Piece]:
        return [p for p in self.pieces if len(p.nets) > 1]


def _row_pads(holes: HoleMap, row: int) -> list[tuple[int, str, str]]:
    """(col, net, pad-label) for every net-carrying hole in ``row``, sorted by column."""
    out = []
    for node, net in holes.nets.items():
        if node.row == row and net is not None:
            label = "/".join(o.label for o in holes.occupants[node])
            out.append((node.col, net, label))
    return sorted(out)


def place_cuts(strips: list[Strip], holes: HoleMap, style: CutStyle | str) -> tuple[list[Cut], list[str]]:
    style = CutStyle(style)
    cuts: list[Cut] = []
    warnings: list[str] = []
    for strip in strips:
        pads = _row_pads(holes, strip.row)
        for (ca, na, la), (cb, nb, lb) in pairs(pads):
            if na == nb:
                continue
            cut_id = f"X{len(cuts) + 1}"
            free = [c for c in range(ca + 1, cb) if holes.is_free(Node(strip.row, c))]
            if style is not CutStyle.KNIFE and free:
                mid2 = ca + cb  # compare 2*c against ca+cb to stay in integers
                col = min(free, key=lambda c: (abs(2 * c - mid2), c))
                strip.cut_hole(col)
                cuts.append(Cut(cut_id, strip.row, float(col), "hole", (na, nb), (la, lb)))
                continue
            segs = [c for c in range(ca, cb) if (strip.row, c) not in holes.slot_segments] or list(
                range(ca, cb)
            )
            mid2 = ca + cb - 1  # the middle segment, doubled
            seg = min(segs, key=lambda c: (abs(2 * c - mid2), c))
            strip.cut_knife(seg)
            cut = Cut(cut_id, strip.row, seg + 0.5, "knife", (na, nb), (la, lb))
            cuts.append(cut)
            if style is not CutStyle.KNIFE:
                why = "adjacent holes" if cb == ca + 1 else "no free hole between them"
                warnings.append(
                    f"{cut_id}: knife cut {cut.where} separating {la} [{na}] and {lb} [{nb}]: {why}"
                )
    return cuts, warnings


def assign_nets(strips: list[Strip], holes: HoleMap) -> list[Piece]:
    pieces: list[Piece] = []
    for strip in strips:
        for start, end in strip.runs():
            nets: list[str] = []
            labels: list[str] = []
            for c in range(start, end + 1):
                node = Node(strip.row, c)
                net = holes.nets.get(node)
                if net is not None and net not in nets:
                    nets.append(net)
                labels += [o.label for o in holes.occupants.get(node, [])]
            pieces.append(Piece(strip.row, start, end, tuple(sorted(nets)), tuple(labels)))
    return pieces


def split(strips: list[Strip], holes: HoleMap, style: CutStyle | str = CutStyle.AUTO) -> SplitResult:
    cuts, warnings = place_cuts(strips, holes, style)
    return SplitResult(cuts=cuts, pieces=assign_nets(strips, holes), warnings=warnings)
