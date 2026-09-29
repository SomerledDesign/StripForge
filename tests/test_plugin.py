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
    assert sfp.built_path(b, "separate") == tmp_path / "fix-stripforge.kicad_pcb"
    assert (
        sfp.built_path(tmp_path / "fix-stripforge.kicad_pcb", "separate")
        == tmp_path / "fix-stripforge.kicad_pcb"
    )
    assert sfp.backup_path(b) == tmp_path / "fix-pre-stripbuild.kicad_pcb"
    assert sfp.sheet_path(tmp_path / "fix-stripforge.kicad_pcb") == tmp_path / "fix-stripforge.sheet.html"
    assert sfp.sheet_path(b) == tmp_path / "fix-stripforge.sheet.html"
    assert sfp.output_mode(None) == "in_place"
    assert sfp.find_config(b) is None
    (tmp_path / "stripboard.toml").write_text("")
    assert sfp.find_config(b) == tmp_path / "stripboard.toml"
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
    out = tmp_path / "fix-stripforge.kicad_pcb"
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
    built = str(tmp_path / "fix-stripforge.kicad_pcb")
    assert sfp.cli_args("build", b, cfg, net) == [
        "build", str(b), "-o", str(b), "--in-place", "--netlist", str(net), "--config", str(cfg),
    ]  # fmt: skip
    assert sfp.cli_args("build", b, cfg, net, mode="separate") == [
        "build", str(b), "-o", built, "--netlist", str(net), "--config", str(cfg),
    ]  # fmt: skip
    out = tmp_path / "fix-stripforge.kicad_pcb"
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
    backup = tmp / "fix-pre-stripbuild.kicad_pcb"
    assert path == tmp / "fix.kicad_pcb" and "Built fix.kicad_pcb in place" in text
    assert board.saved == 3 and board.reverted == 1 and "Reloaded the built board" in text
    assert f"Backup: {backup} (the board just before this build)" in text and "StripForge build:" in text
    assert text.count("Backup: ") == 1
    assert backup.read_bytes() == original and b"StripForge:CUT_" in (tmp / "fix.kicad_pcb").read_bytes()
    assert (tmp / "fix-stripforge.links.json").is_file()

    assert sfp.run("build") == 0  # rebuild: the original moves up to -1
    assert "moved up one: fix-pre-stripbuild-1.kicad_pcb" in shown[-1][1]
    assert (tmp / "fix-pre-stripbuild-1.kicad_pcb").read_bytes() == original
    assert backup.read_bytes() == (tmp / "fix.kicad_pcb").read_bytes()  # the built board, before this build

    assert sfp.run("sheet") == 0
    browser, (title, text, path) = shown[-2], shown[-1]
    assert path == tmp / "fix-stripforge.sheet.html" and path.is_file()
    assert browser == ("browser", path.as_uri(), None)
    assert "StripForge sheet:" in text

    assert sfp.run("drc") == 0
    assert "Result:" in shown[-1][1]


def test_run_build_separate_output(sfp, project):
    tmp, board, shown = project
    cfg = tmp / "stripboard.toml"
    assert 'output = "in_place"' in cfg.read_text()
    cfg.write_text(cfg.read_text().replace('output = "in_place"', 'output = "separate"'))
    assert sfp.run("build") == 0
    title, text, path = shown[-1]
    assert path == tmp / "fix-stripforge.kicad_pcb" and path.is_file()
    assert "The open board was not changed" in text and "StripForge build:" in text
    assert (tmp / "fix.kicad_pcb").read_bytes() == (
        REAL_FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb"
    ).read_bytes()
    assert not (tmp / "fix-pre-stripbuild.kicad_pcb").exists() and getattr(board, "reverted", 0) == 0

    assert sfp.run("sheet") == 0
    assert shown[-1][2] == tmp / "fix-stripforge.sheet.html" and "Built board:" in shown[-1][1]


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
    assert "No fix.kicad_sch next to the board" in shown[-1][1]

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
    backup = src / "sub-pre-links.kicad_sch"
    assert f"Backup: {backup}" in text and backup.read_text() == original
    assert {"W1", "W2", "W3"} <= set(_symbols(src / "sub.kicad_sch"))
    assert (src / "sym-lib-table").read_text() == table  # the symbol is embedded: table untouched

    assert sfp.run("links") == 0  # again: nothing to add, nothing rotated
    assert "Nothing to add" in shown[-1][1] and not (src / "sub-pre-links-1.kicad_sch").exists()


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
    assert not (tmp / "fix-pre-stripbuild.kicad_pcb").exists()


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
