"""Your own cuts and links: [manual] in the toml, and cut markers / W links moved in the built board
are kept ("locked"); StripForge fills in only what is still unjoined and warns about problems."""

from __future__ import annotations

import json

import pytest

import pass2_sim
from sfbuild import one_pad_board
from stripforge import config, writer
from stripforge.board import load_board, save_board
from stripforge.config import BoardConfig
from stripforge.edits import parse_cut, parse_link
from stripforge.grid import parse_hole
from stripforge.links import refs_to_add
from stripforge.writer import place_at

# A at A1 and C1, B at A7: row A is cut between them and one link joins the two A pieces
SPLIT = [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A")]


def links(res):
    return [(lk.start, lk.end) for lk in res.plan.links]


def test_parse_cut_and_link():
    c = parse_cut("J15", "s")
    assert (c.row, c.col, c.style, c.label) == (9, 14.0, "hole", "J15")
    k = parse_cut("J16|J15", "s")
    assert (k.col, k.style, k.label) == (14.5, "knife", "J15-J16")
    lk = parse_link("T16-J16", "s")
    assert lk.label == "J16-T16"
    for bad in ("J15-K15", "J15-J17", "J15-J16-J17"):
        with pytest.raises(ValueError):
            parse_cut(bad, "s")
    with pytest.raises(ValueError):
        parse_link("J16", "s")
    with pytest.raises(ValueError):
        parse_link("J16-J16", "s")


@pytest.mark.parametrize(
    "manual, msg",
    [
        ({"link": ["A1-B1"]}, "unknown [manual] key"),
        ({"links": "A1-B1"}, "must be a list of strings"),
        ({"cuts": ["A1-B1"]}, "neighbours"),
        ({"links": ["A1"]}, "not a link"),
    ],
)
def test_manual_table_is_validated(manual, msg):
    with pytest.raises(ValueError, match=msg.replace("[", r"\[").replace("]", r"\]")):
        config.from_dict({"manual": manual})


def test_manual_table_in_toml(tmp_path):
    p = tmp_path / "s.toml"
    p.write_text('respect_edits = true\n[manual]\nlinks = ["A3-C3"]\ncuts = ["A5"]\nno_cut = ["A4"]\n')
    cfg = config.load(p)
    assert cfg.manual == {"links": ["A3-C3"], "cuts": ["A5"], "no_cut": ["A4"]}


def build(tmp_path, cfg=None, parts=SPLIT, name="out.kicad_pcb"):
    return writer.build(
        one_pad_board(tmp_path, parts), cfg or BoardConfig(trim_pieces=False), tmp_path / name
    )


def test_manual_cut_replaces_the_automatic_one_and_no_cut_is_avoided(tmp_path):
    auto = build(tmp_path)
    (c0,) = auto.analysis.split.cuts
    res = build(tmp_path, BoardConfig(trim_pieces=False, manual={"cuts": ["A6"]}), name="m.kicad_pcb")
    (c,) = res.analysis.split.cuts
    assert (c.row, c.col, c.style) == (0, 5, "hole") and "[manual] cuts" in c.user
    res = build(
        tmp_path,
        BoardConfig(trim_pieces=False, manual={"no_cut": [f"A{int(c0.col) + 1}"]}),
        name="n.kicad_pcb",
    )
    (c,) = res.analysis.split.cuts
    assert c.row == 0 and c.col != c0.col and 0 < c.col < 6 and not c.user
    assert res.plan.ok


def test_manual_link_is_locked(tmp_path):
    res = build(tmp_path, BoardConfig(trim_pieces=False, manual={"links": ["A3-C3"]}))
    (lk,) = res.plan.links
    assert (lk.start, lk.end, lk.origin) == ("A3", "C3", "config") and res.plan.ok
    assert lk.to_dict()["locked"] == "config"
    assert "[manual] links" in (tmp_path / "out-stripforge.links.txt").read_text()


def test_manual_link_on_a_pin_or_shorting_two_nets_is_rejected(tmp_path):
    res = build(tmp_path, BoardConfig(trim_pieces=False, manual={"links": ["A1-C4"]}))
    assert any("A1-C4" in w for w in res.analysis.split.warnings + res.warnings)
    assert links(res) != [("A1", "C4")] and res.plan.ok
    res = build(
        tmp_path, BoardConfig(trim_pieces=False, manual={"links": ["A8-C8"]}), name="s.kicad_pcb"
    )  # B to A
    assert any("A8-C8" in w and "short" in w for w in res.analysis.split.warnings + res.warnings)
    assert ("A8", "C8") not in links(res)


TWO = SPLIT + [("P4", 0, 4, "A"), ("P5", 6, 4, "C")]  # a second cut, on row E


def pass2(tmp_path, cfg=None, parts=SPLIT):
    """Pass 1 then pass 2 (links placed): returns the built pass-2 board path."""
    cfg = cfg or BoardConfig(trim_pieces=False)
    build(tmp_path, cfg, parts, name="p1.kicad_pcb")
    lks = json.loads((tmp_path / "p1-stripforge.links.json").read_text())["links"]
    pass2_sim.add_links_to_board(tmp_path / "in.kicad_pcb", lks, tmp_path / "f8.kicad_pcb")
    out = tmp_path / "p2.kicad_pcb"
    res = writer.build(tmp_path / "f8.kicad_pcb", cfg, out)
    assert res.ok and links(res)[0] == ("A2", "C2")
    return out


def edit(path, ref, hole, knife=False, delete=False):
    b = load_board(path)
    fp = next(f for f in b.footprints if f.ref == ref)
    if delete:
        b.doc.root.remove(fp.node)
    else:
        from stripforge.analyze import make_grid

        g, _ = make_grid(b, BoardConfig(trim_pieces=False))
        x, y = g.hole_xy(parse_hole(hole))
        place_at(fp, x + (g.pitch_nm // 2 if knife else 0), y, fp.angle)
    save_board(b, path)


def rebuild(path, cfg=None):
    return writer.build(path, cfg or BoardConfig(trim_pieces=False), path, in_place=True)


def test_unchanged_rebuild_in_place_is_identical(tmp_path):
    p2 = pass2(tmp_path)
    before = p2.read_text()
    res = rebuild(p2)
    assert p2.read_text() == before and not any(w.startswith("edits:") for w in res.warnings)


def test_moved_link_and_cut_are_kept(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "A5")  # pad 1 onto A5: the (vertical) link now runs A5-C5
    edit(p2, "CUT1", "A6")  # the hole cut moves from A4 to A6
    res = rebuild(p2)
    assert links(res) == [("A5", "C5")] and res.plan.links[0].origin == "board"
    (c,) = res.analysis.split.cuts
    assert (c.style, c.col) == ("hole", 5) and "in the board" in c.user
    assert [(p.ref, p.status) for p in res.placements] == [("W1", "placed")]
    assert "(yours, kept)" in res.placements[0].detail
    assert any(w.startswith("edits: kept your 1 cut(s) and 1 placed link(s)") for w in res.warnings)
    assert res.ok
    # and a second rebuild changes nothing
    before = p2.read_text()
    rebuild(p2)
    assert p2.read_text() == before


def _hand_track(path, a, b, uid, layer="B.Cu", net="A"):
    """A track drawn in pcbnew: its own random uuid, so not one of StripForge's."""
    seg = (
        f"(segment (start {a[0]} {a[1]}) (end {b[0]} {b[1]}) (width 1.8) "
        f'(layer "{layer}") (net "{net}") (uuid "{uid}"))'
    )
    text = path.read_text().rstrip()
    path.write_text(text[:-1] + " " + seg + ")\n")


def test_moved_cut_marker_regenerates_hand_edited_strip_copper(tmp_path):
    """Issue #2: move only the CUT marker; the strip tracks follow on the next build, and strip
    copper re-drawn by hand (it lost StripForge's uuid) is replaced instead of refusing the build."""
    from sfbuild import segments, xy

    p2 = pass2(tmp_path)
    edit(p2, "CUT1", "A6")  # the hole cut moves from A4 to A6
    _hand_track(p2, xy(2, 0), xy(4, 0), "11111111-1111-4111-8111-111111111111")  # bridges the old cut
    _hand_track(p2, xy(4, 0), xy(6, 0), "22222222-2222-4222-8222-222222222222", net="B")  # over the new one
    res = rebuild(p2)
    assert res.ok
    (c,) = res.analysis.split.cuts
    assert (c.style, c.col) == ("hole", 5) and "in the board" in c.user
    assert any(w.startswith("replaced 2 hand-drawn strip track segment(s) on row(s) A") for w in res.warnings)
    segs = segments(p2)
    row_a = sorted((s for s in segs if s[0][1] == xy(0, 0)[1]), key=lambda s: s[0][0])
    assert not any(xy(5, 0) in (s[0], s[1]) for s in row_a)  # no copper into the moved hole cut
    assert [s[3] for s in row_a] == ["A"] * 4 + ["B"] * 3  # A1-A5 on A, A7-A10 on B
    text = p2.read_text()
    assert "11111111-1111" not in text and "22222222-2222" not in text
    before = text
    rebuild(p2)
    assert p2.read_text() == before


@pytest.mark.parametrize(
    "a, b, layer",
    [
        ((1.27, 1.27), (3.81, 3.81), "B.Cu"),
        ((1.27, 1.27), (3.81, 1.27), "F.Cu"),
        ((1.27, 1.27), (1.27, 3.81), "B.Cu"),
    ],
)
def test_other_hand_copper_on_a_built_board_is_still_refused(tmp_path, a, b, layer):
    p2 = pass2(tmp_path)
    _hand_track(p2, a, b, "33333333-3333-4333-8333-333333333333", layer=layer)
    with pytest.raises(writer.BuildError, match="did not write"):
        rebuild(p2)


def test_deleted_cut_that_would_short_is_put_back(tmp_path):
    p2 = pass2(tmp_path, parts=TWO)
    edit(p2, "CUT1", "", delete=True)  # the A/B cut on row A; CUT2 stays
    res = rebuild(p2)
    assert len(res.analysis.split.cuts) == 2 and res.ok
    assert any("would short" in w for w in res.analysis.split.warnings + res.warnings)


def test_link_moved_onto_a_pin_is_rejected_and_replanned(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "A1")  # A1 has P1's pin in it
    res = rebuild(p2)
    ws = res.analysis.split.warnings + res.warnings
    assert any("W1" in w and "pin" in w for w in ws)
    # re-planned on free holes, same ref: pass 2 moves W1 there
    assert res.plan.ok and [lk.ref_hint for lk in res.plan.links] == ["W1"]
    assert links(res) == [("A2", "C2")] and res.placements[0].status == "placed"
    assert ("A1", "C1") not in links(res)


def test_respect_edits_false_plans_from_scratch(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "A3")
    res = rebuild(p2, BoardConfig(trim_pieces=False, respect_edits=False))
    assert links(res) == [("A2", "C2")] and res.plan.links[0].origin == ""
    assert not any(w.startswith("edits:") for w in res.warnings)


def test_refs_to_add(tmp_path):
    res = build(tmp_path, parts=SPLIT + [("P4", 0, 4, "A"), ("P5", 6, 4, "C")])
    assert refs_to_add(res.plan) == "W1..W2"


def test_build_sheet_marks_your_cuts_and_links(tmp_path):
    from stripforge import buildsheet

    p2 = pass2(tmp_path)
    edit(p2, "W1", "A5")
    edit(p2, "CUT1", "A6")
    rebuild(p2)
    html = buildsheet.render_html(
        buildsheet.sheet_model(p2, BoardConfig(trim_pieces=False), date="2026-09-28")
    )
    assert html.count("<b>(yours)</b>") == 2


def _add_link(path, ref, name, hole, nets=None):
    """A link footprint dropped on the board by hand (no nets until F8), pad 1 on ``hole``."""
    from stripforge.analyze import make_grid
    from stripforge.board import _parse_footprint
    from stripforge.writer import embed_footprint

    b = load_board(path)
    node = embed_footprint(name, ref, 0, 0, f"hand/{ref}", nets)
    root = b.doc.root
    last = max(i for i, n in enumerate(root) if isinstance(n, list) and n and str(n[0]) == "footprint")
    root.insert(last + 1, node)
    g, _ = make_grid(b, BoardConfig(trim_pieces=False))
    x, y = g.hole_xy(parse_hole(hole))
    place_at(_parse_footprint(node), x, y, 0)
    save_board(b, path)


def _fp(path, ref):
    return next((f for f in load_board(path).footprints if f.ref == ref), None)


def test_cut_marker_under_a_link_end_is_dropped_and_needed_cut_put_back(tmp_path):
    """Kevin 2026-09-29: a hole cut under his link's end made every build reject the link."""
    p2 = pass2(tmp_path)
    edit(p2, "CUT1", "A2")  # onto W1's pad 1 (W1 runs A2-C2)
    res = rebuild(p2)
    ws = res.analysis.split.warnings + res.warnings
    assert any("under an end of your link W1" in w and "the link wins" in w for w in ws)
    assert links(res) == [("A2", "C2")] and res.plan.links[0].origin == "board"
    (c,) = res.analysis.split.cuts  # A1 [A] and A7 [B] still need a cut: put back, not at A2
    assert c.row == 0 and int(c.col) != 1 and any("would short" in w for w in ws)
    assert [(p.ref, p.status) for p in res.placements] == [("W1", "placed")] and res.ok


def test_cut_under_a_link_end_that_is_needed_there_is_kept(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "A6")  # W1 now A6-C6: A6 is on B's side of any cut in the middle
    edit(p2, "CUT1", "A6")
    res = rebuild(p2)
    ws = res.analysis.split.warnings + res.warnings
    assert any("is under an end of your link W1 but is needed there" in w for w in ws)
    assert ("A6", "C6") not in links(res) and res.plan.ok


def test_leftover_link_on_bare_strip_is_removed_on_rebuild(tmp_path):
    """The second leg of an old bus strip (Kevin's S14-T14) joins nothing once the first leg moved."""
    p2 = pass2(tmp_path)
    _add_link(p2, "W2", "Link_P2.54", "C3", {"1": "A", "2": "A"})  # C3 (net A) to bare D3
    res = rebuild(p2)
    assert links(res) == [("A2", "C2")]
    assert [(p.ref, p.status) for p in res.placements] == [("W1", "placed"), ("W2", "removed")]
    assert "not needed any more" in res.placements[1].detail and res.ok
    assert _fp(p2, "W2") is None and _fp(p2, "W1") is not None
    assert any("W2" in w and "bare strip" in w for w in res.analysis.split.warnings)


def test_hand_placed_ref_link_gets_a_w_name_nets_and_path(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "", delete=True)
    _add_link(p2, "REF**", "Link_P5.08", "A3")  # A3-C3, no nets, no reference
    res = rebuild(p2)
    assert any("REF**" in w and "is now W1" in w for w in res.warnings)
    assert links(res) == [("A3", "C3")] and res.plan.links[0].ref_hint == "W1" and res.ok
    fp = _fp(p2, "W1")
    assert {p.net for p in fp.pads} == {"A"} and "(path " in p2.read_text()
    assert any(w.startswith("links: W1 (A3-C3): set pads on A") for w in res.warnings)


def test_rejected_link_with_another_footprint_keeps_its_name_and_is_not_placed(tmp_path):
    p2 = pass2(tmp_path)
    edit(p2, "W1", "", delete=True)
    _add_link(p2, "W1", "Link_P2.54", "A1", {"1": "A", "2": "A"})  # A1 has P1's pin in it
    res = rebuild(p2)
    st = {p.ref: p.status for p in res.placements}
    assert st["W1"] == "rejected" and "pin" in next(p.detail for p in res.placements if p.ref == "W1")
    assert [lk.ref_hint for lk in res.plan.links] == ["W2"] and st["W2"] == "placed"
    assert res.plan.ok and not res.ok  # the rejected W1 is still a problem to fix
