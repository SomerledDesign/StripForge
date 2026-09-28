"""Slotted parts: pads off their hole along the strip become slot jobs (the BH23APC case)."""

import json

import pytest

from kicad_text import fp, pad, pcb
from stripforge import config
from stripforge.analyze import analyze, format_text, to_dict
from stripforge.grid import Node, SlotJob

# A synthetic MPD BH23APC-style holder: two pads 13 pitches apart less 2 x 0.3175 mm, so each pad
# sits 0.3175 mm inboard of its hole along the strip (holes B1 and B14).
BH23 = fp(
    "BT1",
    "1.27 3.81",
    pad("1", "0.3175 0", "VBAT"),
    pad("2", "32.7025 0", "GND"),
    lib="Battery:BatteryHolder_MPD_BH23APC",
)


def _run(tmp_path, *fps, **cfg):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(pcb(*fps, outline=(0, 0, 40 * 2.54, 5 * 2.54)))
    return analyze(path, config.from_dict(cfg))


def test_unlisted_holder_is_rejected(tmp_path):
    a = _run(tmp_path, BH23)
    assert [s.ref for s in a.rejected] == ["BT1"]
    assert a.slot_jobs == []


def test_slotted_holder_passes_with_slot_jobs(tmp_path):
    a = _run(tmp_path, BH23, slotted=["BT1"])
    assert a.rejected == [] and a.conflicts == []
    bt1 = a.snaps[0]
    assert bt1.slotted and bt1.accepted
    assert [p.node for p in bt1.pads] == [Node(row=1, col=0), Node(row=1, col=13)]
    assert a.slot_jobs == [
        SlotJob("BT1", "1", Node(1, 0), Node(1, 1), 317_500, 0),
        SlotJob("BT1", "2", Node(1, 13), Node(1, 12), 317_500, 0),
    ]
    assert [j.text for j in a.slot_jobs] == [
        "file hole B1 toward B2 by 0.318 mm",
        "file hole B14 toward B13 by 0.318 mm",
    ]
    # the two pads carry different nets on one strip: one cut between them
    assert [(c.label, c.reason) for c in a.split.cuts] == [("B7", ("VBAT", "GND"))]
    text = format_text(a)
    assert "slotted:   BT1" in text and "file hole B14 toward B13 by 0.318 mm" in text
    data = json.loads(json.dumps(to_dict(a)))
    assert data["slotted_refs"] == ["BT1"]
    assert data["slot_jobs"][0]["hole_label"] == "B1" and data["slot_jobs"][0]["length_mm"] == 0.3175


def test_slot_must_run_along_the_strip(tmp_path):
    # 0.3175 mm off in x is fine, but 0.3 mm off in y (across the strip) is not
    off_y = fp("BT1", "1.27 3.81", pad("1", "0.3175 0.3", "VBAT"), pad("2", "32.7025 0.3", "GND"))
    a = _run(tmp_path, off_y, slotted=["BT1"])
    assert [s.ref for s in a.rejected] == ["BT1"]
    assert "slotted part" in a.rejected[0].reason


def test_slot_allowance_per_ref(tmp_path):
    a = _run(tmp_path, BH23, slotted=["BT1"], slot_max_mm_by_ref={"BT1": 0.25})
    assert [s.ref for s in a.rejected] == ["BT1"]
    a = _run(tmp_path, BH23, slotted=["BT1"], slot_max_mm=0.2, slot_max_mm_by_ref={"BT1": 0.5})
    assert a.rejected == [] and len(a.slot_jobs) == 2


@pytest.mark.parametrize(
    "data",
    [{"slot_max_mm": 1.5}, {"slot_max_mm": -0.1}, {"slotted": ["BT1"], "slot_max_mm_by_ref": {"BT2": 0.5}}],
)
def test_bad_slot_config(data):
    with pytest.raises(ValueError):
        config.from_dict(data)


def test_hole_cut_avoids_the_slot_neighbour(tmp_path):
    # a 3-hole strip piece: pads at B1 (slotted toward B2) and B3; the cut can't use B2
    short = fp("BT1", "1.27 3.81", pad("1", "0.3175 0", "VBAT"), pad("2", "5.08 0", "GND"))
    a = _run(tmp_path, short, slotted=["BT1"])
    assert [j.toward.label for j in a.slot_jobs] == ["B2"]
    assert [(c.style, c.label) for c in a.split.cuts] == [("knife", "B2-B3")]
    assert a.conflicts == []
