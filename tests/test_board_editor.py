# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Board tab: edit stripboard.toml in place, keep comments, and do not move parts."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_text import fp, pad, pcb
from stripforge.analyze import analyze
from stripforge.config import (
    ORIGIN_HELP,
    UNSAVED_BOARD_MESSAGE,
    apply_board_form_text,
    board_editor_labels,
    board_form_from_file,
    board_form_from_raw,
    board_form_is_dirty,
    load,
    save_board_form,
)

KEVIN_ORIGIN = ("127", "73.66")
OUTLINE = (125.73, 72.39, 189.23, 135.89)


def _form(raw: dict | None = None, **edits: str) -> dict[str, str]:
    form = board_form_from_raw(raw or {})
    form.update(edits)
    return form


def test_labels_say_origin_is_the_first_hole_not_the_outline_corner():
    labels = dict(board_editor_labels())
    assert "centre of hole A1" in labels["origin_x_mm"]
    assert "centre of hole A1" in labels["origin_y_mm"]
    assert "not the corner" in ORIGIN_HELP
    assert "does not move parts" in ORIGIN_HELP


def test_save_keeps_comments_unknown_keys_tables_and_move_policy(tmp_path):
    path = tmp_path / "stripboard.toml"
    original = (
        "# do not delete\n"
        "rows = 25\n"
        "cols = 25\n"
        "origin_mm = [1.27, 1.27] # first hole, not the outline\n"
        "pitch_mm = 2.54\n"
        'backup_policy = "move"\n'
        "backup_keep = 0\n"
        'workshop_note = "keep me"\n'
        "\n"
        "[bend]\n"
        "SW2 = 0.16\n"
    )
    path.write_text(original)
    form = _form(load_raw(path), origin_x_mm="127", origin_y_mm="73.66")
    save_board_form(path, form)
    text = path.read_text()
    assert "# do not delete" in text
    assert "# first hole, not the outline" in text
    assert 'workshop_note = "keep me"' in text
    assert "[bend]\nSW2 = 0.16\n" in text
    assert 'backup_policy = "move"' in text and "backup_keep = 0" in text
    assert 'workshop_note = "keep me"' in text  # left in place; from_dict still rejects unknown keys
    kept = [line for line in text.splitlines() if not line.startswith("workshop_note")]
    bare = tmp_path / "known.toml"
    bare.write_text("\n".join(kept) + "\n")
    cfg = load(bare)
    assert cfg.origin_mm == (127.0, 73.66) and cfg.backup_policy == "move" and cfg.rows == 25
    assert not (tmp_path / "stripboard.toml.tmp").exists()


def test_invalid_edit_does_not_replace_the_file(tmp_path):
    path = tmp_path / "stripboard.toml"
    path.write_text("rows = 25\ncols = 25\norigin_mm = [1.27, 1.27]\npitch_mm = 2.54\n")
    before = path.read_bytes()
    form = _form(load_raw(path), origin_x_mm="nope", origin_y_mm="73.66")
    with pytest.raises(ValueError, match="origin X"):
        save_board_form(path, form)
    assert path.read_bytes() == before
    form = _form(load_raw(path), origin_x_mm="127", origin_y_mm="")
    with pytest.raises(ValueError, match="both"):
        save_board_form(path, form)
    assert path.read_bytes() == before


def test_absent_defaults_are_not_written_until_they_change(tmp_path):
    path = tmp_path / "stripboard.toml"
    path.write_text("rows = 25\ncols = 25\n")
    form = board_form_from_file(path)
    assert form["pitch_mm"] == "2.54" and form["origin_x_mm"] == ""
    save_board_form(path, form)
    text = path.read_text()
    assert "pitch_mm" not in text and "origin_mm" not in text and "diagonal_links" not in text
    form["pitch_mm"] = "2.5"
    save_board_form(path, form)
    assert "pitch_mm = 2.5" in path.read_text()


def test_dirty_form_is_not_the_saved_one():
    saved = board_form_from_raw({"rows": 25, "cols": 25, "origin_mm": [1.27, 1.27]})
    assert not board_form_is_dirty(saved, dict(saved))
    edited = dict(saved)
    edited["origin_x_mm"] = "127"
    assert board_form_is_dirty(saved, edited)
    assert "nothing was written or built" in UNSAVED_BOARD_MESSAGE


def test_corrected_origin_snaps_pads_that_were_off_the_old_grid(tmp_path):
    """Same numbers as Kevin's moved 25x25 board: outline far from origin 1.27, pads on 127 / 73.66.

    One pad is 0.04 mm off its hole (a 2.50 mm spacing instead of 2.54). It stays inside snap
    tolerance. The editor only rewrites the config; the footprint coordinates are not moved.
    """
    ox, oy = 127.0, 73.66
    board = tmp_path / "in.kicad_pcb"
    board.write_text(
        pcb(
            fp("R1", f"{ox:.2f} {oy:.2f}", pad("1", "0 0", "+5V"), pad("2", "2.54 0", "GND")),
            fp("C1", f"{ox:.2f} {oy + 9 * 2.54 + 0.04:.2f}", pad("1", "0 0", "A"), pad("2", "0 2.50", "B")),
            outline=OUTLINE,
        ),
        encoding="utf-8",
    )
    toml = tmp_path / "stripboard.toml"
    toml.write_text('# keep\nrows = 25\ncols = 25\norigin_mm = [1.27, 1.27]\nbackup_policy = "move"\n')
    before = analyze(board, load(toml))
    assert len(before.off_board) == 2 and len(before.snapped) == 0
    saved_pads = Path(board).read_bytes()
    form = _form(load_raw(toml), origin_x_mm=KEVIN_ORIGIN[0], origin_y_mm=KEVIN_ORIGIN[1])
    save_board_form(toml, form)
    assert Path(board).read_bytes() == saved_pads
    after = analyze(board, load(toml))
    assert after.off_board == [] and {s.ref for s in after.snapped} == {"R1", "C1"}
    c1 = next(s for s in after.snaps if s.ref == "C1")
    assert c1.max_dev_nm == 40_000  # 0.04 mm, inside the 0.15 mm snap tolerance
    text = toml.read_text()
    assert 'backup_policy = "move"' in text and "# keep" in text


def test_apply_text_round_trip_does_not_touch_a_table():
    src = 'rows = 25\n\n[drc]\nignore = ["silk_overlap"]\n'
    form = _form({"rows": 25}, cols="25")
    out = apply_board_form_text(src, form)
    assert '[drc]\nignore = ["silk_overlap"]\n' in out
    assert "cols = 25" in out.split("[drc]", 1)[0]


def test_headless_dialog_saves_origin_and_refuses_a_dirty_action():
    wx = pytest.importorskip("wx")
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "stripforge_plugin_wx", Path(__file__).resolve().parents[1] / "plugins" / "stripforge_plugin.py"
    )
    sfp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sfp)
    app = wx.App(False)
    saved = {}

    def on_save_board(form):
        saved["form"] = dict(form)
        return Path("stripboard.toml"), dict(form), "Config: stripboard.toml"

    ran = []
    ui = sfp.Ui()
    assert ui.wx is not None
    form = board_form_from_raw({"rows": 25, "cols": 25, "origin_mm": [1.27, 1.27], "pitch_mm": 2.54})
    ui.show_hub(
        "StripForge: Dialog",
        "Config: stripboard.toml",
        "move",
        0,
        lambda *_a: ("snap", "backup saved"),
        lambda action: ran.append(action) or "ran",
        form,
        on_save_board,
        lambda: dict(form),
        show=False,
    )
    dlg = wx.GetTopLevelWindows()[-1]
    book = _find(dlg, lambda w: isinstance(w, wx.Notebook))
    assert book is not None and book.GetPageText(0) == "Board" and book.GetPageText(1) == "Backup"
    controls = dlg._board_controls
    controls["origin_x_mm"].SetValue("127")
    controls["origin_y_mm"].SetValue("73.66")
    _click(wx, _find(dlg, lambda w: isinstance(w, wx.Button) and w.GetLabel() == "Save board settings"))
    assert saved["form"]["origin_x_mm"] == "127" and saved["form"]["origin_y_mm"] == "73.66"
    controls["origin_y_mm"].SetValue("1")
    _click(wx, _find(dlg, lambda w: isinstance(w, wx.Button) and w.GetLabel() == "Analyze"))
    assert ran == []
    assert any("unsaved" in text.lower() for text in _texts(dlg))
    dlg.Destroy()
    app.Destroy()


def _click(wx, button) -> None:
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, button.GetId())
    event.SetEventObject(button)
    button.GetEventHandler().ProcessEvent(event)


def _find(window, pred):
    if pred(window):
        return window
    for child in window.GetChildren():
        found = _find(child, pred)
        if found is not None:
            return found
    return None


def _texts(window) -> list[str]:
    found = []
    if hasattr(window, "GetValue") and not isinstance(window, type(None)):
        try:
            found.append(str(window.GetValue()))
        except Exception:
            pass
    if hasattr(window, "GetLabel"):
        found.append(window.GetLabel())
    for child in window.GetChildren():
        found.extend(_texts(child))
    return found


def load_raw(path: Path) -> dict:
    from stripforge.config import raw_dict

    return raw_dict(path)
