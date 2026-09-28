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
        SlotJob("BT1", "1", Node(1, 0), Node(1, 1), 317_500, 0, 317_500, True),
        SlotJob("BT1", "2", Node(1, 13), Node(1, 12), 317_500, 0, 317_500, True),
    ]
    # round 1 mm drills: the hole only moves by the pad offset
    assert [j.text for j in a.slot_jobs] == [
        'file hole B1 0.013" (0.318 mm) toward B2 (toward the part centre)',
        'file hole B14 0.013" (0.318 mm) toward B13 (toward the part centre)',
    ]
    # the two pads carry different nets on one strip: one cut between them
    assert [(c.label, c.reason) for c in a.split.cuts] == [("B7", ("VBAT", "GND"))]
    text = format_text(a)
    assert "slotted:   BT1" in text and 'file hole B14 0.013" (0.318 mm) toward B13' in text
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


# The real BH23APC footprint: oval drills 1.635 x 1.0 mm whose centres sit 0.3175 mm inboard, so
# each end hole is filed 0.635 mm (0.025") toward the part centre and the pins seat 31.75 mm apart.
BH23_OVAL = fp(
    "BT1",
    "1.27 3.81",
    pad("1", "0.3175 0", "VBAT", drill="oval 1.635 1"),
    pad("2", "32.7025 0", "GND", drill="oval 1.635 1"),
)


@pytest.mark.parametrize("angle", [0, 180])
def test_bh23apc_filing_reads_0_025_inch(tmp_path, angle):
    holder = (
        BH23_OVAL
        if angle == 0
        else fp(
            "BT1",
            "34.29 3.81 180",
            pad("1", "0.3175 0 180", "VBAT", drill="oval 1.635 1"),
            pad("2", "32.7025 0 180", "GND", drill="oval 1.635 1"),
        )
    )
    a = _run(tmp_path, holder, slotted=["BT1"])
    assert a.rejected == [] and len(a.slot_jobs) == 2
    for j in a.slot_jobs:
        assert j.file_len_nm == 635_000 and j.inward and j.amount == '0.025" (0.635 mm)'
        assert j.text.endswith("(toward the part centre)")
    assert not any("slot offsets" in w for w in a.warnings)
    data = to_dict(a)["slot_jobs"][0]
    assert data["file_mm"] == 0.635 and data["file_in"] == 0.025 and data["inward"] is True


def test_lopsided_slotted_part_is_warned(tmp_path):
    # the placement Kevin had: rotated 180, origin 0.3175 mm off the grid, pad 1 dead on its hole
    lop = fp(
        "BT1",
        "34.6075 3.81 180",
        pad("1", "0.3175 0 180", "VBAT", drill="oval 1.635 1"),
        pad("2", "32.7025 0 180", "GND", drill="oval 1.635 1"),
    )
    a = _run(tmp_path, lop, slotted=["BT1"])
    assert a.rejected == []
    msg = [w for w in a.warnings if "slot offsets" in w]
    assert msg == [
        "BT1 slot offsets 0.000/0.635 mm; shift -0.318 mm along the strip (toward lower hole numbers) "
        "to centre it, so every end hole is filed the same amount"
    ]
