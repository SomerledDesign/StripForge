"""Placement hints for parts that force cuts by lying along a strip."""

import json

from kicad_text import fp, pad, pcb
from stripforge.analyze import analyze, format_text, to_dict
from stripforge.cli import main
from stripforge.grid import Node

EXPECTED = {"J1", "C1", "C2", "C3", "D1", "D2", "D4", "R6", "R7", "J2", "F1"}


def test_fixture_hints(tpi_board_path):
    a = analyze(tpi_board_path)
    hints = {h.ref: h for h in a.hints}
    assert set(hints) == EXPECTED
    assert hints["J1"].forced_cuts == 5 and hints["J1"].rotation is None
    assert hints["J1"].strips == "strips D-H" and hints["J1"].spans.startswith("D3-D4, E3-E4")
    assert hints["J1"].cut_ids == ["X5", "X10", "X15", "X20", "X24"]
    assert hints["J2"].forced_cuts == 9 and hints["J2"].rotation is None
    r6 = hints["R6"]
    assert r6.text.startswith(
        "R6 lies along strip O (O2-O6), forcing 1 cut; rotating it 90° would put its pins"
    )
    for ref in ("C1", "C2", "C3", "D1", "D2", "D4", "R6", "R7"):
        rot = hints[ref].rotation
        assert rot is not None and rot.separate_strips and rot.cuts_saved >= 1, ref
        assert abs(rot.angle) == 90
    assert hints["F1"].rotation is None and "would not save cuts" in hints["F1"].rotation_note
    text = format_text(a)
    assert "Placement hints: 11" in text
    assert "Summary: 63 cuts, 23 nets needing links; 11 part(s) force 23 cut(s)" in text


def test_fixture_hints_json(tpi_board_path, capsys):
    main(["analyze", str(tpi_board_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    by_ref = {h["ref"]: h for h in data["hints"]}
    assert set(by_ref) == EXPECTED
    c1 = by_ref["C1"]
    assert c1["forced_cuts"] == 1 and c1["rows"] == [{"row": 11, "cols": [2, 3], "label": "L3-L4"}]
    assert c1["rotation"]["separate_strips"] is True and c1["rotation"]["cuts_saved"] >= 1
    assert set(c1["rotation"]["pads"]) == {"1", "2"}
    s = data["hints_summary"]
    assert s["parts"] == 11 and s["forced_cuts"] == 23
    assert set(s["rotatable"]) == {"C1", "C2", "C3", "D1", "D2", "D4", "R6", "R7"}
    assert s["est_cuts_saved"] == sum(by_ref[r]["rotation"]["cuts_saved"] for r in s["rotatable"])


def _analyze(tmp_path, *fps):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(pcb(*fps, outline=(0, 0, 25.4, 12.7)))  # 10 x 5 holes, A1-E10
    return analyze(path)


def test_resistor_along_a_strip_can_rotate(tmp_path):
    a = _analyze(tmp_path, fp("R1", "3.81 6.35", pad("1", "0 0", "A"), pad("2", "5.08 0", "B")))
    (h,) = a.hints
    assert (h.ref, h.forced_cuts, h.strips, h.spans) == ("R1", 1, "strip C", "C2-C4")
    rot = h.rotation
    assert rot is not None and rot.separate_strips and rot.cuts_saved == 1 and rot.cuts_after == 0
    # rotated about the centre of its pads (C3): the pins go to B3 and D3
    assert set(rot.pads.values()) == {Node(row=1, col=2), Node(row=3, col=2)}
    assert "rotating it 90° would put its pins on separate strips (est. 1 cut saved" in h.text
    assert to_dict(a)["hints"][0]["rotation"]["cuts_saved"] == 1


def test_rotation_needs_free_holes_inside_the_board(tmp_path):
    # R1 on strip A: rotated it would stick out above the board unless shifted; the column below
    # it is blocked by a wall of other parts, so no rotation fits.
    wall = [
        fp(f"U{r}", f"{1.27 + 2.54 * c} {1.27 + 2.54 * r}", pad("1", "0 0", f"N{r}{c}"))
        for r in (1, 2, 3, 4)
        for c in range(10)
    ]
    a = _analyze(tmp_path, fp("R1", "3.81 1.27", pad("1", "0 0", "A"), pad("2", "5.08 0", "B")), *wall)
    (h,) = a.hints
    assert h.rotation is None and h.rotation_note == "rotating it 90° does not fit on free on-grid holes here"


def test_parts_across_strips_get_no_hint(tmp_path):
    a = _analyze(tmp_path, fp("R1", "3.81 1.27", pad("1", "0 0", "A"), pad("2", "0 5.08", "B")))
    assert a.hints == []
    a = _analyze(tmp_path, fp("R1", "3.81 1.27", pad("1", "0 0", "A"), pad("2", "5.08 0", "A")))
    assert a.hints == []  # same net: no cut needed
