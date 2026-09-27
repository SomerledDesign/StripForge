# ATtiny10 TPI programming fixture (first test case)

This is StripForge's first real test case: Kevin's ATtiny10 TPI programming fixture, a 26-part
THT board. The KiCad project lives outside this repo, in Dropbox at
`KiCad/ATtiny10_TPI_Fixture`.

**Nothing is copied here yet.** In **M0** this folder will get:

- the netlist, exported with `kicad-cli sch export netlist --format kicadsexpr`;
- the board after *Update PCB from Schematic* (F8) with the parts roughly placed on a 2.54 mm grid;
- its `stripboard.toml` (see [../stripboard.toml](../stripboard.toml) for placeholder values).

Why it's a good first case (details in [Sketch.md](../../Sketch.md) §4.2 and §6):

- C1, C2 and C3 are 2.50 mm pitch parts, 0.04 mm off the 2.54 grid, and F1 is 0.01 mm off-axis,
  so they exercise the snap tolerance.
- J2 is a 2×9 carrier with DIP-style numbering, so pin numbers must come from the footprint.
- The IDC 2×5 header and J2 put adjacent pins on adjacent holes, which forces knife cuts or a
  rotated placement.

Success means all 26 footprints snap, the validator finds no multi-net strip pieces, DRC with
schematic parity is clean, and the fixture can be built from the build sheet with no copper-side
rework.
