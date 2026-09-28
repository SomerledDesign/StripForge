# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Wire-link proposals for nets split across strip pieces (Sketch.md §4.4), pass 1 of the link flow.

A link is a zero-ohm ``W`` jumper from Mildrew's family (``StripForge:Link_P2.54`` …
``StripForge:Link_P81.28``, 1–32 pitches, plus the off-pitch ``Link_D*`` diagonals). It joins a
free hole on one piece of a net to a free hole on another piece of the same net. Every grid hole
counts, not just the ones parts sit in: a *free* hole is any stripboard hole with no pad in it that
is not a hole cut, not the hole a slotted pad is filed toward, and not already used by another
link. A link runs

* straight down a column, across strips (the classic link; tried first, and the only kind that
  can slide a cut, below);
* along a strip, bridging the cut(s) between two pieces of the same net on that strip (the
  footprint rotated 90°);
* on a diagonal from any free hole to any free hole (``diagonal_links``): a Link_P* footprint
  rotated to that angle when the length is a whole number of pitches (3-4-5, 6-8-10, …), else a
  rotated off-pitch ``Link_D<mm>`` (1x1 = ``Link_D3.59``, 2x3 = ``Link_D9.16``, …;
  ``off_pitch_links = false`` keeps to whole-pitch diagonals);
* or as a pair of column or diagonal links meeting on a *bus strip*: a piece of bare, unused strip that the
  planner gives to the net (cut off from the rest of the bare strip where that leaves a useful
  remainder) when no single link can join two pieces (``bus_strips``).

Links never cross or overlap another link (bare wire would short), no link is longer than
``max_link_mm`` (default 81.28 mm, 32 pitches).

The proposal is a greedy minimum spanning forest per net (Kruskal, shortest links first), so a
net split into *n* pieces gets *n − 1* joins when it can be joined at all (a bus-strip join is two
links). Candidates are ranked by: no overlap with another link, then how many part courtyards the
link's courtyard would cross (a wire under a part body), then the kind (column, along a strip,
diagonal, bus strip), then cut slides needed (below), then length, then how many part leads the
wire passes over, then position, so the result is deterministic.

When no column works, the planner may **slide a cut** along its gap (the cut between two pads of
different nets can sit anywhere between them) so that a piece reaches a column where a link can
land. A hole cut is moved to another free hole where possible, otherwise it becomes a knife cut;
every slide is reported. Nets that still can't be joined are reported as unlinkable, with their
pieces, so the placement can be changed.
"""

from __future__ import annotations

import copy
import csv
import io
import json
import math
from dataclasses import dataclass, field

from .board import rotate_nm
from .edits import ref_number
from .grid import Node, hole_label
from .sexpr import atom, find, find_all, head, mm_to_nm
from .splitter import Cut, assign_nets
from .strips import Piece, Strip
from .validate import validate

LIB = "StripForge"
LINK_MIN_PITCHES = 1
LINK_MAX_PITCHES = 32  # Link_P2.54 ... Link_P81.28
PITCH_MM = 2.54
LINK_COURTYARD_NM = 1_100_000  # half-width of the Link_P* courtyard (pad radius + 0.25 mm)
# A link lies flat on the component side, so it must not pass over a hole with a lead in it (a part
# pin or another link's end): the wire would touch it. Clearance from the wire's centreline to such
# a hole's centre: lead radius (0.5) + wire radius (0.3) + a little.
LEAD_CLEAR_NM = 900_000
PLAN_ROUNDS = 4
# Candidate tiers, the first thing a candidate is ranked by (straight links are always the desire):
# a straight-down link (sliding cuts if need be), a link along a strip, two straight-down links
# meeting on a bare bus strip, a whole-pitch diagonal, an off-pitch diagonal, and last a bus strip
# reached by a diagonal leg. Within a tier: courtyards crossed, cut slides, knife cuts, length.
TIER_VERTICAL, TIER_HORIZONTAL, TIER_BUS, TIER_DIAGONAL, TIER_OFF_PITCH, TIER_BUS_DIAGONAL = range(6)


def _tier(dx: int, dy: int) -> int:
    if dx == 0:
        return TIER_VERTICAL
    if dy == 0:
        return TIER_HORIZONTAL
    sq = dx * dx + dy * dy
    return TIER_DIAGONAL if math.isqrt(sq) ** 2 == sq else TIER_OFF_PITCH


def pythagorean_offsets(max_pitches: int) -> list[tuple[int, int]]:
    """``(dx, dy)`` hole offsets, ``dy > 0`` and ``dx != 0``, whose length is a whole number of
    pitches no more than ``max_pitches``: a straight Link_P* footprint rotated to that angle lands
    both pads on holes (3-4-5 gives (±3, 4) and (±4, 3), …)."""
    out = []
    for dy in range(1, max_pitches + 1):
        for dx in range(1, max_pitches + 1):
            c = math.isqrt(dx * dx + dy * dy)
            if c * c == dx * dx + dy * dy and c <= max_pitches:
                out += [(dx, dy), (-dx, dy)]
    return sorted(out, key=lambda o: (o[0] ** 2 + o[1] ** 2, o[1], o[0]))


def any_offsets(max_pitches: int) -> list[tuple[int, int]]:
    """Every ``(dx, dy)`` hole offset, ``dy > 0`` and ``dx != 0``, no longer than ``max_pitches``:
    whole-pitch ones use a rotated Link_P*, the rest a rotated off-pitch Link_D* (shortest first)."""
    out = [
        (s * dx, dy)
        for dy in range(1, max_pitches + 1)
        for dx in range(1, max_pitches + 1)
        for s in (1, -1)
        if dx * dx + dy * dy <= max_pitches * max_pitches
    ]
    return sorted(out, key=lambda o: (o[0] ** 2 + o[1] ** 2, o[1], o[0]))


def link_footprint_for(dx: int, dy: int) -> str:
    """The footprint for a link from a hole to the hole ``(dx, dy)`` away: ``Link_P*`` when the
    length is a whole number of pitches, else the off-pitch ``Link_D<mm>``."""
    sq = dx * dx + dy * dy
    k = math.isqrt(sq)
    if k * k == sq:
        return link_footprint(k)
    if sq > LINK_MAX_PITCHES**2:
        raise ValueError(
            f"no link footprint for a {abs(dx)}x{abs(dy)} offset (max {LINK_MAX_PITCHES} pitches)"
        )
    return f"{LIB}:Link_D{math.sqrt(sq) * PITCH_MM:.2f}"


def link_footprint(pitches: int) -> str:
    """``StripForge:Link_P7.62`` for a 3-pitch link."""
    if not LINK_MIN_PITCHES <= pitches <= LINK_MAX_PITCHES:
        raise ValueError(f"no link footprint for {pitches} pitches (1..{LINK_MAX_PITCHES})")
    return f"{LIB}:Link_P{pitches * PITCH_MM:.2f}"


@dataclass
class LinkProposal:
    ref_hint: str  # "W1"...
    net: str
    col: int  # column of pad 1
    row_a: int  # strip of pad 1 (the upper end; for a link along a strip, the same as row_b)
    row_b: int  # strip of pad 2
    footprint: str  # e.g. "StripForge:Link_P10.16"
    col_b: int | None = None  # column of pad 2 (None: the same column, a straight-down link)
    bus: str = ""  # "R" when this is one of a pair of links meeting on bus strip R
    origin: str = ""  # "board" (a W you placed), "config" ([manual] links): kept as it is

    @property
    def end_col(self) -> int:
        return self.col if self.col_b is None else self.col_b

    @property
    def dx(self) -> int:
        return self.end_col - self.col

    @property
    def dy(self) -> int:
        return self.row_b - self.row_a

    @property
    def kind(self) -> str:
        """``vertical`` (down a column), ``horizontal`` (along a strip) or ``diagonal``."""
        if self.dx == 0:
            return "vertical"
        return "horizontal" if self.dy == 0 else "diagonal"

    @property
    def span(self) -> float:
        """Exact length in pitches."""
        return math.hypot(self.dx, self.dy)

    @property
    def pitches(self) -> int | float:
        """Length in pitches: a whole number, or (off-pitch diagonal) rounded to 2 decimals."""
        whole = round(self.span)
        return whole if abs(self.span - whole) < 1e-9 else round(self.span, 2)

    @property
    def off_pitch(self) -> bool:
        return isinstance(self.pitches, float)

    @property
    def length_mm(self) -> float:
        return round(self.span * PITCH_MM, 2)

    @property
    def rotation(self) -> float:
        """Footprint rotation (KiCad degrees) that puts pad 2 on the far hole; pad 2 of a Link_P*
        footprint is ``pitches`` below pad 1 at rotation 0."""
        if self.dx == 0:
            return 0.0
        return round(math.degrees(math.atan2(self.dx, self.dy)) % 360.0, 4)

    @property
    def start(self) -> str:
        return hole_label(self.row_a, self.col)

    @property
    def end(self) -> str:
        return hole_label(self.row_b, self.end_col)

    @property
    def nodes(self) -> tuple[Node, Node]:
        return Node(self.row_a, self.col), Node(self.row_b, self.end_col)

    def to_dict(self) -> dict:
        return {
            "ref": self.ref_hint,
            "net": self.net,
            "from": self.start,
            "to": self.end,
            "col": self.col,
            "row_a": self.row_a,
            "col_b": self.end_col,
            "row_b": self.row_b,
            "kind": self.kind,
            "rotation": self.rotation,
            "bus": self.bus,
            "locked": self.origin,
            "pitches": self.pitches,
            "length_mm": self.length_mm,
            "footprint": self.footprint,
        }


@dataclass
class CutMove:
    cut_id: str
    before: str  # "hole D5"
    after: str  # "between D5 and D6"
    net: str  # the net whose piece was extended

    @property
    def text(self) -> str:
        nets = self.net.split("\n")
        what = f"a {nets[0]!r} link" if len(nets) == 1 else " and ".join(repr(n) for n in nets) + " links"
        return f"{self.cut_id}: moved from {self.before} to {self.after} so {what} can land"


def merge_moves(moves: list[CutMove]) -> list[CutMove]:
    """One entry per cut: a cut slid twice (for two nets) is reported from its first place to its last."""
    out: dict[str, CutMove] = {}
    for m in moves:
        prev = out.get(m.cut_id)
        if prev is None:
            out[m.cut_id] = CutMove(m.cut_id, m.before, m.after, m.net)
        else:
            nets = prev.net.split("\n")
            prev.after, prev.net = m.after, "\n".join(nets + [m.net] if m.net not in nets else nets)
    return [m for m in out.values() if m.before != m.after]


@dataclass
class Unlinkable:
    net: str
    groups: list[list[str]]  # piece labels, one list per group that could not be joined
    max_link_mm: float = LINK_MAX_PITCHES * PITCH_MM

    @property
    def text(self) -> str:
        groups = " | ".join(", ".join(g) for g in self.groups)
        return (
            f"net {self.net!r} can't be fully linked: {len(self.groups)} groups of pieces [{groups}] have "
            f"no pair of free holes a link of up to {self.max_link_mm:g} mm can join (down a column, along "
            "a strip, on a diagonal or via a bare bus strip) without crossing another link; "
            "move or rotate a part so the pieces come closer, or free some holes"
        )


@dataclass
class LinkPlan:
    links: list[LinkProposal] = field(default_factory=list)
    unlinkable: list[Unlinkable] = field(default_factory=list)
    cut_moves: list[CutMove] = field(default_factory=list)
    needed: int = 0  # sum over split nets of (pieces - 1)
    bus_cuts: list[Cut] = field(default_factory=list)  # cuts added to isolate bus strips

    @property
    def ok(self) -> bool:
        return not self.unlinkable

    @property
    def joins(self) -> int:
        """Piece-to-piece joins made (a bus-strip pair of links is one join)."""
        return self.needed - sum(len(u.groups) - 1 for u in self.unlinkable)

    def to_dict(self) -> dict:
        return {
            "links": [lk.to_dict() for lk in self.links],
            "links_needed": self.needed,
            "unlinkable": [{"net": u.net, "groups": u.groups, "message": u.text} for u in self.unlinkable],
            "cut_moves": [m.text for m in self.cut_moves],
        }


# --- courtyards ------------------------------------------------------------------------------

Box = tuple[int, int, int, int]


def courtyard_box(fp) -> Box | None:
    """Bounding box (board nm) of a footprint's F.CrtYd graphics, or None if it has none."""
    if fp.node is None:
        return None
    pts: list[tuple[int, int]] = []
    for g in fp.node:
        if not (head(g) or "").startswith("fp_") or atom(find(g, "layer"), 1) != "F.CrtYd":
            continue
        for key in ("start", "end", "mid", "center"):
            n = find(g, key)
            if n is not None:
                pts.append((mm_to_nm(atom(n, 1)), mm_to_nm(atom(n, 2))))
        if head(g) == "fp_circle" and len(pts) >= 2:
            (cx, cy), (ex, ey) = pts[-2], pts[-1]
            r = max(abs(ex - cx), abs(ey - cy))
            pts += [(cx - r, cy - r), (cx + r, cy + r)]
        poly = find(g, "pts")
        for xy in find_all(poly, "xy") if poly is not None else ():
            pts.append((mm_to_nm(atom(xy, 1)), mm_to_nm(atom(xy, 2))))
    if not pts:
        return None
    moved = [rotate_nm(x, y, fp.angle) for x, y in pts]
    xs = [fp.x_nm + x for x, _ in moved]
    ys = [fp.y_nm + y for _, y in moved]
    return min(xs), min(ys), max(xs), max(ys)


def _overlap(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# --- planner ---------------------------------------------------------------------------------


class _Planner:
    def __init__(
        self,
        a,
        skip_refs: set[str] = frozenset(),
        priority: tuple[str, ...] = (),
        cache: dict | None = None,
        locked: list[LinkProposal] = (),
        taken_refs: tuple[str, ...] = (),
    ) -> None:
        self.a = a
        # geometry that does not change while planning (courtyard crossings, part pins in the way),
        # shared between planning rounds
        self.cache: dict = {} if cache is None else cache
        self.priority = set(priority)
        cfg = a.config
        self.courtyards = [
            box
            for fp in a.board.footprints
            if fp.ref not in skip_refs and fp.ref not in cfg.offboard_refs
            if (box := courtyard_box(fp)) is not None
        ]
        self.strips: dict[int, Strip] = {s.row: s for s in a.strips}
        self.claimed: set[Node] = set()
        self.links: list[LinkProposal] = []
        self.segments: list[tuple[Node, Node]] = []  # the links' hole-to-hole wires
        self.no_bus: set = set()  # (net, its groups) with no bus-strip candidate
        self.no_offset: set = set()
        self.offset_best: dict = {}
        self.bus_best: dict = {}  # (net, its groups) -> its best bus-strip candidate so far
        self.pins = list(a.holes.occupants)  # holes with a part pin in them
        self.moves: list[CutMove] = []
        self.bus_cuts: list[Cut] = []
        self.bus_nodes: dict[Node, str] = {}  # link ends on a bus strip -> the net it now carries
        max_mm = float(getattr(cfg, "max_link_mm", LINK_MAX_PITCHES * PITCH_MM))
        self.max_link_mm = max_mm
        self.max_pitches = max(0, min(LINK_MAX_PITCHES, int(max_mm / PITCH_MM + 1e-9)))
        self.offsets: list[tuple[int, int]] = [(dx, 0) for dx in range(1, self.max_pitches + 1)]
        if getattr(cfg, "diagonal_links", True):
            if getattr(cfg, "off_pitch_links", True):
                self.offsets += any_offsets(self.max_pitches)
            else:
                self.offsets += pythagorean_offsets(self.max_pitches)
        self.diag_by_dy: dict[int, list[int]] = {}
        self.offset_rank: dict[int, dict[int, int]] = {}  # dy -> dx -> position in self.offsets
        for n, (dx, dy) in enumerate(self.offsets):
            self.offset_rank.setdefault(dy, {})[dx] = n
            if dy:
                self.diag_by_dy.setdefault(dy, []).append(dx)
        self.use_bus = bool(getattr(cfg, "bus_strips", True))
        self.taken_refs = tuple(taken_refs)
        ed = getattr(a, "edits", None)
        self.no_cut_holes = {(c.row, int(c.col)) for c in getattr(ed, "no_cut", []) if c.style == "hole"}
        self.no_cut_segs = {(c.row, int(c.col)) for c in getattr(ed, "no_cut", []) if c.style == "knife"}
        self._refresh()
        self.needed = sum(len(ids) - 1 for ids in self._all_groups_initial())
        # your links: laid first, exactly where they are
        for lk in locked:
            self.links.append(lk)
            self.segments.append(lk.nodes)
            self.claimed.update(lk.nodes)
            for n in lk.nodes:
                i = self.piece_at.get(n)
                if i is not None and not self.pieces[i].nets and not self.pieces[i].pads:
                    self.bus_nodes[n] = lk.net
        if locked:
            self._refresh()

    # pieces and connectivity
    def _refresh(self) -> None:
        pieces = assign_nets(self.a.strips, self.a.holes)
        if self.bus_nodes:
            for k, p in enumerate(pieces):
                if p.nets or p.pads:
                    continue
                nets = sorted(
                    {
                        net
                        for n, net in self.bus_nodes.items()
                        if n.row == p.row and p.col_start <= n.col <= p.col_end
                    }
                )
                if nets:
                    pieces[k] = Piece(p.row, p.col_start, p.col_end, tuple(nets), p.pads)
        self.a.split.pieces = pieces
        self.piece_at: dict[Node, int] = {}
        for i, p in enumerate(self.a.split.pieces):
            for c in range(p.col_start, p.col_end + 1):
                self.piece_at[Node(p.row, c)] = i

    @property
    def pieces(self) -> list[Piece]:
        return self.a.split.pieces

    def free(self, node: Node) -> bool:
        strip = self.strips.get(node.row)
        return (
            strip is not None
            and 0 <= node.col < strip.cols
            and node.col not in strip.dead_holes
            and node not in self.claimed
            and self.a.holes.is_free(node)
        )

    def groups(self) -> dict[str, list[list[int]]]:
        """Per split net: its pieces grouped into connected components (pieces joined by links)."""
        by_net: dict[str, list[int]] = {}
        for i, p in enumerate(self.pieces):
            if p.net:
                by_net.setdefault(p.net, []).append(i)
        parent = {i: i for ids in by_net.values() for i in ids}

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for lk in self.links:
            i, j = (self.piece_at.get(n) for n in lk.nodes)
            if i is not None and j is not None and i in parent and j in parent:
                parent[find(i)] = find(j)
        out: dict[str, list[list[int]]] = {}
        for net, ids in sorted(by_net.items()):
            comps: dict[int, list[int]] = {}
            for i in ids:
                comps.setdefault(find(i), []).append(i)
            if len(comps) > 1:
                out[net] = sorted(comps.values())
        return out

    # costs
    def _overlaps(self, col: int, r1: int, r2: int) -> int:
        return self._link_conflicts(Node(r1, col), Node(r2, col))

    def _link_conflicts(self, n1: Node, n2: Node, extra: list[tuple[Node, Node]] = ()) -> int:
        """How many existing links (and ``extra`` segments) the wire ``n1``-``n2`` crosses or touches."""
        r0, r1 = sorted((n1.row, n2.row))
        c0, c1 = sorted((n1.col, n2.col))
        hits = 0
        for m1, m2 in [*self.segments, *extra]:
            if max(m1.row, m2.row) < r0 or min(m1.row, m2.row) > r1:
                continue
            if max(m1.col, m2.col) < c0 or min(m1.col, m2.col) > c1:
                continue
            hits += _segments_meet(n1, n2, m1, m2)
        return hits

    def _crossings(self, col: int, r1: int, r2: int) -> int:
        """How many part courtyards the link's courtyard would overlap."""
        g = self.a.grid
        x, y1 = g.hole_xy(Node(r1, col))
        _, y2 = g.hole_xy(Node(r2, col))
        m = LINK_COURTYARD_NM
        box = (x - m, y1 - m, x + m, y2 + m)
        return sum(1 for c in self.courtyards if _overlap(box, c))

    def _crossings_any(self, n1: Node, n2: Node) -> int:
        """Courtyard crossings for a link in any direction (its courtyard as a thick line)."""
        key = (0, n1.row, n1.col, n2.row, n2.col)
        hit = self.cache.get(key)
        if hit is not None:
            return hit
        if n1.col == n2.col:
            r1, r2 = sorted((n1.row, n2.row))
            val = self._crossings(n1.col, r1, r2)
        else:
            g = self.a.grid
            p, q = g.hole_xy(n1), g.hole_xy(n2)
            x0, x1 = sorted((p[0], q[0]))
            y0, y1 = sorted((p[1], q[1]))
            m = LINK_COURTYARD_NM
            val = sum(
                1
                for c in self.courtyards
                if _overlap((x0 - m, y0 - m, x1 + m, y1 + m), c) and _seg_box_distance(p, q, c) < m
            )
        self.cache[key] = val
        return val

    def _over_leads(self, col: int, r1: int, r2: int) -> int:
        return sum(1 for r in range(r1 + 1, r2) if Node(r, col) in self.a.holes.occupants)

    def _near(self, nodes, n1: Node, n2: Node) -> bool:
        g = self.a.grid
        p, q = g.hole_xy(n1), g.hole_xy(n2)
        r0, r1 = (n1.row, n2.row) if n1.row <= n2.row else (n2.row, n1.row)
        c0, c1 = (n1.col, n2.col) if n1.col <= n2.col else (n2.col, n1.col)
        for n in nodes:
            if not (r0 - 1 <= n.row <= r1 + 1 and c0 - 1 <= n.col <= c1 + 1) or n == n1 or n == n2:
                continue
            x, y = g.hole_xy(n)
            if _point_seg_distance(x, y, p, q) < LEAD_CLEAR_NM:
                return True
        return False

    def _blocked(self, n1: Node, n2: Node) -> bool:
        """Would wire ``n1``-``n2`` pass over (or too close to) a hole with a lead in it (a part pin,
        or another link's end)?"""
        key = (1, n1.row, n1.col, n2.row, n2.col)
        static = self.cache.get(key)
        if static is None:
            static = self.cache[key] = self._near(self.pins, n1, n2)
        return static or self._near(self.claimed, n1, n2)

    def _over_leads_any(self, n1: Node, n2: Node) -> int:
        dr, dc = n2.row - n1.row, n2.col - n1.col
        steps = math.gcd(abs(dr), abs(dc))
        return sum(
            1
            for k in range(1, steps)
            if Node(n1.row + dr // steps * k, n1.col + dc // steps * k) in self.a.holes.occupants
        )

    # cut sliding
    def _gap(self, p: Piece, side: str) -> tuple[Cut, int, int] | None:
        """The cut on ``side`` of piece ``p`` and the pad columns ``(ca, cb)`` either side of it."""
        row_pads = sorted(n.col for n, net in self.a.holes.nets.items() if n.row == p.row and net)
        own = [c for c in row_pads if p.col_start <= c <= p.col_end]
        if not own:
            return None
        if side == "left":
            other = max((c for c in row_pads if c < own[0]), default=None)
            ca, cb = (other, own[0]) if other is not None else (None, None)
        else:
            other = min((c for c in row_pads if c > own[-1]), default=None)
            ca, cb = (own[-1], other) if other is not None else (None, None)
        if ca is None:
            return None
        for cut in self.a.split.cuts:
            if cut.row == p.row and ca < cut.col < cb:
                return None if cut.user else (cut, ca, cb)  # your cut stays where you put it
        return None

    def _slide_options(self, p: Piece) -> dict[int, tuple[Cut, str, int]]:
        """Holes ``p`` could gain by sliding one of its end cuts: ``{col: (cut, style, position)}``.

        The cut stays in its gap (between the same two pads), prefers a hole cut at the free hole
        nearest the gained hole, else a knife cut right next to it, and never hands over a hole the
        neighbouring piece already uses for a link.
        """
        out: dict[int, tuple[Cut, str, int]] = {}
        slots = self.a.holes.slot_segments
        knife_only = str(self.a.config.cut_style) == "knife"
        row_claims = {n.col for n in self.claimed if n.row == p.row}
        for side in ("left", "right"):
            g = self._gap(p, side)
            if g is None:
                continue
            cut, ca, cb = g
            if side == "left":
                for x in range(p.col_start - 1, ca, -1):
                    opts = [] if knife_only else [h for h in range(x - 1, ca, -1) if self._cuttable(p.row, h)]
                    hole = next((h for h in opts if not row_claims & set(range(h, p.col_start))), None)
                    if hole is not None:
                        out.setdefault(x, (cut, "hole", hole))
                    elif (
                        (p.row, x - 1) not in slots
                        and (p.row, x - 1) not in self.no_cut_segs
                        and not row_claims & set(range(x, p.col_start))
                    ):
                        out.setdefault(x, (cut, "knife", x - 1))
            else:
                for x in range(p.col_end + 1, cb):
                    opts = [] if knife_only else [h for h in range(x + 1, cb) if self._cuttable(p.row, h)]
                    hole = next((h for h in opts if not row_claims & set(range(p.col_end + 1, h + 1))), None)
                    if hole is not None:
                        out.setdefault(x, (cut, "hole", hole))
                    elif (
                        (p.row, x) not in slots
                        and (p.row, x) not in self.no_cut_segs
                        and not row_claims & set(range(p.col_end + 1, x + 1))
                    ):
                        out.setdefault(x, (cut, "knife", x))
        return out

    def _cuttable(self, row: int, col: int) -> bool:
        node = Node(row, col)
        return self.a.holes.is_free(node) and node not in self.claimed and (row, col) not in self.no_cut_holes

    def _apply_slide(self, cut: Cut, style: str, pos: int, net: str) -> None:
        strip = self.strips[cut.row]
        before = cut.where
        if cut.style == "hole":
            c = int(cut.col)
            strip.dead_holes.discard(c)
            if c > 0:
                strip.present[c - 1] = True
            if c < strip.cols - 1:
                strip.present[c] = True
        else:
            strip.present[int(cut.col)] = True
        if style == "hole":
            strip.cut_hole(pos)
            cut.col, cut.style = float(pos), "hole"
        else:
            strip.cut_knife(pos)
            cut.col, cut.style = pos + 0.5, "knife"
        self.moves.append(CutMove(cut.id, before, cut.where, net))
        self._refresh()

    # candidates
    def _candidates(self, net: str, comps: list[list[int]], allow_slides: bool):
        """Straight-down (column) links, possibly sliding cuts."""
        comp_of = {i: k for k, ids in enumerate(comps) for i in ids}
        ids = sorted(comp_of)
        slide = {i: self._slide_options(self.pieces[i]) for i in ids} if allow_slides else {}
        for x in range(len(ids)):
            for y in range(x + 1, len(ids)):
                i, j = ids[x], ids[y]
                if comp_of[i] == comp_of[j]:
                    continue
                p, q = self.pieces[i], self.pieces[j]
                if p.row == q.row:
                    continue
                if p.row > q.row:
                    p, q, i, j = q, p, j, i
                k = q.row - p.row
                if k > self.max_pitches:
                    continue
                cols_p = set(range(p.col_start, p.col_end + 1)) | set(slide.get(i, {}))
                cols_q = set(range(q.col_start, q.col_end + 1)) | set(slide.get(j, {}))
                for col in sorted(cols_p & cols_q):
                    need = []
                    if not p.col_start <= col <= p.col_end:
                        need.append(slide[i][col])
                    if not q.col_start <= col <= q.col_end:
                        need.append(slide[j][col])
                    if need and not allow_slides:
                        continue
                    # a hole cut that slides away frees its own hole
                    moving = {Node(c.row, int(c.col)) for c, _, _ in need if c.style == "hole"}
                    if not all(
                        self.free(n) or n in moving and self._cuttable(n.row, n.col)
                        for n in (Node(p.row, col), Node(q.row, col))
                    ):
                        continue
                    if self._blocked(Node(p.row, col), Node(q.row, col)) or self._overlaps(col, p.row, q.row):
                        continue
                    knives = sum(1 for _, style, _ in need if style == "knife")
                    cost = (
                        TIER_VERTICAL,
                        self._crossings(col, p.row, q.row),
                        0,
                        len(need),
                        knives,
                        k,
                        self._over_leads(col, p.row, q.row),
                        p.row,
                        col,
                        net,
                    )
                    link = LinkProposal("", net, col, p.row, q.row, link_footprint(k))
                    yield cost, net, [link], need, None

    def _offset_candidates(self, net: str, comps: list[list[int]]):
        """Links along a strip (bridging a cut) and diagonals, from any free hole of one group to any
        free hole of another (targets are enumerated piece by piece, not offset by offset)."""
        comp_of = {i: k for k, ids in enumerate(comps) for i in ids}
        free_cols = {
            i: [c for c in range(self.pieces[i].col_start, self.pieces[i].col_end + 1)
                if self.free(Node(self.pieces[i].row, c))]
            for i in comp_of
        }  # fmt: skip
        for i in sorted(comp_of):
            p = self.pieces[i]
            targets = []
            for j in comp_of:
                q = self.pieces[j]
                dy = q.row - p.row
                if comp_of[j] == comp_of[i] or not 0 <= dy <= self.max_pitches or dy not in self.offset_rank:
                    continue
                targets.append((dy, q, free_cols[j]))
            if not targets:
                continue
            for c in free_cols[i]:
                n1 = Node(p.row, c)
                ends = []
                for dy, q, cols in targets:
                    rank = self.offset_rank[dy]
                    ends += [(rank[c2 - c], Node(q.row, c2)) for c2 in cols if c2 - c in rank]
                ends.sort(key=lambda e: e[0])
                for _, n2 in ends:
                    if self._blocked(n1, n2) or self._link_conflicts(n1, n2):
                        continue
                    lk = LinkProposal("", net, c, p.row, n2.row, "", col_b=n2.col)
                    lk.footprint = link_footprint_for(lk.dx, lk.dy)
                    cost = (
                        _tier(lk.dx, lk.dy),
                        self._crossings_any(n1, n2),
                        0,
                        0,
                        0,
                        lk.span,
                        self._over_leads_any(n1, n2),
                        p.row,
                        c,
                        net,
                    )
                    yield cost, net, [lk], [], None

    def _legs_to(self, comp: list[int], bus: Piece, avoid: list[tuple[Node, Node]], limit: int | None = None):
        """Links from any free hole of ``comp`` to bare piece ``bus``: straight down/up a column (also
        from a hole a piece gains by sliding one of its cuts), or on a diagonal. Cheapest first:
        ``(crossings, diagonal, slides, knives, length, col, n1, n2, need)``; with ``limit``, only the
        cheapest ``limit`` (exact: groups are checked in (straight, no slide, length) order and the
        search stops once ``limit`` legs cross no courtyard)."""
        taken = {n for seg in avoid for n in seg}
        bus_cols = {
            c
            for c in range(bus.col_start, bus.col_end + 1)
            if (n := Node(bus.row, c)) not in taken and self.free(n)
        }
        # (diagonal, slides, dx² + k²) -> [(row, dx, cols, need-by-col)]
        groups: dict[tuple[bool, int, int], list] = {}
        for i in comp:
            p = self.pieces[i]
            k = abs(p.row - bus.row)
            if k == 0 or k > self.max_pitches:
                continue
            sign = 1 if bus.row > p.row else -1
            cols = [c for c in range(p.col_start, p.col_end + 1)
                    if (n := Node(p.row, c)) not in taken and self.free(n)]  # fmt: skip
            if cols:
                for dx in [0, *self.diag_by_dy.get(k, [])]:
                    groups.setdefault((dx != 0, 0, dx * dx + k * k), []).append((p.row, dx * sign, cols, {}))
            # straight legs from a hole the piece gains by sliding a cut along its gap
            slid = {}
            for col, opt in self._slide_options(p).items():
                if p.col_start <= col <= p.col_end or col not in bus_cols:
                    continue
                n1 = Node(p.row, col)
                moving = opt[0].style == "hole" and int(opt[0].col) == col
                if n1 in taken or not (self.free(n1) or moving and self._cuttable(p.row, col)):
                    continue
                slid[col] = [opt]
            if slid:
                groups.setdefault((False, 1, k * k), []).append((p.row, 0, sorted(slid), slid))
        out = []
        clean = 0
        for diag, slides, sq in sorted(groups):
            legs = sorted(
                [
                    (c, Node(row, c), Node(bus.row, c + dx), need.get(c, []))
                    for row, dx, cols, need in groups[(diag, slides, sq)]
                    for c in cols
                    if c + dx in bus_cols
                ],
                key=lambda t: (t[0], t[1].row, t[2].col),
            )
            for c, n1, n2, need in legs:
                if self._blocked(n1, n2) or self._link_conflicts(n1, n2, avoid):
                    continue
                crossings = self._crossings_any(n1, n2)
                knives = sum(1 for _, style, _ in need if style == "knife")
                out.append((crossings, diag, slides, knives, math.sqrt(sq), c, n1, n2, need))
                clean += crossings == 0
            if limit is not None and clean >= limit:
                break
        out.sort(key=lambda t: t[:6])
        return out if limit is None else out[:limit]

    def _bus_candidates(self, net: str, comps: list[list[int]]):
        """Two links meeting on a piece of bare strip (the bus), for two unjoined groups."""
        bare = [b for b, p in enumerate(self.pieces) if not p.nets and not p.pads and p.holes >= 1]
        for x in range(len(comps)):
            for y in range(x + 1, len(comps)):
                rows_x = {self.pieces[i].row for i in comps[x]}
                rows_y = {self.pieces[i].row for i in comps[y]}
                for b in bare:
                    bus = self.pieces[b]
                    reach = self.max_pitches
                    if not any(0 < abs(r - bus.row) <= reach for r in rows_x) or not any(
                        0 < abs(r - bus.row) <= reach for r in rows_y
                    ):
                        continue
                    best = None
                    for cx, dgx, sx, kx, lx, colx, a1, a2, needx in self._legs_to(comps[x], bus, [], 6):
                        for cy, dgy, sy, ky, ly, coly, b1, b2, needy in self._legs_to(
                            comps[y], bus, [(a1, a2)], 1
                        ):
                            if {id(c) for c, _, _ in needx} & {id(c) for c, _, _ in needy}:
                                break  # both legs would slide the same cut
                            span = lx + ly + abs(a2.col - b2.col) / 100
                            cost = (
                                TIER_BUS_DIAGONAL if dgx or dgy else TIER_BUS,
                                cx + cy,
                                0,
                                sx + sy,
                                kx + ky,
                                span,
                                0,
                                bus.row,
                                min(colx, coly),
                                net,
                            )
                            if best is None or cost < best[0]:
                                best = (cost, (a1, a2), (b1, b2), needx + needy)
                            break
                    if best is None:
                        continue
                    cost, leg_a, leg_b, need = best
                    links = []
                    for n1, n2 in (leg_a, leg_b):
                        top, bot = sorted((n1, n2), key=lambda n: n.row)
                        lk = LinkProposal(
                            "", net, top.col, top.row, bot.row, "", col_b=bot.col, bus=bus.strip
                        )
                        if lk.dx == 0:
                            lk.col_b = None
                        lk.footprint = link_footprint_for(lk.dx, lk.dy)
                        links.append(lk)
                    yield cost, net, links, need, (bus.row, bus.col_start, bus.col_end)

    def _best_offset(self, net: str, comps: list[list[int]]):
        """The cheapest along-a-strip or diagonal candidate for ``net`` (cached while it stays
        layable: options only shrink as links are laid, so a cached best stays the best)."""
        memo = (net, tuple(tuple(self.pieces[i].label for i in c) for c in comps))
        if memo in self.no_offset:
            return None
        cand = self.offset_best.get(memo)
        if cand is not None:
            n1, n2 = cand[2][0].nodes
            ok = self.free(n1) and self.free(n2) and not self._near(self.claimed, n1, n2)
            if ok and not self._link_conflicts(n1, n2):
                return cand
        cand = min(self._offset_candidates(net, comps), key=lambda c: c[0], default=None)
        if cand is None:
            self.no_offset.add(memo)
        else:
            self.offset_best[memo] = cand
        return cand

    def _bus_index(self, span: tuple[int, int, int]) -> int | None:
        """Index of the bare piece spanning ``(row, col_start, col_end)``, if it is still bare."""
        for i, p in enumerate(self.pieces):
            if (p.row, p.col_start, p.col_end) == span:
                return None if p.nets or p.pads else i
        return None

    def _still_valid(self, cand) -> bool:
        """Can a bus candidate found in an earlier iteration still be laid as it is?"""
        _, _, links, need, span = cand
        if need or self._bus_index(span) is None:  # a cut slide may be stale: recompute
            return False
        for lk in links:
            n1, n2 = lk.nodes
            if not (self.free(n1) and self.free(n2)) or self._near(self.claimed, n1, n2):
                return False
            if self._link_conflicts(n1, n2):
                return False
        return True

    def _isolate_bus(self, b: int, links: list[LinkProposal], net: str) -> None:
        """Give bare piece ``b`` to ``net`` and cut it down to the span its links use, where the
        rest of the bare strip left over is worth keeping (two holes or more)."""
        bus = self.pieces[b]
        strip = self.strips[bus.row]
        ends = [n for lk in links for n in lk.nodes if n.row == bus.row]
        lo, hi = min(n.col for n in ends), max(n.col for n in ends)
        for n in ends:
            self.bus_nodes[n] = net
        knife_only = str(self.a.config.cut_style) == "knife"
        for side, hole, seg, spare in (
            ("left", lo - 1, lo - 1, lo - 1 - bus.col_start),
            ("right", hi + 1, hi, bus.col_end - hi - 1),
        ):
            if spare < 2:
                continue
            cut_id = f"X{len(self.a.split.cuts) + 1}"
            why = (net, "(bare strip)") if side == "right" else ("(bare strip)", net)
            if not knife_only and self._cuttable(bus.row, hole):
                strip.cut_hole(hole)
                cut = Cut(cut_id, bus.row, float(hole), "hole", why, ("bus", "bare strip"))
            elif (bus.row, seg) in self.no_cut_segs:
                continue  # no_cut: leave the rest of the bare strip on the bus
            else:
                strip.cut_knife(seg)
                cut = Cut(cut_id, bus.row, seg + 0.5, "knife", why, ("bus", "bare strip"))
            self.a.split.cuts.append(cut)
            self.bus_cuts.append(cut)

    def run(self) -> LinkPlan:
        needed = self.needed
        while True:
            groups = self.groups()
            if not groups:
                break
            best, best_key = None, None
            net_tier: dict[str, int] = {}
            for net, comps in groups.items():
                cands = list(self._candidates(net, comps, allow_slides=True))
                if not cands:  # no straight link: along a strip or a diagonal
                    off = self._best_offset(net, comps)
                    if off is not None:
                        cands.append(off)
                for cand in cands:
                    net_tier[net] = min(net_tier.get(net, 99), cand[0][0])
                    key = (net not in self.priority, *cand[0])
                    if best_key is None or key < best_key:
                        best, best_key = cand, key
            if self.use_bus:
                # bus strips (two straight links beat a diagonal): for any net with nothing better
                # than a diagonal, and always for priority nets (the ones an earlier round left
                # unjoined) so they get their bus before others box them in
                bus_nets = {
                    n: c for n, c in groups.items() if net_tier.get(n, 99) > TIER_BUS or n in self.priority
                }
                for net, comps in bus_nets.items():
                    # options only shrink as links are laid: a net whose groups have not changed
                    # keeps its best bus candidate while that can still be laid, and a net that had
                    # none still has none
                    memo = (net, tuple(tuple(self.pieces[i].label for i in c) for c in comps))
                    if memo in self.no_bus:
                        continue
                    cand = self.bus_best.get(memo)
                    if cand is None or not self._still_valid(cand):
                        cand = min(self._bus_candidates(net, comps), key=lambda c: c[0], default=None)
                        if cand is None:
                            self.no_bus.add(memo)
                            continue
                        self.bus_best[memo] = cand
                    key = (net not in self.priority, *cand[0])
                    if best_key is None or key < best_key:
                        best, best_key = cand, key
            if best is None:
                break
            _, net, links, need, bus = best
            for cut, style, pos in need:
                self._apply_slide(cut, style, pos, net)
            for lk in links:
                self.links.append(lk)
                self.segments.append(lk.nodes)
                self.claimed.update(lk.nodes)
            if bus is not None:
                b = self._bus_index(bus)
                if b is not None:
                    self._isolate_bus(b, links, net)
                else:  # a slide reshaped the strip: just give the link ends on it to the net
                    for lk in links:
                        for n in lk.nodes:
                            if n.row == bus[0]:
                                self.bus_nodes[n] = net
            self._refresh()
        self.links.sort(key=lambda lk: (lk.row_a, lk.col, lk.row_b, lk.end_col))
        # your placed links keep their references; the rest are numbered after them
        yours = [lk for lk in self.links if lk.origin == "board"]
        start = max((ref_number(r) for r in [*(lk.ref_hint for lk in yours), *self.taken_refs]), default=0)
        for n, lk in enumerate((lk for lk in self.links if lk.origin != "board"), start=start + 1):
            lk.ref_hint = f"W{n}"
        unl = [
            Unlinkable(net, [[self.pieces[i].label for i in ids] for ids in comps], self.max_link_mm)
            for net, comps in self.groups().items()
        ]
        return LinkPlan(self.links, unl, merge_moves(self.moves), needed, self.bus_cuts)

    def _all_groups_initial(self) -> list[list[int]]:
        by_net: dict[str, list[int]] = {}
        for i, p in enumerate(self.pieces):
            if p.net:
                by_net.setdefault(p.net, []).append(i)
        return [ids for ids in by_net.values() if len(ids) > 1]


def _orient(a: Node, b: Node, c: Node) -> int:
    v = (b.col - a.col) * (c.row - a.row) - (b.row - a.row) * (c.col - a.col)
    return (v > 0) - (v < 0)


def _on_segment(a: Node, b: Node, c: Node) -> bool:
    return min(a.col, b.col) <= c.col <= max(a.col, b.col) and min(a.row, b.row) <= c.row <= max(a.row, b.row)


def _segments_meet(p1: Node, p2: Node, q1: Node, q2: Node) -> bool:
    """Do wires ``p1``-``p2`` and ``q1``-``q2`` (hole to hole) cross, touch or overlap?"""
    o1, o2, o3, o4 = _orient(p1, p2, q1), _orient(p1, p2, q2), _orient(q1, q2, p1), _orient(q1, q2, p2)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(p1, p2, q1))
        or (o2 == 0 and _on_segment(p1, p2, q2))
        or (o3 == 0 and _on_segment(q1, q2, p1))
        or (o4 == 0 and _on_segment(q1, q2, p2))
    )


def _point_seg_distance(x: float, y: float, p: tuple[int, int], q: tuple[int, int]) -> float:
    (x0, y0), (x1, y1) = p, q
    dx, dy = x1 - x0, y1 - y0
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / seg2))
    return math.hypot(x0 + t * dx - x, y0 + t * dy - y)


def _point_box_distance(x: float, y: float, box: Box) -> float:
    dx = max(box[0] - x, 0, x - box[2])
    dy = max(box[1] - y, 0, y - box[3])
    return math.hypot(dx, dy)


def _seg_box_distance(p: tuple[int, int], q: tuple[int, int], box: Box) -> float:
    """Shortest distance (nm) between segment ``p``-``q`` and an axis-aligned box (0 if they meet)."""
    (x0, y0), (x1, y1) = p, q
    # Liang-Barsky clip: does the segment enter the box?
    t0, t1 = 0.0, 1.0
    dx, dy = x1 - x0, y1 - y0
    inside = True
    for pk, qk in ((-dx, x0 - box[0]), (dx, box[2] - x0), (-dy, y0 - box[1]), (dy, box[3] - y0)):
        if pk == 0:
            if qk < 0:
                inside = False
                break
            continue
        t = qk / pk
        if pk < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            inside = False
            break
    if inside:
        return 0.0
    best = min(_point_box_distance(x0, y0, box), _point_box_distance(x1, y1, box))
    seg2 = dx * dx + dy * dy
    for cx, cy in ((box[0], box[1]), (box[0], box[3]), (box[2], box[1]), (box[2], box[3])):
        t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((cx - x0) * dx + (cy - y0) * dy) / seg2))
        best = min(best, math.hypot(x0 + t * dx - cx, y0 + t * dy - cy))
    return best


def lock_links(a, specs) -> tuple[list[LinkProposal], list[str], list]:
    """Your links (:class:`stripforge.edits.LinkSpec`) as proposals to keep where they are, a
    warning for every one that can't be used (a pin or a cut in its hole, two nets shorted), and
    those specs."""
    out: list[LinkProposal] = []
    warns: list[str] = []
    rejected: list = []
    pieces = a.split.pieces
    piece_at = {Node(p.row, c): i for i, p in enumerate(pieces) for c in range(p.col_start, p.col_end + 1)}
    strips = {st.row: st for st in a.strips}
    used: dict[Node, str] = {}
    bare_net: dict[int, str] = {}
    for spec in specs:
        name = spec.ref or spec.label
        bad = None
        for n in (spec.n1, spec.n2):
            h = hole_label(n.row, n.col)
            st = strips.get(n.row)
            if st is None or not a.grid.contains(n):
                bad = f"hole {h} is off the stripboard"
            elif n in a.holes.occupants:
                bad = f"hole {h} has a pin in it ({', '.join(o.label for o in a.holes.occupants[n])})"
            elif n.col in st.dead_holes:
                bad = f"hole {h} is a hole cut"
            elif n in a.holes.reserved:
                bad = f"hole {h} is where a slot is filed"
            elif n in used:
                bad = f"hole {h} is already used by {used[n]}"
            if bad:
                break
        if bad is None:
            ends = [piece_at.get(n) for n in (spec.n1, spec.n2)]
            nets = sorted({pieces[i].net or bare_net.get(i) for i in ends if i is not None} - {None})
            if len(nets) > 1:
                bad = f"it would short {nets[0]!r} and {nets[1]!r}"
            elif nets and spec.net and nets[0] != spec.net:
                bad = (
                    f"it is on {spec.net!r} in the schematic but lands on a strip of {nets[0]!r} (a cut "
                    "moved?); move it, or change its net in the schematic"
                )
            elif not nets and not spec.net:
                bad = "neither end is on a strip that carries a net"
        if bad is not None:
            warns.append(f"your link {name} ({spec.source}, {spec.label}) is not used: {bad}")
            rejected.append(spec)
            continue
        net = nets[0] if nets else spec.net
        dx, dy = spec.n2.col - spec.n1.col, spec.n2.row - spec.n1.row
        try:
            fp = spec.footprint or link_footprint_for(dx, dy)
        except ValueError as exc:
            warns.append(f"your link {name} ({spec.source}) is not used: {exc}")
            rejected.append(spec)
            continue
        lk = LinkProposal(
            spec.ref, net, spec.n1.col, spec.n1.row, spec.n2.row, fp,
            col_b=None if dx == 0 else spec.n2.col, origin="board" if spec.ref else "config",
        )  # fmt: skip
        for i in ends:
            if i is not None and not pieces[i].net:
                bare_net[i] = net
        for n in lk.nodes:
            used[n] = name
        for other in out:
            if _segments_meet(*lk.nodes, *other.nodes):
                warns.append(
                    f"your links {name} and {other.ref_hint or other.start + '-' + other.end} cross or touch"
                )
        out.append(lk)
    return out, warns, rejected


def propose(a, locked=(), taken_refs=()) -> LinkPlan:
    """Propose links for every split net of analysis ``a`` (pass 1).

    ``locked`` are your links (:class:`stripforge.edits.LinkSpec`): kept exactly where they are;
    only what they leave unjoined gets new links, numbered after your links and ``taken_refs``
    (the other ``W`` references already on the board).

    ``a`` is edited in place when a cut has to slide or a bus strip is cut off: its strips, cuts,
    pieces and validation are updated, and a warning is added for every hole cut that became a
    knife cut and every bus strip.
    """
    # Greedy planning can box a net in with links it laid for other nets. When nets are left
    # unjoined, plan again from scratch with those nets first (up to a few rounds) and keep the
    # best attempt: fewest unlinkable nets, then fewest links, then fewest extra cuts.
    keep, lock_warns, rejected = lock_links(a, locked)
    start = (a.strips, a.split.cuts, a.split.pieces)
    best = None
    priority: tuple[str, ...] = ()
    cache: dict = {}
    for _ in range(PLAN_ROUNDS):
        a.strips, a.split.cuts, a.split.pieces = copy.deepcopy(start)
        plan = _Planner(
            a, priority=priority, cache=cache, locked=copy.deepcopy(keep), taken_refs=taken_refs
        ).run()
        score = (len(plan.unlinkable), len(plan.links), len(plan.bus_cuts))
        if best is None or score < best[0]:
            best = (score, plan, (a.strips, a.split.cuts, a.split.pieces))
        stuck = {u.net for u in plan.unlinkable}
        if not stuck or stuck <= set(priority):
            break
        priority = tuple(sorted(set(priority) | stuck))
    _, plan, (a.strips, a.split.cuts, a.split.pieces) = best
    # a W you placed that could not be kept: give its ref to the new link on the same holes, else to
    # a new link of its net (pass 2 then moves that W onto the new link's holes)
    free = [spec for spec in rejected if spec.ref]
    for same_holes in (True, False):
        for spec in list(free):
            for lk in plan.links:
                if lk.origin or lk.ref_hint in {s.ref for s in rejected}:
                    continue
                if same_holes and lk.nodes == (spec.n1, spec.n2) and spec.net in (None, lk.net):
                    pass
                elif same_holes or spec.net != lk.net:
                    continue
                lk.ref_hint = spec.ref
                free.remove(spec)
                break
    a.validation = validate(a.split, a.holes, a.strips)
    a.split.warnings += lock_warns
    stuck = {u.net for u in plan.unlinkable}
    for cut in a.split.cuts:
        if cut.user and cut.reason[0] == cut.reason[1] and cut.reason[0] in stuck:
            a.split.warnings.append(
                f"{cut.id}: your cut {cut.where} ({cut.user}) splits {cut.reason[0]!r}, which can't be "
                "joined again; move or remove it"
            )
    for m in plan.cut_moves:
        a.split.warnings.append(m.text)
    buses: dict[str, list[str]] = {}
    for lk in plan.links:
        if lk.bus:
            buses.setdefault(f"{lk.net}|{lk.bus}", []).append(lk.ref_hint)
    for key, refs in buses.items():
        net, row = key.split("|")
        a.split.warnings.append(
            f"bus strip: {' and '.join(refs)} meet on bare strip {row}, which now carries {net!r}"
        )
    return plan


# --- output ----------------------------------------------------------------------------------

CSV_FIELDS = ["ref", "net", "from", "to", "pitches", "length_mm", "footprint", "kind", "rotation", "bus"]


def to_csv(plan: LinkPlan) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for lk in plan.links:
        w.writerow(lk.to_dict())
    return buf.getvalue()


def to_json(plan: LinkPlan, board: str | None = None) -> str:
    data = {"board": board, **plan.to_dict()}
    return json.dumps(data, indent=2) + "\n"


def refs_to_add(plan: LinkPlan) -> str:
    """``W1..W34`` (or ``W35, W37``): the links still to add to the schematic (yours are there)."""
    refs = [lk.ref_hint for lk in plan.links if lk.origin != "board"]
    nums = [ref_number(r) for r in refs]
    if refs and nums == list(range(nums[0], nums[0] + len(nums))):
        return refs[0] if len(refs) == 1 else f"{refs[0]}..{refs[-1]}"
    return ", ".join(refs)


def format_text(plan: LinkPlan) -> str:
    """The link report plus step-by-step instructions for adding the links to the schematic."""
    out = [f"Links: {len(plan.links)} proposed for {plan.needed} needed"]
    if plan.joins != len(plan.links):
        out[0] += f" ({plan.joins} joins; a bus-strip join takes two links)"
    for lk in plan.links:
        how = ""
        if lk.kind != "vertical":
            how = f"  ({lk.kind}, rotated {lk.rotation:g} deg)"
        if lk.bus:
            how += f"  (to bus strip {lk.bus})"
        if lk.origin == "board":
            how += "  (yours, kept)"
        elif lk.origin == "config":
            how += "  ([manual] links)"
        out.append(f"  {lk.ref_hint:<4} {lk.start:>4} -> {lk.end:<4} {lk.footprint:<24} {lk.net}{how}")
    for cut in plan.bus_cuts:
        out.append(f"  note: {cut.id}: {cut.style} cut at {cut.label} isolates a bus strip")
    for m in plan.cut_moves:
        out.append(f"  note: {m.text}")
    if plan.unlinkable:
        out.append(f"Unlinkable nets: {len(plan.unlinkable)}")
        out += [f"  ERROR: {u.text}" for u in plan.unlinkable]
    to_add = [lk for lk in plan.links if lk.origin != "board"]
    if to_add:
        out += [
            "",
            "To add the links to the schematic (pass 2):",
            "  1. In Eeschema, place one 2-pin jumper symbol per link, e.g. Jumper:Jumper_2_Bridged",
            "     (or Device:R with value 0R). Set its Reference and Footprint as listed below.",
            "  2. Connect BOTH pins of each link to the listed net (wire or net label on each pin).",
            "  3. Press F8 (Update PCB from Schematic) on the placement board, then run",
            "     'stripforge build' again: it places each W footprint on its two holes.",
            "",
            "  Ref  Footprint                Net",
        ]
        out += [f"  {lk.ref_hint:<4} {lk.footprint:<24} {lk.net}" for lk in to_add]
    return "\n".join(out) + "\n"
