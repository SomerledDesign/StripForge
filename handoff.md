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
action buttons stay. The window has two tabs and the same action buttons as the toolbar:

- **Board** edits the project's `stripboard.toml` (the file Analyze already reads, not a second
  name). Origin X/Y are the centre of hole A1 in millimetres, not the Edge.Cuts corner. Rows,
  columns, pitch, and the other root settings the loader accepts are editable. `[bend]`, `[drc]`,
  `[manual]`, `[stretch]`, and `slot_max_mm_by_ref` are left in the file and are not edited.
  Save validates first and replaces the file only if the result loads. Comments and unknown keys
  stay. Reload discards unsaved edits. While the form is dirty, the action buttons do not run.
  The editor does not move parts or guess an origin.
- **Backup** edits `backup_policy` and `backup_keep` in that same file.
- **Text size** is not a board setting. Larger text, Smaller text, and Reset text size (and
  Command-plus / Command-minus / Command-0, or Ctrl on Windows and Linux) write a per-user
  file: `~/Library/Application Support/StripForge/dialog-text-scale` on macOS,
  `%APPDATA%\StripForge\dialog-text-scale` on Windows, `$XDG_CONFIG_HOME/stripforge/dialog-text-scale`
  on Linux. Range 100% to 300% in 25% steps. Pinch is bound to wx magnify / zoom gestures when
  that wx build has them. It has not been tried on a Mac trackpad.
- Analyze, Build strips, Add links, Run DRC, Build sheet and Restore last go through `_perform`.

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

On this checkout (Linux, CPython 3.12.3), after Restore last walks back to the first save point:

- `ruff check .` — exit 0
- `ruff format --check .` — exit 0
- `pytest -q` — exit 0, **387 passed, 7 skipped** in 826.11s

The run before this fix, after the board-tab editor, was 380 passed, 7 skipped.

Skips: the five `kicad-cli` tests, `test_clicking_rows_ticks_strikes_saves_and_resets` (headless
Chrome did not finish), and `test_headless_dialog_saves_origin_and_refuses_a_dirty_action`
(`wx` is not installed here; there is no wxPython wheel for this image). No KiCad click-through
was done.

CI (`.github/workflows/ci.yml`) is pinned to `ubuntu-24.04` with Python 3.9, 3.11, and 3.12.
On `3635f6d`, `ubuntu-latest` was Ubuntu 26.04 and the 3.9 job failed in `setup-python` before
pytest (`The version '3.9' ... was not found for Ubuntu 26.04`). 3.11 and 3.12 on that run passed
(369 passed, 5 skipped). Kevin approved the pin. The pin commit `1aa9f85` then passed all six
jobs (369 passed, 5 skipped each).

## PNG profiles (2026-11-08)

Kevin sees an iCCP warning in KiCad. The phrase he quoted ("cRMG") matches libpng's
`cHRM chunk does not match sRGB` (the ICC/PNG cHRM tag, not a cRMG chunk).

libpng 1.6.43 on this machine decoded every PNG in the repo and printed no warning. The PCM
zip's PNGs are `plugins/icons/*.png` (including the dialog icons) and `resources/icon.png`.
None of them contain an iCCP or cHRM chunk. `resources/icon.png` has an sRGB intent byte only.
Ten screenshots under `docs/images/` do contain an iCCP profile (Apple "Display" or "Display P3")
and no cHRM chunk. Those screenshots are not in the PCM zip, and this libpng did not warn on
them either. No pixels were rewritten. The package hash is unchanged:
`0a77c6eb5d1929b9d2f5acd21d00ceabd22dae2f31be116c6610a6d4eeeff00d` (638190 bytes).

This checkout has no wx and no KiCad, so the Mac GUI warning was not reproduced. The next
useful line is the full libpng warning from Terminal.app while starting
`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad`, plus whether it appears before any
StripForge board is opened.

## Restore last (2026-11-08)

Kevin: repeated Restore last stopped moving the board after a few clicks. He wants each click to
step back through every save point until the first one, the board from before StripForge added
strips, holes and tracks.

**Cause.** There is no cutoff at 2 or 3. `layout.restore` with no zip always loaded
`backups()[-1]`, the newest save point, and left that zip where it was. The next click unpacked
that same zip again, so the board stayed put. The toolbar and the dialog both call `_restore`,
and `_restore` asked for that newest zip every time; the dialog does not cache the name. The
README on this branch described that on purpose ("pressing Restore last twice just loads the same
save again"). Further back was only `stripforge restore <board> --backup <zip>`.

A build makes a new newest zip, so the first Restore last after each build did undo that build.
Two or three build-then-restore tries therefore each moved once, and the next click did not.
That is the "past 2-3 restores" report. It is not a Mac-only path.

**What it does now.** Each Restore last loads the next earlier save point. The files in that zip
are written back under their own names, and files the zip recorded as not existing yet are
removed. Repeat until the first zip. That zip is the board saved before the first build, bytes
and all, including any tracks or holes that were already on it. The walk does not delete every
track to invent a baseline. One more click reports `Result: nothing further to restore` and does
not change the board or any zip.

Before it replaces a file it writes
`<Project>-stripforge-safety-YYYY-MM-DD_HHMMSS.zip` (the current board, and any other file it is
about to overwrite or delete). Those copies are kept. They are not steps in the walk, so the next
click cannot toggle back onto the board just replaced. `--list` shows them. `--backup <zip>`
still loads a named zip, including a safety copy.

A save point made after the walk has started (another build) is undone once. The walk then
continues toward the first save point rather than jumping forward into a later intermediate
board. Where the walk is sits in
`stripforge/<Project>-stripforge-backups/restore-cursor.json`, so closing and reopening the
dialog continues. Restore does not rewrite or delete existing save-point zips. `move` still
never deletes. `retention` still deletes older save points only on a later build, sheet or link
update, and it does not delete safety copies. A bad zip is rejected before any project file is
changed. If a write fails part way, the files already replaced are put back from the safety copy
and the cursor does not move.

**Not tried in KiCad.** This checkout has no wx and no pcbnew, so no Mac click and no real
RevertDocument. `tests/test_plugin.py` drives the same `_restore` the dialog button calls: confirm,
report text, and `revert` on a fake board, six times, then the stop. A fresh `select_restore_zip`
reads the cursor file between those calls. `tests/test_restore_walk.py` checks six synthetic
steps back to a baseline that still contains a user track, a same-second `-2` zip, a build made
after a partial walk, retention versus safety copies, a hostile zip, a failed replace, and six
real builds of the TPI fixture whose final bytes match the unbuilt board (`R3` still present,
`StripForge:CUT_` gone).

PCM from this tree, version still 0.2.1 (not a release):

`1a4182cecc187592a1f8a3ecd855a3fff99462d8f84e727931dd0ef89d8ef4c2` (644664 bytes)

`dist/StripForge-0.2.1-pcm.zip`. Install that file, not an older zip. The PNG audit above was
against the previous hash.
