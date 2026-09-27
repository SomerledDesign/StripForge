"""Command-line entry point. Subcommands are stubs; see Sketch.md §4 and §7."""

import argparse


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="stripforge", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, help_ in [
        ("plan", "snap + split + cut/link proposal, JSON report only"),
        ("generate", "write strips/cuts/links into a .kicad_pcb"),
        ("drc", "run kicad-cli DRC and classify shorts/opens/parity"),
        ("sheet", "export copper-side build sheet (SVG/PDF) + CSVs"),
    ]:
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("board", help="path to .kicad_pcb")
        sp.add_argument("--config", default="stripboard.toml")
    args = p.parse_args(argv)
    raise NotImplementedError(f"'{args.cmd}' is not implemented yet (v0 skeleton)")


if __name__ == "__main__":
    raise SystemExit(main())
