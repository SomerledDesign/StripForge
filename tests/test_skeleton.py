import tomllib
from pathlib import Path

import pytest

import stripforge
from stripforge import PITCH_NM
from stripforge.cli import main

ROOT = Path(__file__).resolve().parents[1]


def test_import_and_pitch():
    assert stripforge.__version__
    assert PITCH_NM == 2_540_000


def test_cli_help_uses_stripforge_name(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "stripforge" in capsys.readouterr().out


def test_example_config_parses():
    cfg = tomllib.loads((ROOT / "examples" / "stripboard.toml").read_text())
    assert cfg["rows"] > 0 and cfg["cols"] > 0
    assert cfg["cut_style"] in {"hole", "knife", "auto"}
