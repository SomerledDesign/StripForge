# SPDX-License-Identifier: GPL-3.0-or-later
"""Lead-stretch suggestions and the link cut list (inches, pad-to-pad)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_text import fp, pad, pcb
from sfbuild import hole
from stripforge import links as links_mod
from stripforge import writer
from stripforge.board import load_board
from stripforge.config import BoardConfig, from_dict, load
from stripforge.writer import prepare

ROOT = Path(__file__).resolve().parents[1]
REAL = ROOT / "examples" / "tpi-fixture" / "ATtiny10_TPI_Fixture.kicad_pcb"
X56 = ROOT / "examples" / "x56.toml"


def test_inches_and_cut_list_order():
    a = links_mod.LinkProposal("W1", "N", 0, 0, 1, "StripForge:Link_P2.54")
    b = links_mod.LinkProposal("W2", "N", 1, 0, 2, "StripForge:Link_P5.08")
    c = links_mod.LinkProposal("W7", "N", 2, 0, 11, "StripForge:Link_P27.94")
    assert a.length_in == 0.1 and a.inches == '0.1"'
    assert b.length_in == 0.2 and c.length_in == 1.1 and c.inches == '1.1"'
    d = links_mod.LinkProposal("W9", "N", 0, 0, 3, "StripForge:Link_P12.70", col_b=4)
    assert d.kind == "diagonal" and d.length_in == 0.5
    rows = links_mod.cut_list([c, a, b, links_mod.LinkProposal("W3", "N", 3, 0, 1, "StripForge:Link_P2.54")])
    assert [v for v, _ in rows] == [0.1, 0.2, 1.1]
    assert rows[0][1] == ["W1", "W3"]
    text = "\n".join(links_mod.format_cut_list([a, b, c], 0.15))
    assert '0.1"' in text and "Cut" in text and '0.4"' in text
    assert "pad-to-pad" in text and "both legs" in text
    assert links_mod.LEAD_NOTE.split()[0] == "Lengths"


def test_stretch_config_defaults_and_off():
    cfg = from_dict({})
    assert cfg.stretch["enabled"] is True and cfg.stretch["max_pitches"] == 6.0
    assert cfg.link_lead_allowance_in == 0.0
    cfg = from_dict({"stretch": {"enabled": False, "max_pitches": 3}, "link_lead_allowance_in": 0.15})
    assert cfg.stretch["enabled"] is False and cfg.stretch["max_pitches"] == 3.0
    assert cfg.link_lead_allowance_in == 0.15
    with pytest.raises(ValueError, match="unknown"):
        from_dict({"stretch": {"bogus": 1}})
    with pytest.raises(ValueError, match="link_lead_allowance_in"):
        from_dict({"link_lead_allowance_in": -1})


AXIAL = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P12.70mm_Horizontal"


def _board(tmp_path, extra=()):
    """10x10 grid: R1 (axial) J4 [X] - E4 [N] standing down column 4; P1 [N] at D8. The planner
    joins the E and D strips with a one-pitch link; moving R1.2 up one hole (E4 -> D4) replaces it."""
    parts = [
        fp("R1", hole(3, 9), pad("1", "0 0", "X"), pad("2", "0 -12.7", "N"), lib=AXIAL),
        fp("P1", hole(7, 3), pad("1", "0 0", "N")),
        *[fp(ref, hole(c, r), pad("1", "0 0", net)) for ref, c, r, net in extra],
    ]
    path = tmp_path / "in.kicad_pcb"
    path.write_text(pcb(*parts, outline=(0, 0, 25.4, 25.4)), encoding="utf-8")
    return path


def test_stretch_replaces_a_link(tmp_path):
    cfg = BoardConfig(trim_pieces=False)
    res = writer.build(_board(tmp_path), cfg, tmp_path / "out.kicad_pcb")
    (lk,) = res.plan.links
    assert lk.net == "N"
    (s,) = res.stretches
    assert s.links == (lk.ref_hint,) and s.ref == "R1" and s.pin == "2"
    assert (s.old.label, s.new.label, s.fixed.label) == ("E4", "D4", "J4")
    assert (s.span_old, s.span_new) == (5.0, 6.0) and s.axial
    txt = (tmp_path / "out-stripforge.links.txt").read_text()
    assert "Lead stretches: 1 suggestion(s)" in txt and "move R1 pin 2 from E4 to D4" in txt
    data = json.loads((tmp_path / "out-stripforge.links.json").read_text())
    assert data["lead_stretches"][0]["to"] == "D4"


def test_no_stretch_when_the_old_piece_still_needs_the_join(tmp_path):
    # another N pad on strip E: moving R1.2 off it would leave P2 unjoined
    res = writer.build(
        _board(tmp_path, [("P2", 8, 4, "N")]), BoardConfig(trim_pieces=False), tmp_path / "o.kicad_pcb"
    )
    assert res.plan.links and res.stretches == []


def test_stretch_respects_limits_and_off(tmp_path):
    cfg = BoardConfig(trim_pieces=False, stretch={"max_pitches": 0.5})
    assert writer.build(_board(tmp_path), cfg, tmp_path / "a.kicad_pcb").stretches == []
    cfg = BoardConfig(trim_pieces=False, stretch={"enabled": False})
    assert writer.build(_board(tmp_path), cfg, tmp_path / "b.kicad_pcb").stretches == []
    cfg = BoardConfig(trim_pieces=False, stretch={"skip": ["R1"]})
    assert writer.build(_board(tmp_path), cfg, tmp_path / "c.kicad_pcb").stretches == []


@pytest.fixture(scope="module")
def x56():
    if not REAL.exists():
        pytest.skip("examples/tpi-fixture missing")
    return prepare(load_board(REAL), load(X56))


def test_x56_cut_list_in_report_and_sheet(x56):
    text = links_mod.format_text(x56.plan)
    assert "Link cut list (pad-to-pad)" in text and "both legs" in text
    assert all(f"({lk.inches})" in text for lk in x56.plan.links)
    rows = links_mod.cut_list(x56.plan.links)
    assert [v for v, _ in rows] == sorted(v for v, _ in rows)
    assert sum(len(r) for _, r in rows) == len(x56.plan.links)
    from stripforge import buildsheet

    html = buildsheet.render_html(buildsheet.sheet_model(REAL, x56.config, date="2026-09-28"))
    assert "Link cut list" in html and "Length (inches, pad-to-pad)" in html and "both legs" in html
    assert html.count('data-kind="link-length"') == len(rows)
