from __future__ import annotations

import json

import pytest

from sfbuild import one_pad_board
from stripforge import config, writer
from stripforge.config import BoardConfig
from stripforge.links import LINK_MAX_PITCHES, link_footprint, to_csv


def build(tmp_path, parts, cfg=None):
    return writer.build(
        one_pad_board(tmp_path, parts), cfg or BoardConfig(trim_pieces=False), tmp_path / "out.kicad_pcb"
    )


def test_link_footprint_names_match_the_library():
    assert link_footprint(1) == "StripForge:Link_P2.54"
    assert link_footprint(4) == "StripForge:Link_P10.16"
    assert link_footprint(32) == "StripForge:Link_P81.28"
    assert LINK_MAX_PITCHES == 32
    for bad in (0, 33):
        with pytest.raises(ValueError):
            link_footprint(bad)


def test_split_net_gets_one_vertical_link(tmp_path):
    # row A: A (col 0) | cut | B (col 6); row C: A. The two A pieces are joined at the first free column.
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A")])
    assert res.plan.needed == 1 and res.plan.ok
    (lk,) = res.plan.links
    assert (lk.ref_hint, lk.net, lk.start, lk.end) == ("W1", "A", "A2", "C2")
    assert lk.footprint == "StripForge:Link_P5.08"
    assert res.plan.cut_moves == []


def test_link_never_lands_on_an_occupied_or_cut_hole(tmp_path):
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A"), ("P4", 1, 2, "A")])
    (lk,) = res.plan.links
    a = res.analysis
    for node in lk.nodes:
        assert a.holes.is_free(node)
        assert node.col not in {s.row: s for s in a.strips}[node.row].dead_holes


def test_cut_slides_so_a_link_can_land(tmp_path):
    # the A piece on row A starts as hole A1 alone (cut at A2); the cut moves to A3 to free A2
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 3, 0, "B"), ("P3", 0, 2, "A")])
    (lk,) = res.plan.links
    assert (lk.start, lk.end) == ("A2", "C2")
    assert [m.text for m in res.plan.cut_moves] == [
        "X1: moved from hole A2 to hole A3 so a 'A' link can land"
    ]
    (cut,) = res.analysis.split.cuts
    assert (cut.style, cut.col) == ("hole", 2.0)
    assert res.analysis.validation == []


def test_unlinkable_net_is_a_clear_error(tmp_path):
    # A1 and B2 on neighbouring holes: the A piece on row A is one occupied hole
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 1, 0, "B"), ("P3", 0, 2, "A")])
    assert not res.plan.ok and res.plan.links == []
    (u,) = res.plan.unlinkable
    assert u.net == "A" and "can't be fully linked" in u.text and "A1" in u.text


def test_three_pieces_need_two_links_and_no_shared_holes(tmp_path):
    parts = [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A"), ("P4", 6, 2, "C"), ("P5", 0, 4, "A")]
    res = build(tmp_path, parts)
    assert res.plan.needed == 2 and len(res.plan.links) == 2
    nodes = [n for lk in res.plan.links for n in lk.nodes]
    assert len(nodes) == len(set(nodes))


def test_overlap_in_a_column_is_avoided(tmp_path):
    # A on rows A/E and B on rows B/D would both like column 2; the second link moves over
    parts = [
        ("P1", 0, 0, "A"), ("P2", 5, 0, "X"), ("P3", 0, 4, "A"), ("P4", 5, 4, "Y"),
        ("P5", 0, 1, "B"), ("P6", 5, 1, "Z"), ("P7", 0, 3, "B"), ("P8", 5, 3, "Q"),
    ]  # fmt: skip
    res = build(tmp_path, parts)
    assert len(res.plan.links) == 2
    a, b = res.plan.links
    assert not (a.col == b.col and a.row_a < b.row_b and b.row_a < a.row_b)


def test_report_files(tmp_path):
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A")])
    data = json.loads((tmp_path / "out-stripforge.links.json").read_text())
    assert data["links"][0] == {
        "ref": "W1", "net": "A", "from": "A2", "to": "C2", "col": 1, "row_a": 0, "col_b": 1, "row_b": 2,
        "kind": "vertical", "rotation": 0.0, "bus": "", "locked": "", "pitches": 2, "length_mm": 5.08,
        "length_in": 0.2, "footprint": "StripForge:Link_P5.08",
    }  # fmt: skip
    assert data["links_needed"] == 1
    assert to_csv(res.plan).splitlines() == [
        "ref,net,from,to,pitches,length_mm,footprint,kind,rotation,bus",
        "W1,A,A2,C2,2,5.08,StripForge:Link_P5.08,vertical,0.0,",
    ]
    txt = (tmp_path / "out-stripforge.links.txt").read_text()
    assert "F8" in txt and "W1" in txt and "StripForge:Link_P5.08" in txt


def test_real_fixture_plan(tmp_path, real_board_path, real_netlist_path):
    cfg = config.load(real_board_path.parents[1] / "x56.toml")
    out = tmp_path / "b.kicad_pcb"
    res = writer.build(real_board_path, cfg, out, netlist=str(real_netlist_path))
    plan = res.plan
    assert plan.needed == 39
    assert len(plan.links) == 35 and plan.joins == 29  # six joins go via a bus strip
    assert len(plan.unlinkable) == 10
    assert sum(len(u.groups) - 1 for u in plan.unlinkable) == plan.needed - plan.joins
    assert [lk.ref_hint for lk in plan.links] == [f"W{i}" for i in range(1, 36)]
    assert all(1 <= lk.pitches <= LINK_MAX_PITCHES for lk in plan.links)
    nodes = [n for lk in plan.links for n in lk.nodes]
    assert len(nodes) == len(set(nodes))
    assert res.analysis.validation == []
    # deterministic: a second build gives the same plan
    again = writer.build(real_board_path, cfg, tmp_path / "c.kicad_pcb", netlist=str(real_netlist_path))
    assert [lk.to_dict() for lk in again.plan.links] == [lk.to_dict() for lk in plan.links]
