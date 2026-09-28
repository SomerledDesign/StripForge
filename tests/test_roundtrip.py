"""Byte-exact S-expression round-trip and minimal edits (Sketch.md §4.7, criterion 6)."""

import difflib

import pytest

from stripforge.backends.file_backend import FileBackend
from stripforge.board import board_text, load_board, parse_board
from stripforge.sexpr import Document, SList, Sym, find, loads, nm_to_mm_text, parse


def _read(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return fh.read()


@pytest.mark.parametrize("name", ["ATtiny10_TPI_Fixture.kicad_pcb", "ATtiny10_TPI_Fixture.net"])
def test_unmodified_roundtrip_is_byte_identical(tpi_board_path, name):
    path = tpi_board_path.parent / name
    text = _read(path)
    assert parse(text).dumps() == text
    assert Document.load(path).dumps().encode() == path.read_bytes()


def test_board_roundtrip_through_the_model(tpi_board_path, tmp_path):
    board = load_board(tpi_board_path)
    assert board_text(board) == _read(tpi_board_path)
    out = tmp_path / "copy.kicad_pcb"
    FileBackend(tpi_board_path).save(out)
    assert out.read_bytes() == tpi_board_path.read_bytes()


def _changed_lines(a: str, b: str) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(a.splitlines(), b.splitlines(), lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def test_moving_one_footprint_changes_only_its_at(tpi_board_path):
    text = _read(tpi_board_path)
    board = parse_board(text)
    r6 = board.footprint("R6")
    before = {p.number: (p.x_nm, p.y_nm) for p in r6.pads}
    r6.move(2_540_000, -1_270_000)
    out = board_text(board)
    assert _changed_lines(text, out) == ["-\t\t(at 53.81 86.83)", "+\t\t(at 56.35 85.56)"]
    # KiCad stores pad positions relative to the footprint, so the pads follow the footprint
    again = parse_board(out)
    moved = {p.number: (p.x_nm, p.y_nm) for p in again.footprint("R6").pads}
    assert moved == {n: (x + 2_540_000, y - 1_270_000) for n, (x, y) in before.items()}
    assert moved == {p.number: (p.x_nm, p.y_nm) for p in board.footprint("R6").pads}
    for fp in again.footprints:
        if fp.ref != "R6":
            assert fp.pads == parse_board(text).footprint(fp.ref).pads


def test_moving_a_rotated_footprint_keeps_its_angle(tpi_board_path):
    text = _read(tpi_board_path)
    board = parse_board(text)
    board.footprint("R3").move(-5_000, 0)  # R3 is at -90 degrees
    assert _changed_lines(text, board_text(board)) == [
        "-\t\t(at 112.23 69.05 -90)",
        "+\t\t(at 112.225 69.05 -90)",
    ]


def test_edits_keep_untouched_text_and_use_kicad_style():
    text = '(root (version 1)\n\t(a "x"  keep)\n\t(b\n\t\t(c 1)\n\t)\n)\n'
    doc = parse(text)
    root = doc.root
    find(root, "a")[1] = "y z"  # replaced quoted atom; the double space after it survives
    b = find(root, "b")
    b.append(SList([Sym("d"), Sym("2")]))  # new child list in a multi-line node
    root.append(loads("(e (f 3))"))  # a parsed-elsewhere node is copied verbatim
    root.append([Sym("g"), [Sym("h"), "i"]])  # a node built in code is rendered KiCad-style
    out = doc.dumps()
    assert out == (
        '(root (version 1)\n\t(a "y z"  keep)\n\t(b\n\t\t(c 1)\n\t\t(d 2)\n\t)'
        '\n\t(e (f 3))\n\t(g\n\t\t(h "i")\n\t)\n)\n'
    )
    assert loads(out) == [
        "root",
        ["version", "1"],
        ["a", "y z", "keep"],
        ["b", ["c", "1"], ["d", "2"]],
        ["e", ["f", "3"]],
        ["g", ["h", "i"]],
    ]


def test_removing_children():
    doc = parse("(r\n\t(a 1)\n\t(b 2)\n\t(c 3)\n)")
    del doc.root[2]  # (b 2)
    assert doc.dumps() == "(r\n\t(a 1)\n\t(c 3)\n)"
    del doc.root[-1]
    assert doc.dumps() == "(r\n\t(a 1)\n)"


@pytest.mark.parametrize(
    "nm, text", [(89_370_000, "89.37"), (100_000_000, "100"), (-5_000, "-0.005"), (0, "0"), (1, "0.000001")]
)
def test_nm_to_mm_text(nm, text):
    assert nm_to_mm_text(nm) == text
