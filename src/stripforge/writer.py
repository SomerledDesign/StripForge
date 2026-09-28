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
6. draws every stripboard hole (``draw_holes``, on by default): one board-only
   ``StripForge:Holes`` footprint per strip (ref ``SF_HOLES_<strip>``), with a plated B.Cu pad
   (``hole_drill_mm`` drill, strip-width copper, pad number = hole label) on the strip piece's
   net at every grid hole that has no part pin or link in it; a hole cut is drawn as a bare
   non-plated hole. So the built board looks like real stripboard in KiCad and the 3D viewer. A
   hole too close to another pad's drill (a slot, a mounting hole) is left out.

Existing copper: a track, arc or via that StripForge did not write is refused (the build expects the
placement board); StripForge's own strips and cut markers from an earlier build are removed and
rewritten, so a board can be rebuilt after F8. UUIDs are deterministic (uuid5), so rebuilding an
unchanged board gives a byte-identical file.

Output (``output`` in the config): ``"in_place"`` (default) writes into the board itself, the
project's own ``<name>.kicad_pcb``, after saving it as it is to ``<name>-pre-stripbuild.kicad_pcb``
(older backups rotate to ``-1``, ``-2``, ...; to undo the latest build, delete the built board and
rename the unnumbered backup back);
``"separate"`` writes ``<name>-stripforge.kicad_pcb`` and leaves the board alone. The link files
are ``<name>-stripforge.links.json/.csv/.txt`` either way.
"""

from __future__ import annotations

import os
import re
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import edits as edits_mod
from . import links as links_mod
from . import resources
from . import stretch as stretch_mod
from .analyze import Analysis, analyze_board, apply_best_fit, make_grid
from .board import (
    HOLES_LIB_ID,
    Board,
    Footprint,
    _norm_angle,
    _parse_footprint,
    load_board,
    rotate_nm,
    save_board,
)
from .config import BoardConfig
from .grid import Grid, Node, row_label
from .sexpr import Sym, atom, find, find_all, head, loads, mm_to_nm, nm_to_mm_text

NS = uuid.UUID("7b1e6c1a-51f0-4e43-9b3a-5354524950f1")  # StripForge output namespace (uuid5)
LINK_REF = re.compile(r"^W\d+$")
CUT_LIB = f"{resources.LIB_NICKNAME}:CUT_"
LINK_LIB = f"{resources.LIB_NICKNAME}:Link_"
TRACK_HEADS = ("segment", "arc", "via")
HOLES_REF = "SF_HOLES_"
HOLE_TO_HOLE_NM = 250_000  # KiCad's default minimum hole-to-hole distance
HOLE_CLEARANCE_NM = 250_000  # KiCad's default copper-to-hole clearance
BUILT_SUFFIX = "-stripforge"  # output = "separate": <name>-stripforge.kicad_pcb; also the link files
BACKUP_SUFFIX = "-pre-stripbuild"  # output = "in_place": <name>-pre-stripbuild.kicad_pcb


def base_stem(board: str | Path) -> str:
    """``fixture`` for ``fixture.kicad_pcb``, ``fixture-stripforge.kicad_pcb`` and the backups."""
    s = re.sub(r"(?<=.)" + re.escape(BACKUP_SUFFIX) + r"-\d+$", "", Path(board).stem)
    for suffix in (BUILT_SUFFIX, BACKUP_SUFFIX):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def separate_output(board: str | Path) -> Path:
    """``<name>-stripforge.kicad_pcb`` next to ``board`` (output = "separate")."""
    p = Path(board)
    return p.with_name(base_stem(p) + BUILT_SUFFIX + ".kicad_pcb")


def backup_path(board: str | Path, n: int = 0, suffix: str = BACKUP_SUFFIX) -> Path:
    """``<name>-pre-stripbuild.kicad_pcb`` (the board just before the latest in-place build), or
    ``<name>-pre-stripbuild-<n>.kicad_pcb`` (n builds further back)."""
    p = Path(board)
    return p.with_name(p.stem + suffix + (f"-{n}" if n else "") + p.suffix)


def numbered_backups(board: str | Path, suffix: str = BACKUP_SUFFIX) -> dict[int, Path]:
    """The existing ``<name>-pre-stripbuild-<n>`` files next to ``board``, by n."""
    p = Path(board)
    pat = re.compile(re.escape(p.stem + suffix) + r"-([1-9]\d*)" + re.escape(p.suffix) + "$")
    found = {}
    if p.parent.is_dir():
        for f in p.parent.iterdir():
            m = pat.match(f.name)
            if m and f.is_file():
                found[int(m.group(1))] = f
    return found


def backup_note(res: BuildResult, board: str | Path) -> str:
    """How the backups rotated and how to undo, for the CLI and plugin reports."""
    name = Path(board).name
    parts = []
    if res.backups_shifted:
        older = [new for _, new in res.backups_shifted]
        parts.append(f"Older backups moved up one: {', '.join(reversed(older))}.")
    if res.backups_pruned:
        parts.append(f"Deleted by backup_keep: {', '.join(res.backups_pruned)}.")
    parts.append(
        f"To undo this build: delete {name} and rename {Path(res.backup).name} to {name} "
        f"(-1 is the board before the previous build, and so on)."
    )
    return " ".join(parts)


@dataclass
class Rotation:
    backup: Path  # the new unnumbered backup
    shifted: list[tuple[str, str]] = field(default_factory=list)  # (old name, new name), in order
    pruned: list[str] = field(default_factory=list)  # deleted by backup_keep
    warnings: list[str] = field(default_factory=list)


def rotate_backups(board: str | Path, keep: int = 0, suffix: str = BACKUP_SUFFIX) -> Rotation:
    """Back ``board`` up like logrotate before an in-place build: shift ``-pre-stripbuild-<n>`` to
    ``-<n+1>`` (highest first), the unnumbered backup to ``-1``, then copy the board as it is now
    to the unnumbered name. ``keep`` > 0 then deletes the oldest numbered backups beyond ``keep``
    files in all. Nothing is ever overwritten: a rename whose target exists, or that fails, raises
    :class:`BuildError` before the board is copied (or written)."""
    board = Path(board)
    head_backup = backup_path(board, 0, suffix)
    rot = Rotation(backup=head_backup)
    if head_backup.exists():
        chain = numbered_backups(board, suffix)
        moves = [(chain[n], backup_path(board, n + 1, suffix)) for n in sorted(chain, reverse=True)]
        moves.append((head_backup, backup_path(board, 1, suffix)))
        for src, dst in moves:
            if dst.exists():
                raise BuildError(
                    f"backup rotation: {dst.name} already exists; not overwriting it (nothing built)"
                )
            try:
                os.rename(src, dst)
            except OSError as exc:
                raise BuildError(
                    f"backup rotation: could not rename {src.name} to {dst.name} ({exc}); stopped before "
                    "writing the board"
                ) from exc
            rot.shifted.append((src.name, dst.name))
    try:
        shutil.copy2(board, head_backup)
    except OSError as exc:
        raise BuildError(f"could not back the board up to {head_backup.name} ({exc}); nothing built") from exc
    if keep > 0:
        for n, f in sorted(numbered_backups(board, suffix).items(), reverse=True):
            if n < keep:
                break
            try:
                f.unlink()
                rot.pruned.append(f.name)
            except OSError as exc:
                rot.warnings.append(f"backup_keep: could not delete {f.name} ({exc})")
    return rot


def resolve_output(
    board: str | Path, cfg: BoardConfig, explicit: str | Path | None = None
) -> tuple[Path, bool]:
    """(output board, in place?) for ``board``: ``explicit`` if given, else the board itself
    (``output = "in_place"``) or ``<name>-stripforge.kicad_pcb`` (``"separate"``; a board that
    already is a ``-stripforge`` output is rebuilt in place either way)."""
    board = Path(board)
    if explicit is not None:
        out = Path(explicit)
        return out, out.resolve() == board.resolve()
    if cfg.output == "in_place" or board.stem.endswith(BUILT_SUFFIX):
        return board, True
    return separate_output(board), False


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
    holes_drawn: int = 0  # stripboard hole pads (plated and bare) written
    stretches: list = field(default_factory=list)  # lead-stretch suggestions (report only)
    backup: str | None = None  # in-place build: <name>-pre-stripbuild.kicad_pcb (made by this build)
    backups_shifted: list = field(default_factory=list)  # (old, new) names rotated up by one
    backups_pruned: list = field(default_factory=list)  # deleted by backup_keep
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


def _angle_text(deg: float) -> str:
    """An angle as written to the board: 4 decimals, trailing zeros dropped (``331.3895``, ``90``)."""
    s = f"{deg:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "360") else s


def set_rotation(fp: Footprint, angle: float) -> None:
    """Rotate a footprint in place to ``angle`` about its anchor (90° steps; pads follow)."""
    delta = _norm_angle(angle - fp.angle)
    if min(delta, 360.0 - delta) < 1e-3 or fp.node is None:  # same angle (as written, 4 decimals)
        return
    at = find(fp.node, "at")
    while len(at) < 4:
        at.append(Sym("0"))
    at[3] = Sym(_angle_text(_norm_angle(angle)))
    for child in fp.node:
        if head(child) in ("property", "fp_text", "pad"):
            cat = find(child, "at")
            if cat is not None:
                old = float(cat[3]) if len(cat) > 3 else 0.0
                new = _norm_angle(old + delta)
                if len(cat) > 3:
                    cat[3] = Sym(_angle_text(new))
                elif new:
                    cat.append(Sym(_angle_text(new)))
    fp.angle = _norm_angle(angle)
    fp.pads = [
        replace(p, x_nm=fp.x_nm + rotate_nm(p.local_x_nm, p.local_y_nm, fp.angle)[0],
                y_nm=fp.y_nm + rotate_nm(p.local_x_nm, p.local_y_nm, fp.angle)[1])
        for p in fp.pads
    ]  # fmt: skip


def place_at(fp: Footprint, x_nm: int, y_nm: int, angle: float = 0.0) -> None:
    set_rotation(fp, angle)
    fp.move(x_nm - fp.x_nm, y_nm - fp.y_nm)


# --- placing new W links (place_links) ---------------------------------------------------------

LINK_VALUE = "Link"  # the Value of the StripForge:Link symbol


def link_symbol_uuid(ref: str) -> str:
    """The uuid StripForge gives the ``StripForge:Link`` symbol of link ``ref`` (deterministic)."""
    return _u(f"link-symbol/{ref}")


def _sheet_of(fp: Footprint) -> tuple[str, str, str] | None:
    """(sheet path prefix, sheetname, sheetfile) of a footprint linked to a schematic symbol."""
    node = fp.node or []
    path = atom(find(node, "path"), 1)
    if not path or "/" not in path:
        return None
    prefix = path.rsplit("/", 1)[0]
    return prefix, atom(find(node, "sheetname"), 1) or "", atom(find(node, "sheetfile"), 1) or ""


def link_sheet(board: Board, net: str) -> tuple[str, str, str] | None:
    """The schematic sheet a new link on ``net`` belongs on: the sheet of most parts with a pin
    on the net, else the sheet of most parts on the board; None if nothing is linked to a sheet."""
    from collections import Counter

    on_net: Counter = Counter()
    anywhere: Counter = Counter()
    for fp in board.footprints:
        if is_link(fp):
            continue
        sheet = _sheet_of(fp)
        if sheet is None:
            continue
        anywhere[sheet] += 1
        if any(p.net == net for p in fp.pads):
            on_net[sheet] += 1
    for counts in (on_net, anywhere):
        if counts:
            return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    return None


def place_new_link(board: Board, a: Analysis, lk, library) -> tuple[list, Footprint]:
    """The footprint of proposed link ``lk`` placed on its holes: both pads on the link's net,
    locked, Value ``Link``, and the schematic path of the symbol ``stripforge link-symbols``
    writes for it (so F8 afterwards matches the two up instead of adding a second copy)."""
    ref = lk.ref_hint
    node = embed_footprint(
        lk.footprint.split(":")[-1], ref, 0, 0, f"link/{ref}", {"1": lk.net, "2": lk.net}, library
    )
    layer_i = next(i for i, c in enumerate(node) if head(c) == "layer")
    node.insert(layer_i, [Sym("locked"), Sym("yes")])
    for prop in find_all(node, "property"):
        if atom(prop, 1) == "Value":
            prop[2] = LINK_VALUE
    sheet = link_sheet(board, lk.net)
    prefix, sheetname, sheetfile = sheet if sheet else ("", "", "")
    extra = [[Sym("path"), f"{prefix}/{link_symbol_uuid(ref)}"]]
    if sheetname:
        extra.append([Sym("sheetname"), sheetname])
    if sheetfile:
        extra.append([Sym("sheetfile"), sheetfile])
    last_prop = max(i for i, c in enumerate(node) if head(c) == "property")
    node[last_prop + 1 : last_prop + 1] = extra
    fp = _parse_footprint(node)
    x, y = a.grid.hole_xy(Node(lk.row_a, lk.col))
    place_at(fp, x, y, lk.rotation)
    return node, fp


# --- stripboard holes ------------------------------------------------------------------------


def placed_link_pads(link_fps: list[Footprint]) -> list:
    return [p for fp in link_fps for p in fp.pads]


def _hole_footprints(
    board: Board, a: Analysis, plan: links_mod.LinkPlan, cfg: BoardConfig, extra_pads
) -> list:
    """One board-only ``StripForge:Holes`` footprint per strip with a pad at every free grid hole.

    A hole gets a plated pad (B.Cu only, no mask: stripboard has none; strip-width copper; on
    the net of the strip piece it sits in, no net on bare strip) unless a part pin sits in it, it
    is the hole a slot is filed toward, or its drill would come closer than KiCad's hole-to-hole
    minimum to another pad's drill (a placed W link, a slot, a mounting hole). A hole-cut hole is
    drawn as a bare non-plated hole (the copper round it is gone).
    """
    g = a.grid
    drill = mm_to_nm(cfg.hole_drill_mm)
    size = mm_to_nm(cfg.strip_width_mm)
    # A proposed link's holes are drawn until its W footprint is placed there (its pads then take
    # the hole: see the drill check below).
    busy = set(a.holes.occupants) | set(a.holes.reserved)
    drills = [
        (p.x_nm, p.y_nm, max(p.drill_x_nm, p.drill_y_nm))
        for p in [*board.pads, *extra_pads]
        if max(p.drill_x_nm, p.drill_y_nm) > 0
    ]
    net_at: dict[Node, str | None] = {}
    for piece in a.split.pieces:
        for c in range(piece.col_start, piece.col_end + 1):
            net_at[Node(piece.row, c)] = piece.net
    dead = {Node(s.row, c) for s in a.strips for c in s.dead_holes}

    own = max(drill // 2 + HOLE_TO_HOLE_NM, size // 2 + HOLE_CLEARANCE_NM)  # our hole, our copper

    def near_drill(x: int, y: int) -> bool:
        for px, py, d in drills:
            reach = own + d // 2
            if abs(px - x) < reach and abs(py - y) < reach and (px - x) ** 2 + (py - y) ** 2 < reach * reach:
                return True
        return False

    out = []
    for row in range(g.rows):
        x0, y0 = g.hole_xy(Node(row, 0))
        pads = []
        for col in range(g.cols):
            node = Node(row, col)
            if node in busy:
                continue
            x, y = g.hole_xy(node)
            if near_drill(x, y):
                continue
            at = [Sym("at"), _mm(x - x0), _mm(0)]
            if node in dead:
                pad = [Sym("pad"), node.label, Sym("np_thru_hole"), Sym("circle"), at,
                       [Sym("size"), _mm(drill), _mm(drill)], [Sym("drill"), _mm(drill)],
                       [Sym("layers"), "*.Cu"]]  # fmt: skip
            else:
                pad = [Sym("pad"), node.label, Sym("thru_hole"), Sym("circle"), at,
                       [Sym("size"), _mm(size), _mm(size)], [Sym("drill"), _mm(drill)],
                       [Sym("layers"), "B.Cu"],
                       [Sym("remove_unused_layers"), Sym("no")]]  # fmt: skip
                if net_at.get(node):
                    pad.append([Sym("net"), net_at[node]])
            pad.append([Sym("uuid"), _u(f"holes/{row}/{col}")])
            pads.append(pad)
        if not pads:
            continue
        strip = row_label(row)
        ref = f"{HOLES_REF}{strip}"
        text = f"StripForge stripboard holes, strip {strip} (drawn by stripforge build; not a part)"
        out.append(
            [
                Sym("footprint"),
                HOLES_LIB_ID,
                [Sym("layer"), "F.Cu"],
                [Sym("uuid"), _u(f"holes/{row}")],
                [Sym("at"), _mm(x0), _mm(y0)],
                [Sym("descr"), text],
                [Sym("tags"), "StripForge stripboard holes board_only"],
                _prop("Reference", ref, f"holes/{row}/ref"),
                _prop("Value", "Holes", f"holes/{row}/value"),
                _prop("Datasheet", "", f"holes/{row}/datasheet"),
                _prop("Description", text, f"holes/{row}/descr"),
                [
                    Sym("attr"),
                    Sym("board_only"),
                    Sym("exclude_from_pos_files"),
                    Sym("exclude_from_bom"),
                    Sym("allow_missing_courtyard"),
                ],  # fmt: skip
                *pads,
                [Sym("embedded_fonts"), Sym("no")],
            ]
        )
    return out


def _prop(name: str, value: str, key: str) -> list:
    return [
        Sym("property"),
        name,
        value,
        [Sym("at"), Sym("0"), Sym("0"), Sym("0")],
        [Sym("layer"), "F.Fab"],
        [Sym("hide"), Sym("yes")],
        [Sym("uuid"), _u(key)],
        [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1"), Sym("1")], [Sym("thickness"), Sym("0.15")]]],
    ]


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
    stretches: list = field(default_factory=list)  # lead-stretch suggestions (stretch.Stretch)


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
    root[:] = [n for n in root if not (head(n) == "footprint" and atom(n, 1) == HOLES_LIB_ID)]
    link_fps = [fp for fp in board.footprints if is_link(fp)]
    cfg = replace(cfg, offboard_refs=sorted(set(cfg.offboard_refs) | {fp.ref for fp in link_fps}))
    cfg, clip_warnings = clip_to_outline(board, cfg, grid)

    mine = edits_mod.from_config(cfg)
    a = analyze_board(board, cfg, netlist, mine)
    a, moves = apply_best_fit(a)
    plan = links_mod.propose(a, mine.links)
    warnings = list(clip_warnings)
    if cfg.respect_edits and not a.conflicts:
        theirs = edits_mod.from_board(board, a.grid, old_cuts, link_fps, mm_to_nm(cfg.snap_tol_mm))
        if theirs and _edited(a, plan, theirs):
            # the board's cuts or placed links differ from StripForge's own plan: keep them all
            both = mine.merged(theirs)
            a = analyze_board(board, cfg, netlist, both)
            a, more = apply_best_fit(a)
            moves.update(more)
            plan = links_mod.propose(a, both.links, [fp.ref for fp in link_fps])
            yours = sum(1 for c in a.split.cuts if c.user.endswith("in the board"))
            kept = sum(1 for lk in plan.links if lk.origin == "board")
            warnings.append(
                f"edits: kept your {yours} cut(s) and {kept} placed link(s) from the board as they are; "
                "StripForge only filled in what they leave open (respect_edits = false plans from scratch)"
            )
            warnings += theirs.warnings
    if a.conflicts:
        raise BuildError("the board has conflicts; fix them first:\n  " + "\n  ".join(a.conflicts))
    stretches = stretch_mod.suggest(a, plan, cfg) if plan.links or plan.unlinkable else []
    return Prepared(a, plan, moves, cfg, link_fps, own_tracks, old_cuts, foreign, warnings, stretches)


def _edited(a, plan: links_mod.LinkPlan, theirs) -> bool:
    """Do the board's cut markers or placed ``W`` links differ from StripForge's own plan?"""
    if theirs.cuts and {c.key for c in theirs.cuts} != {(c.row, c.col, c.style) for c in a.split.cuts}:
        return True
    planned = {lk.ref_hint: tuple(sorted(lk.nodes)) for lk in plan.links}
    return any(planned.get(spec.ref) != (spec.n1, spec.n2) for spec in theirs.links)


def build(
    board_path: str | Path,
    cfg: BoardConfig,
    out_path: str | Path,
    netlist: str | None = None,
    library: str | Path | None = None,
    rules: str | Path | None = None,
    in_place: bool = False,
) -> BuildResult:
    """Build ``out_path`` from ``board_path`` (see the module docstring). Raises BuildError.

    ``in_place`` allows ``out_path`` to be ``board_path``: build into the board itself (the default
    ``output = "in_place"``) or rebuild a built board after editing it (your cuts and links are
    kept; see :mod:`stripforge.edits`). Before an in-place write the board is backed up with
    :func:`rotate_backups` (``cfg.backup_keep``)."""

    board_path, out_path = Path(board_path), Path(out_path)
    if out_path.resolve() == board_path.resolve() and not in_place:
        raise BuildError("-o must name a new file, not the input board (or pass --in-place)")
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
    res.stretches = list(prep.stretches)
    res.warnings += resources.check_rules_width(rules.read_text(encoding="utf-8"), cfg.strip_width_mm)

    # pass 2: place the W footprints that F8 brought in
    by_ref = {lk.ref_hint: lk for lk in plan.links}
    for fp in sorted(link_fps, key=lambda f: (len(f.ref), f.ref)):
        lk = by_ref.get(fp.ref)
        if lk is not None and lk.origin == "board":
            res.placements.append(LinkPlacement(fp.ref, "placed", f"{lk.start} -> {lk.end} (yours, kept)"))
            continue
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
        place_at(fp, x, y, lk.rotation)
        res.placements.append(LinkPlacement(fp.ref, "placed", f"{lk.start} -> {lk.end}"))
    new_links: list[list] = []
    if cfg.place_links:
        # first pass (or links the plan added since): place the W footprints ourselves
        present = {fp.ref for fp in link_fps}
        for lk in plan.links:
            if lk.ref_hint in present:
                continue
            node, fp = place_new_link(board, a, lk, library)
            new_links.append(node)
            link_fps.append(fp)
            res.placements.append(LinkPlacement(lk.ref_hint, "placed", f"{lk.start} -> {lk.end} (new)"))
    elif link_fps:
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
    markers = new_links + markers
    hole_fps = (
        _hole_footprints(board, a, plan, cfg, [p for p in placed_link_pads(link_fps)])
        if cfg.draw_holes
        else []
    )
    res.holes_drawn = sum(sum(1 for c in n if head(c) == "pad") for n in hole_fps)
    markers += hole_fps

    last_fp = max((i for i, n in enumerate(root) if head(n) == "footprint"), default=len(root) - 1)
    root[last_fp + 1 : last_fp + 1] = markers
    tail = len(root)
    if root and head(root[-1]) == "embedded_fonts":
        tail -= 1
    root[tail:tail] = new_nodes

    out_path.parent.mkdir(parents=True, exist_ok=True)
    res.warnings += _compare_saved(link_file(out_path, ".json"), plan)
    dru = out_path.with_suffix(".kicad_dru")
    if out_path.resolve() == board_path.resolve() and not board_path.stem.endswith(BUILT_SUFFIX):
        # in place: back up the board as it is now, rotating older backups (a -stripforge board is
        # itself a copy made by output = "separate", so it gets none)
        rot = rotate_backups(board_path, cfg.backup_keep)
        res.backup = str(rot.backup)
        res.backups_shifted, res.backups_pruned = rot.shifted, rot.pruned
        res.warnings += rot.warnings
        rules_text = rules.read_bytes()
        theirs = dru.read_bytes() if dru.is_file() else rules_text
        if theirs != rules_text and b"SF strip width" not in theirs:  # not an older StripForge copy
            dru_backup = backup_path(dru)
            if not dru_backup.exists():
                shutil.copy2(dru, dru_backup)
                res.warnings.append(
                    f"build: {dru.name} had other DRC rules; they were copied to {dru_backup.name} "
                    "and replaced by the StripForge rules"
                )
    save_board(board, out_path)
    res.outputs.append(str(out_path))
    shutil.copyfile(rules, dru)
    res.outputs.append(str(dru))
    res.outputs += write_link_files(plan, out_path, res.stretches, cfg.link_lead_allowance_in)

    # re-read what was written: it must parse and carry exactly the tracks we meant to write
    check = load_board(out_path)
    written = [n for n in check.doc.root if head(n) == "segment"]
    if len(written) != res.segments:
        raise BuildError(f"internal error: wrote {res.segments} tracks but read back {len(written)}")
    return res


# --- link files --------------------------------------------------------------------------------


def link_file(out_path: str | Path, suffix: str) -> Path:
    """``<name>-stripforge.links.json`` / ``.csv`` / ``.txt`` next to the built board (the same
    name whether the board was built in place, ``<name>.kicad_pcb``, or separately,
    ``<name>-stripforge.kicad_pcb``)."""
    p = Path(out_path)
    stem = p.stem if p.stem.endswith(BUILT_SUFFIX) else p.stem + BUILT_SUFFIX
    return p.with_name(stem + ".links" + suffix)


def write_link_files(
    plan: links_mod.LinkPlan, out_path: str | Path, stretches=(), allowance_in: float = 0.0
) -> list[str]:
    """Write the link proposal (JSON, CSV, and a text report with schematic instructions), with
    any lead-stretch suggestions in the JSON and the text report."""
    import json

    data = json.loads(links_mod.to_json(plan, Path(out_path).name))
    text = links_mod.format_text(plan, allowance_in)
    if stretches:
        data["lead_stretches"] = [s.to_dict() for s in stretches]
        text += "\n" + stretch_mod.format_text(stretches)
    out = []
    for suffix, body in (
        (".json", json.dumps(data, indent=2) + "\n"),
        (".csv", links_mod.to_csv(plan)),
        (".txt", text),
    ):
        p = link_file(out_path, suffix)
        p.write_text(body, encoding="utf-8")
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
