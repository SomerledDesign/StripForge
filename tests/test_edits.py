"""Your own cuts and links: [manual] in the toml, and cut markers / W links moved in the built board
are kept ("locked"); StripForge fills in only what is still unjoined and warns about problems."""

from __future__ import annotations

import json
import re

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


def test_board_cut_markers_keep_their_numbers(tmp_path):
    """Kevin's sheet said "cut markers differ (moved or changed: CUT76 ... CUT86)" on a board fresh
    from Build strips: the cuts added while planning are numbered after the rest, and a rebuild or
    the sheet renumbered the board's markers row by row. A board's CUT marker now keeps its number."""
    from stripforge import buildsheet

    p2 = pass2(tmp_path)
    edit(p2, "W1", "A5")
    edit(p2, "CUT1", "A6")
    p2.write_text(p2.read_text().replace('"Reference" "CUT1"', '"Reference" "CUT7"'))
    res = rebuild(p2)
    assert [c.id for c in res.analysis.split.cuts] == ["X7"]
    assert _fp(p2, "CUT7") is not None and _fp(p2, "CUT1") is None
    m = buildsheet.sheet_model(p2, BoardConfig(trim_pieces=False), date="2026-09-29")
    assert m.built == "built" and not any("cut markers differ" in w for w in m.warnings)


@pytest.mark.parametrize("legacy", [False, True])
def test_build_sheet_calls_only_moved_or_added_cuts_yours(tmp_path, legacy):
    """Kevin's edited board: the sheet said "(yours)" on every cut, StripForge's own too. Only a
    marker you moved (or added) is yours; a marker still where StripForge put it is not. A board
    built before 0.2.0 (``legacy``: the marker uuid doesn't record the spot) is judged against
    StripForge's own plan."""
    from stripforge import buildsheet
    from stripforge.edits import own_marker_key

    p2 = pass2(tmp_path, parts=TWO)
    before = {f.ref: f for f in load_board(p2).footprints if f.ref.startswith("CUT")}
    assert len(before) == 2
    if legacy:
        text = p2.read_text()
        for ref in before:
            text = text.replace(
                writer._u(own_marker_key(f"X{ref[3:]}", _cut_label(p2, ref))), writer._u(f"cut/X{ref[3:]}")
            )
        p2.write_text(text)
    row_a = next(r for r in before if _cut_label(p2, r).startswith("A"))
    edit(p2, "W1", "A5")
    edit(p2, row_a, "A6")  # the row A cut moves from A4 to A6; the row E one stays put
    res = rebuild(p2)
    by_ref = {f"CUT{c.id[1:]}": c for c in res.analysis.split.cuts}
    assert by_ref[row_a].user and not by_ref[row_a].auto
    (other,) = set(before) - {row_a}
    assert by_ref[other].user and by_ref[other].auto
    assert any(
        w.startswith("edits: kept your 1 cut(s) and 1 placed link(s)")
        and "StripForge's other 1 cut marker(s) and 1 link(s)" in w
        for w in res.warnings
    ), res.warnings
    for _ in range(2):  # and it stays that way on a second rebuild
        html = buildsheet.render_html(
            buildsheet.sheet_model(p2, BoardConfig(trim_pieces=False), date="2026-09-29")
        )
        cuts = re.findall(r'<li class="item" data-kind="cut".*?</li>', html)
        assert len(cuts) == 2
        yours = [li for li in cuts if "(yours)" in li]
        assert len(yours) == 1 and f'data-cut="X{row_a[3:]}"' in yours[0]  # only the moved cut
        rebuild(p2)


def _cut_label(path, ref):
    from stripforge.analyze import make_grid
    from stripforge.edits import CUT_KNIFE_ID, CutSpec, _snap

    b = load_board(path)
    fp = next(f for f in b.footprints if f.ref == ref)
    g, _ = make_grid(b, BoardConfig(trim_pieces=False))
    knife = fp.lib_id == CUT_KNIFE_ID
    row, col, _ = _snap(g, fp.x_nm, fp.y_nm, knife)
    return CutSpec(row, float(col), "knife" if knife else "hole", "").label


@pytest.mark.parametrize("legacy", [False, True])
def test_only_moved_links_are_yours(tmp_path, legacy):
    """Kevin's Build strips report said "(yours, kept)" on all 34 links of a rebuilt board. Only a
    link you moved (or added) is yours; one still where StripForge put it is just "(kept)". The
    saved link plan records which is which (``legacy``: a plan saved before 0.2.0 without it)."""
    from stripforge import buildsheet

    p2 = pass2(tmp_path, parts=TWO)
    lj = tmp_path / "p2-stripforge.links.json"
    data = json.loads(lj.read_text())
    assert [d["yours"] for d in data["links"]] == [False, False]
    if legacy:
        for d in data["links"]:
            del d["yours"]
        lj.write_text(json.dumps(data))
    row_a = next(r for r in ("CUT1", "CUT2") if _cut_label(p2, r).startswith("A"))
    edit(p2, "W1", "A5")  # W1 now runs A5-C5, so the row A cut moves from A4 to A6
    edit(p2, row_a, "A6")
    for _ in range(2):  # the rebuild, then a second one: W1 stays yours, W2 stays StripForge's
        res = rebuild(p2)
        by_ref = {lk.ref_hint: lk for lk in res.plan.links}
        assert by_ref["W1"].origin == by_ref["W2"].origin == "board"
        assert by_ref["W1"].yours and not by_ref["W2"].yours
        detail = {p.ref: p.detail for p in res.placements}
        assert detail["W1"].endswith("(yours, kept)") and detail["W2"].endswith("(kept)")
        assert "(yours" not in detail["W2"]
        text = (tmp_path / "p2-stripforge.links.txt").read_text()
        (w1,) = [ln for ln in text.splitlines() if ln.strip().startswith("W1 ")]
        (w2,) = [ln for ln in text.splitlines() if ln.strip().startswith("W2 ")]
        assert "(yours, kept)" in w1 and "(kept)" in w2 and "yours" not in w2
        saved = {d["ref"]: (d["yours"], d.get("yours_why")) for d in json.loads(lj.read_text())["links"]}
        assert saved == {"W1": (True, "moved"), "W2": (False, None)}
        html = buildsheet.render_html(
            buildsheet.sheet_model(p2, BoardConfig(trim_pieces=False), date="2026-09-29")
        )
        rows = re.findall(r'<tr class="item" data-kind="link" data-link="(W\d+)"(.*?)</tr>', html, re.S)
        assert [r for r, _ in rows] == ["W1", "W2"]
        assert [r for r, body in rows if "(yours)" in body] == ["W1"]


def _yours(res):
    return sorted(lk.ref_hint for lk in res.plan.links if lk.origin == "board" and lk.yours)


def _set_saved(lj, **changes):
    data = json.loads(lj.read_text())
    for d in data["links"]:
        change = changes.get(d["ref"], {})
        for key, value in change.items() if isinstance(change, dict) else ():
            if value is None:
                d.pop(key, None)
            else:
                d[key] = value
    data["links"] = [d for d in data["links"] if changes.get(d["ref"]) != "drop"]
    lj.write_text(json.dumps(data))


def _moved_w1(tmp_path):
    """The two-link board with W1 moved to A5-C5 (and the row A cut to A6): an edited board."""
    p2 = pass2(tmp_path, parts=TWO)
    row_a = next(r for r in ("CUT1", "CUT2") if _cut_label(p2, r).startswith("A"))
    edit(p2, "W1", "A5")
    edit(p2, row_a, "A6")
    return p2, tmp_path / "p2-stripforge.links.json"


def test_guessed_yours_marks_are_cleared(tmp_path):
    """A 0.2.0 pre-release guessed and saved "yours": true on links StripForge had placed (Kevin's
    W1, W3, W5, W19, W22, W26, W27). With no reason saved, the next build clears the mark."""
    p2, lj = _moved_w1(tmp_path)
    _set_saved(lj, W1={"yours": True}, W2={"yours": True})  # guessed marks, no "yours_why"
    for _ in range(2):
        res = rebuild(p2)
        assert _yours(res) == ["W1"]  # W1 was moved (its holes differ from the saved plan)
        detail = {p.ref: p.detail for p in res.placements}
        assert detail["W2"].endswith("(kept)") and "(yours" not in detail["W2"]
        saved = {d["ref"]: (d["yours"], d.get("yours_why")) for d in json.loads(lj.read_text())["links"]}
        assert saved == {"W1": (True, "moved"), "W2": (False, None)}
        assert any("kept your 1 cut(s) and 1 placed link(s)" in w for w in res.warnings)


def test_old_plan_link_not_in_the_fresh_plan_is_not_yours(tmp_path):
    """The old fallback called a link yours when StripForge's fresh plan (made without your edits)
    wouldn't put a link there, which wrongly took StripForge's own links placed around your moved
    cuts. With a plan saved before 0.2.0, a link at its saved holes is StripForge's."""
    p2, lj = _moved_w1(tmp_path)
    rebuild(p2)  # W1 is saved at A5-C5 (not where a fresh plan puts it)
    _set_saved(lj, W1={"yours": None, "yours_why": None}, W2={"yours": None})  # as before 0.2.0
    for _ in range(2):
        res = rebuild(p2)
        assert ("A5", "C5") in links(res) and _yours(res) == []
        assert all(not d["yours"] for d in json.loads(lj.read_text())["links"])


def test_link_in_no_saved_plan_is_yours(tmp_path):
    """A W link the saved plan doesn't have (you added it) is yours, and stays yours."""
    p2, lj = _moved_w1(tmp_path)
    _set_saved(lj, W2="drop")
    for _ in range(2):
        res = rebuild(p2)
        assert _yours(res) == ["W1", "W2"]
        why = {d["ref"]: d.get("yours_why") for d in json.loads(lj.read_text())["links"]}
        assert why == {"W1": "moved", "W2": "added"}


def test_no_saved_plan_links_are_stripforges(tmp_path):
    """Without a saved plan there is no telling which links you moved: when unsure, a link is
    StripForge's."""
    p2, lj = _moved_w1(tmp_path)
    lj.unlink()
    res = rebuild(p2)
    assert _yours(res) == [] and ("A5", "C5") in links(res)


def test_links_moved_before_the_last_build_are_found_in_the_backups(tmp_path):
    """Kevin moved W1, W3, W5, W19, W25, W26 and W27 off the corner holes over several builds, and
    a 0.2.0 pre-release saved guessed marks (some right, some wrong, W25 missed). The link plan
    doesn't say what happened before the last build, but the -pre-stripbuild backups do: W1 on
    other holes in an older backup was moved by hand. W2, never moved, is StripForge's."""
    p2 = pass2(tmp_path, parts=TWO)
    rebuild(p2)  # backup: W1 at A2-C2, where StripForge put it
    row_a = next(r for r in ("CUT1", "CUT2") if _cut_label(p2, r).startswith("A"))
    edit(p2, "W1", "A5")
    edit(p2, row_a, "A6")
    rebuild(p2)  # backups now: W1 at A2-C2, then A5-C5
    lj = tmp_path / "p2-stripforge.links.json"
    data = json.loads(lj.read_text())
    del data["yours_record"]  # as a pre-release saved it: marks without reasons
    for d in data["links"]:
        d["yours"] = d["ref"] == "W2"  # a wrong guess on W2, a missed one on W1
        d.pop("yours_why", None)
    lj.write_text(json.dumps(data))
    for _ in range(2):
        res = rebuild(p2)
        assert ("A5", "C5") in links(res)  # kept where you put it
        assert _yours(res) == ["W1"]
        saved = json.loads(lj.read_text())
        assert saved["yours_record"] == 1
        why = {d["ref"]: (d["yours"], d.get("yours_why")) for d in saved["links"]}
        assert why == {"W1": (True, "moved"), "W2": (False, None)}


def test_moved_in_backups(tmp_path):
    """Only a change from holes to other holes counts, and only if the link stayed there."""
    from stripforge import writer as w

    p2 = pass2(tmp_path, parts=TWO)
    rebuild(p2)
    assert w.moved_in_backups(p2, BoardConfig(trim_pieces=False)) == {}
    edit(p2, "W2", "A5")
    rebuild(p2)
    moved = w.moved_in_backups(p2, BoardConfig(trim_pieces=False))
    assert list(moved) == ["W2"] and "A5" in moved["W2"]
    edit(p2, "W2", "A2", delete=True)  # gone from the next backup: no longer counted
    rebuild(p2)
    assert w.moved_in_backups(p2, BoardConfig(trim_pieces=False)) == {}
