# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""StripForge inside KiCad 10's PCB editor: the shared code behind the IPC plugin actions.

KiCad starts ``sf_<action>.py`` in this plugin's own virtual environment (``requirements.txt``;
created with ``--system-site-packages``, so KiCad's wxPython is there for dialogs) with
``KICAD_API_SOCKET`` / ``KICAD_API_TOKEN`` set. Each action:

1. connects to KiCad with kicad-python (``kipy``) and asks for the open board's file name;
2. offers to save the board first (StripForge works on the saved ``.kicad_pcb``; KiCad 10's API has
   no "modified" flag, so it always asks; "Build strips" in place requires it);
3. uses ``stripforge/stripboard.toml`` (or ``stripboard.toml`` in the project folder) if there is
   one, else derives the grid from Edge.Cuts; exports a fresh netlist from ``<name>.kicad_sch``
   with kicad-cli when it exists;
4. runs the same code as the ``stripforge`` command line and shows its report.

"Build strips" (``output = "in_place"``, the default) saves the open board, builds into that same
file (the project's own ``<name>.kicad_pcb``, so F8 and schematic parity keep working), and reloads
it in the editor (the API's RevertDocument). Every build first makes a save point, a new zip in
``stripforge/<Project>-stripforge-backups/``. Older zips are deleted only when the config's
``backup_policy`` is ``"retention"`` and ``backup_keep`` is 1 or more. A rebuild keeps your cuts and links
where you put them. "Restore last" steps back one save point at a time, reloading the board,
until the first save point (the board from before that work). The next click then stops.
``output = "separate"`` writes ``stripforge/<Project>-stripforge.kicad_pcb``, the open board left alone.
StripForge's other files (link lists, sheet, log) go to ``stripforge/`` too. "Run DRC" and
"Build sheet" work on the built board. Why not live edits
through the API: see Sketch.md §4.10.

The helpers at the top are pure (no kipy, no wx) so they can be tested without KiCad.
"""

from __future__ import annotations

import contextlib
import inspect
import io
import re
import subprocess
import sys
import tempfile
import time
import traceback
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
IDENTIFIER = "com.github.somerleddesign.stripforge"
BUILT_SUFFIX = "-stripforge"
BACKUP_SUFFIX = "-pre-stripbuild"  # 0.2.x backups (still recognised by base_stem)
OUTPUT_DIR = "stripforge"
CONFIG_NAME = "stripboard.toml"
TITLES = {
    "analyze": "StripForge: Analyze",
    "build": "StripForge: Build strips",
    "drc": "StripForge: Run DRC",
    "sheet": "StripForge: Build sheet",
    "links": "StripForge: Add links to schematic",
    "restore": "StripForge: Restore last",
    "dialog": "StripForge: Dialog",
}
# Actions the shared dialog can run. The matching toolbar buttons stay until this window
# is the one used for the whole workflow. Order is the build workflow, then undo.
DIALOG_ACTIONS = (
    ("analyze", "Analyze"),
    ("build", "Build strips"),
    ("links", "Add links"),
    ("drc", "Run DRC"),
    ("sheet", "Build sheet"),
    ("restore", "Restore last"),
)
REOPEN_NOTE = (
    "IMPORTANT: if the Schematic Editor is open, close it WITHOUT saving and open it again. It does "
    "not reload a schematic changed on disk, and saving the old copy would remove the links again."
)


def bootstrap() -> Path | None:
    """Make ``import stripforge`` work: the copy bundled next to this file, else the repo's ``src``."""
    for root in (HERE, HERE.parent / "src"):
        if (root / "stripforge" / "__init__.py").is_file():
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return root
    return None


# --- pure helpers -------------------------------------------------------------------------------


def board_path(project_dir: str, board_filename: str) -> Path | None:
    """The open board's path from the API's project directory and board file name (None: unsaved)."""
    if not board_filename:
        return None
    p = Path(board_filename)
    return p if p.is_absolute() else Path(project_dir) / p


def base_stem(board: Path) -> str:
    """``fixture`` for ``fixture.kicad_pcb``, ``fixture-stripforge.kicad_pcb`` and the backups."""
    s = re.sub(r"(?<=.)" + re.escape(BACKUP_SUFFIX) + r"-\d+$", "", board.stem)
    for suffix in (BUILT_SUFFIX, BACKUP_SUFFIX):
        if s.endswith(suffix) and len(s) > len(suffix):
            return s[: -len(suffix)]
    return s


def _out_file(board: Path, kind: str, config: Path | None = None) -> Path:
    """``<project>/stripforge/<Project>-stripforge.<kind>`` (stripforge.layout; the config's
    ``output_dir`` when given)."""
    cfg = None
    if config is not None:
        try:
            from stripforge.config import load

            cfg = load(config)
        except Exception:  # a broken config is reported by the action itself
            cfg = None
    try:
        from stripforge.layout import out_file
    except ImportError:  # before bootstrap(): the default layout, without a project lookup
        return board.parent / OUTPUT_DIR / f"{base_stem(board)}{BUILT_SUFFIX}.{kind}"
    return out_file(board, kind, cfg)


def built_path(board: Path, mode: str = "in_place", config: Path | None = None) -> Path:
    """Where "Build strips" writes: the board itself (``output = "in_place"``) or
    ``stripforge/<Project>-stripforge.kicad_pcb`` (``"separate"``)."""
    if mode == "in_place" or is_output_name(board):
        return board
    return _out_file(board, "kicad_pcb", config)


def backups_dir(board: Path, config: Path | None = None) -> Path:
    """``stripforge/<Project>-stripforge-backups``: the save points."""
    from stripforge.config import load
    from stripforge.layout import backup_dir

    return backup_dir(board, load(config) if config else None)


def output_mode(config: Path | None) -> str:
    """The config's ``output`` ("in_place" by default, or "separate")."""
    if config is None:
        return "in_place"
    try:
        from stripforge.config import load

        return load(config).output
    except Exception:  # a broken config is reported by the build itself
        return "in_place"


def is_output_name(board: Path) -> bool:
    return board.stem.endswith(BUILT_SUFFIX) and board.stem != BUILT_SUFFIX


def find_config(board: Path) -> Path | None:
    """``stripforge/stripboard.toml``, then ``stripboard.toml`` in the project folder (next to the
    board), or None (derive the grid from Edge.Cuts)."""
    for p in (board.parent / OUTPUT_DIR / CONFIG_NAME, board.parent / CONFIG_NAME):
        if p.is_file():
            return p
    try:
        from stripforge.layout import find_config as layout_find
    except ImportError:
        return None
    return layout_find(board)


def fallback_config(board: Path) -> tuple[Path | None, str | None]:
    """No ``stripboard.toml``: use the only other ``*.toml`` in ``stripforge/`` or next to the board
    if it is a valid StripForge config (e.g. ``X56.toml``), else point the other files out.
    Returns (config, note)."""
    others = sorted(board.parent.glob(f"{OUTPUT_DIR}/*.toml")) or sorted(board.parent.glob("*.toml"))
    if not others:
        return None, None
    if len(others) == 1:
        try:
            from stripforge.config import load

            load(others[0])
        except Exception as exc:  # not a StripForge config (or broken): say so, don't use it
            return None, (f"NOTE: {others[0].name} found but not used ({exc}); "
                          f"fix it or rename a StripForge config to {CONFIG_NAME}")  # fmt: skip
        return others[0], (f"NOTE: no {CONFIG_NAME}; using {others[0].name}, the only .toml next to the "
                           f"board (rename it to {CONFIG_NAME} to make this explicit)")  # fmt: skip
    names = ", ".join(p.name for p in others)
    return None, f"NOTE: {names} found; copy or rename one of them to {CONFIG_NAME} to use it"


def find_schematic(board: Path, project_name: str = "") -> Path | None:
    """The project's root schematic: ``<project>.kicad_sch`` or ``<name>.kicad_sch`` next to the board."""
    names = [n for n in (project_name, base_stem(board)) if n]
    for n in names:
        p = board.parent / f"{n}.kicad_sch"
        if p.is_file():
            return p
    return None


def has_strips(board: Path) -> bool:
    """Whether the board is a StripForge output (named ``*-stripforge`` or carrying cut markers)."""
    if is_output_name(board):
        return True
    try:
        return '"StripForge:CUT_' in board.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def check_target(board: Path) -> tuple[Path | None, str]:
    """The built board that DRC and the sheet should use, and a note (or why there is none)."""
    if has_strips(board):
        return board, ""
    out = built_path(board, "separate", find_config(board))
    legacy = board.with_name(base_stem(board) + BUILT_SUFFIX + ".kicad_pcb")  # 0.2.x: next to the board
    if not out.is_file() and legacy.is_file():
        out = legacy
    if out == board or not out.is_file():
        return None, (f'No built board yet: run "{TITLES["build"]}" first (it builds into {board.name}).')
    note = ""
    if out.stat().st_mtime < board.stat().st_mtime:
        note = f"NOTE: {out.name} is older than {board.name}; if you changed the board, build again."
    return out, note


def cli_args(action: str, board: Path, config: Path | None, netlist: Path | None = None,
             schematic: Path | None = None, kicad_cli: str | None = None,
             mode: str = "in_place") -> list[str]:  # fmt: skip
    """The ``stripforge`` command line an action runs (``board``: the file that command reads)."""
    cfg = ["--config", str(config)] if config else []
    net = ["--netlist", str(netlist)] if netlist else []
    if action == "analyze":
        return ["analyze", str(board), *net, *cfg]
    if action == "build":
        out = built_path(board, mode, config)
        in_place = ["--in-place"] if out == board else []
        return ["build", str(board), "-o", str(out), *in_place, *net, *cfg]
    if action == "drc":
        extra = ["--schematic", str(schematic)] if schematic else []
        extra += ["--kicad-cli", kicad_cli] if kicad_cli else []
        return ["drc", str(board), *cfg, *extra]
    if action == "sheet":
        return ["sheet", str(board), "-o", str(sheet_path(board)), *net, *cfg]
    raise ValueError(f"unknown action {action!r}")


def sheet_path(board: Path) -> Path:
    """``stripforge/<Project>-stripforge.sheet.html`` (for an in-place built board too)."""
    return _out_file(board, "sheet.html", find_config(board))


LOG_SUFFIX = "-stripforge.log"  # stripforge/<Project>-stripforge.log
NO_WX_NOTE = (
    "StripForge can't show dialogs: wxPython is not importable in the plugin's Python environment, "
    "so KiCad only shows its messages in the PCB editor's status-bar warnings. Fix: Preferences > "
    "Plugins > Python interpreter: pick a Python that has wxPython (on NixOS, one with wxpython in "
    "its packages), then 'Recreate Plugin Environment' and restart KiCad."
)


def log_path(board: Path | None) -> Path:
    """``stripforge/<Project>-stripforge.log``; without a board, ``stripforge.log`` in the temp dir."""
    if board is None:
        return Path(tempfile.gettempdir()) / "stripforge.log"
    return _out_file(board, "log")


def write_log(board: Path | None, title: str, text: str) -> Path | None:
    """Write the run report (overwriting the previous run's); falls back to the temp dir. Returns
    the path written, or None if nothing could be written."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    body = f"{title}  ({stamp})\n\n{text.rstrip()}\n"
    for p in dict.fromkeys([log_path(board), log_path(None)]):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            return p
        except OSError:
            continue
    return None


def outline_problem(board: Path, config: Path | None) -> str | None:
    """Why a build/analyze can't derive a grid (no Edge.Cuts and no origin_mm/rows/cols), or None."""
    try:
        from stripforge.board import load_board
        from stripforge.config import load

        cfg = load(config) if config else None
        if cfg is not None and cfg.origin_mm is not None and cfg.rows and cfg.cols:
            return None
        if load_board(board).outline is not None:
            return None
    except Exception:  # unreadable board or config: the CLI reports it in full
        return None
    return (
        f"{board.name} has no board outline on Edge.Cuts, so StripForge can't tell where the "
        "stripboard is (nothing was changed).\n\n"
        "Draw the board outline first: select the Edge.Cuts layer, draw a rectangle the size of your "
        "stripboard around the parts, save the board, and run this again.\n"
        f"Or set origin_mm, rows and cols in {OUTPUT_DIR}/{CONFIG_NAME}."
    )


def run_cli(argv: list[str]) -> tuple[int, str]:
    """Run ``stripforge <argv>`` in-process; return its exit code and everything it printed."""
    from stripforge import cli

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            code = cli.main(argv)
        except SystemExit as exc:  # argparse
            code = exc.code if isinstance(exc.code, int) else 2
    return int(code or 0), buf.getvalue()


def export_netlist(kicad_cli: str | None, schematic: Path, out: Path) -> str | None:
    """``kicad-cli sch export netlist --format kicadsexpr``; returns an error message or None."""
    if not kicad_cli:
        return "kicad-cli not found, so the netlist cross-check is skipped"
    proc = subprocess.run(
        [kicad_cli, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(schematic)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not out.is_file():
        return f"netlist export failed (exit {proc.returncode}): {(proc.stdout + proc.stderr).strip()[:400]}"
    return None


def _loaded_config(board: Path):
    """``(path or None, note or None, BoardConfig, raw toml dict)`` for the file Analyze would use."""
    bootstrap()
    from stripforge.config import BoardConfig, load, raw_dict

    path = find_config(board)
    note = None
    if path is None:
        path, note = fallback_config(board)
    if path is None:
        return None, note, BoardConfig(), {}
    return path, note, load(path), raw_dict(path)


def backup_settings_path(board: Path) -> Path:
    """The config file the dialog edits: the one Analyze reads, not a second file that would hide it.

    No config yet: ``stripforge/stripboard.toml`` (created on save). A lone ``X56.toml`` is edited
    in place so a new ``stripboard.toml`` does not take over and drop the rest of that file.
    """
    path = find_config(board)
    if path is not None:
        return path
    fallback, _note = fallback_config(board)
    if fallback is not None:
        return fallback
    return board.parent / OUTPUT_DIR / CONFIG_NAME


def apply_backup_policy(board: Path, policy: str, keep: int) -> Path:
    """Save the backup policy. Does not delete any backup; the next save point applies retention."""
    from stripforge.config import save_backup_settings

    path = backup_settings_path(board)
    save_backup_settings(path, policy, keep)
    return path


def _fmt(values) -> str:
    if isinstance(values, dict):
        return ", ".join(f"{k} = {v:g}" for k, v in values.items()) if values else "(none)"
    if isinstance(values, (list, tuple)):
        return ", ".join(str(v) for v in values) if values else "(none)"
    return str(values)


def dialog_snapshot(board: Path) -> str:
    """Read-only settings text, plus the backup policy the dialog can edit. No wx, no KiCad."""
    from stripforge.config import prune_keep

    try:
        path, note, cfg, raw = _loaded_config(board)
    except (OSError, ValueError) as exc:
        return f"Board: {board}\nConfig error: {exc}\nThe backup policy was not changed."
    lines = [f"Board: {board}"]
    if path is None:
        lines.append("Config: none; grid derived from Edge.Cuts")
        lines.append("backup_policy: move (default; no config file)")
    else:
        lines.append(f"Config: {path}")
        if note:
            lines.append(note)
        if "backup_policy" in raw:
            lines.append(f"backup_policy: {cfg.backup_policy}")
        else:
            lines.append("backup_policy: move (default; this file does not set backup_policy)")
    limit = prune_keep(cfg)
    if limit:
        lines.append(f"backup_keep: {cfg.backup_keep} (retention deletes older save points down to {limit})")
    elif cfg.backup_policy == "retention":
        lines.append(f"backup_keep: {cfg.backup_keep} (retention is on; 0 keeps every save point)")
    else:
        lines.append(
            f"backup_keep: {cfg.backup_keep} (not used while policy is move; save points are not deleted)"
        )
    if cfg.origin_mm is None:
        origin = "from the board outline"
    else:
        origin = f"{cfg.origin_mm[0]:g}, {cfg.origin_mm[1]:g}"
    lines.append(f"rows: {cfg.rows if cfg.rows else 'from the board outline'}")
    lines.append(f"cols: {cfg.cols if cfg.cols else 'from the board outline'}")
    lines.append(f"origin_mm: {origin}")
    lines.append(f"cut_style: {cfg.cut_style}")
    lines.append(f"diagonal_links: {str(cfg.diagonal_links).lower()}")
    lines.append(f"max_diagonal_shift: {cfg.max_diagonal_shift}")
    lines.append(f"slotted: {_fmt(cfg.slotted)}")
    lines.append(f"skip: {_fmt(cfg.skip)}")
    lines.append(f"bend: {_fmt(cfg.bend)}")
    lines.append(f"drc.ignore: {_fmt(cfg.drc.ignore)}")
    pairs = [f"{a}+{b}" for a, b in cfg.drc.allow_overlap]
    lines.append(f"drc.allow_overlap: {_fmt(pairs)}")
    lines.append("")
    lines.append(
        "This is the shared StripForge window. The separate toolbar buttons stay until this window "
        "covers the workflow; they are not a second product."
    )
    lines.append("Actions here run the same code as those buttons. Analyze is unchanged.")
    lines.append(
        "With wxPython, the Board tab writes these settings to stripboard.toml. Origin is the centre "
        "of hole A1, not the outline corner. The Backup tab writes the backup policy."
    )
    lines.append(
        "Move keeps every save point. Retention deletes older ones only when Keep is 1 or more, "
        "and only on a later build, sheet or link update: saving this setting deletes nothing."
    )
    return "\n".join(lines)


def reveal(path: Path) -> None:
    """Show ``path`` in Finder / Explorer / the file manager."""
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    elif sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


# --- KiCad and the user -------------------------------------------------------------------------


class Ui:
    """wx dialogs when wxPython is available (it is inside KiCad), else plain stdout."""

    def __init__(self) -> None:
        try:
            import wx
        except ImportError:
            self.wx = None
        else:
            self.wx = wx
            self.app = wx.App.Get() or wx.App(False)

    def ask_save(self, board: Path, required: bool = False) -> bool | None:
        """True: save first; False: use the file on disk; None: cancel. ``required``: building in
        place, so the only choices are save (then build and reload) or cancel."""
        if self.wx is None:
            # no dialog possible (M4d, issue #1): never write into the board silently
            if required:
                return None
            print(f"StripForge: no wxPython for a dialog, so the board is saved first: {board}")
            return True
        wx = self.wx
        if required:
            dlg = wx.MessageDialog(
                None,
                f"Build strips writes into this board file and then reloads it:\n{board}\n\n"
                "The board is saved first, and a save point (a zip of the files the build "
                f"writes) goes into {OUTPUT_DIR}/{base_stem(board)}{BUILT_SUFFIX}-backups.\n\n"
                f"To undo the build: {TITLES['restore']}.",
                "StripForge",
                wx.OK | wx.CANCEL | wx.ICON_QUESTION,
            )
            dlg.SetOKCancelLabels("Save and build", "Cancel")
            r = dlg.ShowModal()
            dlg.Destroy()
            return True if r == wx.ID_OK else None
        dlg = wx.MessageDialog(
            None,
            f"StripForge reads the saved board file:\n{board}\n\nSave the board in KiCad first?",
            "StripForge",
            wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION,
        )
        dlg.SetYesNoCancelLabels("Save and continue", "Use the saved file", "Cancel")
        r = dlg.ShowModal()
        dlg.Destroy()
        return {wx.ID_YES: True, wx.ID_NO: False}.get(r)

    def confirm_restore(self, board: Path, save: Path) -> bool:
        """Restore last: OK to replace the files? Never without a dialog (no wx: False)."""
        if self.wx is None:
            return False
        wx = self.wx
        dlg = wx.MessageDialog(
            None,
            f"Step back to this save point?\n{save.name}\n\n"
            f"{board.name} is reloaded as it was then (built strips, holes, tracks and link edits "
            "from later saves are removed). A safety copy of the current board is kept first. "
            "Repeat Restore last to walk back to the board from before the first build. "
            "Unsaved changes in the open board are lost.",
            "StripForge",
            wx.OK | wx.CANCEL | wx.ICON_QUESTION,
        )
        dlg.SetOKCancelLabels("Restore", "Cancel")
        r = dlg.ShowModal()
        dlg.Destroy()
        return r == wx.ID_OK

    def show_hub(
        self,
        title,
        snapshot,
        policy,
        keep,
        on_save,
        on_action,
        board_form,
        on_save_board,
        on_reload_board,
        show: bool = True,
        text_scale_path=None,
    ) -> None:
        """The shared window. ``on_save_board(form)`` returns ``(path, fresh form, snapshot)`` or raises.

        ``on_reload_board()`` returns the form last saved. Actions are refused while the board form
        is dirty, so a build cannot run on values that were typed but not saved.
        """
        from stripforge.config import (
            ORIGIN_HELP,
            UNSAVED_BOARD_MESSAGE,
            board_editor_labels,
            board_field_kind,
            board_form_is_dirty,
        )
        from stripforge.dialog_text import (
            default_dialog_text_scale_path,
            load_text_scale,
            point_size,
            save_text_scale,
            scale_from_magnify_delta,
            scale_from_zoom_factor,
            scale_percent,
            step_text_scale,
        )

        wx = self.wx
        scale_path = text_scale_path if text_scale_path is not None else default_dialog_text_scale_path()
        scale_state = {"value": load_text_scale(scale_path), "gesture_start": None}
        dlg = wx.Dialog(None, title=title, style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        dlg.SetFont(wx.Font(wx.FontInfo(point_size(scale_state["value"]))))
        root = wx.BoxSizer(wx.VERTICAL)
        book = wx.Notebook(dlg)
        board_page = wx.Panel(book)
        board_sizer = wx.BoxSizer(wx.VERTICAL)
        board_sizer.Add(wx.StaticText(board_page, label=ORIGIN_HELP), 0, wx.ALL, 8)
        board_sizer.Add(wx.StaticText(board_page, label=snapshot.split("\n", 1)[0]), 0, wx.LEFT | wx.RIGHT, 8)
        scrolled = wx.ScrolledWindow(board_page, style=wx.VSCROLL)
        scrolled.SetScrollRate(0, 16)
        grid = wx.FlexGridSizer(cols=2, vgap=4, hgap=8)
        grid.AddGrowableCol(1, 1)
        controls = {}
        choices = {"cut_style": ["auto", "hole", "knife"], "output": ["in_place", "separate"]}
        for key, label in board_editor_labels():
            grid.Add(wx.StaticText(scrolled, label=label), 0, wx.ALIGN_CENTER_VERTICAL)
            kind = "origin" if key.startswith("origin_") else board_field_kind(key)
            value = board_form.get(key, "")
            if kind == "bool":
                ctrl = wx.CheckBox(scrolled)
                ctrl.SetValue(value == "true")
            elif kind == "choice":
                ctrl = wx.Choice(scrolled, choices=choices[key])
                if value in choices[key]:
                    ctrl.SetStringSelection(value)
                else:
                    ctrl.SetSelection(0)
            else:
                ctrl = wx.TextCtrl(scrolled, value=value)
            controls[key] = ctrl
            grid.Add(ctrl, 1, wx.EXPAND)
        scrolled.SetSizer(grid)
        scrolled.SetVirtualSize(grid.CalcMin())
        board_sizer.Add(scrolled, 1, wx.EXPAND | wx.ALL, 8)
        dirty_label = wx.StaticText(board_page, label="Matches the saved stripboard.toml")
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        save_board = wx.Button(board_page, label="Save board settings")
        reload_board = wx.Button(board_page, label="Reload")
        buttons.Add(save_board, 0, wx.RIGHT, 8)
        buttons.Add(reload_board, 0, wx.RIGHT, 8)
        buttons.Add(dirty_label, 0, wx.ALIGN_CENTER_VERTICAL)
        board_sizer.Add(buttons, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        board_page.SetSizer(board_sizer)
        book.AddPage(board_page, "Board")

        backup_page = wx.Panel(book)
        backup_sizer = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(backup_page, label="Backup policy"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        move = wx.RadioButton(backup_page, label="Move (keep every save point)", style=wx.RB_GROUP)
        retention = wx.RadioButton(backup_page, label="Retention (delete older save points)")
        (retention if policy == "retention" else move).SetValue(True)
        row.Add(move, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        row.Add(retention, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 12)
        row.Add(wx.StaticText(backup_page, label="Keep"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        keep_ctrl = wx.SpinCtrl(backup_page, min=0, max=999, initial=max(0, int(keep)))
        keep_ctrl.Enable(policy == "retention")
        row.Add(keep_ctrl, 0, wx.RIGHT, 8)
        save_btn = wx.Button(backup_page, label="Save backup policy")
        row.Add(save_btn, 0)
        backup_sizer.Add(row, 0, wx.ALL, 8)
        backup_sizer.Add(
            wx.StaticText(
                backup_page,
                label="Saving does not delete anything. Retention deletes older save points on the next "
                "build, sheet or link update, and only when Keep is 1 or more. The default is move.",
            ),
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            8,
        )
        backup_page.SetSizer(backup_sizer)
        book.AddPage(backup_page, "Backup")

        text_bar = wx.WrapSizer(wx.HORIZONTAL) if hasattr(wx, "WrapSizer") else wx.BoxSizer(wx.HORIZONTAL)
        larger = wx.Button(dlg, label="Larger text")
        smaller = wx.Button(dlg, label="Smaller text")
        reset_text = wx.Button(dlg, label="Reset text size")
        size_label = wx.StaticText(dlg, label=f"Text size: {scale_percent(scale_state['value'])}")
        text_hint = wx.StaticText(
            dlg,
            label="Command-plus, Command-minus, and Command-0 on a Mac. Ctrl with those keys on "
            "Windows and Linux. A trackpad pinch is used only when wx sends a magnify gesture.",
        )
        text_bar.Add(larger, 0, wx.RIGHT | wx.BOTTOM, 6)
        text_bar.Add(smaller, 0, wx.RIGHT | wx.BOTTOM, 6)
        text_bar.Add(reset_text, 0, wx.RIGHT | wx.BOTTOM, 6)
        text_bar.Add(size_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT | wx.BOTTOM, 8)
        text_bar.Add(text_hint, 0, wx.ALIGN_CENTER_VERTICAL | wx.BOTTOM, 6)
        root.Add(text_bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        root.Add(book, 2, wx.EXPAND | wx.ALL, 8)

        def _toggle(_evt):
            keep_ctrl.Enable(retention.GetValue())

        move.Bind(wx.EVT_RADIOBUTTON, _toggle)
        retention.Bind(wx.EVT_RADIOBUTTON, _toggle)

        baseline = [dict(board_form)]

        def current_form() -> dict:
            form = {}
            for key, _label in board_editor_labels():
                ctrl = controls[key]
                kind = "origin" if key.startswith("origin_") else board_field_kind(key)
                if kind == "bool":
                    form[key] = "true" if ctrl.GetValue() else "false"
                elif kind == "choice":
                    form[key] = ctrl.GetStringSelection()
                else:
                    form[key] = ctrl.GetValue()
            return form

        def is_dirty() -> bool:
            return board_form_is_dirty(baseline[0], current_form())

        def refresh_dirty(_evt=None) -> None:
            dirty_label.SetLabel(
                "Unsaved board settings" if is_dirty() else "Matches the saved stripboard.toml"
            )

        for key, ctrl in controls.items():
            kind = "origin" if key.startswith("origin_") else board_field_kind(key)
            if kind == "bool":
                ctrl.Bind(wx.EVT_CHECKBOX, refresh_dirty)
            elif kind == "choice":
                ctrl.Bind(wx.EVT_CHOICE, refresh_dirty)
            else:
                ctrl.Bind(wx.EVT_TEXT, refresh_dirty)

        def apply_form(form: dict) -> None:
            for key, _label in board_editor_labels():
                ctrl = controls[key]
                kind = "origin" if key.startswith("origin_") else board_field_kind(key)
                value = form.get(key, "")
                if kind == "bool":
                    ctrl.SetValue(value == "true")
                elif kind == "choice":
                    if value in choices[key]:
                        ctrl.SetStringSelection(value)
                else:
                    ctrl.ChangeValue(value)
            baseline[0] = dict(form)
            refresh_dirty()

        report = wx.TextCtrl(dlg, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        report.SetFont(wx.Font(wx.FontInfo(point_size(scale_state["value"])).Family(wx.FONTFAMILY_TELETYPE)))

        def _save_board(_evt):
            try:
                path, fresh, snap = on_save_board(current_form())
            except (OSError, ValueError) as exc:
                report.SetValue(f"Board settings were not saved. The file is unchanged.\n\n{exc}")
                return
            apply_form(fresh)
            report.SetValue(f"Saved board settings in {path}.\nParts were not moved.\n\n{snap}")

        def _reload(_evt):
            try:
                fresh = on_reload_board()
            except (OSError, ValueError) as exc:
                report.SetValue(f"Could not reload stripboard.toml.\n\n{exc}")
                return
            apply_form(fresh)
            report.SetValue("Reloaded stripboard.toml. Unsaved edits were discarded.")

        save_board.Bind(wx.EVT_BUTTON, _save_board)
        reload_board.Bind(wx.EVT_BUTTON, _reload)

        def _save(_evt):
            if is_dirty():
                report.SetValue(UNSAVED_BOARD_MESSAGE)
                return
            which = "retention" if retention.GetValue() else "move"
            try:
                _snap, status = on_save(which, int(keep_ctrl.GetValue()))
            except (OSError, ValueError) as exc:
                report.SetValue(f"Backup policy was not saved.\n\n{exc}")
                return
            report.SetValue(status)

        save_btn.Bind(wx.EVT_BUTTON, _save)

        actions = wx.WrapSizer(wx.HORIZONTAL) if hasattr(wx, "WrapSizer") else wx.BoxSizer(wx.HORIZONTAL)
        for action, label in DIALOG_ACTIONS:
            button = wx.Button(dlg, label=label)

            def _run(_evt, action=action):
                if is_dirty():
                    report.SetValue(UNSAVED_BOARD_MESSAGE)
                    return
                report.SetValue(on_action(action))

            button.Bind(wx.EVT_BUTTON, _run)
            actions.Add(button, 0, wx.RIGHT, 6)
        root.Add(actions, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)
        root.Add(report, 3, wx.EXPAND | wx.ALL, 8)

        bottom = wx.BoxSizer(wx.HORIZONTAL)
        copy = wx.Button(dlg, label="Copy report")

        def _copy(_evt):
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(report.GetValue()))
                wx.TheClipboard.Close()

        copy.Bind(wx.EVT_BUTTON, _copy)
        bottom.Add(copy, 0, wx.RIGHT, 8)
        close = wx.Button(dlg, wx.ID_CLOSE, "Close")
        close.Bind(wx.EVT_BUTTON, lambda _evt: dlg.EndModal(wx.ID_CLOSE))
        bottom.Add(close, 0)
        root.Add(bottom, 0, wx.ALIGN_RIGHT | wx.ALL, 8)

        def apply_text_scale(scale: float, *, persist: bool) -> None:
            """Resize fonts and the layout. Field values, radios, and focus stay put."""
            scale_state["value"] = scale
            if persist:
                save_text_scale(scale_path, scale)
            size_label.SetLabel(f"Text size: {scale_percent(scale)}")
            focused = dlg.FindFocus()
            form_now = current_form()
            saved_baseline = dict(baseline[0])
            policy_now = "retention" if retention.GetValue() else "move"
            keep_now = int(keep_ctrl.GetValue())
            report_now = report.GetValue()
            point = point_size(scale)
            body = wx.Font(wx.FontInfo(point))
            mono = wx.Font(wx.FontInfo(point).Family(wx.FONTFAMILY_TELETYPE))

            def walk(win) -> None:
                win.SetFont(mono if win is report else body)
                for child in win.GetChildren():
                    walk(child)

            walk(dlg)
            # apply_form writes the baseline; put the real saved baseline back so an unsaved
            # origin is still unsaved after the fonts change.
            apply_form(form_now)
            baseline[0] = saved_baseline
            refresh_dirty()
            if policy_now == "retention":
                retention.SetValue(True)
            else:
                move.SetValue(True)
            keep_ctrl.SetValue(keep_now)
            keep_ctrl.Enable(policy_now == "retention")
            report.ChangeValue(report_now)
            dlg.Layout()
            scrolled.SetVirtualSize(grid.CalcMin())
            try:
                display = wx.DisplaySize()
                limit_w, limit_h = max(640, display[0] - 80), max(480, display[1] - 80)
            except Exception:
                limit_w, limit_h = 1400, 1000
            best = dlg.GetBestSize()
            cur = dlg.GetSize()
            dlg.SetSize(
                min(limit_w, max(cur.width, best.width)),
                min(limit_h, max(cur.height, best.height)),
            )
            if focused is not None:
                focused.SetFocus()

        def enlarge(_evt=None) -> None:
            apply_text_scale(step_text_scale(scale_state["value"], 1), persist=True)

        def shrink(_evt=None) -> None:
            apply_text_scale(step_text_scale(scale_state["value"], -1), persist=True)

        def reset_size(_evt=None) -> None:
            apply_text_scale(step_text_scale(scale_state["value"], 0), persist=True)

        larger.Bind(wx.EVT_BUTTON, enlarge)
        smaller.Bind(wx.EVT_BUTTON, shrink)
        reset_text.Bind(wx.EVT_BUTTON, reset_size)

        def on_char_hook(evt) -> None:
            if not (evt.CmdDown() or evt.ControlDown()):
                evt.Skip()
                return
            code = evt.GetKeyCode()
            plus = {ord("="), ord("+")}
            minus = {ord("-"), ord("_")}
            zero = {ord("0")}
            if hasattr(wx, "WXK_NUMPAD_ADD"):
                plus.add(wx.WXK_NUMPAD_ADD)
            if hasattr(wx, "WXK_NUMPAD_SUBTRACT"):
                minus.add(wx.WXK_NUMPAD_SUBTRACT)
            if hasattr(wx, "WXK_NUMPAD0"):
                zero.add(wx.WXK_NUMPAD0)
            if code in plus:
                enlarge()
            elif code in minus:
                shrink()
            elif code in zero:
                reset_size()
            else:
                evt.Skip()

        dlg.Bind(wx.EVT_CHAR_HOOK, on_char_hook)
        # macOS trackpad pinch arrives as wxEVT_MAGNIFY (a delta) and, on some wx builds,
        # wxEVT_GESTURE_ZOOM (a factor since the gesture started). If neither binder exists,
        # the buttons and shortcuts still work.
        if hasattr(wx, "EVT_MAGNIFY"):

            def on_magnify(evt) -> None:
                delta = evt.GetMagnification() if hasattr(evt, "GetMagnification") else 0.0
                apply_text_scale(scale_from_magnify_delta(scale_state["value"], delta), persist=True)

            dlg.Bind(wx.EVT_MAGNIFY, on_magnify)
        if hasattr(wx, "EVT_GESTURE_ZOOM"):

            def on_zoom(evt) -> None:
                if getattr(evt, "IsGestureStart", lambda: False)():
                    scale_state["gesture_start"] = scale_state["value"]
                start = scale_state["gesture_start"]
                if start is None:
                    start = scale_state["value"]
                factor = evt.GetZoomFactor() if hasattr(evt, "GetZoomFactor") else 1.0
                apply_text_scale(scale_from_zoom_factor(start, factor), persist=False)
                if getattr(evt, "IsGestureEnd", lambda: False)():
                    scale_state["gesture_start"] = None
                    save_text_scale(scale_path, scale_state["value"])

            dlg.Bind(wx.EVT_GESTURE_ZOOM, on_zoom)

        dlg.SetSizer(root)
        dlg._board_controls = controls  # tests set values without a click on each field
        dlg._text_scale = scale_state
        apply_text_scale(scale_state["value"], persist=False)
        dlg.CentreOnScreen()
        if not show:
            return
        dlg.ShowModal()
        dlg.Destroy()

    def report(self, title: str, text: str, path: Path | None = None, log: Path | None = None) -> None:
        if self.wx is None:
            print(f"{title}: no wxPython, so no dialog; the report is in {log}"
                  if log else f"{title}\n{text}")  # fmt: skip
            return
        wx = self.wx
        dlg = wx.Dialog(None, title=title, size=(900, 640), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        box = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(dlg, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.HSCROLL)
        txt.SetFont(wx.Font(wx.FontInfo(11).Family(wx.FONTFAMILY_TELETYPE)))
        box.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        if path is not None:
            show = wx.Button(dlg, label="Show in Finder" if sys.platform == "darwin" else "Show file")
            show.Bind(wx.EVT_BUTTON, lambda _e: reveal(path))
            buttons.Add(show, 0, wx.RIGHT, 8)
        copy = wx.Button(dlg, label="Copy report")

        def _copy(_e):
            if wx.TheClipboard.Open():
                wx.TheClipboard.SetData(wx.TextDataObject(text))
                wx.TheClipboard.Close()

        copy.Bind(wx.EVT_BUTTON, _copy)
        buttons.Add(copy, 0, wx.RIGHT, 8)
        buttons.Add(wx.Button(dlg, wx.ID_OK, "Close"), 0)
        box.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizer(box)
        dlg.CentreOnScreen()
        dlg.ShowModal()
        dlg.Destroy()


BUSY_WAIT_S = 10.0  # how long to keep retrying while KiCad says it is busy
BUSY_NOTE = (
    "KiCad is busy and can't take requests from StripForge right now ({what}).\n\n"
    "Press Esc (maybe twice) to end any active tool (routing, moving, a dialog), save the board "
    "with Cmd+S (Ctrl+S), and click {title} again."
)


class KiCadBusy(RuntimeError):
    """KiCad kept answering 'busy' (an interactive tool or a dialog is active)."""

    def __init__(self, what: str) -> None:
        super().__init__(f"KiCad is busy ({what})")
        self.what = what


def is_busy(exc: BaseException) -> bool:
    """kipy's ApiError for AS_BUSY: 'KiCad is busy and cannot respond to API requests right now'."""
    code = getattr(exc, "code", None)
    return "AS_BUSY" in str(code) or "busy" in str(exc).lower()


def busy_retry(call, what: str, wait_s: float | None = None, sleep=None):
    """``call()``, retried with a short backoff (0.25 s doubling to 2 s) for up to ``wait_s``
    seconds while KiCad is busy; then :class:`KiCadBusy`. Any other error is raised at once."""
    wait_s = BUSY_WAIT_S if wait_s is None else wait_s
    sleep = sleep or time.sleep
    delay, waited = 0.25, 0.0
    while True:
        try:
            return call()
        except KiCadBusy:
            raise
        except Exception as exc:
            if not is_busy(exc):
                raise
            if waited >= wait_s:
                raise KiCadBusy(what) from exc
            sleep(delay)
            waited += delay
            delay = min(delay * 2, 2.0)


def connect():
    """The KiCad connection and the open board (raises with a readable message)."""
    try:
        from kipy import KiCad
    except ImportError as exc:
        raise RuntimeError(
            "kicad-python (kipy) is not installed in this plugin's environment. In KiCad open "
            "Preferences > Plugins and use 'Recreate Plugin Environment', then try again."
        ) from exc
    kicad = KiCad(timeout_ms=15000)
    try:
        board = busy_retry(kicad.get_board, "getting the open board")
    except KiCadBusy:
        raise
    except Exception as exc:  # kipy raises ApiError when no board is open
        raise RuntimeError(f"No board is open in the PCB editor ({exc})") from exc
    return kicad, board


def _kicad_cli(kicad) -> str | None:
    from stripforge.drc import find_kicad_cli

    try:
        p = busy_retry(lambda: kicad.get_kicad_binary_path("kicad-cli"), "finding kicad-cli", 2.0)
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    return find_kicad_cli()


def run(action: str) -> int:
    """Entry point for ``sf_<action>.py``."""
    title = TITLES[action]
    ui = Ui()
    state: dict = {"board": None}
    raw_report = ui.report

    def report(title: str, text: str, path: Path | None = None) -> None:
        first = next((ln for ln in text.splitlines() if ln.startswith("Result:")), text.split("\n", 1)[0])
        if ui.wx is None:
            text = f"{NO_WX_NOTE}\n\n{text}"
        log = write_log(state["board"], title, text)
        if log is not None:
            text = f"{text.rstrip()}\n\nLog: {log}"
        print(f"{title}: {first} (full report: {log})", file=sys.stderr)
        if "log" in inspect.signature(raw_report).parameters:
            raw_report(title, text, path, log=log)
        else:  # a replaced report() without the log argument (tests)
            raw_report(title, text, path)

    ui.report = report  # type: ignore[method-assign]
    try:
        if bootstrap() is None:
            raise RuntimeError(f"the stripforge package is missing next to {HERE}")
        kicad, board_doc = connect()
        project = busy_retry(board_doc.get_project, "reading the project")
        board = board_path(project.path, board_doc.name)
        state["board"] = board
        if board is None:
            ui.report(title, "Save the board to a .kicad_pcb file first, then run the action again.")
            return 0
        if action == "dialog":
            return _open_dialog(ui, kicad, board_doc, board, project.name)
        code, text, shown = _perform(ui, action, kicad, board_doc, board, project.name)
        if text is not None:
            ui.report(TITLES[action], text, shown)
        return code
    except KiCadBusy as exc:  # nothing was built: a plain message, not a traceback
        print(f"StripForge: {exc}", file=sys.stderr)
        ui.report(title, BUSY_NOTE.format(what=exc.what, title=title))
        return 1
    except Exception as exc:  # show every failure to the user instead of a silent status-bar line
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        ui.report(title, f"StripForge failed: {exc}\n\n{detail}")
        return 1


RELOAD_HINT = "[reload]"


def _perform(ui, action, kicad, board_doc, board, project_name):
    """Run one existing action. Returns ``(code, report or None, path or None)``.

    A None report means the user cancelled. The toolbar and the shared dialog both call this.
    """
    if action == "restore":
        return _restore(ui, board, board_doc)
    in_place = False
    if action == "build" and bootstrap():
        config = find_config(board) or fallback_config(board)[0]
        in_place = built_path(board, output_mode(config)) == board
    if action in ("build", "analyze") and board.is_file() and bootstrap():
        config = find_config(board) or fallback_config(board)[0]
        problem = outline_problem(board, config)
        if problem:  # before saving: a board with no outline can't be built (issue #1)
            text = f"Board: {board}\nResult: refused (nothing was saved or built)\n\n{problem}"
            return 2, text, None
    choice = ui.ask_save(board, required=in_place)
    if choice is None:
        if ui.wx is None:  # no dialog to confirm an in-place build: refuse, loudly
            text = (
                f"Board: {board}\nResult: refused (nothing was saved or built)\n\n"
                "Build strips writes into the open board file and needs you to confirm "
                "that in a dialog, which can't be shown without wxPython."
            )
            return 2, text, None
        return 0, None, None
    if choice:
        busy_retry(board_doc.save, "saving the board")
    if not board.is_file():
        return 0, f"Board file not found: {board}\nSave the board first.", None
    before = board.stat().st_mtime_ns
    text, shown = _run_action(action, board, project_name, _kicad_cli(kicad))
    if in_place and board.stat().st_mtime_ns != before:
        try:  # reload the rewritten file into the editor (RevertDocument)
            busy_retry(board_doc.revert, "reloading the built board")
            text = text.replace(RELOAD_HINT, "Reloaded the built board in the PCB editor.")
        except Exception as exc:
            why = "KiCad was busy" if isinstance(exc, KiCadBusy) else str(exc)
            text = text.replace(
                RELOAD_HINT,
                f"Could not reload it ({why}): use File > Revert now, and don't save the board "
                "before that (saving would overwrite the build with the old board).",
            )
    return 0, text, shown


def _open_dialog(ui, kicad, board_doc, board, project_name) -> int:
    """Shared window. Opening it does not save the board or change the backup policy."""
    if not board.is_file():
        ui.report(TITLES["dialog"], f"Board file not found: {board}\nSave the board first.")
        return 0
    try:
        _path, _note, cfg, raw = _loaded_config(board)
    except (OSError, ValueError):
        cfg, raw = None, {}
    from stripforge.config import board_form_from_raw

    board_form = board_form_from_raw(raw if cfg is not None else {})
    snapshot = dialog_snapshot(board)
    if ui.wx is None:
        ui.report(
            TITLES["dialog"],
            snapshot + "\n\nResult: no dialog (wxPython is missing)\n\n"
            "The toolbar buttons still run each action. The backup policy was not changed.",
        )
        return 0

    def on_save(policy, keep):
        path = apply_backup_policy(board, policy, keep)
        status = (
            f"Saved backup policy {policy!r}, backup_keep {keep}, in {path}.\n"
            "Nothing was deleted. Retention deletes older save points on the next build, "
            "sheet or link update, and only when Keep is 1 or more."
        )
        return dialog_snapshot(board), status

    def on_save_board(form):
        from stripforge.config import board_form_from_file, save_board_form

        path = backup_settings_path(board)
        save_board_form(path, form)
        return path, board_form_from_file(path), dialog_snapshot(board)

    def on_reload_board():
        from stripforge.config import board_form_from_file

        path = backup_settings_path(board)
        return board_form_from_file(path) if path.is_file() else board_form_from_file(path)

    def on_action(action):
        _code, text, _shown = _perform(ui, action, kicad, board_doc, board, project_name)
        if text is None:
            text = "Cancelled. Nothing was changed."
        log = write_log(board, TITLES[action], text)
        if log is not None:
            text = f"{text.rstrip()}\n\nLog: {log}"
        return text

    ui.show_hub(
        TITLES["dialog"],
        snapshot,
        cfg.backup_policy if cfg is not None else "move",
        cfg.backup_keep if cfg is not None else 0,
        on_save,
        on_action,
        board_form,
        on_save_board,
        on_reload_board,
    )
    return 0


def _run_action(
    action: str, board: Path, project_name: str, kicad_cli: str | None
) -> tuple[str, Path | None]:
    config = find_config(board)
    fallback_note = None
    if config is None:
        config, fallback_note = fallback_config(board)
    notes = [f"Board: {board}", f"Config: {config or 'none; grid derived from Edge.Cuts'}"]
    if fallback_note:
        notes.append(fallback_note)
    mode = output_mode(config)
    out_board = built_path(board, mode, config)
    in_place = action == "build" and out_board == board
    rebuild = action == "build" and has_strips(board)
    if rebuild:
        notes.append(
            f"{board.name} is already built: rebuilding it in place, keeping the cut markers and "
            "W links where you put them"
        )
    if action == "links":
        return _add_links(board, project_name, config, notes)
    target = board
    if action in ("drc", "sheet"):
        target, note = check_target(board)
        if target is None:
            return "\n".join([*notes, "", note]), None
        if target != board:
            notes.append(f"Built board: {target}")
        if note:
            notes.append(note)
    schematic = find_schematic(board, project_name)
    with tempfile.TemporaryDirectory(prefix="stripforge-") as tmp:
        netlist = None
        if schematic and action != "drc":
            netlist = Path(tmp) / f"{base_stem(board)}.net"
            err = export_netlist(kicad_cli, schematic, netlist)
            if err:
                notes.append(f"NOTE: {err}")
                netlist = None
            else:
                notes.append(f"Netlist: exported from {schematic.name}")
        elif action != "drc":
            notes.append("Netlist: no schematic next to the board, so pad nets are not cross-checked")
        argv = cli_args(
            action, target, config, netlist, schematic if action == "drc" else None, kicad_cli, mode
        )
        code, out = run_cli(argv)
    shown: Path | None = None
    if action == "build" and code in (0, 1) and out_board.is_file():  # not a stale file on refusal
        shown = out_board
        if in_place:
            notes.append(f"Built {shown.name} in place. {RELOAD_HINT}")
            # the CLI's backup line (path, rotation, how to undo) goes up here, once
            lines = out.splitlines()
            notes += [line for line in lines if line.startswith("Backup: ")]
            out = "\n".join(line for line in lines if not line.startswith("Backup: "))
        else:
            notes.append(f"Wrote {shown} (output = \"separate\"); open it in KiCad to "
                         "see the strips. The open board was not changed.")  # fmt: skip
    if action == "sheet" and sheet_path(target).is_file():
        shown = sheet_path(target)
        webbrowser.open(shown.as_uri())
    status = {0: "OK", 1: "finished with problems (see the report)", 2: "refused (see the report)",
              3: "skipped: kicad-cli not found"}.get(code, f"exit {code}")  # fmt: skip
    return "\n".join([*notes, f"Result: {status}", "", out.rstrip()]), shown


def _add_links(board: Path, project_name: str, config: Path | None, notes: list) -> tuple[str, Path | None]:
    """ "Add links to schematic": write a StripForge:Link symbol for every placed W footprint into the
    project's schematic, in place (a save point of the changed sheets first; "Restore last" undoes it)."""
    from stripforge.linksym import LinkSymbolError, add_link_symbols, board_links, format_text

    schematic = find_schematic(board, project_name)
    if schematic is None:
        missing = f"No {project_name or base_stem(board)}.kicad_sch next to the board."
        return "\n".join([*notes, "", missing]), None
    notes.append(f"Schematic: {schematic}")
    links, _fps, _warns = board_links(board)
    if not links:
        hint = (
            f'No W link footprints on the board yet: run "{TITLES["build"]}" first '
            "(place_links = true, the default, places them on their holes)."
        )
        return "\n".join([*notes, "", hint]), None
    cfg = None
    if config is not None:
        try:
            from stripforge.config import load

            cfg = load(config)
        except Exception:
            pass
    try:
        res = add_link_symbols(schematic, board, in_place=True, cfg=cfg)
    except (LinkSymbolError, OSError, ValueError) as exc:
        return "\n".join([*notes, "Result: refused (nothing written)", "", str(exc)]), None
    if res.added or res.completed or res.removed:
        if res.removed:
            notes.append(
                f"Removed {len(res.removed)} stale StripForge:Link symbol(s), no longer on the board or in "
                f"the link plan: {', '.join(res.removed)} (so F8 won't bring them back)."
            )
        if res.added:
            sheets = ", ".join(sorted({a.sheet for a in res.added}))
            notes.append(
                f"Added {len(res.added)} StripForge:Link symbol(s) to {sheets}, each with its Footprint "
                "field, the schematic path of its placed W footprint, and net labels."
            )
        if res.completed:
            notes.append(
                f"Completed {len(res.completed)} W symbol(s) that were already in the schematic (kept where "
                f"they are, not duplicated): {', '.join(res.completed)}. Bare pins got net labels, and each "
                "symbol was re-linked to its placed footprint where needed (details below)."
            )
        notes += [f"Backup: {b} (save point: the sheets just before this; {TITLES['restore']} undoes it)"
                  for b in res.backups]  # fmt: skip
        notes.append(REOPEN_NOTE)
        notes.append(
            "Then F8 (Update PCB from Schematic) finds every W footprint already placed and matched "
            "by path: nothing is moved or duplicated."
        )
        status = "OK"
    else:
        notes.append("Nothing to add: every W link is already in the schematic, labelled and linked.")
        status = "OK (no change)"
    if res.conflicts:
        notes += [f"CONFLICT: {c}" for c in res.conflicts]
        status = "finished with conflicts (see the report)"
    return "\n".join([*notes, f"Result: {status}", "", format_text(res)]), schematic


def _restore(ui, board: Path, board_doc):
    """Restore last. Returns ``(code, report or None, path)``. The caller shows the report.

    Each confirmed call loads the next earlier save point. At the first one, a further call
    reports that and does not change the board. The dialog and the toolbar button both use this.
    """
    import zipfile

    from stripforge import layout
    from stripforge.config import load

    config = find_config(board)
    cfg = load(config) if config else None
    try:
        target = layout.select_restore_zip(board, cfg)
    except layout.RestoreExhausted as exc:
        return 0, f"Board: {board}\n{layout.format_exhausted(board, exc.oldest, cfg)}", None
    except FileNotFoundError:
        none = f"No save point yet in {layout.backup_dir(board, cfg)}. Build strips or Add links make one."
        return 0, f"Board: {board}\nResult: nothing to restore\n\n{none}", None
    if not ui.confirm_restore(board, target):
        if ui.wx is None:
            text = (
                f"Board: {board}\nResult: refused (nothing changed)\n\nRestore last "
                "replaces files and needs you to confirm that in a dialog, which can't be shown "
                "without wxPython. From a terminal: stripforge restore <board>."
            )
            return 2, text, None
        return 0, None, None
    try:
        res = layout.restore(board, cfg, target)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        text = (
            f"Board: {board}\nResult: refused (nothing changed)\n\n{exc}\n\nThe save points were not deleted."
        )
        return 2, text, None
    notes = [f"Board: {board}", "Result: OK", "", layout.format_restore(res)]
    rel = lambda p: p.absolute().relative_to(layout.project_dir(board).absolute()).as_posix()  # noqa: E731
    if rel(board) in res.restored or rel(board) in res.removed:
        try:
            busy_retry(board_doc.revert, "reloading the restored board")
            notes.insert(2, "Reloaded the restored board in the PCB editor: respawned where you left off.")
        except Exception as exc:
            why = "KiCad was busy" if isinstance(exc, KiCadBusy) else str(exc)
            notes.insert(
                2,
                f"Could not reload it ({why}): use File > Revert now, and don't save the board before that.",
            )
    if any(r.endswith(".kicad_sch") for r in res.restored):
        notes.append(REOPEN_NOTE.replace("remove the links again", "undo the restore"))
    return 0, "\n".join(notes), None


if __name__ == "__main__":  # python stripforge_plugin.py <action>, for debugging from a terminal
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "analyze"))
