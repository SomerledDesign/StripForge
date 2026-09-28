"""The real-parts ATtiny10 TPI fixture (examples/tpi-fixture; board and netlist by Mildrew)."""

from pathlib import Path

import pytest

from stripforge import config
from stripforge.analyze import analyze
from stripforge.board import load_board

ROOT = Path(__file__).resolve().parents[1]
X56 = ROOT / "examples" / "x56.toml"


@pytest.fixture
def x56(real_board_path, real_netlist_path):
    return analyze(real_board_path, config.load(X56), netlist=real_netlist_path)


def test_board_contents(real_board_path):
    b = load_board(real_board_path)
    assert len(b.footprints) == 22
    assert len(b.pads) == 86
    assert len(b.nets) == 41
    assert b.outline == (50_000_000, 50_000_000, 192_240_000, 110_960_000)


def test_all_parts_snap_with_x56_and_bt1_is_slotted(x56):
    assert len(x56.snaps) == 22
    assert x56.rejected == [] and x56.off_board == []
    bt1 = next(s for s in x56.snaps if s.ref == "BT1")
    assert bt1.slotted and bt1.accepted
    assert [j.text for j in bt1.slots] == [
        'file hole U16 0.025" (0.635 mm) toward U17 (toward the part centre)',
        'file hole U29 0.025" (0.635 mm) toward U28 (toward the part centre)',
    ]


def test_no_conflicts_and_netlist_matches(x56):
    assert x56.conflicts == []
    assert x56.split.multi_net_pieces == []
    assert x56.netlist_summary["matches_board"] is True
    assert (x56.netlist_summary["components"], x56.netlist_summary["nets"]) == (22, 41)


def test_x56_grid_matches_the_outline(x56):
    # Mildrew widened the outline to the X56 board (56 x 24 holes, A1-X56)
    assert x56.grid_warnings == []
    assert x56.grid.span_label == "A1-X56"


def test_bt1_is_rejected_without_the_slotted_config(real_board_path):
    cfg = config.load(X56)
    cfg.slotted = []
    a = analyze(real_board_path, cfg)
    assert [s.ref for s in a.rejected] == ["BT1"]
