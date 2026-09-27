"""Strip model: each row is cols-1 hole-to-hole segments, present or cut. Stub (Sketch.md §4.3)."""

from dataclasses import dataclass, field


@dataclass
class Strip:
    row: int
    cols: int
    present: list[bool] = field(default_factory=list)  # segment c joins (row,c)-(row,c+1)
    dead_holes: set[int] = field(default_factory=set)  # holes consumed by hole cuts


@dataclass
class Piece:
    row: int
    col_start: int
    col_end: int
    net: str | None
