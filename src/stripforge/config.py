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


# The "Beckham tolerance" (bend it like Beckham): how far a [bend] part's legs may be off their
# holes because the builder bends them on. Above BEND_WARN_MM a warning says to check the part
# really bends that far; above BEND_MAX_MM it is refused (at that point it is an adapter job).
BEND_WARN_MM = 0.3
BEND_MAX_MM = 0.5


def bend_from_dict(data: object) -> tuple[dict[str, float], list[str]]:
    """The ``[bend]`` table (``SW2 = 0.16``): ``({ref: mm}, warnings)``. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("[bend] must be a table of reference = millimetres, e.g. SW2 = 0.16")
    out, warns = {}, []
    for ref, v in data.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"[bend] {ref} must be a number of millimetres (6 mil = 0.1524 mm), got {v!r}")
        v = float(v)
        if v <= 0:
            raise ValueError(f"[bend] {ref} must be more than 0 mm, got {v:g}")
        if v > BEND_MAX_MM:
            raise ValueError(
                f"[bend] {ref} = {v:g} mm is more than {BEND_MAX_MM:g} mm; legs that far off need an "
                "adapter or a different footprint, not bending"
            )
        if v > BEND_WARN_MM:
            warns.append(
                f"[bend] {ref} = {v:g} mm is a big bend (over {BEND_WARN_MM:g} mm): check the legs "
                "really reach the holes without stressing the part"
            )
        out[str(ref)] = v
    return out, warns


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
    # The [bend] table, the "Beckham tolerance" (bend it like Beckham): {ref: mm}. A listed part's
    # pads may be up to that far from their holes in any direction (across the strip for a slotted
    # part), overriding snap_tol_mm for that part only; the report and build sheet say which legs
    # to bend and how far. E.g. SW2 = 0.16 for a 312 mil row pitch on 300 mil holes (6 mil a row).
    bend: dict[str, float] = field(default_factory=dict)
    # Parts not on the stripboard (hand-wired, panel-mounted): no snap, no strips for their pads,
    # listed as "wired off-board" on the build sheet. offboard_refs is the older name; both work.
    skip: list[str] = field(default_factory=list)
    # Wire links (M2): the longest link the planner may propose, in mm (the Link_P* footprints go
    # up to 32 pitches, 81.28 mm). Links run down a column, along a strip (bridging a cut), or on a
    # diagonal from any free hole to any free hole (a rotated Link_P* when the length is a whole
    # number of pitches, 3-4-5 and friends, else a rotated off-pitch Link_D*; off_pitch_links = false
    # keeps to whole-pitch diagonals); with bus_strips, two links may meet on an unused bare strip.
    max_link_mm: float = 81.28
    diagonal_links: bool = True
    off_pitch_links: bool = True
    bus_strips: bool = True
    # Build: draw every stripboard hole (a plated pad on its strip's net) in the built board, so it
    # looks like the real board in KiCad and the 3D viewer; hole cuts are drawn as bare holes.
    draw_holes: bool = True
    hole_drill_mm: float = 1.0
    # Your own cuts and links. Building from a board with StripForge cut markers and placed W links
    # (e.g. the built board after moving them in pcbnew) keeps them where they are and only fills
    # in what is missing; false plans from scratch. [manual] gives them as text:
    # links = ["J16-T16"], cuts = ["J15", "C34-C35"] (hole, knife), no_cut = ["J17"].
    respect_edits: bool = True
    manual: dict = field(default_factory=dict)
    # Non-fatal config problems found while loading (e.g. a very large [bend]); shown as warnings.
    warnings: list[str] = field(default_factory=list)

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
    if "warnings" in data:
        raise ValueError("unknown stripboard config key(s): warnings")
    drc = drc_from_dict(data.pop("drc", {}))
    bend, bend_warns = bend_from_dict(data.pop("bend", {}))
    from .edits import manual_from_dict

    manual = manual_from_dict(data.pop("manual", {}))
    cfg = BoardConfig(**data)
    cfg.drc = drc
    cfg.bend = bend
    cfg.manual = manual
    cfg.warnings = bend_warns
    if not isinstance(cfg.skip, list) or not all(isinstance(r, str) and r for r in cfg.skip):
        raise ValueError('skip must be a list of references, e.g. skip = ["SW3"]')
    cfg.skip = [str(r) for r in cfg.skip]
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
    for name in ("max_link_mm", "hole_drill_mm"):
        v = getattr(cfg, name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise ValueError(f"{name} must be a number of millimetres more than 0, got {v!r}")
        setattr(cfg, name, float(v))
    if cfg.hole_drill_mm >= cfg.strip_width_mm:
        raise ValueError(f"hole_drill_mm ({cfg.hole_drill_mm:g}) must be smaller than strip_width_mm")
    for name in ("diagonal_links", "off_pitch_links", "bus_strips", "draw_holes", "respect_edits"):
        if not isinstance(getattr(cfg, name), bool):
            raise ValueError(f"{name} must be true or false")
    # skip and offboard_refs mean the same; the rest of the code reads offboard_refs
    cfg.offboard_refs = list(dict.fromkeys([*cfg.offboard_refs, *cfg.skip]))
    cfg.skip = list(cfg.offboard_refs)  # the user's list (the writer adds W links to offboard_refs only)
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
