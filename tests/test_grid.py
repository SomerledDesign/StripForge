import pytest

from kicad_text import fp, pad, pcb
from stripforge.board import parse_board
from stripforge.grid import Grid, Node, snap_board, snap_footprint

TOL = 150_000


def grid10x5() -> Grid:
    return Grid.from_outline((0, 0, 25_400_000, 12_700_000))


def test_grid_from_outline_tpi_dimensions():
    g = Grid.from_outline((50_000_000, 50_000_000, 126_200_000, 113_500_000))
    assert (g.cols, g.rows) == (30, 25)
    assert (g.origin_x_nm, g.origin_y_nm) == (51_270_000, 51_270_000)
    assert g.hole_xy(Node(row=24, col=29)) == (124_930_000, 112_230_000)


def test_nearest_and_contains():
    g = grid10x5()
    assert g.nearest(1_270_000, 1_270_000) == Node(0, 0)
    assert g.nearest(3_800_000, 6_400_000) == Node(row=2, col=1)
    assert not g.contains(Node(row=5, col=0)) and not g.contains(Node(row=0, col=-1))
    assert g.contains(Node(row=4, col=9))


def _snap(*pads, at="1.27 1.27"):
    b = parse_board(pcb(fp("U1", at, *pads)))
    return snap_footprint(b.footprints[0], grid10x5(), TOL)


def test_on_grid_footprint():
    s = _snap(pad("1", "0 0", "A"), pad("2", "7.62 2.54", "B"))
    assert s.accepted and s.max_dev_nm == 0
    assert [p.node for p in s.pads] == [Node(0, 0), Node(row=1, col=3)]


def test_c_disc_p250_snaps_with_offset():
    s = _snap(pad("1", "0 0", "A"), pad("2", "2.5 0", "B"))
    assert s.accepted
    assert s.max_dev_nm == 40_000
    assert (s.pads[1].node, s.pads[1].dx_nm, s.pads[1].dy_nm) == (Node(0, 1), -40_000, 0)


def test_littelfuse_off_axis_snaps():
    s = _snap(pad("1", "0 0", "A"), pad("2", "5.08 0.01", "B"))
    assert s.accepted and s.max_dev_nm == 10_000


@pytest.mark.parametrize("off, ok", [("0.14", True), ("0.15", True), ("0.16", False), ("1.0", False)])
def test_tolerance_boundary(off, ok):
    s = _snap(pad("1", "0 0", "A"), pad("2", f"{2.54 + float(off):.2f} 0", "B"))
    assert s.accepted is ok
    if not ok:
        assert "tolerance" in s.reason
        # the suggested rigid shift halves the worst offset
        assert s.max_dev_after_shift_nm <= s.max_dev_nm // 2 + 1


def test_pad_off_board_rejects():
    s = _snap(pad("1", "0 0", "A"), pad("2", "0 12.7", "B"))
    assert not s.accepted and "outside" in s.reason
    assert s.pads[1].node is None


def test_smd_pads_skipped():
    s = _snap(pad("1", "0 0", "A"), pad("2", "5 5", "B", kind="smd"))
    assert s.accepted and s.skipped_pads == ["2"]
    assert len(s.pads) == 1


def test_rotated_footprint_snaps_where_rotation_puts_it():
    b = parse_board(pcb(fp("R1", "3.81 1.27 -90", pad("1", "0 0 270", "A"), pad("2", "7.62 0 270", "B"))))
    s = snap_footprint(b.footprints[0], grid10x5(), TOL)
    assert s.accepted and [p.node for p in s.pads] == [Node(row=0, col=1), Node(row=3, col=1)]


def test_snap_board_skips_offboard_refs():
    b = parse_board(pcb(fp("J9", "1.27 1.27", pad("1", "0 0", "A")), fp("R1", "1.27 3.81", pad("1", "0 0"))))
    assert [s.ref for s in snap_board(b, grid10x5(), TOL, skip_refs=["J9"])] == ["R1"]
