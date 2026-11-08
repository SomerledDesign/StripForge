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
  columns, pitch, and the other root settings the loader accepts are editable. The structured
  fields still do not rewrite `[bend]`, `[drc]`, `[manual]`, `[stretch]`, or `slot_max_mm_by_ref`.
  **Edit stripboard.toml** opens that same file in a small resizable text window. The text is
  saved as typed, after it parses and `from_dict` accepts it. `[bend]` numbers are millimetres
  (`SW1 = 0.46` is 0.46 mm: kept, with a warning above 0.3 mm, refused above 0.5 mm). Save, Reload,
  and Cancel are separate. A bad text or a file that changed on disk since the window opened is
  not written, and the text stays in the window. Cancel discards unsaved text. Reload discards it
  and loads the file. The window stays closed while the Board fields or the Backup choice are
  unsaved. A successful save refreshes those controls from the file. Actions do not run on the
  unsaved text. Save validates first and replaces the file only if it is valid. Comments stay.
  Reload discards unsaved edits. While the form is dirty, the action buttons do not run.
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

On this checkout (Linux, CPython 3.12.3), after the 2026-11-07 23:22 archive:

- `ruff check .` — exit 0
- `ruff format --check .` — exit 0
- `pytest -q` — exit 0, **399 passed, 8 skipped** in 831.43s

The eighth skip is the wx click-through of Edit stripboard.toml (`wx` is not installed here).
KiCad's addon guide requires a plugin archive and a library archive. This checkout builds both.
The plugin zip does not contain `footprints/`, `symbols/`, or `3dmodels/` at the archive root.
The libraries zip does, and it does not contain `plugins/`.

- Plugin `StripForge-0.2.1-pcm.zip`: sha256
  `7964fe70f5c66fbc5bf43fcab9cd1e02be74de5ac36a4b471d069f0b7c383f7b`, 651874 bytes.
- Libraries `StripForge-libraries-0.2.1-pcm.zip`: sha256
  `d330372d6148e0802a694e74c54ab87c53b975b1de09f7e7a3e584aaffb9cdcb`, 10826951 bytes.
Six actual builds of the archived LedSwitch25 board were not run: both the archived separate
build and the `SW1 = 0.46` in-place override exit 2. Kevin's own current project does build;
that does not unblock this archive. KiCad was not installed here, so Install from File was not
clicked.

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

## LedSwitch25 acceptance archive (2026-11-08)

Kevin's current circuit is `tests/fixtures/ledswitch25/LedSwitch25-kicad.zip`. Source file
`Niveus Dropbox/KiCad/LedSwitch25-kicad.zip`, mtime 2026-11-07 23:22:26 EST, 25859 bytes, sha256
`cf5b92d393f2172d25f48329f92281ed4a41d2f8a8290fcf72f68dd8cbb09e6c`. The committed file is those
bytes. The previous acceptance zip is kept as
`tests/fixtures/ledswitch25/LedSwitch25-kicad-2026-11-07-2158.zip` (53157 bytes, sha256
`a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592`, source mtime 2026-11-07
21:58:07 EST) and is not what tests open. A 9022-byte `LedSwitch25-kicad.zip` under agent
artifacts is older still and is not this archive. Tests call `tests/ledswitch_fixture.py`, which
checks the current hash, extracts a new directory, and skips `__MACOSX`, `.DS_Store`, and lock
files. Nothing writes either zip back. The versioned box path
`/workspace/test-fixtures/stripforge-canonical-2026-11-07-2323/` was not mounted here; the
attached file matched the mandated hash and those bytes were copied.

TPI (`examples/tpi-fixture/`, `tests/fixtures/tpi-m1/`) stays for unit tests. It is not the
acceptance circuit. An older LedSwitch zip is not this file.

Disposable extract, measured, config not rewritten:

- 9 footprints, 20 through-hole pads, no segments, arcs, or vias. One `gr_rect` outline,
  (127.00, 101.60)–(160.02, 134.62) mm.
- The pcb is `b7e624dfb801e234b9702f0bb3d378ebb75cdc443f0ad07bce8c79210ee760c4` (63325 bytes).
  This zip has no KiCad backup folder.
- Comments still say 25 by 25 and hole A1 at (1.27, 1.27) mm. Those comments are stale. The
  loaded settings are rows 13, cols 13, origin `[129.54, 104.14]`, pitch 2.54,
  `output = "separate"`, `link_lead_allowance_in = 0.15` (inches a leg), `backup_policy = "move"`,
  `backup_keep = 0`. Those settings were left as archived.
- The 13×13 hole grid is A1–M13. From that origin the last hole centres sit on the outline's
  far edges (160.02, 134.62). Analyze reports that this grid extends past Edge.Cuts. Both spans
  are 33.020 mm; the outline starts 2.54 mm before A1.
- Eight parts snap. C1's second pad is 0.040 mm along the strip and is inside tolerance. R2, R3,
  and R5 are 10.16 mm pad to pad. SW1 is at (134.62, 129.08) mm. Pad 1 is on a hole, pad 2 is
  0.460 mm off, and pad 3 is 0.920 mm off. The archive has no `[bend]` table.
  `stripforge build` with the archived `output = "separate"` exits 2. The original pcb is
  unchanged and no StripForge save point is written. `Net-(D2-K)` was not reached.
- The previously approved working-copy line `[bend]` / `SW1 = 0.46` (millimetres, not baked into
  the zip) was applied with the editor save helper and built with an explicit `--in-place`
  override. That also exits 2. The message says the pins match the hole pitch and the part is
  0.460 mm off the grid (Move Exactly X = 0.000, Y = 0.460 mm). `SW1 = 0.92` is refused by the
  existing loader because it is over 0.5 mm. No placement, schematic, or loader change was made.
  Six actual builds were not run. A separate test still takes six save points of the archived
  board and restores them to the original pcb bytes. This zip has no KiCad backup folder; those
  save points are the ones the test creates.
- Add links to schematic writes one `StripForge:Link` per placed W footprint. KiCad matches a
  footprint's `(path)` to `"/"` plus that symbol's uuid on the root sheet, and reports that it
  cannot find a symbol for the footprint when they differ. The symbol file
  `symbols/StripForge.kicad_sym` (packaged as `plugins/symbols/StripForge.kicad_sym`) contains
  symbol `Link`. The schematic embeds it as `StripForge:Link` with units `Link_0_1` and
  `Link_1_1`. A placed W symbol whose unit is missing is reported as
  `Cannot find symbol <lib_id> for <W ref>`. On this archive the open board has no W footprints
  until a build succeeds. The symbol test places links with the build's helper and does not move
  SW1. A second run does not add them again, and the first save point is kept (`move`). When
  `output = "separate"`, Add links reads `stripforge/<Project>-stripforge.kicad_pcb` if that file
  has the W footprints, and finds the schematic next to the `.kicad_pro`.

The PCM package does not contain `tests/`. Add links follows a separate build into `stripforge/`
and keeps the footprint's symbol uuid, which is what KiCad matches when it says it cannot find a
symbol for `W1`, `W2`, and the rest.

Installing the plugin also installs the footprint library, the symbol library, and whatever
straight-link models are in `3dmodels/StripForge.3dshapes`. KiCad extracts `footprints/`,
`symbols/`, and `3dmodels/` into `${KICAD10_3RD_PARTY}/<kind>/com_github_somerleddesign_stripforge/`.
The default nickname is `PCM_StripForge`; rename it to `StripForge`.

The model bytes are the read-only copy of `Penus/iWasCoding/StripForge/footprints/`
(sha256 `102cd79069ac8f684e6645be05665308aefcc18914a7a74783c134af2f2cfe6e`, 10686008 bytes).
The 64 STEP and WRL files in that zip are byte-identical to the earlier staged set. Kevin's own
`StripForge.pretty` footprints do not contain a model reference. The references added here are
only in this repository's generated footprints. Nothing in a footprint path points at Dropbox.

Install the libraries package, then the plugin package. The library id is
`com.github.somerleddesign.stripforge.libraries`. Each straight `Link_P*` footprint names
`${KICAD10_3RD_PARTY}/3dmodels/com_github_somerleddesign_stripforge_libraries/StripForge.3dshapes/<name>.step`.
`KICAD10_3RD_PARTY` is the KiCad 10 variable for Plugin and Content Manager files. It is not
`KICAD10_3DMODEL_DIR`, which is KiCad's own model library. The `.wrl` has the same basename and
is installed beside the STEP. The KiCad manual says a library package is added to the global
tables with the `PCM_` nickname prefix by default. That was not clicked here, so do not treat the
row as already renamed to `StripForge`. If it is missing, add nickname `StripForge` at
`${KICAD10_3RD_PARTY}/footprints/com_github_somerleddesign_stripforge_libraries/StripForge.pretty`
and the symbol file under `symbols/` for the same id. The plugin keeps a private copy of the
footprints under `plugins/` so it can build without that table. The 3D bytes are only in the
libraries package.

Each straight footprint carries that reference (zero offset, unit scale, zero rotation). Placing
a link copies the footprint, so a new straight link inherits the model. A horizontal link is only
the footprint's 90° angle. The build does not assign models itself, and it leaves a model already
on a placed link or on a part alone. Diagonal `Link_D*` footprints have none. The installed
library and the copy under `plugins/footprints/` are the same files. This was checked headless,
from the written board text. KiCad's 3D viewer was not opened here, and Install from File was
not run. Both in-zip metadata files validate against the KiCad PCM schemas v1 and v2
(`https://go.kicad.org/pcm/schemas/v1` and `v2`). Every path in both zips matches the allow-list
in `ci/validate/package.py` of `https://gitlab.com/kicad/addons/metadata`. That script's full
repository check also wants a download URL, which these local zips do not have, so that entry
point was not run. Version stays 0.2.1. This is not a release.
