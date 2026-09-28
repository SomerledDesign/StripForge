# Frozen M1 TPI fixture

The ATtiny10 TPI fixture as committed in `cea21cd` (25 footprints, 36 nets, 82 THT pads): the
board and netlist that M1 and M2 part A were developed against. It is frozen here so the tests
that pin its exact numbers (cuts, hints, snap offsets) stay stable. `examples/tpi-fixture/` now
holds the real-parts board (22 footprints, 41 nets). Grid config: `examples/stripboard.toml`.
