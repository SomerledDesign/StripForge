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

import re
from collections import Counter
from dataclasses import dataclass, field

from .config import CutStyle
from .grid import Node, hole_label
from .strips import HoleMap, Piece, Strip, pairs

_BOARD_CUT = re.compile(r"CUT(\d+) in the board$")


def next_cut_id(cuts: list[Cut]) -> str:
    """A free cut id after the highest one in use ("X87" after X86)."""
    nums = [int(c.id[1:]) for c in cuts if c.id[1:].isdigit()]
    return f"X{max(nums, default=0) + 1}"


@dataclass
class Cut:
    id: str  # "X1"...
    row: int
    col: float  # integer = hole cut at that hole; x.5 = knife cut between x and x+1
    style: str  # "hole" | "knife"
    reason: tuple[str, str]  # the two nets separated
    between: tuple[str, str] = ("", "")  # the pads either side, e.g. ("J2.3", "J2.4")
    user: str = ""  # a cut you made (where it came from, e.g. "CUT12 in the board"); never slid
    # a board cut marker still where StripForge put it: kept like yours, but not called yours
    auto: bool = False
    knife_for: str = ""  # a knife cut made because of knife_cuts (the part refs, e.g. "SW2")
    note: str = field(default="", repr=False, compare=False)
    note_missing: str = field(default="", repr=False, compare=False)

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


def knife_keep_clear(holes: HoleMap, knife_refs) -> tuple[set[tuple[int, int]], set[tuple[int, int]]]:
    """For the ``knife_cuts`` parts: the holes right beside their pins (``(row, col)``, never cut: a
    drilled hole there would cut into the big pad) and the strip segments that touch those holes
    (``(row, seg)``: a knife cut there leaves the neighbour hole, whose copper the pad overhangs, on
    another net). Knife cuts go further out, so the hole beside each pin stays on the pin's net."""
    refs = set(knife_refs or ())
    near_holes: set[tuple[int, int]] = set()
    near_segs: set[tuple[int, int]] = set()
    for node, occ in holes.occupants.items():
        if refs & {o.ref for o in occ}:
            near_holes |= {(node.row, node.col - 1), (node.row, node.col + 1)}
            near_segs |= {(node.row, node.col - 1), (node.row, node.col)}
    return near_holes, near_segs


def place_cuts(
    strips: list[Strip],
    holes: HoleMap,
    style: CutStyle | str,
    fixed: list | None = None,
    no_cut: list | None = None,
    complete: bool = False,
    knife_refs=(),
) -> tuple[list[Cut], list[str]]:
    """Cut every strip between neighbouring pads of different nets (see the module docstring).

    ``fixed`` are the user's cuts (:class:`stripforge.edits.CutSpec`): each is made exactly where it
    is and never slid, and a gap that already has one gets no cut of StripForge's. ``no_cut`` are
    spots StripForge must not cut. With ``complete`` (the fixed cuts are all the board's cuts), a gap
    with no fixed cut would short two nets: StripForge cuts it and says so.

    ``knife_refs`` (``knife_cuts``) are parts with big pads: every cut next to one of their pins is a
    knife cut, placed to leave the hole beside each of its pins on that pin's net (see
    :func:`knife_keep_clear`); when the pins are too close for that, the best knife cut there is made
    and a warning says so.
    """
    knife_refs = set(knife_refs or ())
    near_holes, near_segs = knife_keep_clear(holes, knife_refs)
    style = CutStyle(style)
    found: list[tuple[int, float, Cut]] = []
    warnings: list[str] = []
    by_row = {s.row: s for s in strips}
    banned_holes = {(c.row, int(c.col)) for c in no_cut or [] if c.style == "hole"}
    banned_segs = {(c.row, int(c.col)) for c in no_cut or [] if c.style == "knife"}
    user: dict[int, list] = {}
    seen = set()
    for spec in fixed or []:
        strip = by_row.get(spec.row)
        c = int(spec.col)
        if strip is None or not 0 <= c < strip.cols or (spec.style == "knife" and c >= strip.cols - 1):
            warnings.append(f"{spec.source}: cut {spec.label} is off the stripboard; ignored")
            continue
        if spec.style == "hole" and Node(spec.row, c) in holes.occupants:
            who = ", ".join(o.label for o in holes.occupants[Node(spec.row, c)])
            warnings.append(
                f"{spec.source}: hole cut at {spec.label} would cut off the pin in it ({who}); ignored"
            )
            continue
        if spec.key in seen:
            continue
        seen.add(spec.key)
        user.setdefault(spec.row, []).append(spec)
    for strip in strips:
        pads = _row_pads(holes, strip.row)
        mine = user.get(strip.row, [])
        for (ca, na, la), (cb, nb, lb) in pairs(pads):
            if na == nb:
                continue
            if any(ca < u.col < cb for u in mine):
                continue  # the user's cut separates them
            listed = sorted(
                {o.ref for c in (ca, cb) for o in holes.occupants.get(Node(strip.row, c), [])} & knife_refs
            )
            if listed:
                cut = _knife_for(strip.row, ca, cb, na, nb, la, lb, listed, holes, banned_segs, near_segs)
                if complete:
                    cut.note_missing = (
                        f"no cut between {la} [{na}] and {lb} [{nb}] in the board: that would short the "
                        f"two nets, so StripForge cuts {cut.where}"
                    )
                found.append((strip.row, cut.col, cut))
                continue
            free = [
                c
                for c in range(ca + 1, cb)
                if holes.is_free(Node(strip.row, c))
                and (strip.row, c) not in banned_holes
                and (strip.row, c) not in near_holes
            ]
            if style is not CutStyle.KNIFE and free:
                mid2 = ca + cb  # compare 2*c against ca+cb to stay in integers
                col = min(free, key=lambda c: (abs(2 * c - mid2), c))
                cut = Cut("", strip.row, float(col), "hole", (na, nb), (la, lb))
            else:
                segs = (
                    [
                        c
                        for c in range(ca, cb)
                        if (strip.row, c) not in holes.slot_segments and (strip.row, c) not in banned_segs
                    ]
                    or [c for c in range(ca, cb) if (strip.row, c) not in banned_segs]
                    or list(range(ca, cb))
                )
                mid2 = ca + cb - 1  # the middle segment, doubled
                seg = min(segs, key=lambda c: (abs(2 * c - mid2), c))
                cut = Cut("", strip.row, seg + 0.5, "knife", (na, nb), (la, lb))
                if style is not CutStyle.KNIFE:
                    why = "adjacent holes" if cb == ca + 1 else "no free hole between them"
                    cut.note = f"knife cut {cut.where} separating {la} [{na}] and {lb} [{nb}]: {why}"
                if (strip.row, seg) in banned_segs:
                    warnings.append(
                        f"no_cut: {la} [{na}] and {lb} [{nb}] must be cut apart and there is nowhere "
                        f"else; cut {cut.where}"
                    )
            if complete:
                cut.note_missing = (
                    f"no cut between {la} [{na}] and {lb} [{nb}] in the board: that would short the two "
                    f"nets, so StripForge cuts {cut.where}"
                )
            found.append((strip.row, cut.col, cut))
        for u in mine:
            if u.style == "hole" and (strip.row, int(u.col)) in near_holes:
                warnings.append(
                    f"{u.source}: hole cut at {u.label} is right beside a pin of a knife_cuts part "
                    f"({', '.join(sorted(knife_refs))}): the drill cuts into its pad; a knife cut one hole "
                    "further out is safer"
                )
            left = [(c, n, lab) for c, n, lab in pads if c < u.col]
            right = [(c, n, lab) for c, n, lab in pads if c > u.col]
            ln, ll = (left[-1][1], left[-1][2]) if left else ("(bare strip)", "")
            rn, rl = (right[0][1], right[0][2]) if right else ("(bare strip)", "")
            cut = Cut("", strip.row, float(u.col), u.style, (ln, rn), (ll, rl), user=u.source)
            cut.auto = getattr(u, "placed", "you") == "stripforge"
            found.append((strip.row, cut.col, cut))
    cuts: list[Cut] = []
    # a cut marker in the board keeps its number (CUT85 stays X85), so a rebuilt board and its build
    # sheet name the cuts as the board does; the other cuts are numbered row by row after them
    kept: dict[int, int] = {}
    for _row, _col, cut in found:
        m = _BOARD_CUT.match(cut.user or "")
        if m and int(m.group(1)) not in kept.values():
            kept[id(cut)] = int(m.group(1))
    taken = set(kept.values())
    n = 0
    for row, _col, cut in sorted(found, key=lambda t: (t[0], t[1])):
        if id(cut) in kept:
            cut.id = f"X{kept[id(cut)]}"
        else:
            n += 1
            while n in taken:
                n += 1
            cut.id = f"X{n}"
        c = int(cut.col)
        if cut.style == "hole":
            by_row[row].cut_hole(c)
        else:
            by_row[row].cut_knife(c)
        cuts.append(cut)
        if cut.note:
            warnings.append(f"{cut.id}: {cut.note}")
        if cut.note_missing:
            warnings.append(f"{cut.id}: {cut.note_missing}")
    return cuts, warnings


def _knife_for(row, ca, cb, na, nb, la, lb, listed, holes, banned_segs, near_segs) -> Cut:
    """The knife cut between pads ``ca`` and ``cb`` when one of them belongs to a knife_cuts part:
    as far from that part's pins as it needs to be (leaving the hole beside each pin on its net),
    then as central as possible."""
    pins = [c for c in (ca, cb) if {o.ref for o in holes.occupants.get(Node(row, c), [])} & set(listed)]
    segs = [c for c in range(ca, cb) if (row, c) not in banned_segs] or list(range(ca, cb))
    clear = [c for c in segs if (row, c) not in near_segs and (row, c) not in holes.slot_segments]
    clear = clear or [c for c in segs if (row, c) not in near_segs]
    mid2 = ca + cb - 1

    def key(c: int) -> tuple:
        far = min(abs(2 * c + 1 - 2 * p) for p in pins)  # doubled distance to the nearest listed pin
        return (-min(far, 3), abs(2 * c - mid2), c)

    seg = min(clear or segs, key=key)
    who = " and ".join(listed)
    cut = Cut("", row, seg + 0.5, "knife", (na, nb), (la, lb), knife_for=who)
    if not clear:
        need = 3 if len(pins) == 2 else 2
        cut.note = (
            f"knife cut {cut.where} separating {la} [{na}] and {lb} [{nb}] (knife_cuts {who}): the pins are "
            f"{cb - ca} hole(s) apart, too close to leave a spare hole beside each big pad (needs {need}); "
            "the neighbour hole's copper sits within the pad's overhang, so check DRC clearance here"
        )
    return cut


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


def split(
    strips: list[Strip], holes: HoleMap, style: CutStyle | str = CutStyle.AUTO, edits=None, knife_refs=()
) -> SplitResult:
    if edits is None:
        cuts, warnings = place_cuts(strips, holes, style, knife_refs=knife_refs)
    else:
        cuts, warnings = place_cuts(
            strips, holes, style, edits.cuts, edits.no_cut, edits.complete, knife_refs=knife_refs
        )
    return SplitResult(cuts=cuts, pieces=assign_nets(strips, holes), warnings=warnings)
