# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Pure-Python pre-DRC checks on the strip model (Sketch.md §4.6, belt and braces).

M1 checks shorts only: a piece carrying more than one net, or a pad sitting in a hole that was
cut away. Opens (split nets) are reported by the splitter; link and parity checks come in M2.
"""

from __future__ import annotations

from .grid import Node
from .splitter import SplitResult
from .strips import HoleMap, Strip


def validate(split: SplitResult, holes: HoleMap, strips: list[Strip]) -> list[str]:
    errors: list[str] = []
    for p in split.multi_net_pieces:
        errors.append(f"short: strip piece {p.label} carries {len(p.nets)} nets: " + ", ".join(p.nets))
    for strip in strips:
        for c in sorted(strip.dead_holes):
            node = Node(strip.row, c)
            if not holes.is_free(node):
                who = ", ".join(o.label for o in holes.occupants[node])
                errors.append(f"hole cut at occupied hole {node} ({who})")
    return errors
