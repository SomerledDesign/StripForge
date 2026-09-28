# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""StripForge command line. `analyze` and `snap` work; the other subcommands are stubs (Sketch.md §7)."""

import argparse
import json
import sys
from pathlib import Path


def _config(args: argparse.Namespace):
    from . import config as config_mod

    cfg = config_mod.load(args.config) if args.config else config_mod.BoardConfig()
    if getattr(args, "cut_style", None):
        cfg.cut_style = config_mod.CutStyle(args.cut_style)
    if args.tol is not None:
        cfg.snap_tol_mm = args.tol
    return cfg


def _snap(args: argparse.Namespace) -> int:
    from .analyze import analyze, apply_best_fit
    from .board import save_board

    if args.output and Path(args.output).resolve() == Path(args.board).resolve():
        print("stripforge snap: error: -o must name a new file, not the input board", file=sys.stderr)
        return 2
    try:
        before = analyze(args.board, _config(args))
        after, moves = apply_best_fit(before)
    except (OSError, ValueError) as exc:
        print(f"stripforge snap: error: {exc}", file=sys.stderr)
        return 2
    old = {s.ref: s for s in before.snaps}
    print(f"StripForge snap: {args.board}")
    print(f"Best-fit moves: {len(moves)} footprint(s)" + ("" if moves else " (nothing to move)"))
    for s in after.snaps:
        if s.ref not in moves:
            continue
        dx, dy = moves[s.ref]
        holes = ", ".join(p.where for p in s.pads)
        print(
            f"  {s.ref:<5} moved ({dx / 1e6:+.3f}, {dy / 1e6:+.3f}) mm; worst pad offset "
            f"{old[s.ref].max_dev_nm / 1e6:.3f} -> {s.max_dev_nm / 1e6:.3f} mm [{holes}]"
        )
    kept = [s.ref for s in after.snaps if s.slotted and s.accepted]
    if kept:
        print(f"  not moved (slotted): {', '.join(kept)}")
    for s in after.rejected:
        print(f"  not moved (REJECTED): {s.ref}: {s.reason}")
    if args.output:
        try:
            save_board(after.board, args.output)
        except OSError as exc:
            print(f"stripforge snap: error: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote {args.output} (only the moved footprints' (at ...) lines differ from the input)")
    else:
        print("Dry run: nothing written (use -o <out.kicad_pcb> to write the moved board)")
    return 1 if after.rejected else 0


def _analyze(args: argparse.Namespace) -> int:
    from .analyze import analyze, format_text, to_dict

    cfg = _config(args)
    try:
        a = analyze(args.board, cfg, netlist=args.netlist)
    except (OSError, ValueError) as exc:
        print(f"stripforge analyze: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(to_dict(a), sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_text(a))
    return 1 if (a.conflicts or a.rejected) else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stripforge", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    an = sub.add_parser(
        "analyze",
        help="read-only: snap parts, split strips by net, report cuts and nets needing links",
        description="Read a .kicad_pcb, snap footprints to the hole grid, split the strips by net "
        "and report cuts, pieces per net, nets needing links, warnings and conflicts. Exit code 0 = "
        "clean, 1 = conflicts or rejected footprints, 2 = input error.",
    )
    an.add_argument("board", help="path to .kicad_pcb")
    an.add_argument("--netlist", help="kicadsexpr .net file to cross-check pad nets against")
    an.add_argument("--config", help="stripboard.toml (default: derive the grid from Edge.Cuts)")
    an.add_argument("--cut-style", choices=["hole", "knife", "auto"], help="override the config")
    an.add_argument("--tol", type=float, help="snap tolerance in mm (overrides the config)")
    an.add_argument("--json", action="store_true", help="machine-readable JSON output")
    an.set_defaults(func=_analyze)

    sn = sub.add_parser(
        "snap",
        help="move each snapped footprint by its best-fit shift so its pads sit on holes",
        description="Move every snapped footprint by the rigid shift that best centres its pads on "
        "their holes (slotted and rejected parts stay put; no rotation). Without -o this is a dry run. "
        "The output is the input board byte for byte except the moved footprints' (at ...). "
        "Exit code 0 = all parts snapped, 1 = some rejected (still written), 2 = input error.",
    )
    sn.add_argument("board", help="path to .kicad_pcb")
    sn.add_argument("-o", "--output", help="write the moved board here (must differ from the input)")
    sn.add_argument("--config", help="stripboard.toml (default: derive the grid from Edge.Cuts)")
    sn.add_argument("--tol", type=float, help="snap tolerance in mm (overrides the config)")
    sn.set_defaults(func=_snap)

    for name, help_ in [
        ("plan", "snap + split + cut/link proposal, JSON report only"),
        ("generate", "write strips/cuts/links into a .kicad_pcb"),
        ("drc", "run kicad-cli DRC and classify shorts/opens/parity"),
        ("sheet", "export copper-side build sheet (SVG/PDF) + CSVs"),
    ]:
        sp = sub.add_parser(name, help=help_ + " (not implemented yet)")
        sp.add_argument("board", help="path to .kicad_pcb")
        sp.add_argument("--config", default="stripboard.toml")
    args = p.parse_args(argv)
    if hasattr(args, "func"):
        return args.func(args)
    raise NotImplementedError(f"'{args.cmd}' is not implemented yet (v0 skeleton)")


if __name__ == "__main__":
    raise SystemExit(main())
