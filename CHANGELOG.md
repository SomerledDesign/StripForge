# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Build sheet: tick the checklists off in the browser** (Kevin, building the TPI fixture). A
  click on a row or its box ticks it, strikes the row through except the Status, and sets the
  Status to "Cut" (cuts) or "Installed" (links and parts; parts get a Status column); click again
  to undo. Slot jobs, link lengths, off-board parts and nets tick and strike the same way. Ticks
  are kept in localStorage under `stripforge-sheet:<board>:<build hash>` (a hash of the
  checklists, so a reprint keeps them and a changed build starts afresh), with a progress count
  per list ("12/34 links") and a **Reset checklist** button. Print shows the ticks as they are.
  One inline script, no external files. The PDF stays static (Chrome's print-to-PDF makes no form
  fields): print it and tick by pen.

### Docs

- Roadmap: a new **M4** in Sketch.md §7 (one StripForge button and dialog; a Clean action that moves
  backups into `stripforge-backups/` and never deletes; diagonal links off by default with at most
  a one-hole shift). Pre-drilled mounting holes move to "Later". Plans only, no code.
- README and release notes: screenshots of Install from File in the Plugin and Content Manager.
- The footprint library path for a PCM install is `${KICAD10_3RD_PARTY}/plugins/com_github_somerleddesign_stripforge/...`:
  the Plugin and Content Manager names the folder with underscores, not dots as the docs said.

## [0.2.0] - 2026-09-29

First beta. KiCad 10 only; tested on one real board (an ATtiny10 programming fixture on a 24 × 56
hole stripboard), where it goes from placed parts to a clean DRC (no shorts, nothing unconnected)
and a printable build sheet.

### Highlights

- **Builds in your board.** Build strips saves the project's own board, backs it up
  (`<name>-pre-stripbuild.kicad_pcb`, older backups rotate) and writes the strips, cuts and wire
  links into it, so F8 keeps working.
- **Links placed for you.** The `W` wire links are placed on their holes, and the new **Add links
  to schematic** button puts their symbols in the schematic. No more copying links by hand.
- **Your edits are kept.** Move a cut marker or a link and build again: StripForge keeps what you
  did and fills in the rest.
- **Joins more nets.** Nets left unjoined get a second try after unused strip ends are trimmed,
  and a spare strip can carry a net between two pieces (a "bus strip").
- **Backups everywhere**: the board, each schematic sheet it changes, and the previous build
  sheet.
- New README with the step-by-step workflow, tips and screenshots (`docs/images/`).

The details follow, grouped by topic.

### Added

**Building in your board, and backups**

- `output` toml key: `"in_place"` (default) builds into the project's own `<name>.kicad_pcb`;
  `"separate"` writes `<name>-stripforge.kicad_pcb` as before. `build --separate` overrides it for
  one run, and `-o` is now optional.
- Rotating backups for in-place builds, like logrotate. Before every build the numbered backups
  move up one, highest first (`-pre-stripbuild-2` → `-3`, `-1` → `-2`), and `-pre-stripbuild`
  becomes `-1`. Then the board as it is now is saved as `<name>-pre-stripbuild.kicad_pcb`.
  - Nothing is overwritten: a failed or blocked rename stops the build before the board is written.
  - `backup_keep = N` keeps N backups in all; the default, 0, keeps every one.
  - A different existing `.kicad_dru` is kept once as `<name>-pre-stripbuild.kicad_dru`.
  - The CLI and plugin reports give the backup path, which backups moved, and how to undo.
- The build sheet keeps the previous sheet: when the new HTML differs, the old HTML and PDF are
  backed up as `<name>-stripforge.sheet-prev.html` / `.pdf` (rotating to `-prev-1`, ...,
  `backup_keep`).

**Wire links and the schematic**

- `place_links` (toml) / `stripforge build --place-links`: the build places every proposed `W`
  link footprint on its holes, with the net on both pads, locked, and with the schematic path of
  its future symbol. No ratsnest is left to wire.
- Plugin action **StripForge: Add links to schematic** (L badge icon). It writes a
  `StripForge:Link` symbol for every placed W footprint into the project's schematic, in place.
  Each symbol gets its Footprint field, the schematic path of its footprint, and net labels. Each
  changed sheet is backed up first as `<sheet>-pre-links.kicad_sch`, rotated like the board
  backups. The report says to close and reopen the Schematic Editor. The symbol is embedded, so no
  sym-lib-table entry is needed.
- The same from the command line: `stripforge link-symbols <board> [--schematic <root>]
  (--out-dir DIR | --in-place)`. Each symbol gets its reference, Footprint field, the uuid from the
  footprint's path (so F8 matches them) and net labels on both pins (local for `/Sheet/` nets,
  global otherwise; an unnamed net is named on one of its pins). `--out-dir` writes a copy of the
  project; `--in-place` uses the rotating `-pre-links` backups. KiCad's Update Schematic from PCB
  can't add missing symbols, so this step fills the gap.
- W symbols already in the schematic (for example placed by hand) are completed, never
  duplicated:
  - bare pins get net labels;
  - the uuid is set to the placed footprint's path, so F8 matches by path;
  - an empty or different Footprint is set to the board's;
  - a pin wired to another net is reported as a CONFLICT, and that symbol is left unchanged.
- `symbols/StripForge.kicad_sym`: the generic `StripForge:Link` symbol for wire links (2 passive
  pins, reference `W`, value `Link`, empty footprint, footprint filter `Link_*`). It is bundled in
  the plugin/PCM zip as `plugins/symbols/`.
- Links from any free hole to any free hole: along a strip, on a diagonal (rotated `Link_P*` for
  whole-pitch lengths, else the new off-pitch `Link_D*` family: 305 footprints, `Link_D3.59` …
  `Link_D81.16`), or two links meeting on a bare **bus strip** isolated by hole cuts. Options
  `max_link_mm`, `diagonal_links`, `off_pitch_links`, `bus_strips`. The report, links CSV/JSON
  (`kind`, `rotation`, `bus`) and build sheet show them.
- Link lengths in inches, pad-to-pad (pitches × 0.1"; diagonals to 0.01"): every `Wn` line in the
  build report and `.links.txt` (`W1  A8 -> L8  (1.1")`) and the build sheet's link list.
- A link cut list in the report, `.links.txt` and on the build sheet: one row per length, shortest
  first (Length | Qty | Links), to pre-cut and bend all links of a length in one go, with a
  reminder that the lengths are pad-to-pad and the legs need extra wire. `link_lead_allowance_in`
  (inches a leg, default 0 = off) adds a cut-length column.
- Lead-stretch suggestions (report only, on by default; `[stretch]` table: `enabled`,
  `max_pitches` = 6, `radial_max_pitches` = 2, `skip`, `allow_under_parts`): links that a longer
  lead on a two-pin leaded part could replace, with the pin, the new hole and the new span. In the
  report, `.links.txt`, `.links.json` (`lead_stretches`) and on the build sheet.

**Your own cuts and links**

- Move, add or delete `CUT…` markers and move `W` links in the built board, then build again:
  they are kept ("locked") and StripForge only links what is still unjoined. Or give them as text
  in a `[manual]` table (`links`, `cuts`, `no_cut`). Checked: a link on a pin, cut or slot hole, a
  link shorting two nets, a missing cut that would short (put back), a cut splitting a net that
  can't be joined. `respect_edits = false` plans from scratch. The report and build sheet mark
  your cuts and links "(yours)".

**Strips, cuts and holes**

- Build draws every stripboard hole: a board-only `SF_HOLES_<strip>` footprint per strip with a
  plated `B.Cu` pad per free hole on the strip's net (hole cuts as bare holes), so pcbnew and the
  3D viewer show the real board. `draw_holes`, `hole_drill_mm`.
- `knife_cuts = ["SW2"]`: every cut next to a listed part's pins is a knife cut, placed so the hole
  beside each pin stays on that pin's net (its big pad overhangs it); the planner never slides
  those cuts closer. Pins too close for that get a warning. Build sheet: "knife, per SW2 setting".
- Trimming: after planning, each net piece is cut back to its outermost used hole (pin or link
  end) when that frees at least `trim_min_free` (default 4) holes as bare strip (`trim_pieces`,
  default on).

**Parts that don't quite fit the grid**

- `[bend]` table, the "Beckham tolerance": per-part snap tolerance in mm (e.g. `SW2 = 0.16` for a
  312 mil row pitch on 300 mil holes), in any direction, across the strip for slotted parts.
  Analyze lists each leg to bend (mm and mil), the build sheet says "bend legs up to ...", and
  `[bend]` parts are never moved by the best-fit shift. Values over 0.5 mm are refused, over
  0.3 mm warned.
- `skip = [...]`: parts wired off-board (alias of `offboard_refs`): not snapped, no strips, a
  warning naming the nets to hand-wire, and a "Wired off-board" list on the build sheet.
- Better hints for a rejected part, by direction: across the strip suggests `[bend]` with a value,
  along the strip suggests `slotted = ["REF"]`, and a part that only sits off the grid is told how
  far to move it.
- Slotted parts: a warning when the slots point away from the part's centre (it is half a pitch
  off) or the part is placed lopsided ("BT1 slot offsets 0.000/0.635 mm; shift -0.318 mm along the
  strip ... to centre it").
- A warning for `slotted` / `[bend]` / `skip` entries naming parts that are not on the board.

**DRC**

- `[drc]` table in `stripboard.toml`: `ignore` (KiCad DRC types suppressed board-wide) and
  `allow_overlap` (reference pairs whose courtyard overlaps are accepted). Applied by
  `stripforge drc --config` and the plugin's Run DRC; suppressed items are counted in a
  "filtered (config)" line. Unknown keys are an error, unknown type names a warning.
- `stripforge drc` counts the stripboard-hole footprints' courtyard/library items as
  "stripboard-hole item(s)" instead of failing on them, and hints to run `link-symbols` when the
  only parity items are `W` footprints with no symbol.

### Changed

**Workflow**

- Build in place by default (Kevin's design). F8 only works in the project's own board opened from
  the project manager, so the separate `-stripforge` board broke the second pass ("PCB editor is
  opened in stand-alone mode") and looked for a `-stripforge.kicad_sch`. The plugin's Build strips
  saves the board first (OK/Cancel), writes into it, then reloads it in the PCB editor.
- `place_links = true` is now the default: the first build places the W link footprints, and
  **Add links to schematic** carries them into the schematic. `build --no-place-links` /
  `place_links = false` gives the old two-pass flow.
- `link-symbols --schematic` is optional (defaults to `<name>.kicad_sch` next to the board, also
  for a `-stripforge` board); `stripforge drc` finds `<name>.kicad_sch` for a `-stripforge` board
  by itself. Link lists are always `<name>-stripforge.links.{json,csv,txt}`.
- The plugin uses the only other `*.toml` next to the board (e.g. `X56.toml`) when there is no
  `stripboard.toml` and it is a valid config.

**Link planning**

- Straight links first: the planner ranks vertical < along the strip < bus strip with straight
  drops < whole-pitch diagonal < off-pitch diagonal, and slides a cut (extends a piece) so two
  pieces share a column or a piece reaches a bus. On the X56 test board: 34 links, no diagonals.
- Links never cross or touch another link and never pass over a part pin or another link's end
  (a bare wire would short); the planner retries unlinkable nets first for up to 4 rounds.
- Nets left unjoined after the first link pass get a second chance: the pieces are trimmed to
  their used holes and the planner tries again, so a strip freed by the trim can become their
  bus strip (Kevin's DAT1 on strip S). On the example board 3 more nets are joined (7 unlinkable
  instead of 10). The unlinkable message now lists only the ways that were tried (`diagonal_links`
  and `bus_strips` off say so).

**Looks**

- Placed links are easier to see (Kevin and Mildrew): every straight `Link_P*` footprint has an
  unfilled ring on `User.4` around each pad (colour User.4 yellow in KiCad to make them stand out;
  `Link_D*` have none). The `StripForge:Link` symbol is redrawn as a small wire bridge, with its
  pins ending 3.81 mm either side so the net labels sit clear of it.
- New StripForge icon (Kevin's 64 × 64 px "S" with wordmark) as `resources/icon.png` (PCM), and
  regenerated toolbar icons cropped to the "S".
- Slot jobs give the filing distance for the pad's drill (offset plus half an oval drill's excess
  length) in inches and mm, toward the part centre: the BH23APC reads 0.025" (0.635 mm), not
  0.32 mm. JSON `slot_jobs` gain `file_mm`, `file_in` and `inward`.

### Fixed

**Your edits**

- Moving a cut is just moving its `CUT` marker (issue #2): Build strips regenerates the strip
  tracks from the markers, and a strip track re-drawn by hand along a strip row (dragged, split or
  routed in pcbnew, so it lost StripForge's uuid) is now replaced with a warning instead of making
  the build refuse the board ("track/via item(s) that StripForge did not write"). Other hand-drawn
  copper (diagonal or vertical tracks, F.Cu, vias) is still refused.
- A board's `CUT` markers keep their numbers when it is built again and on the build sheet. Cuts
  added while planning (bus strips, trims) were numbered after the rest and then renumbered row by
  row, so a fresh board's sheet said "The board's cut markers differ from the model (moved or
  changed: CUT76, ...)" and its cut numbers didn't match the markers on the board.
- On an edited board the build sheet called every cut "(yours)", StripForge's own too. Now only a
  cut marker you moved or added is "(yours)"; one still where StripForge put it is not, and the
  build report counts them apart ("kept your 1 cut(s) ... (and StripForge's other 85 cut
  marker(s) where they were)"). A marker records where StripForge placed it; for a board built
  with an older version, a marker counts as StripForge's if StripForge would cut there too.
- The same for links: the Build strips report said "(yours, kept)" on every link of a rebuilt
  board. Now a link still where StripForge put it says "(kept)", and only one you moved or added
  says "(yours, kept)" (also on the build sheet). A link is yours only when that's clear: its
  holes differ from the link plan saved by the last build (you moved it), it's in no saved plan
  or you placed it as `REF**` (you added it). When unsure (no saved plan, or one from before
  0.2.0) it's StripForge's. The plan (`.links.json`, new `yours` and `yours_why` fields) records
  which is which. A plan saved without that record (before 0.2.0, or by a 0.2.0 pre-release,
  which guessed from a fresh plan and so took StripForge's links around your moved cuts) is
  checked once against the `-pre-stripbuild` backups: a link on other holes in an older backup
  that has stayed where you moved it is yours; the other guessed marks are cleared. Your links
  stay where you put them either way.
- Your links and cut markers (Kevin's DAT1 link that kept coming back):
  - a hole-cut marker under an end of your link no longer makes every build reject the link: the
    link wins, the marker is dropped with a warning, and the strip is cut elsewhere only where two
    nets would short (if the cut is needed exactly there, it is kept and the report says so);
  - a link the build can't use keeps its name and is reported as REJECTED (not "placed"); its
    name goes to a new link only when that one has the same footprint (so it can really move);
  - a link that no longer joins anything (an end on bare strip no other link reaches, like the
    second leg of an old bus strip) is removed from the board on rebuild, with a warning;
  - a link footprint placed by hand as `REF**` gets the next free W number, its pads' net, Value
    `Link` and a schematic path, so Add links to schematic and F8 handle it like the others.

**Add links to schematic**

- Net labels no longer overlap the Link symbols: the symbol's pins now end 3.81 mm either side
  (clear of the body) and the labels' text runs away from it. A sheet with the old symbol is
  updated on the next run: the new symbol is embedded, the labels move out to the new pin ends,
  and anything else on an old pin point gets a short wire, so every link stays on its net.
- The note above the link symbols is wrapped to four short lines so it fits the sheet (the paper
  size allows for it), and a second run no longer adds a second copy.
- Stale StripForge:Link W symbols (not on the built board and not in its link plan) are removed
  with their labels, after backing the sheet up, so F8 doesn't re-add a removed link.
- A W symbol's Description is updated from its footprint. When the board was re-planned (e.g.
  rebuilt from a backup) so a W ref now names a link on another net, the labels it put on that
  symbol's pins move to the new net instead of leaving parity errors (a pin with a wire of yours
  stays a conflict).
- The link report says to use Add links to schematic for links the build placed itself.

**Footprints and files**

- Link footprints' courtyard is a dumbbell: 0.25 mm around each pad and 0.25 mm either side of the
  0.6 mm wire, instead of a pad-wide rectangle, so a link is about as wide as a real wire. Update
  footprints on existing boards (Tools > Update Footprints from Library) to get the new outline.
- Rotated (diagonal) link footprints are written with 4-decimal angles, so rebuilding a board
  with placed links gives the same bytes. Before, a 6-significant-digit angle was re-rotated by
  0.0005° on each rebuild.

**Plugin**

- No more kipy `ApiError` traceback when KiCad is busy (an active tool or a dialog). Saving,
  reading the board and project, finding kicad-cli, and reloading the built board are retried for
  up to 10 s. If KiCad is still busy, a short message says to press Esc, save with Cmd+S (Ctrl+S)
  and click the action again; nothing is built. If only the reload after an in-place build fails,
  the report says to use File > Revert before saving.
- It no longer says "Wrote <name>-stripforge.kicad_pcb" for a refused build when an old output
  file exists.
- The Build strips confirmation no longer breaks a sentence in the middle ("delete the built
  board / and rename ..."); it now says to close the board and rename the backup back.

## [0.1.0] - 2026-09-27

M3: build sheet and KiCad plugin.

### Added

- `stripforge sheet <built board> [--netlist] [--config] -o <out>.html`: a printable,
  self-contained HTML build sheet, and a PDF when Chrome/Chromium is found. `--png` adds previews
  of both views. The two view SVGs and a cuts CSV are always written. The sheet contains:
  - a header;
  - the copper side, MIRRORED as held for cutting, with labels on every edge, an A1 mark and a
    ruler;
  - the component side (outlines, refs, values, pin-1, links as wires);
  - checklists in build order: cuts by strip, slot jobs, wire links, and parts low-profile first
    with every pin's hole;
  - a net continuity table and warnings;
  - pages split for large boards, and a banner when the board isn't built or differs from the plan.
- KiCad 10 IPC plugin (`plugins/`): four PCB editor actions: StripForge: Analyze, Build strips,
  Run DRC and Build sheet.
  - It gets the open board through kicad-python, offers to save, and uses `stripboard.toml` next to
    the board (or Edge.Cuts).
  - It exports the netlist with kicad-cli and shows the report in a dialog.
  - Build strips writes `<name>-stripforge.kicad_pcb` and never edits the open board.
- `stripforge drc --schematic <sch>`: schematic parity for a board whose name differs from the
  schematic's (runs on a shadow copy of the project).
- `tools/make_pcm_zip.py`: builds a deterministic PCM package zip and prints the sha256,
  download_size and install_size. `tools/make_icons.py` regenerates the toolbar icons.
- `docs/PCM-SUBMISSION.md`: how to submit to KiCad's official PCM repository.

### Changed

- Runs on Python 3.9 too (KiCad 10's bundled Python on macOS): `tomli` fallback, no `StrEnum` or
  `zip(strict=)`. CI tests 3.9, 3.11 and 3.12.
- `build` is split into `writer.prepare()` (plan only, shared with the sheet) and the writer.
- The library and rules are also found in the plugin bundle.
- `metadata.json` is now the real PCM metadata: identifier `com.github.somerleddesign.stripforge`,
  version 0.1.0, `runtime: ipc`, schema v2.

### Added

- M2 part B: board output and DRC.
  - `stripforge build <board> [--netlist] [--config] -o <out>`: snaps the parts, then writes the
    strip copper (B.Cu, hole to hole, `strip_width_mm`, on the piece's net; bare strip as no-net
    copper), leaves real gaps at cuts, embeds a `StripForge:CUT_Hole` / `CUT_Knife` marker per cut
    (refs `CUT<n>`, board-only), copies the rules to `<out>.kicad_dru` and writes the link proposal
    (`<out>.links.json/.csv/.txt`). Refuses foreign tracks, rebuilds its own output, never
    overwrites the input, deterministic output. Exit 0 complete, 1 incomplete, 2 refused.
  - Link proposal (pass 1, `links.py`): vertical links of 1–32 pitches
    (`StripForge:Link_P2.54`…`Link_P81.28`) joining each split net like a minimum spanning tree,
    avoiding shared holes, same-column overlaps and part courtyards, sliding a cut within its gap
    when that frees a landing hole; unlinkable nets are reported as errors. The report includes the
    steps to add the `W` links to the schematic.
  - Link placement (pass 2): `W` footprints brought in by F8 are matched by ref and net and placed
    on their holes; missing, extra, wrong-footprint and wrong-net links are reported.
  - `stripforge drc <board>`: runs `kicad-cli pcb drc --format json --severity-all
    --schematic-parity` and classifies shorts, clearance, unconnected, parity and `SF` rule
    violations (exit 1) against filtered noise (`track_dangling`, library-not-configured; counted)
    and link-courtyard items (reported). Exit 3 when kicad-cli is missing; kicad-cli tests skip.
  - `resources.py`: finds the StripForge library and rules (repo, `$STRIPFORGE_LIBRARY`,
    `$STRIPFORGE_RULES` or `--library/--rules`); warns when the `.kicad_dru` strip width differs
    from `strip_width_mm`.
  - Footprint library `footprints/StripForge.pretty` (32 `Link_P*` wire links, `CUT_Hole`,
    `CUT_Knife`) and DRC rules `rules/stripforge.kicad_dru` with `rules/gen_dru.py`, by Mildrew.
    The link footprints now declare pads 1 and 2 as a jumper pad group.
  - The StripForge icon (`assets/`) and a 64 × 64 plugin-manager icon (`resources/icon.png`).
  - Tests for the real-parts fixture, the link planner, the writer, pass 2 and the DRC classifier.

- M2 part A (the parts that don't need Mildrew's strip, cut and link footprints):
  - Hole labels: strips (rows) are letters `A..Z, AA..ZZ`, holes along a strip are numbered from 1,
    `A1` is top-left (the fixture runs `A1`–`Y30`). `grid.hole_label`, `parse_hole`, `row_label`,
    `parse_row_label`. All human-readable `analyze` output uses labels; the JSON keeps the 0-based
    `(col, row)` and adds `label` / `hole_label` fields.
  - `examples/x56.toml` for Kevin's X56 board (24 strips A–X × 56 holes, 142.24 × 60.96 mm).
    Parts outside the configured grid are reported as off board with the nearest label (text and
    JSON `off_board`), and a warning is given when the grid and Edge.Cuts disagree.
  - Slotted parts: config `slotted`, `slot_max_mm` (default 1.0 mm) and `slot_max_mm_by_ref`. A
    listed part's pad that is off its hole along the strip (within the allowance) and on the strip
    centreline becomes a slot job, "file hole V16 toward V17 by 0.318 mm", kept in
    `Analysis.slot_jobs` and the JSON `slot_jobs`. Cuts avoid the slotted hole's neighbour.
  - Best-fit moves: `analyze.best_fit_moves` / `apply_best_fit` move every snapped (non-slotted)
    footprint in the in-memory board by its best-fit shift; `stripforge snap <board> -o <out>`
    writes the result (dry run without `-o`, refuses to overwrite its input).
  - Placement hints (`hints.py`): parts whose own pads put different nets on one strip, with the
    cuts they force and, where a 90° rotation fits on free on-grid holes, the estimated cuts
    saved; a summary line, and `hints` / `hints_summary` in the JSON.
  - Byte-exact S-expression writing: `sexpr.parse` returns a `Document` that keeps the source
    text and node spans; unmodified parse-then-write is byte-identical for the fixture
    `.kicad_pcb` and `.net`, and edits re-emit only the changed nodes. Moving a footprint rewrites
    only its `(at …)` (pads are stored relative to it; checked with `kicad-cli` 10.0.4).
  - `FileBackend` can move footprints and save.

- M1 model and splitting, pure Python:
  - `sexpr`: S-expression reader/writer with query helpers; `board`: footprints, references,
    positions and rotations, pads (absolute position including footprint rotation, orientation
    relative to the footprint, net) and the Edge.Cuts outline from a KiCad 10 `.kicad_pcb`;
    `netlist`: `kicadsexpr` netlist parser.
  - `grid`: hole grid from the outline and pitch with `(col, row)` indexing; per-footprint
    snapping that maps each THT pad to its nearest hole within `snap_tol_mm` (default 0.15 mm),
    reports offsets and flags footprints that do not snap. Geometry is never modified.
  - `strips` and `splitter`: horizontal hole-to-hole strips, a net per occupied hole, one cut
    between every pair of neighbouring different nets (hole cut at the free hole nearest the
    midpoint, else a knife cut with a warning), pieces with one net or none, pieces per net and
    nets that need links. Conflicts: two nets in one hole, pads with no hole.
  - `validate`: pure-Python check for multi-net pieces and cuts at occupied holes.
  - `stripforge analyze <board> [--netlist] [--config] [--json]`: read-only report.
  - `config.load()` for `stripboard.toml`; grid size and origin now default to the Edge.Cuts
    outline (new keys `pitch_mm`, `hole_inset_mm`).
- ATtiny10 TPI fixture inputs (`examples/tpi-fixture/`: netlist and placed `.kicad_pcb`) with
  unit tests and an integration test on them.

### Changed

- `examples/tpi-fixture/` is now Mildrew's real-parts board (22 footprints, 41 nets, BT1
  slotted, Edge.Cuts widened to the X56 board, 56 × 24 holes); the M1 board is frozen in
  `tests/fixtures/tpi-m1/` for the M1/M2A tests.
- Strip width confirmed at 1.8 mm (caliper-measured on the X56 board); the `.kicad_dru` must be
  regenerated with `rules/gen_dru.py` whenever it changes.
- Library nickname `StripForge` everywhere (`StripForge:Link_P10.16`, `StripForge:CUT_Hole`, …).
- The `generate` CLI stub is replaced by `build`.

- Reports, warnings and cut positions use hole labels (`hole B26`, `between D3 and D4`) instead of
  `(col,row)`; nets needing links list their pieces.
- Knife cuts pick the middle segment that is not part of a slot.
- Sketch.md: decisions recorded for hole labels, the X56 board, slotted parts, unused-pin
  `unconnected-*` nets keeping their own strip pieces, hole-style cuts falling back to knife cuts
  with a warning, and the fixture's real size (25 footprints, 36 nets).
- `examples/stripboard.toml` now holds the TPI fixture's real grid (30 × 25 holes, origin
  51.27, 51.27 mm).

- Initial scaffold: the `stripforge` Python package (stub modules for the grid, strips, splitter,
  links, validation, DRC, build sheet and backends) and a stub `stripforge` CLI.
- Sketch.md design plan (v0), README, GPL-3.0-or-later license, contributing guide.
- Example board config (`examples/stripboard.toml`) and a placeholder for the ATtiny10 TPI fixture
  test case.
- GitHub issue and pull request templates, and CI running ruff and pytest on Python 3.11 and 3.12.
- KiCad Plugin and Content Manager `metadata.json` stub.
