from stripforge.config import CutStyle
from stripforge.grid import Grid, Node, PadSnap, SnapResult
from stripforge.splitter import SplitResult, assign_nets, split
from stripforge.strips import Strip, assign_holes, build_strips
from stripforge.validate import validate


def row_board(cols: int, pads: dict[int, str | None], rows: int = 1):
    """One footprint per pad, all on row 0 at the given columns."""
    grid = Grid(1_270_000, 1_270_000, cols, rows)
    snaps = [
        SnapResult(ref=f"P{c}", pads=[PadSnap("1", net, Node(row=0, col=c), 0, 0)]) for c, net in pads.items()
    ]
    holes = assign_holes(snaps)
    return grid, holes, build_strips(grid)


def run(cols, pads, style=CutStyle.AUTO):
    grid, holes, strips = row_board(cols, pads)
    res = split(strips, holes, style)
    assert validate(res, holes, strips) == []
    return res, holes, strips


def test_strip_runs():
    s = Strip(row=0, cols=6)
    assert s.runs() == [(0, 5)]
    s.cut_hole(2)
    s.cut_knife(3)
    assert s.runs() == [(0, 1), (3, 3), (4, 5)]
    s.cut_hole(0)
    assert s.runs() == [(1, 1), (3, 3), (4, 5)]


def test_same_net_needs_no_cut():
    res, _, _ = run(8, {1: "A", 4: "A", 6: "A"})
    assert res.cuts == [] and res.warnings == []
    assert res.pieces_per_net == {"A": 1} and res.split_nets == {}


def test_hole_cut_at_free_hole_nearest_midpoint():
    res, _, strips = run(10, {1: "A", 7: "B"})
    [cut] = res.cuts
    assert (cut.style, cut.col, cut.row, cut.reason) == ("hole", 4.0, 0, ("A", "B"))
    assert cut.between == ("P1.1", "P7.1")
    assert strips[0].dead_holes == {4}
    assert [(p.col_start, p.col_end, p.nets) for p in res.pieces] == [(0, 3, ("A",)), (5, 9, ("B",))]


def test_midpoint_tie_goes_to_lower_column():
    res, _, _ = run(10, {1: "A", 4: "B"})
    assert res.cuts[0].col == 2.0


def test_hole_cut_skips_occupied_holes():
    # P3 has no net but occupies its hole, so the only free hole between A and B is 2
    res, _, _ = run(10, {1: "A", 3: None, 4: "B"})
    assert [(c.style, c.col) for c in res.cuts] == [("hole", 2.0)]


def test_adjacent_nets_force_knife_with_warning():
    res, _, strips = run(6, {2: "A", 3: "B"})
    [cut] = res.cuts
    assert (cut.style, cut.col) == ("knife", 2.5)
    assert strips[0].present == [True, True, False, True, True]
    assert len(res.warnings) == 1 and "knife" in res.warnings[0] and "adjacent" in res.warnings[0]


def test_knife_style_cuts_middle_segment_without_warning():
    res, _, _ = run(10, {1: "A", 7: "B"}, CutStyle.KNIFE)
    assert [(c.style, c.col) for c in res.cuts] == [("knife", 3.5)]
    assert res.warnings == []


def test_split_net_needs_links_and_counts():
    res, _, _ = run(12, {0: "A", 2: "B", 4: "A", 6: "B", 8: "C"})
    assert [c.col for c in res.cuts] == [1.0, 3.0, 5.0, 7.0]
    assert res.pieces_per_net == {"A": 2, "B": 2, "C": 1}
    assert res.split_nets == {"A": 2, "B": 2}
    assert all(len(p.nets) <= 1 for p in res.pieces)


def test_rows_are_independent():
    grid = Grid(1_270_000, 1_270_000, 5, 2)
    snaps = [
        SnapResult(ref="U1", pads=[PadSnap("1", "A", Node(0, 0), 0, 0), PadSnap("2", "B", Node(1, 0), 0, 0)]),
        SnapResult(ref="U2", pads=[PadSnap("1", "B", Node(0, 4), 0, 0), PadSnap("2", "B", Node(1, 4), 0, 0)]),
    ]
    holes = assign_holes(snaps)
    res = split(build_strips(grid), holes)
    assert [(c.row, c.col) for c in res.cuts] == [(0, 2.0)]
    assert res.split_nets == {"B": 2}


def test_two_nets_in_one_hole_is_a_conflict():
    snaps = [
        SnapResult(ref="U1", pads=[PadSnap("1", "A", Node(0, 1), 0, 0)]),
        SnapResult(ref="U2", pads=[PadSnap("1", "B", Node(0, 1), 0, 0)]),
    ]
    holes = assign_holes(snaps)
    assert len(holes.conflicts) == 1 and "different nets" in holes.conflicts[0]
    assert holes.nets[Node(0, 1)] is None


def test_same_net_twice_in_one_hole_is_a_warning():
    two = [PadSnap("1", "A", Node(0, 1), 0, 0), PadSnap("2", "A", Node(0, 1), 0, 0)]
    snaps = [SnapResult(ref="U1", pads=two)]
    holes = assign_holes(snaps)
    assert holes.conflicts == [] and len(holes.warnings) == 1


def test_rejected_footprint_pads_have_no_hole():
    snaps = [
        SnapResult(
            ref="U1", accepted=False, reason="too far", pads=[PadSnap("1", "A", Node(0, 1), 300_000, 0)]
        )
    ]
    holes = assign_holes(snaps)
    assert holes.occupants == {}
    assert holes.conflicts == ["pad U1.1 has no hole (too far)"]


def test_validate_catches_multi_net_piece():
    grid, holes, strips = row_board(6, {1: "A", 3: "B"})
    res = SplitResult(pieces=assign_nets(strips, holes))  # no cuts placed: a short
    errs = validate(res, holes, strips)
    assert len(errs) == 1 and errs[0].startswith("short:")
