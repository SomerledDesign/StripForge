"""The [drc] table of stripboard.toml: board-wide ignores and allowed courtyard overlaps."""

import json
from pathlib import Path

import pytest

from stripforge import config, drc

# Trimmed from a real kicad-cli 10.0.4 report on Kevin's X56 TPI fixture (2026-09-27): J2's
# courtyard overlaps C2 and C3 (courtyards_overlap + pth_inside_courtyard), one silk_overlap
# (R3/D3), dead strip ends and the StripForge library warnings.
REAL = json.loads((Path(__file__).parent / "fixtures" / "drc-kicad10-x56.json").read_text(encoding="utf-8"))


def _cfg(**drc_table):
    return config.from_dict({"drc": drc_table}).drc


def test_real_report_types_are_known_kicad_types():
    assert {v["type"] for v in REAL["violations"]} <= config.KICAD_DRC_TYPES
    for t in ("silk_overlap", "silk_over_copper", "silk_edge_clearance", "courtyards_overlap"):
        assert t in config.KICAD_DRC_TYPES


def test_without_config_the_overlaps_are_errors():
    res = drc.classify(REAL)
    assert len(res.other_errors) == 6 and res.filtered_config == {}


def test_ignore_and_allow_overlap_filter_but_count():
    cfg = _cfg(ignore=["silk_overlap", "silk_over_copper"], allow_overlap=[["J2", "C2"], ["C3", "J2"]])
    res = drc.classify(REAL, drc_config=cfg)
    assert res.other_errors == [] and res.other == []
    assert res.filtered_config == {
        "courtyards_overlap J2/C2": 1,
        "pth_inside_courtyard J2/C2": 2,
        "courtyards_overlap C3/J2": 1,
        "pth_inside_courtyard C3/J2": 2,
        "silk_overlap": 1,
    }
    assert res.counts()["filtered_config"] == 7
    text = drc.format_text(res, "b.kicad_pcb")
    assert "filtered (config): 1 courtyards_overlap C3/J2, 1 courtyards_overlap J2/C2" in text
    assert "1 silk_overlap" in text
    # track_dangling / library noise is still its own bucket
    assert res.filtered_dangling == 2 and "filtered (expected): 2 track_dangling" in text


def test_allow_overlap_is_pair_exact():
    report = {
        "violations": [
            {"type": "courtyards_overlap", "severity": "error", "description": "Courtyards overlap",
             "items": [{"description": "Footprint J2"}, {"description": "Footprint R1"}]},
            {"type": "courtyards_overlap", "severity": "error", "description": "Courtyards overlap",
             "items": [{"description": "Footprint C2"}, {"description": "Footprint J2"}]},
            {"type": "clearance", "severity": "error", "description": "Clearance violation",
             "items": [{"description": "PTH pad 1 [GND] of J2"}, {"description": "PTH pad 1 [A] of C2"}]},
        ]
    }  # fmt: skip
    res = drc.classify(report, drc_config=_cfg(allow_overlap=[["J2", "C2"]]))
    assert res.filtered_config == {"courtyards_overlap J2/C2": 1}
    assert [v["items"][1]["description"] for v in res.other_errors] == ["Footprint R1"]
    assert len(res.clearance) == 1  # only courtyard items are covered by allow_overlap


def test_item_refs():
    v = {
        "items": [
            {"description": "PTH pad 2 [/SCHEMATIC DIAGRAM/VCC1] of C3"},
            {"description": "Footprint J2"},
        ]
    }
    assert drc.item_refs(v) == ["C3", "J2"]


def test_unknown_ignore_type_is_warned_not_fatal():
    res = drc.classify(REAL, drc_config=_cfg(ignore=["silk_ovrlap"]))
    assert res.config_warnings == ["[drc] ignore: 'silk_ovrlap' is not a KiCad 10 DRC type name (typo?)"]
    assert "warning: [drc] ignore: 'silk_ovrlap'" in drc.format_text(res, "b")


@pytest.mark.parametrize(
    "table, msg",
    [
        ({"ignroe": ["silk_overlap"]}, "unknown [drc] key(s): ignroe"),
        ({"ignore": "silk_overlap"}, "must be a list"),
        ({"allow_overlap": [["J2"]]}, "reference pairs"),
        ({"allow_overlap": [["J2", "J2"]]}, "same part twice"),
    ],
)
def test_bad_drc_tables(table, msg):
    with pytest.raises(ValueError, match=msg.replace("[", r"\[").replace("(", r"\(").replace(")", r"\)")):
        config.from_dict({"drc": table})


def test_example_config_parses_with_its_drc_table():
    ex = Path(__file__).parents[1] / "examples" / "x56.toml"
    cfg = config.load(ex)
    assert cfg.slotted == ["BT1"] and isinstance(cfg.drc, config.DrcConfig)


def test_toml_drc_table_round_trip(tmp_path):
    p = tmp_path / "stripboard.toml"
    p.write_text('slotted = ["BT1"]\n[drc]\nignore = ["silk_overlap"]\nallow_overlap = [["J2", "C2"]]\n')
    cfg = config.load(p)
    assert (
        cfg.drc.ignore == ["silk_overlap"] and cfg.drc.allows("C2", "J2") and not cfg.drc.allows("J2", "C3")
    )


def test_cli_drc_applies_the_config_drc_table(monkeypatch, tmp_path, capsys):
    from stripforge.cli import main

    cfg = tmp_path / "stripboard.toml"
    cfg.write_text('[drc]\nignore = ["silk_overlap"]\nallow_overlap = [["J2", "C2"], ["J2", "C3"]]\n')
    seen = {}

    def fake_run_drc(board, kicad_cli=None, parity=True, report_path=None, schematic=None, drc_config=None):
        seen["cfg"] = drc_config
        return drc.classify(REAL, drc_config=drc_config)

    monkeypatch.setattr(drc, "run_drc", fake_run_drc)
    assert main(["drc", str(tmp_path / "b.kicad_pcb"), "--config", str(cfg), "--no-parity"]) == 1
    out = capsys.readouterr().out
    assert seen["cfg"].allow_overlap == [("J2", "C2"), ("J2", "C3")]
    assert "other errors 0" in out and "filtered (config):" in out and "1 silk_overlap" in out


def test_cli_drc_rejects_a_bad_drc_table(tmp_path, capsys):
    from stripforge.cli import main

    cfg = tmp_path / "stripboard.toml"
    cfg.write_text('[drc]\nallow = ["J2"]\n')
    assert main(["drc", str(tmp_path / "b.kicad_pcb"), "--config", str(cfg)]) == 2
    assert "unknown [drc] key(s): allow" in capsys.readouterr().err


def test_cli_drc_parity_uses_the_project_schematic(monkeypatch, tmp_path):
    from stripforge.cli import main

    seen = []

    def fake_run_drc(board, kicad_cli=None, parity=True, report_path=None, schematic=None, drc_config=None):
        seen.append(schematic)
        return drc.classify(REAL, drc_config=drc_config)

    monkeypatch.setattr(drc, "run_drc", fake_run_drc)
    (tmp_path / "fix.kicad_sch").write_text("(kicad_sch)")
    for name in ("fix.kicad_pcb", "fix-stripforge.kicad_pcb"):
        main(["drc", str(tmp_path / name)])
    # in place: kicad-cli finds fix.kicad_sch itself; a separate board is pointed at it
    assert seen == [None, str(tmp_path / "fix.kicad_sch")]
