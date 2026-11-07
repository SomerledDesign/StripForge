# Handoff: shared dialog and backup policy (2026-11-07)

Implementation is on `cursor/m4c-dialog-slice-3529`, based on `origin/m4c-no-diagonals`
(`d3fa2ad`), which already contains `m4b-folder-layout`. It is not merged. `main` is still
`49b8bc8` (0.2.1). There is no `master`.

[PR #4](https://github.com/SomerledDesign/StripForge/pull/4) is this work
(`cursor/m4c-dialog-slice-3529`). It is not merged.
[PR #3](https://github.com/SomerledDesign/StripForge/pull/3) (`cursor/m4-dialog-handoff-3529`)
is the earlier inspection note. Its history was left as pushed. This file is the current handoff.

## Backup policy (Kevin, 2026-11-07)

`backup_policy` in `stripboard.toml`:

- **`move`** (default, and the value used when the key is absent). Each save point is a new
  timestamped zip. Older zips are kept. `backup_keep` is stored and shown, and it does not delete.
- **`retention`** (opt-in). Then `backup_keep = N` with N ≥ 1 deletes older StripForge save-point
  zips, and older sheet copies in that folder, so N remain. `backup_keep = 0` still keeps all.
- A file that only has `backup_keep = N` does not delete. That is the safe reading of an old config.
- Saving the setting from the dialog does not delete anything. The next build, sheet, or link
  update applies the policy.
- **Restore last** is the same in both policies. It asks first, unpacks the zip, and removes only
  files the manifest recorded as not existing yet. The zip is kept. It does not prune other zips.
- Retention deletes only `<Project>-stripforge-YYYY-MM-DD_HHMMSS.zip` (and previous sheet copies
  in the backup folder). KiCad's own zips and legacy `-pre-stripbuild` files are left in place.
  "(yours)" still reads the legacy files and the zips that remain.
- "Move" means the save point is filed and kept, using the zip Restore already understands. There
  is no second format that `os.replace`s loose backup files.

`examples/x56.toml` sets `backup_policy = "move"` and `backup_keep = 0`. Those defaults were not
changed to pruning.

## Dialog

**StripForge: Dialog** (`plugins/sf_dialog.py`) is a temporary extra toolbar button. The six
action buttons stay. The window:

- shows the saved settings (read-only): rows, cols, origin, cut style, diagonal links, slotted,
  skip, bend, DRC ignore and overlaps, and the backup policy;
- edits only `backup_policy` and `backup_keep`, written into the config file Analyze already
  reads (a lone `X56.toml` is edited in place; a missing file becomes
  `stripforge/stripboard.toml` with just those keys). Comments and other keys are kept;
- runs Analyze, Build strips, Add links, Run DRC, Build sheet and Restore last through
  `_perform`, the same path as the toolbar.

The end state is still one dialog. This button is not that end state.

## Try it in KiCad

This cloud checkout has no wxPython, no `kicad-cli` and no `pcbnew`. Nothing was installed into
KiCad here, and no toolbar click was made. `docs/KICAD-PLUGIN-TEST.md` step 6 is the click-through.

The tree is not merged and the version is still 0.2.1, so a GitHub release zip is the old plugin.
From a checkout of this branch, build the existing package and install that file (not the release):

POSIX (macOS):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
python3 tools/make_pcm_zip.py
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e '.[dev]'
python tools/make_pcm_zip.py
```

In KiCad: Plugin and Content Manager, Install from File, pick `dist/StripForge-0.2.1-pcm.zip` from
that checkout, restart KiCad. Use a copy of a project. Expect the existing buttons plus Dialog.

## Checks

POSIX, from the repo root, Python 3.12.3 on this checkout:

```sh
ruff check .
ruff format --check .
pytest
```

Windows PowerShell uses the same three commands after `.\.venv\Scripts\Activate.ps1`.

On this checkout (Linux, CPython 3.12.3), after the dialog and backup-policy changes:

- `ruff check .` — exit 0
- `ruff format --check .` — exit 0
- `pytest -q` — exit 0, **368 passed, 6 skipped** in 752.54s

The six skips are the same environment limits as on `main`: four tests because `kicad-cli` is
absent (`tests/test_stripforge_rules.py` three tests, `tests/test_drc.py::test_real_fixture_pass1_drc`,
`tests/test_linksym.py::test_link_symbols_netlist_with_kicad_cli`) and
`tests/test_buildsheet.py::test_clicking_rows_ticks_strikes_saves_and_resets` (`headless Chrome
did not finish`). `test_pdf_via_chrome` passed on the previous run of this machine. No KiCad
click-through was done.

CI (`.github/workflows/ci.yml`) is pinned to `ubuntu-24.04` with Python 3.9, 3.11, and 3.12.
On `3635f6d`, `ubuntu-latest` was Ubuntu 26.04 and the 3.9 job failed in `setup-python` before
pytest (`The version '3.9' ... was not found for Ubuntu 26.04`). 3.11 and 3.12 on that run passed
(369 passed, 5 skipped). Kevin approved the pin.
