"""Integration test on Kevin's ATtiny10 TPI fixture (examples/tpi-fixture)."""

import json
from pathlib import Path

import pytest

from stripforge import config
from stripforge.analyze import analyze, format_text
from stripforge.board import load_board
from stripforge.cli import main

ROOT = Path(__file__).resolve().parents[1]
OFF_PITCH = {"C1": 40_000, "C2": 40_000, "C3": 40_000, "F1": 10_000}


@pytest.fixture
def result(tpi_board_path, tpi_netlist_path):
    return analyze(tpi_board_path, netlist=tpi_netlist_path)


def test_board_contents(tpi_board_path):
    b = load_board(tpi_board_path)
    assert len(b.footprints) == 25
    assert len(b.pads) == 82
    # 36 distinct pad nets; matches the schematic netlist (the M1 brief expected 37)
    assert len(b.nets) == 36
    assert b.outline == (50_000_000, 50_000_000, 126_200_000, 113_500_000)


def test_grid_is_30_by_25(result):
    g = result.grid
    assert (g.cols, g.rows, g.origin_x_nm, g.origin_y_nm) == (30, 25, 51_270_000, 51_270_000)
    assert result.grid_source == "Edge.Cuts"


def test_all_footprints_snap(result):
    assert len(result.snaps) == 25
    assert result.rejected == []
    devs = {s.ref: s.max_dev_nm for s in result.snaps}
    for ref, dev in OFF_PITCH.items():
        assert devs[ref] == dev, ref
    assert all(d <= 5_000 for r, d in devs.items() if r not in OFF_PITCH)
    assert {s.ref for s in result.off_pitch} == set(OFF_PITCH)


def test_rotated_resistor_lands_vertically(result):
    r3 = next(s for s in result.snaps if s.ref == "R3")  # at -90 degrees
    assert {p.number: (p.node.col, p.node.row) for p in r3.pads} == {"1": (24, 7), "2": (24, 11)}


def test_no_conflicts_and_single_net_pieces(result):
    assert result.conflicts == []
    assert result.split.multi_net_pieces == []
    assert all(len(p.nets) <= 1 for p in result.split.pieces)


def test_netlist_matches_board(result):
    assert result.netlist_summary["matches_board"] is True
    assert result.netlist_summary["components"] == 25
    assert result.netlist_warnings == []


def test_every_net_is_on_the_strips(result):
    assert set(result.split.pieces_per_net) == set(result.board.nets)
    for net, n in result.split.split_nets.items():
        assert n > 1, net


def test_config_file_gives_same_result(tpi_board_path, result):
    cfg = config.load(ROOT / "examples" / "stripboard.toml")
    other = analyze(tpi_board_path, cfg)
    assert other.grid == result.grid
    assert [c.__dict__ for c in other.split.cuts] == [c.__dict__ for c in result.split.cuts]


def test_text_report(result):
    text = format_text(result)
    assert "25 snapped, 0 rejected" in text
    assert "Conflicts: 0" in text
    assert "off pitch: C1" in text and "off pitch: F1" in text


def test_cli_json(tpi_board_path, tpi_netlist_path, capsys):
    rc = main(["analyze", str(tpi_board_path), "--netlist", str(tpi_netlist_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["footprints"] == 25 and data["nets"] == 36
    assert data["conflicts"] == []
    assert len(data["cuts"]) > 0 and data["nets_needing_links"]


def test_cli_rejects_with_tight_tolerance(tpi_board_path, capsys):
    rc = main(["analyze", str(tpi_board_path), "--tol", "0.02"])
    out = capsys.readouterr().out
    assert rc == 1
    rejected = [line.split(":")[1].strip() for line in out.splitlines() if "REJECTED" in line]
    assert rejected == ["C3", "C2", "C1"]  # 0.04 mm > 0.02 mm; F1 (0.01 mm) still snaps


def test_hole_labels_in_reports(result, tpi_board_path, capsys):
    assert result.grid.span_label == "A1-Y30"
    by_id = {c.id: c for c in result.split.cuts}
    assert by_id["X1"].label == "B26" and by_id["X1"].where == "hole B26"
    assert by_id["X5"].label == "D3-D4" and by_id["X5"].where == "between D3 and D4"
    text = format_text(result)
    assert "Holes: A1-Y30" in text
    assert "off pitch: C1    max 0.040 mm  [pad 2 L4 off" in text
    assert "X5   knife D3-D4" in text
    main(["analyze", str(tpi_board_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["grid"]["labels"] == "A1-Y30"
    x5 = next(c for c in data["cuts"] if c["id"] == "X5")
    assert (x5["label"], x5["row"], x5["col"]) == ("D3-D4", 3, 2.5)
    r3 = next(s for s in data["snaps"] if s["ref"] == "R3")
    assert [(p["hole"], p["hole_label"]) for p in r3["pads"]] == [([24, 7], "H25"), ([24, 11], "L25")]
    assert all("label" in p for p in data["pieces"])
