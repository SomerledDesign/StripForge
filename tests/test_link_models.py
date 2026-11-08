# SPDX-License-Identifier: GPL-3.0-or-later
"""Straight links inherit the 3D model from the Link_P footprint.

The footprint file is the source of the model. Placing a link copies that footprint, so the
model comes with it. A link already on the board is left as it is, including a model someone
else assigned. Headless: this checks the written board text, not KiCad's 3D viewer.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from kicad_text import fp, pad, pcb
from sfbuild import hole
from stripforge.board import load_board
from stripforge.config import BoardConfig
from stripforge.grid import Grid
from stripforge.links import LinkProposal
from stripforge.resources import footprint_file, link_model_uri
from stripforge.sexpr import atom, find, head, loads
from stripforge.writer import build, place_new_link

USER_MODEL = "Resistors.3dshapes/R.wrl"


def _models(node) -> list:
    return [c for c in node if head(c) == "model"]


def _angle(node) -> float:
    at = find(node, "at")
    return float(at[3]) if at is not None and len(at) > 3 else 0.0


def _library_model(name: str):
    node = loads(footprint_file(name).read_text(encoding="utf-8"))
    models = _models(node)
    assert len(models) == 1, name
    return models[0]


def _assert_inherited(node, name: str) -> None:
    """The placed footprint carries the library's model, not one written specially at build time."""
    lib = _library_model(name)
    ours = [m for m in _models(node) if atom(m, 1) == atom(lib, 1)]
    assert len(ours) == 1, name
    assert ours[0] == lib
    uri = atom(lib, 1)
    assert uri == link_model_uri(name)
    assert "${KICAD10_3RD_PARTY}/" in uri and "Dropbox" not in uri and not uri.startswith("/")


def _placed(board, lk: LinkProposal):
    grid = Grid.from_outline((0, 0, int(25.4e6), int(12.7e6)))
    node, fp = place_new_link(board, SimpleNamespace(grid=grid), lk, None)
    return node, fp


def test_place_new_link_attaches_the_model_horizontally_and_vertically(tmp_path):
    board = load_board(_write(tmp_path, []))
    vertical = LinkProposal("W1", "N", 0, 0, 2, "StripForge:Link_P5.08")
    horizontal = LinkProposal("W2", "N", 0, 0, 0, "StripForge:Link_P5.08", col_b=2)
    diagonal = LinkProposal("W3", "N", 0, 0, 2, "StripForge:Link_D5.68", col_b=1)
    v_node, v_fp = _placed(board, vertical)
    h_node, h_fp = _placed(board, horizontal)
    d_node, _d_fp = _placed(board, diagonal)
    assert vertical.rotation == 0.0 and _angle(v_node) == 0.0
    assert horizontal.rotation == 90.0 and _angle(h_node) == 90.0
    _assert_inherited(v_node, "Link_P5.08")
    _assert_inherited(h_node, "Link_P5.08")
    assert _models(d_node) == []
    assert len(_models(v_fp.node)) == 1


def test_build_inherits_the_footprint_model_and_keeps_other_assignments(tmp_path):
    """A real build places a horizontal and a vertical straight link by copying the footprint.
    A later build does not invent a model for a link that no longer has one, and it leaves a
    part's own model alone."""
    user = '(model "Resistors.3dshapes/R.wrl" (offset (xyz 1 2 3)) (scale (xyz 1 1 1)) (rotate (xyz 0 0 0)))'
    parts = []
    for ref, col, row in (("R1", 8, 0), ("R2", 8, 1), ("R3", 8, 3)):
        body = fp(ref, hole(col, row), pad("1", "0 0", "N"))
        parts.append(body[:-1] + " " + user + ")")
    path = _write(tmp_path, parts)
    cfg = BoardConfig(
        trim_pieces=False,
        place_links=True,
        manual={"links": ["A1-A3", "B1-D1"]},
        backup_policy="move",
        backup_keep=0,
    )
    out = tmp_path / "out.kicad_pcb"
    res = build(path, cfg, out)
    assert res.ok, res.warnings
    board = load_board(out)
    by_ends = {}
    for placed in board.footprints:
        if not placed.ref.startswith("W"):
            continue
        ends = tuple(sorted((round(p.x_nm / 1e6, 2), round(p.y_nm / 1e6, 2)) for p in placed.pads))
        by_ends[ends] = placed
    horizontal = by_ends[(1.27, 1.27), (6.35, 1.27)]
    vertical = by_ends[(1.27, 3.81), (1.27, 8.89)]
    assert _angle(horizontal.node) == 90.0
    assert _angle(vertical.node) == 0.0
    _assert_inherited(horizontal.node, "Link_P5.08")
    _assert_inherited(vertical.node, "Link_P5.08")
    for part in board.footprints:
        if part.ref.startswith("R"):
            paths = [atom(m, 1) for m in _models(part.node)]
            assert paths == [USER_MODEL]
            xyz = find(find(_models(part.node)[0], "offset"), "xyz")
            assert [float(x) for x in xyz[1:]] == [1.0, 2.0, 3.0]

    text = out.read_text(encoding="utf-8")
    # The model lives on the placed footprint. Taking it off is kept; a build does not put it back.
    stripped, removed = re.subn(
        r'\(model "\$\{KICAD10_3RD_PARTY\}/3dmodels/'
        r'com_github_somerleddesign_stripforge/StripForge\.3dshapes/Link_P[0-9.]+\.step"\n'
        r"\t\t\t\(offset\n\t\t\t\t\(xyz 0 0 0\)\n\t\t\t\)\n"
        r"\t\t\t\(scale\n\t\t\t\t\(xyz 1 1 1\)\n\t\t\t\)\n"
        r"\t\t\t\(rotate\n\t\t\t\t\(xyz 0 0 0\)\n\t\t\t\)\n"
        r"\t\t\)\n?",
        "",
        text,
    )
    assert removed >= 2 and "KICAD10_3RD_PARTY" not in stripped
    stripped = stripped.replace(
        '(property "Reference" "W1"',
        '(model "Custom.3dshapes/Mine.wrl" (offset (xyz 4 5 6)) '
        '(scale (xyz 1 1 1)) (rotate (xyz 0 0 0))) (property "Reference" "W1"',
        1,
    )
    out.write_text(stripped, encoding="utf-8")
    res2 = build(out, cfg, out, in_place=True)
    assert res2.ok
    again = load_board(out)
    w1 = next(placed for placed in again.footprints if placed.ref == "W1")
    paths = [atom(m, 1) for m in _models(w1.node)]
    assert paths == ["Custom.3dshapes/Mine.wrl"]
    for placed in again.footprints:
        if placed.ref.startswith("W") and placed.ref != "W1":
            assert _models(placed.node) == []
        elif placed.ref.startswith("R"):
            assert [atom(m, 1) for m in _models(placed.node)] == [USER_MODEL]
    second = out.read_bytes()
    res3 = build(out, cfg, out, in_place=True)
    assert res3.ok and out.read_bytes() == second


def _write(tmp_path, parts: list[str]):
    path = tmp_path / "in.kicad_pcb"
    path.write_text(pcb(*parts, outline=(0, 0, 25.4, 12.7)), encoding="utf-8")
    return path
