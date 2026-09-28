"""knife_cuts (knife cuts only, clear of a big-pad part's pins) and trimming net pieces back to
their last used hole (trim_pieces, trim_min_free)."""

from __future__ import annotations

import pytest

from sfbuild import one_pad_board
from stripforge import buildsheet, config, writer
from stripforge.config import BoardConfig
from stripforge.grid import Node
from stripforge.splitter import knife_keep_clear


def build(tmp_path, parts, **kw):
    kw.setdefault("trim_pieces", False)
    return writer.build(one_pad_board(tmp_path, parts), BoardConfig(**kw), tmp_path / "out.kicad_pcb")


BIG = [("S1", 0, 0, "A"), ("P2", 4, 0, "B")]  # S1 (big pads) at A1, P2 at A5


def test_without_knife_cuts_a_hole_cut_goes_in_the_middle(tmp_path):
    (cut,) = build(tmp_path, BIG).analysis.split.cuts
    assert (cut.style, cut.label, cut.knife_for) == ("hole", "A3", "")


def test_knife_cuts_part_gets_a_knife_cut_clear_of_its_pin(tmp_path):
    res = build(tmp_path, BIG, knife_cuts=["S1"])
    (cut,) = res.analysis.split.cuts
    # not A1|A2 (A2, beside the pin, stays on S1's net under its big pad)
    assert (cut.style, cut.label, cut.knife_for) == ("knife", "A2-A3", "S1")
    html = buildsheet.render_html(buildsheet.sheet_model(tmp_path / "out.kicad_pcb", BoardConfig(
        knife_cuts=["S1"], trim_pieces=False), date="2026-09-28"))  # fmt: skip
    assert "(knife, per S1 setting)" in html


def test_knife_cuts_pins_too_close_are_reported(tmp_path):
    res = build(tmp_path, [("S1", 0, 0, "A"), ("S2", 1, 0, "B")], knife_cuts=["S1", "S2"])
    (cut,) = res.analysis.split.cuts
    assert (cut.style, cut.label) == ("knife", "A1-A2")
    assert any("too close to leave a spare hole" in w for w in res.analysis.split.warnings)


def test_three_holes_apart_leaves_a_spare_hole_beside_each_pin(tmp_path):
    # like SW2's pins, 3 pitches apart: the knife goes between the two middle holes
    res = build(tmp_path, [("S1", 0, 0, "A"), ("S2", 3, 0, "B")], knife_cuts=["S1", "S2"])
    (cut,) = res.analysis.split.cuts
    assert cut.label == "A2-A3" and not any("too close" in w for w in res.analysis.split.warnings)


def test_keep_clear_sets():
    from stripforge.strips import HoleMap, Occupant

    holes = HoleMap(occupants={Node(2, 5): [Occupant("SW2", "1", "A")]}, nets={Node(2, 5): "A"})
    near_holes, near_segs = knife_keep_clear(holes, ["SW2"])
    assert near_holes == {(2, 4), (2, 6)} and near_segs == {(2, 4), (2, 5)}
    assert knife_keep_clear(holes, []) == (set(), set())


def test_knife_cuts_config_is_validated(tmp_path):
    assert config.from_dict({"knife_cuts": ["SW2"]}).knife_cuts == ["SW2"]
    with pytest.raises(ValueError, match="knife_cuts must be a list"):
        config.from_dict({"knife_cuts": "SW2"})
    for bad in (0, 1.5, True):
        with pytest.raises(ValueError, match="trim_min_free"):
            config.from_dict({"trim_min_free": bad})
    with pytest.raises(ValueError, match="trim_pieces"):
        config.from_dict({"trim_pieces": "yes"})
    res = build(tmp_path, BIG, knife_cuts=["SW9"])
    assert any(
        "SW9 is listed in knife_cuts but is not on the board" in w for w in res.analysis.config_ref_warnings
    )


# --- trimming ------------------------------------------------------------------------------------

LONG = [("P1", 0, 0, "A"), ("P2", 2, 0, "A")]  # net A on A1 and A3: the strip runs on to A10


def test_trim_cuts_a_piece_back_to_its_last_used_hole(tmp_path):
    res = build(tmp_path, LONG, trim_pieces=True)
    (cut,) = res.plan.trim_cuts
    assert (cut.style, cut.label) == ("hole", "A4") and cut in res.analysis.split.cuts
    row_a = [(p.col_start, p.col_end, p.net) for p in res.analysis.split.pieces if p.row == 0]
    assert row_a == [(0, 2, "A"), (4, 9, None)]
    assert any(
        "trim: cut hole A4" in w and "frees A5-A10 (6 hole(s))" in w for w in res.analysis.split.warnings
    )


def test_trim_needs_trim_min_free_holes_and_can_be_off(tmp_path):
    assert build(tmp_path, LONG, trim_pieces=True, trim_min_free=7).plan.trim_cuts == []
    assert build(tmp_path, LONG).plan.trim_cuts == []


def test_trim_uses_a_knife_cut_in_knife_style(tmp_path):
    (cut,) = build(tmp_path, LONG, trim_pieces=True, cut_style="knife").plan.trim_cuts
    assert (cut.style, cut.label) == ("knife", "A3-A4")


def test_trim_keeps_link_ends(tmp_path):
    # A at A1 and C1, B at A7: the A link lands at A2/C2, and the C piece is trimmed after C2
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A")], trim_pieces=True)
    (lk,) = res.plan.links
    assert (lk.start, lk.end) == ("A2", "C2")
    assert [c.label for c in res.plan.trim_cuts] == ["C3"]
