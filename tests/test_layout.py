# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""M4b: the stripforge/ folder layout, save points and Restore last (stripforge.layout)."""

from __future__ import annotations

import zipfile

import pytest

from stripforge import layout
from stripforge.config import BoardConfig, load


def _project(tmp_path, name="LoadMan"):
    (tmp_path / f"{name}.kicad_pro").write_text("{}\n")
    board = tmp_path / f"{name}.kicad_pcb"
    board.write_text("board v1")
    return board


def test_paths_use_the_project_name_and_the_stripforge_folder(tmp_path):
    board = _project(tmp_path)
    sf = tmp_path / "stripforge"
    assert layout.project_dir(board) == tmp_path and layout.project_name(board) == "LoadMan"
    assert layout.out_file(board, "links.json") == sf / "LoadMan-stripforge.links.json"
    assert layout.backup_dir(board) == sf / "LoadMan-stripforge-backups"
    # a separate output inside stripforge/ belongs to the same project
    built = sf / "LoadMan-stripforge.kicad_pcb"
    assert (
        layout.project_dir(built) == tmp_path
        and layout.out_file(built, "log") == sf / "LoadMan-stripforge.log"
    )
    # the board named differently from the only project
    other = tmp_path / "rev2.kicad_pcb"
    assert layout.project_name(other) == "LoadMan"
    assert layout.legacy_file(board, "links.json") == tmp_path / "LoadMan-stripforge.links.json"


def test_output_and_backup_dir_are_relative_config_keys(tmp_path):
    board = _project(tmp_path)
    cfg = BoardConfig(output_dir="build/sf", backup_dir="saves")
    assert layout.out_file(board, "log", cfg) == tmp_path / "build" / "sf" / "LoadMan-stripforge.log"
    assert layout.backup_dir(board, cfg) == tmp_path / "saves"
    toml = tmp_path / "c.toml"
    for bad in ("/abs/path", "../outside", "C:\\x", "a/../../b", ""):
        toml.write_text(f'output_dir = "{bad}"\n'.replace("\\", "\\\\"))
        with pytest.raises(ValueError, match="output_dir"):
            load(toml)
    toml.write_text('backup_dir = "stripforge/old saves"\n')
    assert load(toml).backup_dir == "stripforge/old saves"


def test_find_config_prefers_the_stripforge_folder(tmp_path):
    board = _project(tmp_path)
    assert layout.find_config(board) is None
    (tmp_path / "stripboard.toml").write_text("")
    assert layout.find_config(board) == tmp_path / "stripboard.toml"
    (tmp_path / "stripforge").mkdir()
    (tmp_path / "stripforge" / "stripboard.toml").write_text("")
    assert layout.find_config(board) == tmp_path / "stripforge" / "stripboard.toml"


def test_save_points_are_new_files_never_renamed_and_pruned_oldest_first(tmp_path):
    board = _project(tmp_path)
    links = layout.out_file(board, "links.json")
    t = 1_790_000_000.0
    a = layout.save_point(board, [board, links], action="build", now=t)
    assert (
        a.path.name
        == "LoadMan-stripforge-" + layout.time.strftime("%Y-%m-%d_%H%M%S", layout.time.localtime(t)) + ".zip"
    )
    assert a.files == ["LoadMan.kicad_pcb"] and a.absent == ["stripforge/LoadMan-stripforge.links.json"]
    b = layout.save_point(board, [board], now=t)  # same second: a fresh name
    assert b.path.name.endswith("-2.zip") and a.path.is_file()
    c = layout.save_point(board, [board], now=t + 60)
    assert layout.backups(board) == [a.path, b.path, c.path]
    with zipfile.ZipFile(a.path) as z:
        assert set(z.namelist()) == {layout.MANIFEST, "LoadMan.kicad_pcb"}
    d = layout.save_point(board, [board], cfg=BoardConfig(backup_keep=2), now=t + 120)
    assert d.pruned == [a.path.name, b.path.name] and layout.backups(board) == [c.path, d.path]
    assert not list(layout.backup_dir(board).glob("*.part"))
    # KiCad's own project backups and other zips are not ours
    (layout.backup_dir(board) / "LoadMan-2026-10-26_120000.zip").write_bytes(b"")
    assert len(layout.backups(board)) == 2


def test_restore_removes_newer_work_and_unpacks_under_the_real_names(tmp_path):
    board = _project(tmp_path)
    links = layout.out_file(board, "links.json")
    sp = layout.save_point(board, [board, links], action="build")
    board.write_text("board v2 (built)")
    links.parent.mkdir(parents=True, exist_ok=True)
    links.write_text("{}")
    res = layout.restore(board)
    assert res.zip == sp.path and res.action == "build"
    assert res.restored == ["LoadMan.kicad_pcb"] and res.removed == [
        "stripforge/LoadMan-stripforge.links.json"
    ]
    assert board.read_text() == "board v1" and not links.exists() and sp.path.is_file()
    assert "Loaded save point" in layout.format_restore(res)


def test_restore_without_a_save_point_and_a_hostile_zip(tmp_path):
    board = _project(tmp_path)
    with pytest.raises(FileNotFoundError, match="no save point yet"):
        layout.restore(board)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr(layout.MANIFEST, '{"files": ["../evil.txt"], "absent": []}')
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError, match="outside the project"):
        layout.restore(board, zip_path=bad)
    assert not (tmp_path.parent / "evil.txt").exists() and board.read_text() == "board v1"


def test_legacy_note_names_the_old_flat_files(tmp_path):
    board = _project(tmp_path)
    assert layout.legacy_note(board) is None
    (tmp_path / "LoadMan-pre-stripbuild-1.kicad_pcb").write_text("")
    (tmp_path / "LoadMan-stripforge.links.json").write_text("")
    (tmp_path / "LoadMan-other.txt").write_text("")  # not ours
    note = layout.legacy_note(board)
    assert "LoadMan-pre-stripbuild-1.kicad_pcb" in note and "LoadMan-stripforge.links.json" in note
    assert "LoadMan-other.txt" not in note and "left where they are" in note
