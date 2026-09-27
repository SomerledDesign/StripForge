from pathlib import Path

import pytest

from stripforge import config

ROOT = Path(__file__).resolve().parents[1]


def test_load_example_config():
    cfg = config.load(ROOT / "examples" / "stripboard.toml")
    assert (cfg.cols, cfg.rows) == (30, 25)
    assert cfg.origin_mm == (51.27, 51.27)
    assert cfg.cut_style is config.CutStyle.AUTO
    assert cfg.snap_tol_mm == 0.15


def test_defaults_derive_grid():
    cfg = config.from_dict({})
    assert cfg.rows is None and cfg.cols is None and cfg.origin_mm is None
    assert cfg.pitch_mm == 2.54


@pytest.mark.parametrize(
    "data",
    [{"bogus": 1}, {"cut_style": "laser"}, {"rows": 0}, {"origin_mm": [1.0]}, {"pitch_mm": 0}],
)
def test_bad_config(data):
    with pytest.raises(ValueError):
        config.from_dict(data)
