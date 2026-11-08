# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Board configuration (stripboard.toml)."""

from __future__ import annotations

import json
import os
import re

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
    # Parts with big pads (e.g. a slide switch's 2.9 mm blade pads): every cut next to their pins is
    # a knife cut between holes, never a drilled hole cut, placed so the hole beside each pin stays on
    # that pin's net (the pad overhangs it); the link planner never slides those cuts closer.
    knife_cuts: list[str] = field(default_factory=list)
    # After link planning, cut every net piece back to its outermost used hole (pin, link end) when
    # that frees at least trim_min_free holes, so the rest of the strip is bare copper again. When a
    # net is left unjoined, planning is also tried with the pieces trimmed first (more bare strip for
    # buses) and kept if it joins more.
    trim_pieces: bool = True
    trim_min_free: int = 4
    # Wire links (M2): the longest link the planner may propose, in mm (the Link_P* footprints go
    # up to 32 pitches, 81.28 mm). Links run down a column or along a strip (bridging a cut); with
    # bus_strips, two links may meet on an unused bare strip. Diagonal links are off by default
    # (M4c): with diagonal_links = true a link may also drop to another strip and move at most
    # max_diagonal_shift holes along it (a rotated Link_P* when the length is a whole number of
    # pitches, 3-4-5 and friends, else a rotated off-pitch Link_D*; off_pitch_links = false keeps to
    # whole-pitch diagonals, which need a shift of 3 or more).
    max_link_mm: float = 81.28
    # The link cut list gives pad-to-pad lengths; set this (inches a leg, e.g. 0.15) to add a
    # "cut" column: pad-to-pad + 2 x allowance for the legs through the holes. 0 = no column.
    link_lead_allowance_in: float = 0.0
    diagonal_links: bool = False
    max_diagonal_shift: int = 1
    off_pitch_links: bool = True
    bus_strips: bool = True
    # Build: draw every stripboard hole (a plated pad on its strip's net) in the built board, so it
    # looks like the real board in KiCad and the 3D viewer; hole cuts are drawn as bare holes.
    draw_holes: bool = True
    # Build: place the W link footprints on their holes (with the net on both pads) on the first
    # pass, instead of only proposing them. Each is locked and carries the schematic path of the
    # StripForge:Link symbol that 'stripforge link-symbols' writes, so the links flow from the board
    # to the schematic (KiCad's Update Schematic from PCB can't add symbols): run the plugin's
    # "Add links to schematic" (or 'stripforge link-symbols <board> --in-place') after building. On
    # by default; don't also add the W symbols by hand (F8 would bring in a second copy of each
    # link). false = only propose the links (add them to the schematic yourself, F8, build again).
    place_links: bool = True
    # Build: where the built board goes. "in_place" (default): into the board itself, i.e. the
    # project's own <name>.kicad_pcb, so F8 (Update PCB from Schematic) works in it and DRC parity
    # checks <name>.kicad_sch directly. Every build first makes a save point: a new zip
    # stripforge/<Project>-stripforge-backups/<Project>-stripforge-YYYY-MM-DD_HHMMSS.zip of the
    # files it is about to write. "Restore last" (stripforge restore) walks them newest to oldest.
    # "separate": a new stripforge/<Project>-stripforge.kicad_pcb; the board is left alone.
    output: str = "in_place"
    # How many save points to keep when backup_policy is "retention": the newest N.
    # 0 keeps them all. Ignored while backup_policy is "move" (the default): nothing is deleted.
    backup_keep: int = 0
    # "move" (default, also when the key is absent): file each save point and keep it.
    # "retention": opt in to deleting older save points down to backup_keep, and only when
    # backup_keep is 1 or more. A backup_keep number on its own does not delete.
    backup_policy: str = "move"
    # Where StripForge's files go, relative to the project folder (the .kicad_pro's, ${KIPRJMOD});
    # never absolute. None: "stripforge" and "stripforge/<Project>-stripforge-backups".
    output_dir: str | None = None
    backup_dir: str | None = None
    hole_drill_mm: float = 1.0
    # Your own cuts and links. Building from a board with StripForge cut markers and placed W links
    # (e.g. the built board after moving them in pcbnew) keeps them where they are and only fills
    # in what is missing; false plans from scratch. [manual] gives them as text:
    # links = ["J16-T16"], cuts = ["J15", "C34-C35"] (hole, knife), no_cut = ["J17"].
    respect_edits: bool = True
    manual: dict = field(default_factory=dict)
    # The [stretch] table: lead-stretch suggestions (report only, on by default). After the link
    # plan, list the links a longer lead on a two-pin leaded part (R, C, D, L, F) could replace:
    # which pin to move to which hole. enabled = false turns them off; max_pitches (6) and
    # radial_max_pitches (2) cap how much longer an axial / radial part's span may get; skip lists
    # parts never to stretch; allow_under_parts = true also allows a new line under another part.
    stretch: dict = field(default_factory=dict)
    # Non-fatal config problems found while loading (e.g. a very large [bend]); shown as warnings.
    warnings: list[str] = field(default_factory=list)

    def slot_max_for(self, ref: str) -> float | None:
        """Slot allowance in mm for ``ref``, or None if the part is not slotted."""
        if ref not in self.slotted:
            return None
        return self.slot_max_mm_by_ref.get(ref, self.slot_max_mm)


OUTPUT_MODES = ("in_place", "separate")
BACKUP_POLICIES = ("move", "retention")
STRETCH_KEYS = {"enabled", "max_pitches", "radial_max_pitches", "skip", "allow_under_parts"}


def stretch_from_dict(data: object) -> dict:
    """Check the [stretch] table (see BoardConfig.stretch) and fill in its defaults."""
    if not isinstance(data, dict):
        raise ValueError("[stretch] must be a table")
    unknown = sorted(set(data) - STRETCH_KEYS)
    if unknown:
        raise ValueError(f"unknown [stretch] key(s): {', '.join(unknown)}")
    out = {
        "enabled": True,
        "max_pitches": 6.0,
        "radial_max_pitches": 2.0,
        "skip": [],
        "allow_under_parts": False,
    }
    for name in ("enabled", "allow_under_parts"):
        if name in data:
            if not isinstance(data[name], bool):
                raise ValueError(f"[stretch] {name} must be true or false")
            out[name] = data[name]
    for name in ("max_pitches", "radial_max_pitches"):
        if name in data:
            v = data[name]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0:
                raise ValueError(f"[stretch] {name} must be a number of pitches, 0 or more, got {v!r}")
            out[name] = float(v)
    skip = data.get("skip", [])
    if not isinstance(skip, list) or not all(isinstance(r, str) and r for r in skip):
        raise ValueError('[stretch] skip must be a list of references, e.g. skip = ["C1"]')
    out["skip"] = list(skip)
    return out


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
    stretch = stretch_from_dict(data.pop("stretch", {}))
    from .edits import manual_from_dict

    manual = manual_from_dict(data.pop("manual", {}))
    cfg = BoardConfig(**data)
    cfg.drc = drc
    cfg.bend = bend
    cfg.stretch = stretch
    cfg.manual = manual
    cfg.warnings = bend_warns
    if not isinstance(cfg.skip, list) or not all(isinstance(r, str) and r for r in cfg.skip):
        raise ValueError('skip must be a list of references, e.g. skip = ["SW3"]')
    cfg.skip = [str(r) for r in cfg.skip]
    if not isinstance(cfg.knife_cuts, list) or not all(isinstance(r, str) and r for r in cfg.knife_cuts):
        raise ValueError('knife_cuts must be a list of references, e.g. knife_cuts = ["SW2"]')
    cfg.knife_cuts = [str(r) for r in cfg.knife_cuts]
    if isinstance(cfg.trim_min_free, bool) or not isinstance(cfg.trim_min_free, int) or cfg.trim_min_free < 1:
        raise ValueError(
            f"trim_min_free must be a whole number of holes, 1 or more, got {cfg.trim_min_free!r}"
        )
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
    v = cfg.link_lead_allowance_in
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise ValueError(f"link_lead_allowance_in must be inches a leg, 0 to 1, got {v!r}")
    cfg.link_lead_allowance_in = float(v)
    v = cfg.max_diagonal_shift
    if isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 32:
        raise ValueError(f"max_diagonal_shift must be a whole number of holes, 1 to 32, got {v!r}")
    v = cfg.backup_keep
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise ValueError(f"backup_keep must be a whole number of backups, 0 for all of them, got {v!r}")
    if cfg.backup_policy not in BACKUP_POLICIES:
        raise ValueError(f'backup_policy must be "move" or "retention", got {cfg.backup_policy!r}')
    from .layout import check_relative

    for name in ("output_dir", "backup_dir"):
        if getattr(cfg, name) is not None:
            setattr(cfg, name, check_relative(getattr(cfg, name), name))
    if cfg.output not in OUTPUT_MODES:
        raise ValueError(f'output must be "in_place" or "separate", got {cfg.output!r}')
    if cfg.hole_drill_mm >= cfg.strip_width_mm:
        raise ValueError(f"hole_drill_mm ({cfg.hole_drill_mm:g}) must be smaller than strip_width_mm")
    for name in (
        "diagonal_links",
        "off_pitch_links",
        "bus_strips",
        "draw_holes",
        "place_links",
        "respect_edits",
        "trim_pieces",
    ):
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
    return from_dict(raw_dict(path))


def raw_dict(path: str | Path) -> dict:
    """The TOML file as a dict, before :func:`from_dict` fills defaults."""
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a TOML table")
    return data


def prune_keep(cfg, keep: int | None = None) -> int:
    """How many save points may remain after a new one is written. ``0`` means delete none.

    Deletion is opt-in. ``backup_policy`` must be ``"retention"`` and the count at least 1.
    ``"move"``, a missing policy, and ``backup_keep = 0`` return 0 even when ``keep`` is passed.
    ``keep`` only chooses the count once retention is already the policy (the link-symbol caller
    passes its own number); it cannot turn deletion on by itself.
    """
    if cfg is None or getattr(cfg, "backup_policy", "move") != "retention":
        return 0
    n = getattr(cfg, "backup_keep", 0) if keep is None else keep
    if isinstance(n, bool) or not isinstance(n, int) or n <= 0:
        return 0
    return n


def update_backup_settings_text(text: str, policy: str, keep: int | None = None) -> str:
    """Set ``backup_policy`` and, when ``keep`` is given, ``backup_keep`` in a TOML document.

    Root keys already in the file are replaced in place. Missing ones are inserted before the
    first ``[table]``. Comments, unknown keys and tables are left as they are. A commented-out
    key is not uncommented.
    """
    if policy not in BACKUP_POLICIES:
        raise ValueError(f'backup_policy must be "move" or "retention", got {policy!r}')
    if keep is not None and (isinstance(keep, bool) or not isinstance(keep, int) or keep < 0):
        raise ValueError(f"backup_keep must be a whole number, 0 or more, got {keep!r}")
    assignments: dict[str, str | None] = {"backup_policy": json.dumps(policy)}
    if keep is not None:
        assignments["backup_keep"] = str(keep)
    return update_root_assignments(text, assignments)


def save_backup_settings(path: str | Path, policy: str, keep: int | None = None) -> None:
    """Write the backup policy into ``path``, preserving the rest of an existing file.

    A new file gets only these keys and a short comment. It does not dump the whole config.
    """
    import json

    path = Path(path)
    if path.is_file():
        write_text_atomic(path, update_backup_settings_text(path.read_text(encoding="utf-8"), policy, keep))
        return
    if policy not in BACKUP_POLICIES:
        raise ValueError(f'backup_policy must be "move" or "retention", got {policy!r}')
    if keep is not None and (isinstance(keep, bool) or not isinstance(keep, int) or keep < 0):
        raise ValueError(f"backup_keep must be a whole number, 0 or more, got {keep!r}")
    lines = [
        "# StripForge backup policy. Other settings can be added in this file.",
        '# "move" keeps every save point. "retention" deletes older ones only when backup_keep is 1 or more.',
        f"backup_policy = {json.dumps(policy)}",
    ]
    if keep is not None:
        lines.append(f"backup_keep = {keep}")
    write_text_atomic(path, "\n".join(lines) + "\n")


def write_text_atomic(path: Path, text: str) -> None:
    """Replace ``path`` only after the new text is fully written. A failed write leaves the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        if tmp.is_file():
            tmp.unlink()
        raise


def update_root_assignments(text: str, assignments: dict[str, str | None]) -> str:
    """Replace, insert, or delete uncommented root keys.

    A value is the TOML right-hand side. ``None`` deletes the assignment. Missing keys are
    inserted before the first ``[table]``. Comments, unknown keys, and tables stay. A `` #``
    comment on the assignment line is kept. A commented-out key is not uncommented.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    ends_nl = text.endswith("\n") or text == ""
    lines = text.splitlines()

    def table_at() -> int:
        return next((i for i, line in enumerate(lines) if line.lstrip().startswith("[")), len(lines))

    def is_assign(i: int, key: str) -> bool:
        if i >= table_at() or lines[i].lstrip().startswith("#"):
            return False
        return re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]) is not None

    for key, rhs in assignments.items():
        idxs = [i for i in range(table_at()) if is_assign(i, key)]
        if rhs is None:
            for i in reversed(idxs):
                del lines[i]
            continue
        for i in idxs:
            lines[i] = _replace_rhs(lines[i], key, rhs)
    missing = []
    for key, rhs in assignments.items():
        if rhs is None:
            continue
        if any(is_assign(i, key) for i in range(table_at())):
            continue
        missing.append(f"{key} = {rhs}")
    if missing:
        at = table_at()
        lines[at:at] = missing
    out = nl.join(lines)
    if ends_nl:
        out += nl
    return out


def _replace_rhs(line: str, key: str, rhs: str) -> str:
    match = re.match(rf"^(\s*{re.escape(key)}\s*=\s*)(.*)$", line)
    if match is None:
        return line
    rest = match.group(2)
    comment = _trailing_comment(rest)
    body = rhs if not comment else f"{rhs} {comment.lstrip()}"
    return f"{match.group(1)}{body}"


def _trailing_comment(text: str) -> str:
    """A `` #...`` suffix outside quotes, or ``""``."""
    quote = ""
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            continue
        if ch == "#" and (i == 0 or text[i - 1].isspace()):
            return text[i:]
    return ""


# Root keys the board tab edits. Tables ([bend], [drc], [manual], [stretch]) and
# slot_max_mm_by_ref stay in the file and are not rewritten by the tab.
# (form key, kind, optional). optional blank means the key is omitted.
_BOARD_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("rows", "int", True),
    ("cols", "int", True),
    ("pitch_mm", "float", False),
    ("hole_inset_mm", "float", True),
    ("strip_width_mm", "float", False),
    ("snap_tol_mm", "float", False),
    ("hole_drill_mm", "float", False),
    ("slot_max_mm", "float", False),
    ("cut_style", "choice", False),
    ("cut_marker_layer", "str", False),
    ("max_link_mm", "float", False),
    ("link_lead_allowance_in", "float", False),
    ("max_diagonal_shift", "int", False),
    ("trim_min_free", "int", False),
    ("diagonal_links", "bool", False),
    ("off_pitch_links", "bool", False),
    ("bus_strips", "bool", False),
    ("draw_holes", "bool", False),
    ("place_links", "bool", False),
    ("trim_pieces", "bool", False),
    ("respect_edits", "bool", False),
    ("output", "choice", False),
    ("slotted", "refs", False),
    ("skip", "refs", False),
    ("knife_cuts", "refs", False),
    ("offboard_refs", "refs", False),
)
BOARD_FIELD_KEYS = tuple(key for key, _kind, _opt in _BOARD_FIELDS)
ORIGIN_X_LABEL = "Origin X (mm), centre of hole A1"
ORIGIN_Y_LABEL = "Origin Y (mm), centre of hole A1"
ORIGIN_HELP = (
    "Origin is the centre of the first hole (column 0, row 0, label A1), in millimetres. "
    "It is not the corner of the Edge.Cuts outline. Leave both boxes empty to take the grid "
    "from the outline. This editor does not move parts."
)
UNSAVED_BOARD_MESSAGE = (
    "Board settings have unsaved edits. Save them, or Reload to discard them, before running "
    "an action. Nothing was read from the form, and nothing was written or built."
)


def board_field_kind(key: str) -> str:
    for name, kind, _optional in _BOARD_FIELDS:
        if name == key:
            return kind
    raise KeyError(key)


def board_editor_labels() -> list[tuple[str, str]]:
    """``(form key, label)`` in tab order, including the two origin boxes."""
    labels = {
        "origin_x_mm": ORIGIN_X_LABEL,
        "origin_y_mm": ORIGIN_Y_LABEL,
        "rows": "Rows (strips)",
        "cols": "Columns (holes along a strip)",
        "pitch_mm": "Pitch (mm)",
        "hole_inset_mm": "Hole inset (mm, optional)",
        "strip_width_mm": "Strip width (mm)",
        "snap_tol_mm": "Snap tolerance (mm)",
        "hole_drill_mm": "Hole drill (mm)",
        "slot_max_mm": "Slot allowance (mm)",
        "cut_style": "Cut style",
        "cut_marker_layer": "Cut marker layer",
        "max_link_mm": "Longest link (mm)",
        "link_lead_allowance_in": "Link lead allowance (inches)",
        "max_diagonal_shift": "Max diagonal shift (holes)",
        "trim_min_free": "Trim when this many holes are free",
        "diagonal_links": "Diagonal links",
        "off_pitch_links": "Off-pitch diagonal links",
        "bus_strips": "Bus strips",
        "draw_holes": "Draw stripboard holes",
        "place_links": "Place link footprints",
        "trim_pieces": "Trim unused strip ends",
        "respect_edits": "Keep moved cuts and links",
        "output": "Build output",
        "slotted": "Slotted refs (comma separated)",
        "skip": "Skip refs, wired off-board (comma separated)",
        "knife_cuts": "Knife-cut refs (comma separated)",
        "offboard_refs": "Off-board refs, older name (comma separated)",
    }
    return [("origin_x_mm", labels["origin_x_mm"]), ("origin_y_mm", labels["origin_y_mm"])] + [
        (key, labels[key]) for key in BOARD_FIELD_KEYS
    ]


def _fmt_num(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _fmt_refs(values: object) -> str:
    if not values:
        return ""
    return ", ".join(str(item) for item in values)  # type: ignore[arg-type]


def board_form_from_raw(raw: dict | None) -> dict[str, str]:
    """Form strings for the board tab. Absent optional keys are blank; other absent keys show the default."""
    raw = {} if raw is None else raw
    defaults = BoardConfig()
    origin = raw.get("origin_mm")
    if isinstance(origin, (list, tuple)) and len(origin) == 2:
        ox, oy = _fmt_num(origin[0]), _fmt_num(origin[1])
    else:
        ox, oy = "", ""
    form = {"origin_x_mm": ox, "origin_y_mm": oy}
    for key, kind, optional in _BOARD_FIELDS:
        if key not in raw:
            if optional or kind == "refs":
                form[key] = ""
            elif kind == "bool":
                form[key] = "true" if getattr(defaults, key) else "false"
            elif kind == "int":
                form[key] = str(getattr(defaults, key))
            elif kind == "float":
                form[key] = _fmt_num(getattr(defaults, key))
            else:
                form[key] = str(getattr(defaults, key))
            continue
        value = raw[key]
        if kind == "bool":
            form[key] = "true" if value else "false"
        elif kind == "int":
            form[key] = str(value)
        elif kind == "float":
            form[key] = _fmt_num(value)
        elif kind == "refs":
            form[key] = _fmt_refs(value)
        else:
            form[key] = str(value)
    return form


def board_form_from_file(path: str | Path) -> dict[str, str]:
    path = Path(path)
    if not path.is_file():
        return board_form_from_raw({})
    return board_form_from_raw(raw_dict(path))


def _parse_float(text: str, key: str) -> float:
    try:
        if text.strip().lower() in ("true", "false"):
            raise ValueError
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{key} must be a number of millimetres, got {text!r}") from exc


def _parse_int(text: str, key: str) -> int:
    if not re.fullmatch(r"[+-]?\d+", text.strip()):
        raise ValueError(f"{key} must be a whole number, got {text!r}")
    return int(text.strip())


def _parse_bool(text: str, key: str) -> bool:
    if text.strip() == "true":
        return True
    if text.strip() == "false":
        return False
    raise ValueError(f"{key} must be true or false, got {text!r}")


def _parse_refs(text: str, key: str) -> list[str]:
    if not text.strip():
        return []
    parts = [part.strip() for part in text.split(",")]
    if any(not part for part in parts):
        raise ValueError(f"{key} is a comma-separated list of references, got {text!r}")
    return parts


def _parsed_board_values(form: dict[str, str]) -> dict[str, object]:
    """Form strings to TOML values. ``origin_mm`` is omitted from the dict when both boxes are blank."""
    missing = [key for key in ("origin_x_mm", "origin_y_mm", *BOARD_FIELD_KEYS) if key not in form]
    if missing:
        raise ValueError(f"board form is missing {', '.join(missing)}")
    ox, oy = form["origin_x_mm"].strip(), form["origin_y_mm"].strip()
    out: dict[str, object] = {}
    if ox or oy:
        if not ox or not oy:
            raise ValueError("set both origin X and origin Y, or leave both empty")
        out["origin_mm"] = [_parse_float(ox, "origin X"), _parse_float(oy, "origin Y")]
    for key, kind, optional in _BOARD_FIELDS:
        text = form[key].strip()
        if text == "":
            if optional or kind == "refs":
                continue
            raise ValueError(f"{key} is required")
        if kind == "float":
            out[key] = _parse_float(text, key)
        elif kind == "int":
            out[key] = _parse_int(text, key)
        elif kind == "bool":
            out[key] = _parse_bool(text, key)
        elif kind == "refs":
            out[key] = _parse_refs(text, key)
        elif kind == "choice":
            out[key] = text
        else:
            if not text:
                raise ValueError(f"{key} is required")
            out[key] = text
    return out


def _known_config(data: dict) -> dict:
    known = {item.name for item in fields(BoardConfig)} - {"warnings"}
    return {key: value for key, value in data.items() if key in known}


def merged_board_raw(raw: dict | None, form: dict[str, str]) -> dict:
    """The file's raw table with the form applied. Raises ValueError when the result is not a valid config.

    Unknown keys stay in the returned dict. :func:`from_dict` checks only the keys it knows, so a
    comment-only unknown key in the file does not block the save.
    """
    raw = {} if raw is None else dict(raw)
    parsed = _parsed_board_values(form)
    if "origin_mm" not in parsed:
        raw.pop("origin_mm", None)
    else:
        raw["origin_mm"] = parsed["origin_mm"]
    for key, _kind, optional in _BOARD_FIELDS:
        if key in parsed:
            raw[key] = parsed[key]
        elif optional or _kind == "refs":
            raw.pop(key, None)
    from_dict(_known_config(raw))
    return raw


def _render_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return _fmt_num(value)
    if isinstance(value, list):
        if value and isinstance(value[0], (int, float)) and not isinstance(value[0], bool):
            return "[" + ", ".join(_fmt_num(item) for item in value) + "]"
        return json.dumps(value)
    return json.dumps(str(value))


def _default_value(key: str) -> object:
    value = getattr(BoardConfig(), key)
    if isinstance(value, CutStyle):
        return str(value)
    return value


def board_assignments(raw: dict | None, form: dict[str, str]) -> dict[str, str | None]:
    """Root assignments to write. A key equal to its default and absent from the file is left absent."""
    raw = {} if raw is None else raw
    merged = merged_board_raw(raw, form)
    assignments: dict[str, str | None] = {}
    keys = ["origin_mm", *BOARD_FIELD_KEYS]
    for key in keys:
        present = key in raw
        if key not in merged:
            if present:
                assignments[key] = None
            continue
        value = merged[key]
        if not present and key != "origin_mm" and value == _default_value(key):
            continue
        if not present and key == "origin_mm":
            assignments[key] = _render_value(value)
            continue
        assignments[key] = _render_value(value)
    return assignments


def apply_board_form_text(text: str, form: dict[str, str]) -> str:
    """Return ``text`` with the board form applied. Raises ValueError and does not claim a write."""
    raw = tomllib.loads(text) if text.strip() else {}
    if not isinstance(raw, dict):
        raise ValueError("stripboard.toml must be a table")
    return update_root_assignments(text, board_assignments(raw, form))


def save_board_form(path: str | Path, form: dict[str, str]) -> None:
    """Validate ``form`` and replace ``path`` only if it is valid. The previous file stays on error."""
    path = Path(path)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    else:
        text = (
            "# StripForge board settings.\n"
            "# origin_mm is the centre of hole A1 (column 0, row 0), not the Edge.Cuts corner.\n"
        )
    write_text_atomic(path, apply_board_form_text(text, form))


def board_form_is_dirty(saved: dict[str, str], edited: dict[str, str]) -> bool:
    keys = ("origin_x_mm", "origin_y_mm", *BOARD_FIELD_KEYS)
    return any(saved.get(key, "").strip() != edited.get(key, "").strip() for key in keys)


class StripboardConflict(ValueError):
    """The file on disk is not the copy this editor loaded. Nothing was written."""


def validate_stripboard_text(text: str) -> BoardConfig:
    """Parse ``text`` and run :func:`from_dict`. The text is not rewritten.

    A ``[bend]`` number is millimetres, the same rule as :func:`bend_from_dict`. ``SW1 = 0.46`` is
    0.46 mm: over :data:`BEND_WARN_MM` it is kept and warned, over :data:`BEND_MAX_MM` it is refused.
    A warning does not reject the text.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"stripboard.toml is not valid TOML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("stripboard.toml must be a table")
    return from_dict(data)


def read_stripboard_text(path: str | Path) -> tuple[str, bytes | None]:
    """``(text, bytes)`` for ``path``. ``(empty string, None)`` when the file is not there yet."""
    path = Path(path)
    if not path.is_file():
        return "", None
    data = path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError(f"{path} is not UTF-8, so it was not opened: {exc}") from exc
    return text, data


def save_stripboard_text(path: str | Path, text: str, baseline: bytes | None) -> BoardConfig:
    """Validate ``text``, then replace ``path`` with those exact characters.

    ``baseline`` is what :func:`read_stripboard_text` last returned (``None`` if there was no file).
    The write happens only when the file is still those bytes, and only after the TOML parses and
    :func:`from_dict` accepts it. Comments and spacing stay as typed. A bad text or a file that
    changed in the meantime leaves the disk as it was.
    """
    cfg = validate_stripboard_text(text)
    path = Path(path)
    current = path.read_bytes() if path.is_file() else None
    if current != baseline:
        raise StripboardConflict(
            f"{path} changed after this editor opened it. Nothing was written. "
            "Reload to pick up the file, or Cancel to keep the text you typed."
        )
    write_text_atomic(path, text)
    return cfg


def raw_toml_blocked(board_dirty: bool, backup_dirty: bool) -> str | None:
    """Why the raw TOML window must stay closed, or None when it may open.

    An unsaved Board field or an unsaved Backup choice would be thrown away when the text editor
    saved the whole file and then refreshed those controls from disk.
    """
    if board_dirty or backup_dirty:
        return (
            "The Board or Backup form has unsaved edits. Save them, or Reload, before editing "
            "the TOML text. The file was not opened, and nothing was written."
        )
    return None
