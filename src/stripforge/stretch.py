# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Lead-stretch suggestions: links a longer lead on a two-pin part could replace (report only).

After the link plan, each join (one link, or the two links of a bus strip) is tried against every
leaded two-pin part (resistor, capacitor, diode, LED, inductor, fuse) with a pin on that join's
net. The part keeps its other pin where it is; the pin on the net moves to a free hole on the far
side of the join, so the join is no longer needed. A move is suggested only when

* the whole net is still connected by the remaining links with the pin in its new hole (the
  pin's old piece must not need it: nothing else on that side of the join has a pad of the net);
* the new hole is free (no pad, not a hole cut, not a slot's filing hole, not another link's end)
  and sits on a piece that already carries the net, so no new cut is needed and nothing shorts;
* the new lead span is sane: at most ``max_pitches`` longer than now for an axial part (resistor,
  DO-41 diode, laid flat), ``radial_max_pitches`` for a radial one (a legged cap, LED, fuse), and
  no shorter than an axial body allows;
* the new hole is not right beside a pin of a ``knife_cuts`` part (its big pad overhangs that
  hole);
* the part's new line (from its fixed pin to the new hole) crosses no remaining link, passes over
  no hole with a lead in it, and runs under no part courtyard it did not already sit under
  (``allow_under_parts = true`` lifts that last rule; such moves are then ranked last).

Nothing on the board is changed: the report says which pin to move to which hole, and which link
(``Wn``) then need not go into the schematic. Suggestions are made greedily, one per join, and
checked together (no two share a part or a hole, their leads don't cross, and the net stays
connected with all of them taken). A net the planner could not join is tried the same way.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .grid import Node
from .links import (
    LEAD_CLEAR_NM,
    LINK_COURTYARD_NM,
    _overlap,
    _point_seg_distance,
    _seg_box_distance,
    _segments_meet,
    courtyard_box,
)
from .splitter import knife_keep_clear

LEADED_PREFIXES = ("R", "C", "D", "L", "F", "FB")
DEFAULT_MAX_PITCHES = 6.0
DEFAULT_RADIAL_MAX_PITCHES = 2.0
_BODY_RE = re.compile(r"_L(\d+(?:\.\d+)?)mm")


@dataclass
class Stretch:
    links: tuple[str, ...]  # the W refs it replaces ("" for an unlinkable net)
    net: str
    ref: str
    pin: str
    fixed: Node  # the part's other pin (stays)
    old: Node
    new: Node
    span_old: float  # pitches
    span_new: float
    axial: bool

    @property
    def extra(self) -> float:
        return self.span_new - self.span_old

    def text(self) -> str:
        what = f"replaces {' + '.join(self.links)}" if self.links else f"joins {self.net!r}"
        return (
            f"{what}: move {self.ref} pin {self.pin} from {self.old.label} to {self.new.label} "
            f"({self.ref}.{'1' if self.pin != '1' else '2'} stays at {self.fixed.label}; span "
            f"{self.span_old:.3g} -> {self.span_new:.3g} pitches, {self.extra:+.3g})"
        )

    def to_dict(self) -> dict:
        return {
            "links": list(self.links),
            "net": self.net,
            "ref": self.ref,
            "pin": self.pin,
            "fixed": self.fixed.label,
            "from": self.old.label,
            "to": self.new.label,
            "span_old": round(self.span_old, 3),
            "span_new": round(self.span_new, 3),
            "kind": "axial" if self.axial else "radial",
        }


def _prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z_#]+", ref)
    return m.group(0).upper() if m else ""


def _axial(lib_id: str) -> bool:
    name = lib_id.split(":")[-1]
    return "Axial" in name or "_DO-" in name or name.startswith("DO-") or "_Horizontal" in name


def _min_span(lib_id: str, axial: bool) -> float:
    if not axial:
        return 1.0
    m = _BODY_RE.search(lib_id)
    body = float(m.group(1)) if m else 5.0
    return math.ceil((body + 2.0) / 2.54)  # the body plus a bend at each end, laid flat


def _dist(a: Node, b: Node) -> float:
    return math.hypot(a.row - b.row, a.col - b.col)


class _State:
    def __init__(self, a, plan) -> None:
        self.a = a
        self.pieces = a.split.pieces
        self.piece_at: dict[Node, int] = {}
        for i, p in enumerate(self.pieces):
            for c in range(p.col_start, p.col_end + 1):
                self.piece_at[Node(p.row, c)] = i
        self.strips = {s.row: s for s in a.strips}
        self.links = list(plan.links)
        self.removed: set[str] = set()
        self.moved: dict[tuple[str, str], Node] = {}  # (ref, pin) -> new hole
        self.vacated: set[Node] = set()
        self.taken: set[Node] = set()  # new holes used by earlier suggestions
        self.leads: list[tuple[Node, Node]] = []  # earlier suggestions' part lines
        near, _ = knife_keep_clear(a.holes, getattr(a.config, "knife_cuts", ()))
        self.overhung = {Node(r, c) for r, c in near}  # beside a big knife_cuts pad
        self.pads: dict[str, list[tuple[str, str, Node]]] = {}  # net -> (ref, pin, hole)
        for node, occ in a.holes.occupants.items():
            for o in occ:
                if o.net:
                    self.pads.setdefault(o.net, []).append((o.ref, o.pad, node))

    def live_links(self):
        return [lk for lk in self.links if lk.ref_hint not in self.removed]

    def free(self, node: Node) -> bool:
        strip = self.strips.get(node.row)
        if strip is None or not 0 <= node.col < strip.cols or node.col in strip.dead_holes:
            return False
        if node in self.taken or node in self.overhung:
            return False
        if node in self.a.holes.reserved:
            return False
        if node in self.a.holes.occupants and node not in self.vacated:
            return False
        return all(node not in lk.nodes for lk in self.live_links())

    def connected(self, net: str, moved: dict[tuple[str, str], Node], removed: set[str]) -> bool:
        parent: dict[int, int] = {}

        def find(i: int) -> int:
            parent.setdefault(i, i)
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for lk in self.links:
            if lk.net != net or lk.ref_hint in removed:
                continue
            i, j = (self.piece_at.get(n) for n in lk.nodes)
            if i is not None and j is not None:
                parent[find(i)] = find(j)
        roots = set()
        for ref, pin, node in self.pads.get(net, []):
            node = moved.get((ref, pin), node)
            i = self.piece_at.get(node)
            if i is None:
                return False
            roots.add(find(i))
        return len(roots) <= 1


def _joins(plan) -> list[list]:
    """The plan's links grouped into joins: a bus strip's links together, every other link alone."""
    out: list[list] = []
    bus: dict[tuple[str, str], list] = {}
    for lk in plan.links:
        if lk.bus:
            key = (lk.net, lk.bus)
            if key not in bus:
                bus[key] = []
                out.append(bus[key])
            bus[key].append(lk)
        else:
            out.append([lk])
    return out


def suggest(a, plan, cfg=None) -> list[Stretch]:
    """Lead-stretch suggestions for the plan's links and unlinkable nets (see the module doc)."""
    cfg = cfg if cfg is not None else a.config
    opts = getattr(cfg, "stretch", None) or {}
    if not opts.get("enabled", True):
        return []
    max_ax = float(opts.get("max_pitches", DEFAULT_MAX_PITCHES))
    max_rad = float(opts.get("radial_max_pitches", DEFAULT_RADIAL_MAX_PITCHES))
    under_ok = bool(opts.get("allow_under_parts", False))
    skip = set(opts.get("skip", ())) | set(cfg.slotted) | set(cfg.offboard_refs)
    g = a.grid
    st = _State(a, plan)

    # the leaded two-pin parts, with both pins in holes
    where: dict[str, dict[str, Node]] = {}
    for node, occ in a.holes.occupants.items():
        for o in occ:
            where.setdefault(o.ref, {})[o.pad] = node
    parts = []
    for fp in a.board.footprints:
        pads = [p for p in fp.pads if p.kind == "thru_hole"]
        if fp.ref in skip or _prefix(fp.ref) not in LEADED_PREFIXES or len(pads) != 2 or len(fp.pads) != 2:
            continue
        holes = where.get(fp.ref, {})
        if len(holes) != 2:
            continue
        ax = _axial(fp.lib_id)
        parts.append((fp, holes, ax))
    boxes = {fp.ref: box for fp in a.board.footprints if (box := courtyard_box(fp)) is not None}
    pins = set(a.holes.occupants)

    def under(n1: Node, n2: Node, own: str) -> set[str]:
        p, q = g.hole_xy(n1), g.hole_xy(n2)
        x0, x1 = sorted((p[0], q[0]))
        y0, y1 = sorted((p[1], q[1]))
        m = LINK_COURTYARD_NM
        return {
            ref
            for ref, box in boxes.items()
            if ref != own
            and _overlap((x0 - m, y0 - m, x1 + m, y1 + m), box)
            and _seg_box_distance(p, q, box) < m
        }

    def over_lead(n1: Node, n2: Node, ignore: set[Node]) -> bool:
        p, q = g.hole_xy(n1), g.hole_xy(n2)
        ends = {n for lk in st.live_links() for n in lk.nodes}
        for n in (pins - st.vacated) | ends | st.taken:
            if n in ignore or n == n1 or n == n2:
                continue
            x, y = g.hole_xy(n)
            if _point_seg_distance(x, y, p, q) < LEAD_CLEAR_NM:
                return True
        return False

    def best_move(net: str, removed: set[str]) -> Stretch | None:
        best, best_key = None, None
        for fp, holes, ax in parts:
            if any(k[0] == fp.ref for k in st.moved):
                continue
            for pin, old in holes.items():
                if a.holes.nets.get(old) != net:
                    continue
                other = next(p for p in holes if p != pin)
                fixed = holes[other]
                span_old = _dist(old, fixed)
                limit = span_old + (max_ax if ax else max_rad)
                lo = _min_span(fp.lib_id, ax)
                before = under(fixed, old, fp.ref)
                r = int(math.ceil(limit))
                for dr in range(-r, r + 1):
                    for dc in range(-r, r + 1):
                        new = Node(fixed.row + dr, fixed.col + dc)
                        if new == old or new == fixed:
                            continue
                        span = _dist(new, fixed)
                        if span > limit + 1e-9 or span < lo - 1e-9:
                            continue
                        i = st.piece_at.get(new)
                        if i is None or st.pieces[i].net != net or not st.free(new):
                            continue
                        moved = {**st.moved, (fp.ref, pin): new}
                        if not st.connected(net, moved, st.removed | removed):
                            continue
                        segs = [lk.nodes for lk in st.live_links() if lk.ref_hint not in removed] + st.leads
                        if any(_segments_meet(fixed, new, m1, m2) for m1, m2 in segs):
                            continue
                        if over_lead(fixed, new, {old}):
                            continue
                        under_extra = len(under(fixed, new, fp.ref) - before)
                        if under_extra and not under_ok:
                            continue
                        key = (span - span_old, under_extra, span, new.row, new.col, fp.ref)
                        if best_key is not None and key >= best_key:
                            continue
                        best_key = key
                        best = Stretch(
                            tuple(sorted(removed, key=_refkey)),
                            net,
                            fp.ref,
                            pin,
                            fixed,
                            old,
                            new,
                            span_old,
                            span,
                            ax,
                        )
        return best

    out: list[Stretch] = []
    for join in _joins(plan):
        net = join[0].net
        refs = {lk.ref_hint for lk in join}
        s = best_move(net, refs)
        if s is None:
            continue
        st.removed |= refs
        st.moved[(s.ref, s.pin)] = s.new
        st.vacated.add(s.old)
        st.taken.add(s.new)
        st.leads.append((s.fixed, s.new))
        out.append(s)
    for u in plan.unlinkable:
        s = best_move(u.net, set())
        if s is not None:
            st.moved[(s.ref, s.pin)] = s.new
            st.vacated.add(s.old)
            st.taken.add(s.new)
            st.leads.append((s.fixed, s.new))
            out.append(s)
    return out


def _refkey(ref: str) -> tuple[str, int]:
    m = re.match(r"(\D*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0))


def format_text(stretches: list[Stretch]) -> str:
    if not stretches:
        return "Lead stretches: none (no link can be replaced by a longer lead on a two-pin part)\n"
    gone = sum(len(s.links) for s in stretches)
    out = [
        f"Lead stretches: {len(stretches)} suggestion(s), {gone} link(s) fewer (report only; nothing moved)"
    ]
    out += [f"  {s.text()}" for s in stretches]
    out.append(
        "  To take one: change the part's footprint (pitch/rotation) so the pin lands on the new hole,"
    )
    out.append("  leave those W links out of the schematic, and build again.")
    return "\n".join(out) + "\n"
