"""Links from any free grid hole: diagonals (whole-pitch and off-pitch), bus strips, no crossings,
no wire over a pin; and the built board drawing every stripboard hole."""

from __future__ import annotations

import json

import pytest

import pass2_sim
from sfbuild import one_pad_board
from stripforge import config, drc, resources, writer
from stripforge.board import HOLES_LIB_ID, load_board
from stripforge.config import BoardConfig
from stripforge.grid import Node
from stripforge.links import _segments_meet, any_offsets, link_footprint_for, pythagorean_offsets
from stripforge.sexpr import atom, find, find_all, head


def build(tmp_path, parts, cfg=None, name="out.kicad_pcb"):
    return writer.build(one_pad_board(tmp_path, parts), cfg or BoardConfig(), tmp_path / name)


# row A: A at A2 | cut A3 | B at A4  -> the A piece is A1-A2 (A1 free)
# row D: C at D3 | cut D4 | A at D6  -> the A piece is D5-D10 (D5 free)
# no column has a free hole on both A pieces (even sliding the cuts), but A1 -> D5 is 4 across and
# 3 down: 5 pitches, so Link_P12.70 rotated fits exactly.
DIAGONAL = [("P1", 1, 0, "A"), ("P2", 3, 0, "B"), ("P3", 2, 3, "C"), ("P4", 5, 3, "A")]


def test_pythagorean_offsets_are_whole_pitch_lengths():
    assert pythagorean_offsets(4) == []
    assert pythagorean_offsets(5) == [(-4, 3), (4, 3), (-3, 4), (3, 4)]
    assert all((dx * dx + dy * dy) ** 0.5 % 1 == 0 for dx, dy in pythagorean_offsets(32))


def test_diagonal_link_when_no_column_fits(tmp_path):
    res = build(tmp_path, DIAGONAL)
    assert res.plan.ok and res.plan.needed == 1
    (lk,) = res.plan.links
    assert (lk.start, lk.end, lk.kind, lk.pitches) == ("A1", "D5", "diagonal", 5)
    assert lk.footprint == "StripForge:Link_P12.70" and lk.rotation == pytest.approx(53.1301)
    d = lk.to_dict()
    assert (d["kind"], d["col_b"], d["rotation"]) == ("diagonal", 4, lk.rotation)
    assert "(diagonal, rotated 53.1301 deg)" in (tmp_path / "out.links.txt").read_text()


def test_diagonal_links_can_be_turned_off(tmp_path):
    cfg = BoardConfig(diagonal_links=False, bus_strips=False)
    res = build(tmp_path, DIAGONAL, cfg)
    assert not res.plan.ok and res.plan.links == []
    assert "on a diagonal" in res.plan.unlinkable[0].text


def test_pass2_places_a_diagonal_link_rotated_onto_its_holes(tmp_path):
    board = one_pad_board(tmp_path, DIAGONAL)
    res = writer.build(board, BoardConfig(), tmp_path / "p1.kicad_pcb")
    links = json.loads((tmp_path / "p1.links.json").read_text())["links"]
    pass2_sim.add_links_to_board(board, links, tmp_path / "f8.kicad_pcb")
    res2 = writer.build(tmp_path / "f8.kicad_pcb", BoardConfig(), tmp_path / "p2.kicad_pcb")
    assert [p.status for p in res2.placements] == ["placed"] and res2.ok
    (w1,) = [f for f in load_board(tmp_path / "p2.kicad_pcb").footprints if f.ref == "W1"]
    g = res.analysis.grid
    want = [g.hole_xy(n) for n in res.plan.links[0].nodes]
    got = sorted((p.x_nm, p.y_nm) for p in w1.pads)
    assert all(abs(a - b) <= 1000 for pa, pb in zip(got, sorted(want)) for a, b in zip(pa, pb))
    assert w1.angle == pytest.approx(53.1301)


# row A: A at A2 | cut | B at A4;  row C: C at C6 | cut | A at C9. Two strips apart, no whole-pitch
# diagonal with a 2-strip leg, no shared column: the two A pieces meet on bare strip B.
BUS = [("P1", 1, 0, "A"), ("P2", 3, 0, "B"), ("P3", 5, 2, "C"), ("P4", 8, 2, "A")]


def test_bus_strip_joins_two_pieces_with_two_links(tmp_path):
    res = build(tmp_path, BUS)
    plan = res.plan
    assert plan.ok and plan.needed == 1 and plan.joins == 1
    assert [(lk.start, lk.end, lk.bus) for lk in plan.links] == [("A1", "B1", "B"), ("B8", "C8", "B")]
    bus = [p for p in res.analysis.split.pieces if p.row == 1]
    assert [p.net for p in bus] == ["A"]  # the bare strip now carries A
    assert res.analysis.validation == []
    assert any("meet on bare strip B" in w for w in res.analysis.split.warnings)
    assert "(to bus strip B)" in (tmp_path / "out.links.txt").read_text()


def test_bus_strip_is_cut_down_to_what_it_uses(tmp_path):
    # the same, one strip further right on a 20-hole board: the bus is cut off from the spare strip
    from kicad_text import fp, pad, pcb
    from sfbuild import hole

    parts = [("P1", 1, 0, "A"), ("P2", 3, 0, "B"), ("P3", 5, 2, "C"), ("P4", 8, 2, "A"), ("P5", 18, 4, "D")]
    text = pcb(*[fp(r, hole(c, w), pad("1", "0 0", n)) for r, c, w, n in parts], outline=(0, 0, 50.8, 12.7))
    (tmp_path / "in.kicad_pcb").write_text(text)
    res = writer.build(tmp_path / "in.kicad_pcb", BoardConfig(), tmp_path / "out.kicad_pcb")
    assert res.plan.ok
    (cut,) = res.plan.bus_cuts
    assert (cut.row, cut.style, cut.label) == (1, "hole", "B9")
    assert cut in res.analysis.split.cuts


def test_links_never_meet_and_never_pass_over_a_pin(tmp_path, real_board_path, real_netlist_path):
    cfg = config.load(real_board_path.parents[1] / "x56.toml")
    res = writer.build(real_board_path, cfg, tmp_path / "b.kicad_pcb", netlist=str(real_netlist_path))
    links = res.plan.links
    for i, a in enumerate(links):
        for b in links[i + 1 :]:
            assert not _segments_meet(*a.nodes, *b.nodes), (a.ref_hint, b.ref_hint)
    pins = set(res.analysis.holes.occupants)
    for lk in links:
        (r1, c1), (r2, c2) = ((n.row, n.col) for n in lk.nodes)
        steps = max(abs(r2 - r1), abs(c2 - c1))
        inner = {Node(r1 + (r2 - r1) * k // steps, c1 + (c2 - c1) * k // steps) for k in range(1, steps)}
        assert not inner & pins, lk.ref_hint


def test_max_link_mm_limits_link_length(tmp_path):
    parts = [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 4, "A")]  # A1 .. E1: 4 strips apart
    assert build(tmp_path, parts).plan.ok
    res = build(tmp_path, parts, BoardConfig(max_link_mm=5.08, bus_strips=False), name="short.kicad_pcb")
    assert not res.plan.ok and "up to 5.08 mm" in res.plan.unlinkable[0].text


# --- drawn holes -------------------------------------------------------------------------------


def hole_pads(path):
    out = {}
    for n in load_board(path).doc.root:
        if head(n) == "footprint" and atom(n, 1) == HOLES_LIB_ID:
            for p in find_all(n, "pad"):
                net = find(p, "net")
                layers = find(p, "layers")
                out[atom(p, 1)] = (
                    atom(p, 2),
                    atom(net, 1) if net else None,
                    [atom(layers, i) for i in range(1, len(layers))],
                )
    return out


def test_build_draws_every_free_hole_on_its_strip_net(tmp_path):
    res = build(tmp_path, [("P1", 0, 0, "A"), ("P2", 4, 0, "B")])  # cut at A3
    pads = hole_pads(tmp_path / "out.kicad_pcb")
    assert res.holes_drawn == len(pads) == 50 - 2  # 10 x 5 grid less the two pins
    assert "A1" not in pads and "A5" not in pads  # the pins' own holes
    assert pads["A2"] == ("thru_hole", "A", ["B.Cu"])
    assert pads["A4"] == ("thru_hole", "B", ["B.Cu"])
    assert pads["A3"][0] == "np_thru_hole" and pads["A3"][1] is None  # the hole cut: bare hole
    assert pads["C7"] == ("thru_hole", None, ["B.Cu"])  # bare strip: no net
    refs = [atom(next(find_all(n, "property")), 2) for n in load_board(tmp_path / "out.kicad_pcb").doc.root
            if head(n) == "footprint" and atom(n, 1) == HOLES_LIB_ID]  # fmt: skip
    assert refs == [f"SF_HOLES_{s}" for s in "ABCDE"]


def test_drawn_holes_are_not_parts_and_rebuild_is_identical(tmp_path):
    build(tmp_path, [("P1", 0, 0, "A"), ("P2", 4, 0, "B")])
    out = tmp_path / "out.kicad_pcb"
    assert {f.ref for f in load_board(out).footprints} >= {"P1", "P2"}
    assert not any(f.ref.startswith("SF_HOLES") for f in load_board(out).footprints)
    writer.build(out, BoardConfig(), tmp_path / "again.kicad_pcb")
    assert (tmp_path / "again.kicad_pcb").read_text() == out.read_text()


def test_draw_holes_off(tmp_path):
    res = build(tmp_path, [("P1", 0, 0, "A")], BoardConfig(draw_holes=False))
    assert res.holes_drawn == 0 and hole_pads(tmp_path / "out.kicad_pcb") == {}


def test_no_hole_next_to_another_drill(tmp_path):
    from kicad_text import fp, pad, pcb
    from sfbuild import hole

    # a 3.2 mm mounting hole (NPTH, part skipped) over B2: B1..B3 and A2, C2 are too close
    text = pcb(
        fp("P1", hole(0, 0), pad("1", "0 0", "A")),
        fp("MH1", hole(1, 1), pad("", "0 0", None, "np_thru_hole", "3.2")),
    )
    (tmp_path / "in.kicad_pcb").write_text(text)
    writer.build(tmp_path / "in.kicad_pcb", BoardConfig(skip=["MH1"]), tmp_path / "out.kicad_pcb")
    pads = hole_pads(tmp_path / "out.kicad_pcb")
    assert not {"B1", "B2", "B3", "A2", "C2"} & set(pads)
    assert {"A3", "D2", "B4"} <= set(pads)


def test_drc_filters_holes_under_parts_as_expected():
    def item(d):
        return {"description": d}

    report = {"violations": [
        {"type": "pth_inside_courtyard", "severity": "error", "description": "PTH inside courtyard",
         "items": [item("PTH pad Q15 [CLK1] of SF_HOLES_Q"), item("Footprint SW1")]},
        {"type": "lib_footprint_issues", "severity": "warning",
         "description": "The footprint library 'StripForge' has no footprint 'Holes'",
         "items": [item("Footprint SF_HOLES_A")]},
        {"type": "pth_inside_courtyard", "severity": "error", "description": "PTH inside courtyard",
         "items": [item("PTH pad 2 [GND] of R6"), item("Footprint J2")]},
    ]}  # fmt: skip
    res = drc.classify(report)
    assert res.filtered_holes == 2 and len(res.other) == 1
    assert res.counts()["filtered_holes"] == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"max_link_mm": 0},
        {"hole_drill_mm": 1.8},
        {"hole_drill_mm": "1"},
        {"draw_holes": "yes"},
        {"bus_strips": 1},
    ],  # fmt: skip
)
def test_link_and_hole_options_are_validated(bad):
    with pytest.raises(ValueError):
        config.from_dict(bad)


# row A: A at A2 | cut | B at A4;  row C: C at C3 | cut | A at C6. Two strips apart there is no
# whole-pitch diagonal and no shared column, so (without a bus strip) only an off-pitch diagonal
# joins them: a rotated Link_D*.
OFF_PITCH = [("P1", 1, 0, "A"), ("P2", 3, 0, "B"), ("P3", 2, 2, "C"), ("P4", 5, 2, "A")]


def test_off_pitch_diagonal_uses_a_rotated_link_d(tmp_path):
    res = build(tmp_path, OFF_PITCH, BoardConfig(bus_strips=False))
    assert res.plan.ok and res.plan.needed == 1
    (lk,) = res.plan.links
    assert (lk.start, lk.end, lk.kind, lk.off_pitch) == ("A1", "C5", "diagonal", True)
    assert lk.pitches == 4.47 and lk.length_mm == 11.36
    assert lk.footprint == "StripForge:Link_D11.36" and lk.rotation == pytest.approx(63.4349)
    lib = resources.footprint_file("Link_D11.36")
    assert "(at 0 11.359225)" in lib.read_text()
    # pass 2: KiCad brings the Link_D in; the build rotates it onto both holes
    links = json.loads((tmp_path / "out.links.json").read_text())["links"]
    board = one_pad_board(tmp_path, OFF_PITCH)
    pass2_sim.add_links_to_board(board, links, tmp_path / "f8.kicad_pcb")
    res2 = writer.build(tmp_path / "f8.kicad_pcb", BoardConfig(bus_strips=False), tmp_path / "p2.kicad_pcb")
    assert [p.status for p in res2.placements] == ["placed"] and res2.ok
    (w1,) = [f for f in load_board(tmp_path / "p2.kicad_pcb").footprints if f.ref == "W1"]
    want = sorted(res.analysis.grid.hole_xy(n) for n in lk.nodes)
    got = sorted((p.x_nm, p.y_nm) for p in w1.pads)
    assert all(abs(a - b) <= 1000 for pa, pb in zip(got, want) for a, b in zip(pa, pb))


def test_off_pitch_links_can_be_turned_off(tmp_path):
    res = build(tmp_path, OFF_PITCH, BoardConfig(bus_strips=False, off_pitch_links=False))
    assert not res.plan.ok and res.plan.links == []


def test_link_footprint_for_every_offset_is_in_the_library():
    for dx, dy in any_offsets(32):
        name = link_footprint_for(dx, dy).split(":")[1]
        assert resources.footprint_file(name).exists(), name
    assert link_footprint_for(3, 4) == "StripForge:Link_P12.70"
    assert link_footprint_for(-1, 1) == "StripForge:Link_D3.59"
