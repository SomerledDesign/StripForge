from stripforge import netlist


def test_parse_text_minimal():
    comps, nets = netlist.parse_text(
        '(export (version "E") (components (comp (ref "R1") (value "1k") (footprint "R:R") '
        '(sheetpath (names "/") (tstamps "/")))) '
        '(nets (net (code "1") (name "GND") (node (ref "R1") (pin "2")))))'
    )
    assert [(c.ref, c.footprint, c.sheet_path, c.value) for c in comps] == [("R1", "R:R", "/", "1k")]
    assert nets[0].name == "GND" and nets[0].nodes == [("R1", "2")]
    assert netlist.pin_nets(nets) == {("R1", "2"): "GND"}


def test_tpi_netlist(tpi_netlist_path):
    comps, nets = netlist.parse(tpi_netlist_path)
    assert len(comps) == 25
    assert len(nets) == 36
    pins = netlist.pin_nets(nets)
    assert pins[("C1", "1")] == "/SCHEMATIC DIAGRAM/+5V_T"
    assert pins[("J2", "3")] == "/SCHEMATIC DIAGRAM/CLK0"
