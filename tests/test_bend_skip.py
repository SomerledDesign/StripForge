"""Per-part config: the [bend] table (the "Beckham tolerance") and skip (wired off-board)."""

import json

import pytest

from kicad_text import fp, pad, pcb
from stripforge import buildsheet, config, writer
from stripforge.analyze import analyze, best_fit_moves, format_text, to_dict
from stripforge.grid import suggest_bend_mm


def _dpdt(ref="SW2", at="11.43 3.81", slotted_drill="1"):
    """Kevin's DPDT slide switch: 300 mil pin pitch along a row, 312 mil between the rows, centred,
    so each row sits 6 mil (0.1524 mm) off its strip across the strip."""
    rows = (-0.1524, 7.62 + 0.1524)
    pads = []
    for i, (x, y) in enumerate([(x, y) for y in rows for x in (0, 7.62, 15.24)], start=1):
        pads.append(pad(str(i), f"{x} {y}", f"N{i}", drill=slotted_drill))
    return fp(ref, at, *pads)


def _run(tmp_path, *fps, **cfg):
    path = tmp_path / "b.kicad_pcb"
    path.write_text(pcb(*fps, outline=(0, 0, 40 * 2.54, 8 * 2.54)))
    return analyze(path, config.from_dict(cfg)), path


def test_without_bend_the_switch_is_rejected_with_a_bend_hint(tmp_path):
    a, _ = _run(tmp_path, _dpdt())
    [s] = a.rejected
    assert "0.152 mm from the nearest hole (tolerance 0.150 mm)" in s.reason
    assert "miss the holes across the strip by up to 0.152 mm (6.0 mil)" in s.reason
    assert "add it to the config's [bend] table (SW2 = 0.16)" in s.reason
    assert "slotted" not in s.reason


def test_bend_accepts_and_lists_every_leg(tmp_path):
    a, _ = _run(tmp_path, _dpdt(), bend={"SW2": 0.16})
    assert a.rejected == [] and a.conflicts == []
    [s] = a.snaps
    assert s.bend_nm == 160_000 and len(s.bends) == 6
    assert {b.direction for b in s.bends} == {"across the strip"}
    assert s.bends[0].text == "pad 1 at B5: 0.152 mm (6.0 mil) across the strip"
    text = format_text(a)
    assert "bend legs: SW2   [bend] 0.16 mm  [pad 1 at B5: 0.152 mm (6.0 mil) across the strip;" in text
    assert "SW2: accepted with its [bend] tolerance (0.16 mm): bend 6 leg(s) up to 0.152 mm (6.0 mil)" in text
    assert "off pitch: SW2" not in text
    data = json.loads(json.dumps(to_dict(a)))
    assert data["bend_mm"] == {"SW2": 0.16}
    snap = data["snaps"][0]
    assert snap["bend_tol_mm"] == 0.16 and snap["leg_bends"][0]["bend_mm"] == 0.1524
    assert best_fit_moves(a) == {}  # placed off the holes on purpose: never moved


def test_bend_is_per_part_and_too_small_still_rejects(tmp_path):
    a, _ = _run(tmp_path, _dpdt("SW2"), _dpdt("SW3", "31.75 3.81"), bend={"SW2": 0.16, "SW3": 0.1})
    assert [s.ref for s in a.rejected] == ["SW3"]
    assert "(tolerance 0.100 mm from [bend])" in a.rejected[0].reason


def test_bend_with_slotted_sets_the_across_strip_tolerance(tmp_path):
    # rows off by 0.1524 across AND 0.3 mm along: slotted takes the along part, bend the across part
    sw = fp(
        "SW2",
        "11.73 3.81",
        *[pad(str(i), f"{x} {y}", f"N{i}") for i, (x, y) in enumerate([(0, -0.1524), (7.62, 7.7724)], 1)],
    )
    a, _ = _run(tmp_path, sw, slotted=["SW2"])
    assert "[bend] table (SW2 = 0.16)" in a.rejected[0].reason and "as a slotted part" in a.rejected[0].reason
    a, _ = _run(tmp_path, sw, slotted=["SW2"], bend={"SW2": 0.16})
    [s] = a.snaps
    assert s.accepted and len(s.slots) == 2
    assert [b.bend_nm for b in s.bends] == [152_400, 152_400]  # only the across-strip part is bent


def test_along_strip_miss_still_suggests_slotted(tmp_path):
    odd = fp("BT1", "1.27 3.81", pad("1", "0.3175 0", "A"), pad("2", "32.7025 0", "B"))
    a, _ = _run(tmp_path, odd)
    assert 'slotted = ["BT1"]' in a.rejected[0].reason and "[bend]" not in a.rejected[0].reason


def test_off_grid_part_is_told_to_move(tmp_path):
    r = fp("R1", "2.02 3.81", pad("1", "0 0", "A"), pad("2", "10.16 0", "B"))  # 0.75 mm right of A2
    a, _ = _run(tmp_path, r)
    reason = a.rejected[0].reason
    assert "just off the grid: move it by (-0.750, 0.000) mm" in reason and "slotted" not in reason


@pytest.mark.parametrize(
    "table, msg",
    [
        ({"SW2": 0}, "more than 0 mm"),
        ({"SW2": -0.1}, "more than 0 mm"),
        ({"SW2": 0.6}, "more than 0.5 mm"),
        ({"SW2": "6mil"}, "number of millimetres"),
        ({"SW2": True}, "number of millimetres"),
    ],
)
def test_bad_bend_values(table, msg):
    with pytest.raises(ValueError, match=msg):
        config.from_dict({"bend": table})


def test_big_bend_warns_and_unknown_refs_warn(tmp_path):
    cfg = config.from_dict({"bend": {"SW2": 0.35}})
    assert cfg.bend == {"SW2": 0.35} and "big bend" in cfg.warnings[0]
    a, _ = _run(tmp_path, _dpdt(), bend={"SW2": 0.35, "SW9": 0.2}, slotted=["BT7"], skip=["X1"])
    w = a.warnings
    assert any("big bend" in x for x in w)
    assert "config: SW9 is listed in [bend] but is not on the board (typo or renamed?)" in w
    assert "config: BT7 is listed in slotted but is not on the board (typo or renamed?)" in w
    assert "config: X1 is listed in skip but is not on the board (typo or renamed?)" in w


def test_suggest_bend():
    assert (
        suggest_bend_mm(152_400) == 0.16 and suggest_bend_mm(160_000) == 0.16 and suggest_bend_mm(1) == 0.01
    )


def test_toml_bend_table(tmp_path):
    p = tmp_path / "stripboard.toml"
    p.write_text('slotted = ["BT1"]\nskip = ["SW3"]\n[bend]   # Beckham tolerance\nSW2 = 0.16\nSW5 = 0.2\n')
    cfg = config.load(p)
    assert cfg.bend == {"SW2": 0.16, "SW5": 0.2} and cfg.skip == ["SW3"] and cfg.offboard_refs == ["SW3"]


def test_skip_and_offboard_refs_merge():
    cfg = config.from_dict({"offboard_refs": ["J9"], "skip": ["SW3", "J9"]})
    assert cfg.offboard_refs == ["J9", "SW3"] and cfg.skip == ["J9", "SW3"]
    with pytest.raises(ValueError, match="skip must be a list"):
        config.from_dict({"skip": "SW3"})


def test_skipped_part_is_not_snapped_and_is_hand_wired(tmp_path):
    far = fp("SW3", "500 500", pad("1", "0 0", "PWR"), pad("2", "0.3 0", "unconnected-(SW3-Pad2)"))
    r = fp("R1", "1.27 3.81", pad("1", "0 0", "PWR"), pad("2", "10.16 0", "B"))
    a, _ = _run(tmp_path, far, r, skip=["SW3"])
    assert [s.ref for s in a.snaps] == ["R1"] and a.rejected == [] and a.conflicts == []
    assert a.skipped == ["SW3"]
    assert (
        "SW3: skipped (wired off-board): its pads get no strips, so hand-wire its connection(s) to PWR"
        in a.warnings
    )
    assert "Skipped (wired off-board): SW3" in format_text(a)


def test_sheet_says_bend_and_wired_off_board(tmp_path):
    far = fp("SW3", "500 500", pad("1", "0 0", "N1"))
    _, path = _run(tmp_path, _dpdt(), far)
    cfg = config.from_dict({"bend": {"SW2": 0.16}, "skip": ["SW3"]})
    out = tmp_path / "out" / "b-stripforge.kicad_pcb"
    writer.build(path, cfg, out)
    html = buildsheet.render_html(buildsheet.sheet_model(out, cfg, None, "2026-09-28"))
    assert "(bend legs up to 0.152 mm (6.0 mil) across the strip to fit the holes)" in html
    assert "Wired off-board (1)" in html
    assert "<b>SW3</b>" in html and "wired off-board, not on the stripboard; hand-wire it to N1" in html
