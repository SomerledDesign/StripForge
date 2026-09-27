"""2.54 mm grid model and per-footprint rigid snapping. Stub (Sketch.md §4.2)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    row: int
    col: int


@dataclass
class SnapResult:
    ref: str
    dx_nm: int
    dy_nm: int
    rotation_deg: int
    max_dev_nm: int  # e.g. ~40_000 for C_Disc P2.50, ~10_000 for Littelfuse 395
    pad_nodes: dict[str, Node]
    accepted: bool


def snap_footprint(ref, pads_nm, tol_nm) -> SnapResult:
    """Choose translation (+90° steps) minimising max pad-to-node deviation."""
    raise NotImplementedError
