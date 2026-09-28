# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Board configuration (stripboard.toml)."""

from __future__ import annotations

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 (KiCad 10's bundled Python is 3.9)
    import tomli as tomllib
from dataclasses import dataclass, field, fields
from enum import Enum
from pathlib import Path


class CutStyle(str, Enum):  # a StrEnum, written so it also runs on Python 3.9 (KiCad's)
    HOLE = "hole"  # spot-face cut at a free hole (default)
    KNIFE = "knife"  # score between two adjacent holes
    AUTO = "auto"  # hole when possible, else knife (+ warning)

    def __str__(self) -> str:
        return self.value

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)


# KiCad 10 DRC violation type names (the "type" of each item in kicad-cli's JSON report; the list
# of pcbnew/drc/drc_item.cpp on the 10.0 branch). Used to warn about a mistyped [drc] ignore entry.
KICAD_DRC_TYPES = frozenset(
    """annular_width assertion_failure clearance connection_width copper_edge_clearance copper_sliver
    courtyards_overlap creepage diff_pair_gap_out_of_range diff_pair_uncoupled_length_too_long
    drill_out_of_range duplicate_footprints extra_footprint footprint footprint_filters_mismatch
    footprint_symbol_field_mismatch footprint_symbol_mismatch footprint_type_mismatch generic_error
    generic_warning hole_clearance hole_to_hole holes_co_located invalid_outline isolated_copper
    item_on_disabled_layer items_not_allowed length_out_of_range lib_footprint_issues
    lib_footprint_mismatch malformed_courtyard microvia_drill_out_of_range mirrored_text_on_front_layer
    missing_courtyard missing_footprint missing_tuning_profile net_conflict
    nonmirrored_text_on_back_layer npth_inside_courtyard padstack padstack_invalid pth_inside_courtyard
    shorting_items silk_edge_clearance silk_over_copper silk_overlap skew_out_of_range
    solder_mask_bridge starved_thermal text_height text_on_edge_cuts text_thickness
    through_hole_pad_without_hole too_many_vias track_angle track_dangling track_not_centered_on_via
    track_on_post_machined_layer track_segment_length track_width tracks_crossing
    tuning_profile_track_geometries unresolved_variable via_dangling via_diameter
    zones_intersect""".split()
)


@dataclass
class DrcConfig:
    """The ``[drc]`` table: what ``stripforge drc`` (and the plugin's Run DRC) may filter.

    ``ignore``: KiCad DRC violation types suppressed board-wide (e.g. ``silk_overlap``).
    ``allow_overlap``: reference pairs (order does not matter) whose courtyard overlaps are
    accepted: ``courtyards_overlap`` and ``pth_inside_courtyard`` / ``npth_inside_courtyard``
    items between exactly those two footprints. Everything suppressed is still counted in the
    report ("filtered (config): ...").
    """

    ignore: list[str] = field(default_factory=list)
    allow_overlap: list[tuple[str, str]] = field(default_factory=list)

    def unknown_types(self) -> list[str]:
        return [t for t in self.ignore if t not in KICAD_DRC_TYPES]

    def allows(self, a: str, b: str) -> bool:
        return frozenset((a, b)) in {frozenset(p) for p in self.allow_overlap}


def drc_from_dict(data: object) -> DrcConfig:
    if not isinstance(data, dict):
        raise ValueError("[drc] must be a table")
    known = {f.name for f in fields(DrcConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"unknown [drc] key(s): {', '.join(unknown)} (expected: {', '.join(sorted(known))})")
    ignore = data.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(t, str) and t for t in ignore):
        raise ValueError('[drc] ignore must be a list of KiCad DRC type names, e.g. ["silk_overlap"]')
    pairs = []
    for p in data.get("allow_overlap", []):
        if not (isinstance(p, list) and len(p) == 2 and all(isinstance(r, str) and r for r in p)):
            raise ValueError(
                f'[drc] allow_overlap entries must be reference pairs like ["J2", "C2"], got {p!r}'
            )
        if p[0] == p[1]:
            raise ValueError(f"[drc] allow_overlap pair {p!r} names the same part twice")
        pairs.append((p[0], p[1]))
    return DrcConfig(ignore=list(ignore), allow_overlap=pairs)


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
    # 1.8 mm: caliper-measured on Kevin's X56 board (2026-09-27). rules/stripforge.kicad_dru must be
    # regenerated with rules/gen_dru.py --strip-width <w> whenever this changes.
    strip_width_mm: float = 1.8
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
    drc: DrcConfig = field(default_factory=DrcConfig)  # the [drc] table

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
    data = dict(data)
    drc = drc_from_dict(data.pop("drc", {}))
    cfg = BoardConfig(**data)
    cfg.drc = drc
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
