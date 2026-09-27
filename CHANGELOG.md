# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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

- `examples/stripboard.toml` now holds the TPI fixture's real grid (30 × 25 holes, origin
  51.27, 51.27 mm).

- Initial scaffold: the `stripforge` Python package (stub modules for the grid, strips, splitter,
  links, validation, DRC, build sheet and backends) and a stub `stripforge` CLI.
- Sketch.md design plan (v0), README, GPL-3.0-or-later license, contributing guide.
- Example board config (`examples/stripboard.toml`) and a placeholder for the ATtiny10 TPI fixture
  test case.
- GitHub issue and pull request templates, and CI running ruff and pytest on Python 3.11 and 3.12.
- KiCad Plugin and Content Manager `metadata.json` stub.
