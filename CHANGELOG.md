# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Plugin action **StripForge: Add links to schematic** (L badge icon). It writes a
  `StripForge:Link` symbol for every placed W footprint into the project's schematic, in place.
  Each symbol gets its Footprint field, the schematic path of its footprint, and net labels. Each
  changed sheet is backed up first as `<sheet>-pre-links.kicad_sch`, rotated like the board
  backups. The report says to close and reopen the Schematic Editor. The symbol is embedded, so no
  sym-lib-table entry is needed. `link-symbols --in-place` uses the same backups (no more
  timestamped `.bak`).
- `link-symbols` / Add links to schematic complete W symbols that are already in the schematic
  (for example placed by hand) and never duplicate them:
  - bare pins get net labels;
  - the uuid is set to the placed footprint's path, so F8 matches by path;
  - an empty or different Footprint is set to the board's;
  - a pin wired to another net is reported as a CONFLICT, and that symbol is left unchanged.


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

- `symbols/StripForge.kicad_sym`: the generic `StripForge:Link` symbol for wire links (2 passive
  pins, reference `W`, value `Link`, empty footprint, footprint filter `Link_*`). It is bundled in the
  plugin/PCM zip as `plugins/symbols/`.
- `place_links` (toml) / `stripforge build --place-links`: the first build places every proposed
  `W` link footprint on its holes, with the net on both pads, locked, and with the schematic path
  of its future symbol. No ratsnest is left to wire. It is off by default.
- `stripforge link-symbols <board> --schematic <root> (--out-dir DIR | --in-place)`: writes a
  `StripForge:Link` symbol for every `W` footprint on the board that the schematic lacks. Each gets
  its reference, Footprint field, the uuid from the footprint's path (so F8 matches them) and net
  labels on both pins (local for `/Sheet/` nets, global otherwise; an unnamed net is named on one of
  its pins). Output goes to a copy of the project or in place with timestamped `.bak` backups. KiCad's
  Update Schematic from PCB can't add missing symbols, so this step fills the gap.
- `stripforge drc`: a hint to run `link-symbols` when the only parity items are `W` footprints
  with no symbol.

- Link lengths in inches, pad-to-pad (pitches × 0.1"; diagonals to 0.01"): every `Wn` line in the
  build report and `.links.txt` (`W1  A8 -> L8  (1.1")`) and the build sheet's link list.
- Link cut list in the report, `.links.txt` and on the build sheet: one row per length, shortest
  first (Length | Qty | Links), to pre-cut and bend all links of a length in one go, with a reminder
  that the lengths are pad-to-pad and the legs need extra wire. `link_lead_allowance_in` (inches a
  leg, default 0 = off) adds a cut-length column.
- Lead-stretch suggestions (report only, on by default; `[stretch]` table: `enabled`,
  `max_pitches` = 6, `radial_max_pitches` = 2, `skip`, `allow_under_parts`): links that a longer
  lead on a two-pin leaded part could replace, with the pin, the new hole and the new span. In the
  report, `.links.txt`, `.links.json` (`lead_stretches`) and on the build sheet.

- `knife_cuts = ["SW2"]`: every cut next to a listed part's pins is a knife cut, placed so the hole
  beside each pin stays on that pin's net (its big pad overhangs it); the planner never slides those
  cuts closer. Pins too close for that get a warning. Build sheet: "knife, per SW2 setting".
- Trimming: after planning, each net piece is cut back to its outermost used hole (pin or link end)
  when that frees at least `trim_min_free` (default 4) holes as bare strip (`trim_pieces`, default
  on). A retry with the pieces trimmed first is tried when a net is left unjoined.

- Your own cuts and links are kept: move, add or delete `CUT…` markers and move `W` links in the
  built `*-stripforge.kicad_pcb`, then build again (the plugin's Build strips on a `-stripforge`
  board rebuilds it in place; `stripforge build --in-place`): they are "locked" and StripForge only
  links what is still unjoined. Or as text in a `[manual]` table (`links`, `cuts`, `no_cut`).
  Checked: a link on a pin, cut or slot hole, a link shorting two nets, a missing cut that would
  short (put back), a cut splitting a net that can't be joined. `respect_edits = false` plans from
  scratch. The report and build sheet mark them "(yours)".

### Changed

- `place_links = true` is now the default: the first build places the W link footprints, and
  **Add links to schematic** carries them into the schematic. KiCad's Update Schematic from PCB
  can't create symbols. `build --no-place-links` / `place_links = false` gives the old two-pass
  flow.
- Build in place by default (Kevin's design). F8 only works in the project's own board opened from
  the project manager, so the separate `-stripforge` board broke pass 2 ("PCB editor is opened in
  stand-alone mode") and looked for a `-stripforge.kicad_sch`. Now pass 2 is: F8 in the same board,
  then Build strips again. The plugin's Build strips must save the board first (OK/Cancel), then
  reloads it in the PCB editor (kipy `Board.revert()`) after writing.
- `link-symbols --schematic` is optional (defaults to `<name>.kicad_sch` next to the board, also for
  a `-stripforge` board); `stripforge drc` finds `<name>.kicad_sch` for a `-stripforge` board by
  itself. Link lists are always `<name>-stripforge.links.{json,csv,txt}`.
- Straight links first: the planner ranks vertical < along the strip < bus strip with straight
  drops < whole-pitch diagonal < off-pitch diagonal, and slides a cut (extends a piece) so two
  pieces share a column or a piece reaches a bus. On the X56 test board: 34 links, no diagonals.

- Build draws every stripboard hole: a board-only `SF_HOLES_<strip>` footprint per strip with a
  plated `B.Cu` pad per free hole on the strip's net (hole cuts as bare holes), so pcbnew and the
  3D viewer show the real board. `draw_holes`, `hole_drill_mm`. `stripforge drc` counts the hole
  footprints' courtyard/library items as "stripboard-hole item(s)" instead of failing on them.
- Wire links from any free hole to any free hole: along a strip, on a diagonal (rotated `Link_P*`
  for whole-pitch lengths, else the new off-pitch `Link_D*` family: 305 footprints, `Link_D3.59`
  … `Link_D81.16`), or two links meeting on a bare **bus strip** isolated by hole cuts. Options
  `max_link_mm`, `diagonal_links`, `off_pitch_links`, `bus_strips`. Pass 2 places rotated links;
  the report, links CSV/JSON (`kind`, `rotation`, `bus`) and build sheet show them.
- Links never cross or touch another link and never pass over a part pin or another link's end
  (a bare wire would short); the planner retries unlinkable nets first for up to 4 rounds.

- `[bend]` table, the "Beckham tolerance": per-part snap tolerance in mm (e.g. `SW2 = 0.16` for a
  312 mil row pitch on 300 mil holes), in any direction, across the strip for slotted parts.
  Analyze lists each leg to bend (mm and mil), the build sheet says "bend legs up to ...", and
  `[bend]` parts are never moved by the best-fit shift. Values over 0.5 mm are refused, over
  0.3 mm warned.
- `skip = [...]`: parts wired off-board (alias of `offboard_refs`): not snapped, no strips, a
  warning naming the nets to hand-wire, and a "Wired off-board" list on the build sheet.
- Warnings for `slotted` / `[bend]` / `skip` entries naming parts that are not on the board.
- Rejection hints by direction: across the strip suggests `[bend]` with a value, along the strip
  suggests `slotted`, and a part that only sits off the grid is told how far to move it. A slotted
  part whose slots point away from its centre is warned to be half a pitch off.

- `[drc]` table in `stripboard.toml`: `ignore` (KiCad DRC types suppressed board-wide) and
  `allow_overlap` (reference pairs whose courtyard overlaps are accepted). Applied by
  `stripforge drc --config` and the plugin's Run DRC; suppressed items are counted in a
  "filtered (config)" line. Unknown keys are an error, unknown type names a warning.
- Analyze warns when a slotted part is placed lopsided ("BT1 slot offsets 0.000/0.635 mm; shift
  -0.318 mm along the strip ... to centre it").
- A part rejected only for being off along the strip now hints `slotted = ["REF"]`.

### Changed

- Slot jobs give the filing distance for the pad's drill (offset plus half an oval drill's excess
  length) in inches and mm, toward the part centre: the BH23APC reads 0.025" (0.635 mm), not
  0.32 mm. JSON `slot_jobs` gain `file_mm`, `file_in` and `inward`.
- New StripForge icon (Kevin's 64 × 64 px "S" with wordmark) as `resources/icon.png` (PCM), and
  regenerated toolbar icons cropped to the "S".
- The plugin uses the only other `*.toml` next to the board (e.g. `X56.toml`) when there is no
  `stripboard.toml` and it is a valid config.

### Fixed

- Rotated (diagonal) link footprints are written with 4-decimal angles, so rebuilding a board
  with placed links gives the same bytes. Before, a 6-significant-digit angle was re-rotated by
  0.0005° on each rebuild.

- The plugin no longer says "Wrote <name>-stripforge.kicad_pcb" for a refused build when an old
  output file exists.

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
