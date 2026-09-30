# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""`stripforge sheet`: a printable build sheet for a built board (M3, Sketch.md §4.9).

The sheet is one self-contained HTML file (inline CSS and SVG, no scripts, no network), laid out
for US Letter, with a PDF next to it when a Chrome/Chromium is found. It is drawn from the same
model ``stripforge build`` writes (:func:`stripforge.writer.prepare`), and checked against the
board: if the board's strips or cut markers differ from what StripForge would build now, the
sheet says so.

Contents, in build order:

1. header: project, board size, date, StripForge version, and a summary;
2. the **copper side, mirrored** (the board flipped left to right, as you hold it to cut): strips,
   cuts (hole cuts as a red ring and cross, knife cuts as a red bar), slot jobs, solder joints and
   link ends, strip letters and hole numbers on both edges, an A1 corner mark and a 10-hole ruler;
3. the **component side**: part outlines (F.Fab), refs, values, pads with pin-1 marks and the wire
   links drawn as wires;
4. checklists with a box per item: cuts grouped by strip, slot jobs, wire links, then the parts
   (links, resistors, diodes, ... switches last) with the hole of every pin;
5. the net check table (every hole each net should reach, for a continuity meter);
6. warnings (knife cuts, courtyard overlaps, unlinkable nets, anything else).

Views wider than :data:`MAX_COLS` holes or taller than :data:`MAX_ROWS` strips are split over
several pages.
"""

from __future__ import annotations

import csv
import datetime as _dt
import html
import io
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from . import links as links_mod
from .board import Board, Footprint, load_board, rotate_nm
from .config import BoardConfig
from .grid import Node, bend_text, hole_label, row_label
from .sexpr import atom, find, find_all, head, mm_to_nm
from .splitter import Cut
from .writer import Prepared, prepare

MAX_COLS = 60  # holes per page before a view is split
MAX_ROWS = 36  # strips per page before a view is split
PAGE_W_MM = 250.0  # drawing area on Letter landscape (279.4 x 215.9 mm, 10 mm margins, less header)
PAGE_H_MM = 160.0
MAX_SCALE = 2.0
LABEL_PITCHES = 2.0  # margin round the holes for the letters and numbers, in pitches

COPPER = "#d8a25e"
CUT_RED = "#d11a1a"
LINK_BLUE = "#1f5fbf"
SLOT_ORANGE = "#e07b00"

# --- model ------------------------------------------------------------------------------------


@dataclass
class LinkRow:
    link: links_mod.LinkProposal
    status: str  # placed | on-board | to-add
    detail: str = ""


@dataclass
class PartRow:
    ref: str
    value: str
    footprint: str
    group: int
    group_name: str
    pins: list[tuple[str, str, str]]  # (pad number, hole label, net)
    slotted: bool = False
    bend: str = ""  # "bend legs up to 0.152 mm (6.0 mil) ..." for a [bend] part (Beckham tolerance)


@dataclass
class NetRow:
    net: str
    holes: list[tuple[str, str]]  # (hole label, what is there: "R3.1" or "W4 end")
    pieces: int
    joined: bool  # all pieces joined by the (proposed) links


@dataclass
class SheetModel:
    board_path: str
    project: str
    prep: Prepared
    links: list[LinkRow]
    parts: list[PartRow]
    nets: list[NetRow]
    single_pin_nets: int
    warnings: list[str] = field(default_factory=list)
    knife_cuts: list[Cut] = field(default_factory=list)
    overlaps: list[str] = field(default_factory=list)
    unlinkable: list[str] = field(default_factory=list)
    built: str = "built"  # built | not-built | differs
    date: str = ""

    @property
    def a(self):
        return self.prep.analysis

    @property
    def cuts(self) -> list[Cut]:
        return sorted(self.a.split.cuts, key=lambda c: (c.row, c.col))


def _value(fp: Footprint) -> str:
    for prop in find_all(fp.node, "property"):
        if atom(prop, 1) == "Value":
            return atom(prop, 2, "") or ""
    for txt in find_all(fp.node, "fp_text"):
        if atom(txt, 1) == "value":
            return atom(txt, 2, "") or ""
    return ""


def _natural(s: str) -> tuple:
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s))


GROUPS = [
    "Wire links",
    "Resistors",
    "Diodes and LEDs",
    "Transistors",
    "ICs and sockets",
    "Capacitors",
    "Headers and connectors",
    "Switches",
    "Other parts",
]


def part_group(ref: str, lib_id: str) -> int:
    """Build-order group (index into :data:`GROUPS`): low-profile parts first, bulky ones last."""
    prefix = re.match(r"[A-Za-z]*", ref).group(0).upper()
    lib = lib_id.upper()
    if prefix == "W" or ":LINK_" in lib:
        return 0
    if prefix in ("R", "RN", "RV"):
        return 1
    if prefix in ("D", "LED"):
        return 2
    if prefix == "Q":
        return 3
    if prefix in ("U", "IC") or "SOCKET" in lib or "DIP" in lib:
        return 4
    if prefix == "C":
        return 5
    if prefix in ("J", "P", "CN", "CONN", "X", "TP"):
        return 6
    if prefix in ("SW", "S", "K"):
        return 7
    return 8


def _board_title(board: Board, path: Path) -> str:
    tb = find(board.doc.root, "title_block") if board.doc is not None else None
    title = atom(find(tb, "title"), 1) if tb is not None and find(tb, "title") is not None else None
    return title or path.stem


def _check_against_board(prep: Prepared) -> tuple[str, list[str]]:
    """Compare the board's StripForge copper and cut markers with the model."""
    a = prep.analysis
    want_tracks = sum(p.col_end - p.col_start for p in a.split.pieces)
    want_cuts = {}
    for cut in a.split.cuts:
        x, y = a.grid.hole_xy(Node(cut.row, int(cut.col)))
        if cut.style == "knife":
            x += a.grid.pitch_nm // 2
        name = "CUT_Hole" if cut.style == "hole" else "CUT_Knife"
        want_cuts[f"CUT{cut.id[1:]}"] = (name, x, y)
    have_cuts = {fp.ref: (fp.lib_id.split(":")[-1], fp.x_nm, fp.y_nm) for fp in prep.old_cuts}
    if not prep.own_tracks and not prep.old_cuts:
        return "not-built", [
            "This board has no StripForge strips or cut markers yet (it looks like the placement "
            "board). The sheet shows what 'stripforge build' would make; build first and print the "
            "sheet of the built board."
        ]
    out = []
    if len(prep.own_tracks) != want_tracks:
        out.append(
            f"The board has {len(prep.own_tracks)} StripForge strip track(s) but the model has "
            f"{want_tracks}: the board changed since it was built (or another config was used). "
            "Build again before cutting."
        )
    if have_cuts != want_cuts:
        missing = sorted(set(want_cuts) - set(have_cuts), key=_natural)
        extra = sorted(set(have_cuts) - set(want_cuts), key=_natural)
        moved = sorted(
            (r for r in set(want_cuts) & set(have_cuts) if want_cuts[r] != have_cuts[r]), key=_natural
        )
        bits = [
            f"{label}: {', '.join(refs)}"
            for label, refs in (("missing", missing), ("extra", extra), ("moved or changed", moved))
            if refs
        ]
        out.append(
            "The board's cut markers differ from the model ("
            + "; ".join(bits)
            + "). Build again before cutting."
        )
    if prep.foreign:
        out.append(
            f"The board has {len(prep.foreign)} track/via item(s) StripForge did not write "
            f"({', '.join(sorted(set(prep.foreign)))}); they are not on this sheet."
        )
    return ("differs" if out else "built"), out


def _link_rows(prep: Prepared) -> tuple[list[LinkRow], list[str]]:
    a = prep.analysis
    by_ref = {fp.ref: fp for fp in prep.link_fps}
    rows, warnings = [], []
    for lk in prep.plan.links:
        fp = by_ref.get(lk.ref_hint)
        if fp is None:
            rows.append(LinkRow(lk, "to-add", "not in the schematic yet (pass 1): add it, F8, build again"))
            continue
        x, y = a.grid.hole_xy(Node(lk.row_a, lk.col))
        turned = abs((fp.angle - lk.rotation + 180.0) % 360.0 - 180.0) < 0.01
        if (
            (fp.x_nm, fp.y_nm) == (x, y)
            and turned
            and fp.lib_id.split(":")[-1] == lk.footprint.split(":")[-1]
        ):
            rows.append(LinkRow(lk, "placed"))
        else:
            rows.append(LinkRow(lk, "on-board", "on the board but not on its holes: build again"))
    planned = {lk.ref_hint for lk in prep.plan.links}
    for fp in prep.link_fps:
        if fp.ref not in planned:
            warnings.append(
                f"{fp.ref} is on the board but not in the link proposal; remove it from the schematic"
            )
    return rows, warnings


def _part_rows(prep: Prepared) -> list[PartRow]:
    a = prep.analysis
    fps = {fp.ref: fp for fp in a.board.footprints}
    rows = []
    for s in a.snapped:
        fp = fps.get(s.ref)
        if fp is None or not s.pads:
            continue
        pins = sorted(
            ((p.number, p.node.label if p.node else p.where, p.net or "") for p in s.pads),
            key=lambda t: _natural(t[0]),
        )
        g = part_group(s.ref, fp.lib_id)
        bend = ""
        if s.bends:
            worst = max(s.bends, key=lambda b: b.bend_nm)
            bend = f"bend legs up to {bend_text(worst.bend_nm)} {worst.direction} to fit the holes"
        row = PartRow(s.ref, _value(fp), fp.lib_id.split(":")[-1], g, GROUPS[g], pins, slotted=bool(s.slots))
        row.bend = bend
        rows.append(row)
    return sorted(rows, key=lambda r: (r.group, _natural(r.ref)))


def _net_rows(prep: Prepared) -> tuple[list[NetRow], int]:
    a = prep.analysis
    holes: dict[str, dict[str, list[str]]] = {}
    for node, occ in a.holes.occupants.items():
        for o in occ:
            if o.net:
                holes.setdefault(o.net, {}).setdefault(node.label, []).append(o.label)
    for lk in prep.plan.links:
        for end, node in zip(("end 1", "end 2"), lk.nodes):
            holes.setdefault(lk.net, {}).setdefault(node.label, []).append(f"{lk.ref_hint} {end}")
    pieces = a.split.pieces_per_net
    unjoined = {u.net for u in prep.plan.unlinkable}
    rows, singles = [], 0
    for net in sorted(holes, key=lambda n: (n.startswith("unconnected-"), _natural(n))):
        hs = holes[net]
        if len(hs) < 2 and net not in unjoined:
            singles += 1
            continue
        items = sorted(hs.items(), key=lambda kv: _hole_key(kv[0]))
        rows.append(
            NetRow(net, [(h, ", ".join(w)) for h, w in items], pieces.get(net, 1), net not in unjoined)
        )
    return rows, singles


def _link_how(lk) -> str:
    """How a link runs, for the links table (nothing for a plain straight-down link)."""
    notes = []
    if lk.kind == "horizontal":
        notes.append("along the strip")
    elif lk.kind == "diagonal":
        notes.append(f"diagonal, {abs(lk.dx)} across and {lk.dy} down")
    if lk.bus:
        notes.append(f"to bus strip {lk.bus}")
    return f"<br><small>{_e('; '.join(notes))}</small>" if notes else ""


def _link_span(v, lk) -> tuple[float, float, float, float] | None:
    """A link's wire clipped to view ``v`` (its rows and columns), in view mm, or None if outside."""
    (r1, c1), (r2, c2) = (lk.row_a, lk.col), (lk.row_b, lk.end_col)
    t0, t1 = 0.0, 1.0
    for a, b, lo, hi in ((c1, c2, v.c0, v.c1), (r1, r2, v.r0, v.r1)):
        d = b - a
        if d == 0:
            if not lo <= a <= hi:
                return None
            continue
        ta, tb = sorted(((lo - a) / d, (hi - a) / d))
        t0, t1 = max(t0, ta), min(t1, tb)
        if t0 > t1:
            return None
    g = v.grid

    def at(t: float) -> tuple[float, float]:
        col, row = c1 + (c2 - c1) * t, r1 + (r2 - r1) * t
        return v.x(g.origin_x_nm + col * g.pitch_nm), v.y(g.origin_y_nm + row * g.pitch_nm)

    (xa, ya), (xb, yb) = at(t0), at(t1)
    return xa, ya, xb, yb


def _hole_key(label: str) -> tuple[int, int]:
    from .grid import parse_hole

    try:
        n = parse_hole(label)
        return n.row, n.col
    except ValueError:
        return (10**6, 0)


def _overlaps(prep: Prepared) -> list[str]:
    a = prep.analysis
    skip = set(a.config.offboard_refs)
    boxes = [
        (fp.ref, box)
        for fp in a.board.footprints
        if fp.ref not in skip and (box := links_mod.courtyard_box(fp)) is not None
    ]
    out = []
    for i, (ra, ba) in enumerate(boxes):
        for rb, bb in boxes[i + 1 :]:
            if links_mod._overlap(ba, bb):
                out.append(f"courtyards of {ra} and {rb} overlap: check the parts fit side by side")
    half = links_mod.LINK_COURTYARD_NM
    for lk in prep.plan.links:
        n1, n2 = lk.nodes
        if lk.kind == "vertical":
            x, y0 = a.grid.hole_xy(n1)
            _, y1 = a.grid.hole_xy(n2)
            lbox = (x - half, y0 - half, x + half, y1 + half)
            under = [ref for ref, box in boxes if links_mod._overlap(lbox, box)]
        else:
            p, q = a.grid.hole_xy(n1), a.grid.hole_xy(n2)
            under = [ref for ref, box in boxes if links_mod._seg_box_distance(p, q, box) < half]
        if under:
            out.append(
                f"link {lk.ref_hint} ({lk.start}-{lk.end}) runs under {', '.join(sorted(under, key=_natural))}: "
                "fit it first, flat on the board"
            )
    return sorted(out, key=_natural)


def sheet_model(
    board_path: str | Path, cfg: BoardConfig, netlist: str | None = None, date: str | None = None
) -> SheetModel:
    """Everything the build sheet shows, for the board at ``board_path``. Raises BuildError."""
    board_path = Path(board_path)
    board = load_board(board_path)
    project = _board_title(board, board_path)
    prep = prepare(board, cfg, netlist)
    built, check = _check_against_board(prep)
    link_rows, link_warn = _link_rows(prep)
    nets, singles = _net_rows(prep)
    a = prep.analysis
    knife = [c for c in sorted(a.split.cuts, key=lambda c: (c.row, c.col)) if c.style == "knife"]
    model = SheetModel(
        board_path=str(board_path),
        project=project,
        prep=prep,
        links=link_rows,
        parts=_part_rows(prep),
        nets=nets,
        single_pin_nets=singles,
        knife_cuts=knife,
        overlaps=_overlaps(prep),
        unlinkable=[u.text for u in prep.plan.unlinkable],
        built=built,
        date=date or _dt.date.today().isoformat(),
    )
    model.warnings += check + link_warn + list(prep.warnings)
    for s in a.rejected:
        model.warnings.append(f"{s.ref} is not on the sheet: {s.reason}")
    todo = [r.link.ref_hint for r in link_rows if r.status != "placed"]
    if todo:
        model.warnings.append(
            f"{len(todo)} wire link(s) are not placed on the board yet ({_span(todo)}): they are drawn "
            "dashed. Add them to the schematic, press F8 and build again."
        )
    knife_ids = {c.id for c in knife}
    for w in a.warnings:
        if w.split(":", 1)[0] in knife_ids and ": knife cut " in w:
            continue  # listed with the knife cuts
        model.warnings.append(w)
    return model


def _span(refs: list[str]) -> str:
    return refs[0] if len(refs) == 1 else f"{refs[0]}..{refs[-1]}" if len(refs) > 3 else ", ".join(refs)


# --- SVG --------------------------------------------------------------------------------------


def _f(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def _e(s: str) -> str:
    return html.escape(str(s), quote=True)


@dataclass
class _View:
    """Maps board nanometres to view millimetres for one page of one side."""

    model: SheetModel
    mirrored: bool
    c0: int
    c1: int  # inclusive
    r0: int
    r1: int

    @property
    def grid(self):
        return self.model.a.grid

    @property
    def pitch(self) -> float:
        return self.grid.pitch_nm / 1e6

    @property
    def margin(self) -> float:
        return LABEL_PITCHES * self.pitch

    @property
    def width(self) -> float:
        return (self.c1 - self.c0) * self.pitch + 2 * self.margin

    @property
    def height(self) -> float:
        return (self.r1 - self.r0) * self.pitch + 2 * self.margin + 3 * self.pitch  # + ruler

    def x(self, x_nm: float) -> float:
        left = self.grid.hole_xy(Node(0, self.c0))[0]
        right = self.grid.hole_xy(Node(0, self.c1))[0]
        if self.mirrored:
            return self.margin + (right - x_nm) / 1e6
        return self.margin + (x_nm - left) / 1e6

    def y(self, y_nm: float) -> float:
        top = self.grid.hole_xy(Node(self.r0, 0))[1]
        return self.margin + (y_nm - top) / 1e6

    def hole(self, row: int, col: float) -> tuple[float, float]:
        x = self.grid.origin_x_nm + col * self.grid.pitch_nm
        y = self.grid.origin_y_nm + row * self.grid.pitch_nm
        return self.x(x), self.y(y)

    def has_row(self, r: int) -> bool:
        return self.r0 <= r <= self.r1

    def has_col(self, c: float) -> bool:
        return self.c0 <= c <= self.c1

    @property
    def scale(self) -> float:
        return min(MAX_SCALE, PAGE_W_MM / self.width, PAGE_H_MM / self.height)


def _labels(v: _View) -> list[str]:
    p, out = v.pitch, []
    fs = 0.42 * p
    for r in range(v.r0, v.r1 + 1):
        _, y = v.hole(r, v.c0)
        for x in (v.margin * 0.45, v.width - v.margin * 0.45):
            out.append(
                f'<text x="{_f(x)}" y="{_f(y + fs * 0.35)}" class="lbl" font-size="{_f(fs)}">{row_label(r)}</text>'
            )
    for c in range(v.c0, v.c1 + 1):
        x, _ = v.hole(v.r0, c)
        _, ybot = v.hole(v.r1, c)
        for y in (v.margin * 0.55, ybot + v.margin * 0.45 + fs * 0.35):
            out.append(
                f'<text x="{_f(x)}" y="{_f(y)}" class="lbl num" font-size="{_f(fs * 0.8)}">{c + 1}</text>'
            )
    return out


def _outline(v: _View) -> str:
    a = v.model.a
    half = a.grid.pitch_nm // 2
    x0 = a.grid.hole_xy(Node(0, v.c0))[0] - half
    x1 = a.grid.hole_xy(Node(0, v.c1))[0] + half
    y0 = a.grid.hole_xy(Node(v.r0, 0))[1] - half
    y1 = a.grid.hole_xy(Node(v.r1, 0))[1] + half
    xs = sorted((v.x(x0), v.x(x1)))
    return (
        f'<rect class="board" x="{_f(xs[0])}" y="{_f(v.y(y0))}" width="{_f(xs[1] - xs[0])}" '
        f'height="{_f(v.y(y1) - v.y(y0))}" rx="0.6"/>'
    )


def _a1_mark(v: _View) -> list[str]:
    if not (v.has_row(0) and v.has_col(0)):
        return []
    x, y = v.hole(0, 0)
    p = v.pitch
    dx = p * 0.9 if v.mirrored else -p * 0.9
    tip = (x + dx, y - p * 0.9)
    pts = (
        f"{_f(tip[0])},{_f(tip[1])} {_f(tip[0] - dx * 0.8)},{_f(tip[1])} {_f(tip[0])},{_f(tip[1] + p * 0.8)}"
    )
    return [f'<polygon class="a1" points="{pts}"><title>hole A1 corner</title></polygon>']


def _ruler(v: _View) -> list[str]:
    p = v.pitch
    y = v.height - 1.4 * p
    x0 = v.margin
    n = min(10, v.c1 - v.c0) or 1
    out = [f'<g class="ruler"><line x1="{_f(x0)}" y1="{_f(y)}" x2="{_f(x0 + n * p)}" y2="{_f(y)}"/>']
    for i in range(n + 1):
        h = 0.6 if i in (0, n) else 0.3
        out.append(f'<line x1="{_f(x0 + i * p)}" y1="{_f(y - h * p)}" x2="{_f(x0 + i * p)}" y2="{_f(y)}"/>')
    out.append(
        f'<text x="{_f(x0 + n * p + 0.6 * p)}" y="{_f(y)}" font-size="{_f(0.42 * p)}">'
        f"{n} holes = {_f(n * p)} mm on the board; printed at {v.scale:.2f}:1 this ruler is "
        f"{_f(n * p * v.scale)} mm</text></g>"
    )
    return out


def _svg(v: _View, body: list[str], title: str) -> str:
    s = v.scale
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="view" viewBox="0 0 {_f(v.width)} {_f(v.height)}" '
        f'width="{_f(v.width * s)}mm" height="{_f(v.height * s)}mm" data-side="{"copper" if v.mirrored else "component"}" '
        f'data-cols="{v.c0 + 1}-{v.c1 + 1}" data-rows="{row_label(v.r0)}-{row_label(v.r1)}" data-scale="{s:.3f}">'
        f"<title>{_e(title)}</title>" + "".join(body) + "</svg>"
    )


def copper_svg(model: SheetModel, v: _View) -> str:
    """The copper side, mirrored left to right (as seen with the board flipped over to cut)."""
    a, p = model.a, v.pitch
    sw = a.config.strip_width_mm
    body = [_outline(v)]
    for piece in a.split.pieces:
        if not v.has_row(piece.row):
            continue
        c0, c1 = max(piece.col_start, v.c0), min(piece.col_end, v.c1)
        if c0 > c1:
            continue
        (xa, y), (xb, _) = v.hole(piece.row, c0), v.hole(piece.row, c1)
        xl, xr = min(xa, xb), max(xa, xb)
        net = _e(piece.net or "no net")
        body.append(
            f'<rect class="strip" x="{_f(xl - sw / 2)}" y="{_f(y - sw / 2)}" width="{_f(xr - xl + sw)}" '
            f'height="{_f(sw)}" rx="{_f(sw / 2)}"><title>{piece.label} [{net}]</title></rect>'
        )
    occupied = a.holes.occupants
    link_ends = {n: r.link for r in model.links for n in r.link.nodes}
    dead = {(s.row, c) for s in a.strips for c in s.dead_holes}
    for r in range(v.r0, v.r1 + 1):
        for c in range(v.c0, v.c1 + 1):
            x, y = v.hole(r, c)
            node = Node(r, c)
            if node in occupied:
                who = ", ".join(f"{o.label} [{o.net or 'no net'}]" for o in occupied[node])
                body.append(
                    f'<circle class="joint" cx="{_f(x)}" cy="{_f(y)}" r="0.85"><title>{node.label}: {_e(who)}</title></circle>'
                    f'<circle class="lead" cx="{_f(x)}" cy="{_f(y)}" r="0.4"/>'
                )
            elif node in link_ends:
                lk = link_ends[node]
                body.append(
                    f'<circle class="linkend" cx="{_f(x)}" cy="{_f(y)}" r="0.8"><title>{node.label}: '
                    f"{lk.ref_hint} end [{_e(lk.net)}]</title></circle>"
                )
            elif (r, c) not in dead:
                body.append(f'<circle class="hole" cx="{_f(x)}" cy="{_f(y)}" r="0.45"/>')
    for row in model.links:
        lk = row.link
        span = _link_span(v, lk)
        if span is None:
            continue
        xa, ya, xb, yb = span
        body.append(
            f'<line class="ghost" x1="{_f(xa)}" y1="{_f(ya)}" x2="{_f(xb)}" y2="{_f(yb)}"><title>{lk.ref_hint} '
            f"(on the component side)</title></line>"
        )
    for job in a.slot_jobs:
        if not (v.has_row(job.hole.row) and v.has_col(job.hole.col)):
            continue
        x, y = v.hole(job.hole.row, job.hole.col)
        xt, _ = v.hole(job.toward.row, job.toward.col)
        d = max(job.file_len_nm / 1e6, 0.5) * (1 if xt > x else -1)
        body.append(
            f'<g class="slot" data-slot="{job.hole.label}"><circle cx="{_f(x)}" cy="{_f(y)}" r="1.15"/>'
            f'<line x1="{_f(x)}" y1="{_f(y)}" x2="{_f(x + d)}" y2="{_f(y)}"/>'
            f'<text x="{_f(x)}" y="{_f(y + 1.9)}" font-size="{_f(0.36 * p)}">slot</text>'
            f"<title>{_e(job.text)}</title></g>"
        )
    fs = 0.36 * p
    for cut in a.split.cuts:
        if not (v.has_row(cut.row) and v.has_col(cut.col)):
            continue
        x, y = v.hole(cut.row, cut.col)
        if cut.style == "hole":
            d = 0.8
            mark = (
                f'<circle cx="{_f(x)}" cy="{_f(y)}" r="1.1"/>'
                f'<path d="M{_f(x - d)},{_f(y - d)}L{_f(x + d)},{_f(y + d)}M{_f(x - d)},{_f(y + d)}L{_f(x + d)},{_f(y - d)}"/>'
            )
        else:
            mark = f'<line x1="{_f(x)}" y1="{_f(y - 1.3)}" x2="{_f(x)}" y2="{_f(y + 1.3)}" class="knife"/>'
        body.append(
            f'<g class="cut {cut.style}" data-cut="{cut.id}">{mark}'
            f'<text x="{_f(x)}" y="{_f(y - 1.35)}" font-size="{_f(fs)}">{cut.id}</text>'
            f"<title>{cut.id}: {cut.style} cut {_e(cut.where)}</title></g>"
        )
    body += _labels(v) + _a1_mark(v) + _ruler(v)
    return _svg(v, body, f"{model.project}: copper side, mirrored")


def _fab_shapes(fp: Footprint, v: _View) -> list[str]:
    layer = "B.Fab" if fp.layer == "B.Cu" else "F.Fab"
    shapes = [g for g in fp.node if (head(g) or "").startswith("fp_") and atom(find(g, "layer"), 1) == layer]
    if not shapes:
        crt = "B.CrtYd" if fp.layer == "B.Cu" else "F.CrtYd"
        shapes = [
            g for g in fp.node if (head(g) or "").startswith("fp_") and atom(find(g, "layer"), 1) == crt
        ]

    def pt(node, key):
        n = find(node, key)
        if n is None:
            return None
        rx, ry = rotate_nm(mm_to_nm(atom(n, 1)), mm_to_nm(atom(n, 2)), fp.angle)
        return v.x(fp.x_nm + rx), v.y(fp.y_nm + ry)

    out = []
    for g in shapes:
        kind = head(g)
        if kind == "fp_line":
            a, b = pt(g, "start"), pt(g, "end")
            if a and b:
                out.append(f'<line x1="{_f(a[0])}" y1="{_f(a[1])}" x2="{_f(b[0])}" y2="{_f(b[1])}"/>')
        elif kind == "fp_rect":
            s, e = find(g, "start"), find(g, "end")
            if s is None or e is None:
                continue
            xs = (mm_to_nm(atom(s, 1)), mm_to_nm(atom(e, 1)))
            ys = (mm_to_nm(atom(s, 2)), mm_to_nm(atom(e, 2)))
            pts = []
            for lx, ly in ((xs[0], ys[0]), (xs[1], ys[0]), (xs[1], ys[1]), (xs[0], ys[1])):
                rx, ry = rotate_nm(lx, ly, fp.angle)
                pts.append(f"{_f(v.x(fp.x_nm + rx))},{_f(v.y(fp.y_nm + ry))}")
            out.append(f'<polygon points="{" ".join(pts)}"/>')
        elif kind == "fp_circle":
            c, e = pt(g, "center"), pt(g, "end")
            if c and e:
                out.append(f'<circle cx="{_f(c[0])}" cy="{_f(c[1])}" r="{_f(math.dist(c, e))}"/>')
        elif kind == "fp_arc":
            s, m, e = pt(g, "start"), pt(g, "mid"), pt(g, "end")
            if s and m and e:
                out.append(_arc(s, m, e))
        elif kind == "fp_poly":
            poly = find(g, "pts")
            pts = []
            for xy in find_all(poly, "xy") if poly is not None else ():
                rx, ry = rotate_nm(mm_to_nm(atom(xy, 1)), mm_to_nm(atom(xy, 2)), fp.angle)
                pts.append(f"{_f(v.x(fp.x_nm + rx))},{_f(v.y(fp.y_nm + ry))}")
            if pts:
                out.append(f'<polygon points="{" ".join(pts)}"/>')
    return out


def _arc(s, m, e) -> str:
    (ax, ay), (bx, by), (cx, cy) = s, m, e
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return f'<line x1="{_f(ax)}" y1="{_f(ay)}" x2="{_f(cx)}" y2="{_f(cy)}"/>'
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.dist((ux, uy), s)
    cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
    sweep = 1 if cross > 0 else 0
    # the arc is more than half a circle when the mid point and the centre are on the same side of the chord
    side = lambda px, py: (cx - ax) * (py - ay) - (cy - ay) * (px - ax)  # noqa: E731
    large = 1 if side(bx, by) * side(ux, uy) > 0 else 0
    return f'<path d="M{_f(ax)},{_f(ay)}A{_f(r)},{_f(r)} 0 {large} {sweep} {_f(cx)},{_f(cy)}"/>'


def component_svg(model: SheetModel, v: _View) -> str:
    """The component side: part outlines, refs and values, pads with pin 1 marked, wire links."""
    a, p = model.a, v.pitch
    body = [_outline(v)]
    for piece in a.split.pieces:
        if not v.has_row(piece.row):
            continue
        c0, c1 = max(piece.col_start, v.c0), min(piece.col_end, v.c1)
        if c0 <= c1:
            (xa, y), (xb, _) = v.hole(piece.row, c0), v.hole(piece.row, c1)
            body.append(f'<line class="underside" x1="{_f(xa)}" y1="{_f(y)}" x2="{_f(xb)}" y2="{_f(y)}"/>')
    for r in range(v.r0, v.r1 + 1):
        for c in range(v.c0, v.c1 + 1):
            x, y = v.hole(r, c)
            body.append(f'<circle class="hole" cx="{_f(x)}" cy="{_f(y)}" r="0.4"/>')
    skip = set(a.config.offboard_refs)
    snapped = {s.ref for s in a.snapped}
    texts: list[str] = []  # part labels go on top of the wire links so a link never hides a ref
    for fp in a.board.footprints:
        if fp.ref in skip or fp.ref not in snapped:
            continue
        shapes = _fab_shapes(fp, v)
        pads = [(pd, v.x(pd.x_nm), v.y(pd.y_nm)) for pd in fp.pads if pd.is_tht]
        if not pads:
            continue
        xs = [x for _, x, _ in pads]
        ys = [y for _, _, y in pads]
        if not shapes:
            shapes = [
                f'<rect x="{_f(min(xs) - 1.2)}" y="{_f(min(ys) - 1.2)}" width="{_f(max(xs) - min(xs) + 2.4)}" '
                f'height="{_f(max(ys) - min(ys) + 2.4)}"/>'
            ]
        g = [f'<g class="part" data-ref="{_e(fp.ref)}"><g class="fab">' + "".join(shapes) + "</g>"]
        for pd, x, y in pads:
            if pd.number == "1":
                g.append(
                    f'<rect class="pad pin1" x="{_f(x - 0.8)}" y="{_f(y - 0.8)}" width="1.6" height="1.6">'
                    f"<title>{_e(fp.ref)} pin 1</title></rect>"
                )
            else:
                g.append(f'<circle class="pad" cx="{_f(x)}" cy="{_f(y)}" r="0.8"/>')
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        one = next(((x, y) for pd, x, y in pads if pd.number == "1"), None)
        if one is not None and len(pads) > 1:
            g.append(f'<circle class="pin1dot" cx="{_f(one[0])}" cy="{_f(one[1])}" r="0.35"/>')
        fs = 0.5 * p
        texts.append(f'<text class="ref" x="{_f(cx)}" y="{_f(cy)}" font-size="{_f(fs)}">{_e(fp.ref)}</text>')
        val = _value(fp)
        if val:
            short = val if len(val) <= 18 else val[:17] + "…"
            texts.append(
                f'<text class="val" x="{_f(cx)}" y="{_f(cy + fs * 0.95)}" font-size="{_f(fs * 0.62)}">'
                f"{_e(short)}<title>{_e(val)}</title></text>"
            )
        g.append("</g>")
        body.append("".join(g))
    for row in model.links:
        lk = row.link
        span = _link_span(v, lk)
        if span is None:
            continue
        xa, ya, xb, yb = span
        cls = "wire" if row.status == "placed" else "wire proposed"
        xm, ym = (xa + xb) / 2, (ya + yb) / 2
        if lk.kind == "horizontal":
            ym -= 0.35 * p
        body.append(
            f'<g class="{cls}" data-link="{lk.ref_hint}"><line x1="{_f(xa)}" y1="{_f(ya)}" x2="{_f(xb)}" y2="{_f(yb)}"/>'
            f'<circle cx="{_f(xa)}" cy="{_f(ya)}" r="0.55"/><circle cx="{_f(xb)}" cy="{_f(yb)}" r="0.55"/>'
            f'<text x="{_f(xm + 0.5)}" y="{_f(ym)}" font-size="{_f(0.42 * p)}">{lk.ref_hint}</text>'
            f"<title>{lk.ref_hint}: {lk.start} to {lk.end} [{_e(lk.net)}]</title></g>"
        )
    body.append('<g class="part-labels">' + "".join(texts) + "</g>")
    body += _labels(v) + _a1_mark(v) + _ruler(v)
    return _svg(v, body, f"{model.project}: component side")


def views(model: SheetModel, mirrored: bool) -> list[_View]:
    """One view per page: the grid cut into blocks of at most MAX_COLS x MAX_ROWS holes."""
    g = model.a.grid
    col_blocks = [(c, min(c + MAX_COLS, g.cols) - 1) for c in range(0, g.cols, MAX_COLS)]
    row_blocks = [(r, min(r + MAX_ROWS, g.rows) - 1) for r in range(0, g.rows, MAX_ROWS)]
    if mirrored:  # flipped over, the right-hand columns come first
        col_blocks = col_blocks[::-1]
    return [_View(model, mirrored, c0, c1, r0, r1) for r0, r1 in row_blocks for c0, c1 in col_blocks]


# --- HTML -------------------------------------------------------------------------------------

CSS = """
@page { size: Letter portrait; margin: 12mm; }
@page landscape { size: Letter landscape; margin: 10mm; }
* { box-sizing: border-box; }
body { font-family: "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10pt; color: #111;
       background: #fff; margin: 0 auto; max-width: 270mm; padding: 6mm; }
h1 { font-size: 15pt; margin: 0 0 1.5mm; } h2 { font-size: 13pt; margin: 5mm 0 2mm; border-bottom: 1.5pt solid #333; }
h3 { font-size: 11pt; margin: 3mm 0 1mm; }
.meta { border-collapse: collapse; margin-bottom: 1.5mm; font-size: 9pt; }
.meta td { padding: 0.3mm 3mm 0.3mm 0; } .meta td:nth-child(odd) { font-weight: bold; white-space: nowrap; }
.summary { display: flex; flex-wrap: wrap; gap: 2mm 6mm; margin: 2mm 0; }
.summary span b { font-size: 12pt; }
.banner { border: 1.2pt solid #b00; background: #fff3f3; padding: 2mm 3mm; margin: 2mm 0; }
.first, .viewpage { page: landscape; }
.viewpage { break-before: page; }
h4 { font-size: 10pt; margin: 2.5mm 0 1mm; }
ul.check.three { columns: 3; column-gap: 5mm; font-size: 9pt; }
table.parts { table-layout: fixed; }
col.c-box { width: 7mm; } col.c-ref { width: 12mm; } col.c-val { width: 34mm; } col.c-fp { width: 62mm; }
table.parts td { overflow-wrap: anywhere; }
.viewpage h2 { margin-top: 0; }
.mirror-note { font-weight: bold; color: #b00; font-size: 11pt; margin: 1mm 0 2mm; }
svg.view { display: block; margin: 0 auto; background: #fff; }
svg .board { fill: #fbf7ef; stroke: #555; stroke-width: 0.25; }
svg[data-side="component"] .board { fill: #f3f6ef; }
svg .strip { fill: #d8a25e; stroke: #a8743a; stroke-width: 0.08; }
svg .hole { fill: #fff; stroke: #999; stroke-width: 0.1; }
svg .joint { fill: #9a9a9a; stroke: #333; stroke-width: 0.12; }
svg .lead { fill: #222; }
svg .linkend { fill: #cfe0fb; stroke: #1f5fbf; stroke-width: 0.25; }
svg .ghost { stroke: #1f5fbf; stroke-width: 0.35; stroke-dasharray: 0.8 0.6; opacity: 0.55; }
svg .cut { stroke: #d11a1a; fill: none; stroke-width: 0.35; }
svg .cut .knife { stroke-width: 0.7; }
svg .cut text { fill: #d11a1a; stroke: #fff; stroke-width: 0.25; paint-order: stroke; font-weight: bold;
                text-anchor: middle; }
svg .slot line { stroke: #e07b00; stroke-width: 1.1; stroke-linecap: round; }
svg .slot circle { fill: none; stroke: #e07b00; stroke-width: 0.4; }
svg .slot text { fill: #e07b00; text-anchor: middle; font-weight: bold; }
svg .lbl { fill: #333; text-anchor: middle; font-weight: bold; }
svg .a1 { fill: #d11a1a; }
svg .ruler line { stroke: #000; stroke-width: 0.15; } svg .ruler text { fill: #000; }
svg .underside { stroke: #d8a25e; stroke-width: 1.8; stroke-linecap: round; opacity: 0.22; }
svg .fab { fill: none; stroke: #2b2b2b; stroke-width: 0.18; }
svg .pad { fill: #fff; stroke: #555; stroke-width: 0.18; }
svg .pin1 { fill: #ffe9a8; stroke: #b00; stroke-width: 0.25; }
svg .pin1dot { fill: #b00; }
svg .ref { fill: #000; font-weight: bold; text-anchor: middle; stroke: #fff; stroke-width: 0.4; paint-order: stroke; }
svg .val { fill: #333; text-anchor: middle; stroke: #fff; stroke-width: 0.3; paint-order: stroke; }
svg .wire line { stroke: #1f5fbf; stroke-width: 0.6; stroke-linecap: round; }
svg .wire circle { fill: #1f5fbf; }
svg .wire text { fill: #1f5fbf; font-weight: bold; stroke: #fff; stroke-width: 0.2; paint-order: stroke; }
svg .wire.proposed line { stroke-dasharray: 1 0.7; }
.legend { font-size: 8.5pt; margin: 1.5mm 0; color: #333; }
.legend i { display: inline-block; width: 4mm; height: 2.5mm; vertical-align: middle; margin: 0 1mm 0 3mm; }
ol.steps { padding-left: 5mm; }
ul.check { list-style: none; padding: 0; margin: 0 0 2mm; columns: 2; column-gap: 8mm; }
ul.check.one { columns: 1; }
ul.check li.strip-head { break-after: avoid; }
ul.check li { break-inside: avoid; padding: 0.6mm 0 0.6mm 6mm; text-indent: -6mm; }
.box { display: inline-block; width: 3.2mm; height: 3.2mm; border: 0.9pt solid #000; margin-right: 2.2mm;
       vertical-align: -0.5mm; text-indent: 0; }
.mono { font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 9pt; }
table.list { border-collapse: collapse; width: 100%; margin-bottom: 2mm; }
table.list th, table.list td { border: 0.5pt solid #999; padding: 0.8mm 1.5mm; text-align: left; vertical-align: top; }
table.list th { background: #eee; }
table.list tr { break-inside: avoid; }
.muted { color: #666; }
.warn li { margin-bottom: 1mm; }
section.lists { break-before: page; }
footer { margin-top: 6mm; font-size: 8pt; color: #666; }
"""


def _box() -> str:
    return '<span class="box"></span>'


def _legend(copper: bool) -> str:
    if copper:
        items = [
            (COPPER, "strip copper"),
            (CUT_RED, "cut: ring + cross = hole cut (spot-face), bar = knife cut"),
            ("#9a9a9a", "solder joint (part lead)"),
            ("#cfe0fb", "link end (solder a wire link here)"),
            (SLOT_ORANGE, "slot: file the hole along the strip"),
        ]
    else:
        items = [
            ("#2b2b2b", "part outline"),
            ("#ffe9a8", "pin 1 (square pad, red dot)"),
            (LINK_BLUE, "wire link (dashed: proposed, not placed yet)"),
        ]
    return (
        '<div class="legend">'
        + "".join(f'<i style="background:{c}"></i>{_e(t)}' for c, t in items)
        + "</div>"
    )


def _cut_how(cut: Cut) -> str:
    if cut.style == "hole":
        return f"hole <b>{cut.label}</b>"
    c = int(cut.col)
    how = f"<b>knife {hole_label(cut.row, c)}|{hole_label(cut.row, c + 1)}</b>"
    if cut.knife_for:
        how += f" (knife, per {_e(cut.knife_for)} setting)"
    return how


def render_html(model: SheetModel) -> str:
    a = model.a
    g, cfg = a.grid, a.config
    cuts = model.cuts
    hole_cuts = sum(1 for c in cuts if c.style == "hole")
    placed = sum(1 for r in model.links if r.status == "placed")
    parts = [r for r in model.parts if r.group != 0]
    out = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="generator" content="StripForge ' + _e(__version__) + '">',
        f"<title>StripForge build sheet: {_e(model.project)}</title>",
        f"<style>{CSS}</style></head><body>",
        '<div class="first">',
        f"<h1>StripForge build sheet: {_e(model.project)}</h1>",
        '<table class="meta">',
        f"<tr><td>Project</td><td>{_e(model.project)}</td><td>Date</td><td>{_e(model.date)}</td></tr>",
        f'<tr><td>Board file</td><td class="mono">{_e(Path(model.board_path).name)}</td>'
        f"<td>StripForge</td><td>{_e(__version__)}</td></tr>",
        f"<tr><td>Board</td><td colspan='3'>{g.cols} holes × {g.rows} strips ({g.span_label}), "
        f"{_f(g.cols * g.pitch_nm / 1e6)} × {_f(g.rows * g.pitch_nm / 1e6)} mm at {_f(g.pitch_nm / 1e6)} mm pitch; "
        f"strips run horizontally, lettered A.. top to bottom (component side), holes numbered 1.. left to right</td></tr>",
        "</table>",
        '<div class="summary">',
        f"<span><b>{len(cuts)}</b> cuts ({hole_cuts} hole, {len(cuts) - hole_cuts} knife)</span>",
        f"<span><b>{len(a.slot_jobs)}</b> slot jobs</span>",
        f"<span><b>{len(model.links)}</b> wire links ({placed} placed)</span>",
        f"<span><b>{len(parts)}</b> parts</span>",
        f"<span><b>{len(model.nets)}</b> nets to check</span>",
        f"<span><b>{_n_warnings(model)}</b> warnings (section 5)</span>",
        "</div>",
    ]
    if model.built != "built":
        out.append(
            '<div class="banner" data-built="'
            + model.built
            + '"><b>Check before building:</b> '
            + _e(model.warnings[0])
            + "</div>"
        )
    cviews = views(model, mirrored=True)
    for i, v in enumerate(cviews, 1):
        part = f" (page {i} of {len(cviews)}: holes {v.c0 + 1}-{v.c1 + 1}, strips {row_label(v.r0)}-{row_label(v.r1)})"
        out += [
            '<section class="viewpage" data-view="copper">' if i > 1 else '<section data-view="copper">',
            f"<h2>1. Copper side (bottom), MIRRORED{part if len(cviews) > 1 else ''}</h2>",
            '<p class="mirror-note">MIRRORED: this is the board flipped over left to right, copper up, as you '
            "hold it to cut. Hole 1 is on the RIGHT; strip A is at the top. The red corner mark is hole A1.</p>",
            copper_svg(model, v),
            _legend(True),
            "</section>" + ("</div>" if i == 1 else ""),
        ]
    pviews = views(model, mirrored=False)
    for i, v in enumerate(pviews, 1):
        part = f" (page {i} of {len(pviews)}: holes {v.c0 + 1}-{v.c1 + 1}, strips {row_label(v.r0)}-{row_label(v.r1)})"
        out += [
            '<section class="viewpage" data-view="component">',
            f"<h2>2. Component side (top){part if len(pviews) > 1 else ''}</h2>",
            '<p class="legend">Not mirrored: as seen from the parts side. Hole 1 is on the left. The faint '
            "bands are the strips underneath.</p>",
            component_svg(model, v),
            _legend(False),
            "</section>",
        ]
    out.append(
        '<section class="lists"><h2>3. Checklists, in build order</h2><ol class="steps"><li>Cut the strips '
        "(list 1) and file the slots (list 2), working from the copper-side view with the board flipped over."
        "</li><li>Check every cut with a loupe or a meter: no copper bridges.</li><li>Fit the wire links "
        "(list 3), then the parts (list 4), lowest first.</li><li>Before power, check each net with a "
        "continuity meter (section 4).</li></ol>"
    )
    out += _cut_list(model, cuts)
    out += _slot_list(model)
    out += _link_list(model)
    out += _part_list(model)
    out.append("</section>")
    out += _net_table(model)
    out += _warnings(model)
    out.append(
        f"<footer>Generated by StripForge {_e(__version__)} from {_e(Path(model.board_path).name)}, "
        f"config grid {g.span_label}, strip width {_f(cfg.strip_width_mm)} mm, cut style "
        f"{_e(str(cfg.cut_style))}. Print on Letter at 100% (no 'fit to page'); the ruler under each view "
        "gives the printed scale.</footer></body></html>"
    )
    return "\n".join(out) + "\n"


def _cut_list(model: SheetModel, cuts: list[Cut]) -> list[str]:
    out = [
        f"<h3>List 1: cuts ({len(cuts)}), grouped by strip</h3>",
        '<p class="legend"><b>hole</b>: cut the copper right round that hole with a spot-face cutter (or a '
        "3-4 mm drill bit turned by hand). <b>knife</b> A|B: between holes A and B, score across the strip "
        "twice and lift the copper between. The pads either side are in brackets.</p>",
    ]
    if not cuts:
        return out + ['<p class="muted">No cuts.</p>']
    rows: dict[int, list[Cut]] = {}
    for c in cuts:
        rows.setdefault(c.row, []).append(c)
    out.append('<ul class="check three">')
    for r, rc in rows.items():
        out.append(f'<li class="strip-head"><b>Strip {row_label(r)}</b> ({len(rc)})</li>')
        for c in rc:
            out.append(
                f'<li class="item" data-kind="cut" data-cut="{c.id}" data-style="{c.style}">{_box()}'
                f'<span class="mono">{c.id}</span> {_cut_how(c)} <span class="muted">({_e(c.between[0])}|'
                f"{_e(c.between[1])})</span>{' <b>(yours)</b>' if c.user and not c.auto else ''}</li>"
            )
    out.append("</ul>")
    return out


def _slot_list(model: SheetModel) -> list[str]:
    jobs = model.a.slot_jobs
    out = [f"<h3>List 2: slot jobs ({len(jobs)})</h3>"]
    if not jobs:
        return out + ['<p class="muted">No slotted parts.</p>']
    out.append('<ul class="check one">')
    for j in jobs:
        where = "toward the part centre" if j.inward else "away from the part centre"
        out.append(
            f'<li class="item" data-kind="slot" data-hole="{j.hole.label}">{_box()}file <b>{j.hole.label}</b> '
            f"<b>{_e(j.amount)}</b> toward <b>{j.toward.label}</b> ({where}) for {_e(j.ref)} pin "
            f"{_e(j.pad)}: elongate the hole along the strip so the pin drops in</li>"
        )
    out.append("</ul>")
    return out


def _link_list(model: SheetModel) -> list[str]:
    out = [f"<h3>List 3: wire links ({len(model.links)})</h3>"]
    if not model.links:
        return out + ['<p class="muted">No wire links.</p>']
    out.append(
        '<table class="list"><tr><th></th><th>Ref</th><th>From</th><th>To</th><th>Length (pad-to-pad)</th>'
        "<th>Footprint</th><th>Net</th><th>Status</th></tr>"
    )
    for r in model.links:
        lk = r.link
        status = "placed" if r.status == "placed" else f"<b>{_e(r.detail)}</b>"
        if lk.origin:
            status += " <b>(yours)</b>" if lk.origin == "board" else " <b>([manual])</b>"
        out.append(
            f'<tr class="item" data-kind="link" data-link="{lk.ref_hint}"><td>{_box()}</td><td class="mono">{lk.ref_hint}</td>'
            f"<td><b>{lk.start}</b></td><td><b>{lk.end}</b></td><td><b>{_e(lk.inches)}</b> ({lk.pitches} holes, {_f(lk.length_mm)} mm){_link_how(lk)}</td>"
            f'<td class="mono">{_e(lk.footprint)}</td><td>{_e(lk.net)}</td><td>{status}</td></tr>'
        )
    out.append("</table>")
    out += _link_cut_list(model)
    out += _stretch_list(model)
    return out


def _link_cut_list(model: SheetModel) -> list[str]:
    """One row per link length, shortest first: pre-cut and bend every link of a length in one go."""
    rows = links_mod.cut_list([r.link for r in model.links])
    allowance = float(getattr(model.prep.config, "link_lead_allowance_in", 0.0) or 0.0)
    cut = allowance > 0
    out = [
        f"<h4>Link cut list ({len(rows)} length(s))</h4>",
        '<table class="list cutlist"><tr><th></th><th>Length (inches, pad-to-pad)</th><th>Qty</th>'
        + ("<th>Cut length</th>" if cut else "")
        + "<th>Links</th></tr>",
    ]
    for v, refs in rows:
        extra = f"<td><b>{_e(links_mod.inch_text(round(v + 2 * allowance, 2)))}</b></td>" if cut else ""
        out.append(
            f'<tr class="item" data-kind="link-length" data-length="{v:g}"><td>{_box()}</td>'
            f"<td><b>{_e(links_mod.inch_text(v))}</b></td><td>{len(refs)}</td>{extra}"
            f'<td class="mono">{_e(", ".join(refs))}</td></tr>'
        )
    out.append("</table>")
    note = links_mod.LEAD_NOTE
    if cut:
        note += f" Cut length = pad-to-pad + 2 x {links_mod.inch_text(allowance)} (link_lead_allowance_in)."
    out.append(f'<p class="legend cutlist-note"><b>Reminder:</b> {_e(note)}</p>')
    return out


def _stretch_list(model: SheetModel) -> list[str]:
    """Lead-stretch suggestions (report only): links a longer lead on a two-pin part could replace."""
    items = list(getattr(model.prep, "stretches", []) or [])
    if not items:
        return []
    out = [
        f"<h4>Lead-stretch suggestions ({len(items)})</h4>",
        '<p class="legend">Optional: a longer lead on a two-pin part could stand in for these links. '
        "Nothing was moved; to take one, change the part's footprint so the pin lands on the new hole, "
        "leave the link out of the schematic and build again.</p>",
        '<ul class="warn">',
    ]
    out += [f'<li data-kind="stretch">{_e(s.text())}</li>' for s in items]
    out.append("</ul>")
    return out


def _part_list(model: SheetModel) -> list[str]:
    parts = [r for r in model.parts if r.group != 0]
    out = [f"<h3>List 4: parts ({len(parts)}), lowest first</h3>"]
    if model.links:
        out.append(
            f"<p>Fit the {len(model.links)} wire links from list 3 first; they lie flat under the parts.</p>"
        )
    group = None
    for r in parts:
        if r.group != group:
            if group is not None:
                out.append("</table>")
            group = r.group
            out.append(
                f"<h4>{_e(r.group_name)}</h4>"
                '<table class="list parts"><colgroup><col class="c-box"><col class="c-ref"><col class="c-val">'
                '<col class="c-fp"><col></colgroup>'
                "<tr><th></th><th>Ref</th><th>Value</th><th>Footprint</th><th>Pins (pin: hole)</th></tr>"
            )
        pins = " ".join(f"{_e(n)}:<b>{_e(h)}</b>" for n, h, _ in r.pins)
        note = " <i>(slotted: file its holes first)</i>" if r.slotted else ""
        if r.bend:
            note += f" <i>({_e(r.bend)})</i>"
        out.append(
            f'<tr class="item" data-kind="part" data-ref="{_e(r.ref)}"><td>{_box()}</td><td class="mono">{_e(r.ref)}</td>'
            f'<td>{_e(r.value)}</td><td class="mono">{_e(r.footprint)}</td><td class="mono">{pins}{note}</td></tr>'
        )
    if group is not None:
        out.append("</table>")
    out += _offboard_list(model)
    return out


def _offboard_list(model: SheetModel) -> list[str]:
    """Parts the config skips: not on the stripboard, so their connections are hand-wired."""
    a = model.a
    fps = {fp.ref: fp for fp in a.board.footprints}
    refs = a.skipped
    if not refs:
        return []
    out = [f"<h4>Wired off-board ({len(refs)})</h4>", '<ul class="check one">']
    for r in refs:
        nets = sorted({p.net for p in fps[r].pads if p.net and not p.net.startswith("unconnected-")})
        out.append(
            f'<li class="item" data-kind="offboard" data-ref="{_e(r)}">{_box()}<b>{_e(r)}</b> '
            f"({_e(_value(fps[r]))}): wired off-board, not on the stripboard; hand-wire it to "
            f"{_e(', '.join(nets)) if nets else 'nothing (no nets)'}</li>"
        )
    out.append("</ul>")
    return out


def _net_table(model: SheetModel) -> list[str]:
    out = [
        '<section class="nets"><h2>4. Net check (continuity)</h2>',
        "<p>With the meter on continuity, each net must beep between every hole listed, and must not beep "
        "to the neighbouring strips. Link ends count as holes on their net.</p>",
        '<table class="list"><tr><th></th><th>Net</th><th>Holes it must reach</th></tr>',
    ]
    for n in model.nets:
        holes = ", ".join(f'<b>{_e(h)}</b> <span class="muted">{_e(w)}</span>' for h, w in n.holes)
        flag = "" if n.joined else ' <b style="color:#b00">(not fully joined: see warnings)</b>'
        out.append(
            f'<tr class="item" data-kind="net" data-net="{_e(n.net)}"><td>{_box()}</td><td>{_e(n.net)}{flag}</td>'
            f'<td class="mono">{holes}</td></tr>'
        )
    out.append("</table>")
    if model.single_pin_nets:
        out.append(
            f'<p class="muted">{model.single_pin_nets} net(s) with a single pin (unused pins) are not listed.</p>'
        )
    out.append("</section>")
    return out


def _n_warnings(model: SheetModel) -> int:
    return len(model.warnings) + len(model.knife_cuts) + len(model.overlaps) + len(model.unlinkable)


def _warnings(model: SheetModel) -> list[str]:
    out = [f'<section class="warnings"><h2>5. Warnings ({_n_warnings(model)})</h2>']
    sections = [
        (
            "Knife cuts",
            "knife",
            [f"{c.id}: {c.where} ({c.between[0]} | {c.between[1]})" for c in model.knife_cuts],
        ),
        ("Courtyard overlaps", "overlap", model.overlaps),
        ("Unlinkable nets", "unlinkable", model.unlinkable),
        ("Other", "other", model.warnings),
    ]
    for title, kind, items in sections:
        out.append(f"<h3>{title} ({len(items)})</h3>")
        if not items:
            out.append('<p class="muted">None.</p>')
            continue
        out.append(
            '<ul class="warn">' + "".join(f'<li data-kind="{kind}">{_e(t)}</li>' for t in items) + "</ul>"
        )
    out.append("</section>")
    return out


def cuts_csv(model: SheetModel) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["id", "strip", "style", "at", "between_a", "between_b", "net_a", "net_b"])
    for c in model.cuts:
        w.writerow([c.id, row_label(c.row), c.style, c.label, *c.between, *c.reason])
    return buf.getvalue()


# --- PDF and PNG via headless Chrome ----------------------------------------------------------

CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge")
CHROME_MAC = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)


def find_chrome(explicit: str | None = None) -> str | None:
    """``--chrome``, ``$STRIPFORGE_CHROME``, a Chrome/Chromium on PATH, then the macOS apps."""
    for cand in (explicit, os.environ.get("STRIPFORGE_CHROME")):
        if cand:
            return cand if Path(cand).exists() or shutil.which(cand) else None
    for name in CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return next((p for p in CHROME_MAC if Path(p).exists()), None)


def _run_chrome(cmd: list[str], out: Path, timeout: float) -> None:
    """Run Chrome until ``out`` is written. Headless Chrome on macOS often writes the file and then
    never exits, so poll for the file and a stable size, then stop the whole process group."""
    if out.exists():
        out.unlink()
    kw = {"start_new_session": True} if os.name == "posix" else {}
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, **kw)
    deadline, last, stable = time.monotonic() + timeout, -1, 0
    try:
        while time.monotonic() < deadline:
            code = proc.poll()
            size = out.stat().st_size if out.exists() else -1
            if size > 0 and size == last:
                stable += 1
                if stable >= 4 or code is not None:  # ~1 s at 0.25 s polls
                    return
            else:
                stable = 0
            last = size
            if code is not None:
                if size > 0:
                    return
                err = proc.stderr.read().decode(errors="replace")[-400:] if proc.stderr else ""
                raise subprocess.CalledProcessError(code, cmd, stderr=err)
            time.sleep(0.25)
        raise subprocess.TimeoutExpired(cmd, timeout)
    finally:
        if proc.poll() is None:
            _stop(proc)
        if proc.stderr:
            proc.stderr.close()


def _stop(proc: subprocess.Popen) -> None:
    """Terminate Chrome and its helper processes (its own process group on POSIX)."""
    import signal

    for sig in (signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)):
        try:
            if os.name == "posix":
                os.killpg(proc.pid, sig)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            continue


def _chrome(chrome: str, args: list[str], out: Path, timeout: int = 120) -> None:
    with tempfile.TemporaryDirectory(prefix="stripforge-chrome-") as prof:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            f"--user-data-dir={prof}",
            *args,
        ]
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd.insert(1, "--no-sandbox")
        try:
            _run_chrome(cmd, out, timeout)
        except subprocess.CalledProcessError:
            if "--no-sandbox" in cmd:
                raise
            # some Linux hosts (containers, CI) have no usable Chrome sandbox; the page is our own file
            _run_chrome([cmd[0], "--no-sandbox", *cmd[1:]], out, timeout)


def html_to_pdf(html_path: Path, pdf_path: Path, chrome: str) -> None:
    _chrome(
        chrome,
        ["--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
        pdf_path,
    )
    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise RuntimeError("Chrome did not write the PDF")


def svg_to_png(svg: str, png_path: Path, chrome: str, width_px: int = 2000) -> None:
    m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
    w, h = (float(m.group(1)), float(m.group(2))) if m else (4.0, 3.0)
    height_px = int(width_px * h / w) + 8
    page = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}'
        f"{CSS.split('svg.view')[1].split('}', 1)[1]}svg{{width:{width_px}px;height:auto;display:block}}"
        f"</style></head><body>{svg}</body></html>"
    )
    with tempfile.TemporaryDirectory(prefix="stripforge-png-") as tmp:
        src = Path(tmp) / "view.html"
        src.write_text(page, encoding="utf-8")
        _chrome(
            chrome,
            [f"--window-size={width_px},{height_px}", f"--screenshot={png_path}", src.as_uri()],
            png_path,
        )
    if not png_path.exists():
        raise RuntimeError("Chrome did not write the PNG")


# --- entry point ------------------------------------------------------------------------------


@dataclass
class SheetResult:
    model: SheetModel
    outputs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)  # the previous sheet (HTML, PDF), when it changed


SHEET_BACKUP_SUFFIX = "-prev"  # <name>-stripforge.sheet-prev.html, -prev-1.html, ... (like the boards)


def _keep_previous(out_path: Path, html: str, keep: int, res: SheetResult) -> None:
    """Back the previous sheet up before it is overwritten, rotating older copies like the board
    backups, but only when the new sheet differs from it (the HTML and, when there is one, its PDF)."""
    from .writer import rotate_backups

    try:
        if not out_path.is_file() or out_path.read_text(encoding="utf-8") == html:
            return
    except (OSError, UnicodeDecodeError):
        return
    for p in (out_path, out_path.with_suffix(".pdf")):
        if p.is_file():
            rot = rotate_backups(p, keep, SHEET_BACKUP_SUFFIX)
            res.backups.append(str(rot.backup))
            res.notes += rot.warnings


def write_sheet(
    board_path: str | Path,
    cfg: BoardConfig,
    out_path: str | Path,
    netlist: str | None = None,
    pdf: bool = True,
    png: bool = False,
    chrome: str | None = None,
    date: str | None = None,
) -> SheetResult:
    """Write ``<out>.html`` (and ``.pdf``, the two view SVGs, ``.cuts.csv`` and optional PNGs)."""
    out_path = Path(out_path)
    if out_path.suffix.lower() not in (".html", ".htm"):
        out_path = out_path.with_suffix(".html")
    model = sheet_model(board_path, cfg, netlist, date)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    res = SheetResult(model)
    html = render_html(model)
    _keep_previous(out_path, html, cfg.backup_keep, res)
    out_path.write_text(html, encoding="utf-8")
    res.outputs.append(str(out_path))
    svgs = {
        "copper": copper_svg(model, views(model, True)[0]),
        "component": component_svg(model, views(model, False)[0]),
    }
    for side, svg in svgs.items():
        p = out_path.with_suffix(f".{side}.svg")
        p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + _standalone(svg), encoding="utf-8")
        res.outputs.append(str(p))
    csv_path = out_path.with_suffix(".cuts.csv")
    csv_path.write_text(cuts_csv(model), encoding="utf-8")
    res.outputs.append(str(csv_path))
    if pdf or png:
        exe = find_chrome(chrome)
        if exe is None:
            res.notes.append("no Chrome/Chromium found: HTML only (print it from a browser for a PDF)")
        else:
            if pdf:
                try:
                    pdf_path = out_path.with_suffix(".pdf")
                    html_to_pdf(out_path, pdf_path, exe)
                    res.outputs.append(str(pdf_path))
                except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                    res.notes.append(f"PDF not written ({exc}); print the HTML from a browser instead")
            if png:
                for side, svg in svgs.items():
                    p = out_path.with_suffix(f".{side}.png")
                    try:
                        svg_to_png(svg, p, exe)
                        res.outputs.append(str(p))
                    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
                        res.notes.append(f"{p.name} not written ({exc})")
    return res


def _standalone(svg: str) -> str:
    """An SVG with the sheet's styles inlined, so it renders on its own."""
    rules = CSS.split("svg.view", 1)[1].split("}", 1)[1]
    head_end = svg.index(">") + 1
    return svg[:head_end] + f"<style>{rules}</style>" + svg[head_end:]
