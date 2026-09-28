import pytest

from stripforge import config, drc, writer
from stripforge.cli import main


def v(type_, desc="", severity="error", items=()):
    return {"type": type_, "description": desc, "severity": severity, "items": list(items)}


def item(desc, x=1.27, y=1.27):
    return {"description": desc, "pos": {"x": x, "y": y}}


MISSING_LIB = "The current configuration does not include the footprint library 'StripForge'"
REPORT = {
    "kicad_version": "10.0.4",
    "violations": [
        v("track_dangling", "Track has unconnected end", "warning"),
        v("track_dangling", "Track has unconnected end", "warning"),
        v("lib_footprint_issues", MISSING_LIB, "warning"),
        v("shorting_items", "Items shorting two nets", items=[item("Track [A] on B.Cu")]),
        v("clearance", "Clearance violation (netclass 'Default' clearance 0.2 mm)"),
        v("clearance", "Clearance violation (rule 'SF strip gap' clearance 0.4 mm)"),
        v("courtyards_overlap", "Courtyards overlap", items=[item("Footprint W5"), item("Footprint U1")]),
        v("courtyards_overlap", "Courtyards overlap", items=[item("Footprint U2"), item("Footprint U1")]),
        v("silk_overlap", "Silkscreen overlap", "warning"),
    ],
    "unconnected_items": [
        v("unconnected_items", "Missing connection", items=[item("PTH pad 1 [GND] of J1"), item("Track")]),
        v("unconnected_items", "Missing connection", items=[item("PTH pad 2 [VCC] of C1")]),
    ],
    "schematic_parity": [],
}  # fmt: skip


def test_classify_buckets():
    res = drc.classify(REPORT)
    c = res.counts()
    assert c["filtered_track_dangling"] == 2
    assert res.filtered_missing_library == {"StripForge": 1}
    assert (c["shorts"], c["clearance"], c["stripboard_rules"], c["unconnected"]) == (1, 1, 1, 2)
    assert c["link_courtyard"] == 1  # W5 over U1: reported, not failing
    assert c["other_errors"] == 1  # U1/U2 courtyards: a real placement problem
    assert c["other_warnings"] == 1
    assert res.real_count == 1 + 1 + 1 + 2 + 1 and not res.ok
    assert res.unconnected_nets() == {"GND": 1, "VCC": 1}
    text = drc.format_text(res, "b.kicad_pcb", expected_links=2)
    assert "2 track_dangling" in text and "needs 2 link(s)" in text and "real problem" in text


def test_only_noise_is_clean():
    res = drc.classify({"violations": REPORT["violations"][:3], "unconnected_items": []})
    assert res.ok and res.filtered_dangling == 2


def test_parity_not_checked_is_reported():
    res = drc.classify({}, parity_requested=True, log="Failed to fetch schematic netlist for parity tests.")
    assert res.parity_checked is False
    assert "NOT checked" in drc.format_text(res, "b")


def test_missing_kicad_cli_skips(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("KICAD_CLI", str(tmp_path / "no-such-kicad-cli"))
    assert drc.find_kicad_cli() is None
    assert main(["drc", str(tmp_path / "b.kicad_pcb")]) == 3
    assert "skipped" in capsys.readouterr().err


needs_kicad = pytest.mark.skipif(drc.find_kicad_cli() is None, reason="kicad-cli not installed")


@needs_kicad
def test_real_fixture_pass1_drc(tmp_path, real_board_path, real_netlist_path):
    cfg = config.load(real_board_path.parents[1] / "x56.toml")
    out = tmp_path / "b.kicad_pcb"
    res = writer.build(real_board_path, cfg, out, netlist=str(real_netlist_path))
    d = drc.run_drc(out, parity=False)
    assert d.counts()["shorts"] == 0 and d.counts()["clearance"] == 0
    assert not d.stripboard_rules
    assert len(d.unconnected) == res.plan.needed
    assert d.filtered_dangling > 0
