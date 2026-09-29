# StripForge: v0 Plan

*StripForge is a KiCad stripboard (Veroboard) layout tool. This sketch was originally drafted as the "KiCad Stripboard Layout Tool" v0 plan.*

*Status: v0 plan (2026-09-27). M1 and M2 are done. M3 is done as 0.1.0: `stripforge sheet` (a printable HTML/PDF build sheet) and a KiCad 10 IPC plugin with four PCB-editor actions, packaged for the PCM (§4.9, §4.10). The live-board IPC backend (§3) is still a stub; see §4.10 for why the plugin writes new files instead.*
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
  - **Applying the shift (M2 part A).** `stripforge snap <board> -o <out.kicad_pcb>` (dry run
    without `-o`; it refuses to overwrite its input) moves every snapped footprint by its best-fit
    translation: the midpoint of its pads' per-axis offsets, so on-pitch parts land exactly on
    their holes and a 2.50 mm part splits its 0.04 mm between its pads (0.02 mm each). Only the
    footprint's `(at …)` changes in the file. Slotted and rejected parts are not moved, and
    rotation is out of scope. In code: `analyze.best_fit_moves` / `analyze.apply_best_fit` on the
    in-memory board. Checked with `kicad-cli` 10.0.4: the moved board loads, DRC results are the
    same as for the input, and the pads follow the footprint.
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
  such pad is a **slot job**, reported as "file hole V16 0.025" (0.635 mm) toward V17 (toward the
  part centre)" (the pad offset plus half the oval drill's excess length, 2026-09-27) and kept in the
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
  - **Decision (M1):** `hole` style behaves the same way when there is no free hole between the
    two pads (adjacent holes, or every hole between them taken): it falls back to a knife cut and
    warns, rather than failing. Only `knife` style never warns.
  - **Decision (M1): unused pins keep their own strip pieces.** KiCad gives each unconnected pin
    its own `unconnected-(…)` net. The splitter treats these as real nets and cuts them off from
    their neighbours; merging them into a neighbouring net's strip would make KiCad's DRC flag
    the pad as shorting two nets (`shorting_items`). So an unused pin sitting between two used
    pins on one strip costs cuts; the placement hints show where.
  - When adjacent pins sit on adjacent holes (such as the IDC and J2 columns lying on the same
    row), only a knife cut is possible. Otherwise the placement should put the part *across*
    strips, as DIPs normally are. We report these cases so Kevin can rotate or move the part.
  - **Placement hints (M2 part A, `hints.py`).** A part *forces* a cut when two of its own pads
    with different nets share a strip. `analyze` lists each such part with its strips and spans,
    e.g. "R6 lies along strip O (O2–O6), forcing 1 cut; rotating it 90° would put its pins on
    separate strips (est. 1 cut saved; pins to R6.1 Q4, R6.2 M4)". The rotation is tried both
    ways about the centre of the part's pad bounding box, with a whole-hole shift of up to one
    pitch each way so odd pin spans land on the grid. It is only offered if every rotated pad is
    within `snap_tol` of a hole, inside the board and on a hole no other part uses, if the part
    then forces fewer cuts, and if the whole board needs fewer cuts. Savings are estimated per
    part (they don't add up across parts), and part bodies/courtyards are *not* checked. A summary
    line ends the report; the JSON has `hints` and `hints_summary`. On the fixture: J1, J2, C1–C3,
    D1, D2, D4, R6, R7 and F1 force 23 cuts; rotating C1–C3, D1, D2, D4, R6 or R7 saves about
    12 in total.
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
  *Update (2026-09-28, Kevin):* links now start and end at any free grid hole: along a strip, on
  diagonals (rotated `Link_P*` for whole-pitch lengths, off-pitch `Link_D*` otherwise) and as two
  links meeting on a bare bus strip, within `max_link_mm`, never crossing another link or passing
  over a pin. A proposal also records the end column, kind, rotation and bus strip.
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

- **As built (M2 part B, `links.py`, `writer.py`).**
  - Link footprints are Mildrew's `StripForge:Link_P2.54` … `StripForge:Link_P81.28` (1–32
    pitches), vertical at 0° with pad 1 on top. Pass 2 places them at rotation 0 with pad 1 on the
    upper hole (not 90/270: the footprints are already drawn along a column). Their pads 1 and 2
    form a jumper pad group (`jumper_pad_groups`), so KiCad treats a placed link as a connection;
    without it a link joined nothing in `kicad-cli` DRC.
  - Planner: a global greedy Kruskal over all split nets' piece groups. A candidate is a column
    where both pieces have a free hole (no pad, no cut, not used by another link), 1–32 strips apart.
    Ranking: no same-column overlap, fewest part courtyards crossed, fewest cut slides, fewest knife
    cuts created, length, leads passed over, then position (deterministic).
  - **Cut sliding (decision).** A cut can move within its gap (between the same two pads) to give a
    piece a landing hole: a hole cut moves to another free hole if there is one, else becomes a
    knife cut next to the landing hole (with a warning). Slot segments and holes already used by a
    link are respected, and knife-only boards only get knife cuts.
  - `W` footprints (ref `W<n>` or a `StripForge:Link_*` footprint) are excluded from the analysis,
    so adding them in pass 2 changes neither strips nor cuts. The pass-1 proposal is recomputed
    deterministically in pass 2 and compared with a saved `<out>.links.json` (a warning if they
    differ).
  - Outputs: `<out>.links.json` / `.csv` / `.txt` (ref, net, from/to labels, pitches, footprint;
    the text file has the schematic steps). A net that can't be joined by vertical links is an
    error naming its piece groups. Links along a strip (two pieces on one row) are not proposed.

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
  - We still leave the door open: Mildrew may define a board-only, pad-less `StripForge:CUT_Hole`
    / `StripForge:CUT_Knife` footprint (Not in schematic, excluded from BOM and position files) as a
    *selectable marker* in M2 or later, if refdes numbering and selection in the GUI turn out to
    matter. The validator must then check that each marker's position matches a real gap.
- **As built (M2 part B).** The markers are Mildrew's `StripForge:CUT_Hole` (on the hole) and
  `StripForge:CUT_Knife` (between the two holes), graphics on `User.1`, board-only and excluded
  from BOM and position files, so schematic parity ignores them. `stripforge build` embeds them in
  the board as KiCad does (refs `CUT<n>` for cut `X<n>`), so the board loads and DRCs with the
  library unconfigured; each marker is written at its gap from the same model, and a rebuild
  replaces them.

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
  - set severity to ignore for dangling dead-copper tracks, if needed. *(As built: dead strip ends
    and bare no-net strip give `track_dangling`; the DRC wrapper filters and counts them rather
    than changing the rule severity.)*
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
- **As built (M2 part B, `stripforge build`).**
  - Strip pieces with a net are written as B.Cu segments hole to hole at `strip_width_mm` (1.8 mm,
    measured) with `(net "name")`; **bare strip is written as copper with no net** (decision: it is
    physically there, and KiCad only reports `track_dangling` for it, which the DRC wrapper
    filters). One-hole pieces have no track.
  - **Existing copper (decision):** a track, arc or via StripForge did not write is refused (the
    input must be the placement board); StripForge's own strips (known uuid5s) and `CUT` markers are
    removed and rewritten, so the pass-2 build can start from the pass-1 output.
    On a built board (it has StripForge strips or `CUT` markers), a B.Cu segment lying along one
    strip row inside the grid is strip copper re-drawn by hand and is replaced like StripForge's
    own, with a warning (issue #2, M3: the markers are the authority for cuts, so moving a cut is
    moving its marker). Anything else hand-drawn is still refused.
  - The configured grid is clipped to the Edge.Cuts outline with a warning (the fixture's outline
    was 30 holes wide while X56 is 56, until Mildrew widened it).
  - The rules are copied to `<out>.kicad_dru`; the output never overwrites the input.
  - UUIDs are uuid5, so rebuilding unchanged input is byte-identical. Instead of a `stripforge:*`
    group, StripForge recognises its output by uuid (tracks) and footprint (`StripForge:CUT_*`).
- **IPC backend (M3).** Does the same on the live board through kipy: read footprints and pads,
  `update_items` for snapping, `create_items` for `Track` and `BoardSegment`, all in one commit (so
  one undo step), then `save()`. Only 10.0.1-and-earlier features are used.
- Both backends sit behind one `Backend` protocol, so the model code never touches KiCad APIs.

### 4.8 DRC

- `kicad-cli pcb drc --format json --severity-all [--schematic-parity] --units mm -o drc.json board.kicad_pcb`
  (kicad-cli 10.0.4; `--exit-code-violations` exists but `stripforge drc` decides the exit code
  itself). Parity needs the project's `.kicad_sch`/`.kicad_pro` next to the board under the same
  name; otherwise kicad-cli logs "Failed to fetch schematic netlist for parity tests." and skips
  it, which the report states.
- `drc.py` buckets the JSON into **shorts** (`shorting_items`, `tracks_crossing`), **clearance**,
  **unconnected**, **parity**, **stripboard rules** (`SF …` rules), **link courtyards** (a `W` over a
  part courtyard: reported, not failing) and **other**. Real problems (exit 1) are the first five
  plus any other error. **Filtered and counted**: `track_dangling` (dead strip ends, Kevin's
  decision) and the library-not-configured warning (footprints are embedded). Unconnected items
  are also grouped by net, and positions are given as hole labels as well as mm. Exit 3 when
  kicad-cli is not found; its tests are skipped in CI.
- **Per-part tolerances (2026-09-28):** `[bend]` ("Beckham tolerance") gives a part its own snap
  tolerance in any direction (across the strip when also slotted) and lists the legs to bend;
  `skip` (alias `offboard_refs`) leaves hand-wired parts out entirely. Added for Kevin's DPDT
  slide switch SW2 (312 mil rows on 300 mil holes: 0.1524 mm per row).
- **`[drc]` config table (2026-09-27):** `ignore` (KiCad DRC types, board-wide) and
  `allow_overlap` (reference pairs whose courtyard items are accepted) filter items into a
  "filtered (config)" bucket that is counted per type/pair, never silently dropped. Added for the
  X56 TPI fixture, where J2's carrier courtyard overlaps C2 and C3 by design.
- Measured on the real-parts fixture with `examples/x56.toml` (2026-09-27): pass 1 gives 0 shorts,
  0 clearance, 39 unconnected (exactly the links needed, net by net), 104 filtered `track_dangling`,
  and parity 0 against Mildrew's schematic; the simulated pass 2 (25 `W` links) gives 14
  unconnected, exactly the 13 unlinkable nets' remaining joins.

### 4.9 Build sheet (as built in M3)

`stripforge sheet <built board> [--netlist] [--config] -o <out>.html` (`buildsheet.py`) writes a
single self-contained HTML file (inline SVG and CSS, no scripts or network, light background, Letter
landscape print CSS). When Chrome/Chromium is found it also writes a PDF next to it (headless
`--print-to-pdf`). With `--png` it writes PNG previews of both views. It always writes the two view
SVGs and `<out>.cuts.csv`.

- **Header:** project, board file, board size (strips × holes, mm, hole range), date, StripForge
  version, and a count line (cuts, slot jobs, links placed, parts, nets, warnings).
- **1. Copper side (bottom), MIRRORED:** drawn as seen with the board flipped left-to-right to cut
  (hole 1 on the right), with a red banner saying so. It shows the strips, hole cuts (red ✕ on
  the hole), knife cuts (red bar between holes), solder points, link ends and slot filings. Strip
  letters are on both sides and hole numbers on top and bottom. A red corner mark shows hole A1,
  and there is a 10-hole ruler.
- **2. Component side (top):** part outlines (courtyard or fab box), refs, values, pin-1 squares,
  and links drawn as wires with their refs.
- Large boards split into pages of at most 60 holes × 36 strips, with labels on every page.
  The scale is capped at 2× and fits 250 × 160 mm.
- **3. Checklists in build order**, each line with a checkbox:
  - cuts grouped by strip (hole or knife);
  - slot jobs ("file U16 0.025" (0.635 mm) toward U17 (toward the part centre)");
  - wire links (ref, from, to, length in holes, footprint, net, placed or not);
  - parts, low profile first (links, resistors, diodes, then ICs/sockets, capacitors, headers,
    switches, other), with the hole of every pin.
- **4. Net check:** every net and every hole it must reach, for a continuity meter.
- **5. Warnings:** knife cuts, courtyard overlaps, unlinkable nets, links still to add, rejected
  parts, and a board cross-check.
- **Board cross-check:** the sheet re-plans from the board's parts and compares that plan with the
  strips and cuts in the file. If the file isn't built or differs, a red banner says so, so a stale
  sheet can't pass as current.
- Decisions: HTML plus headless Chrome instead of cairosvg or weasyprint (neither is on Kevin's
  Mac or KiCad's Python; Chrome is on both). Chrome on macOS writes the file and then doesn't
  exit, so StripForge polls for a stable file and then kills Chrome's process group. The planned
  `kicad-cli pcb export pdf --mirror` cross-check was left out.

### 4.10 KiCad plugin (as built in M3)

- **Format:** KiCad 10 IPC plugin. `plugins/plugin.json` (schema
  <https://go.kicad.org/api/schemas/v1>), runtime `python`, identifier
  `com.github.somerleddesign.stripforge`, five actions (scope `pcb`, toolbar buttons with 24/48 px
  icons):
  - StripForge: Analyze
  - Build strips
  - Run DRC
  - Build sheet
- **Runtime:** KiCad creates a venv for the plugin from `requirements.txt` (`kicad-python==0.8.0`,
  `tomli`) with `--system-site-packages`, so KiCad's wxPython is available for dialogs. KiCad 10
  ignores `args` for Python actions, so each action has its own entry script (`sf_<action>.py`).
  StripForge supports Python 3.9 because KiCad 10.0.4's bundled Python on macOS is 3.9.13.
- **Flow:**
  1. kipy gives the open board (`board.name`, `project.path`; checked against KiCad 10.0.4).
  2. A dialog offers to save first. The API has no "modified" flag, so the plugin always asks.
  3. It uses `stripboard.toml` next to the board, else the only other `*.toml` there if it is a
     valid StripForge config (noted in the report), else derives the grid from Edge.Cuts and
     points out the other `*.toml` files found.
  4. It exports a fresh netlist from `<project>.kicad_sch` with kicad-cli (path from
     `get_kicad_binary_path`).
  5. It runs the CLI code in-process and shows the report in a dialog, with "Show in Finder" and
     "Copy report" buttons. The sheet opens in the browser.
- **File writes, no live edits:** "Build strips" writes the board file with the tested writer, in
  place by default (§4.15); 0.1.0 wrote only a new `<name>-stripforge.kicad_pcb`. Live editing
  through the API was rejected:
  - KiCad 10.0.4's API can't place a library footprint (`place_footprint_from_library` is
    KiCad 11), so the embedded CUT markers and W links couldn't be written live.
  - There is no dirty flag.
  - A 1000+ track commit through the API is slow, and the file writer is already deterministic
    and tested.
- **DRC parity for the built sibling:** `stripforge drc --schematic` runs kicad-cli on a shadow
  copy named after the schematic (with sub-sheets, `.kicad_pro`, library tables and links to the
  project's folders), so parity works for `<name>-stripforge.kicad_pcb`.
- **PCM package:** `tools/make_pcm_zip.py` builds `metadata.json` + `resources/icon.png` +
  `plugins/**` (with the `stripforge` package, footprints and rules bundled). It is
  `type: plugin`, `runtime: ipc`, `kicad_version: 10.0`, schema v2.
- **Footprint library:** a plugin package can't register a footprint library (the metadata
  repository's validator only allows `footprints/*.pretty` in `library` packages), so the
  `StripForge` library for the schematic's W links needs a separate `library` package. PCM
  libraries get a `PCM_` nickname prefix by default, which is open.

### 4.11 Straight links first, and your own edits (as built)

Link cost is tiered before anything else: straight down a column < along a strip < bus strip with
straight drops < whole-pitch diagonal < off-pitch diagonal. To make a straight link possible the
planner may slide a cut within its gap (a piece grows into holes no other net needs), also for a
leg onto a bus strip. User edits are "locked": `[manual]` links/cuts/no_cut in the toml, and the
built board's CUT markers and on-hole `W` footprints when they differ from StripForge's own plan
(`respect_edits`). Locked cuts are never slid; locked links are laid first; only what they leave
unjoined is planned. Invalid edits are rejected with a warning (pin, cut or slot hole, short, wrong
net); a deleted cut that would short is put back. The plugin rebuilds a `-stripforge` board in place.

### 4.12 knife_cuts and trimming unused strip ends

`knife_cuts = ["SW2"]`: every cut next to a listed part's pins is a knife cut, placed to leave the
hole beside each pin on that pin's net; the planner never slides those cuts closer. After planning,
`trim_pieces` (default on) cuts each net piece back to its outermost used hole when that frees at
least `trim_min_free` (default 4) holes as bare strip. A retry with pieces trimmed first is tried
when a net is left unjoined.

### 4.13 Link cut list and lead-stretch suggestions (as built)

Every link's length is shown in inches, pad-to-pad (`LinkProposal.length_in`: span × 2.54 mm /
25.4, so whole pitches give 0.1" steps and diagonals their real length to 0.01"). The report,
`.links.txt` and the build sheet add a cut list grouped by length, shortest first, with a reminder
that the builder adds wire for both legs; `link_lead_allowance_in` adds a cut column (+2 × allowance).

`stretch.suggest` runs after the plan (report only; it never moves a part). Joins are the plan's
links, a bus strip's two links counting as one join. For each join, every leaded two-pin part (ref
prefix R/C/D/L/F/FB, two THT pads, not slotted or skipped) with a pin on the net is tried: the other
pin stays, the pin moves to a free hole (not a pad, cut, slot filing hole, live link end, or the
hole beside a `knife_cuts` pin) on a piece already carrying the net. Accepted when the net is still
one component (pieces joined by the remaining links, the pin counted at its new hole), the part's
new line crosses no link and passes no lead within `LEAD_CLEAR_NM`, it goes under no courtyard it
didn't already (`allow_under_parts` lifts this), and the span stays in limits (axial: +`max_pitches`,
no shorter than body + 2 mm; radial: +`radial_max_pitches`). Best = least extra span. Suggestions
are taken greedily and checked together (no shared part or hole, no crossing leads); unlinkable
nets are tried too. On the TPI fixture this finds R2.2 O10 -> P10 (span 4 -> 5) replacing the
TPICLK link; R3 for HV_RST would need a line under SW2 onto the hole its big pad overhangs.

### 4.14 Links from the board to the schematic (as built)

Links flow from the PCB to the schematic. `place_links = true` (or `build --place-links`) places each
proposed link's `Link_P*`/`Link_D*` footprint on its holes in the first build: both pads on the net,
`(locked yes)`, Value `Link`, and `(path "<sheet path>/<uuid5(link-symbol/Wn)>")` with the
sheetname/sheetfile of the parts on that net (the sheet of most parts, if the net has none).
`stripforge link-symbols` (`linksym.py`) reads the W footprints back from the board and writes one
`StripForge:Link` symbol per missing reference. The symbol takes the footprint's path uuid, its
Footprint, Value and Description, and in_bom/in_pos from its attributes, so F8 and DRC parity
match the two by path. Each pin gets a label with the net's name. `/Sheet/NAME` gives a local label
on that sheet; other nets give a global label (verified with kicad-cli 10.0.4: a global `GND`
label joins the power net). An unnamed `Net-(...)` also gets a global label of that name at one of
its pins; the pin position comes from the placed symbol's library pin, with library y up, then
rotation, then mirror. The symbol definition is embedded in `lib_symbols`. The output is a project
copy (library tables with `${KIPRJMOD}` made absolute) or in place with `<sheet>-pre-links.kicad_sch`
backups, rotated like the board's (`writer.rotate_backups`, all before any write). `place_links`
is on by default, and the plugin's **Add links to schematic** action runs this in place on the
project's root schematic, then says to close and reopen eeschema (it doesn't reload a file changed
on disk). No sym-lib-table entry is needed because the symbol is embedded. On Kevin's board all 33
netlist W paths and footprint IDs equal the board's `(path)` and lib IDs, so F8 matches them by
path and changes nothing. Existing W symbols (for example placed by hand) are completed, not
duplicated:
- a pin with nothing at its end (no label, wire end, junction, no-connect or other pin) gets a net
  label;
- the symbol's uuid is set to its footprint's path uuid when the sheet matches;
- the Footprint field is set to the board's.

A pin that already reaches a label of another net, directly or along wires, is a CONFLICT, and that
symbol is left alone. KiCad's
Update Schematic from PCB (`BACK_ANNOTATE`) only changes existing symbols; for a footprint with no
symbol it reports "Cannot find symbol for footprint".

### 4.15 Build in place, with rotating backups (as built)

F8 only runs in the project's own board opened through the project manager. In the separate
`-stripforge` board pcbnew is standalone ("Cannot update the PCB because PCB editor is opened in
stand-alone mode"), and that board would look for a `-stripforge.kicad_sch`. So `output =
"in_place"` (the default) builds into `<name>.kicad_pcb` itself. `output = "separate"` keeps the old
file, and a `-stripforge` board is still rebuilt in place.

- **Backups rotate (Kevin's rule, like logrotate):** just before every in-place write,
  `writer.rotate_backups` does the following:
  1. If `<name>-pre-stripbuild.kicad_pcb` exists, it renames `-pre-stripbuild-<n>` to `-<n+1>`,
     highest n first (gaps are kept), then the unnumbered file to `-1`.
  2. It copies the board as it is now (`shutil.copy2`) to `<name>-pre-stripbuild.kicad_pcb`.
  3. `backup_keep = N` (0 = unlimited) then deletes numbered backups with n ≥ N, leaving N files.

  A rename whose target exists (`os.rename` would silently replace it on POSIX), or that fails,
  raises `BuildError` before the board or `.kicad_dru` is written. The unnumbered backup is always
  the board just before the latest build. To revert one build: delete the board and rename the
  unnumbered backup back. A differing `.kicad_dru` with no StripForge rules is copied once to
  `<name>-pre-stripbuild.kicad_dru`. `BuildResult.backup`, `backups_shifted` and `backups_pruned`
  feed the reports (`writer.backup_note`).
- **Rebuilds** use the existing in-place logic: StripForge strips its own tracks, `CUT` markers and
  `SF_HOLES_*`, reads the user's markers and W links as locked edits, and rewrites them. The output
  stays deterministic.
- **Plugin:** kipy's `Board.revert()` (API `RevertDocument`) reloads from disk and silently drops
  unsaved edits, and the API has no dirty flag. So Build strips requires saving first (OK/Cancel),
  writes the file, and calls `revert()` if the file's mtime changed. If that fails, the report
  says to use File > Revert.
- **Pass 2 in one board:** after F8 adds the W footprints, Build strips again matches and places
  them. `link-symbols` defaults to `<name>.kicad_sch`, and DRC parity runs on `<name>.kicad_pcb` /
  `<name>.kicad_sch` directly, with no shadow copy. Link lists stay `<name>-stripforge.links.*`.

## 5. v0 scope

In scope:
- THT only; a single horizontal strip axis; a rectangular board.
- Manual placement plus automatic snapping.
- Automatic cuts, nets per piece, link *proposals* and link placement (two-pass).
- File backend plus `kicad-cli` DRC.
- SVG/PDF build sheet.

Out of scope:
- SMD; automatic placement; vertical-strip or mixed boards; 2-sided or IC-socket special boards;
  schematic writing (since added for W links only: §4.14).
- The IPC plugin UI (M3 is stretch).

## 6. Success criteria on the ATtiny10 TPI fixture (25 THT parts)

*The M1 fixture has 25 footprints and 36 nets (82 THT pads), not the 26 and 37 first assumed; it is
frozen in `tests/fixtures/tpi-m1/`. Since M2 part B, `examples/tpi-fixture/` is Mildrew's
real-parts board: 22 footprints, 41 nets, 86 pads, with BT1 slotted.*

1. All 25 footprints snap. C1, C2 and C3 (0.04 mm) and F1 (0.01 mm) are accepted and logged. All
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
- *Status:* writer, DRC wrapper and two-pass link flow done (M2 part B); mutation tests and
  Mildrew's sign-off still open.
- Mildrew signs off the DRU and runs board validation.
- *Exit:* criteria 3, 4, 6 and 7 pass.

**M3: Build sheet and IPC (stretch).**
- SVG/PDF build sheet and CSVs.
- IPC backend (live board, one-commit undo) and an optional IPC plugin wrapper (`plugin.json`).
- *Status (0.1.0):* the build sheet (§4.9), the IPC plugin and the PCM package (§4.10) are done.
  The live IPC backend was deferred on purpose (§4.10). Kevin's in-KiCad click test and the M2
  mutation tests are open.
- *Exit:* criterion 5 passes. Kevin's real build (criterion 8) is the final sign-off.

**Future: one StripForge dialog (after 0.1.x).**
- Replace the four toolbar buttons with **one "StripForge" button** that opens a single dialog
  where the decisions are made and saved to `stripboard.toml`: board size (rows, cols, origin),
  slotted parts (with the filing amount and the lopsided warning shown per part), DRC ignores and
  allowed courtyard overlaps, cut style.
- **Per-part choices in one table:** each footprint that doesn't sit cleanly on the holes gets a
  row with its offsets and a choice: on grid / **slotted** (file the end holes) / **bend** (the
  Beckham tolerance, with the leg bend shown in mm and mil) / **skip** (wired off-board) /
  **adapter** (a carrier board or socket; not implemented yet). The dialog suggests the fitting
  choice the way the rejection hints do, and writes `slotted`, `[bend]` and `skip`.
- **Analyze inside the dialog** (live report pane; Build strips and Run DRC as buttons there).
- **Built-in Sheet view:** a PDF-viewer-like preview of the build sheet (pages, zoom, copper /
  component side), instead of only opening the browser.
- Before writing it, look at how other KiCad / perfboard tools do dialogs and previews for code to
  reuse. **perfboard-studio / PerfStudio** (medinstech; code Apache-2.0, 3D meshes CC-BY-SA-4.0)
  is a candidate: Apache-2.0 code may be used in this GPL-3.0-or-later project with credit (keep
  its LICENSE/NOTICE text and name it in Credits); do not copy the CC-BY-SA meshes. Note it uses
  PySide6/Qt and Python 3.12+, while a KiCad 10 plugin has wxPython and Python 3.9, so ideas and
  pure-Python parts port more easily than UI code. GPL-compatible KiCad plugins are fine too.

**Post-M3 backlog: pre-existing mounting holes (Kevin, 2026-09-29; not in M3).**
- A `stripboard.toml` option for mounting holes already drilled in the stripboard, given either
  by hole label plus diameter (e.g. `C3`, `C65`, `X3`, `X65`, 3.2 mm) or by board coordinates in
  inches (e.g. `(0.3, 0.3)`, `(6.5, 0.3)`, `(0.3, 2.4)`, `(6.5, 2.4)`). The origin for inch
  coordinates has to be defined first (board corner / Edge.Cuts top-left, or hole A1).
- Effort: about 1–2 days. What it touches:
  - **Planning:** the holes (and any hole whose copper the drill removes) are excluded like slot
    holes: no pin, link end or hole cut may use them; a part over one is rejected with a hint.
  - **Cuts:** a drilled hole cuts its strip there, so the splitter treats it as a fixed cut (no
    extra cut needed, and nets on each side stay apart). A hole wider than the strip gap (3.2 mm on
    2.54 mm strips) also nicks the neighbouring strips: warn, or treat them as narrowed there.
  - **DRC:** write NPTH mounting-hole footprints (e.g. `MountingHole_3.2mm`) at those spots so
    KiCad checks clearance to strips, pads and links.
  - **Build sheet:** draw the holes on both views and list them (they are drilled, not cut: keep
    them out of the cut list and the cut count).

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
