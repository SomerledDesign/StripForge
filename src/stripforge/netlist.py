"""Parse `kicad-cli sch export netlist --format kicadsexpr` output. Stub."""

from dataclasses import dataclass


@dataclass
class Component:
    ref: str
    footprint: str  # lib_id, e.g. "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
    sheet_path: str  # for schematic parity


@dataclass
class Net:
    name: str
    nodes: list[tuple[str, str]]  # (ref, pad number)


def parse(path: str) -> tuple[list[Component], list[Net]]:
    raise NotImplementedError
