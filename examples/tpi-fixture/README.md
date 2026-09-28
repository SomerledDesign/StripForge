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
- The Edge.Cuts outline is (50, 50)–(192.24, 110.96) mm, Kevin's X56 board: 56 × 24 holes
  (`A1`–`X56`), the first hole half a pitch in, at (51.27, 51.27) mm. The parts sit in the left
  30 columns.

Use it with Kevin's X56 config ([../x56.toml](../x56.toml)), which lists `BT1` as slotted:

```sh
stripforge analyze examples/tpi-fixture/ATtiny10_TPI_Fixture.kicad_pcb \
    --netlist examples/tpi-fixture/ATtiny10_TPI_Fixture.net --config examples/x56.toml
```

All 22 parts snap (BT1 as two slot jobs on strip U), with 0 conflicts, and the X56 grid matches
the outline. `stripforge build` on it gives 59 cuts and proposes 25 wire links of the 39 needed;
13 nets can't be joined by vertical links with this placement (see the build report).

The earlier M1 version of this fixture (25 footprints, 36 nets) is frozen in
[tests/fixtures/tpi-m1](../../tests/fixtures/tpi-m1) for the M1 tests.
