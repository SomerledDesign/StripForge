# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Board configuration (stripboard.toml)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from enum import StrEnum
from pathlib import Path


class CutStyle(StrEnum):
    HOLE = "hole"  # spot-face cut at a free hole (default)
    KNIFE = "knife"  # score between two adjacent holes
    AUTO = "auto"  # hole when possible, else knife (+ warning)


@dataclass
class BoardConfig:
    # Grid. Anything left as None is derived from the board's Edge.Cuts outline: the first hole
    # sits hole_inset_mm (default: half a pitch) in from the outline's top-left corner, and the
    # grid fills the outline.
    rows: int | None = None
    cols: int | None = None
    origin_mm: tuple[float, float] | None = None  # centre of hole (col 0, row 0)
    pitch_mm: float = 2.54
    hole_inset_mm: float | None = None
    strip_width_mm: float = 1.8  # Mildrew to confirm
    cut_style: CutStyle = CutStyle.AUTO
    snap_tol_mm: float = 0.15
    cut_marker_layer: str = "User.1"  # renamed "Strip.Cuts" in the board file
    offboard_refs: list[str] = field(default_factory=list)
    # Parts with slotted pads or a pin spacing that is not a multiple of the pitch (the BH23APC
    # battery holder). A listed part's pad may sit off its hole along the strip (x) by up to
    # slot_max_mm, provided it is within snap_tol_mm of the strip centreline in y; the hole is
    # then filed into a short slot toward the pad (a "slot job") instead of rejecting the part.
    slotted: list[str] = field(default_factory=list)
    slot_max_mm: float = 1.0
    slot_max_mm_by_ref: dict[str, float] = field(default_factory=dict)  # per-ref override

    def slot_max_for(self, ref: str) -> float | None:
        """Slot allowance in mm for ``ref``, or None if the part is not slotted."""
        if ref not in self.slotted:
            return None
        return self.slot_max_mm_by_ref.get(ref, self.slot_max_mm)


def from_dict(data: dict) -> BoardConfig:
    known = {f.name for f in fields(BoardConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown stripboard config key(s): {', '.join(unknown)}")
    cfg = BoardConfig(**data)
    if cfg.origin_mm is not None:
        if len(cfg.origin_mm) != 2:
            raise ValueError("origin_mm must be [x, y]")
        cfg.origin_mm = (float(cfg.origin_mm[0]), float(cfg.origin_mm[1]))
    cfg.cut_style = CutStyle(cfg.cut_style)
    for name in ("rows", "cols"):
        v = getattr(cfg, name)
        if v is not None and (not isinstance(v, int) or v < 1):
            raise ValueError(f"{name} must be a positive integer")
    if cfg.pitch_mm <= 0 or cfg.snap_tol_mm < 0:
        raise ValueError("pitch_mm must be > 0 and snap_tol_mm >= 0")
    cfg.offboard_refs = list(cfg.offboard_refs)
    cfg.slotted = [str(r) for r in cfg.slotted]
    cfg.slot_max_mm_by_ref = {str(k): float(v) for k, v in dict(cfg.slot_max_mm_by_ref).items()}
    for ref, v in [("slot_max_mm", cfg.slot_max_mm), *cfg.slot_max_mm_by_ref.items()]:
        if not 0 <= v < cfg.pitch_mm / 2:
            raise ValueError(f"slot allowance for {ref} must be between 0 and half a pitch, got {v}")
    unknown = sorted(set(cfg.slot_max_mm_by_ref) - set(cfg.slotted))
    if unknown:
        raise ValueError(f"slot_max_mm_by_ref lists refs that are not in slotted: {', '.join(unknown)}")
    return cfg


def load(path: str | Path) -> BoardConfig:
    with open(path, "rb") as fh:
        return from_dict(tomllib.load(fh))
