# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Mildrew's footprint library and custom DRC rules (footprints/, rules/).

The kicad-cli tests are mutation tests: kicad-cli does not report errors in a .kicad_dru (a bad
condition silently disables rules), so the only real check is that each rule fires on a board
built to violate it and stays quiet on a clean one. They are skipped when kicad-cli is missing.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "footprints" / "StripForge.pretty"
DRU = ROOT / "rules" / "stripforge.kicad_dru"
BOARD = ROOT / "examples" / "tpi-fixture" / "ATtiny10_TPI_Fixture.kicad_pcb"
MAC_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
KICAD_CLI = (
    os.environ.get("KICAD_CLI") or shutil.which("kicad-cli") or (MAC_CLI if Path(MAC_CLI).exists() else None)
)
needs_cli = pytest.mark.skipif(KICAD_CLI is None, reason="kicad-cli not found")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen_fp = _load(ROOT / "footprints" / "gen_footprints.py", "sf_gen_footprints")
gen_dru = _load(ROOT / "rules" / "gen_dru.py", "sf_gen_dru")

LINKS = [f"Link_P{k * 2.54:.2f}" for k in range(1, 33)]  # Link_P2.54 ... Link_P81.28


# ---------------------------------------------------------------- pure-Python checks


def test_library_is_generated_and_up_to_date(tmp_path):
    out = tmp_path / "StripForge.pretty"
    gen_fp.generate(out, "User.1")
    names = sorted(p.name for p in out.iterdir())
    diag = [gen_fp.diagonal_name(n) for n in gen_fp.diagonal_squares()]
    assert len(diag) == 305 and "Link_D3.59" in diag and "Link_D81.16" in diag
    assert len(names) == 34 + 305 and "Link_P81.28.kicad_mod" in names
    assert names == sorted([f"{n}.kicad_mod" for n in LINKS + diag + ["CUT_Hole", "CUT_Knife"]])
    assert sorted(p.name for p in LIB.iterdir()) == names  # nothing stale left in the library
    for p in out.iterdir():
        assert (LIB / p.name).read_text() == p.read_text(), f"{p.name} is stale: re-run gen_footprints.py"


def test_link_geometry():
    for k, name in enumerate(LINKS, start=1):
        t = (LIB / f"{name}.kicad_mod").read_text()
        pads = re.findall(
            r'\(pad "(\d)" thru_hole circle \(at ([-\d.]+) ([-\d.]+)\) \(size 1.7 1.7\) \(drill 1\)', t
        )
        assert [(n, float(x), float(y)) for n, x, y in pads] == [("1", 0, 0), ("2", 0, round(k * 2.54, 2))]
        assert "(attr through_hole)" in t


def test_off_pitch_link_geometry():
    for n in gen_fp.diagonal_squares():
        name = gen_fp.diagonal_name(n)
        t = (LIB / f"{name}.kicad_mod").read_text()
        pads = re.findall(r'\(pad "(\d)" thru_hole circle \(at ([-\d.]+) ([-\d.]+)\)', t)
        assert [(k, float(x)) for k, x, _ in pads] == [("1", 0), ("2", 0)] and float(pads[0][2]) == 0
        assert abs(float(pads[1][2]) - n**0.5 * 2.54) < 1e-6
        assert '(jumper_pad_groups ("1" "2"))' in t


def test_cut_markers_are_board_only_and_padless():
    for name in ("CUT_Hole", "CUT_Knife"):
        t = (LIB / f"{name}.kicad_mod").read_text()
        assert "(pad " not in t
        assert "(attr board_only exclude_from_pos_files exclude_from_bom allow_missing_courtyard)" in t
        drawn = re.findall(r'\((fp_line|fp_circle|fp_rect) .*?\(layer "([^"]+)"\)', t)
        assert drawn and all(layer == "User.1" for _, layer in drawn)


def test_dru_is_up_to_date():
    assert DRU.read_text() == gen_dru.render(1.8, 0.1), "re-run rules/gen_dru.py"


# ---------------------------------------------------------------- kicad-cli mutation tests

U = lambda: str(uuid.uuid4())  # noqa: E731


def _fp(name: str, x: float, y: float, ref: str, layer: str = "F.Cu") -> str:
    t = (LIB / f"{name}.kicad_mod").read_text()
    t = re.sub(
        r'\(version \d+\) \(generator "[^"]*"\) \(layer "F.Cu"\)',
        f'(layer "{layer}") (uuid "{U()}") (at {x} {y} 0)',
        t,
        count=1,
    )
    return t.replace('(footprint "', '(footprint "StripForge:', 1).replace('"REF**"', f'"{ref}"')


# Empty spots on the fixture board (first hole at 51.27, 51.27).
CLEAN_ITEMS = [
    # a legal row-0 strip piece, 1.8 mm wide, touching the top edge region
    lambda: f'(segment (start 51.27 51.27) (end 53.81 51.27) (width 1.8) (layer "B.Cu") (uuid "{U()}"))',
    lambda: f'(segment (start 53.81 51.27) (end 56.35 51.27) (width 1.8) (layer "B.Cu") (uuid "{U()}"))',
    lambda: _fp("CUT_Hole", 51.27, 56.35, "X1"),
    lambda: _fp("CUT_Knife", 52.54, 58.89, "X2"),
]
BAD_ITEMS = {
    "SF strip width": lambda: (
        f'(segment (start 56.35 61.43) (end 58.89 61.43) (width 0.25) (layer "B.Cu") (uuid "{U()}"))'
    ),
    "SF no F.Cu tracks": lambda: (
        f'(segment (start 56.35 63.97) (end 58.89 63.97) (width 1.8) (layer "F.Cu") (uuid "{U()}"))'
    ),
    "SF no vias": lambda: (
        f'(via (at 56.35 66.51) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (uuid "{U()}"))'
    ),
    "SF no copper zones": lambda: (
        f'(zone (net 0) (net_name "") (layer "B.Cu") (uuid "{U()}") (hatch edge 0.5) '
        "(connect_pads (clearance 0.5)) (min_thickness 0.25) (filled_areas_thickness no) "
        "(fill (thermal_gap 0.5) (thermal_bridge_width 0.5)) "
        "(polygon (pts (xy 60 108) (xy 63 108) (xy 63 111) (xy 60 111))))"
    ),
    "SF no back-side footprints": lambda: _fp("Link_P5.08", 53.81, 104.14, "W99", layer="B.Cu"),
}


def _drc(tmp_path: Path, items: list[str], with_rules: bool = True) -> list[dict]:
    b = BOARD.read_text().rstrip()
    assert b.endswith(")")
    board = tmp_path / BOARD.name
    board.write_text(b[:-1] + "\n" + "\n".join(items) + "\n)\n")
    if with_rules:
        shutil.copy(DRU, board.with_suffix(".kicad_dru"))
    report = tmp_path / "drc.json"
    subprocess.run(
        [KICAD_CLI, "pcb", "drc", "--format", "json", "--severity-all", "-o", str(report), str(board)],
        check=True,
        capture_output=True,
    )
    return json.loads(report.read_text())["violations"]


def _sf(violations: list[dict]) -> list[str]:
    return [m.group(1) for v in violations if (m := re.search(r"rule '(SF [^']+)'", v["description"]))]


@needs_cli
def test_rules_quiet_on_clean_board(tmp_path):
    v = _drc(tmp_path, [f() for f in CLEAN_ITEMS])
    assert _sf(v) == []
    kinds = {x["type"] for x in v}
    assert "copper_edge_clearance" not in kinds  # rule "SF strip and pad edge clearance"
    assert kinds <= {"lib_footprint_issues", "track_dangling"}, kinds


@needs_cli
def test_edge_clearance_trips_without_rules(tmp_path):
    v = _drc(tmp_path, [f() for f in CLEAN_ITEMS], with_rules=False)
    assert "copper_edge_clearance" in {x["type"] for x in v}


@needs_cli
def test_each_rule_fires(tmp_path):
    v = _drc(tmp_path, [f() for f in CLEAN_ITEMS] + [f() for f in BAD_ITEMS.values()])
    assert sorted(_sf(v)) == sorted(BAD_ITEMS)
