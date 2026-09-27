# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Initial scaffold: the `stripforge` Python package (stub modules for the grid, strips, splitter,
  links, validation, DRC, build sheet and backends) and a stub `stripforge` CLI.
- Sketch.md design plan (v0), README, GPL-3.0-or-later license, contributing guide.
- Example board config (`examples/stripboard.toml`) and a placeholder for the ATtiny10 TPI fixture
  test case.
- GitHub issue and pull request templates, and CI running ruff and pytest on Python 3.11 and 3.12.
- KiCad Plugin and Content Manager `metadata.json` stub.
