# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  slotted); the M1 board is frozen in `tests/fixtures/tpi-m1/` for the M1/M2A tests.
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
