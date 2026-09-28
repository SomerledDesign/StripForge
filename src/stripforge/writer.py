# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""`stripforge build`: write strip copper, cut markers and placed links into a board (M2 part B).

Starting from the placement board (after F8), the build:

1. snaps the parts (the best-fit shift of ``stripforge snap``);
2. splits the strips by net and proposes wire links (pass 1, :mod:`links`), sliding a cut where
   that lets a link land;
3. places any ``W`` link footprints already on the board (pass 2, after F8) on their holes;
4. writes, for every strip piece, one ``B.Cu`` track per pair of neighbouring holes, hole centre
   to hole centre, ``strip_width_mm`` wide, on the piece's net. Pieces with no net are written as
   copper with no net: uncut bare strip is physically there, and a no-net track touching no pad
   raises only KiCad's ``track_dangling`` warning, which the DRC wrapper filters. A one-hole piece
   has no track (its copper is just the pad, or bare copper round an empty hole).
5. leaves real gaps at the cuts (no copper touches a cut hole; a knife cut removes the track
   between its two holes) and puts a ``StripForge:CUT_Hole`` / ``StripForge:CUT_Knife`` marker at
   each, embedded in the board as KiCad does (refs ``CUT1``…, same number as the cut id ``X1``…;
   board-only, excluded from BOM and position files), so the board loads and passes DRC in
   ``kicad-cli`` without the library configured.

Existing copper: a track, arc or via that StripForge did not write is refused (the build expects the
placement board); StripForge's own strips and cut markers from an earlier build are removed and
rewritten, so a board can be rebuilt after F8. UUIDs are deterministic (uuid5), so rebuilding an
unchanged board gives a byte-identical file. The input board is never overwritten.
"""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import links as links_mod
from . import resources
from .analyze import Analysis, analyze_board, apply_best_fit, make_grid
from .board import Board, Footprint, _norm_angle, load_board, rotate_nm, save_board
from .config import BoardConfig
from .grid import Grid, Node
from .sexpr import Sym, atom, find, find_all, head, loads, mm_to_nm, nm_to_mm_text

NS = uuid.UUID("7b1e6c1a-51f0-4e43-9b3a-5354524950f1")  # StripForge output namespace (uuid5)
LINK_REF = re.compile(r"^W\d+$")
CUT_LIB = f"{resources.LIB_NICKNAME}:CUT_"
LINK_LIB = f"{resources.LIB_NICKNAME}:Link_"
TRACK_HEADS = ("segment", "arc", "via")


class BuildError(ValueError):
    """The board can't be built (refused input, conflicts, missing library)."""


@dataclass
class LinkPlacement:
    ref: str
    status: str  # placed | missing | extra | wrong-footprint | wrong-net | back-side
    detail: str = ""


@dataclass
class BuildResult:
    analysis: Analysis
    plan: links_mod.LinkPlan
    moves: dict[str, tuple[int, int]]
    segments: int = 0
    segments_no_net: int = 0
    cut_markers: int = 0
    placements: list[LinkPlacement] = field(default_factory=list)
    removed_previous: tuple[int, int] = (0, 0)  # (tracks, cut markers) from an earlier build
    warnings: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)

    @property
    def placed(self) -> list[LinkPlacement]:
        return [p for p in self.placements if p.status == "placed"]

    @property
    def link_problems(self) -> list[LinkPlacement]:
        return [p for p in self.placements if p.status != "placed"]

    @property
    def pass2(self) -> bool:
        return bool(self.placements)

    @property
    def ok(self) -> bool:
        return self.plan.ok and not self.link_problems and not self.analysis.rejected


# --- helpers ---------------------------------------------------------------------------------


def _u(key: str) -> str:
    return str(uuid.uuid5(NS, key))


def _mm(nm: int) -> Sym:
    return Sym(nm_to_mm_text(nm))


def _plain(node):
    """A parsed node as plain lists (no source spans), so it is rendered in KiCad's style."""
    return [_plain(c) if isinstance(c, list) else c for c in node]


def is_link(fp: Footprint) -> bool:
    return bool(LINK_REF.match(fp.ref)) or fp.lib_id.startswith(LINK_LIB)


def strip_uuids(grid: Grid) -> set[str]:
    return {_u(f"strip/{r}/{c}") for r in range(grid.rows) for c in range(grid.cols)}


def clip_to_outline(board: Board, cfg: BoardConfig, grid: Grid) -> tuple[BoardConfig, list[str]]:
    """Limit a configured grid to the holes inside Edge.Cuts (copper must stay on the board)."""
    if board.outline is None:
        return cfg, []
    x0, y0, x1, y1 = board.outline
    half = grid.pitch_nm // 2
    cols = min(grid.cols, max(0, (x1 - half - grid.origin_x_nm) // grid.pitch_nm + 1))
    rows = min(grid.rows, max(0, (y1 - half - grid.origin_y_nm) // grid.pitch_nm + 1))
    inside = grid.origin_x_nm - half >= x0 and grid.origin_y_nm - half >= y0
    if (cols, rows) == (grid.cols, grid.rows) or not inside or cols < 1 or rows < 1:
        return cfg, []
    new = replace(cfg, cols=cols, rows=rows, origin_mm=(grid.origin_x_nm / 1e6, grid.origin_y_nm / 1e6))
    last = Grid(grid.origin_x_nm, grid.origin_y_nm, cols, rows, grid.pitch_nm).span_label
    return new, [
        f"build: the configured {grid.cols}x{grid.rows} grid extends past Edge.Cuts; copper is written "
        f"for the holes inside the outline only ({last})"
    ]


# --- embedding footprints ---------------------------------------------------------------------


def embed_footprint(
    name: str,
    ref: str,
    x_nm: int,
    y_nm: int,
    key: str,
    nets: dict[str, str] | None = None,
    library: str | Path | None = None,
) -> list:
    """A library footprint as KiCad embeds it in a board: ``StripForge:<name>`` at ``(x, y)``.

    The ``version``/``generator`` header goes, ``uuid`` and ``at`` follow the layer, the
    Reference is set, pad nets are added and every uuid is replaced by a deterministic one
    derived from ``key``.
    """
    text = resources.footprint_file(name, library).read_text(encoding="utf-8")
    node = _plain(loads(text))
    node[1] = f"{resources.LIB_NICKNAME}:{name}"
    node[:] = [c for c in node if head(c) not in ("version", "generator")]
    counter = iter(range(1, 10_000))

    def renew(n: list) -> None:
        for c in n:
            if isinstance(c, list):
                if head(c) == "uuid" and len(c) > 1:
                    c[1] = _u(f"{key}/{next(counter)}")
                else:
                    renew(c)

    renew(node)
    layer_i = next(i for i, c in enumerate(node) if head(c) == "layer")
    node[layer_i + 1 : layer_i + 1] = [
        [Sym("uuid"), _u(key)],
        [Sym("at"), _mm(x_nm), _mm(y_nm)],
    ]
    for prop in find_all(node, "property"):
        if atom(prop, 1) == "Reference":
            prop[2] = ref
    for pad in find_all(node, "pad"):
        net = (nets or {}).get(atom(pad, 1) or "")
        if net:
            at_uuid = next((i for i, c in enumerate(pad) if head(c) == "uuid"), len(pad))
            pad.insert(at_uuid, [Sym("net"), net])
    return node


def _segment(x0: int, y0: int, x1: int, y1: int, width_nm: int, net: str | None, key: str) -> list:
    seg = [
        Sym("segment"),
        [Sym("start"), _mm(x0), _mm(y0)],
        [Sym("end"), _mm(x1), _mm(y1)],
        [Sym("width"), _mm(width_nm)],
        [Sym("layer"), "B.Cu"],
    ]
    if net:
        seg.append([Sym("net"), net])
    seg.append([Sym("uuid"), _u(key)])
    return seg


def set_rotation(fp: Footprint, angle: float) -> None:
    """Rotate a footprint in place to ``angle`` about its anchor (90° steps; pads follow)."""
    delta = _norm_angle(angle - fp.angle)
    if delta == 0 or fp.node is None:
        return
    at = find(fp.node, "at")
    while len(at) < 4:
        at.append(Sym("0"))
    at[3] = Sym(f"{_norm_angle(angle):g}")
    for child in fp.node:
        if head(child) in ("property", "fp_text", "pad"):
            cat = find(child, "at")
            if cat is not None:
                old = float(cat[3]) if len(cat) > 3 else 0.0
                new = _norm_angle(old + delta)
                if len(cat) > 3:
                    cat[3] = Sym(f"{new:g}")
                elif new:
                    cat.append(Sym(f"{new:g}"))
    fp.angle = _norm_angle(angle)
    fp.pads = [
        replace(p, x_nm=fp.x_nm + rotate_nm(p.local_x_nm, p.local_y_nm, fp.angle)[0],
                y_nm=fp.y_nm + rotate_nm(p.local_x_nm, p.local_y_nm, fp.angle)[1])
        for p in fp.pads
    ]  # fmt: skip


def place_at(fp: Footprint, x_nm: int, y_nm: int, angle: float = 0.0) -> None:
    set_rotation(fp, angle)
    fp.move(x_nm - fp.x_nm, y_nm - fp.y_nm)


# --- build -----------------------------------------------------------------------------------


def _existing_output(board: Board, grid: Grid) -> tuple[list[list], list[Footprint], list[str]]:
    """StripForge's own tracks and cut markers, and descriptions of foreign copper."""
    root = board.doc.root
    ours = strip_uuids(grid)
    own_tracks, foreign = [], []
    for node in root:
        if head(node) in TRACK_HEADS:
            if head(node) == "segment" and atom(find(node, "uuid"), 1) in ours:
                own_tracks.append(node)
            else:
                layer = atom(find(node, "layer"), 1) or "?"
                foreign.append(f"{head(node)} on {layer}")
    cuts = [fp for fp in board.footprints if fp.lib_id.startswith(CUT_LIB)]
    return own_tracks, cuts, foreign


@dataclass
class Prepared:
    """A board analysed exactly as ``stripforge build`` sees it (shared with ``stripforge sheet``)."""

    analysis: Analysis
    plan: links_mod.LinkPlan
    moves: dict[str, tuple[int, int]]
    config: BoardConfig
    link_fps: list[Footprint]
    own_tracks: list[list]
    old_cuts: list[Footprint]
    foreign: list[str]
    warnings: list[str] = field(default_factory=list)


def prepare(board: Board, cfg: BoardConfig, netlist: str | None = None) -> Prepared:
    """Snap, split and plan links on ``board`` the way ``build`` does, without writing anything.

    StripForge's own strips and cut markers from an earlier build are taken out of the in-memory
    board (so a built board gives the same plan as its placement board) and ``W`` link footprints
    are left out of the analysis. Raises BuildError if the board has conflicts.
    """
    grid, _ = make_grid(board, cfg)
    own_tracks, old_cuts, foreign = _existing_output(board, grid)
    root = board.doc.root
    for node in own_tracks:
        root.remove(node)
    for fp in old_cuts:
        root.remove(fp.node)
        board.footprints.remove(fp)
    link_fps = [fp for fp in board.footprints if is_link(fp)]
    cfg = replace(cfg, offboard_refs=sorted(set(cfg.offboard_refs) | {fp.ref for fp in link_fps}))
    cfg, clip_warnings = clip_to_outline(board, cfg, grid)

    a = analyze_board(board, cfg, netlist)
    a, moves = apply_best_fit(a)
    plan = links_mod.propose(a)
    if a.conflicts:
        raise BuildError("the board has conflicts; fix them first:\n  " + "\n  ".join(a.conflicts))
    return Prepared(a, plan, moves, cfg, link_fps, own_tracks, old_cuts, foreign, clip_warnings)


def build(
    board_path: str | Path,
    cfg: BoardConfig,
    out_path: str | Path,
    netlist: str | None = None,
    library: str | Path | None = None,
    rules: str | Path | None = None,
) -> BuildResult:
    """Build ``out_path`` from ``board_path`` (see the module docstring). Raises BuildError."""

    board_path, out_path = Path(board_path), Path(out_path)
    if out_path.resolve() == board_path.resolve():
        raise BuildError("-o must name a new file, not the input board")
    library = resources.library_dir(library)
    rules = resources.rules_file(rules)
    board = load_board(board_path)
    grid, _ = make_grid(board, cfg)
    _, _, foreign = _existing_output(board, grid)
    if foreign:
        raise BuildError(
            f"the input board already has {len(foreign)} track/via item(s) that StripForge did not write "
            f"({', '.join(sorted(set(foreign)))}); build expects the placement board with no copper tracks"
        )
    prep = prepare(board, cfg, netlist)
    a, plan, moves, cfg, link_fps = prep.analysis, prep.plan, prep.moves, prep.config, prep.link_fps
    root = board.doc.root

    res = BuildResult(
        analysis=a, plan=plan, moves=moves, removed_previous=(len(prep.own_tracks), len(prep.old_cuts))
    )
    res.warnings += prep.warnings
    res.warnings += resources.check_rules_width(rules.read_text(encoding="utf-8"), cfg.strip_width_mm)

    # pass 2: place the W footprints that F8 brought in
    by_ref = {lk.ref_hint: lk for lk in plan.links}
    for fp in sorted(link_fps, key=lambda f: (len(f.ref), f.ref)):
        lk = by_ref.get(fp.ref)
        if lk is None:
            res.placements.append(LinkPlacement(fp.ref, "extra", "not in the link proposal; remove it"))
            continue
        want = lk.footprint
        if fp.lib_id.split(":")[-1] != want.split(":")[-1]:
            res.placements.append(
                LinkPlacement(fp.ref, "wrong-footprint", f"is {fp.lib_id}; the proposal needs {want}")
            )
            continue
        nets = {p.net for p in fp.pads}
        if nets != {lk.net}:
            got = ", ".join(sorted(n or "(no net)" for n in nets))
            res.placements.append(LinkPlacement(fp.ref, "wrong-net", f"pins are on {got}; expected {lk.net}"))
            continue
        if fp.layer != "F.Cu":
            res.placements.append(LinkPlacement(fp.ref, "back-side", "flip it to the front (F.Cu)"))
            continue
        x, y = a.grid.hole_xy(Node(lk.row_a, lk.col))
        place_at(fp, x, y, 0.0)
        res.placements.append(LinkPlacement(fp.ref, "placed", f"{lk.start} -> {lk.end}"))
    if link_fps:
        present = {fp.ref for fp in link_fps}
        for lk in plan.links:
            if lk.ref_hint not in present:
                res.placements.append(
                    LinkPlacement(lk.ref_hint, "missing", f"{lk.start} -> {lk.end} {lk.footprint} [{lk.net}]")
                )

    # strips
    width = mm_to_nm(cfg.strip_width_mm)
    new_nodes: list[list] = []
    for p in a.split.pieces:
        for c in range(p.col_start, p.col_end):
            x0, y0 = a.grid.hole_xy(Node(p.row, c))
            x1, y1 = a.grid.hole_xy(Node(p.row, c + 1))
            new_nodes.append(_segment(x0, y0, x1, y1, width, p.net, f"strip/{p.row}/{c}"))
            res.segments += 1
            res.segments_no_net += p.net is None
    # cut markers
    markers: list[list] = []
    for cut in a.split.cuts:
        c = int(cut.col)
        x, y = a.grid.hole_xy(Node(cut.row, c))
        if cut.style == "knife":
            x += a.grid.pitch_nm // 2
        name = "CUT_Hole" if cut.style == "hole" else "CUT_Knife"
        markers.append(embed_footprint(name, f"CUT{cut.id[1:]}", x, y, f"cut/{cut.id}", library=library))
    res.cut_markers = len(markers)

    last_fp = max((i for i, n in enumerate(root) if head(n) == "footprint"), default=len(root) - 1)
    root[last_fp + 1 : last_fp + 1] = markers
    tail = len(root)
    if root and head(root[-1]) == "embedded_fonts":
        tail -= 1
    root[tail:tail] = new_nodes

    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.warnings += _compare_saved(link_file(out_path, ".json"), plan)
    save_board(board, out_path)
    res.outputs.append(str(out_path))
    dru = out_path.with_suffix(".kicad_dru")
    shutil.copyfile(rules, dru)
    res.outputs.append(str(dru))
    res.outputs += write_link_files(plan, out_path)

    # re-read what was written: it must parse and carry exactly the tracks we meant to write
    check = load_board(out_path)
    written = [n for n in check.doc.root if head(n) == "segment"]
    if len(written) != res.segments:
        raise BuildError(f"internal error: wrote {res.segments} tracks but read back {len(written)}")
    return res


# --- link files --------------------------------------------------------------------------------


def link_file(out_path: str | Path, suffix: str) -> Path:
    """``<board>.links.json`` / ``.csv`` / ``.txt`` next to the output board."""
    p = Path(out_path)
    return p.with_name(p.stem + ".links" + suffix)


def write_link_files(plan: links_mod.LinkPlan, out_path: str | Path) -> list[str]:
    """Write the link proposal (JSON, CSV, and a text report with schematic instructions)."""
    out = []
    for suffix, text in (
        (".json", links_mod.to_json(plan, Path(out_path).name)),
        (".csv", links_mod.to_csv(plan)),
        (".txt", links_mod.format_text(plan)),
    ):
        p = link_file(out_path, suffix)
        p.write_text(text, encoding="utf-8")
        out.append(str(p))
    return out


def _compare_saved(path: Path, plan: links_mod.LinkPlan) -> list[str]:
    """Warn when a saved proposal (from pass 1) differs from the one just recomputed."""
    import json

    if not path.exists():
        return []
    try:
        saved = {d["ref"]: (d["net"], d["from"], d["to"]) for d in json.loads(path.read_text())["links"]}
    except (OSError, ValueError, KeyError, TypeError):
        return [f"links: could not read the saved proposal {path.name}; it will be replaced"]
    now = {lk.ref_hint: (lk.net, lk.start, lk.end) for lk in plan.links}
    if saved == now:
        return []
    out = [f"links: the proposal differs from the saved {path.name} (the placement changed since pass 1?)"]
    for ref in sorted(set(saved) | set(now), key=lambda r: (len(r), r)):
        a, b = saved.get(ref), now.get(ref)
        if a != b:
            fmt = lambda v: f"{v[1]}->{v[2]} [{v[0]}]" if v else "none"  # noqa: E731
            out.append(f"links: {ref} was {fmt(a)}, now {fmt(b)}")
    return out
