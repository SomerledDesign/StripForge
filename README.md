# StripForge

<p align="center"><img src="assets/StripForge-icon-1024x1024.png" alt="StripForge: netlist to stripboard" width="320"></p>

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

**Pre-alpha: M1 and M2 done (build sheet and IPC are M3).** `stripforge analyze` reads a
`.kicad_pcb` (and optionally its netlist), snaps footprints to the hole grid, splits the strips by
net and reports cuts, nets needing links, slot jobs, off-board parts, placement hints, warnings and
conflicts, using hole labels (`A1`…). `stripforge snap -o` writes a copy of the board with each
part moved by its best-fit shift. `stripforge build` writes the strip copper, the cuts and the wire
links into a copy of the board, and `stripforge drc` runs KiCad's DRC through `kicad-cli` and sorts
the results into real problems and expected stripboard noise.

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

An **ATtiny10 TPI programming fixture** (22 real THT parts, 41 nets; the earlier 25-part M1 board is
frozen in `tests/fixtures/tpi-m1/`). See [examples/tpi-fixture](examples/tpi-fixture/).
It is done when every part snaps, DRC with schematic parity is clean, and the fixture can be built
from the build sheet with no rework on the copper side.

## Development

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
stripforge analyze examples/tpi-fixture/ATtiny10_TPI_Fixture.kicad_pcb \
    --netlist examples/tpi-fixture/ATtiny10_TPI_Fixture.net      # add --json for machine output
stripforge analyze <board>.kicad_pcb --config examples/x56.toml  # Kevin's X56 board (A1-X56)
stripforge snap <board>.kicad_pcb -o <out>.kicad_pcb   # move parts by their best-fit shift (dry run without -o)
stripforge build <board>.kicad_pcb --netlist <board>.net --config examples/x56.toml -o <out>.kicad_pcb
stripforge drc <out>.kicad_pcb                          # kicad-cli DRC, classified
stripforge --help      # plan | sheet are still stubs
ruff check . && ruff format --check .
pytest
```

### Building a board: `stripforge build` and the two-pass link flow

`stripforge build <board> [--netlist <net>] [--config <toml>] -o <out.kicad_pcb>` never touches its
input. It snaps the parts (as `snap` does), splits the strips by net and writes into `<out>`:

- **strips**: one `B.Cu` track per pair of neighbouring holes, hole centre to hole centre,
  `strip_width_mm` wide, on the piece's net. Uncut bare strip is written as copper with no net (it
  is physically there; KiCad only calls it `track_dangling`, which `stripforge drc` filters);
- **cuts**: a real gap in the copper (a hole cut leaves no copper touching the hole; a knife cut
  removes the track between two holes) plus a `StripForge:CUT_Hole` / `StripForge:CUT_Knife`
  marker footprint on `User.1` (refs `CUT1`…, board-only, not in the BOM or position files). The
  markers are embedded in the board, so it loads and DRCs without the StripForge library configured;
- **links**: see below;
- next to `<out>`: `<out>.kicad_dru` (a copy of `rules/stripforge.kicad_dru`, which `kicad-cli`
  and pcbnew pick up automatically) and the link proposal as `<out>.links.json`, `.links.csv` and
  `.links.txt`.

The input must be the placement board: a track or via StripForge did not write is refused. Building
again from a StripForge output removes its own strips and cut markers first and rewrites them; the
output is deterministic (same input, same bytes). Parts outside the Edge.Cuts outline are reported,
and copper is only written for holes inside the outline.

**Pass 1.** For every net split over several strip pieces, StripForge proposes straight wire links
along a column (across strips), 1–32 pitches long (`StripForge:Link_P2.54` … `Link_P81.28`), like a
minimum spanning tree: fewest and shortest links, no two links in one hole, no overlapping links in
one column where it can be avoided, avoiding part courtyards, and only on free holes (never a cut or
a pad). A cut may slide within its gap to free a landing hole. The report lists each link:

```
W4    D12 -> J12  StripForge:Link_P15.24   GND
```

Add them to the schematic: one 2-pin jumper per line (`Jumper:Jumper_2_Bridged` or a 0 Ω
resistor), Reference `W4`, Footprint `StripForge:Link_P15.24`, both pins wired to the net (`GND`).
Then press F8 (Update PCB from Schematic). `<out>.links.txt` has the full list and these steps. A
net that can't be joined with vertical links is an error in the report: move or rotate a part so its
pieces share a column with free holes.

**Pass 2.** Build again from the board that now has the `W` footprints (anywhere on the board): each
`W` is matched by reference and net to the proposal and placed on its two holes (pad 1 on the upper
hole; the link footprints are vertical at 0°). Missing, extra, wrong-footprint or wrong-net `W`
parts are reported. The `W` parts are not treated as components, so the strips and cuts don't
change.

Exit codes: 0 complete (every net joined, every link placed), 1 incomplete (links still to add or
place, unlinkable nets, rejected parts), 2 refused (bad input, conflicts, output = input).

### DRC: `stripforge drc`

`stripforge drc <board.kicad_pcb> [--no-parity] [--report drc.json] [--json]` runs
`kicad-cli pcb drc --format json --severity-all --schematic-parity` (kicad-cli 10.0.4; found via
`--kicad-cli`, `$KICAD_CLI`, `PATH` or the macOS app bundle) and classifies the result:

- **real problems** (exit 1): shorts, clearance, unconnected items, schematic parity, violations of
  a StripForge (`SF …`) rule, and any other error;
- **filtered, counted** (expected on stripboard): `track_dangling` (dead strip ends) and the
  "footprint library not configured" warning (the footprints are embedded);
- **reported, not failing**: a `W` link over a part courtyard (a wire can run under a part), and
  other warnings (silk), summarised by type.

Schematic parity needs the `.kicad_sch` (and `.kicad_pro`) next to the board with the same name;
without it kicad-cli skips parity and the report says so. After pass 1 the unconnected items are
exactly the links still to add; after pass 2 they should be 0. Exit 3 means kicad-cli was not found
(the tests needing it are skipped in CI).

### Strip width and the DRC rules

`strip_width_mm` is 1.8 mm (caliper-measured on Kevin's X56 board). The rules in
`rules/stripforge.kicad_dru` are generated for that width: **whenever `strip_width_mm` changes,
regenerate them** with `python rules/gen_dru.py --strip-width <w>`. `stripforge build` warns when
the rules file and the config disagree.

### Hole labels

Copper strips run horizontally. Strips (rows) are letters `A..Z`, then `AA, AB, …, ZZ`
(spreadsheet style); holes along a strip are numbered from 1. The top-left hole (component side) is
`A1`, so `K12` is the 12th hole on the 11th strip. The TPI fixture's 30 × 25 grid runs `A1`–`Y30`.
Reports use labels throughout; the `--json` output also keeps the 0-based `(col, row)`.

```
src/stripforge/
  cli.py          command-line entry (analyze | snap | build | drc; plan | sheet are stubs)
  analyze.py      snap + split report (text or JSON), best-fit moves
  hints.py        placement hints (parts lying along a strip, 90° rotation estimate)
  config.py       stripboard.toml model
  netlist.py      kicad-cli netlist (kicadsexpr) parser
  sexpr.py        S-expression reader/writer (byte-exact for untouched nodes)
  board.py        footprints, pads (absolute positions, rotation) and outline from a .kicad_pcb
  grid.py         2.54 grid, hole labels, per-footprint snap with tolerance and slots
  strips.py       rows -> hole-to-hole segments
  splitter.py     cut placement + net per piece
  links.py        link proposals (pass 1), link reports
  writer.py       stripforge build: strips, cut markers, link placement (pass 2)
  resources.py    StripForge footprint library and rules lookup
  validate.py     pure-Python short/open/parity pre-check
  drc.py          kicad-cli pcb drc wrapper + classifier
  buildsheet.py   copper-side SVG/PDF + cut/link CSV
  backends/       file (.kicad_pcb), ipc (kipy), swig_fallback (isolated, unused)
```

### Icon

The StripForge icon is `assets/StripForge-icon-1024x1024.png` (1488 × 1328 px; a JPEG copy sits
next to it). The KiCad plugin manager icon is `resources/icon.png`, a 64 × 64 px crop of the "S".
The PCM `metadata.json` schema has no icon field: the PCM takes the icon from `resources/icon.png`
in the package archive, and from an `icon.png` next to `metadata.json` in the metadata repository.

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
