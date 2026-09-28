# StripForge: v0 Plan

*StripForge is a KiCad stripboard (Veroboard) layout tool. This sketch was originally drafted as the "KiCad Stripboard Layout Tool" v0 plan.*

*Status: draft v0 plan (2026-09-27). Nothing here is implemented yet. Target: KiCad 10.0.4.*
*Owners: Jarvis (scaffolding, generator, net splitting, build sheet, planning) and Mildrew (EE: strip, cut and link footprints, DRC rules, board validation).*

Anything marked **[UNVERIFIED]** must be checked against a real KiCad 10.0.4 install during M0.

---

## 1. Goal

Take a KiCad project's real footprints and netlist. Produce a normal `.kicad_pcb` whose copper is
stripboard: one horizontal strip per row on **B.Cu** at a 2.54 mm pitch, built from hole-to-hole
track pieces. The tool then:

- snaps THT pads to the 2.54 grid, with a tolerance for parts that are slightly off pitch;
- gives each strip piece the net of the pads on it;
- suggests and marks a cut wherever two nets would share a strip;
- treats wire links as zero-ohm jumper footprints that also exist in the schematic, so schematic
  and board stay in sync;
- uses KiCad's own DRC and connectivity (through `kicad-cli`) to catch shorts, opens and
  schematic-parity errors;
- exports a copper-side build sheet (SVG, then PDF) that lists every cut and link.

SMD parts are out of scope for v0. Automatic *placement* is also out of scope: v0 lays copper
around a placement that Kevin makes (or that already exists).

## 2. Recommendation: build new, borrow ideas

**Build new.** We found no KiCad plugin or script, in the official PCM or on GitHub, that writes
stripboard copper into a `.kicad_pcb`. The KiCad PCM (`packages-v1.json`, 127 packages, checked
2026-09-26) has no stripboard or veroboard add-on. Its closest entry is *Breadboard Builder*.
Every real stripboard tool is a standalone app with its own board model. See the findings table in
the report and in §10.

What to borrow:

- **perfboard-studio** (Apache-2.0, active). Its `stripboard.py`, `connectivity.py` and
  `striproute.py` hold a clear stripboard model: a strip becomes segments at the cuts. It also has
  a KiCad netlist importer, a footprint-to-hole-pattern mapping, and a build guide. The license
  lets us borrow code with attribution. It is the best reference for algorithms and tests.
- **VeroRoute** (GPL; the exact version is **[UNVERIFIED]**), **kicad-breadboard** (GPL-3.0,
  SWIG action plugin for KiCad 9/10). Borrow ideas only. Don't copy code unless we choose to
  license under GPL.
- **License decision (2026-09-27):** StripForge is licensed **GPL-3.0-or-later**. Apache-2.0 code
  (perfboard-studio) can be incorporated into a GPL-3 project with attribution and its NOTICE
  kept. GPL-3.0 code (kicad-breadboard) is now license-compatible too; GPL-2.0-only code is not,
  so VeroRoute's exact license must still be checked before copying anything.
- **perfboard-router** has no license, so reading it for ideas is the only safe use.
  **VeroDesigner** and **vCad Online** had no license file at HEAD when checked, so treat them
  the same way.

## 3. KiCad 10 API reality (drives the architecture)

Sources: kicad-python 0.8.0 wheel (built against API `10.0.6`), https://docs.kicad.org/kicad-python-main/board.html,
https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/index.html,
https://docs.kicad.org/10.0/en/cli/cli.html.

| Need | IPC (kipy) on KiCad 10.0.4 | Fallback |
|---|---|---|
| Read footprints, positions, orientation | Yes: `Board.get_footprints()`, `FootprintInstance.position/orientation` | – |
| Read pads, pad nets, padstack/drill | Yes: `Board.get_pads()`, `Pad.net`, `Pad.padstack`. (Main docs say pad position is relative to its footprint; verify the 0.8.0 behaviour.) | – |
| Move or rotate existing footprints | Yes: `update_items` (fixed in 0.4.0) | – |
| Create tracks, set net | Yes: `Track` (start/end/width/layer/net) plus `create_items`; new nets through `Net.name` (0.4.0) | – |
| Graphics and text on user layers | Yes: `BoardSegment`/`BoardCircle`/`BoardText` on `BL_User_1…` with `create_items` | – |
| Undoable batch edits, save | Yes: `begin_commit`/`push_commit`, `save()` | – |
| Connectivity queries | Yes: `get_connected_items`, `get_items_by_net` (need KiCad **10.0.1+**) | – |
| Add a footprint from a library | **No** on 10.x: `place_footprint_from_library` is **KiCad 11** | Update PCB from Schematic (F8) in the GUI, or write the S-expression, or SWIG `pcbnew.FootprintLoad` |
| Import a netlist into the board | `import_netlist` is in the dev docs with no version tag. **Assume no** on 10.0.4 **[UNVERIFIED]** | F8 in the GUI; parse `kicad-cli sch export netlist` output ourselves |
| Run DRC | **No**. There is no DRC-run call in kipy 0.8.0 or in the main docs | `kicad-cli pcb drc --format json --schematic-parity --severity-all --exit-code-violations` |
| Create DRC markers | An `InjectDrcError` protobuf message exists in 0.8.0, but it has no Python wrapper and no documented version **[UNVERIFIED]** | Draw our own marker graphics on a user layer |
| Read or write custom DRC rules | `get/set_custom_design_rules` are **KiCad 11** | Write the `.kicad_dru` text file directly |
| Plot or export SVG/PDF | **No** on 9/10 (the IPC export jobs are KiCad 11) | `kicad-cli pcb export svg/pdf --mirror`, plus our own SVG renderer |
| Headless, no GUI | **No** on 9/10 (IPC needs a running GUI; headless IPC comes in KiCad 11) | Pure-Python `.kicad_pcb` file backend plus `kicad-cli` |
| Look up a layer by user name | `get_layer_by_name` needs **10.0.6** (not 10.0.4) | Use the enum `BL_User_1` directly |

Notes:

- `KiCad.check_version()` raises only when KiCad is *newer* than kipy. kipy 0.8.0 on KiCad 10.0.4
  passes that check, but any call gated at 10.0.6 or later (`flip_items`, `get_layer_by_name`, …)
  will fail. We keep a list of the calls we allow.
- SWIG `pcbnew` still ships in KiCad 10 but is **removed in KiCad 11** (per the kicad-python README). We
  keep SWIG out of v0 entirely unless M0 finds a hard need, and we isolate it behind a
  `backends/swig_fallback.py` stub.
- The "kipy" documented at zeo.dev is a *commercial fork* (Zeo) with `board.drc.run()` and more.
  It is **not** official kicad-python. Don't rely on it.

## 4. Architecture

```
            ┌──────────────── inputs ────────────────┐
 .kicad_sch ─► kicad-cli sch export netlist ─► netlist.py ─┐
 .kicad_pcb (after F8 + Kevin's placement) ─► backend.read ─┤
 stripboard.toml (board size, origin, cut style, tol) ──────┤
                                                            ▼
                                             grid.py   (snap + tolerance report)
                                                            ▼
                                             strips.py (rows → hole-to-hole segments)
                                                            ▼
                                             splitter.py (cuts, net per piece)
                                                            ▼
                                             links.py  (open-net detection, link proposals)
                                                            ▼
                                             validate.py (pure-Python pre-DRC)
                                                            ▼
                     backend.write (file backend: .kicad_pcb | IPC backend: live board)
                                                            ▼
                     drc.py → kicad-cli pcb drc (JSON) → classify shorts/opens/parity
                                                            ▼
                     buildsheet.py → copper-side SVG (+ PDF), cut/link CSV
```

### 4.1 Input

- **Netlist.** Primary source: `kicad-cli sch export netlist --format kicadsexpr`. It gives refs,
  footprint lib IDs, nets with ref/pin pairs, and symbol sheet paths and UUIDs (needed for
  parity). We cross-check it against pad nets read from the board.
- **Footprints and placement.** Read from a `.kicad_pcb` that was populated with *Update PCB from
  Schematic* (F8) and roughly placed by Kevin on a 2.54 grid. This keeps KiCad's own
  symbol-footprint links intact, which is the cheapest route to clean schematic parity. It also
  avoids re-implementing footprint-library resolution in v0.
- **Board config**, `stripboard.toml`: rows × columns (for example 25 × 64), grid origin, strip
  axis (horizontal), strip width, cut style (`hole`/`knife`/`auto`), snap tolerance, the user
  layer used for cut markers, and the refs that are off-board wire pads.
- **Kevin's board is an X56 (2026-09-27):** 24 strips (`A`–`X`) of 56 holes, 142.24 × 60.96 mm of
  holes. `examples/x56.toml` sets `rows = 24`, `cols = 56` and `origin_mm` (hole `A1`); with
  rows/cols/origin omitted the grid is still derived from Edge.Cuts. A pad outside the configured
  grid rejects its footprint as **off board**, reported with the nearest label (for example
  `off board (near Y3)`), and the analysis carries on with the other parts. A warning is given
  when the configured grid and the Edge.Cuts outline disagree.

### 4.2 Grid model

- A node is `(row, col)` and maps to `origin + (col·2.54, row·2.54)` mm. Internally we use integer
  nanometres, as KiCad does.
- **Hole labels (Kevin's decision, 2026-09-27).** Copper strips run horizontally. Strips (rows) are
  letters `A..Z`, then `AA, AB, …, ZZ` (spreadsheet style), top to bottom as seen from the
  component side. Holes along a strip (columns) are numbered from 1, left to right. The top-left
  hole is `A1`; the 30 × 25 TPI fixture ends at `Y30`, Kevin's X56 board at `X56`. Every
  human-readable report (cuts, links, snap reports, hints) uses labels; the JSON keeps the 0-based
  `(col, row)` too and adds a `label`/`hole_label` field. Code: `grid.hole_label`,
  `grid.parse_hole`, `grid.row_label`, `grid.parse_row_label`.
- **Per-footprint rigid snap.** Try the footprint's current orientation plus 90° steps, and pick
  the translation that minimises the *maximum* pad-to-node deviation. We never alter footprint
  geometry. If the best max deviation is ≤ `snap_tol` (default **0.15 mm**), we accept and log the
  deviation. Otherwise we reject with a clear error.
  - **C1** `CP_Radial … P2.50`, **C2/C3** `C_Disc … P2.50`: 0.04 mm short of pitch. Place one pad
    on a node; the other lands 0.04 mm off, which passes.
  - **F1** Littelfuse 395, 5.08 pitch, second pad 0.01 mm off-axis: passes.
  - **J2** is a 2×9 carrier with DIP numbering and 25.4 mm (10-pitch) column spacing. It is exact,
    but pin numbering must come from the footprint and never be assumed (DIP runs down one side
    and up the other).
  - IDC 2×5, P10.16 axials, DO-41 and 3 mm LEDs are all on-grid; we expect deviation 0.
- **Slotted parts (2026-09-27).** Some parts have pins that are not on a 2.54 multiple and cannot be
  shifted onto the grid; the MPD BH23APC 23A battery holder (BT1) has pads 0.3175 mm inboard of
  holes 1 and 14 of one strip. The config lists them (`slotted = ["BT1"]`) with an allowance
  (`slot_max_mm`, default 1.0 mm, less than half a pitch; per-ref overrides in
  `slot_max_mm_by_ref`). For a listed part a pad further off than `snap_tol` is accepted when it
  lies along the strip (`|dx| ≤ slot_max`) and on the strip centreline (`|dy| ≤ snap_tol`). Each
  such pad is a **slot job**, reported as "file hole V16 toward V17 by 0.318 mm" and kept in the
  analysis (`Analysis.slot_jobs`, JSON `slot_jobs`) for the M3 build sheet. The hole a slot points
  toward is never used for a hole cut, and knife cuts avoid the slotted segment where they can.
  Slotted parts are never moved by the best-fit shift.
- An off-grid pad still connects: every strip track endpoint sits *on the node*, and a node
  0.04 mm from the pad centre is well inside the pad copper. **[UNVERIFIED]** that KiCad's
  connectivity treats a track endpoint inside a pad (not at its exact centre) as connected. We
  expect it does; test in M0.
- **Occupancy map**: node → `(ref, pad, net)`. Two pads on one node is a hard error.

### 4.3 Strip model and net splitting

- Each row is a strip, modelled as `C-1` **segments** `(r, c)→(r, c+1)`. Each segment is either
  present or cut. On the board each present segment is one `B.Cu` track from node centre to node
  centre. KiCad joins collinear tracks at their shared endpoints.
- **Cut placement.** Walk each row's occupied nodes in column order. For each consecutive pair of
  pads `a` and `b` whose nets differ, at least one cut is required somewhere between them:
  - `hole` style (spot-face cutter; the default, following perfboard-studio's reasoning): choose a
    *free* hole strictly between `a` and `b`, nearest the midpoint. Both segments touching that
    hole are removed, and the hole is marked unusable.
  - `knife` style: remove one segment between them, choosing the middle one.
  - `auto`: use `hole` when a free hole exists, otherwise fall back to `knife` and emit a warning
    that it is a knife cut at 2.54 mm.
  - When adjacent pins sit on adjacent holes (such as the IDC and J2 columns lying on the same
    row), only a knife cut is possible. Otherwise the placement should put the part *across*
    strips, as DIPs normally are. We report these cases so Kevin can rotate or move the part.
- **Net per piece.** After the cuts, each maximal run of present segments is a *piece*. Its net is
  the set of pad nets on its nodes:
  - exactly 1 net: assign it;
  - 0 nets: leave it unassigned (dead copper, which is still drawn because it physically exists);
  - 2 or more nets: impossible after cut placement, so this raises an internal error.
- **Minimise cuts (later).** v0 uses the greedy placement above. That gives the minimal *count*,
  since each differing adjacent pair needs exactly one cut. Position choice is heuristic.

### 4.4 Links (wire jumpers)

- After splitting, run a union-find per net over pieces. A net whose pads end up on more than one
  piece is an **open** and needs links.
- **Proposal.** Build a minimum spanning tree over each net's pieces. Prefer *vertical* links in a
  column where both pieces have free holes, with a length of k × 2.54 mm chosen from Mildrew's link
  family (for example `Link_P5.08 … Link_P25.40`). Each proposal records row A, row B, column, the
  link footprint, and the net.
- **Schematic sync (Kevin's decision).** Links are 0 Ω jumper *symbols* (ref prefix `W`) whose two
  pins sit on the **same net**, with the footprint taken from the link family. KiCad 10 has no
  schematic IPC, so v0 is **two-pass**:
  1. The tool writes `links_proposed.csv` and a human-readable list. Kevin adds `W1…Wn` to the
     schematic and presses F8. Links that already exist in the schematic are placed like any other
     part.
  2. The tool places the `W` footprints at their proposed holes (matched by ref) and re-validates.
  - An optional feature for M3+ would insert the `W` symbols into the `.kicad_sch` text directly.
    That is risky, so it is not in v0.
  - The alternative is board-only links (the "Not in schematic" attribute). That breaks the
    board/schematic sync Kevin wants, so it is rejected for v0.

### 4.5 Cut representation (decision)

**A cut is a real gap in the B.Cu track pieces, plus a marker graphic on a user layer, grouped
together.**

- The **gap is the electrical truth**. KiCad's connectivity and DRC see exactly what the physical
  board will have.
- The **marker** is a cross or circle on `User.1`, which we rename "Strip.Cuts" in the board file,
  plus an optional label (`X12`, placed at hole `C12`, say). It makes the cut visible in the editor and plottable for the
  build sheet.
- **Why not a "cut" footprint as the primary representation?**
  - A footprint with no pads is invisible to DRC and connectivity, so it adds nothing to checking.
    It also drifts out of sync if someone moves it without moving the gap.
  - A footprint *with* pads would create fake nets or copper, and it would need to be in the
    schematic or marked "Not in schematic".
  - We still leave the door open: Mildrew may define a board-only, pad-less `Stripboard:CUT_Hole`
    / `CUT_Knife` footprint (Not in schematic, excluded from BOM and position files) as a
    *selectable marker* in M2 or later, if refdes numbering and selection in the GUI turn out to
    matter. The validator must then check that each marker's position matches a real gap.

### 4.6 How DRC flags a strip carrying two nets (decision)

We make KiCad's **built-in** electrical checks do the work rather than inventing a custom rule:

- The generator never leaves an uncut path between different nets. If a strip piece carrying net A
  touches a pad on net B, for example because a part was moved after generation or a cut was
  deleted, KiCad reports **"Items shorting two nets"** (`shorting_items`, an Error by default; see
  the KiCad 10 PCB manual, Electrical DRC checks). If a piece is edited so tracks of two nets
  overlap, the same check fires, or `tracks_crossing`/`clearance` does.
- An **open** (a missing link, or an extra cut that splits a net) shows up as
  **unconnected items** (ratsnest).
- A **link out of sync** with the schematic shows up in the `--schematic-parity` checks (missing
  or extra footprint, net mismatch).
- **Custom `.kicad_dru` rules** (Mildrew) are only for stripboard hygiene, never for net-sharing:
  - B.Cu track width equal to the strip width;
  - disallow tracks and vias on F.Cu (links are footprints, not tracks);
  - a pad-to-track clearance relaxation if large pads trip clearance against the *neighbouring*
    strip;
  - set severity to ignore for dangling dead-copper tracks, if needed.
  - All rule syntax needs Mildrew's validation on 10.0.4 **[UNVERIFIED]**.
- **Belt and braces**: `validate.py` runs the same short/open/parity logic in pure Python before
  KiCad does, so problems fail fast and give better messages. It also re-reads the written board
  to confirm the nets per piece.

### 4.7 `.kicad_pcb` output

- **File backend (v0 default, headless, testable on the box).** Round-trip the F8-populated board
  with a small, lossless S-expression reader and writer that preserves unknown nodes.
  **Byte-exact (M2 part A):** `sexpr.parse` keeps the source text and every node's span, so an
  unmodified parse-then-write returns the input byte for byte (tested on the fixture `.kicad_pcb`
  and `.net`). Only edited nodes are re-emitted: their untouched children and whitespace still come
  from the source, replaced atoms are written fresh, and new child lists are rendered in KiCad's
  tab-indented style. Moving a footprint rewrites only its `(at x y [angle])`: KiCad stores pad,
  text and graphic positions relative to the footprint (pad *angles* are absolute, but a
  translation does not change them). It then:
  - moves footprints to their snapped positions;
  - removes the tool's previous output (tagged by a group named `stripforge:*`);
  - appends `segment` nodes on `B.Cu` with nets, cut markers on the user layer, row and column
    labels, `Edge.Cuts` for the board outline, and a hole-grid drawing on `B.Fab` or a user layer;
  - uses deterministic UUIDs (uuid5 from row/col/kind), so re-runs give clean diffs.
  - **[UNVERIFIED]**: the exact KiCad 10 syntax for segment nets (net code vs name) and the layer
    table. M0 must inspect a board saved by KiCad 10.0.4.
- **IPC backend (M3).** Does the same on the live board through kipy: read footprints and pads,
  `update_items` for snapping, `create_items` for `Track` and `BoardSegment`, all in one commit (so
  one undo step), then `save()`. Only 10.0.1-and-earlier features are used.
- Both backends sit behind one `Backend` protocol, so the model code never touches KiCad APIs.

### 4.8 DRC

- `kicad-cli pcb drc --format json --severity-all --schematic-parity --exit-code-violations -o drc.json board.kicad_pcb`.
  Exit code 0 means clean and 5 means violations were found.
- `drc.py` parses the JSON and buckets violations into **short**, **open**, **parity**,
  **stripboard-rule** and **other**. The report is addressed in hole labels ("F23") as
  well as mm.
- An allowlist of expected warnings (for example, silk over holes) lives in the repo.

### 4.9 Build sheet

- A custom SVG renderer (`buildsheet.py`) draws the **copper side, mirrored**:
  - strips, with cut holes and knife cuts shown as red ✕ marks;
  - pads as dots labelled `ref.pin`;
  - links drawn dashed, as ghosted component-side items;
  - strip letters (`A…`) and hole numbers (`1…`) that match the physical board and the reports;
  - a board outline and an orientation marker (a notched corner), so the classic "built it
    mirrored" error is hard to make.
- A second page shows the **component side**: part outlines, refs and link positions.
- Tables: a cut list (`X1 C12 hole`), a link list (`W3 D18–H18 10.16 mm`), and
  a parts list.
- PDF comes from the SVG via `cairosvg` (optional dependency). As a cross-check, also run
  `kicad-cli pcb export pdf --mirror --layers B.Cu,User.1,Edge.Cuts,B.Fab` so KiCad renders the
  same board.
- Print at 1:1 scale; the sheet includes a 10-hole ruler for checking scale.

## 5. v0 scope

In scope:
- THT only; a single horizontal strip axis; a rectangular board.
- Manual placement plus automatic snapping.
- Automatic cuts, nets per piece, link *proposals* and link placement (two-pass).
- File backend plus `kicad-cli` DRC.
- SVG/PDF build sheet.

Out of scope:
- SMD; automatic placement; vertical-strip or mixed boards; 2-sided or IC-socket special boards;
  schematic writing.
- The IPC plugin UI (M3 is stretch).

## 6. Success criteria on the ATtiny10 TPI fixture (26 THT parts)

1. All 26 footprints snap. C1, C2 and C3 (0.04 mm) and F1 (0.01 mm) are accepted and logged. All
   other parts deviate by ≤ 0.005 mm. No part is rejected.
2. Every B.Cu piece carries exactly the net of the pads on it. The pure-Python validator reports
   0 multi-net pieces.
3. `kicad-cli pcb drc --schematic-parity` on the final board, with the W links present in the
   schematic, gives **0 errors** for shorts, crossings, clearance, unconnected items and parity.
   Warnings are allowlisted only.
4. **Mutation tests.** Deleting any one cut makes DRC report `shorting_items`. Deleting any one
   link, or adding a cut inside a net, makes DRC report unconnected items. This proves DRC
   actually guards the layout.
5. The build sheet lists every cut and link. Its counts equal the board's. It is mirror-correct
   (checked against an asymmetric test board) and prints at 1:1.
6. Re-running on unchanged input gives a byte-identical `.kicad_pcb`.
7. The board opens in KiCad 10.0.4 with no upgrade prompt or load warnings.
8. Kevin builds the fixture from the sheet with no copper-side rework. This is the final
   acceptance test and it is manual.

## 7. Milestones

**M0: Foundations.**
- Jarvis:
  - repo skeleton (done, see `src/`), CI with pytest on the box;
  - S-expression round-trip on a sample KiCad 10 board;
  - netlist parser;
  - fixture intake: Kevin exports the TPI netlist and an F8-populated `.kicad_pcb` to the box.
    *Needs Kevin's OK; we don't touch his Mac or Dropbox without it.*
- Mildrew:
  - strip parameters (width, clearance) and the link-family spec (lengths, footprint names);
  - the cut-marker spec;
  - a first `.kicad_dru`.
- Both: verify every **[UNVERIFIED]** item, including the 10.0.4 file syntax, track-endpoint-in-pad
  connectivity, and kipy 0.8.0 against 10.0.4 in a smoke test (needs Kevin's approval to run on
  his Mac).
- *Exit:* round-trip is lossless on the fixture board, the netlist parses, and there is a written
  answer to each item.

**M1: Model and splitting (pure Python).**
- `grid`, `strips`, `splitter`, `links` (proposals only) and `validate`.
- Unit tests on synthetic boards; snap tests for C1, C2, C3 and F1; JSON plan output.
- *Exit:* criteria 1 and 2 pass on the fixture in the pure-Python model.

**M2: Board output and DRC.**
- File-backend writer; `drc.py` wrapping `kicad-cli`; the two-pass link flow; mutation tests.
- Mildrew signs off the DRU and runs board validation.
- *Exit:* criteria 3, 4, 6 and 7 pass.

**M3: Build sheet and IPC (stretch).**
- SVG/PDF build sheet and CSVs.
- IPC backend (live board, one-commit undo) and an optional IPC plugin wrapper (`plugin.json`).
- *Exit:* criterion 5 passes. Kevin's real build (criterion 8) is the final sign-off.

## 8. Jarvis / Mildrew split

| Area | Jarvis | Mildrew |
|---|---|---|
| Repo, packaging, CI, CLI | Owns | Reviews |
| Netlist and `.kicad_pcb` I/O, backends (file, IPC) | Owns | – |
| Grid snap and tolerance policy | Implements | Sets tolerance and signs off per-part exceptions |
| Strip model, cut placement, net splitting | Owns | Reviews physical realism (hole vs knife cut) |
| Link proposal algorithm | Owns | Link-family lengths and limits |
| Strip, cut-marker and **zero-ohm link footprint family** | Consumes | **Owns** |
| Custom DRC rules (`.kicad_dru`), severities, allowlist | Wires into `drc.py` | **Owns** |
| Board validation and electrical review of the fixture | Supplies reports | **Owns** |
| Build sheet (SVG/PDF), CSV export | Owns | Reviews for buildability |
| Planning and milestones | Owns | Input |

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| IPC gaps on 10.x (no DRC, no library placement, no export, no headless) | Can't do it all in IPC | File backend plus `kicad-cli` is the v0 path; IPC is an optional M3 backend |
| kipy 0.8.0 is built against 10.0.6 but Kevin runs 10.0.4 | Runtime errors on newer calls | Allowlist of calls ≤ 10.0.4; smoke test; consider moving Kevin to 10.0.6 (same 10.0 series; his call) |
| SWIG removed in KiCad 11 | Code rot | No SWIG in v0; isolate any fallback |
| Unknown 10.0.4 file-format details (net refs on segments, layer table) | Invalid board | M0 inspection of a 10.0.4-saved board; always re-open with `kicad-cli pcb drc` in tests |
| Connectivity for off-centre track endpoints in a pad | False opens | M0 test; if it fails, add a short stub track from the node to the pad centre |
| Large pads (IDC, J2, F1) against the neighbouring strip trip clearance | False DRC errors | Strip width and clearance tuned by Mildrew; targeted DRU relaxation |
| Links require a manual schematic edit (no schematic IPC in 10) | Friction, and sync mistakes | Two-pass flow and a clear CSV; parity DRC catches mistakes; schematic writer later |
| Hole-cut style needs a free hole between different nets | Some placements are infeasible | `auto` mode with a knife fallback and warnings; placement hints |
| Pin numbering on J2 (DIP-style vs header numbering) or the IDC | Wrong strips | Always use footprint pad numbers and netlist pins; never assume a pinout |
| Mirrored build sheet | Board built wrong | Asymmetric test board, orientation notch, labels matching the physical board |
| License contamination (GPL-2.0-only, or no-license code) | Legal | Project is GPL-3.0-or-later; borrow code only from Apache-2.0 or GPL-3-compatible sources, with attribution; ideas only from no-license code |
| Manual placement quality drives the number of cuts and links | Messy boards | Report metrics (cuts, links, board area); auto-placement is a post-v0 item |

## 10. Existing tools (summary; details with links in the report)

| Tool | What | KiCad | Status | License | Verdict |
|---|---|---|---|---|---|
| perfboard-studio (github.com/medinstech/perfboard-studio) | Desktop app: KiCad netlist → perfboard/stripboard layout, DRC/LVS, build guide | Netlist import only | Very active (pushed 2026-09-26) | Apache-2.0 | **Borrow** model, algorithms and tests |
| perfboard-router (github.com/aluisioalves123/perfboard-router) | KiCad `.net` → perfboard placement, routing and jumpers; SVG output | Netlist only; doesn't read `.kicad_pcb` | Active (2026-09) | None | Ideas only |
| VeroRoute (sourceforge.net/projects/veroroute) | Qt stripboard/perfboard/PCB app; imports KiCad netlists | Netlist import | Maintained | GPL (version unverified) | Ideas only |
| VeroDesigner (github.com/blazethablunt/VeroDesigner) | Browser auto vero-layout generator; beta `.kicad_sch` import | Schematic import (beta) | Active (2025–26) | No license file | Ideas only |
| vCad Online (github.com/idoroseman/vcad-online) | Browser stripboard editor; KiCad netlist import; short/open check | Netlist import | Active | No license file | Ideas only |
| kicad-breadboard (github.com/kerstensrobin/kicad-breadboard) | KiCad 9/10 PCM plugin (SWIG `pcbnew`) for virtual breadboards | 9.0+ | Active (2026-09-15) | GPL-3.0 | Plugin packaging and validation UX; ideas only |
| "Stripboard meets KiCad" (hackaday.io/project/166863) and forum/blog manual methods | Manual: strips as tracks, cuts on a user layer | Any | Articles | – | Confirms the representation |
| VeeCAD, DIYLC, LochMaster, Fritzing | Non-KiCad stripboard editors | – | – | VeeCAD freeware/paid (Windows); DIYLC GPL-3 (Java); LochMaster commercial (Abacom); Fritzing GPL | Not relevant beyond UX ideas |
