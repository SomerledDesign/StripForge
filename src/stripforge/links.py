# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Wire-link proposals for nets split across strip pieces (Sketch.md §4.4), pass 1 of the link flow.

A link is a zero-ohm ``W`` jumper from Mildrew's family (``StripForge:Link_P2.54`` …
``StripForge:Link_P81.28``, 1–32 pitches). It runs straight down one column, across strips, from
a free hole on one piece of a net to a free hole on another piece of the same net. A *free* hole
has no pad in it, is not a hole cut, is not the hole a slotted pad is filed toward, and is not
already used by another link.

The proposal is a greedy minimum spanning forest per net (Kruskal, shortest links first), so a
net split into *n* pieces gets exactly *n − 1* links when it can be joined at all. Candidates are
ranked by: no overlap with another link in the same column, then how many part courtyards the
link's courtyard would cross (a wire under a part body), then cut slides needed (below), then
length, then how many part leads the wire passes over, then position, so the result is
deterministic.

When no column works, the planner may **slide a cut** along its gap (the cut between two pads of
different nets can sit anywhere between them) so that a piece reaches a column where a link can
land. A hole cut is moved to another free hole where possible, otherwise it becomes a knife cut;
every slide is reported. Nets that still can't be joined are reported as unlinkable, with their
pieces, so the placement can be changed.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field

from .board import rotate_nm
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


def link_footprint(pitches: int) -> str:
    """``StripForge:Link_P7.62`` for a 3-pitch link."""
    if not LINK_MIN_PITCHES <= pitches <= LINK_MAX_PITCHES:
        raise ValueError(f"no link footprint for {pitches} pitches (1..{LINK_MAX_PITCHES})")
    return f"{LIB}:Link_P{pitches * PITCH_MM:.2f}"


@dataclass
class LinkProposal:
    ref_hint: str  # "W1"...
    net: str
    col: int
    row_a: int  # upper strip (pad 1 of the link footprint)
    row_b: int  # lower strip (pad 2)
    footprint: str  # e.g. "StripForge:Link_P10.16"

    @property
    def pitches(self) -> int:
        return self.row_b - self.row_a

    @property
    def length_mm(self) -> float:
        return round(self.pitches * PITCH_MM, 2)

    @property
    def start(self) -> str:
        return hole_label(self.row_a, self.col)

    @property
    def end(self) -> str:
        return hole_label(self.row_b, self.col)

    @property
    def nodes(self) -> tuple[Node, Node]:
        return Node(self.row_a, self.col), Node(self.row_b, self.col)

    def to_dict(self) -> dict:
        return {
            "ref": self.ref_hint,
            "net": self.net,
            "from": self.start,
            "to": self.end,
            "col": self.col,
            "row_a": self.row_a,
            "row_b": self.row_b,
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
        return f"{self.cut_id}: moved from {self.before} to {self.after} so a {self.net!r} link can land"


@dataclass
class Unlinkable:
    net: str
    groups: list[list[str]]  # piece labels, one list per group that could not be joined

    @property
    def text(self) -> str:
        groups = " | ".join(", ".join(g) for g in self.groups)
        return (
            f"net {self.net!r} can't be fully linked: {len(self.groups)} groups of pieces [{groups}] have "
            f"no column where both have a free hole within {LINK_MAX_PITCHES} strips; move or rotate a "
            "part so the pieces overlap in a column with free holes"
        )


@dataclass
class LinkPlan:
    links: list[LinkProposal] = field(default_factory=list)
    unlinkable: list[Unlinkable] = field(default_factory=list)
    cut_moves: list[CutMove] = field(default_factory=list)
    needed: int = 0  # sum over split nets of (pieces - 1)

    @property
    def ok(self) -> bool:
        return not self.unlinkable

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
    def __init__(self, a, skip_refs: set[str] = frozenset()) -> None:
        self.a = a
        self.courtyards = [
            box
            for fp in a.board.footprints
            if fp.ref not in skip_refs and fp.ref not in a.config.offboard_refs
            if (box := courtyard_box(fp)) is not None
        ]
        self.strips: dict[int, Strip] = {s.row: s for s in a.strips}
        self.claimed: set[Node] = set()
        self.links: list[LinkProposal] = []
        self.moves: list[CutMove] = []
        self._refresh()

    # pieces and connectivity
    def _refresh(self) -> None:
        self.a.split.pieces = assign_nets(self.a.strips, self.a.holes)
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
    def _overlaps(self, col: int, r1: int, r2: int) -> bool:
        return any(lk.col == col and lk.row_a < r2 and r1 < lk.row_b for lk in self.links)

    def _crossings(self, col: int, r1: int, r2: int) -> int:
        """How many part courtyards the link's courtyard would overlap."""
        g = self.a.grid
        x, y1 = g.hole_xy(Node(r1, col))
        _, y2 = g.hole_xy(Node(r2, col))
        m = LINK_COURTYARD_NM
        box = (x - m, y1 - m, x + m, y2 + m)
        return sum(1 for c in self.courtyards if _overlap(box, c))

    def _over_leads(self, col: int, r1: int, r2: int) -> int:
        return sum(1 for r in range(r1 + 1, r2) if Node(r, col) in self.a.holes.occupants)

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
                return cut, ca, cb
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
                    elif (p.row, x - 1) not in slots and not row_claims & set(range(x, p.col_start)):
                        out.setdefault(x, (cut, "knife", x - 1))
            else:
                for x in range(p.col_end + 1, cb):
                    opts = [] if knife_only else [h for h in range(x + 1, cb) if self._cuttable(p.row, h)]
                    hole = next((h for h in opts if not row_claims & set(range(p.col_end + 1, h + 1))), None)
                    if hole is not None:
                        out.setdefault(x, (cut, "hole", hole))
                    elif (p.row, x) not in slots and not row_claims & set(range(p.col_end + 1, x + 1)):
                        out.setdefault(x, (cut, "knife", x))
        return out

    def _cuttable(self, row: int, col: int) -> bool:
        node = Node(row, col)
        return self.a.holes.is_free(node) and node not in self.claimed

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
                if k > LINK_MAX_PITCHES:
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
                    knives = sum(1 for _, style, _ in need if style == "knife")
                    cost = (
                        self._overlaps(col, p.row, q.row),
                        self._crossings(col, p.row, q.row),
                        len(need),
                        knives,
                        k,
                        self._over_leads(col, p.row, q.row),
                        p.row,
                        col,
                        net,
                    )
                    yield cost, net, col, p.row, q.row, need

    def run(self) -> LinkPlan:
        needed = sum(len(ids) - 1 for ids in self._all_groups_initial())
        while True:
            groups = self.groups()
            if not groups:
                break
            best = None
            for net, comps in groups.items():
                for cand in self._candidates(net, comps, allow_slides=True):
                    if best is None or cand[0] < best[0]:
                        best = cand
            if best is None:
                break
            _, net, col, r1, r2, need = best
            for cut, style, pos in need:
                self._apply_slide(cut, style, pos, net)
            lk = LinkProposal("", net, col, r1, r2, link_footprint(r2 - r1))
            self.links.append(lk)
            self.claimed.update(lk.nodes)
        self.links.sort(key=lambda lk: (lk.row_a, lk.col))
        for n, lk in enumerate(self.links, start=1):
            lk.ref_hint = f"W{n}"
        unl = [
            Unlinkable(net, [[self.pieces[i].label for i in ids] for ids in comps])
            for net, comps in self.groups().items()
        ]
        return LinkPlan(self.links, unl, self.moves, needed)

    def _all_groups_initial(self) -> list[list[int]]:
        by_net: dict[str, list[int]] = {}
        for i, p in enumerate(self.pieces):
            if p.net:
                by_net.setdefault(p.net, []).append(i)
        return [ids for ids in by_net.values() if len(ids) > 1]


def propose(a) -> LinkPlan:
    """Propose links for every split net of analysis ``a`` (pass 1).

    ``a`` is edited in place when a cut has to slide: its strips, cuts, pieces and validation are
    updated, and a warning is added for every hole cut that became a knife cut.
    """
    planner = _Planner(a)
    plan = planner.run()
    a.validation = validate(a.split, a.holes, a.strips)
    for m in plan.cut_moves:
        a.split.warnings.append(m.text)
    return plan


# --- output ----------------------------------------------------------------------------------

CSV_FIELDS = ["ref", "net", "from", "to", "pitches", "length_mm", "footprint"]


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


def format_text(plan: LinkPlan) -> str:
    """The link report plus step-by-step instructions for adding the links to the schematic."""
    out = [f"Links: {len(plan.links)} proposed for {plan.needed} needed"]
    for lk in plan.links:
        out.append(f"  {lk.ref_hint:<4} {lk.start:>4} -> {lk.end:<4} {lk.footprint:<24} {lk.net}")
    for m in plan.cut_moves:
        out.append(f"  note: {m.text}")
    if plan.unlinkable:
        out.append(f"Unlinkable nets: {len(plan.unlinkable)}")
        out += [f"  ERROR: {u.text}" for u in plan.unlinkable]
    if plan.links:
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
        out += [f"  {lk.ref_hint:<4} {lk.footprint:<24} {lk.net}" for lk in plan.links]
    return "\n".join(out) + "\n"
