# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""`stripforge analyze`: read a board, snap, build strips, split nets and report (M1).

Read-only: nothing is written to the board.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import netlist as netlist_mod
from .board import Board, load_board
from .config import BoardConfig
from .grid import ON_GRID_NM, Grid, Node, SlotJob, SnapResult, bend_text, snap_board
from .hints import Hint, placement_hints
from .hints import summary as hints_summary
from .sexpr import mm_to_nm
from .splitter import SplitResult, split
from .strips import HoleMap, Strip, assign_holes, build_strips
from .validate import validate


@dataclass
class Analysis:
    board: Board
    grid: Grid
    grid_source: str
    config: BoardConfig
    snaps: list[SnapResult]
    holes: HoleMap
    strips: list[Strip]
    split: SplitResult
    validation: list[str]
    netlist_summary: dict | None = None
    netlist_warnings: list[str] = field(default_factory=list)
    grid_warnings: list[str] = field(default_factory=list)
    config_ref_warnings: list[str] = field(default_factory=list)
    hints: list[Hint] = field(default_factory=list)
    edits: object = None  # stripforge.edits.Edits: your own cuts and links (None: none)

    @property
    def tol_nm(self) -> int:
        return mm_to_nm(self.config.snap_tol_mm)

    @property
    def snapped(self) -> list[SnapResult]:
        return [s for s in self.snaps if s.accepted]

    @property
    def rejected(self) -> list[SnapResult]:
        return [s for s in self.snaps if not s.accepted]

    @property
    def off_board(self) -> list[SnapResult]:
        """Footprints with at least one pad outside the configured grid."""
        return [s for s in self.snaps if s.off_board_pads]

    @property
    def off_pitch(self) -> list[SnapResult]:
        return [s for s in self.snapped if s.max_dev_nm > ON_GRID_NM and not s.slots and not s.bends]

    @property
    def leg_bends(self) -> list:
        """Legs to bend onto their holes ([bend] parts, the Beckham tolerance)."""
        return [b for s in self.snapped for b in s.bends]

    @property
    def skipped(self) -> list[str]:
        """References on the board that the config skips (wired off-board)."""
        refs = {fp.ref for fp in self.board.footprints}
        return [r for r in self.config.skip if r in refs]

    @property
    def slot_jobs(self) -> list[SlotJob]:
        """Holes to file into slots for slotted parts (kept for the M3 build sheet)."""
        return [j for s in self.snapped for j in s.slots]

    @property
    def conflicts(self) -> list[str]:
        return self.holes.conflicts + self.validation

    @property
    def warnings(self) -> list[str]:
        out = (
            list(self.config.warnings)
            + list(self.config_ref_warnings)
            + list(self.grid_warnings)
            + list(self.holes.warnings)
            + list(self.split.warnings)
            + list(self.netlist_warnings)
        )
        for s in self.snaps:
            if s.skipped_pads:
                out.append(f"{s.ref}: non-THT pad(s) {', '.join(s.skipped_pads)} ignored (THT only in v0)")
            if s.accepted and s.reason:
                out.append(f"{s.ref}: {s.reason}")
            if s.accepted and s.lopsided:
                out.append(s.lopsided)
            if s.accepted and s.bends:
                worst = max(b.bend_nm for b in s.bends)
                out.append(
                    f"{s.ref}: accepted with its [bend] tolerance ({s.bend_nm / 1e6:g} mm): bend "
                    f"{len(s.bends)} leg(s) up to {bend_text(worst)} onto their holes"
                )
        for ref in self.skipped:
            fp = next(f for f in self.board.footprints if f.ref == ref)
            nets = sorted({p.net for p in fp.pads if p.net and not p.net.startswith("unconnected-")})
            out.append(
                f"{ref}: skipped (wired off-board): its pads get no strips, so hand-wire its "
                f"connection(s) to {', '.join(nets) if nets else 'nothing (no nets)'}"
            )
        return out


def make_grid(board: Board, cfg: BoardConfig) -> tuple[Grid, str]:
    pitch = mm_to_nm(cfg.pitch_mm)
    inset = mm_to_nm(cfg.hole_inset_mm) if cfg.hole_inset_mm is not None else pitch // 2
    if cfg.origin_mm is not None and cfg.rows and cfg.cols:
        ox, oy = (mm_to_nm(v) for v in cfg.origin_mm)
        return Grid(ox, oy, cfg.cols, cfg.rows, pitch), "config"
    if board.outline is None:
        raise ValueError("board has no Edge.Cuts outline; set origin_mm, rows and cols in the config")
    x0, y0, x1, y1 = board.outline
    if cfg.origin_mm is not None:
        ox, oy = (mm_to_nm(v) for v in cfg.origin_mm)
    else:
        ox, oy = x0 + inset, y0 + inset
    cols, rows = Grid._fit(ox, oy, x1 - inset, y1 - inset, pitch)
    source = "Edge.Cuts" if cfg.origin_mm is None and not cfg.rows and not cfg.cols else "config+Edge.Cuts"
    return Grid(ox, oy, cfg.cols or cols, cfg.rows or rows, pitch), source


def _grid_warnings(board: Board, grid: Grid, source: str) -> list[str]:
    """Warn when a configured grid and the board's Edge.Cuts outline disagree."""
    if source == "Edge.Cuts" or board.outline is None:
        return []
    x0, y0, x1, y1 = board.outline
    gx0, gy0 = grid.hole_xy(Node(0, 0))
    gx1, gy1 = grid.hole_xy(grid.last)
    p, half = grid.pitch_nm, grid.pitch_nm // 2
    size = f"{grid.cols}x{grid.rows} grid ({grid.span_label}, {_mm(grid.cols * p)} x {_mm(grid.rows * p)} mm)"
    edge = f"Edge.Cuts outline ({_mm(x1 - x0)} x {_mm(y1 - y0)} mm)"
    out: list[str] = []
    if gx0 - half < x0 or gy0 - half < y0 or gx1 + half > x1 or gy1 + half > y1:
        out.append(f"grid: the configured {size} extends past the {edge}")
    extra_cols = max(0, (x1 - half - gx1) // p)
    extra_rows = max(0, (y1 - half - gy1) // p)
    if extra_cols or extra_rows:
        more = " and ".join(
            t
            for t in (
                f"{extra_cols} more hole(s) per strip" if extra_cols else "",
                f"{extra_rows} more strip(s)" if extra_rows else "",
            )
            if t
        )
        out.append(
            f"grid: the {edge} has room for {more} than the configured {size}; parts there are off board"
        )
    return out


def _check_netlist(board: Board, path: str) -> tuple[dict, list[str]]:
    comps, nets = netlist_mod.parse(path)
    warns: list[str] = []
    board_refs = {fp.ref for fp in board.footprints}
    net_refs = {c.ref for c in comps}
    for ref in sorted(net_refs - board_refs):
        warns.append(f"netlist: {ref} is in the schematic but not on the board")
    for ref in sorted(board_refs - net_refs):
        warns.append(f"netlist: {ref} is on the board but not in the schematic")
    pins = netlist_mod.pin_nets(nets)
    mismatches = 0
    for pad in board.pads:
        want = pins.get((pad.ref, pad.number))
        if want is not None and pad.net != want:
            mismatches += 1
            warns.append(f"netlist: pad {pad.label} is on net {pad.net!r} but the schematic says {want!r}")
    board_nets = set(board.nets)
    sch_nets = {n.name for n in nets}
    for n in sorted(sch_nets - board_nets):
        warns.append(f"netlist: net {n!r} has no pad on the board")
    for n in sorted(board_nets - sch_nets):
        warns.append(f"netlist: board net {n!r} is not in the schematic")
    summary = {
        "path": path,
        "components": len(comps),
        "nets": len(nets),
        "pin_mismatches": mismatches,
        "matches_board": not warns,
    }
    return summary, warns


def _config_ref_warnings(board: Board, cfg: BoardConfig) -> list[str]:
    """Config entries naming parts that aren't on the board, or a part both skipped and fitted."""
    refs = {fp.ref for fp in board.footprints}
    out = []
    for what, listed in (("slotted", cfg.slotted), ("[bend]", list(cfg.bend)), ("skip", cfg.skip)):
        for r in listed:
            if r not in refs:
                out.append(f"config: {r} is listed in {what} but is not on the board (typo or renamed?)")
    for r in cfg.skip:
        both = [w for w, lst in (("slotted", cfg.slotted), ("[bend]", cfg.bend)) if r in lst]
        if r in refs and both:
            out.append(f"config: {r} is in skip and in {' and '.join(both)}; skip wins (it is not placed)")
    return out


def analyze(board_path: str | Path, cfg: BoardConfig | None = None, netlist: str | None = None) -> Analysis:
    return analyze_board(load_board(board_path), cfg, netlist)


def analyze_board(
    board: Board, cfg: BoardConfig | None = None, netlist: str | None = None, edits=None
) -> Analysis:
    """Snap, build strips and split for an in-memory board (see :func:`analyze`)."""
    cfg = cfg or BoardConfig()
    grid, source = make_grid(board, cfg)
    slot_max = {ref: mm_to_nm(cfg.slot_max_for(ref)) for ref in cfg.slotted}
    bend = {ref: mm_to_nm(mm) for ref, mm in cfg.bend.items()}  # the Beckham tolerance
    snaps = snap_board(
        board,
        grid,
        mm_to_nm(cfg.snap_tol_mm),
        skip_refs=cfg.offboard_refs,
        slot_max_nm=slot_max,
        bend_nm=bend,
    )
    holes = assign_holes(snaps)
    strips = build_strips(grid)
    if edits is None:
        from .edits import from_config

        edits = from_config(cfg)
    result = split(strips, holes, cfg.cut_style, edits)
    a = Analysis(
        board=board,
        grid=grid,
        grid_source=source,
        config=cfg,
        snaps=snaps,
        holes=holes,
        strips=strips,
        split=result,
        validation=validate(result, holes, strips),
        grid_warnings=_grid_warnings(board, grid, source),
        config_ref_warnings=_config_ref_warnings(board, cfg),
        hints=placement_hints(board, snaps, grid, mm_to_nm(cfg.snap_tol_mm), cfg.cut_style, result.cuts),
        edits=edits,
    )
    if netlist:
        a.netlist_summary, a.netlist_warnings = _check_netlist(board, str(netlist))
    return a


def best_fit_moves(a: Analysis) -> dict[str, tuple[int, int]]:
    """``{ref: (dx_nm, dy_nm)}``: the best-fit shift of every snapped footprint that has one.

    The shift centres the footprint's pad offsets on their holes (it minimises the worst per-axis
    offset), so on-pitch parts land exactly on their holes and a 2.50 mm part splits its error
    between its pads. Slotted parts, [bend] parts (placed off the holes on purpose) and rejected
    parts are never moved; rotations are out of scope.
    """
    return {
        s.ref: s.shift_nm
        for s in a.snaps
        if s.accepted and not s.slotted and s.bend_nm is None and s.shift_nm != (0, 0)
    }


def apply_best_fit(a: Analysis) -> tuple[Analysis, dict[str, tuple[int, int]]]:
    """Move each snapped footprint of ``a.board`` by its best-fit shift and re-analyse.

    The board model (and its S-expression document, for writing) is edited in place. Returns the
    new analysis and the moves applied.
    """
    moves = best_fit_moves(a)
    for ref, (dx, dy) in moves.items():
        a.board.footprint(ref).move(dx, dy)
    netlist = a.netlist_summary["path"] if a.netlist_summary else None
    return analyze_board(a.board, a.config, netlist, a.edits), moves


# --- reporting -------------------------------------------------------------------------------


def _mm(nm: int) -> str:
    return f"{nm / 1e6:.3f}"


def _xy(nm: tuple[int, int]) -> list[float]:
    return [nm[0] / 1e6, nm[1] / 1e6]


def to_dict(a: Analysis) -> dict:
    g = a.grid
    return {
        "board": a.board.path,
        "grid": {
            "source": a.grid_source,
            "cols": g.cols,
            "rows": g.rows,
            "pitch_mm": g.pitch_nm / 1e6,
            "origin_mm": _xy((g.origin_x_nm, g.origin_y_nm)),
            "labels": g.span_label,
            "outline_mm": [v / 1e6 for v in a.board.outline] if a.board.outline else None,
        },
        "snap_tol_mm": a.config.snap_tol_mm,
        "cut_style": str(a.config.cut_style),
        "footprints": len(a.board.footprints),
        "pads": len(a.board.pads),
        "nets": len(a.board.nets),
        "skipped_refs": list(a.config.offboard_refs),
        "slotted_refs": list(a.config.slotted),
        "bend_mm": dict(a.config.bend),
        "snaps": [
            {
                "ref": s.ref,
                "accepted": s.accepted,
                "slotted": s.slotted,
                "bend_tol_mm": s.bend_nm / 1e6 if s.bend_nm is not None else None,
                "leg_bends": [
                    {
                        "pad": b.pad,
                        "hole_label": b.hole.label,
                        "bend_mm": b.bend_nm / 1e6,
                        "direction": b.direction,
                    }
                    for b in s.bends
                ],
                "max_dev_mm": s.max_dev_nm / 1e6,
                "reason": s.reason,
                "suggested_shift_mm": _xy(s.shift_nm),
                "max_dev_after_shift_mm": s.max_dev_after_shift_nm / 1e6,
                "pads": [
                    {
                        "pad": p.number,
                        "net": p.net,
                        "hole": [p.node.col, p.node.row] if p.node else None,
                        "hole_label": p.node.label if p.node else None,
                        "near_label": p.near.label if p.near else None,
                        "offset_mm": _xy((p.dx_nm, p.dy_nm)),
                        "dev_mm": p.dev_nm / 1e6,
                    }
                    for p in s.pads
                ],
            }
            for s in a.snaps
        ],
        "slot_jobs": [
            {
                "ref": j.ref,
                "pad": j.pad,
                "hole": [j.hole.col, j.hole.row],
                "hole_label": j.hole.label,
                "toward": [j.toward.col, j.toward.row],
                "toward_label": j.toward.label,
                "length_mm": j.length_nm / 1e6,
                "file_mm": j.file_len_nm / 1e6,
                "file_in": round(j.file_len_nm / 25.4e6, 4),
                "inward": j.inward,
                "text": j.text,
            }
            for j in a.slot_jobs
        ],
        "off_board": [
            {
                "ref": s.ref,
                "pads": [
                    {"pad": p.number, "near": [p.near.col, p.near.row], "near_label": p.near.label}
                    for p in s.off_board_pads
                    if p.near is not None
                ],
            }
            for s in a.off_board
        ],
        "cuts": [
            {
                "id": c.id,
                "label": c.label,
                "row": c.row,
                "col": c.col,
                "style": c.style,
                "nets": list(c.reason),
                "between": list(c.between),
            }
            for c in a.split.cuts
        ],
        "pieces": [
            {
                "label": p.label,
                "row": p.row,
                "cols": [p.col_start, p.col_end],
                "nets": list(p.nets),
                "pads": list(p.pads),
            }
            for p in a.split.pieces
            if p.pads
        ],
        "pieces_per_net": a.split.pieces_per_net,
        "nets_needing_links": a.split.split_nets,
        "hints": [
            {
                "ref": h.ref,
                "forced_cuts": h.forced_cuts,
                "rows": [
                    {"row": r, "cols": [c0, c1], "label": f"{Node(r, c0).label}-{Node(r, c1).label}"}
                    for r, (c0, c1) in sorted(h.rows.items())
                ],
                "cuts": h.cut_ids,
                "text": h.text,
                "rotation": None
                if h.rotation is None
                else {
                    "angle": h.rotation.angle,
                    "shift_mm": _xy(h.rotation.shift_nm),
                    "pads": {
                        n: {"hole": [node.col, node.row], "hole_label": node.label}
                        for n, node in h.rotation.pads.items()
                    },
                    "cuts_after": h.rotation.cuts_after,
                    "cuts_saved": h.rotation.cuts_saved,
                    "separate_strips": h.rotation.separate_strips,
                    "links_delta": h.rotation.links_delta,
                },
                "rotation_note": h.rotation_note,
            }
            for h in a.hints
        ],
        "hints_summary": {
            "parts": len(a.hints),
            "forced_cuts": sum(h.forced_cuts for h in a.hints),
            "rotatable": [h.ref for h in a.hints if h.rotation],
            "est_cuts_saved": sum(h.rotation.cuts_saved for h in a.hints if h.rotation),
            "text": hints_summary(a.hints),
        },
        "netlist": a.netlist_summary,
        "warnings": a.warnings,
        "conflicts": a.conflicts,
    }


def format_text(a: Analysis) -> str:
    g = a.grid
    lines: list[str] = []
    add = lines.append
    add(f"StripForge analyze: {a.board.path}")
    add(
        f"Grid: {g.cols} cols x {g.rows} rows at {_mm(g.pitch_nm)} mm, hole A1 at "
        f"({_mm(g.origin_x_nm)}, {_mm(g.origin_y_nm)}) mm [{a.grid_source}]"
    )
    add(
        f"Holes: {g.span_label}; strips (rows) are letters A, B, ... top to bottom, holes along a strip "
        "are numbered from 1 left to right (component side)"
    )
    add(f"Footprints: {len(a.board.footprints)}, pads: {len(a.board.pads)}, nets: {len(a.board.nets)}")
    if a.netlist_summary:
        ns = a.netlist_summary
        verdict = "matches the board" if ns["matches_board"] else "DIFFERS from the board (see warnings)"
        add(f"Netlist: {ns['components']} components, {ns['nets']} nets; {verdict}")
    if a.config.offboard_refs:
        add(f"Skipped (wired off-board): {', '.join(a.config.offboard_refs)}")
    add("")
    tol = a.config.snap_tol_mm
    add(
        f"Snap (tolerance {tol:.3f} mm): {len(a.snapped)} snapped, {len(a.rejected)} rejected"
        + (f" ({len(a.off_board)} off board)" if a.off_board else "")
    )
    exact = [s.ref for s in a.snapped if s.max_dev_nm <= ON_GRID_NM]
    add(f"  on grid (<= {_mm(ON_GRID_NM)} mm): {', '.join(exact) if exact else '-'}")
    for s in a.off_pitch:
        offs = "; ".join(
            f"pad {p.number} {p.node} off ({_mm(p.dx_nm)}, {_mm(p.dy_nm)})"
            for p in s.pads
            if p.dev_nm > ON_GRID_NM
        )
        add(f"  off pitch: {s.ref:<5} max {_mm(s.max_dev_nm)} mm  [{offs}]")
    for s in a.snapped:
        if s.slots:
            jobs = "; ".join(f"pad {j.pad}: {j.text}" for j in s.slots)
            add(f"  slotted:   {s.ref:<5} max {_mm(s.max_dev_nm)} mm  [{jobs}]")
        if s.bends:  # the Beckham tolerance: accepted because its legs can be bent onto the holes
            legs = "; ".join(b.text for b in s.bends)
            add(f"  bend legs: {s.ref:<5} [bend] {s.bend_nm / 1e6:g} mm  [{legs}]")
    for s in a.off_board:
        pads = ", ".join(f"{p.number}@{p.where}" for p in s.pads)
        add(
            f"  OFF BOARD: {s.ref}: {len(s.off_board_pads)} of {len(s.pads)} pad(s) outside "
            f"{g.span_label} [{pads}]"
        )
    for s in a.rejected:
        if s.off_board_pads:
            continue
        add(
            f"  REJECTED: {s.ref}: {s.reason}; a shift of ({_mm(s.shift_nm[0])}, {_mm(s.shift_nm[1])}) mm "
            f"would give max {_mm(s.max_dev_after_shift_nm)} mm"
        )
    add("")
    cuts = a.split.cuts
    nh = sum(c.style == "hole" for c in cuts)
    add(f"Cuts ({a.config.cut_style}): {len(cuts)} ({nh} hole, {len(cuts) - nh} knife)")
    for c in cuts:
        sides = f"{c.between[0]} [{c.reason[0]}] | {c.between[1]} [{c.reason[1]}]"
        add(f"  {c.id:<4} {c.style:<5} {c.label:<9} {sides}")
    add("")
    ppn = a.split.pieces_per_net
    add(f"Pieces per net ({len(ppn)} nets on the strips):")
    for net, n in ppn.items():
        add(f"  {n:>3}  {net}")
    unplaced = sorted(set(a.board.nets) - set(ppn))
    if unplaced:
        add(f"  nets with no pad on the strips: {', '.join(unplaced)}")
    add("")
    need = a.split.split_nets
    add(f"Nets needing links (M2): {len(need)}" + ("" if need else " (none)"))
    for net, n in need.items():
        where = ", ".join(p.label for p in a.split.pieces if net in p.nets)
        add(f"  {net}: {n} pieces -> at least {n - 1} link(s) [{where}]")
    add("")
    add(f"Placement hints: {len(a.hints)}" + ("" if a.hints else " (none)"))
    for h in a.hints:
        add(f"  - {h.text}")
    add("")
    add(f"Warnings: {len(a.warnings)}" + ("" if a.warnings else " (none)"))
    for w in a.warnings:
        add(f"  - {w}")
    add(f"Conflicts: {len(a.conflicts)}" + ("" if a.conflicts else " (none)"))
    for c in a.conflicts:
        add(f"  - {c}")
    if a.hints:
        add("")
        add(
            f"Summary: {len(a.split.cuts)} cuts, {len(a.split.split_nets)} nets needing links; "
            + hints_summary(a.hints)
        )
    return "\n".join(lines) + "\n"
