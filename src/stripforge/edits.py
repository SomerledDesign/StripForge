# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Your own cuts and links: kept as they are, with StripForge filling in only what is missing.

Two ways to give them:

* **In the board.** Build again from a board that carries StripForge cut markers
  (``StripForge:CUT_Hole`` / ``CUT_Knife``) and ``W`` link footprints, e.g. the built
  ``<name>-stripforge.kicad_pcb`` after moving things in pcbnew (or with those footprints copied
  into the placement board). When the markers or the placed links differ from what StripForge
  would do on its own, every marker is taken as a cut exactly where it sits and every ``W`` whose
  two pads sit on holes as a link exactly where it sits ("locked"). A missing cut that would
  short two nets is put back (with a warning); links are only proposed for what is still unjoined.
  ``respect_edits = false`` ignores them and plans from scratch.
* **In stripboard.toml**, a ``[manual]`` table (always applied)::

      [manual]
      links = ["J16-T16"]        # a wire link from hole J16 to hole T16 (any two holes)
      cuts = ["J15", "C34-C35"]  # a hole cut at J15, a knife cut between C34 and C35
      no_cut = ["J17"]           # never cut here (StripForge picks another spot in the gap)

A user cut replaces StripForge's cut in the same gap (it is never slid). A user cut inside a
stretch of one net splits that net, which then needs a link. Problems are reported: a cut or link
on a hole with a pin in it, a link that would short two nets, a missing cut that would short two
nets, a split that can't be joined.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .grid import Grid, Node, hole_label, parse_hole

CUT_HOLE_ID = "StripForge:CUT_Hole"
CUT_KNIFE_ID = "StripForge:CUT_Knife"
_W_REF = re.compile(r"^W(\d+)$")


@dataclass
class CutSpec:
    row: int
    col: float  # integer: hole cut at that hole; x.5: knife cut between x and x+1
    style: str  # "hole" | "knife"
    source: str  # "CUT12 in the board", "[manual] cuts"
    # who put a board marker where it is: "you" (moved or added by hand), "stripforge" (left where
    # StripForge put it) or "unknown" (a marker from before 0.2.0; see own_marker_key)
    placed: str = "you"

    @property
    def label(self) -> str:
        c = int(self.col)
        if self.style == "hole":
            return hole_label(self.row, c)
        return f"{hole_label(self.row, c)}-{hole_label(self.row, c + 1)}"

    @property
    def key(self) -> tuple[int, float, str]:
        return (self.row, self.col, self.style)


@dataclass
class LinkSpec:
    n1: Node
    n2: Node
    source: str  # "W18 in the board", "[manual] links"
    ref: str = ""  # the W reference, for a link placed in the board
    footprint: str = ""  # its footprint (lib id), for a link placed in the board
    net: str | None = None  # its pads' net (from the schematic), for a link placed in the board
    why: str = ""  # why it can't be used (set when it is rejected)

    @property
    def label(self) -> str:
        return f"{hole_label(self.n1.row, self.n1.col)}-{hole_label(self.n2.row, self.n2.col)}"


@dataclass
class Edits:
    cuts: list[CutSpec] = field(default_factory=list)
    no_cut: list[CutSpec] = field(default_factory=list)
    links: list[LinkSpec] = field(default_factory=list)
    # the cuts are the board's complete set (from markers): a gap with none of them in it is a
    # cut the user removed, which would short two nets
    complete: bool = False
    warnings: list[str] = field(default_factory=list)
    won: list = field(default_factory=list)  # (link name, dropped cut) from links_win

    def __bool__(self) -> bool:
        return bool(self.cuts or self.no_cut or self.links)

    def merged(self, other: Edits) -> Edits:
        return Edits(
            self.cuts + other.cuts,
            self.no_cut + other.no_cut,
            self.links + other.links,
            self.complete or other.complete,
            self.warnings + other.warnings,
        )


def parse_cut(text: str, source: str) -> CutSpec:
    """``J15`` (hole cut) or ``J15-J16`` / ``J15|J16`` (knife cut between two neighbouring holes)."""
    parts = [p for p in re.split(r"[-|]", str(text)) if p.strip()]
    if len(parts) == 1:
        n = parse_hole(parts[0])
        return CutSpec(n.row, float(n.col), "hole", source)
    if len(parts) == 2:
        a, b = sorted((parse_hole(parts[0]), parse_hole(parts[1])))
        if a.row != b.row or b.col != a.col + 1:
            raise ValueError(
                f"knife cut {text!r}: the two holes must be neighbours on one strip, e.g. J15-J16"
            )
        return CutSpec(a.row, a.col + 0.5, "knife", source)
    raise ValueError(
        f"not a cut: {text!r} (expected a hole like J15, or two neighbouring holes like J15-J16)"
    )


def parse_link(text: str, source: str) -> LinkSpec:
    """``J16-T16``: a wire link between two holes."""
    parts = [p for p in re.split(r"[-|]|->", str(text)) if p.strip()]
    if len(parts) != 2:
        raise ValueError(f"not a link: {text!r} (expected two holes, e.g. J16-T16)")
    a, b = parse_hole(parts[0]), parse_hole(parts[1])
    if a == b:
        raise ValueError(f"link {text!r} starts and ends in the same hole")
    return LinkSpec(*sorted((a, b)), source)


MANUAL_KEYS = ("links", "cuts", "no_cut")


def manual_from_dict(data: dict) -> dict[str, list[str]]:
    """Validate the ``[manual]`` table (labels are parsed here so a typo fails at load time)."""
    if not isinstance(data, dict):
        raise ValueError("[manual] must be a table")
    unknown = sorted(set(data) - set(MANUAL_KEYS))
    if unknown:
        raise ValueError(f"unknown [manual] key(s): {', '.join(unknown)} (expected links, cuts, no_cut)")
    out: dict[str, list[str]] = {}
    for key in MANUAL_KEYS:
        vals = data.get(key, [])
        if not isinstance(vals, list) or not all(isinstance(v, str) for v in vals):
            raise ValueError(f'[manual] {key} must be a list of strings, e.g. {key} = ["J15"]')
        for v in vals:
            (parse_link if key == "links" else parse_cut)(v, "")
        out[key] = [str(v).strip() for v in vals]
    return out


def from_config(cfg) -> Edits:
    manual = getattr(cfg, "manual", None) or {}
    return Edits(
        cuts=[parse_cut(t, f"[manual] cuts {t!r}") for t in manual.get("cuts", [])],
        no_cut=[parse_cut(t, f"[manual] no_cut {t!r}") for t in manual.get("no_cut", [])],
        links=[parse_link(t, f"[manual] links {t!r}") for t in manual.get("links", [])],
    )


def own_marker_key(cut_id: str, label: str) -> str:
    """The uuid key of a cut marker StripForge placed itself: its cut id and where it put it
    (``cut/X12@K13``). A marker you move keeps its uuid but no longer sits at that label, and one
    you add or copy gets a new uuid, so either counts as yours on the next build."""
    return f"cut/{cut_id}@{label}"


def _snap(grid: Grid, x: int, y: int, half: bool) -> tuple[int, float, int]:
    """Nearest (row, col) to board point ``(x, y)``; with ``half``, the nearest half hole along the
    strip (a knife cut sits between two holes). Also the distance in nm from that spot."""
    ox, oy = grid.hole_xy(Node(0, 0))
    p = grid.pitch_nm
    row = round((y - oy) / p)
    fc = (x - ox) / p
    col = math.floor(fc) + 0.5 if half else float(round(fc))
    dx = x - (ox + col * p)
    dy = y - (oy + row * p)
    return row, col, int((dx * dx + dy * dy) ** 0.5)


def from_board(board, grid: Grid, markers: list, link_fps: list, tol_nm: int) -> Edits:
    """The cuts the markers mark and the links the ``W`` footprints on holes make."""
    ed = Edits(complete=bool(markers))
    for fp in markers:
        knife = fp.lib_id == CUT_KNIFE_ID
        row, col, off = _snap(grid, fp.x_nm, fp.y_nm, knife)
        spec = CutSpec(row, float(col), "knife" if knife else "hole", f"{fp.ref} in the board")
        if not (0 <= row < grid.rows and 0 <= col <= grid.cols - 1):
            ed.warnings.append(f"{fp.ref}: cut marker is off the stripboard; ignored")
            continue
        if off > max(tol_nm, grid.pitch_nm // 4):
            ed.warnings.append(
                f"{fp.ref}: cut marker is {off / 1e6:.2f} mm off {spec.label}; taken as {spec.label}"
            )
        spec.placed = _placed_by(fp, spec)
        ed.cuts.append(spec)
    for fp in link_fps:
        nodes = []
        for pad in fp.pads:
            n = grid.nearest(pad.x_nm, pad.y_nm)
            x, y = grid.hole_xy(n)
            if grid.contains(n) and abs(x - pad.x_nm) <= tol_nm and abs(y - pad.y_nm) <= tol_nm:
                nodes.append(n)
        if len(fp.pads) != 2 or len(nodes) != 2 or nodes[0] == nodes[1]:
            continue  # not placed on two holes yet: pass 2 places it from the proposal
        nets = {p.net for p in fp.pads if p.net}
        a, b = sorted(nodes)
        ed.links.append(
            LinkSpec(
                a, b, f"{fp.ref} in the board", fp.ref, fp.lib_id, nets.pop() if len(nets) == 1 else None
            )
        )
    return ed


def _placed_by(fp, spec: CutSpec) -> str:
    from .sexpr import atom, find
    from .writer import _u  # (writer imports this module)

    node = find(fp.node, "uuid") if fp.node is not None else None
    uid = atom(node, 1) if node is not None else None
    num = fp.ref[3:] if fp.ref.startswith("CUT") else ""
    if not uid or not num.isdigit():
        return "you"
    if uid == _u(own_marker_key(f"X{num}", spec.label)):
        return "stripforge"
    if uid == _u(f"cut/X{num}"):  # written before 0.2.0: the uuid doesn't say where it was put
        return "unknown"
    return "you"


def links_win(ed: Edits) -> Edits:
    """A hole cut under an end of one of your links: the link wins. The cut is dropped (with a
    warning) and that hole is kept uncut; StripForge cuts the strip elsewhere only where two nets
    would otherwise short (``complete``)."""
    ends = {(n.row, n.col): lk for lk in ed.links for n in (lk.n1, lk.n2)}
    ed.won = []
    keep = []
    for c in ed.cuts:
        lk = ends.get((c.row, int(c.col))) if c.style == "hole" else None
        if lk is None:
            keep.append(c)
            continue
        name = lk.ref or lk.label
        ed.warnings.append(
            f"{c.source}: hole cut at {c.label} is under an end of your link {name} ({lk.label}); the link "
            "wins, so the cut is dropped (StripForge cuts this strip elsewhere only if two nets would short)"
        )
        ed.no_cut.append(CutSpec(c.row, c.col, "hole", f"end of your link {name}"))
        ed.won.append((name, c))
    ed.cuts = keep
    return ed


def ref_number(ref: str) -> int:
    m = _W_REF.match(ref)
    return int(m[1]) if m else 0
