"""Parts outside the configured grid are reported with labels and never crash the analysis."""

import json

from kicad_text import fp, pad, pcb
from stripforge.analyze import analyze, format_text, to_dict
from stripforge.config import BoardConfig
from stripforge.grid import Node


def _board(tmp_path, *fps, outline=(0, 0, 25.4, 12.7)):
    p = tmp_path / "b.kicad_pcb"
    p.write_text(pcb(*fps, outline=outline))
    return p


def test_part_past_the_last_strip_is_off_board(tmp_path):
    # 10 x 5 outline, but the config says 4 strips (A-D): R2 sits on strip E
    path = _board(
        tmp_path,
        fp("R1", "1.27 1.27", pad("1", "0 0", "A"), pad("2", "5.08 0", "B")),
        fp("R2", "1.27 11.43", pad("1", "0 0", "A"), pad("2", "7.62 0", "B")),
        fp("U1", "21.59 8.89", pad("1", "0 0", "A"), pad("2", "0 2.54", "B"), pad("3", "0 60", "C")),
    )
    cfg = BoardConfig(rows=4, cols=10, origin_mm=(1.27, 1.27))
    a = analyze(path, cfg)
    assert [s.ref for s in a.off_board] == ["R2", "U1"]
    r2 = next(s for s in a.snaps if s.ref == "R2")
    assert not r2.accepted
    assert [p.near for p in r2.pads] == [Node(row=4, col=0), Node(row=4, col=3)]
    assert "E1" in r2.reason and "E4" in r2.reason and "A1-D10" in r2.reason
    u1 = next(s for s in a.snaps if s.ref == "U1")
    assert [p.where for p in u1.pads] == ["D9", "off board (near E9)", "off board (near AB9)"]
    text = format_text(a)
    assert "2 rejected (2 off board)" in text
    assert (
        "OFF BOARD: R2: 2 of 2 pad(s) outside A1-D10 [1@off board (near E1), 2@off board (near E4)]" in text
    )
    assert "has room for 1 more strip(s)" in text
    data = json.loads(json.dumps(to_dict(a)))
    assert data["off_board"][0] == {
        "ref": "R2",
        "pads": [
            {"pad": "1", "near": [0, 4], "near_label": "E1"},
            {"pad": "2", "near": [3, 4], "near_label": "E4"},
        ],
    }
    # the on-board part still splits normally
    assert [c.label for c in a.split.cuts] == ["A2"]


def test_part_far_outside_does_not_crash(tmp_path):
    path = _board(tmp_path, fp("J9", "-500 -900", pad("1", "0 0", "A")))
    a = analyze(path, BoardConfig(rows=5, cols=10, origin_mm=(1.27, 1.27)))
    assert a.off_board[0].pads[0].where.startswith("off board (near row-")
    assert "OFF BOARD: J9" in format_text(a)
