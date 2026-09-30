# Contributing to StripForge

Thanks for your interest. StripForge is in beta, so the design in [Sketch.md](Sketch.md) is still
moving. Please open an issue to discuss anything larger than a small fix before writing code.

## Setup

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Before you open a pull request

- `ruff check .` and `ruff format --check .` pass.
- `pytest` passes, and new behaviour has tests.
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md).
- Target **KiCad 10**. Don't add SWIG (`pcbnew`) dependencies; use the file backend, `kicad-cli`
  or the IPC API.

## Licensing

StripForge is GPL-3.0-or-later. By contributing you agree that your contribution is licensed under
the same terms. Only bring in third-party code whose license is GPL-3-compatible (for example
Apache-2.0 or MIT), keep its attribution, and say where it came from in the pull request. Code
with no license can be read for ideas but not copied.
