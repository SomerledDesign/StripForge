# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
