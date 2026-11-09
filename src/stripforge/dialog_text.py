# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Text size for the StripForge dialog. This is not a board setting.

The number is kept outside ``stripboard.toml`` so a larger font cannot change the
hole grid, the origin, or the backup policy. 1.0 is the size the dialog was drawn at.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SCALE_MIN = 1.0
SCALE_MAX = 3.0
SCALE_STEP = 0.25
SCALE_DEFAULT = 1.0
# Kept so a caller that has no system font still has a defined size. The dialog
# does not use this as the Mac baseline: see baseline_point_size.
BASE_POINT_SIZE = 10
# Apple's Human Interface Guidelines use 13pt for body text. The dialog used to
# request 10pt. wx on macOS has also drawn a font built from a point size smaller
# than the same number on the system font (a 10pt request landing near 7.5pt,
# which is 10 * 72/96). 100% is the system GUI font, and on macOS it is never
# below this body size. A larger text-size step multiplies this baseline once.
MAC_BODY_POINT_SIZE = 13
FALLBACK_BODY_POINT_SIZE = 11


def clamp_text_scale(value: float) -> float:
    """A finite scale inside the dialog's bounds. Anything else becomes 1.0."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return SCALE_DEFAULT
    if number != number or number in (float("inf"), float("-inf")):  # NaN or infinity
        return SCALE_DEFAULT
    if number < SCALE_MIN:
        return SCALE_MIN
    if number > SCALE_MAX:
        return SCALE_MAX
    return number


def baseline_point_size(system_point_size: int | None, platform: str) -> int:
    """Point size of the dialog at 100%.

    ``system_point_size`` is ``GetPointSize()`` of ``SYS_DEFAULT_GUI_FONT``. A missing
    or unusable value does not fall back to the old 10pt request. On macOS the result
    is never below 13pt, so a report of 10pt (or of 0) cannot shrink the dialog.
    """
    try:
        reported = int(system_point_size)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        reported = 0
    if reported < 0:
        reported = 0
    if platform == "darwin":
        return max(reported, MAC_BODY_POINT_SIZE)
    return reported if reported > 0 else FALLBACK_BODY_POINT_SIZE


# The dialog Report. Multiline and read-only, wrapped on word boundaries. No horizontal
# scrollbar and no TE_DONTWRAP. TE_WORDWRAP is omitted by the caller when that wx build
# does not define it; multiline text then still wraps.
REPORT_STYLE_NAMES = ("TE_MULTILINE", "TE_READONLY", "TE_WORDWRAP")


def report_text_style(wx_module) -> int:
    """wx style for the dialog Report: word wrap, read-only, no sideways scroll."""
    style = 0
    for name in REPORT_STYLE_NAMES:
        flag = getattr(wx_module, name, None)
        if flag is None:
            if name == "TE_WORDWRAP":
                continue
            raise AttributeError(name)
        style |= flag
    return style


def point_size(scale: float, base: int = BASE_POINT_SIZE) -> int:
    """``base`` times the text-size scale, rounded. The scale is applied once."""
    try:
        base_n = int(base)
    except (TypeError, ValueError):
        base_n = BASE_POINT_SIZE
    if base_n < 1:
        base_n = BASE_POINT_SIZE
    return max(1, int(round(base_n * clamp_text_scale(scale))))


def step_text_scale(current: float, steps: int) -> float:
    """Move by ``steps`` notches of 0.25. Positive is larger. Zero steps resets to 1.0."""
    if steps == 0:
        return SCALE_DEFAULT
    current = clamp_text_scale(current)
    index = int(round((current - SCALE_MIN) / SCALE_STEP))
    return clamp_text_scale(SCALE_MIN + (index + steps) * SCALE_STEP)


def scale_from_magnify_delta(current: float, magnification: float) -> float:
    """wx ``EVT_MAGNIFY``: ``GetMagnification`` is a delta. Positive zooms in.

    A zero or non-numeric delta leaves the size alone.
    """
    current = clamp_text_scale(current)
    try:
        delta = float(magnification)
    except (TypeError, ValueError):
        return current
    if delta != delta or delta in (float("inf"), float("-inf")) or delta <= -1.0:
        return current
    return clamp_text_scale(current * (1.0 + delta))


def scale_from_zoom_factor(start: float, factor: float) -> float:
    """wx ``EVT_GESTURE_ZOOM``: ``GetZoomFactor`` is the factor since the gesture began.

    ``start`` is the scale when the gesture started, not the scale from the previous event.
    """
    start = clamp_text_scale(start)
    try:
        factor_f = float(factor)
    except (TypeError, ValueError):
        return start
    if factor_f != factor_f or factor_f in (float("inf"), float("-inf")) or factor_f <= 0.0:
        return start
    return clamp_text_scale(start * factor_f)


def scale_percent(scale: float) -> str:
    return f"{int(round(clamp_text_scale(scale) * 100))}%"


def default_dialog_text_scale_path() -> Path:
    """Per-user file. Not next to a board, and not inside ``stripboard.toml``."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "StripForge"
    elif sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", str(Path.home()))) / "StripForge"
    else:
        config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        base = Path(config_home) / "stripforge"
    return base / "dialog-text-scale"


def load_text_scale(path: str | Path) -> float:
    """Read a saved scale. A missing or unreadable file is 1.0."""
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return SCALE_DEFAULT
    return clamp_text_scale(float(text)) if _is_float(text) else SCALE_DEFAULT


def _is_float(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


def save_text_scale(path: str | Path, scale: float) -> float:
    """Write the clamped scale. Returns the value that was written."""
    path = Path(path)
    value = clamp_text_scale(scale)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{value:.2f}\n", encoding="utf-8")
    os.replace(tmp, path)
    return value
