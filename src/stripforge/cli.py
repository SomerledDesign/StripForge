# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""StripForge command line. `analyze` works (M1); the other subcommands are stubs (Sketch.md §7)."""

import argparse
import json
import sys


def _analyze(args: argparse.Namespace) -> int:
    from . import config as config_mod
    from .analyze import analyze, format_text, to_dict

    cfg = config_mod.load(args.config) if args.config else config_mod.BoardConfig()
    if args.cut_style:
        cfg.cut_style = config_mod.CutStyle(args.cut_style)
    if args.tol is not None:
        cfg.snap_tol_mm = args.tol
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
