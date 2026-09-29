import json
import shutil

import pytest

import pass2_sim
from sfbuild import footprints, one_pad_board, segments, xy
from stripforge import config, writer
from stripforge.config import BoardConfig, CutStyle
from stripforge.sexpr import atom, find

SPLIT = [("P1", 0, 0, "A"), ("P2", 6, 0, "B"), ("P3", 0, 2, "A")]


def build(tmp_path, parts=SPLIT, cfg=None, **kw):
    out = tmp_path / "out.kicad_pcb"
    cfg = cfg or BoardConfig(trim_pieces=False, place_links=False)  # the two-pass flow unless asked
    return writer.build(one_pad_board(tmp_path, parts), cfg, out, **kw), out


def touches(seg, point):
    return seg[0] == point or seg[1] == point


def test_strips_are_written_hole_to_hole_on_their_nets(tmp_path):
    res, out = build(tmp_path)
    segs = segments(out)
    # 5 rows x 9 gaps = 45, minus 2 either side of the hole cut on row A
    assert len(segs) == res.segments == 43
    assert all(s[2] == 1.8 for s in segs)
    row_a = sorted((s for s in segs if s[0][1] == xy(0, 0)[1]), key=lambda s: s[0][0])
    assert [s[3] for s in row_a] == ["A", "A"] + ["B"] * 5
    assert row_a[0][0] == xy(0, 0) and row_a[0][1] == xy(1, 0)
    # bare rows B, D and E are copper with no net
    assert res.segments_no_net == 27
    assert sum(1 for s in segs if s[3] is None) == 27
    assert {s[3] for s in segs if s[0][1] == xy(0, 2)[1]} == {"A"}


def test_no_copper_touches_a_hole_cut(tmp_path):
    res, out = build(tmp_path)
    (cut,) = res.analysis.split.cuts
    assert cut.style == "hole" and cut.col == 3.0
    assert not any(touches(s, xy(3, 0)) for s in segments(out))


def test_knife_cut_leaves_a_gap_between_two_holes(tmp_path):
    res, out = build(tmp_path, cfg=BoardConfig(trim_pieces=False, cut_style=CutStyle.KNIFE))
    (cut,) = res.analysis.split.cuts
    assert cut.style == "knife" and cut.col == 2.5
    segs = segments(out)
    assert not any(touches(s, xy(2, 0)) and touches(s, xy(3, 0)) for s in segs)
    assert any(touches(s, xy(2, 0)) for s in segs) and any(touches(s, xy(3, 0)) for s in segs)


def test_cut_markers_are_embedded_board_only(tmp_path):
    res, out = build(tmp_path, cfg=BoardConfig(trim_pieces=False, cut_style=CutStyle.KNIFE))
    (cut_fp,) = footprints(out, "CUT")
    assert cut_fp.ref == "CUT1" and cut_fp.lib_id == "StripForge:CUT_Knife"
    assert (cut_fp.x_nm, cut_fp.y_nm) == (7_620_000, 1_270_000)
    attr = find(cut_fp.node, "attr")
    for flag in ("board_only", "exclude_from_pos_files", "exclude_from_bom"):
        assert flag in [str(x) for x in attr]
    assert find(cut_fp.node, "fp_line") or find(cut_fp.node, "fp_circle") or find(cut_fp.node, "fp_rect")
    res2, out2 = build(tmp_path)
    assert footprints(out2, "CUT")[0].lib_id == "StripForge:CUT_Hole"


def test_rules_and_link_files_are_written_next_to_the_board(tmp_path):
    res, out = build(tmp_path)
    assert (tmp_path / "out.kicad_dru").read_text() == writer.resources.rules_file().read_text()
    for suffix in (".links.json", ".links.csv", ".links.txt"):
        assert (tmp_path / f"out-stripforge{suffix}").exists()


def test_rules_width_mismatch_warns(tmp_path):
    res, _ = build(tmp_path, cfg=BoardConfig(trim_pieces=False, strip_width_mm=2.0))
    assert any("gen_dru.py" in w for w in res.warnings)
    res, _ = build(tmp_path)
    assert not any("gen_dru.py" in w for w in res.warnings)


def test_input_is_never_overwritten(tmp_path):
    board = one_pad_board(tmp_path, SPLIT)
    before = board.read_bytes()
    with pytest.raises(writer.BuildError):
        writer.build(board, BoardConfig(trim_pieces=False, place_links=False), board)
    assert board.read_bytes() == before
    writer.build(board, BoardConfig(trim_pieces=False, place_links=False), tmp_path / "out.kicad_pcb")
    assert board.read_bytes() == before


def test_foreign_tracks_are_refused(tmp_path):
    seg = '(segment (start 1 1) (end 3 1) (width 0.25) (layer "B.Cu") (net "A") (uuid "u1"))'
    board = one_pad_board(tmp_path, SPLIT, extra=seg)
    with pytest.raises(writer.BuildError, match="did not write"):
        writer.build(board, BoardConfig(trim_pieces=False, place_links=False), tmp_path / "out.kicad_pcb")


def test_rebuild_replaces_own_output_and_is_deterministic(tmp_path):
    res, out = build(tmp_path)
    again = tmp_path / "again.kicad_pcb"
    res2 = writer.build(out, BoardConfig(trim_pieces=False, place_links=False), again)
    assert res2.removed_previous == (43, 1)
    assert res2.segments == 43 and len(footprints(again, "CUT")) == 1
    # same board, same bytes (uuid5)
    third = tmp_path / "third.kicad_pcb"
    writer.build(tmp_path / "in.kicad_pcb", BoardConfig(trim_pieces=False, place_links=False), third)
    assert third.read_bytes() == out.read_bytes()


def test_conflicts_refuse_the_build(tmp_path):
    parts = [("P1", 0, 0, "A"), ("P2", 0, 0, "B")]
    with pytest.raises(writer.BuildError, match="conflicts"):
        build(tmp_path, parts)


# --- pass 2 ------------------------------------------------------------------------------------


def _pass2_board(tmp_path, links):
    src = tmp_path / "in.kicad_pcb"
    p2 = tmp_path / "p2in.kicad_pcb"
    pass2_sim.add_links_to_board(src, links, p2)
    return p2


def test_pass2_places_links_on_their_holes(tmp_path):
    res, out = build(tmp_path)
    links = json.loads((tmp_path / "out-stripforge.links.json").read_text())["links"]
    p2 = _pass2_board(tmp_path, links)
    res2 = writer.build(p2, BoardConfig(trim_pieces=False, place_links=False), tmp_path / "p2.kicad_pcb")
    assert res2.pass2 and [p.status for p in res2.placements] == ["placed"]
    assert res2.ok
    (w1,) = footprints(tmp_path / "p2.kicad_pcb", "W")
    pads = {p.number: (p.x_nm / 1e6, p.y_nm / 1e6) for p in w1.pads}
    assert pytest.approx(pads["1"]) == xy(1, 0) and pytest.approx(pads["2"]) == xy(1, 2)
    # the link is not treated as a part: same strips and cuts as pass 1
    assert res2.segments == res.segments
    assert [c.where for c in res2.analysis.split.cuts] == [c.where for c in res.analysis.split.cuts]


def test_pass2_reports_missing_extra_and_wrong_net(tmp_path):
    parts = SPLIT + [("P4", 0, 4, "A"), ("P5", 6, 4, "C")]
    res, _ = build(tmp_path, parts)
    links = json.loads((tmp_path / "out-stripforge.links.json").read_text())["links"]
    assert [lk["ref"] for lk in links] == ["W1", "W2"]
    extra = dict(links[0], ref="W9")
    wrong = dict(links[1], net="B")
    p2 = _pass2_board(tmp_path, [wrong, extra])  # W1 missing
    res2 = writer.build(p2, BoardConfig(trim_pieces=False, place_links=False), tmp_path / "p2.kicad_pcb")
    status = {p.ref: p.status for p in res2.placements}
    assert status == {"W1": "missing", "W2": "wrong-net", "W9": "extra"}
    assert not res2.ok


def test_real_fixture_build(tmp_path, real_board_path, real_netlist_path):
    cfg = config.load(real_board_path.parents[1] / "x56.toml")
    cfg.place_links = False  # pass 1 of the two-pass flow
    out = tmp_path / "b.kicad_pcb"
    res = writer.build(real_board_path, cfg, out, netlist=str(real_netlist_path))
    assert len(res.analysis.snapped) == 22 and not res.analysis.rejected
    assert (
        res.cut_markers == len(res.analysis.split.cuts) == 97
    )  # 30 of them trim pieces back to their last used hole
    assert res.segments == len(segments(out))
    assert not any("Edge.Cuts" in w for w in res.warnings)  # the outline is the X56 board
    assert res.segments == 1154 and res.segments_no_net == 718
    assert res.holes_drawn == 1256  # every free grid hole (pass 1: link holes still empty)
    # pass 2 on a copy: every proposed link is placed
    links = json.loads((tmp_path / "b-stripforge.links.json").read_text())["links"]
    shutil.copy(real_board_path, tmp_path / "in.kicad_pcb")
    p2 = _pass2_board(tmp_path, links)
    res2 = writer.build(p2, cfg, tmp_path / "p2.kicad_pcb", netlist=str(real_netlist_path))
    assert [p.status for p in res2.placements] == ["placed"] * len(links)
    assert res2.holes_drawn == res.holes_drawn - 2 * len(links)  # the W pads take their holes
    assert [lk.to_dict() for lk in res2.plan.links] == [lk.to_dict() for lk in res.plan.links]
    ws = footprints(tmp_path / "p2.kicad_pcb", "W")
    assert len(ws) == 40
    angles = {fp.ref: float(atom(find(fp.node, "at"), 3) or 0) for fp in ws}
    # diagonals only where nothing straight fits (last resort)
    assert {r: a for r, a in angles.items() if a} == {"W5": 348.6901, "W24": 344.0546}
    assert next(lk for lk in res.plan.links if lk.ref_hint == "W24").footprint == "StripForge:Link_D18.49"


# --- rotating backups (output = "in_place") --------------------------------------------------


def _chain(tmp_path, n):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("now")
    writer.backup_path(board).write_text("b0")
    for i in range(1, n + 1):
        writer.backup_path(board, i).write_text(f"b{i}")
    return board


def _contents(tmp_path):
    return {p.name: p.read_text() for p in sorted(tmp_path.iterdir())}


def test_rotate_backups_first_time(tmp_path):
    board = tmp_path / "b.kicad_pcb"
    board.write_text("now")
    rot = writer.rotate_backups(board)
    assert rot.backup == tmp_path / "b-pre-stripbuild.kicad_pcb" and not rot.shifted
    assert _contents(tmp_path) == {"b-pre-stripbuild.kicad_pcb": "now", "b.kicad_pcb": "now"}


def test_rotate_backups_shifts_highest_first(tmp_path):
    board = _chain(tmp_path, 2)
    (tmp_path / "b-pre-stripbuild-x.kicad_pcb").write_text("other")  # not part of the chain
    rot = writer.rotate_backups(board)
    assert rot.shifted == [
        ("b-pre-stripbuild-2.kicad_pcb", "b-pre-stripbuild-3.kicad_pcb"),
        ("b-pre-stripbuild-1.kicad_pcb", "b-pre-stripbuild-2.kicad_pcb"),
        ("b-pre-stripbuild.kicad_pcb", "b-pre-stripbuild-1.kicad_pcb"),
    ]
    assert _contents(tmp_path) == {
        "b-pre-stripbuild-1.kicad_pcb": "b0",
        "b-pre-stripbuild-2.kicad_pcb": "b1",
        "b-pre-stripbuild-3.kicad_pcb": "b2",
        "b-pre-stripbuild-x.kicad_pcb": "other",
        "b-pre-stripbuild.kicad_pcb": "now",
        "b.kicad_pcb": "now",
    }
    assert writer.base_stem(tmp_path / "b-pre-stripbuild-3.kicad_pcb") == "b"


def test_rotate_backups_with_a_gap_and_unlimited(tmp_path):
    board = _chain(tmp_path, 0)
    writer.backup_path(board, 5).write_text("b5")  # a gap: -1..-4 missing
    for _ in range(3):
        writer.rotate_backups(board)
    names = _contents(tmp_path)
    assert names["b-pre-stripbuild-8.kicad_pcb"] == "b5" and names["b-pre-stripbuild-3.kicad_pcb"] == "b0"
    assert len([n for n in names if "pre-stripbuild" in n]) == 5  # nothing lost, nothing overwritten


def test_rotate_backups_keep(tmp_path):
    board = _chain(tmp_path, 3)
    rot = writer.rotate_backups(board, keep=3)  # the unnumbered one, -1 and -2
    assert rot.pruned == ["b-pre-stripbuild-4.kicad_pcb", "b-pre-stripbuild-3.kicad_pcb"]
    assert _contents(tmp_path) == {
        "b-pre-stripbuild-1.kicad_pcb": "b0",
        "b-pre-stripbuild-2.kicad_pcb": "b1",
        "b-pre-stripbuild.kicad_pcb": "now",
        "b.kicad_pcb": "now",
    }
    writer.rotate_backups(board, keep=1)
    assert _contents(tmp_path) == {"b-pre-stripbuild.kicad_pcb": "now", "b.kicad_pcb": "now"}


def test_rotate_backups_stops_before_writing_when_a_rename_fails(tmp_path, monkeypatch):
    board = _chain(tmp_path, 2)
    real_rename = writer.os.rename

    def flaky(src, dst):
        if str(dst).endswith("-2.kicad_pcb"):
            raise PermissionError("locked")
        real_rename(src, dst)

    monkeypatch.setattr(writer.os, "rename", flaky)
    with pytest.raises(writer.BuildError, match="could not rename b-pre-stripbuild-1.kicad_pcb"):
        writer.rotate_backups(board)
    # -2 moved to -3 first; nothing was overwritten and the unnumbered backup was not replaced
    assert _contents(tmp_path) == {
        "b-pre-stripbuild-1.kicad_pcb": "b1",
        "b-pre-stripbuild-3.kicad_pcb": "b2",
        "b-pre-stripbuild.kicad_pcb": "b0",
        "b.kicad_pcb": "now",
    }


def test_rotate_backups_never_overwrites(tmp_path, monkeypatch):
    board = _chain(tmp_path, 1)
    monkeypatch.setattr(writer, "numbered_backups", lambda b, s=None: {})  # e.g. -1 appeared meanwhile
    with pytest.raises(writer.BuildError, match="b-pre-stripbuild-1.kicad_pcb already exists"):
        writer.rotate_backups(board)
    assert _contents(tmp_path)["b-pre-stripbuild-1.kicad_pcb"] == "b1"


def test_backup_keep_config():
    assert BoardConfig().backup_keep == 0
    assert config.from_dict({"backup_keep": 4}).backup_keep == 4
    for bad in (-1, 1.5, True, "3"):
        with pytest.raises(ValueError, match="backup_keep"):
            config.from_dict({"backup_keep": bad})
