# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""StripForge command line: analyze, snap, build, link-symbols, drc and sheet (plan is a stub).

See Sketch.md §7.
"""

from __future__ import annotations

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
    if getattr(args, "place_links", False):
        cfg.place_links = True
    return cfg


def _link_symbols(args: argparse.Namespace) -> int:
    from .linksym import LinkSymbolError, add_link_symbols, format_text

    if args.schematic is None:
        from .writer import base_stem

        sch = Path(args.board).with_name(base_stem(args.board) + ".kicad_sch")
        if not sch.is_file():
            print(
                f"stripforge link-symbols: error: no {sch.name} next to the board; pass --schematic",
                file=sys.stderr,
            )
            return 2
        args.schematic = str(sch)
    try:
        res = add_link_symbols(
            args.schematic, args.board, out_dir=args.out_dir, in_place=args.in_place, symbol_lib=args.symbols
        )
    except (LinkSymbolError, OSError, ValueError) as exc:
        print(f"stripforge link-symbols: error: {exc}", file=sys.stderr)
        return 2
    print(format_text(res))
    if res.outputs:
        print("Wrote: " + ", ".join(res.outputs))
    return 0


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


def _build(args: argparse.Namespace) -> int:
    from . import links as links_mod
    from .writer import BuildError, build, resolve_output

    try:
        cfg = _config(args)
        if args.separate:
            cfg.output = "separate"
        out, in_place = resolve_output(args.board, cfg, args.output)
        res = build(
            args.board,
            cfg,
            out,
            netlist=args.netlist,
            library=args.library,
            rules=args.rules,
            in_place=in_place and (args.output is None or args.in_place),
        )
    except (BuildError, OSError, ValueError) as exc:
        print(f"stripforge build: error: {exc}", file=sys.stderr)
        return 2
    a, plan = res.analysis, res.plan
    hole = sum(1 for c in a.split.cuts if c.style == "hole")
    print(f"StripForge build: {args.board} -> {out}" + (" (in place)" if in_place else ""))
    if res.backup:
        how = "made now, before the first write" if res.backup_created else "kept as it was (not replaced)"
        print(
            f"Backup: {res.backup} ({how}). To undo the build: delete {Path(out).name} and rename the "
            f"backup to {Path(out).name}"
        )
    print(
        f"Parts: {len(a.snapped)} snapped, {len(res.moves)} moved by their best-fit shift, "
        f"{len(a.rejected)} rejected"
    )
    if any(res.removed_previous):
        t, c = res.removed_previous
        print(f"Removed the previous StripForge output: {t} strip track(s), {c} cut marker(s)")
    print(
        f"Strips: {res.segments} B.Cu track(s) at {a.config.strip_width_mm:g} mm "
        f"({res.segments_no_net} on bare strip with no net)"
    )
    print(
        f"Cuts: {len(a.split.cuts)} ({hole} hole, {len(a.split.cuts) - hole} knife), "
        f"{res.cut_markers} marker(s)"
    )
    if res.holes_drawn:
        print(f"Holes: {res.holes_drawn} stripboard hole(s) drawn (every free grid hole; hole cuts bare)")
    sys.stdout.write(
        links_mod.format_text(plan, a.config.link_lead_allowance_in).split("\n\nTo add")[0].rstrip("\n")
        + "\n"
    )
    if res.stretches:
        from .stretch import format_text as stretch_text

        sys.stdout.write(stretch_text(res.stretches))
    if a.config.place_links and plan.links:
        print(f"Links: {len(res.placed)} of {len(plan.links)} placed on their holes (place_links)")
        for p in res.link_problems:
            print(f"  {p.status.upper()}: {p.ref} {p.detail}")
        print(
            "Next: 'stripforge link-symbols <board> --in-place' (or --out-dir DIR) writes the W symbols "
            "into <name>.kicad_sch; then F8 matches them up"
        )
    elif res.pass2:
        print(f"Pass 2: {len(res.placed)} of {len(plan.links)} link(s) placed")
        for p in res.link_problems:
            print(f"  {p.status.upper()}: {p.ref} {p.detail}")
    elif links_mod.refs_to_add(plan):
        print(f"Pass 1: add {links_mod.refs_to_add(plan)} to the schematic, press F8, then build again")
    if a.warnings or res.warnings:
        print(f"Warnings: {len(a.warnings) + len(res.warnings)}")
        for w in list(res.warnings) + list(a.warnings):
            print(f"  - {w}")
    print("Wrote: " + ", ".join(res.outputs))
    return 0 if res.ok else 1


def _drc(args: argparse.Namespace) -> int:
    from . import drc

    label = None
    drc_config = None
    if args.config:
        try:
            drc_config = _config(args).drc
        except (OSError, ValueError) as exc:  # a broken [drc] table is an input error, not noise
            print(f"stripforge drc: error: {args.config}: {exc}", file=sys.stderr)
            return 2
    try:
        from .analyze import make_grid
        from .board import load_board

        grid, _ = make_grid(load_board(args.board), _config(args))
        label = lambda x, y: grid.nearest(round(x * 1e6), round(y * 1e6)).label  # noqa: E731
    except (OSError, ValueError):
        pass
    if args.schematic is None and not args.no_parity:
        from .writer import base_stem

        sch = Path(args.board).with_name(base_stem(args.board) + ".kicad_sch")
        if sch.is_file() and sch.stem != Path(args.board).stem:
            args.schematic = str(sch)  # a -stripforge board: check parity against <name>.kicad_sch
    try:
        res = drc.run_drc(
            args.board,
            args.kicad_cli,
            parity=not args.no_parity,
            report_path=args.report,
            schematic=args.schematic,
            drc_config=drc_config,
        )
    except drc.KiCadCliMissing as exc:
        print(f"stripforge drc: skipped: {exc}", file=sys.stderr)
        return 3
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"stripforge drc: error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(
            {"board": args.board, **res.counts(), "unconnected_nets": res.unconnected_nets()},
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        expected = None
        from .writer import link_file

        links_json = link_file(args.board, ".json")
        if links_json.exists():
            try:
                expected = json.loads(links_json.read_text(encoding="utf-8")).get("links_needed")
            except (OSError, ValueError):
                pass
        sys.stdout.write(drc.format_text(res, args.board, label, expected_links=expected))
    return 0 if res.ok else 1


def _sheet(args: argparse.Namespace) -> int:
    from .buildsheet import write_sheet
    from .writer import BuildError

    try:
        res = write_sheet(
            args.board,
            _config(args),
            args.output,
            netlist=args.netlist,
            pdf=not args.no_pdf,
            png=args.png,
            chrome=args.chrome,
            date=args.date,
        )
    except (BuildError, OSError, ValueError) as exc:
        print(f"stripforge sheet: error: {exc}", file=sys.stderr)
        return 2
    m = res.model
    a = m.a
    print(f"StripForge sheet: {args.board}")
    print(
        f"Cuts: {len(a.split.cuts)}, slot jobs: {len(a.slot_jobs)}, wire links: {len(m.links)} "
        f"({sum(r.status == 'placed' for r in m.links)} placed), "
        f"parts: {sum(r.group != 0 for r in m.parts)}, "
        f"nets to check: {len(m.nets)}"
    )
    if m.built != "built":
        print(f"NOTE: {m.warnings[0]}")
    for n in res.notes:
        print(f"note: {n}")
    print("Wrote: " + ", ".join(res.outputs))
    return 0


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

    bu = sub.add_parser(
        "build",
        help="write strip copper, cut markers and placed links into the board (in place, with a backup)",
        description="Snap the parts, split the strips by net, propose wire links and write the built board: "
        "B.Cu strip tracks on their nets, real gaps at the cuts with embedded StripForge:CUT_* markers, "
        'and any W link footprints (after F8) placed on their holes. By default (output = "in_place") '
        "the board itself is rewritten, after copying it once to <name>-pre-stripbuild.kicad_pcb (a "
        "rebuild never replaces that copy; undo = delete the built board, rename the backup back); "
        '--separate / output = "separate" writes <name>-stripforge.kicad_pcb instead. Also writes '
        "<out>.kicad_dru (the StripForge rules) and <name>-stripforge.links.json/.csv/.txt (the link "
        "proposal and schematic instructions). "
        "Exit code 0 = written and complete, 1 = written but nets still need links or W parts are "
        "missing/wrong, 2 = refused or input error (nothing written).",
    )
    bu.add_argument("board", help="the placement .kicad_pcb (the project's own <name>.kicad_pcb)")
    bu.add_argument(
        "-o",
        "--output",
        help='the .kicad_pcb to write (default, output = "in_place": the board itself, after a one-time '
        'backup to <name>-pre-stripbuild.kicad_pcb; output = "separate": <name>-stripforge.kicad_pcb)',
    )
    bu.add_argument(
        "--separate",
        action="store_true",
        help='write <name>-stripforge.kicad_pcb and leave the board alone (output = "separate")',
    )
    bu.add_argument("--netlist", help="kicadsexpr .net file to cross-check pad nets against")
    bu.add_argument("--config", help="stripboard.toml (default: derive the grid from Edge.Cuts)")
    bu.add_argument("--cut-style", choices=["hole", "knife", "auto"], help="override the config")
    bu.add_argument("--tol", type=float, help="snap tolerance in mm (overrides the config)")
    bu.add_argument("--library", help="StripForge.pretty directory (default: the repository copy)")
    bu.add_argument("--rules", help="stripforge.kicad_dru (default: the repository copy)")
    bu.add_argument(
        "--in-place",
        action="store_true",
        help='allow -o to be the input (what the default output = "in_place" does without -o): '
        "rebuild a built board after moving its cuts and links in pcbnew "
        "(they are kept where you put them; StripForge fills in the rest)",
    )
    bu.add_argument(
        "--place-links",
        action="store_true",
        help="place the W link footprints on their holes now (place_links = true), for 'stripforge "
        "link-symbols' to carry into the schematic",
    )
    bu.set_defaults(func=_build)

    ls = sub.add_parser(
        "link-symbols",
        help="write the board's W links into the schematic as StripForge:Link symbols",
        description="Add a StripForge:Link symbol for every W link footprint on the board that the schematic "
        "lacks (KiCad's Update Schematic from PCB can't add symbols): reference, Footprint field, and a "
        "label with the net's name on both pins, on the sheet of the net. Written to a copy of the whole "
        "schematic (--out-dir, with the project file, library tables and the board, ready for kicad-cli) "
        "or in place (--in-place; every changed sheet is first copied to a timestamped .bak).",
    )
    ls.add_argument("board", help="the built .kicad_pcb with the W link footprints placed")
    ls.add_argument(
        "--schematic", help="the root .kicad_sch of the project (default: <name>.kicad_sch next to the board)"
    )
    where = ls.add_mutually_exclusive_group(required=True)
    where.add_argument("--out-dir", help="write a copy of the schematic (and project) here")
    where.add_argument("--in-place", action="store_true", help="edit the schematic (backups first)")
    ls.add_argument("--symbols", help="StripForge.kicad_sym (default: the repository copy)")
    ls.set_defaults(func=_link_symbols)

    dr = sub.add_parser(
        "drc",
        help="run kicad-cli DRC and classify shorts, clearance, unconnected and parity",
        description="Run 'kicad-cli pcb drc --format json --severity-all --schematic-parity' and sort the "
        "result into real problems (shorts, clearance, unconnected, parity, StripForge rules, other errors) "
        "and expected noise (track_dangling from dead strip ends, library-not-configured warnings), which "
        "is counted but filtered; with --config, the [drc] table's ignore types and allow_overlap pairs are "
        "filtered and counted too. Exit code 0 = clean, 1 = real problems, 2 = kicad-cli failed, "
        "3 = kicad-cli not found (skipped).",
    )
    dr.add_argument("board", help="path to .kicad_pcb")
    dr.add_argument(
        "--config", help="stripboard.toml: label positions with holes (A1...) and apply its [drc] table"
    )
    dr.add_argument("--kicad-cli", help="path to kicad-cli (default: $KICAD_CLI, PATH, the macOS app)")
    dr.add_argument("--no-parity", action="store_true", help="skip the schematic parity check")
    dr.add_argument(
        "--schematic",
        help="check parity against this .kicad_sch (a <name>-stripforge board has none beside it)",
    )
    dr.add_argument("--report", help="also keep kicad-cli's JSON report here")
    dr.add_argument("--json", action="store_true", help="machine-readable summary")
    dr.set_defaults(func=_drc, tol=None)

    sh = sub.add_parser(
        "sheet",
        help="write a printable build sheet (HTML, PDF when Chrome is found) for a built board",
        description="Write a self-contained, printable HTML build sheet for a board built with 'stripforge "
        "build': the copper side MIRRORED (as you hold the board to cut) with every cut, the component side "
        "with parts and wire links, checklists (cuts by strip, slot jobs, links, parts in build order), "
        "a net continuity table and warnings. Also writes <out>.copper.svg, <out>.component.svg and "
        "<out>.cuts.csv, "
        "and <out>.pdf through headless Chrome/Chromium when one is found. "
        "Exit code 0 = written, 2 = input error.",
    )
    sh.add_argument("board", help="the built .kicad_pcb (the output of 'stripforge build')")
    sh.add_argument("-o", "--output", required=True, help="the .html to write")
    sh.add_argument("--netlist", help="kicadsexpr .net file to cross-check pad nets against")
    sh.add_argument("--config", help="stripboard.toml (default: derive the grid from Edge.Cuts)")
    sh.add_argument("--cut-style", choices=["hole", "knife", "auto"], help="override the config")
    sh.add_argument("--tol", type=float, help="snap tolerance in mm (overrides the config)")
    sh.add_argument("--no-pdf", action="store_true", help="HTML (and SVG/CSV) only")
    sh.add_argument("--png", action="store_true", help="also write PNG previews of both views (needs Chrome)")
    sh.add_argument(
        "--chrome", help="path to Chrome/Chromium (default: $STRIPFORGE_CHROME, PATH, macOS apps)"
    )
    sh.add_argument("--date", help="date printed in the header (default: today, YYYY-MM-DD)")
    sh.set_defaults(func=_sheet)

    for name, help_ in [
        ("plan", "snap + split + cut/link proposal, JSON report only"),
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
