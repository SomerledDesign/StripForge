"""The KiCad 10 IPC plugin (plugins/): plugin.json, the entry scripts and the helpers, without KiCad."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

import pytest

from conftest import REAL_FIXTURE

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
IDENT = re.compile(r"^[a-zA-Z][-_a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$")  # go.kicad.org/api/schemas/v1
ACTION_IDENT = re.compile(r"^[a-zA-Z][-_a-zA-Z0-9.]{0,48}[a-zA-Z0-9]$")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def sfp():
    return _load("stripforge_plugin", PLUGINS / "stripforge_plugin.py")


def test_plugin_json_follows_the_kicad_10_api_plugin_schema():
    pj = json.loads((PLUGINS / "plugin.json").read_text())
    assert pj["$schema"] == "https://go.kicad.org/api/schemas/v1"
    assert IDENT.match(pj["identifier"]) and pj["identifier"] == "com.github.somerleddesign.stripforge"
    assert len(pj["name"]) <= 200 and len(pj["description"]) <= 500
    assert pj["runtime"]["type"] == "python"
    names = [a["name"] for a in pj["actions"]]
    assert names == [
        "StripForge: Analyze",
        "StripForge: Build strips",
        "StripForge: Add links to schematic",
        "StripForge: Run DRC",
        "StripForge: Build sheet",
        "StripForge: Restore last",
        "StripForge: Dialog",
    ]
    ids = [a["identifier"] for a in pj["actions"]]
    assert len(set(ids)) == len(ids)
    for a in pj["actions"]:
        assert ACTION_IDENT.match(a["identifier"])
        assert set(a) <= {"identifier", "name", "description", "show-button", "scopes", "entrypoint", "args",
                          "icons-light", "icons-dark"}  # fmt: skip
        assert a["description"] and len(a["description"]) <= 500
        assert a["scopes"] == ["pcb"] and a["show-button"] is True
        assert "args" not in a  # KiCad 10 ignores args for Python actions: one script per action
        script = PLUGINS / a["entrypoint"]
        assert script.is_file() and "from stripforge_plugin import run" in script.read_text()
        for icon in a["icons-light"] + a["icons-dark"]:
            assert (PLUGINS / icon).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_requirements_file_exists_and_pins_kicad_python():
    req = (PLUGINS / "requirements.txt").read_text()
    assert re.search(r"^kicad-python==0\.8\.\d+$", req, re.M)
    assert "tomli" in req


def test_entry_scripts_map_to_actions(sfp):
    for action in sfp.TITLES:
        text = (PLUGINS / f"sf_{action}.py").read_text()
        assert f'run("{action}")' in text


def test_path_helpers(sfp, tmp_path):
    assert sfp.board_path("/p", "") is None
    assert sfp.board_path("/p", "b.kicad_pcb") == Path("/p/b.kicad_pcb")
    assert sfp.board_path("/p", "/q/b.kicad_pcb") == Path("/q/b.kicad_pcb")
    b = tmp_path / "fix.kicad_pcb"
    assert sfp.base_stem(b) == "fix" and sfp.base_stem(tmp_path / "fix-stripforge.kicad_pcb") == "fix"
    assert sfp.base_stem(tmp_path / "fix-pre-stripbuild.kicad_pcb") == "fix"
    assert sfp.base_stem(tmp_path / "fix-pre-stripbuild-12.kicad_pcb") == "fix"
    assert sfp.built_path(b) == b  # output = "in_place" (default)
    sf = tmp_path / "stripforge"
    assert sfp.built_path(b, "separate") == sf / "fix-stripforge.kicad_pcb"
    assert sfp.built_path(sf / "fix-stripforge.kicad_pcb", "separate") == sf / "fix-stripforge.kicad_pcb"
    assert sfp.sheet_path(sf / "fix-stripforge.kicad_pcb") == sf / "fix-stripforge.sheet.html"
    assert sfp.sheet_path(b) == sf / "fix-stripforge.sheet.html"
    assert sfp.log_path(b) == sf / "fix-stripforge.log"
    (tmp_path / "LoadMan.kicad_pro").write_text("{}")  # the project's name, not the board's
    assert sfp.sheet_path(b) == sf / "LoadMan-stripforge.sheet.html"
    (tmp_path / "LoadMan.kicad_pro").unlink()
    assert sfp.output_mode(None) == "in_place"
    assert sfp.find_config(b) is None
    (tmp_path / "stripboard.toml").write_text("")
    assert sfp.find_config(b) == tmp_path / "stripboard.toml"
    sf.mkdir()
    (sf / "stripboard.toml").write_text("")
    assert sfp.find_config(b) == sf / "stripboard.toml"  # stripforge/ first
    assert sfp.find_schematic(b) is None
    (tmp_path / "proj.kicad_sch").write_text("")
    (tmp_path / "fix.kicad_sch").write_text("")
    assert sfp.find_schematic(b, "proj") == tmp_path / "proj.kicad_sch"
    assert sfp.find_schematic(tmp_path / "fix-stripforge.kicad_pcb") == tmp_path / "fix.kicad_sch"


def test_check_target_prefers_the_built_sibling(sfp, tmp_path):
    b = tmp_path / "fix.kicad_pcb"
    b.write_text("(kicad_pcb)")
    target, note = sfp.check_target(b)
    assert target is None and "Build strips" in note
    legacy = tmp_path / "fix-stripforge.kicad_pcb"  # 0.2.x put it next to the board
    legacy.write_text("(kicad_pcb)")
    assert sfp.check_target(b)[0] == legacy
    out = tmp_path / "stripforge" / "fix-stripforge.kicad_pcb"
    out.parent.mkdir()
    out.write_text("(kicad_pcb)")
    old = time.time() - 100
    os.utime(out, (old, old))
    target, note = sfp.check_target(b)
    assert target == out and "older" in note
    assert sfp.check_target(out) == (out, "")
    marked = tmp_path / "other.kicad_pcb"
    marked.write_text('(kicad_pcb (footprint "StripForge:CUT_Hole"))')
    assert sfp.check_target(marked) == (marked, "")


def test_cli_args(sfp, tmp_path):
    b, cfg, net = tmp_path / "fix.kicad_pcb", tmp_path / "stripboard.toml", tmp_path / "fix.net"
    assert sfp.cli_args("analyze", b, None) == ["analyze", str(b)]
    built = str(tmp_path / "stripforge" / "fix-stripforge.kicad_pcb")
    assert sfp.cli_args("build", b, cfg, net) == [
        "build", str(b), "-o", str(b), "--in-place", "--netlist", str(net), "--config", str(cfg),
    ]  # fmt: skip
    assert sfp.cli_args("build", b, cfg, net, mode="separate") == [
        "build", str(b), "-o", built, "--netlist", str(net), "--config", str(cfg),
    ]  # fmt: skip
    out = tmp_path / "stripforge" / "fix-stripforge.kicad_pcb"
    sch = tmp_path / "fix.kicad_sch"
    assert sfp.cli_args("drc", out, None, schematic=sch, kicad_cli="/k") == [
        "drc", str(out), "--schematic", str(sch), "--kicad-cli", "/k",
    ]  # fmt: skip
    assert sfp.cli_args("sheet", out, None)[:4] == [
        "sheet",
        str(out),
        "-o",
        str(out.with_suffix(".sheet.html")),
    ]
    with pytest.raises(ValueError):
        sfp.cli_args("plan", b, None)


class FakeProject:
    def __init__(self, path, name):
        self.path, self.name = str(path), name


class FakeBoard:
    def __init__(self, path: Path):
        self._path = path
        self.saved = 0

    @property
    def name(self):
        return self._path.name

    def get_project(self):
        return FakeProject(self._path.parent, self._path.stem)

    def save(self):
        self.saved += 1

    def revert(self):
        self.reverted = getattr(self, "reverted", 0) + 1


class FakeKiCad:
    def get_kicad_binary_path(self, name):
        return ""


@pytest.fixture
def project(tmp_path, sfp, monkeypatch):
    """The real fixture as an open 'project', with the KiCad connection and the UI faked."""
    shutil.copy(REAL_FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb", tmp_path / "fix.kicad_pcb")
    shutil.copy(REAL_FIXTURE.parent / "x56.toml", tmp_path / "stripboard.toml")
    board = FakeBoard(tmp_path / "fix.kicad_pcb")
    shown = []
    monkeypatch.setattr(sfp, "connect", lambda: (FakeKiCad(), board))
    monkeypatch.setattr(sfp.Ui, "__init__", lambda self: setattr(self, "wx", None))
    monkeypatch.setattr(
        sfp.Ui, "report", lambda self, title, text, path=None: shown.append((title, text, path))
    )
    monkeypatch.setattr(sfp.webbrowser, "open", lambda url: shown.append(("browser", url, None)))
    monkeypatch.setattr(sfp, "_kicad_cli", lambda kicad: None)
    monkeypatch.setattr(sfp.Ui, "ask_save", lambda self, b, required=False: True)  # the user said OK
    monkeypatch.setenv("STRIPFORGE_CHROME", str(tmp_path / "no-chrome"))  # HTML only, no PDF
    return tmp_path, board, shown


def test_run_analyze_build_sheet_without_kicad(sfp, project):
    tmp, board, shown = project
    assert sfp.run("analyze") == 0
    title, text, path = shown[-1]
    assert title == "StripForge: Analyze" and board.saved == 1
    assert f"Config: {tmp / 'stripboard.toml'}" in text and "Result:" in text and "A1" in text

    assert sfp.run("sheet") == 0
    assert "No built board yet" in shown[-1][1]

    original = (tmp / "fix.kicad_pcb").read_bytes()
    assert sfp.run("build") == 0
    title, text, path = shown[-1]
    from stripforge import layout

    (backup,) = layout.backups(tmp / "fix.kicad_pcb")
    assert backup.parent == tmp / "stripforge" / "fix-stripforge-backups"
    assert path == tmp / "fix.kicad_pcb" and "Built fix.kicad_pcb in place" in text
    assert board.saved == 3 and board.reverted == 1 and "Reloaded the built board" in text
    assert f"Backup: {backup} (save point" in text and "StripForge build:" in text
    assert text.count("Backup: ") == 1
    assert layout.read_member(backup, "fix.kicad_pcb").encode() == original
    assert b"StripForge:CUT_" in (tmp / "fix.kicad_pcb").read_bytes()
    assert (tmp / "stripforge" / "fix-stripforge.links.json").is_file()
    assert not list(tmp.glob("fix-pre-stripbuild*")) and not (tmp / "fix-stripforge.log").exists()

    built = (tmp / "fix.kicad_pcb").read_bytes()
    assert sfp.run("build") == 0  # rebuild: a second zip; the first is untouched
    zips = layout.backups(tmp / "fix.kicad_pcb")
    assert zips[0] == backup and len(zips) == 2

    assert sfp.run("sheet") == 0
    browser, (title, text, path) = shown[-2], shown[-1]
    assert path == tmp / "stripforge" / "fix-stripforge.sheet.html" and path.is_file()
    assert browser == ("browser", path.as_uri(), None)
    assert "StripForge sheet:" in text

    assert sfp.run("drc") == 0
    assert "Result:" in shown[-1][1]
    assert (tmp / "fix.kicad_pcb").read_bytes() == built


def test_restore_last(sfp, project, monkeypatch):
    tmp, board, shown = project
    assert sfp.run("restore") == 0
    assert "No save point yet" in shown[-1][1] and getattr(board, "reverted", 0) == 0
    original = (tmp / "fix.kicad_pcb").read_bytes()
    assert sfp.run("build") == 0
    links = tmp / "stripforge" / "fix-stripforge.links.json"
    assert links.is_file() and (tmp / "fix.kicad_pcb").read_bytes() != original
    assert sfp.run("restore") == 2  # no wx: no confirmation dialog, so nothing is changed
    assert "Result: refused" in shown[-1][1] and links.is_file()
    asked = []
    monkeypatch.setattr(sfp.Ui, "confirm_restore", lambda self, b, z: asked.append(z.name) or True)
    reverted = board.reverted
    assert sfp.run("restore") == 0
    text = shown[-1][1]
    assert asked and asked[0].startswith("fix-stripforge-") and asked[0].endswith(".zip")
    assert "Loaded save point" in text and "respawned where you left off" in text
    assert "first save point" in text
    assert board.reverted == reverted + 1
    assert (tmp / "fix.kicad_pcb").read_bytes() == original and not links.exists()
    assert sfp.run("restore") == 0  # already at the only save point: stop, do not reload
    assert "nothing further to restore" in shown[-1][1]
    assert (tmp / "fix.kicad_pcb").read_bytes() == original and board.reverted == reverted + 1


def test_restore_last_walks_six_save_points_and_reopens_at_the_same_step(sfp, project, monkeypatch):
    """Dialog and toolbar both call ``_perform`` → ``_restore``. A new ``run`` reads the cursor
    from disk, which is what reopening the window does."""
    from stripforge import layout

    tmp, board, shown = project
    pcb = tmp / "fix.kicad_pcb"
    marker = b'\n(gr_line (start 0 0) (end 1 1) (layer "Dwgs.User") (uuid "USER-ORIGINAL-GEOMETRY"))\n'
    baseline = pcb.read_bytes() + marker
    assert b"StripForge:CUT_" not in baseline and b"R3" in baseline
    pcb.write_bytes(baseline)
    links = layout.out_file(pcb, "links.json")
    t = 1_800_000_000.0
    points = []
    for i in range(6):
        sp = layout.save_point(pcb, [pcb, links], action="build", now=t + i * 10)
        points.append((sp.path, sp.path.read_bytes()))
        pcb.write_bytes(
            baseline
            + f'\n(segment (start 0 0) (end {i + 1} 0) (layer "B.Cu") (net {i + 1}))\n'.encode()
            + b'(footprint "StripForge:CUT_Hole" (layer "User.1"))\n'
            + b'(pad "1" (size 1.7 1.7) (drill 1) (layers "F.Cu"))\n'
        )
        links.parent.mkdir(parents=True, exist_ok=True)
        links.write_text("{}\n")
    assert b"StripForge:CUT_Hole" in pcb.read_bytes()
    asked = []
    monkeypatch.setattr(sfp.Ui, "confirm_restore", lambda self, b, z: asked.append(z.name) or True)
    for step in range(6):
        assert sfp.run("restore") == 0
        text = shown[-1][1]
        assert "Loaded save point" in text and "respawned where you left off" in text
        assert "safety" not in asked[-1]
        assert asked[-1] == points[5 - step][0].name
        if step == 2:
            # reopen: the next choice comes from the cursor file, not from this process's Ui
            assert layout.select_restore_zip(pcb).name == points[2][0].name
            cursor = (layout.backup_dir(pcb) / layout.CURSOR_NAME).read_text()
            assert points[3][0].name in cursor
    assert pcb.read_bytes() == baseline
    assert b"USER-ORIGINAL-GEOMETRY" in pcb.read_bytes() and b"R3" in pcb.read_bytes()
    assert b"StripForge:CUT_Hole" not in pcb.read_bytes() and not links.exists()
    assert board.reverted == 6
    for path, data in points:
        assert path.read_bytes() == data
    assert len(layout.safety_copies(pcb)) == 6
    assert sfp.run("restore") == 0
    assert "nothing further to restore" in shown[-1][1]
    assert len(asked) == 6 and board.reverted == 6 and pcb.read_bytes() == baseline
    for path, data in points:
        assert path.read_bytes() == data


def test_run_build_separate_output(sfp, project):
    tmp, board, shown = project
    cfg = tmp / "stripboard.toml"
    assert 'output = "in_place"' in cfg.read_text()
    cfg.write_text(cfg.read_text().replace('output = "in_place"', 'output = "separate"'))
    assert sfp.run("build") == 0
    title, text, path = shown[-1]
    assert path == tmp / "stripforge" / "fix-stripforge.kicad_pcb" and path.is_file()
    assert "The open board was not changed" in text and "StripForge build:" in text
    assert (tmp / "fix.kicad_pcb").read_bytes() == (
        REAL_FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb"
    ).read_bytes()
    assert getattr(board, "reverted", 0) == 0

    assert sfp.run("sheet") == 0
    assert shown[-1][2] == tmp / "stripforge" / "fix-stripforge.sheet.html" and "Built board:" in shown[-1][1]


def test_build_on_an_output_rebuilds_it_in_place_and_reports_errors(sfp, project, monkeypatch):
    tmp, board, shown = project
    out = tmp / "fix-stripforge.kicad_pcb"
    shutil.copy(tmp / "fix.kicad_pcb", out)
    board._path = out
    assert sfp.cli_args("build", out, None)[:4] == ["build", str(out), "-o", str(out)]
    assert "--in-place" in sfp.cli_args("build", out, None)
    assert "--in-place" in sfp.cli_args("build", tmp / "fix.kicad_pcb", None)
    assert "--in-place" not in sfp.cli_args("build", tmp / "fix.kicad_pcb", None, mode="separate")
    assert sfp.run("build") == 0
    text = shown[-1][1]
    assert "rebuilding it in place" in text and "Reloaded the built board" in text
    assert '"StripForge:CUT_' in out.read_text()  # rebuilt: strips and cut markers written

    def boom():
        raise RuntimeError("No board is open in the PCB editor")

    monkeypatch.setattr(sfp, "connect", boom)
    assert sfp.run("analyze") == 1
    assert "StripForge failed: No board is open" in shown[-1][1]


def test_unsaved_board(sfp, project, monkeypatch):
    tmp, board, shown = project
    monkeypatch.setattr(sfp, "board_path", lambda d, n: None)
    assert sfp.run("analyze") == 0
    assert "Save the board" in shown[-1][1] and board.saved == 0


def test_only_other_toml_next_to_the_board_is_used(sfp, project):
    tmp, board, shown = project
    (tmp / "stripboard.toml").rename(tmp / "X56.toml")
    assert sfp.run("analyze") == 0
    text = shown[-1][1]
    assert f"Config: {tmp / 'X56.toml'}" in text
    assert "using X56.toml, the only .toml next to the board" in text
    assert "grid derived from Edge.Cuts" not in text


def test_several_other_tomls_are_pointed_out(sfp, project):
    tmp, board, shown = project
    (tmp / "stripboard.toml").rename(tmp / "X56.toml")
    (tmp / "other.toml").write_text("rows = 10\n")
    assert sfp.run("analyze") == 0
    text = shown[-1][1]
    assert "grid derived from Edge.Cuts" in text
    assert "X56.toml, other.toml found; copy or rename one of them to stripboard.toml" in text


def test_invalid_other_toml_is_not_used(sfp, project):
    tmp, board, shown = project
    (tmp / "stripboard.toml").unlink()
    (tmp / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    assert sfp.run("analyze") == 0
    text = shown[-1][1]
    assert "grid derived from Edge.Cuts" in text and "pyproject.toml found but not used" in text


def test_refused_build_does_not_claim_a_stale_file(sfp, project, monkeypatch):
    tmp, board, shown = project
    (tmp / "fix-stripforge.kicad_pcb").write_text("stale")
    monkeypatch.setattr(sfp, "run_cli", lambda argv: (2, "stripforge build: error: the board has conflicts"))
    sfp.run("build")
    text = shown[-1][1]
    assert "Result: refused" in text and "Wrote " not in text


def test_add_links_to_schematic(sfp, project, monkeypatch):
    from test_linksym import _project, _symbols

    tmp, board, shown = project
    assert sfp.run("links") == 0  # the placement board has no W footprints yet
    assert "No fix.kicad_sch in the project folder" in shown[-1][1]

    (tmp / "demo-proj").mkdir()
    src, built = _project(tmp / "demo-proj")
    table = (src / "sym-lib-table").read_text()
    own = built.rename(src / "demo.kicad_pcb")
    demo = FakeBoard(own)
    monkeypatch.setattr(sfp, "connect", lambda: (FakeKiCad(), demo))
    original = (src / "sub.kicad_sch").read_text()
    assert sfp.run("links") == 0
    title, text, path = shown[-1]
    assert title == "StripForge: Add links to schematic" and path == src / "demo.kicad_sch"
    assert "Added 3 StripForge:Link symbol(s) to sub.kicad_sch" in text and sfp.REOPEN_NOTE in text
    from stripforge import layout

    (backup,) = layout.backups(own)
    assert f"Backup: {backup}" in text and layout.read_manifest(backup)["action"] == "links"
    assert layout.read_member(backup, "sub.kicad_sch") == original
    assert not list(src.glob("*-pre-links*"))
    assert {"W1", "W2", "W3"} <= set(_symbols(src / "sub.kicad_sch"))
    assert (src / "sym-lib-table").read_text() == table  # the symbol is embedded: table untouched

    assert sfp.run("links") == 0  # again: nothing to add, nothing rotated
    assert "Nothing to add" in shown[-1][1] and len(layout.backups(own)) == 1


class BusyError(Exception):
    """Like kipy's ApiError for AS_BUSY."""

    def __init__(self):
        super().__init__("KiCad is busy and cannot respond to API requests right now")
        self.code = "AS_BUSY"


def test_busy_retry_backs_off_then_gives_up(sfp):
    naps = []
    calls = iter([BusyError(), BusyError(), "ok"])

    def call():
        v = next(calls)
        if isinstance(v, Exception):
            raise v
        return v

    assert sfp.busy_retry(call, "saving", sleep=naps.append) == "ok" and naps == [0.25, 0.5]

    def always():
        raise BusyError()

    naps.clear()
    with pytest.raises(sfp.KiCadBusy, match="saving"):
        sfp.busy_retry(always, "saving", wait_s=10, sleep=naps.append)
    assert sum(naps) >= 10 and max(naps) == 2.0

    def other():
        raise ValueError("no board")

    with pytest.raises(ValueError):  # not busy: raised at once, no retry
        sfp.busy_retry(other, "x", sleep=lambda s: pytest.fail("slept"))


def test_build_waits_while_kicad_is_busy_saving(sfp, project, monkeypatch):
    """Kevin 2026-09-29: board_doc.save() raised 'KiCad is busy' right after the OK dialog."""
    tmp, board, shown = project
    monkeypatch.setattr(sfp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sfp.Ui, "ask_save", lambda self, b, required=False: True)
    busy = {"save": 2, "revert": 1}

    def save():
        if busy["save"]:
            busy["save"] -= 1
            raise BusyError()
        board.saved += 1

    def revert():
        if busy["revert"]:
            busy["revert"] -= 1
            raise BusyError()
        board.reverted = getattr(board, "reverted", 0) + 1

    monkeypatch.setattr(board, "save", save)
    monkeypatch.setattr(board, "revert", revert)
    assert sfp.run("build") == 0
    text = shown[-1][1]
    assert board.saved == 1 and board.reverted == 1 and "Reloaded the built board" in text
    assert "Traceback" not in text


def test_build_reports_busy_kicad_plainly_and_builds_nothing(sfp, project, monkeypatch):
    tmp, board, shown = project
    monkeypatch.setattr(sfp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sfp.Ui, "ask_save", lambda self, b, required=False: True)
    before = (tmp / "fix.kicad_pcb").read_bytes()

    def save():
        raise BusyError()

    monkeypatch.setattr(board, "save", save)
    assert sfp.run("build") == 1
    title, text, _ = shown[-1]
    assert title == "StripForge: Build strips" and "Traceback" not in text
    assert "KiCad is busy" in text and "saving the board" in text
    assert "Press Esc" in text and "Cmd+S" in text and "click StripForge: Build strips again" in text
    assert (tmp / "fix.kicad_pcb").read_bytes() == before
    assert not list(tmp.glob("stripforge/*-backups"))


def test_busy_reload_after_the_build_says_revert_without_saving(sfp, project, monkeypatch):
    tmp, board, shown = project
    monkeypatch.setattr(sfp.time, "sleep", lambda s: None)
    monkeypatch.setattr(sfp.Ui, "ask_save", lambda self, b, required=False: True)

    def revert():
        raise BusyError()

    monkeypatch.setattr(board, "revert", revert)
    assert sfp.run("build") == 0
    text = shown[-1][1]
    assert "Could not reload it (KiCad was busy): use File > Revert now" in text
    assert "don't save the board before that" in text


# --- M4d: fail loudly (issue #1) ----------------------------------------------------------------


def test_every_report_is_logged_next_to_the_board(sfp, project):
    tmp, board, shown = project
    assert sfp.run("analyze") == 0
    log = tmp / "stripforge" / "fix-stripforge.log"
    assert log.is_file() and "StripForge: Analyze" in log.read_text()
    assert f"Log: {log}" in shown[-1][1] and "Result:" in log.read_text()


def test_without_wx_the_report_says_why_there_is_no_dialog(sfp, project):
    tmp, board, shown = project
    assert sfp.run("analyze") == 0
    assert "wxPython is not importable" in shown[-1][1] and "Recreate Plugin Environment" in shown[-1][1]


def test_without_wx_ask_save_refuses_an_in_place_build(sfp, tmp_path, capsys):
    ui = sfp.Ui.__new__(sfp.Ui)
    ui.wx = None
    board = tmp_path / "b.kicad_pcb"
    assert sfp.Ui.ask_save(ui, board, required=True) is None
    assert sfp.Ui.ask_save(ui, board) is True and "saved first" in capsys.readouterr().out


def test_without_wx_build_in_place_is_refused_and_nothing_saved(sfp, project, monkeypatch):
    tmp, board, shown = project
    monkeypatch.setattr(sfp.Ui, "ask_save", lambda self, b, required=False: None if required else True)
    before = (tmp / "fix.kicad_pcb").read_bytes()
    assert sfp.run("build") == 2
    assert board.saved == 0 and "refused" in shown[-1][1] and "wxPython" in shown[-1][1]
    assert (tmp / "fix.kicad_pcb").read_bytes() == before
    assert (tmp / "stripforge" / "fix-stripforge.log").is_file()


def test_no_edge_cuts_is_refused_before_saving(sfp, project):
    tmp, board, shown = project
    (tmp / "stripboard.toml").unlink()
    pcb = tmp / "fix.kicad_pcb"
    text = pcb.read_text()
    pcb.write_text(text.replace('(layer "Edge.Cuts")', '(layer "Cmts.User")'))
    assert sfp.run("build") == 2
    msg = shown[-1][1]
    assert board.saved == 0 and "no board outline on Edge.Cuts" in msg and "Draw the board outline" in msg


def test_report_without_wx_prints_one_line_pointing_at_the_log(sfp, tmp_path, capsys):
    ui = sfp.Ui.__new__(sfp.Ui)
    ui.wx = None
    sfp.Ui.report(ui, "T", "long\nreport", log=tmp_path / "x.log")
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and str(tmp_path / "x.log") in out


def test_dialog_shows_settings_and_does_not_save_or_change_policy(sfp, project, monkeypatch):
    tmp, board, shown = project
    before = (tmp / "stripboard.toml").read_text()

    def boom(*_a, **_k):
        raise AssertionError("opening the dialog must not run an action")

    monkeypatch.setattr(sfp, "_perform", boom)
    assert sfp.run("dialog") == 0
    title, text, _path = shown[-1]
    assert title == "StripForge: Dialog"
    assert "backup_policy: move" in text and "rows: 24" in text and "slotted: BT1" in text
    assert "The backup policy was not changed" in text
    assert board.saved == 0 and (tmp / "stripboard.toml").read_text() == before
    assert (tmp / "stripforge" / "fix-stripforge.log").is_file()


def test_dialog_analyze_uses_the_same_action_as_the_toolbar(sfp, project):
    tmp, board, shown = project
    ui = sfp.Ui()
    code, text, _shown = sfp._perform(ui, "analyze", FakeKiCad(), board, board._path, board._path.stem)
    assert code == 0 and board.saved == 1
    assert "Result:" in text and "A1" in text and f"Config: {tmp / 'stripboard.toml'}" in text
    assert sfp.run("analyze") == 0
    assert "A1" in shown[-1][1]


def test_apply_backup_policy_preserves_the_file_and_deletes_nothing(sfp, tmp_path):
    from stripforge import layout
    from stripforge.config import load, prune_keep

    board = tmp_path / "fix.kicad_pcb"
    board.write_text("(kicad_pcb)\n")
    (tmp_path / "fix.kicad_pro").write_text("{}\n")
    toml = tmp_path / "stripboard.toml"
    toml.write_text(
        "# Kevin's note\nrows = 24\ncols = 56\norigin_mm = [1, 2]\nbackup_keep = 4\n\n[bend]\nSW2 = 0.16\n"
    )
    kept = layout.save_point(board, [board], action="build")
    legacy = tmp_path / "fix-pre-stripbuild.kicad_pcb"
    legacy.write_text("old")
    path = sfp.apply_backup_policy(board, "retention", 1)
    assert path == toml
    text = toml.read_text()
    assert "# Kevin's note" in text and "[bend]" in text and "SW2 = 0.16" in text and "rows = 24" in text
    assert 'backup_policy = "retention"' in text and "backup_keep = 1" in text
    cfg = load(toml)
    assert cfg.backup_policy == "retention" and cfg.backup_keep == 1 and prune_keep(cfg) == 1
    assert kept.path.is_file() and legacy.read_text() == "old"  # saving the setting deletes nothing
    snap = sfp.dialog_snapshot(board)
    assert "backup_policy: retention" in snap and "down to 1" in snap
    # back to move: the number stays, deletion stops, the zip stays
    sfp.apply_backup_policy(board, "move", 1)
    assert prune_keep(load(toml)) == 0 and kept.path.is_file() and "Kevin's note" in toml.read_text()
