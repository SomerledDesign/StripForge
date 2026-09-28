# SPDX-License-Identifier: GPL-3.0-or-later
"""Links from the board to the schematic: place_links (first-pass W footprints) and link-symbols."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from kicad_text import fp, pad, pcb
from sfbuild import hole
from stripforge import drc, writer
from stripforge.board import load_board
from stripforge.config import BoardConfig, from_dict
from stripforge.linksym import LinkSymbolError, add_link_symbols, format_text, load_hierarchy, pin_points
from stripforge.sexpr import atom, find, find_all, loads

AXIAL = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P12.70mm_Horizontal"
SHEET = "11111111-2222-3333-4444-555555555555"


def _with_path(text: str, uid: str) -> str:
    """A footprint linked to a symbol on the sub-sheet (path, sheetname, sheetfile, as F8 writes)."""
    return text[:-1] + f' (path "/{SHEET}/{uid}") (sheetname "/Sub/") (sheetfile "sub.kicad_sch"))'


def _board(tmp_path):
    """R1 J4 [X] - E4 [/Sub/N] down column 4 and P1 [/Sub/N] at D8: the planner joins the E and D
    strips with one link (see test_stretch)."""
    parts = [
        _with_path(
            fp("R1", hole(3, 9), pad("1", "0 0", "X"), pad("2", "0 -12.7", "/Sub/N"), lib=AXIAL), "r1"
        ),
        _with_path(fp("P1", hole(7, 3), pad("1", "0 0", "/Sub/N")), "p1"),
    ]
    path = tmp_path / "in.kicad_pcb"
    path.write_text(pcb(*parts, outline=(0, 0, 25.4, 25.4)), encoding="utf-8")
    return path


def test_place_links_off_by_default_and_config():
    assert from_dict({}).place_links is False
    assert from_dict({"place_links": True}).place_links is True
    with pytest.raises(ValueError, match="place_links"):
        from_dict({"place_links": "yes"})


def test_place_links_first_pass(tmp_path):
    out = tmp_path / "out.kicad_pcb"
    res = writer.build(_board(tmp_path), BoardConfig(trim_pieces=False), out)
    assert res.plan.links and not res.placements  # default: proposal only
    assert not [f for f in load_board(out).footprints if f.ref.startswith("W")]

    cfg = BoardConfig(trim_pieces=False, place_links=True)
    res = writer.build(_board(tmp_path), cfg, out)
    (lk,) = res.plan.links
    assert [(p.ref, p.status) for p in res.placements] == [(lk.ref_hint, "placed")] and res.ok
    (w,) = [f for f in load_board(out).footprints if f.ref == lk.ref_hint]
    assert w.lib_id == lk.footprint and {p.net for p in w.pads} == {"/Sub/N"}
    assert sorted((p.x_nm, p.y_nm) for p in w.pads) == sorted(res.analysis.grid.hole_xy(n) for n in lk.nodes)
    node = w.node
    assert atom(find(node, "locked"), 1) == "yes"
    assert atom(find(node, "path"), 1) == f"/{SHEET}/{writer.link_symbol_uuid(lk.ref_hint)}"
    assert atom(find(node, "sheetfile"), 1) == "sub.kicad_sch"
    assert {atom(p, 1): atom(p, 2) for p in find_all(node, "property")}["Value"] == "Link"
    # rebuilding the built board keeps the link where it is and gives the same file
    first = out.read_bytes()
    res2 = writer.build(out, cfg, out, in_place=True)
    assert [p.status for p in res2.placements] == ["placed"] and out.read_bytes() == first


# --- link-symbols ----------------------------------------------------------------------------

ROOT_UUID = "aaaaaaaa-0000-0000-0000-000000000000"
ROOT_SCH = f"""(kicad_sch (version 20260306) (generator "eeschema") (uuid "{ROOT_UUID}")
  (paper "A4") (lib_symbols)
  (sheet (at 50 50) (size 20 10) (uuid "{SHEET}")
    (property "Sheetname" "Sub" (at 50 49 0)) (property "Sheetfile" "sub.kicad_sch" (at 50 61 0)))
  (sheet_instances (path "/" (page "1"))))
"""
SUB_UUID = "bbbbbbbb-0000-0000-0000-00000000000b"
SUB_SCH = f"""(kicad_sch (version 20260306) (generator "eeschema") (uuid "{SUB_UUID}")
  (paper "A4")
  (lib_symbols (symbol "Device:R" (symbol "R_1_1"
    (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
    (pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2")))))
  (symbol (lib_id "Device:R") (at 100 100 0) (unit 1) (uuid "r1")
    (property "Reference" "R1" (at 102 100 0))
    (instances (project "demo" (path "/{ROOT_UUID}/{SHEET}" (reference "R1") (unit 1)))))
)
"""


def _link(ref: str, net: str, at: str, uid: str | None) -> str:
    text = fp(ref, at, pad("1", "0 0", net), pad("2", "0 2.54", net), lib="StripForge:Link_P2.54")
    return _with_path(text, uid) if uid else text


def _project(tmp_path):
    src = tmp_path / "proj"
    src.mkdir()
    (src / "demo.kicad_sch").write_text(ROOT_SCH, encoding="utf-8")
    (src / "sub.kicad_sch").write_text(SUB_SCH, encoding="utf-8")
    (src / "demo.kicad_pro").write_text("{}\n", encoding="utf-8")
    (src / "sym-lib-table").write_text(
        '(sym_lib_table\n  (version 7)\n  (lib (name "Mine") (type "KiCad") '
        '(uri "${KIPRJMOD}/lib/m.kicad_sym") (options "") (descr ""))\n)\n',
        encoding="utf-8",
    )
    r1 = _with_path(fp("R1", "20 20", pad("1", "0 0", "/Sub/N"), pad("2", "0 7.62", "Net-(R1-Pad2)")), "r1")
    board = src / "demo-built.kicad_pcb"
    board.write_text(
        pcb(
            r1,
            _link("W1", "/Sub/N", "10 10", "w1uuid"),
            _link("W2", "Net-(R1-Pad2)", "12 10", None),
            _link("W3", "GND", "14 10", None),
        ),
        encoding="utf-8",
    )
    return src, board


def _symbols(path: Path) -> dict:
    root = loads(path.read_text(encoding="utf-8"))
    out = {}
    for s in find_all(root, "symbol"):
        if find(s, "lib_id") is not None:
            props = {atom(p, 1): atom(p, 2) for p in find_all(s, "property")}
            out[props["Reference"]] = (atom(find(s, "lib_id"), 1), props, s)
    return out


def _labels(path: Path, kind: str) -> list[tuple[str, float, float]]:
    root = loads(path.read_text(encoding="utf-8"))
    return [
        (atom(n, 1), round(float(find(n, "at")[1]), 2), float(find(n, "at")[2])) for n in find_all(root, kind)
    ]


def test_pin_points_follow_rotation_and_library_y_up(tmp_path):
    src, _ = _project(tmp_path)
    sheets = load_hierarchy(src / "demo.kicad_sch")
    assert [(s.file.name, s.name_path, s.uuid_path) for s in sheets] == [
        ("demo.kicad_sch", "/", ""),
        ("sub.kicad_sch", "/Sub/", f"/{SHEET}"),
    ]
    sym = next(n for n in find_all(sheets[1].doc.root, "symbol") if find(n, "lib_id") is not None)
    assert pin_points(sheets[1], sym) == {"1": (100.0, 96.19), "2": (100.0, 103.81)}
    find(sym, "at")[3] = "90"  # rotated 90 degrees counter-clockwise
    assert pin_points(sheets[1], sym) == {"1": (96.19, 100.0), "2": (103.81, 100.0)}


def test_link_symbols_to_a_copy(tmp_path):
    src, board = _project(tmp_path)
    out = tmp_path / "copy"
    res = add_link_symbols(src / "demo.kicad_sch", board, out_dir=out)
    assert [(a.ref, a.sheet, a.label) for a in res.added] == [
        ("W1", "sub.kicad_sch", "local"),
        ("W2", "sub.kicad_sch", "global"),
        ("W3", "sub.kicad_sch", "global"),
    ]
    assert res.added[1].anchor == "R1.2"
    # the originals are untouched; the root sheet is copied byte for byte
    assert (src / "sub.kicad_sch").read_text() == SUB_SCH
    assert (out / "demo.kicad_sch").read_text() == ROOT_SCH
    syms = _symbols(out / "sub.kicad_sch")
    lib_id, props, node = syms["W1"]
    assert lib_id == "StripForge:Link" and props["Footprint"] == "StripForge:Link_P2.54"
    assert props["Value"] == "Link"
    assert atom(find(node, "uuid"), 1) == "w1uuid"  # the uuid the board's path names
    inst = find(find(find(node, "instances"), "project"), "path")
    assert atom(find(find(node, "instances"), "project"), 1) == "demo"
    assert atom(inst, 1) == f"/{ROOT_UUID}/{SHEET}"
    x, y = float(find(node, "at")[1]), float(find(node, "at")[2])
    labels = _labels(out / "sub.kicad_sch", "label")
    assert ("N", round(x - 5.08, 2), y) in labels and ("N", round(x + 5.08, 2), y) in labels
    glob = _labels(out / "sub.kicad_sch", "global_label")
    assert ("Net-(R1-Pad2)", 100.0, 103.81) in glob  # names the unnamed net on R1 pin 2
    assert sum(1 for g in glob if g[0] == "GND") == 2
    lib = find(loads((out / "sub.kicad_sch").read_text()), "lib_symbols")
    assert "StripForge:Link" in [atom(s, 1) for s in find_all(lib, "symbol")]
    table = (out / "sym-lib-table").read_text()
    assert str(src.resolve()) + "/lib/m.kicad_sym" in table and '(name "StripForge")' in table
    assert (out / "demo.kicad_pcb").exists() and (out / "demo.kicad_pro").exists()
    assert "added 3 StripForge:Link symbol(s)" in format_text(res)
    # running it on the copy again adds nothing
    again = add_link_symbols(out / "demo.kicad_sch", board, out_dir=tmp_path / "copy2")
    assert again.already == ["W1", "W2", "W3"] and not again.added


def test_link_symbols_in_place_keeps_a_backup(tmp_path):
    src, board = _project(tmp_path)
    with pytest.raises(LinkSymbolError):
        add_link_symbols(src / "demo.kicad_sch", board, out_dir=src)
    res = add_link_symbols(src / "demo.kicad_sch", board, in_place=True)
    (bak,) = res.backups
    assert Path(bak).read_text() == SUB_SCH and Path(bak).name.startswith("sub.kicad_sch.stripforge-")
    assert "W3" in _symbols(src / "sub.kicad_sch")
    assert (src / "demo.kicad_sch").read_text() == ROOT_SCH  # unchanged sheets are not rewritten


def test_link_symbols_cli_defaults_to_the_project_schematic(tmp_path, capsys):
    from stripforge.cli import main

    src, board = _project(tmp_path)
    assert main(["link-symbols", str(board), "--out-dir", str(tmp_path / "x")]) == 2
    assert "no demo-built.kicad_sch next to the board" in capsys.readouterr().err
    own = board.rename(src / "demo.kicad_pcb")  # the in-place build's board: <name>.kicad_pcb
    assert main(["link-symbols", str(own), "--out-dir", str(tmp_path / "a")]) == 0
    assert "W3" in _symbols(tmp_path / "a" / "sub.kicad_sch")
    sep = own.rename(src / "demo-stripforge.kicad_pcb")  # output = "separate" still finds demo.kicad_sch
    assert main(["link-symbols", str(sep), "--out-dir", str(tmp_path / "b")]) == 0
    assert "W3" in _symbols(tmp_path / "b" / "sub.kicad_sch")


def test_drc_hint_for_links_without_symbols():
    report = {
        "schematic_parity": [
            {
                "type": "extra_footprint",
                "description": "Extra footprint",
                "items": [{"description": "Footprint W1"}],
            }
        ]
    }
    text = drc.format_text(drc.classify(report, True), "b.kicad_pcb")
    assert "stripforge link-symbols" in text


@pytest.mark.skipif(not drc.find_kicad_cli(), reason="kicad-cli not installed")
def test_link_symbols_netlist_with_kicad_cli(tmp_path):
    src, board = _project(tmp_path)
    out = tmp_path / "copy"
    add_link_symbols(src / "demo.kicad_sch", board, out_dir=out)
    net = out / "demo.net"
    subprocess.run(
        [drc.find_kicad_cli(), "sch", "export", "netlist", "-o", str(net), str(out / "demo.kicad_sch")],
        check=True,
        capture_output=True,
    )
    nets = {}
    for n in find_all(find(loads(net.read_text()), "nets"), "net"):
        for node in find_all(n, "node"):
            nets[(atom(find(node, "ref"), 1), atom(find(node, "pin"), 1))] = atom(find(n, "name"), 1)
    assert nets[("W1", "1")] == nets[("W1", "2")] == "/Sub/N"  # (R1.1 isn't wired in this sketch)
    assert nets[("W2", "1")] == nets[("R1", "2")] == "Net-(R1-Pad2)"
    assert nets[("W3", "1")] == "GND"
    shutil.rmtree(out)
