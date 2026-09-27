"""Cut placement and net-per-piece assignment. Stub (Sketch.md §4.3, §4.5)."""

from dataclasses import dataclass


@dataclass
class Cut:
    id: str  # "X1"...
    row: int
    col: float  # integer = hole cut at that hole; x.5 = knife cut between x and x+1
    style: str  # "hole" | "knife"
    reason: tuple[str, str]  # the two nets separated


def place_cuts(strips, occupancy, style):
    raise NotImplementedError


def assign_nets(strips, occupancy):
    raise NotImplementedError
