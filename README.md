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

**Pre-alpha, 0.1.0: M1, M2 and M3 done.** StripForge runs inside KiCad 10's PCB editor as an
IPC plugin (see [Install in KiCad](#install-in-kicad)) and as a command line. `stripforge analyze` reads a
`.kicad_pcb` (and optionally its netlist), snaps footprints to the hole grid, splits the strips by
net and reports cuts, nets needing links, slot jobs, off-board parts, placement hints, warnings and
conflicts, using hole labels (`A1`…). `stripforge snap -o` writes a copy of the board with each
part moved by its best-fit shift. `stripforge build` writes the strip copper, the cuts and the wire
links into a copy of the board, and `stripforge drc` runs KiCad's DRC through `kicad-cli` and sorts
the results into real problems and expected stripboard noise. `stripforge sheet` writes a printable
build sheet (HTML, and PDF when Chrome is available).

## Roadmap

| Milestone | Scope |
|---|---|
| **M0: Foundations** | Repo and CI; lossless S-expression round-trip on a KiCad 10 board; netlist parser; fixture intake; strip, cut-marker and link-footprint specs; a first `.kicad_dru`; verify every open question against a real KiCad 10 install. |
| **M1: Model and splitting** | Pure-Python grid snap, strip model, cut placement, net per piece, link proposals and validation, with unit tests and a JSON plan output. |
| **M2: Board output and DRC** | File-backend `.kicad_pcb` writer, `kicad-cli` DRC wrapper and classifier, the two-pass link flow, and mutation tests proving DRC guards the layout. |
| **M3: Build sheet and IPC (stretch)** | Mirrored SVG/PDF build sheet with cut and link lists; a live-board IPC backend (one undo step) and an optional KiCad plugin wrapper. |

## Target

**KiCad 10** (tested with 10.0.4). The work is done by a file backend plus `kicad-cli`, both
headless. The KiCad plugin uses the IPC API (kicad-python) to find and save the open board. The
SWIG `pcbnew` API is avoided because KiCad 11 removes it. Python 3.9+ (KiCad 10's bundled Python on
macOS is 3.9).

## Install in KiCad

StripForge is a KiCad 10 **IPC plugin**. It isn't in the official PCM yet, because the repository
is private.

1. KiCad > Preferences > Plugins: tick **Enable KiCad API**, and check that the Python interpreter
   is KiCad's own (the default on macOS).
2. Install it one of two ways:
   - **From the package:** build it with `python tools/make_pcm_zip.py` (writes
     `dist/StripForge-<version>-pcm.zip`), then Plugin and Content Manager > **Install from
     File…**.
   - **By hand:** copy the *contents* of the zip's `plugins/` folder to
     `~/Documents/KiCad/10.0/plugins/com.github.somerleddesign.stripforge/` (macOS; on Linux
     `~/.local/share/kicad/10.0/plugins/`, on Windows `Documents\KiCad\10.0\plugins\`).
3. Restart KiCad and open a board in the PCB editor. KiCad creates the plugin's Python environment
   (kicad-python, from `plugins/requirements.txt`) the first time, which takes a minute. Four
   StripForge toolbar buttons appear in the PCB editor:
   - **StripForge: Analyze**: a read-only report: snap, cuts, links needed, slot jobs, hints.
   - **StripForge: Build strips**: writes `<name>-stripforge.kicad_pcb` (plus `.kicad_dru` and
     the link lists) **next to the board**. The open board is never changed. Open that file to see
     the strips.
   - **StripForge: Run DRC**: kicad-cli DRC with schematic parity on the built board, classified.
   - **StripForge: Build sheet**: writes `<name>-stripforge.sheet.html` (and `.pdf` if Chrome is
     installed) and opens it in the browser.

Each action offers to save the board first, because StripForge reads the saved file. It uses
`stripboard.toml` next to the board if there is one. Without it, it uses the only other `*.toml`
next to the board (e.g. `X56.toml`) if that is a valid StripForge config, and says so in the
report; with several, or none, it derives the grid from the Edge.Cuts outline. If `<project>.kicad_sch` is there, it exports a fresh netlist with kicad-cli to
cross-check pad nets. Results appear in a dialog ("Show in Finder", "Copy report"). If a button
does nothing, see the status-bar warnings or Preferences > Plugins > "Recreate Plugin
Environment". To uninstall a hand install, delete the folder from step 2. Manual test steps:
[docs/KICAD-PLUGIN-TEST.md](docs/KICAD-PLUGIN-TEST.md).

The StripForge footprint library (`footprints/StripForge.pretty`, needed for the `W` links'
`StripForge:Link_*` footprints in the schematic) is bundled in the plugin but **not registered**:
add it to the footprint library table by hand, with nickname `StripForge`. A PCM plugin package
can't register libraries; that needs a separate library package (see
[docs/PCM-SUBMISSION.md](docs/PCM-SUBMISSION.md)).

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
stripforge drc <out>.kicad_pcb [--schematic <board>.kicad_sch]   # kicad-cli DRC, classified
stripforge sheet <out>.kicad_pcb --netlist <board>.net --config examples/x56.toml -o <out>.sheet.html
python tools/make_pcm_zip.py   # the KiCad PCM package in dist/
stripforge --help      # plan is still a stub
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
- **holes**: every free stripboard hole is drawn (one board-only `SF_HOLES_<strip>` footprint per
  strip with a plated pad per hole, on that strip's net, on `B.Cu` only), so the built board looks
  like the real board in pcbnew and the 3D viewer. Hole cuts are drawn as bare (non-plated) holes;
  holes under part pins and link pads take the part's pad instead. `draw_holes = false` turns this
  off, `hole_drill_mm` (default 1.0) sets the drill;
- **links**: see below;
- next to `<out>`: `<out>.kicad_dru` (a copy of `rules/stripforge.kicad_dru`, which `kicad-cli`
  and pcbnew pick up automatically) and the link proposal as `<out>.links.json`, `.links.csv` and
  `.links.txt`.

The input must be the placement board: a track or via StripForge did not write is refused. Building
again from a StripForge output removes its own strips and cut markers first and rewrites them; the
output is deterministic (same input, same bytes). Parts outside the Edge.Cuts outline are reported,
and copper is only written for holes inside the outline.

**Pass 1.** For every net split over several strip pieces, StripForge proposes wire links like a
minimum spanning tree: fewest and shortest links, from **any free hole** of one piece to any free
hole of another (every grid hole counts, not only footprint holes; never a cut, a pad or another
link's hole). In order of preference a link runs:

1. straight down a column (`StripForge:Link_P2.54` … `Link_P81.28`, 1–32 pitches); a cut may slide
   within its gap to free a landing hole;
2. along a strip, bridging a cut (rare: two pieces of one net on one strip are only split when a
   different-net pin sits between them, and a wire can't run over that pin);
3. on a diagonal (`diagonal_links`, default on), rotated: a `Link_P*` when the length is a whole
   number of pitches (3-4-5 and friends), otherwise an off-pitch `Link_D<mm>` (`Link_D3.59` for a
   1x1 offset … `Link_D81.16`; 305 footprints in the library; `off_pitch_links = false` keeps to
   whole-pitch diagonals);
4. as two links meeting on a **bus strip** (`bus_strips`, default on): a piece of unused bare strip
   that the net takes over; hole cuts isolate it from the rest of that strip where that leaves a
   useful remainder (the report says "hole cut at R5 isolates a bus strip").

Rules for every link: no longer than `max_link_mm` (default 81.28 mm = 32 pitches); never crossing
or touching another link; never passing over (within 0.9 mm of) a part pin or another link's end,
since bare wire would short to it; part courtyards avoided where possible (a wire may run under a
part, reported). The report lists each link, with the rotation for diagonals:

```
W4    D12 -> J12  StripForge:Link_P15.24   GND
W19   J18 -> T16  StripForge:Link_D25.90   +5V_T  (diagonal, rotated 348.69 deg)  (to bus strip T)
```

Add them to the schematic: one 2-pin jumper per line (`Jumper:Jumper_2_Bridged` or a 0 Ω
resistor), Reference `W4`, Footprint `StripForge:Link_P15.24`, both pins wired to the net (`GND`).
Then press F8 (Update PCB from Schematic). `<out>.links.txt` has the full list and these steps. A
net that still can't be joined is an error in the report, naming its pieces: move or rotate a part
so they come closer, or free some holes.

**Pass 2.** Build again from the board that now has the `W` footprints (anywhere on the board): each
`W` is matched by reference and net to the proposal and placed on its two holes (pad 1 on the upper
hole; a straight-down link at 0°, a diagonal or along-the-strip link rotated so pad 2 lands on
its hole). Missing, extra, wrong-footprint or wrong-net `W`
parts are reported. The `W` parts are not treated as components, so the strips and cuts don't
change.

Exit codes: 0 complete (every net joined, every link placed), 1 incomplete (links still to add or
place, unlinkable nets, rejected parts), 2 refused (bad input, conflicts, output = input).

**The same flow in KiCad:**
1. Open the placement board and click **Build strips**. The report lists the `W` links.
2. Add them to the schematic and press F8 in the *placement* board.
3. Click **Build strips** again: `<name>-stripforge.kicad_pcb` is rewritten with the links placed.
4. Click **Run DRC** (after pass 2, unconnected should be 0), then **Build sheet**.

### Build sheet: `stripforge sheet`

`stripforge sheet <built board> [--netlist <net>] [--config <toml>] -o <out>.html [--png] [--no-pdf]`
writes one self-contained, printable HTML file (Letter landscape, light background, works offline).
When Chrome/Chromium is found (`--chrome`, `$STRIPFORGE_CHROME`, `PATH`, or the macOS app) it
also writes `<out>.pdf`. `--png` adds `<out>.copper.png` / `<out>.component.png` previews. The two
view SVGs and `<out>.cuts.csv` are always written. The sheet has:

1. **Copper side (bottom), MIRRORED:** as seen with the board flipped over to cut (hole 1 on the
   right). It shows cuts (hole ✕, knife bar), solder points, link ends and slot jobs, with strip
   letters and hole numbers on every edge, an A1 corner mark and a 10-hole ruler.
2. **Component side (top):** part outlines, refs, values, pin-1 marks and links drawn as wires.
3. **Checklists in build order** with checkboxes:
   - cuts grouped by strip;
   - slot jobs ("file U16 0.025" (0.635 mm) toward U17 (toward the part centre)");
   - wire links (ref, from, to, length in holes, footprint, and "diagonal, 4 across and 3 down",
     "along the strip" or "to bus strip R"); both views draw every link at its real angle;
   - parts, low-profile first, with every pin's hole.
4. **Net check:** every net and every hole it must touch, for a continuity meter.
5. **Warnings:** knife cuts, courtyard overlaps, unlinkable nets, links still to add, and a banner
   if the file isn't a (current) StripForge build.

Large boards are split across pages.

### DRC: `stripforge drc`

`stripforge drc <board.kicad_pcb> [--no-parity] [--report drc.json] [--json]` runs
`kicad-cli pcb drc --format json --severity-all --schematic-parity` (kicad-cli 10.0.4; found via
`--kicad-cli`, `$KICAD_CLI`, `PATH` or the macOS app bundle) and classifies the result:

- **real problems** (exit 1): shorts, clearance, unconnected items, schematic parity, violations of
  a StripForge (`SF …`) rule, and any other error;
- **filtered, counted** (expected on stripboard): `track_dangling` (dead strip ends), the
  "footprint library not configured" warning (the footprints are embedded), and courtyard/library
  items of the drawn `SF_HOLES_*` hole footprints ("N stripboard-hole item(s)": the holes under
  parts are real holes);
- **reported, not failing**: a `W` link over a part courtyard (a wire can run under a part), and
  other warnings (silk), summarised by type.

**Config filters (`[drc]` in `stripboard.toml`):** pass `--config` (the plugin always does) and
the table's filters apply. Everything they suppress is still counted, one line per type or pair:
`filtered (config): 1 courtyards_overlap J2/C2, 2 pth_inside_courtyard J2/C2, 1 silk_overlap`.

```toml
[drc]
# KiCad 10 DRC type names, suppressed board-wide (a name KiCad doesn't have is warned about)
ignore = ["silk_overlap", "silk_over_copper", "silk_edge_clearance"]
# courtyard overlaps accepted for exactly these reference pairs (order doesn't matter): covers
# courtyards_overlap and pth_/npth_inside_courtyard between the two parts; others still count
allow_overlap = [["J2", "C2"], ["J2", "C3"]]
```

Unknown keys in `[drc]` are an input error (exit 2). The type names are the `type` values in
kicad-cli's JSON report (KiCad 10's list is in `stripforge.config.KICAD_DRC_TYPES`).

Schematic parity needs the `.kicad_sch` (and `.kicad_pro`) next to the board with the same name;
without it kicad-cli skips parity and the report says so. For a built board with another name
(`<name>-stripforge.kicad_pcb`), pass `--schematic <name>.kicad_sch`: DRC then runs on a shadow copy
of the project in a temp folder. The KiCad plugin does this automatically. After pass 1 the unconnected items are
exactly the links still to add; after pass 2 they should be 0. Exit 3 means kicad-cli was not found
(the tests needing it are skipped in CI).

### Slotted parts

Parts whose pins are not on the 2.54 mm grid along a strip (the MPD BH23APC 23A holder, BT1) are
listed in `slotted = ["BT1"]`. Their end holes are filed toward the part centre instead of
rejecting the part. The filing distance is the pad's offset from its hole plus half of how much
longer than wide its drill is: for the BH23APC (oval 1.635 × 1.0 mm slots 0.3175 mm inboard;
slot centres 32.385 mm apart, pins seat 31.75 mm apart) that is **0.025" (0.635 mm)** at each
end. Place the footprint with its origin on a hole; if one end is nearer its hole than the other,
analyze warns, e.g. "BT1 slot offsets 0.000/0.635 mm; shift -0.318 mm along the strip (toward lower
hole numbers) to centre it" (in KiCad: Move Exactly, X -0.3175).

### Bent legs: `[bend]` (the "Beckham tolerance")

Some parts can't sit on the holes but their legs bend far enough to reach them, like Kevin's DPDT
slide switch (SW2): 300 mil pin pitch along a row but 312 mil between the rows. Centred, each row
is 6 mil (0.1524 mm) off its strip, just over `snap_tol_mm` (0.15 mm). List such parts in a `[bend]`
table with the most a leg may be off its hole, in mm (6 mil = 0.1524 mm, 10 mil = 0.254 mm):

```toml
[bend]   # Beckham tolerance: parts whose legs can be bent onto the holes (bend it like Beckham)
SW2 = 0.16
```

- The value replaces `snap_tol_mm` for that part only, in any direction. For a part that is also
  `slotted`, the slot takes the along-strip offset and `[bend]` sets the across-strip tolerance.
- Values must be more than 0 and at most 0.5 mm; above 0.3 mm there is a warning (check the part
  really bends that far). A `[bend]`, `slotted` or `skip` entry for a part that isn't on the board
  is warned about.
- Analyze lists every leg to bend (`bend legs: SW2 [bend] 0.16 mm [pad 1 at C40: 0.152 mm (6.0 mil)
  across the strip; ...]`) and warns that the part was accepted with its bend tolerance. The build
  sheet's parts list says "bend legs up to 0.152 mm (6.0 mil) across the strip to fit the holes".
- `[bend]` parts are placed off the holes on purpose, so the best-fit shift never moves them.
- A rejected part now gets the matching hint: a miss across the strip suggests `[bend]` with a
  value (e.g. `SW2 = 0.16`), a miss along the strip suggests `slotted`, and a part whose pins match
  the pitch but sit off the grid is told how far to move it (KiCad: Move Exactly).

### Parts off the stripboard: `skip`

`skip = ["SW3", "J9"]` lists parts that are not on the stripboard (panel-mounted, hand-wired). They
are not snapped or rejected, their pads get no strips, and nothing is planned for them. Analyze
warns, per part, which nets must be hand-wired to it, and the build sheet lists them under "Wired
off-board". Nets they share with on-board parts are still split and linked as usual on the
board; the wire to the skipped part is up to you. (`offboard_refs`, the older name, still works;
the two lists are merged.)

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
  cli.py          command-line entry (analyze | snap | build | drc | sheet; plan is a stub)
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
  buildsheet.py   stripforge sheet: printable HTML/PDF build sheet, view SVGs/PNGs, cuts CSV
  backends/       file (.kicad_pcb), ipc (kipy; stub), swig_fallback (isolated, unused)
plugins/          KiCad 10 IPC plugin: plugin.json, sf_*.py entry scripts, stripforge_plugin.py, icons
tools/            make_pcm_zip.py (PCM package), make_icons.py (toolbar icons)
```

### Icon

The StripForge icon is `resources/icon.png`: Kevin's 64 × 64 px icon (the copper "S" with a
"StripForge" wordmark, 2026-09-27), used as the KiCad Plugin and Content Manager icon. The PCM
`metadata.json` schema has no icon field: the PCM takes the icon from `resources/icon.png` in the
package archive, and from an `icon.png` next to `metadata.json` in the metadata repository. The
toolbar icons in `plugins/icons/` (24 and 48 px, one per action with a letter badge) are made from
it by `python tools/make_icons.py`, cropped to the "S" because the wordmark can't be read at
toolbar size. The larger artwork (`assets/StripForge-icon-1024x1024.png`) is kept for reference.

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
