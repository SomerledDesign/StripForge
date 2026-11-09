# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Dialog text size. No wx and no trackpad: the arithmetic and the preference file only."""

from __future__ import annotations

from stripforge.config import board_editor_labels, board_form_groups
from stripforge.dialog_text import (
    MAC_BODY_POINT_SIZE,
    SCALE_DEFAULT,
    SCALE_MAX,
    SCALE_MIN,
    baseline_point_size,
    clamp_text_scale,
    load_text_scale,
    point_size,
    save_text_scale,
    scale_from_magnify_delta,
    scale_from_zoom_factor,
    scale_percent,
    step_text_scale,
)


def test_default_body_is_the_system_font_and_not_the_old_10pt_request():
    """100% is the platform GUI font. On macOS that is never below 13pt body text."""
    assert baseline_point_size(10, "darwin") == MAC_BODY_POINT_SIZE == 13
    assert baseline_point_size(0, "darwin") == 13
    assert baseline_point_size(None, "darwin") == 13
    assert baseline_point_size(16, "darwin") == 16  # a larger system font is kept
    assert baseline_point_size(9, "win32") == 9
    assert baseline_point_size(0, "linux") == 11
    assert point_size(1.0, 13) == 13
    assert point_size(1.25, 13) == 16
    assert point_size(2.0, 13) == 26  # one multiply, not a second pass
    assert point_size(1.0, baseline_point_size(10, "darwin")) == 13


def test_board_form_groups_cover_every_field_once():
    labels = [key for key, _label in board_editor_labels()]
    grouped = [key for _title, keys in board_form_groups() for key in keys]
    assert sorted(grouped) == sorted(labels)
    assert len(grouped) == len(set(grouped))
    assert [title for title, _keys in board_form_groups()] == [
        "Origin",
        "Grid",
        "Copper and cuts",
        "Links",
        "Build",
        "Parts",
    ]


def test_steps_stay_inside_bounds_and_reset():
    assert step_text_scale(1.0, 1) == 1.25
    assert step_text_scale(1.0, -1) == SCALE_MIN
    assert step_text_scale(SCALE_MAX, 1) == SCALE_MAX
    assert step_text_scale(2.0, 0) == SCALE_DEFAULT
    assert clamp_text_scale("nope") == SCALE_DEFAULT
    assert clamp_text_scale(float("nan")) == SCALE_DEFAULT
    assert point_size(2.0) == 20
    assert scale_percent(1.5) == "150%"


def test_pinch_math_uses_a_delta_or_a_gesture_start_factor():
    # EVT_MAGNIFY: positive delta zooms in, and a nonsense delta does not move.
    assert scale_from_magnify_delta(1.0, 0.5) == 1.5
    assert scale_from_magnify_delta(1.0, 0.0) == 1.0
    assert scale_from_magnify_delta(1.0, float("nan")) == 1.0
    assert scale_from_magnify_delta(2.5, 1.0) == SCALE_MAX
    # EVT_GESTURE_ZOOM: factor is since the gesture began, from the scale at the start.
    assert scale_from_zoom_factor(2.0, 1.2) == 2.4
    assert scale_from_zoom_factor(2.0, 0.5) == 1.0
    assert scale_from_zoom_factor(2.0, 0.0) == 2.0


def test_preference_file_is_not_the_board_config(tmp_path):
    pref = tmp_path / "StripForge" / "dialog-text-scale"
    toml = tmp_path / "stripboard.toml"
    toml.write_text('# keep\nbackup_policy = "move"\nbackup_keep = 0\nrows = 25\norigin_mm = [1.27, 1.27]\n')
    before = toml.read_bytes()
    assert load_text_scale(pref) == 1.0
    assert save_text_scale(pref, 1.5) == 1.5
    assert load_text_scale(pref) == 1.5
    assert save_text_scale(pref, 99) == SCALE_MAX
    pref.write_text("not a number\n")
    assert load_text_scale(pref) == 1.0
    assert toml.read_bytes() == before
