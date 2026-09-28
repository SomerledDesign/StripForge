"""Open-net detection (union-find over pieces) and link proposals. Stub (Sketch.md §4.4)."""

from dataclasses import dataclass


@dataclass
class LinkProposal:
    ref_hint: str  # "W1"...
    net: str
    col: int
    row_a: int
    row_b: int
    footprint: str  # from Mildrew's zero-ohm link family, e.g. "StripForge:Link_P10.16"


def propose(pieces, net_pads):
    raise NotImplementedError
