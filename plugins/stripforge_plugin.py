# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""StripForge inside KiCad 10's PCB editor: the shared code behind the four IPC plugin actions.

KiCad starts ``sf_<action>.py`` in this plugin's own virtual environment (``requirements.txt``;
created with ``--system-site-packages``, so KiCad's wxPython is there for dialogs) with
``KICAD_API_SOCKET`` / ``KICAD_API_TOKEN`` set. Each action:

1. connects to KiCad with kicad-python (``kipy``) and asks for the open board's file name;
2. offers to save the board first (StripForge works on the saved ``.kicad_pcb``; KiCad 10's API has
   no "modified" flag, so it always asks);
3. uses ``stripboard.toml`` next to the board if there is one, else derives the grid from
   Edge.Cuts; exports a fresh netlist from ``<name>.kicad_sch`` with kicad-cli when it exists;
4. runs the same code as the ``stripforge`` command line and shows its report.

Nothing changes the open board. "Build strips" writes ``<name>-stripforge.kicad_pcb`` (and its
``.kicad_dru`` and link lists) next to it; "Run DRC" and "Build sheet" work on that built board
(or on the open board when it is already a StripForge output). Why not live edits through the API:
see Sketch.md §4.10.

The helpers at the top are pure (no kipy, no wx) so they can be tested without KiCad.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import traceback
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
IDENTIFIER = "com.github.somerleddesign.stripforge"
BUILT_SUFFIX = "-stripforge"
CONFIG_NAME = "stripboard.toml"
TITLES = {
    "analyze": "StripForge: Analyze",
    "build": "StripForge: Build strips",
    "drc": "StripForge: Run DRC",
    "sheet": "StripForge: Build sheet",
}


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
    """``fixture`` for both ``fixture.kicad_pcb`` and ``fixture-stripforge.kicad_pcb``."""
    s = board.stem
    return s[: -len(BUILT_SUFFIX)] if s.endswith(BUILT_SUFFIX) and len(s) > len(BUILT_SUFFIX) else s


def built_path(board: Path) -> Path:
    """Where "Build strips" writes: ``<name>-stripforge.kicad_pcb`` next to the placement board."""
    return board.with_name(base_stem(board) + BUILT_SUFFIX + ".kicad_pcb")


def is_output_name(board: Path) -> bool:
    return board.stem.endswith(BUILT_SUFFIX) and board.stem != BUILT_SUFFIX


def find_config(board: Path) -> Path | None:
    """``stripboard.toml`` next to the board, or None (derive the grid from Edge.Cuts)."""
    p = board.parent / CONFIG_NAME
    return p if p.is_file() else None


def fallback_config(board: Path) -> tuple[Path | None, str | None]:
    """No ``stripboard.toml``: use the only other ``*.toml`` next to the board if it is a valid
    StripForge config (e.g. ``X56.toml``), else point the other files out. Returns (config, note)."""
    others = sorted(board.parent.glob("*.toml"))
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
    out = built_path(board)
    if not out.is_file():
        return None, (
            f'No built board yet: run "{TITLES["build"]}" first; it writes {out.name} next to {board.name}.'
        )
    note = ""
    if out.stat().st_mtime < board.stat().st_mtime:
        note = f"NOTE: {out.name} is older than {board.name}; if you changed the board, build again."
    return out, note


def cli_args(action: str, board: Path, config: Path | None, netlist: Path | None = None,
             schematic: Path | None = None, kicad_cli: str | None = None) -> list[str]:  # fmt: skip
    """The ``stripforge`` command line an action runs (``board``: the file that command reads)."""
    cfg = ["--config", str(config)] if config else []
    net = ["--netlist", str(netlist)] if netlist else []
    if action == "analyze":
        return ["analyze", str(board), *net, *cfg]
    if action == "build":
        return ["build", str(board), "-o", str(built_path(board)), *net, *cfg]
    if action == "drc":
        extra = ["--schematic", str(schematic)] if schematic else []
        extra += ["--kicad-cli", kicad_cli] if kicad_cli else []
        return ["drc", str(board), *cfg, *extra]
    if action == "sheet":
        return ["sheet", str(board), "-o", str(sheet_path(board)), *net, *cfg]
    raise ValueError(f"unknown action {action!r}")


def sheet_path(board: Path) -> Path:
    return board.with_suffix(".sheet.html")


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

    def ask_save(self, board: Path) -> bool | None:
        """True: save first; False: use the file on disk; None: cancel."""
        if self.wx is None:
            return True
        wx = self.wx
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

    def report(self, title: str, text: str, path: Path | None = None) -> None:
        if self.wx is None:
            print(f"{title}\n{text}")
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
        board = kicad.get_board()
    except Exception as exc:  # kipy raises ApiError when no board is open
        raise RuntimeError(f"No board is open in the PCB editor ({exc})") from exc
    return kicad, board


def _kicad_cli(kicad) -> str | None:
    from stripforge.drc import find_kicad_cli

    try:
        p = kicad.get_kicad_binary_path("kicad-cli")
        if p and Path(p).exists():
            return p
    except Exception:
        pass
    return find_kicad_cli()


def run(action: str) -> int:
    """Entry point for ``sf_<action>.py``."""
    title = TITLES[action]
    ui = Ui()
    try:
        if bootstrap() is None:
            raise RuntimeError(f"the stripforge package is missing next to {HERE}")
        kicad, board_doc = connect()
        project = board_doc.get_project()
        board = board_path(project.path, board_doc.name)
        if board is None:
            ui.report(title, "Save the board to a .kicad_pcb file first, then run the action again.")
            return 0
        choice = ui.ask_save(board)
        if choice is None:
            return 0
        if choice:
            board_doc.save()
        if not board.is_file():
            ui.report(title, f"Board file not found: {board}\nSave the board first.")
            return 0
        text, shown = _run_action(action, board, project.name, _kicad_cli(kicad))
        ui.report(title, text, shown)
        return 0
    except Exception as exc:  # show every failure to the user instead of a silent status-bar line
        detail = traceback.format_exc()
        print(detail, file=sys.stderr)
        ui.report(title, f"StripForge failed: {exc}\n\n{detail}")
        return 1


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
    if action == "build" and is_output_name(board):
        return (
            f"{board.name} is already a StripForge output. Open the placement board "
            f"{base_stem(board)}.kicad_pcb and build from that.",
            None,
        )
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
        argv = cli_args(action, target, config, netlist, schematic if action == "drc" else None, kicad_cli)
        code, out = run_cli(argv)
    shown: Path | None = None
    if action == "build" and code in (0, 1) and built_path(board).is_file():  # not a stale file on refusal
        shown = built_path(board)
        notes.append(f"Wrote {shown.name} next to the board; open it in KiCad to see the strips. "
                     "The open board was not changed.")  # fmt: skip
    if action == "sheet" and sheet_path(target).is_file():
        shown = sheet_path(target)
        webbrowser.open(shown.as_uri())
    status = {0: "OK", 1: "finished with problems (see the report)", 2: "refused (see the report)",
              3: "skipped: kicad-cli not found"}.get(code, f"exit {code}")  # fmt: skip
    return "\n".join([*notes, f"Result: {status}", "", out.rstrip()]), shown


if __name__ == "__main__":  # python stripforge_plugin.py <action>, for debugging from a terminal
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "analyze"))
