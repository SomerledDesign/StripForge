import pytest

from stripforge import config, resources


def test_library_and_rules_are_found():
    lib = resources.library_dir()
    assert lib.name == "StripForge.pretty"
    assert resources.footprint_file("Link_P7.62").exists()
    assert resources.footprint_file("CUT_Hole").exists()
    assert resources.rules_file().name == "stripforge.kicad_dru"


def test_missing_footprint_is_a_clear_error():
    with pytest.raises(FileNotFoundError, match="StripForge:Link_P99"):
        resources.footprint_file("Link_P99")


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("STRIPFORGE_RULES", str(tmp_path / "nope.kicad_dru"))
    with pytest.raises(FileNotFoundError):
        resources.rules_file()


def test_committed_rules_match_the_config_width():
    text = resources.rules_file().read_text()
    assert resources.dru_strip_width(text) == 1.8
    for name in ("x56.toml", "stripboard.toml"):
        cfg = config.load(resources.REPO_ROOT / "examples" / name)
        assert resources.check_rules_width(text, cfg.strip_width_mm) == []


def test_width_mismatch_warns():
    text = resources.rules_file().read_text()
    (w,) = resources.check_rules_width(text, 1.6)
    assert "1.8 mm" in w and "1.6 mm" in w and "gen_dru.py --strip-width 1.6" in w
    assert 'no "SF strip width" rule' in resources.check_rules_width("(version 1)", 1.8)[0]
