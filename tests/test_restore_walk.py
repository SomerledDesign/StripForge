# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Restore last walks every save point back to the pre-build board, then stops.

The baseline is the board file that was zipped before the first build. Getting there puts those
bytes back. It does not delete every track or hole from the live board.

The TPI file used by ``test_real_builds_restore_to_the_unbuilt_fixture`` is unit coverage of a
board ``stripforge build`` accepts. Circuit acceptance is ``test_ledswitch_acceptance.py``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from stripforge import layout
from stripforge.cli import main
from stripforge.config import BoardConfig

USER_TRACK = '(segment (start 1 1) (end 2 1) (layer "B.Cu") (net 0) (uuid "USER-ORIGINAL-GEOMETRY"))\n'


def _project(tmp_path: Path) -> Path:
    (tmp_path / "LoadMan.kicad_pro").write_text("{}\n")
    board = tmp_path / "LoadMan.kicad_pcb"
    board.write_text("board v0 no strips\n" + USER_TRACK)
    return board


def _generated(i: int) -> str:
    return (
        "board v0 no strips\n"
        + USER_TRACK
        + f'(segment (start 0 0) (end {i} 0) (layer "B.Cu") (net {i}))\n'
        + f'(footprint "StripForge:CUT_Hole" (layer "User.1") (uuid "cut-{i}"))\n'
        + f'(footprint "StripForge:Hole" (pad (drill 1)) (uuid "hole-{i}"))\n'
    )


def test_six_restores_reach_the_baseline_then_stop(tmp_path):
    board = _project(tmp_path)
    baseline = board.read_text()
    assert "StripForge:CUT_" not in baseline and "USER-ORIGINAL-GEOMETRY" in baseline
    links = layout.out_file(board, "links.json")
    t0 = 1_790_000_000.0
    saved = []
    for i in range(1, 7):
        sp = layout.save_point(board, [board, links], action="build", now=t0 + i * 10)
        saved.append((sp.path, sp.path.read_bytes()))
        board.write_text(_generated(i))
        links.parent.mkdir(parents=True, exist_ok=True)
        links.write_text(f'{{"generated": {i}}}\n')
    assert "StripForge:CUT_Hole" in board.read_text()
    assert [p.name for p, _data in saved] == [p.name for p in layout.backups(board)]

    loaded = []
    for step in range(6):
        res = layout.restore(board)
        loaded.append(res.zip.name)
        assert res.safety is not None and res.safety.is_file() and "safety" in res.safety.name
        # a fresh call (reopen) reads the cursor file and continues one step older
        if step < 5:
            assert layout.select_restore_zip(board).name == saved[4 - step][0].name
    assert loaded == [p.name for p, _data in reversed(saved)]
    assert board.read_text() == baseline
    assert "USER-ORIGINAL-GEOMETRY" in board.read_text()
    assert "StripForge:CUT_Hole" not in board.read_text()
    assert "StripForge:Hole" not in board.read_text()
    assert not links.exists()
    for path, data in saved:
        assert path.is_file() and path.read_bytes() == data
    assert len(layout.safety_copies(board)) == 6
    assert len(layout.backups(board)) == 6

    with pytest.raises(layout.RestoreExhausted, match="first save point"):
        layout.restore(board)
    assert board.read_text() == baseline and not links.exists()
    for path, data in saved:
        assert path.read_bytes() == data
    assert len(layout.safety_copies(board)) == 6  # the stop did not copy or delete


def test_same_second_zips_walk_newest_suffix_first(tmp_path):
    board = _project(tmp_path)
    t = 1_790_000_000.0
    board.write_text("v0\n" + USER_TRACK)
    first = layout.save_point(board, [board], action="build", now=t)
    board.write_text("v1\n" + USER_TRACK + '(footprint "StripForge:CUT_Hole")\n')
    second = layout.save_point(board, [board], action="build", now=t)
    board.write_text("v2\n" + USER_TRACK)
    assert second.path.name.endswith("-2.zip") and first.path.is_file()
    first_bytes, second_bytes = first.path.read_bytes(), second.path.read_bytes()
    res = layout.restore(board)
    assert res.zip == second.path and board.read_text().startswith("v1\n")
    res = layout.restore(board)
    assert res.zip == first.path and board.read_text() == "v0\n" + USER_TRACK
    with pytest.raises(layout.RestoreExhausted):
        layout.restore(board)
    assert board.read_text() == "v0\n" + USER_TRACK
    assert first.path.read_bytes() == first_bytes and second.path.read_bytes() == second_bytes


def test_a_later_build_does_not_toggle_or_jump_forward(tmp_path):
    board = _project(tmp_path)
    t = 1_790_000_000.0
    states = ["B0\n" + USER_TRACK, "B1\n" + USER_TRACK, "B2\n" + USER_TRACK]
    points = []
    for i, text in enumerate(states):
        board.write_text(text)
        points.append(layout.save_point(board, [board], action="build", now=t + i))
    board.write_text("B3 built\n" + USER_TRACK + '(footprint "StripForge:CUT_Hole")\n')
    # Z0=B0, Z1=B1, Z2=B2, live=B3. Two restores land on B1 (zip Z1).
    assert layout.restore(board).zip == points[2].path
    assert board.read_text() == states[2]
    assert layout.restore(board).zip == points[1].path
    assert board.read_text() == states[1]
    kept = {p.path: p.path.read_bytes() for p in points}
    # A new build from here must undo once, then continue older, not back to B2 or to B3.
    board.write_text(states[1])
    newer = layout.save_point(board, [board], action="build", now=t + 50)
    board.write_text("B4 built\n" + '(footprint "StripForge:CUT_Hole")\n')
    res = layout.restore(board)
    assert res.zip == newer.path and board.read_text() == states[1]
    res = layout.restore(board)
    assert res.zip == points[0].path and board.read_text() == states[0]
    assert "StripForge:CUT_Hole" not in board.read_text()
    with pytest.raises(layout.RestoreExhausted):
        layout.restore(board)
    assert board.read_text() == states[0]
    for path, data in kept.items():
        assert path.read_bytes() == data
    assert newer.path.is_file()


def test_restore_does_not_prune_and_retention_keeps_safety_copies(tmp_path):
    board = _project(tmp_path)
    t = 1_790_000_000.0
    for i in range(3):
        board.write_text(f"v{i}\n" + USER_TRACK)
        layout.save_point(board, [board], cfg=BoardConfig(backup_policy="move", backup_keep=1), now=t + i)
    board.write_text("live\n" + USER_TRACK)
    before = {p.name: p.read_bytes() for p in layout.backups(board)}
    assert len(before) == 3  # backup_keep is ignored while the policy is move
    res = layout.restore(board, BoardConfig(backup_policy="retention", backup_keep=1))
    assert {p.name: p.read_bytes() for p in layout.backups(board)} == before
    assert res.safety is not None and res.safety.read_bytes()
    safety_bytes = res.safety.read_bytes()
    # the next build, with retention opted in, still prunes save points and still keeps the copy
    board.write_text("after\n" + USER_TRACK)
    made = layout.save_point(
        board, [board], cfg=BoardConfig(backup_policy="retention", backup_keep=1), now=t + 30
    )
    assert layout.backups(board) == [made.path]
    assert res.safety.is_file() and res.safety.read_bytes() == safety_bytes
    assert made.pruned  # older save points, not the safety copy


def test_a_bad_zip_and_a_failed_replace_leave_the_board(tmp_path, monkeypatch):
    import zipfile

    board = _project(tmp_path)
    baseline = board.read_text()
    notes = tmp_path / "notes.txt"
    notes.write_text("old notes\n")
    sp = layout.save_point(board, [board, notes], action="build")
    board.write_text("built\n" + USER_TRACK + '(footprint "StripForge:CUT_Hole")\n')
    notes.write_text("current notes\n")
    zip_bytes = sp.path.read_bytes()

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr(layout.MANIFEST, '{"files": ["../evil.txt"], "absent": []}')
        z.writestr("../evil.txt", "x")
    with pytest.raises(ValueError, match="outside the project"):
        layout.restore(board, zip_path=bad)
    assert board.read_text().startswith("built\n")
    assert notes.read_text() == "current notes\n"
    assert not (tmp_path.parent / "evil.txt").exists()
    assert layout.safety_copies(board) == []
    assert sp.path.read_bytes() == zip_bytes

    real_replace = Path.replace

    def fail_notes(self, target):
        if target.name == "notes.txt":
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_notes)
    with pytest.raises(OSError, match="disk full"):
        layout.restore(board)
    assert board.read_text().startswith("built\n")  # the board write was rolled back
    assert notes.read_text() == "current notes\n"  # the failed file was not replaced
    assert sp.path.read_bytes() == zip_bytes
    assert layout.read_restore_cursor(board) is None
    monkeypatch.undo()

    res = layout.restore(board)
    assert res.zip == sp.path
    assert board.read_text() == baseline
    assert notes.read_text() == "old notes\n"
    assert "USER-ORIGINAL-GEOMETRY" in board.read_text()
    assert "StripForge:CUT_Hole" not in board.read_text()
    assert sp.path.read_bytes() == zip_bytes


def test_real_builds_restore_to_the_unbuilt_fixture(tmp_path, capsys, real_board_path):
    board = tmp_path / "fix.kicad_pcb"
    shutil.copy(real_board_path, board)
    (tmp_path / "fix.kicad_pro").write_text("{}\n")
    x56 = real_board_path.parents[1] / "x56.toml"
    baseline = board.read_bytes()
    assert b"StripForge:CUT_" not in baseline and b"R3" in baseline
    for _ in range(6):
        assert main(["build", str(board), "--config", str(x56)]) == 1
    capsys.readouterr()
    points = layout.backups(board)
    assert len(points) == 6
    saved = {p: p.read_bytes() for p in points}
    assert layout.read_member(points[0], "fix.kicad_pcb").encode() == baseline
    assert b"StripForge:CUT_" in board.read_bytes()
    loaded = []
    for _ in range(6):
        assert main(["restore", str(board)]) == 0
        text = capsys.readouterr().out
        assert "Loaded save point" in text and "Safety copy:" in text
        loaded.append(text)
    assert board.read_bytes() == baseline
    assert b"StripForge:CUT_" not in board.read_bytes() and b"R3" in board.read_bytes()
    assert "first save point" in loaded[-1]
    for path, data in saved.items():
        assert path.read_bytes() == data
    assert len(layout.safety_copies(board)) == 6
    assert main(["restore", str(board)]) == 0
    done = capsys.readouterr().out
    assert "nothing further to restore" in done and "Nothing was changed" in done
    assert board.read_bytes() == baseline
    for path, data in saved.items():
        assert path.read_bytes() == data
    assert len(layout.safety_copies(board)) == 6
