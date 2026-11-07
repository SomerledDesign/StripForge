# Handoff: M4 inspection (2026-11-07)

Inspection only. No dialog code, no branch merge, no release. Default branch is `main`
(`origin/HEAD` -> `origin/main`; GitHub `defaultBranchRef` is `main`). There is no `master`.
Leave the default as `main`.

Repo: https://github.com/SomerledDesign/StripForge

## What is on main

`main` is `49b8bc8` (2026-10-26), release 0.2.1. M0–M3 and **M4d (fail loudly, issue #1)** are in
this tree. Issue #1 is closed. The plugin is still five PCB toolbar buttons, each a
`plugins/sf_<action>.py` script that calls `stripforge_plugin.run`:

| Action | Entrypoint |
|---|---|
| StripForge: Analyze | `plugins/sf_analyze.py` |
| StripForge: Build strips | `plugins/sf_build.py` |
| StripForge: Add links to schematic | `plugins/sf_links.py` |
| StripForge: Run DRC | `plugins/sf_drc.py` |
| StripForge: Build sheet | `plugins/sf_sheet.py` |

Dialogs today are `Ui.ask_save` and `Ui.report` in `plugins/stripforge_plugin.py` (a wx
`MessageDialog` and a read-only report `wx.Dialog`). There is no unified settings dialog.
`config.load` reads `stripboard.toml`. Nothing in `src/stripforge` writes that file back.
Per-part decisions that already exist are TOML fields the CLI already honours: `slotted`,
`skip` (merged with `offboard_refs`), and `[bend]`. A bend over 0.5 mm is refused as an
adapter job. There is no `adapter` key. Analyze text already tells you to add a ref to
`slotted` in `stripboard.toml` (`src/stripforge/grid.py`). DRC filtering is `[drc] ignore`
and `[drc] allow_overlap` (`DrcConfig` in `src/stripforge/config.py`). "(yours)" evidence
walks the `-pre-stripbuild` chain via `writer.numbered_backups` / `writer.moved_in_backups`.

Sketch.md §7 still opens M4 with "Docs only so far: nothing here is built yet." That sentence
is stale for M4d. It is accurate for M4a on every branch.

Open issue that is out of this slice: [#2](https://github.com/SomerledDesign/StripForge/issues/2),
board profiles (M4e in Sketch.md). Leave it.

## Remote branches and pull requests

`git fetch origin --prune` and `git ls-remote --heads origin` show three heads. `gh pr list --state all`
returned no pull requests, open or closed. The two feature branches were pushed without a PR.

| Branch | Tip | Ahead of `main` | URL |
|---|---|---|---|
| `main` | `49b8bc8` | — | https://github.com/SomerledDesign/StripForge/tree/main |
| `m4b-folder-layout` | `cf51fb6` (2026-10-26) | 1 commit | https://github.com/SomerledDesign/StripForge/tree/m4b-folder-layout |
| `m4c-no-diagonals` | `d3fa2ad` (2026-10-27) | 3 commits | https://github.com/SomerledDesign/StripForge/tree/m4c-no-diagonals |

`m4b-folder-layout` is an ancestor of `m4c-no-diagonals`. `main` has no commits that those
branches lack, so a merge would be a fast-forward. Do not merge either branch into `main`
until the backup decision below is made. Do not rebase or force-push them.

### M4b as implemented (`cf51fb6`)

https://github.com/SomerledDesign/StripForge/commit/cf51fb63df5cd6e4628f0778de8e05c7dd8f940c

`src/stripforge/layout.py` puts StripForge's outputs in `<project>/stripforge/`
(`output_dir`, default `stripforge`). The board and its `.kicad_dru` stay next to the
`.kicad_pro`. Config is read from `stripforge/stripboard.toml` first, then `stripboard.toml`
in the project folder. Backups are new timestamped zips under
`stripforge/<Project>-stripforge-backups/` (`backup_dir`). `backup_keep` counts zips and
deletes the oldest. A sixth toolbar action, **StripForge: Restore last**, and
`stripforge restore` unpack the newest save point. Old `-pre-stripbuild` files are left in
place and still read. `writer.moved_in_backups` on this branch still walks those files, then
build save-point zips. The existing "(yours)" tests were updated only for the new links-file
path and passed in CI on this branch.

Sketch.md §7 M4b on `main` describes a different mechanism: a Clean action that **moves**
backup names into `stripforge-backups/` with `os.replace`, never deletes, and keeps the
rotating names. Current `<name>-stripforge.*` outputs stay next to the board. That Clean
command does not exist on `main` or on this branch.

### M4c as implemented (`5867837`, `d3fa2ad`)

https://github.com/SomerledDesign/StripForge/commit/586783712c591c7a1ec73e0c105332796d5acc0f
https://github.com/SomerledDesign/StripForge/commit/d3fa2ad8f56db313cbda31f375e68f0e4993fa89

Stacked on M4b. `diagonal_links` defaults to `false`. With it on, a diagonal may shift at
most `max_diagonal_shift` holes (new key, default 1). The report names the shift that would
be required. Sketch.md on this branch marks M4c done and still says M4a is unbuilt. The
TPI fixture numbers are kept with a diagonals-on copy of the example config.

M4c cannot merge without also merging M4b. Cherry-picking only the diagonal commits is a
later, explicit step after the backup decision. It is not part of the first dialog slice.
On `main`, `diagonal_links` still defaults to `true` (`BoardConfig` in `src/stripforge/config.py`).
The README tip and `examples/x56.toml` already turn diagonals off for the fixture.

## CI

Workflow: `.github/workflows/ci.yml`. On push and pull request, Python 3.9, 3.11, and 3.12:

```sh
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest -v
```

Latest runs (all three branches, 2026-10-27):

| Branch | Run | 3.11 | 3.12 | 3.9 |
|---|---|---|---|---|
| `main` | https://github.com/SomerledDesign/StripForge/actions/runs/40437358391 | 350 passed, 5 skipped | 350 passed, 5 skipped | job failed before pytest |
| `m4b-folder-layout` | https://github.com/SomerledDesign/StripForge/actions/runs/40515595153 | 354 passed, 5 skipped | 354 passed, 5 skipped | same 3.9 failure |
| `m4c-no-diagonals` | https://github.com/SomerledDesign/StripForge/actions/runs/40515595251 | 363 passed, 5 skipped | 363 passed, 5 skipped | same 3.9 failure |

The 3.9 failure is `The version '3.9' with architecture 'x64' was not found for Ubuntu 26.04`
in `actions/setup-python`. Ruff had already passed on the 3.11 and 3.12 jobs (`All checks passed!`).
The suite itself was not reported failing. Fixing the 3.9 job means editing the workflow.
Leave the workflow alone until that edit is approved. KiCad 10's macOS plugin Python is still 3.9
(`pyproject.toml`, `requires-python >= 3.9`), so the missing cell is a real gap, and it is not a
reason to treat `main` as red on the tests that did run.

## Commands run in this cloud checkout

This machine is Linux, CPython 3.12.3. Authoritative local commands are the ones in
`CONTRIBUTING.md` and the README Development section. They were run from the repo root on
`main` at `49b8bc8` (the handoff branch contains only this file).

POSIX (macOS, and this Linux checkout):

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
```

Windows PowerShell (not run here; same tools, different activation):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e '.[dev]'
ruff check .
ruff format --check .
pytest
```

Results on this checkout, Python 3.12.3:

- `ruff check .` — exit 0, `All checks passed!`
- `ruff format --check .` — exit 0, `83 files already formatted`
- `pytest -q` — exit 0, `349 passed, 6 skipped in 957.45s`. `pytest` and `pytest -q` collect the same `tests/` suite (`pyproject.toml`).

The six local skips:

- `tests/test_stripforge_rules.py`: `test_rules_quiet_on_clean_board`, `test_edge_clearance_trips_without_rules`, `test_each_rule_fires` — `kicad-cli not found`
- `tests/test_drc.py::test_real_fixture_pass1_drc` — `kicad-cli not installed`
- `tests/test_linksym.py::test_link_symbols_netlist_with_kicad_cli` — `kicad-cli not installed`
- `tests/test_buildsheet.py::test_clicking_rows_ticks_strikes_saves_and_resets` — `headless Chrome did not finish` (the test's own 120s timeout). `google-chrome` is at `/usr/local/bin/google-chrome`. `test_pdf_via_chrome` passed.

CI's five skips match the four `kicad-cli` skips plus one more that this note does not re-list from the log. The extra local skip is the checklist Chrome test. That is an environment timeout, not a dialog failure.

`python tools/make_pcm_zip.py` was not run. No package was installed into KiCad.

## What this environment cannot show

- `import wx` raises `ModuleNotFoundError`. The plugin dialogs cannot be opened here.
- `kicad-cli` and `pcbnew` are not on `PATH`. No board was opened in KiCad.
- `DISPLAY` is set. That does not provide KiCad. No toolbar click was performed, and none should be claimed.

A later dialog slice can still be tested the way `tests/test_plugin.py` already tests the plugin: plugin.json shape, pure helpers, and the no-wx log path. A KiCad click-through stays on a machine that has KiCad 10 (`docs/KICAD-PLUGIN-TEST.md`).

## Decision before any merge

Pick one backup design and make Sketch.md §7 M4b say that one thing.

1. **Sketch Clean (text on `main`).** Move backup files into `stripforge-backups/`, never delete, keep rotating names, and teach `moved_in_backups` to walk that folder. Current `*-stripforge.*` outputs stay beside the board. This is not implemented.
2. **Branch save points (`m4b-folder-layout`).** New zip save points, `backup_keep` may delete old zips, Restore last, and every StripForge output except the board and `.kicad_dru` moves under `stripforge/`. Implemented, with CI green on 3.11 and 3.12. "(yours)" tests passed there because `moved_in_backups` also reads build zips.

The first dialog slice does not need this decision. It branches from `main` and does not touch backup code.

## First testable dialog slice

Branch from `main`. Leave the five buttons in place (Sketch.md: the toolbar keeps working until the dialog covers every action). Add a sixth button that only opens a shell and runs Analyze.

1. `plugins/sf_dialog.py`, same pattern as `plugins/sf_analyze.py`: `run("dialog")`.
2. One new `plugins/plugin.json` action, `scopes: ["pcb"]`, `show-button: true`. Point its icons at copies of an existing 24/48 px PNG under `plugins/icons/` so `tests/test_plugin.py` still sees a PNG header. Do not add Pillow or change `tools/make_icons.py`.
3. A pure helper next to the plugin helpers, with no wx and no kipy. Input: a board path. It uses `find_config` and `config.load`. Output: the config path (or "grid from Edge.Cuts"), `rows`, `cols`, `origin_mm`, `cut_style`, `diagonal_links`, `slotted`, `skip`, `bend`, `drc.ignore`, `drc.allow_overlap`, and the action list in workflow order with only Analyze enabled. Build, links, DRC, sheet, and Clean stay visible as disabled or absent. This slice does not write TOML.
4. When wx imports, a `wx.Dialog` shows that snapshot as read-only text, an Analyze button, and Close. Analyze calls the existing `_run_action("analyze", ...)` path and puts the report in the same kind of read-only `TextCtrl` as `Ui.report`. Save-first stays `Ui.ask_save` (not required, same as Analyze today). When wx is missing, reuse the M4d behaviour: write `<name>-stripforge.log`, explain that there is no dialog, and do not save the board silently.
5. Tests in `tests/test_plugin.py` only:
   - `plugin.json` lists the new action and the five existing ones, in that order, with the schema checks already in `test_plugin_json_follows_the_kicad_10_api_plugin_schema`.
   - The snapshot helper matches a temp `stripboard.toml` and the no-config case.
   - The no-wx path writes the log and does not report success as a dialog. Follow `test_without_wx_the_report_says_why_there_is_no_dialog`.

Inner loop after that change:

```sh
ruff check .
ruff format --check .
pytest tests/test_plugin.py -q
```

Before a pull request, the CONTRIBUTING gate is still full `pytest`. Do not claim a KiCad click test from this cloud environment.

Leave out of this slice: TOML writing, the per-part Slotted / bend / Skip / adapter table, editable DRC ignores, Clean, Restore, the sheet preview, removing the five buttons, merging `m4b-folder-layout` or `m4c-no-diagonals`, and any CI, dependency, or directory restructure.

The TOML writer is the slice after this one, because settings have to round-trip through `config.load` and a naive dump would drop the comments in `examples/x56.toml`. That writer gets its own test: load, change one field, write, load again, and check the field. It still does not need wx.

## Next step

Wait for a yes on this slice (and, separately, on which backup design is authoritative). Then implement only the slice above on a new branch from `main`.
