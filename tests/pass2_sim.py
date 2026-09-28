"""Simulate pass 2 of the link flow: add the proposed W links as if F8 had brought them in.

Adds each proposed link to a copy of the board (the embedded StripForge:Link_P* footprint with
both pads on the link's net, dropped off to the right of the board, as F8 drops new parts) and to
a copy of the netlist (a 2-pin component whose pins are on that net).

    python tests/pass2_sim.py <board> <netlist> <out.links.json> <out-dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from stripforge.board import load_board
from stripforge.sexpr import Sym, atom, find, find_all, parse
from stripforge.writer import embed_footprint


def add_links_to_board(board_path: Path, links: list[dict], out: Path) -> None:
    board = load_board(board_path)
    root = board.doc.root
    x0 = (board.outline[2] if board.outline else 0) + 10_000_000
    fps = []
    for i, lk in enumerate(links):
        name = lk["footprint"].split(":")[-1]
        node = embed_footprint(
            name,
            lk["ref"],
            x0 + i * 3_000_000,
            60_000_000,
            f"sim/{lk['ref']}",
            {"1": lk["net"], "2": lk["net"]},
        )
        fps.append(node)
    last = max(i for i, n in enumerate(root) if isinstance(n, list) and n and n[0] == "footprint")
    root[last + 1 : last + 1] = fps
    out.write_text(board.doc.dumps(), encoding="utf-8")


def add_links_to_netlist(net_path: Path, links: list[dict], out: Path) -> None:
    doc = parse(net_path.read_text(encoding="utf-8"))
    comps = find(doc.root, "components")
    nets = {atom(find(n, "name"), 1): n for n in find_all(find(doc.root, "nets"), "net")}
    for lk in links:
        comps.append(
            [
                Sym("comp"),
                [Sym("ref"), lk["ref"]],
                [Sym("value"), "0R"],
                [Sym("footprint"), lk["footprint"]],
                [Sym("sheetpath"), [Sym("names"), "/"], [Sym("tstamps"), "/"]],
            ]
        )
        for pin in ("1", "2"):
            nets[lk["net"]].append(
                [Sym("node"), [Sym("ref"), lk["ref"]], [Sym("pin"), pin], [Sym("pintype"), "passive"]]
            )
    out.write_text(doc.dumps(), encoding="utf-8")


def main(argv: list[str]) -> None:
    board, netlist, links_json, out_dir = (Path(a) for a in argv)
    links = json.loads(links_json.read_text())["links"]
    out_dir.mkdir(parents=True, exist_ok=True)
    add_links_to_board(board, links, out_dir / board.name)
    add_links_to_netlist(netlist, links, out_dir / netlist.name)
    print(f"added {len(links)} W link(s) to {out_dir / board.name} and {out_dir / netlist.name}")


if __name__ == "__main__":
    main(sys.argv[1:])
