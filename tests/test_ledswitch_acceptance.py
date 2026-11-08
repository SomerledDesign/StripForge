# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Circuit acceptance on Kevin's current LedSwitch25 archive.

Unit fixtures (TPI and the synthetic restore walk) stay. This file is the one that has to use
the archived project. Each test extracts a new copy. The zip in the repo is not modified.
"""

from __future__ import annotations

import hashlib
import zipfile

import pytest

from ledswitch_fixture import PCB_SHA256, ZIP_SHA256, archive_bytes, extract
from stripforge import layout
from stripforge.analyze import analyze
from stripforge.board import load_board
from stripforge.cli import main
from stripforge.config import load
from stripforge.sexpr import head, parse

USER_TRACK = "USER-ORIGINAL-GEOMETRY"


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _top_counts(board) -> dict[str, int]:
    counts: dict[str, int] = {}
    for node in parse(board.read_text()).root:
        name = head(node)
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


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


def test_archived_board_against_its_settings(tmp_path):
    project = extract(tmp_path / "proj")
    board = project / "LedSwitch25.kicad_pcb"
    toml_path = project / "stripboard.toml"
    toml = toml_path.read_text()
    assert "25 by 25" in toml and "(1.27, 1.27)" in toml  # the comment, left as archived
    cfg = load(toml_path)
    assert (cfg.rows, cfg.cols, cfg.origin_mm, cfg.pitch_mm) == (13, 13, (129.54, 104.14), 2.54)
    assert cfg.backup_policy == "move" and cfg.backup_keep == 0
    assert toml_path.read_text() == toml  # loading does not rewrite the file

    assert _sha(board) == PCB_SHA256
    counts = _top_counts(board)
    assert counts.get("footprint") == 9
    assert "segment" not in counts and "via" not in counts and "arc" not in counts
    loaded = load_board(board)
    assert len(loaded.footprints) == 9 and len(loaded.pads) == 20
    assert loaded.outline == (127_000_000, 101_600_000, 160_020_000, 134_620_000)
    # 13 holes at 2.54 mm from that origin end on the outline's far edges.
    assert 129.54 + 12 * 2.54 == pytest.approx(160.02)
    assert 104.14 + 12 * 2.54 == pytest.approx(134.62)

    backup = project / "LedSwitch25-backups" / "LedSwitch25-2026-11-07_215736.zip"
    backup_bytes = backup.read_bytes()
    with zipfile.ZipFile(backup) as z:
        assert z.read("LedSwitch25.kicad_pcb") == board.read_bytes()
        assert z.read("_autosave-LedSwitch25.kicad_pcb") == board.read_bytes()

    analysis = analyze(board, cfg)
    assert analysis.grid.span_label == "A1-M13"
    assert analysis.grid_warnings == [
        "grid: the configured 13x13 grid (A1-M13, 33.020 x 33.020 mm) extends past "
        "the Edge.Cuts outline (33.020 x 33.020 mm)"
    ]
    assert [s.ref for s in analysis.rejected] == ["SW1"]
    accepted = sorted(s.ref for s in analysis.snaps if s.accepted)
    assert accepted == ["BT1", "C1", "D1", "D2", "Q1", "R2", "R3", "R5"]
    sw1 = next(s for s in analysis.rejected if s.ref == "SW1")
    assert sw1.max_dev_nm == 460_000
    assert toml_path.read_text() == toml

    code = main(["build", str(board), "--config", str(toml_path), "--in-place"])
    assert code == 2
    assert _sha(board) == PCB_SHA256
    assert toml_path.read_text() == toml
    assert backup.read_bytes() == backup_bytes
    assert layout.backups(board, cfg) == []
    assert hashlib.sha256(archive_bytes()).hexdigest() == ZIP_SHA256


def test_restore_returns_to_the_archived_board(tmp_path, capsys):
    before = archive_bytes()
    project = extract(tmp_path / "proj")
    board = project / "LedSwitch25.kicad_pcb"
    toml_path = project / "stripboard.toml"
    baseline = board.read_bytes()
    toml_bytes = toml_path.read_bytes()
    kicad_backup = project / "LedSwitch25-backups" / "LedSwitch25-2026-11-07_215736.zip"
    kicad_backup_bytes = kicad_backup.read_bytes()
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
    assert kicad_backup.read_bytes() == kicad_backup_bytes
    assert toml_path.read_bytes() == toml_bytes
    assert 'backup_policy = "move"' in toml_path.read_text() and "backup_keep = 0" in toml_path.read_text()
    assert archive_bytes() == before
