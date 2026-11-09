# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Circuit acceptance on Kevin's current LedSwitch25 archive.

Unit fixtures (TPI and the synthetic restore walk) stay. This file is the one that has to use
the archived project. Each test extracts a new copy. The zip in the repo is not modified.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from ledswitch_fixture import (
    HISTORICAL_SHA256,
    HISTORICAL_SIZE,
    HISTORICAL_ZIP,
    PCB_SHA256,
    ZIP_SHA256,
    archive_bytes,
    extract,
)
from stripforge import layout, resources
from stripforge import links as links_mod
from stripforge.analyze import analyze
from stripforge.board import HOLES_LIB_ID, load_board, save_board
from stripforge.cli import main
from stripforge.config import (
    StripboardConflict,
    board_form_from_file,
    load,
    read_stripboard_text,
    save_stripboard_text,
)
from stripforge.grid import Grid, Node
from stripforge.linksym import _placed_symbols, add_link_symbols, load_hierarchy, pin_points
from stripforge.sexpr import atom, find, find_all, head, parse
from stripforge.writer import place_new_link

USER_TRACK = "USER-ORIGINAL-GEOMETRY"
# Edge.Cuts of the archived pcb: 1.3 in square. Hole centres sit one pitch inside every edge.
OUTLINE_NM = (127_000_000, 101_600_000, 160_020_000, 134_620_000)
GRID = Grid(129_540_000, 104_140_000, 12, 12, 2_540_000)
STRIP_WIDTH_MM = 1.8


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _top_counts(board) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in parse(board.read_text()).root:
        name = head(node)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


def _covered_holes(path) -> set[str]:
    """Hole labels occupied by a drawn hole, a cut, or a pad within 0.05 mm of that hole."""
    board = load_board(path)
    found: set[str] = set()
    for node in board.doc.root:
        if head(node) != "footprint" or atom(node, 1) != HOLES_LIB_ID:
            continue
        for pad in find_all(node, "pad"):
            found.add(atom(pad, 1))
    for fp in board.footprints:
        points = [(fp.x_nm, fp.y_nm)] if fp.ref.startswith("CUT") else [(p.x_nm, p.y_nm) for p in fp.pads]
        for x_nm, y_nm in points:
            node = GRID.nearest(x_nm, y_nm)
            if not GRID.contains(node):
                continue
            hx, hy = GRID.hole_xy(node)
            if max(abs(x_nm - hx), abs(y_nm - hy)) <= 50_000:
                found.add(node.label)
    return found


def _assert_a1_l12(path) -> None:
    """Generated strips, holes and cuts are the whole A1-L12 grid, inside the 33.02 mm outline."""
    board = load_board(path)
    assert board.outline == OUTLINE_NM
    x0, y0, x1, y1 = board.outline
    a1 = GRID.hole_xy(Node(0, 0))
    last = GRID.hole_xy(GRID.last)
    assert (a1[0] - x0, a1[1] - y0, x1 - last[0], y1 - last[1]) == (2_540_000,) * 4
    expected = {Node(row, col).label for row in range(12) for col in range(12)}
    assert _covered_holes(path) == expected
    half = int(STRIP_WIDTH_MM * 1e6) // 2
    tracks = 0
    for node in board.doc.root:
        if head(node) != "segment":
            continue
        tracks += 1
        assert atom(find(node, "layer"), 1) == "B.Cu"
        assert float(atom(find(node, "width"), 1)) == STRIP_WIDTH_MM
        for tag in ("start", "end"):
            pt = find(node, tag)
            x, y = int(round(float(pt[1]) * 1e6)), int(round(float(pt[2]) * 1e6))
            assert x0 <= x - half and x + half <= x1
            assert y0 <= y - half and y + half <= y1
            hole = GRID.nearest(x, y)
            assert GRID.contains(hole) and GRID.hole_xy(hole) == (x, y)
    assert tracks == 118


def test_archive_checksum_and_a_fresh_extract_cannot_replace_it(tmp_path):
    first = archive_bytes()
    assert hashlib.sha256(first).hexdigest() == ZIP_SHA256
    project = extract(tmp_path / "one")
    board = project / "LedSwitch25.kicad_pcb"
    board.write_bytes(board.read_bytes() + b"\nchanged\n")
    assert archive_bytes() == first
    again = extract(tmp_path / "two")
    assert _sha(again / "LedSwitch25.kicad_pcb") == PCB_SHA256
    assert (again / "LedSwitch25.kicad_pcb").read_bytes() != board.read_bytes()


def test_archived_board_against_its_settings(tmp_path, capsys):
    project = extract(tmp_path / "proj")
    board = project / "LedSwitch25.kicad_pcb"
    toml_path = project / "stripboard.toml"
    toml = toml_path.read_text()
    assert "25 by 25" in toml and "(1.27, 1.27)" in toml  # the comment, left as archived
    cfg = load(toml_path)
    assert (cfg.rows, cfg.cols, cfg.origin_mm, cfg.pitch_mm) == (12, 12, (129.54, 104.14), 2.54)
    assert cfg.backup_policy == "move" and cfg.backup_keep == 0
    assert cfg.output == "in_place" and cfg.link_lead_allowance_in == pytest.approx(0.15)
    assert cfg.bend == {}
    assert toml_path.read_text() == toml  # loading does not rewrite the file

    original_pcb = board.read_bytes()
    assert _sha(board) == PCB_SHA256
    counts = _top_counts(board)
    assert counts.get("footprint") == 9
    assert "segment" not in counts and "via" not in counts and "arc" not in counts
    loaded = load_board(board)
    assert len(loaded.footprints) == 9 and len(loaded.pads) == 20
    assert loaded.outline == OUTLINE_NM
    x0, y0, x1, y1 = (v / 1e6 for v in loaded.outline)
    assert (x1 - x0, y1 - y0) == pytest.approx((33.02, 33.02))
    # 12 holes: the far centres are one pitch inside the far edges, same as A1 on the near edges.
    assert 129.54 + 11 * 2.54 == pytest.approx(157.48)
    assert 104.14 + 11 * 2.54 == pytest.approx(132.08)
    assert x1 - (129.54 + 11 * 2.54) == pytest.approx(2.54)
    assert y1 - (104.14 + 11 * 2.54) == pytest.approx(2.54)

    assert not (project / "LedSwitch25-backups").exists()
    sw1_fp = loaded.footprint("SW1")
    assert sw1_fp.lib_id == "Button_Switch_THT:SW_Slide-03_Wuerth-WS-SLTV_10x2.5x6.4_P2.54mm"
    assert (sw1_fp.x_nm / 1e6, sw1_fp.y_nm / 1e6) == pytest.approx((129.54, 127.0))
    for ref in ("R2", "R3", "R5"):
        pads = loaded.footprint(ref).pads
        span = ((pads[0].x_nm - pads[1].x_nm) ** 2 + (pads[0].y_nm - pads[1].y_nm) ** 2) ** 0.5
        assert span / 1e6 == pytest.approx(10.16)

    analysis = analyze(board, cfg)
    assert analysis.grid.span_label == "A1-L12"
    assert analysis.grid_warnings == []
    assert analysis.rejected == []
    accepted = sorted(s.ref for s in analysis.snaps if s.accepted)
    assert accepted == ["BT1", "C1", "D1", "D2", "Q1", "R2", "R3", "R5", "SW1"]
    assert toml_path.read_text() == toml

    # Archived output is in_place. No bend override. A1-L12 is the whole configured grid.
    code = main(["build", str(board), "--config", str(toml_path)])
    assert code == 0
    assert board.read_bytes() != original_pcb
    _assert_a1_l12(board)
    report = capsys.readouterr().out
    assert "118 B.Cu" in report and "Holes: 108" in report
    assert "Cuts: 7" in report and "8 of 8 placed" in report
    assert "extends past Edge.Cuts" not in report
    assert toml_path.read_text() == toml
    assert layout.backups(board, cfg)
    assert hashlib.sha256(archive_bytes()).hexdigest() == ZIP_SHA256
    assert HISTORICAL_ZIP.is_file()
    assert HISTORICAL_ZIP.stat().st_size == HISTORICAL_SIZE
    assert hashlib.sha256(HISTORICAL_ZIP.read_bytes()).hexdigest() == HISTORICAL_SHA256


def test_restore_returns_to_the_archived_board(tmp_path, capsys):
    """Six save points of this archive's own board, then six restores.

    This is not six builds. A separate test builds this archive six times.
    """
    before = archive_bytes()
    project = extract(tmp_path / "proj")
    board = project / "LedSwitch25.kicad_pcb"
    toml_path = project / "stripboard.toml"
    baseline = board.read_bytes()
    toml_bytes = toml_path.read_bytes()
    assert not (project / "LedSwitch25-backups").exists()
    assert hashlib.sha256(baseline).hexdigest() == PCB_SHA256
    assert USER_TRACK.encode() not in baseline
    cfg = load(toml_path)
    assert cfg.backup_policy == "move" and cfg.backup_keep == 0
    links = layout.out_file(board, "links.json", cfg)
    t0 = 1_800_000_000.0
    saved = []
    for i in range(6):
        sp = layout.save_point(board, [board, links], cfg=cfg, action="build", now=t0 + i * 10)
        saved.append((sp.path, sp.path.read_bytes()))
        board.write_bytes(baseline + f'\n(gr_text "SF-GEN-{i}" (uuid "{USER_TRACK}-{i}"))\n'.encode())
        links.parent.mkdir(parents=True, exist_ok=True)
        links.write_text(f'{{"generated": {i}}}\n')
    assert USER_TRACK.encode() in board.read_bytes()
    assert layout.backups(board, cfg) == [path for path, _data in saved]

    for step in range(6):
        assert main(["restore", str(board), "--config", str(toml_path)]) == 0
        text = capsys.readouterr().out
        assert "Loaded save point" in text
        assert saved[5 - step][0].name in text
        assert "safety" not in saved[5 - step][0].name
        if step == 5:
            assert "first save point" in text
    assert board.read_bytes() == baseline
    assert USER_TRACK.encode() not in board.read_bytes()
    assert not links.exists()
    assert main(["restore", str(board), "--config", str(toml_path)]) == 0
    done = capsys.readouterr().out
    assert "nothing further to restore" in done and "Nothing was changed" in done
    assert board.read_bytes() == baseline
    for path, data in saved:
        assert path.is_file() and path.read_bytes() == data
    assert len(layout.backups(board, cfg)) == 6
    assert len(layout.safety_copies(board, cfg)) == 6
    assert toml_path.read_bytes() == toml_bytes
    assert 'backup_policy = "move"' in toml_path.read_text() and "backup_keep = 0" in toml_path.read_text()
    assert archive_bytes() == before


def test_raw_bend_on_the_archived_toml_then_restore_checks(tmp_path):
    """[bend] is millimetres. The archived zip is not a file the editor writes."""
    before = archive_bytes()
    project = extract(tmp_path / "proj")
    path = project / "stripboard.toml"
    original, baseline = read_stripboard_text(path)
    edited = original if original.endswith("\n") else original + "\n"
    edited += "[bend]\nSW1 = 0.46\n"
    cfg = save_stripboard_text(path, edited, baseline)
    assert cfg.bend["SW1"] == pytest.approx(0.46)
    assert cfg.rows == 12 and cfg.cols == 12 and cfg.origin_mm == (129.54, 104.14)
    assert cfg.backup_policy == "move" and cfg.backup_keep == 0
    assert any("0.46" in warning and "mm" in warning for warning in cfg.warnings)
    assert path.read_text() == edited
    assert "# 25 by 25" in edited and "(1.27, 1.27)" in edited
    disk = path.read_bytes()
    with pytest.raises(ValueError, match="not valid TOML"):
        save_stripboard_text(path, edited + "SW1 =\n", disk)
    assert path.read_bytes() == disk
    with pytest.raises(ValueError, match="0.5 mm"):
        save_stripboard_text(path, edited.replace("SW1 = 0.46", "SW1 = 0.6"), disk)
    assert path.read_bytes() == disk
    path.write_bytes(disk + b"# external\n")
    external = path.read_bytes()
    with pytest.raises(StripboardConflict, match="changed after"):
        save_stripboard_text(path, edited.replace("rows = 12", "rows = 14"), disk)
    assert path.read_bytes() == external

    synced = extract(tmp_path / "sync")
    sync_path = synced / "stripboard.toml"
    sync_text, sync_base = read_stripboard_text(sync_path)
    save_stripboard_text(sync_path, sync_text.replace("rows = 12", "rows = 14"), sync_base)
    form = board_form_from_file(sync_path)
    assert form["rows"] == "14" and form["origin_x_mm"] == "129.54" and form["origin_y_mm"] == "104.14"
    assert load(sync_path).backup_policy == "move" and load(sync_path).backup_keep == 0
    assert "bend" not in form
    assert archive_bytes() == before


def test_six_builds_then_six_restores_return_the_archived_board(tmp_path, capsys):
    """Six in-place builds, then six restores, back to the archived pcb bytes.

    SW1 needs no bend override. Each build writes the whole A1-L12 grid inside the 33.02 mm
    outline. The zip is not changed.
    """
    before = archive_bytes()
    project = extract(tmp_path / "proj")
    board = project / "LedSwitch25.kicad_pcb"
    toml_path = project / "stripboard.toml"
    original = board.read_bytes()
    cfg = load(toml_path)
    assert cfg.output == "in_place" and cfg.bend == {}
    assert (cfg.rows, cfg.cols) == (12, 12)
    for n in range(6):
        assert main(["build", str(board), "--config", str(toml_path)]) == 0
        report = capsys.readouterr().out
        assert "extends past Edge.Cuts" not in report
        _assert_a1_l12(board)
        if n == 0:
            assert "Holes: 108" in report and "118 B.Cu" in report
    assert len(layout.backups(board, cfg)) == 6
    for _ in range(6):
        assert main(["restore", str(board), "--config", str(toml_path)]) == 0
        capsys.readouterr()
    assert board.read_bytes() == original
    assert archive_bytes() == before


def test_link_symbols_resolve_on_the_archived_schematic(tmp_path):
    """W footprints get a StripForge:Link whose uuid is the footprint path KiCad looks up.

    The links are placed by the same helper the build uses, without moving SW1. Add links to
    schematic is run twice. The canonical zip is not written.
    """
    before = archive_bytes()
    project = extract(tmp_path / "proj")
    board_path = project / "LedSwitch25.kicad_pcb"
    sw1 = next(fp for fp in load_board(board_path).footprints if fp.ref == "SW1")
    sw1_at = (sw1.x_nm, sw1.y_nm)
    cfg = load(project / "stripboard.toml")
    assert cfg.output == "in_place" and cfg.bend == {}
    analysis = analyze(board_path, cfg)
    plan = links_mod.propose(analysis)
    assert plan.links
    board = load_board(board_path)
    library = resources.library_dir()
    root = board.doc.root
    last = max((i for i, node in enumerate(root) if head(node) == "footprint"), default=len(root) - 1)
    placed = []
    for lk in plan.links:
        node, _fp = place_new_link(board, analysis, lk, library)
        placed.append((lk.ref_hint, atom(find(node, "path"), 1), lk.footprint))
        root.insert(last + 1, node)
        last += 1
    save_board(board, board_path)
    assert next(fp for fp in load_board(board_path).footprints if fp.ref == "SW1").x_nm == sw1_at[0]
    assert next(fp for fp in load_board(board_path).footprints if fp.ref == "SW1").y_nm == sw1_at[1]

    res = add_link_symbols(project / "LedSwitch25.kicad_sch", board_path, in_place=True, cfg=cfg)
    assert {item.ref for item in res.added} == {ref for ref, _path, _fp in placed}
    assert res.backups and "links" in layout.read_manifest(Path(res.backups[0]))["action"]
    first_backup = Path(res.backups[0]).read_bytes()
    sheets = load_hierarchy(project / "LedSwitch25.kicad_sch")
    symbols = {ref: node for node, ref in _placed_symbols(sheets[0]) if ref and ref.startswith("W")}
    libs = find(sheets[0].doc.root, "lib_symbols")
    embedded = next(s for s in find_all(libs, "symbol") if atom(s, 1) == "StripForge:Link")
    units = [atom(s, 1) for s in find_all(embedded, "symbol")]
    assert units == ["Link_0_1", "Link_1_1"]
    for ref, path, footprint in placed:
        node = symbols[ref]
        assert atom(find(node, "lib_id"), 1) == "StripForge:Link"
        assert atom(find(node, "uuid"), 1) == path.rsplit("/", 1)[-1]
        assert set(pin_points(sheets[0], node)) == {"1", "2"}
        fp_field = next(atom(p, 2) for p in find_all(node, "property") if atom(p, 1) == "Footprint")
        assert fp_field == footprint
    again = add_link_symbols(project / "LedSwitch25.kicad_sch", board_path, in_place=True, cfg=cfg)
    assert not again.added
    sheets = load_hierarchy(project / "LedSwitch25.kicad_sch")
    refs = [ref for _node, ref in _placed_symbols(sheets[0]) if ref and ref.startswith("W")]
    assert sorted(refs) == sorted(ref for ref, _path, _fp in placed)
    assert Path(res.backups[0]).read_bytes() == first_backup
    assert len(layout.backups(board_path, cfg)) == 1
    assert archive_bytes() == before
