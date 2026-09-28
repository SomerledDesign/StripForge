"""Tiny builders for synthetic KiCad S-expression text used by the unit tests."""

from __future__ import annotations


def pcb(*footprints: str, outline: tuple[float, float, float, float] | None = (0, 0, 25.4, 12.7)) -> str:
    """A minimal .kicad_pcb text. Default outline: 10 cols x 5 rows, hole (0,0) at (1.27, 1.27)."""
    edge = ""
    if outline:
        x0, y0, x1, y1 = outline
        edge = f'(gr_rect (start {x0} {y0}) (end {x1} {y1}) (layer "Edge.Cuts"))'
    return f'(kicad_pcb (version 20260206) (generator "pcbnew") {" ".join(footprints)} {edge})'


def fp(ref: str, at: str, *pads: str, lib: str = "Test:FP") -> str:
    return (
        f'(footprint "{lib}" (layer "F.Cu") (at {at}) '
        f'(property "Reference" "{ref}" (at 0 0 0) (layer "F.SilkS")) {" ".join(pads)})'
    )


def pad(num: str, at: str, net: str | None = None, kind: str = "thru_hole", drill: str = "1") -> str:
    n = f' (net "{net}")' if net else ""
    return f'(pad "{num}" {kind} circle (at {at}) (size 1.7 1.7) (drill {drill}) (layers "*.Cu" "*.Mask"){n})'
