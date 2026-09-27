# ATtiny10 TPI programming fixture (first test case)

This is StripForge's first real test case: Kevin's ATtiny10 TPI programming fixture. The KiCad
project lives outside this repo, in Dropbox at `KiCad/ATtiny10_TPI_Fixture`.

Files:

- `ATtiny10_TPI_Fixture.net`: the netlist, exported with
  `kicad-cli sch export netlist --format kicadsexpr` (Eeschema 10.0.4). 25 components, 36 nets.
- `ATtiny10_TPI_Fixture.kicad_pcb`: the board after *Update PCB from Schematic* (F8), saved by
  KiCad 10.0.4, with the 25 footprints (82 THT pads) placed roughly on a 2.54 mm grid and no
  tracks. The Edge.Cuts outline is (50, 50)–(126.2, 113.5) mm: 30 × 25 holes, the first hole half
  a pitch in, at (51.27, 51.27) mm. The project-library footprint for J2 is embedded in the board,
  so the `.pretty` library is not needed here.
- Grid config: [../stripboard.toml](../stripboard.toml).

Try it:

```sh
stripforge analyze examples/tpi-fixture/ATtiny10_TPI_Fixture.kicad_pcb \
    --netlist examples/tpi-fixture/ATtiny10_TPI_Fixture.net
```

Why it's a good first case (details in [Sketch.md](../../Sketch.md) §4.2 and §6):

- C1, C2 and C3 are 2.50 mm pitch parts, 0.04 mm off the 2.54 grid, and F1 is 0.01 mm off-axis,
  so they exercise the snap tolerance.
- J2 is a 2×9 carrier with DIP-style numbering, so pin numbers must come from the footprint.
- The IDC 2×5 header and J2 put adjacent pins on adjacent holes, which forces knife cuts or a
  rotated placement.

Success means all footprints snap, the validator finds no multi-net strip pieces, DRC with
schematic parity is clean, and the fixture can be built from the build sheet with no copper-side
rework.
