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
from .grid import ON_GRID_NM, Grid, SnapResult, snap_board
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
    def off_pitch(self) -> list[SnapResult]:
        return [s for s in self.snapped if s.max_dev_nm > ON_GRID_NM]

    @property
    def conflicts(self) -> list[str]:
        return self.holes.conflicts + self.validation

    @property
    def warnings(self) -> list[str]:
        out = list(self.holes.warnings) + list(self.split.warnings) + list(self.netlist_warnings)
        for s in self.snaps:
            if s.skipped_pads:
                out.append(f"{s.ref}: non-THT pad(s) {', '.join(s.skipped_pads)} ignored (THT only in v0)")
            if s.accepted and s.reason:
                out.append(f"{s.ref}: {s.reason}")
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


def analyze(board_path: str | Path, cfg: BoardConfig | None = None, netlist: str | None = None) -> Analysis:
    cfg = cfg or BoardConfig()
    board = load_board(board_path)
    grid, source = make_grid(board, cfg)
    snaps = snap_board(board, grid, mm_to_nm(cfg.snap_tol_mm), skip_refs=cfg.offboard_refs)
    holes = assign_holes(snaps)
    strips = build_strips(grid)
    result = split(strips, holes, cfg.cut_style)
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
    )
    if netlist:
        a.netlist_summary, a.netlist_warnings = _check_netlist(board, str(netlist))
    return a


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
            "outline_mm": [v / 1e6 for v in a.board.outline] if a.board.outline else None,
        },
        "snap_tol_mm": a.config.snap_tol_mm,
        "cut_style": str(a.config.cut_style),
        "footprints": len(a.board.footprints),
        "pads": len(a.board.pads),
        "nets": len(a.board.nets),
        "skipped_refs": list(a.config.offboard_refs),
        "snaps": [
            {
                "ref": s.ref,
                "accepted": s.accepted,
                "max_dev_mm": s.max_dev_nm / 1e6,
                "reason": s.reason,
                "suggested_shift_mm": _xy(s.shift_nm),
                "max_dev_after_shift_mm": s.max_dev_after_shift_nm / 1e6,
                "pads": [
                    {
                        "pad": p.number,
                        "net": p.net,
                        "hole": [p.node.col, p.node.row] if p.node else None,
                        "offset_mm": _xy((p.dx_nm, p.dy_nm)),
                        "dev_mm": p.dev_nm / 1e6,
                    }
                    for p in s.pads
                ],
            }
            for s in a.snaps
        ],
        "cuts": [
            {
                "id": c.id,
                "row": c.row,
                "col": c.col,
                "style": c.style,
                "nets": list(c.reason),
                "between": list(c.between),
            }
            for c in a.split.cuts
        ],
        "pieces": [
            {"row": p.row, "cols": [p.col_start, p.col_end], "nets": list(p.nets), "pads": list(p.pads)}
            for p in a.split.pieces
            if p.pads
        ],
        "pieces_per_net": a.split.pieces_per_net,
        "nets_needing_links": a.split.split_nets,
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
        f"Grid: {g.cols} cols x {g.rows} rows at {_mm(g.pitch_nm)} mm, hole (0,0) at "
        f"({_mm(g.origin_x_nm)}, {_mm(g.origin_y_nm)}) mm [{a.grid_source}]; holes are (col,row) from 0"
    )
    add(f"Footprints: {len(a.board.footprints)}, pads: {len(a.board.pads)}, nets: {len(a.board.nets)}")
    if a.netlist_summary:
        ns = a.netlist_summary
        verdict = "matches the board" if ns["matches_board"] else "DIFFERS from the board (see warnings)"
        add(f"Netlist: {ns['components']} components, {ns['nets']} nets; {verdict}")
    if a.config.offboard_refs:
        add(f"Skipped (off-board): {', '.join(a.config.offboard_refs)}")
    add("")
    tol = a.config.snap_tol_mm
    add(f"Snap (tolerance {tol:.3f} mm): {len(a.snapped)} snapped, {len(a.rejected)} rejected")
    exact = [s.ref for s in a.snapped if s.max_dev_nm <= ON_GRID_NM]
    add(f"  on grid (<= {_mm(ON_GRID_NM)} mm): {', '.join(exact) if exact else '-'}")
    for s in a.off_pitch:
        offs = "; ".join(
            f"pad {p.number} {p.node} off ({_mm(p.dx_nm)}, {_mm(p.dy_nm)})"
            for p in s.pads
            if p.dev_nm > ON_GRID_NM
        )
        add(f"  off pitch: {s.ref:<5} max {_mm(s.max_dev_nm)} mm  [{offs}]")
    for s in a.rejected:
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
        add(f"  {c.id:<4} {c.style:<5} {c.where:<28} {sides}")
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
        add(f"  {net}: {n} pieces -> at least {n - 1} link(s)")
    add("")
    add(f"Warnings: {len(a.warnings)}" + ("" if a.warnings else " (none)"))
    for w in a.warnings:
        add(f"  - {w}")
    add(f"Conflicts: {len(a.conflicts)}" + ("" if a.conflicts else " (none)"))
    for c in a.conflicts:
        add(f"  - {c}")
    return "\n".join(lines) + "\n"
