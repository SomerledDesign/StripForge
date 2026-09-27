import pytest

from kicad_text import fp, pad, pcb
from stripforge.board import parse_board, rotate_nm


def one_fp(rot: str, *pads: str):
    b = parse_board(pcb(fp("R1", f"10 20 {rot}".strip(), *pads)))
    return b.footprints[0]


def positions(footprint):
    return {p.number: (p.x_nm, p.y_nm) for p in footprint.pads}


def test_unrotated_footprint_pads_are_offsets():
    f = one_fp("", pad("1", "0 0", "A"), pad("2", "10.16 0", "B"))
    assert f.ref == "R1" and f.angle == 0
    assert positions(f) == {"1": (10_000_000, 20_000_000), "2": (20_160_000, 20_000_000)}
    assert [p.net for p in f.pads] == ["A", "B"]


@pytest.mark.parametrize(
    "rot, expected",
    [
        ("90", (10_000_000, 9_840_000)),  # CCW on screen: +x local goes up (−y)
        ("-90", (10_000_000, 30_160_000)),  # CW on screen: +x local goes down (+y)
        ("270", (10_000_000, 30_160_000)),
        ("180", (-160_000, 20_000_000)),
    ],
)
def test_footprint_rotation_moves_pads(rot, expected):
    # In the file a pad's angle is absolute (footprint angle included), as KiCad writes it.
    f = one_fp(rot, pad("1", f"0 0 {rot}"), pad("2", f"10.16 0 {rot}"))
    assert positions(f)["2"] == expected
    assert positions(f)["1"] == (10_000_000, 20_000_000)


def test_pad_angle_does_not_move_pad_and_relative_angle_is_derived():
    # footprint at 90, pad rotated a further 90 inside the footprint -> file says 180
    f = one_fp("90", pad("1", "2.54 0 180"), pad("2", "0 2.54 90"))
    p1, p2 = f.pads
    assert (p1.x_nm, p1.y_nm) == (10_000_000, 17_460_000)
    assert (p2.x_nm, p2.y_nm) == (12_540_000, 20_000_000)  # local +y (down) turns to +x
    assert (p1.angle_abs, p1.angle_rel) == (180.0, 90.0)
    assert (p2.angle_abs, p2.angle_rel) == (90.0, 0.0)
    assert (p1.local_x_nm, p1.local_y_nm) == (2_540_000, 0)


def test_non_right_angle_rotation():
    x, y = rotate_nm(2_540_000, 0, 45)
    assert (x, y) == (1_796_051, -1_796_051)


def test_legacy_reference_and_net_syntax():
    text = (
        '(kicad_pcb (footprint "L:F" (layer "F.Cu") (at 0 0) (fp_text reference "U7" (at 0 0)) '
        '(pad "1" thru_hole circle (at 0 0) (net 3 "VCC")) (pad "2" np_thru_hole circle (at 2.54 0))))'
    )
    f = parse_board(text).footprints[0]
    assert f.ref == "U7"
    assert [(p.net, p.kind, p.is_tht) for p in f.pads] == [
        ("VCC", "thru_hole", True),
        (None, "np_thru_hole", True),
    ]


def test_outline_from_rect_and_lines():
    assert parse_board(pcb(outline=(50, 50, 126.2, 113.5))).outline == (
        50_000_000,
        50_000_000,
        126_200_000,
        113_500_000,
    )
    lines = (
        '(kicad_pcb (gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts")) '
        '(gr_line (start 10 0) (end 10 5) (layer "Edge.Cuts")) '
        '(gr_line (start 0 0) (end 99 99) (layer "F.SilkS")))'
    )
    assert parse_board(lines).outline == (0, 0, 10_000_000, 5_000_000)
    assert parse_board(pcb(outline=None)).outline is None


def test_not_a_board():
    with pytest.raises(ValueError):
        parse_board("(export (version E))")


def test_file_backend_reads_footprints(tpi_board_path):
    from stripforge.backends.file_backend import FileBackend

    fps = FileBackend(str(tpi_board_path)).read_footprints()
    assert len(fps) == 25 and fps[0].ref == "J1"
