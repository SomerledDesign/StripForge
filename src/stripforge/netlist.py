# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Parse `kicad-cli sch export netlist --format kicadsexpr` output (.net)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .sexpr import atom, find, find_all, head, loads


@dataclass
class Component:
    ref: str
    footprint: str  # lib_id, e.g. "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
    sheet_path: str  # for schematic parity
    value: str = ""


@dataclass
class Net:
    name: str
    nodes: list[tuple[str, str]] = field(default_factory=list)  # (ref, pad number)
    code: str = ""


def parse_text(text: str) -> tuple[list[Component], list[Net]]:
    root = loads(text)
    if head(root) != "export":
        raise ValueError(f"not a kicadsexpr netlist (top-level node is {head(root)!r})")
    comps: list[Component] = []
    comps_node = find(root, "components")
    for c in find_all(comps_node or [], "comp"):
        sheet = find(c, "sheetpath")
        comps.append(
            Component(
                ref=atom(find(c, "ref"), 1, "") or "",
                footprint=atom(find(c, "footprint"), 1, "") or "",
                sheet_path=atom(find(sheet, "names"), 1, "") if sheet is not None else "",
                value=atom(find(c, "value"), 1, "") or "",
            )
        )
    nets: list[Net] = []
    for n in find_all(find(root, "nets") or [], "net"):
        net = Net(name=atom(find(n, "name"), 1, "") or "", code=atom(find(n, "code"), 1, "") or "")
        for node in find_all(n, "node"):
            net.nodes.append((atom(find(node, "ref"), 1, "") or "", atom(find(node, "pin"), 1, "") or ""))
        nets.append(net)
    return comps, nets


def parse(path: str | Path) -> tuple[list[Component], list[Net]]:
    return parse_text(Path(path).read_text(encoding="utf-8"))


def pin_nets(nets: list[Net]) -> dict[tuple[str, str], str]:
    """Map (ref, pin) -> net name."""
    return {node: net.name for net in nets for node in net.nodes}
