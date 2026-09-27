# StripForge

**Lay out stripboard (Veroboard) circuits in KiCad with real footprints and a live netlist, and get a normal `.kicad_pcb` whose copper is the strips.**

[![CI](https://github.com/SomerledDesign/StripForge/actions/workflows/ci.yml/badge.svg)](https://github.com/SomerledDesign/StripForge/actions/workflows/ci.yml)
![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange)
![KiCad 10](https://img.shields.io/badge/KiCad-10-314CB0)
![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)

## Why

Stripboard is still the quickest way to build a one-off circuit you can solder, but laying it out
usually means leaving KiCad for a separate app with its own parts and its own idea of the circuit.
Then the schematic and the build drift apart.

StripForge keeps the whole job inside KiCad:

- **Real KiCad footprints.** It uses the parts from your project, not a second parts library.
- **A live netlist.** The schematic stays the source of truth, and KiCad's schematic-parity check
  keeps the board honest.
- **A normal `.kicad_pcb` as output.** The copper is stripboard strips on B.Cu instead of routed
  tracks. You can open, edit, DRC and plot it like any other board.

## How it works

1. **Snap parts to the grid.** Place the THT parts roughly on a 2.54 mm grid. StripForge snaps each
   footprint rigidly to the nearest holes, within a tolerance for parts that are slightly off pitch
   (for example 2.50 mm capacitors), and logs every deviation.
2. **Split strips by net.** Each row becomes hole-to-hole copper pieces on B.Cu. Wherever two nets
   would share a strip, StripForge places a cut and gives every remaining piece the net of the pads
   on it.
3. **Cuts are real copper gaps plus markers.** The gap in the copper is the electrical truth, so
   KiCad sees exactly what the physical board will have. A marker on a user layer makes each cut
   visible and plottable.
4. **Wire links are zero-ohm jumpers.** Links are `W` jumper parts that also exist in the
   schematic, so the schematic and board stay in sync.
5. **KiCad DRC catches shorts and opens.** KiCad's own checks (`kicad-cli pcb drc` with schematic
   parity) flag a strip carrying two nets, a missing link or an extra cut. StripForge also runs the
   same checks in pure Python first, for faster and clearer errors.
6. **A mirrored copper-side build sheet.** An SVG/PDF of the copper side, mirrored the way you hold
   the board, with every cut and link listed, row and column labels, and a 1:1 scale ruler.

The full design is in [Sketch.md](Sketch.md).

## Status

**Pre-alpha: M1 (model and splitting) in progress.** `stripforge analyze` reads a `.kicad_pcb`
(and optionally its netlist), snaps footprints to the hole grid, splits the strips by net and
reports cuts, nets needing links, warnings and conflicts. It is read-only: nothing writes a board
yet (that is M2).

## Roadmap

| Milestone | Scope |
|---|---|
| **M0: Foundations** | Repo and CI; lossless S-expression round-trip on a KiCad 10 board; netlist parser; fixture intake; strip, cut-marker and link-footprint specs; a first `.kicad_dru`; verify every open question against a real KiCad 10 install. |
| **M1: Model and splitting** | Pure-Python grid snap, strip model, cut placement, net per piece, link proposals and validation, with unit tests and a JSON plan output. |
| **M2: Board output and DRC** | File-backend `.kicad_pcb` writer, `kicad-cli` DRC wrapper and classifier, the two-pass link flow, and mutation tests proving DRC guards the layout. |
| **M3: Build sheet and IPC (stretch)** | Mirrored SVG/PDF build sheet with cut and link lists; a live-board IPC backend (one undo step) and an optional KiCad plugin wrapper. |

## Target

**KiCad 10.** v0 works headless through a file backend plus `kicad-cli`. The IPC API
(kicad-python) backend comes later, and the SWIG `pcbnew` API is avoided because KiCad 11 removes it.

## First test case

An **ATtiny10 TPI programming fixture** (26 THT parts). See [examples/tpi-fixture](examples/tpi-fixture/).
It is done when every part snaps, DRC with schematic parity is clean, and the fixture can be built
from the build sheet with no rework on the copper side.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
stripforge analyze examples/tpi-fixture/ATtiny10_TPI_Fixture.kicad_pcb \
    --netlist examples/tpi-fixture/ATtiny10_TPI_Fixture.net      # add --json for machine output
stripforge --help      # plan | generate | drc | sheet are still stubs
ruff check . && ruff format --check .
pytest
```

```
src/stripforge/
  cli.py          command-line entry (analyze | plan | generate | drc | sheet)
  analyze.py      read-only snap + split report (text or JSON)
  config.py       stripboard.toml model
  netlist.py      kicad-cli netlist (kicadsexpr) parser
  sexpr.py        S-expression reader/writer
  board.py        footprints, pads (absolute positions, rotation) and outline from a .kicad_pcb
  grid.py         2.54 grid + per-footprint snap with tolerance
  strips.py       rows -> hole-to-hole segments
  splitter.py     cut placement + net per piece
  links.py        open-net detection + link proposals
  validate.py     pure-Python short/open/parity pre-check
  drc.py          kicad-cli pcb drc wrapper + classifier
  buildsheet.py   copper-side SVG/PDF + cut/link CSV
  backends/       file (.kicad_pcb), ipc (kipy), swig_fallback (isolated, unused)
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## Credits and prior art

- **[perfboard-studio](https://github.com/medinstech/perfboard-studio)** (Apache-2.0) inspired
  StripForge. Its stripboard model (strips split into segments at the cuts), connectivity checks
  and build guide shaped this design. Any code adapted from it will keep its attribution and
  Apache-2.0 notices, which is compatible with GPL-3.0.
- **[VeroRoute](https://sourceforge.net/projects/veroroute/)**, **VeeCAD** and
  **[DIYLC](https://github.com/bancika/diy-layout-creator)** are the standalone stripboard and
  layout tools that came before. They are credited for ideas only; no code is taken from them.

## License

Copyright (C) 2026 Somerled Design.

StripForge is free software: you can redistribute it and/or modify it under the terms of the
**GNU General Public License** as published by the Free Software Foundation, either **version 3** of
the License, or (at your option) **any later version** (`GPL-3.0-or-later`). It is distributed in
the hope that it will be useful, but WITHOUT ANY WARRANTY. See [LICENSE](LICENSE) for the full text.

## Author

**Somerled Design** ([@SomerledDesign](https://github.com/SomerledDesign))
