# ATtiny10 TPI programming fixture (real parts)

This is StripForge's main test case: Kevin's ATtiny10 TPI programming fixture with the real parts
chosen, placed by Mildrew. The KiCad project lives outside this repo, in Dropbox at
`KiCad/ATtiny10_TPI_Fixture`.

Files:

- `ATtiny10_TPI_Fixture.net`: the netlist, exported with
  `kicad-cli sch export netlist --format kicadsexpr` (Eeschema 10.0.4). 22 components, 41 nets.
- `ATtiny10_TPI_Fixture.kicad_pcb`: the board after *Update PCB from Schematic* (F8), saved by
  KiCad 10.0.4, with the 22 footprints (86 THT pads) placed on a 2.54 mm grid and no tracks.
  Parts include the MSS6200 ZIF adapter (SW1, 2×9), the SOT23-6 carrier (J2, 2×9), the IDC 2×5
  (J1), two SS12D00G slide switches (SW2, SW3, 2.50 mm pitch), a push button (SW4), the MPD
  BH23APC battery holder (BT1, slotted) and a Littelfuse 395 fuse (F1). Project-library
  footprints are embedded in the board, so the `.pretty` library is not needed here.
- The Edge.Cuts outline is still (50, 50)–(126.2, 113.5) mm: 30 × 25 holes, the first hole half a
  pitch in, at (51.27, 51.27) mm.

Use it with Kevin's X56 config ([../x56.toml](../x56.toml)), which lists `BT1` as slotted:

```sh
stripforge analyze examples/tpi-fixture/ATtiny10_TPI_Fixture.kicad_pcb \
    --netlist examples/tpi-fixture/ATtiny10_TPI_Fixture.net --config examples/x56.toml
```

All 22 parts snap (BT1 as two slot jobs), with 0 conflicts. The X56 grid is 56 holes wide but the
outline is only 30, so `analyze` warns that the grid extends past Edge.Cuts; that is expected
until the outline is widened. `stripforge build` writes copper only for the holes inside the
outline.

The earlier M1 version of this fixture (25 footprints, 36 nets) is frozen in
[tests/fixtures/tpi-m1](../../tests/fixtures/tpi-m1) for the M1 tests.
