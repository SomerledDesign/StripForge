# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Backup policy: move never deletes; retention is opt-in. Editing the TOML keeps comments."""

from __future__ import annotations

from stripforge import layout
from stripforge.config import (
    BoardConfig,
    load,
    prune_keep,
    save_backup_settings,
    update_backup_settings_text,
)


def test_update_backup_settings_text_keeps_comments_unknown_shape_and_tables():
    src = '# keep this\nrows = 24\nbackup_keep = 4\n# backup_policy = "retention"\n\n[bend]\nSW2 = 0.16\n'
    out = update_backup_settings_text(src, "retention", 2)
    assert out.startswith("# keep this\n")
    assert "rows = 24\n" in out and "[bend]\nSW2 = 0.16\n" in out
    assert '# backup_policy = "retention"\n' in out  # the comment is not the setting
    root = {line.strip() for line in out.split("[bend]", 1)[0].splitlines()}
    assert 'backup_policy = "retention"' in root and "backup_keep = 2" in root
    again = update_backup_settings_text(out, "move", 2)
    lines = [line.strip() for line in again.splitlines()]
    assert lines.count('backup_policy = "move"') == 1
    assert '# backup_policy = "retention"' in lines and "[bend]" in lines and "SW2 = 0.16" in lines


def test_a_new_config_file_is_only_the_policy(tmp_path):
    path = tmp_path / "stripforge" / "stripboard.toml"
    save_backup_settings(path, "move", 0)
    text = path.read_text()
    assert "rows" not in text and "diagonal_links" not in text
    cfg = load(path)
    assert cfg.backup_policy == "move" and cfg.backup_keep == 0 and prune_keep(cfg) == 0


def test_restore_under_both_policies_keeps_the_zip(tmp_path):
    for policy in ("move", "retention"):
        root = tmp_path / policy
        root.mkdir()
        (root / "LoadMan.kicad_pro").write_text("{}\n")
        board = root / "LoadMan.kicad_pcb"
        board.write_text("board v1")
        cfg = BoardConfig(backup_policy=policy, backup_keep=1)
        sp = layout.save_point(board, [board], cfg=cfg, action="build")
        board.write_text("built")
        res = layout.restore(board, cfg)
        assert board.read_text() == "board v1" and sp.path.is_file() and res.zip == sp.path
        assert layout.backups(board, cfg) == [sp.path]
