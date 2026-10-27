# StripForge 0.2.1 (beta)

A bug-fix release: StripForge now **fails loudly** instead of seeming to do nothing (issue #1, thanks to @NickLarsenNZ). Every action writes its report to `<name>-stripforge.log` next to the board, a board with no Edge.Cuts outline is refused with a clear "draw the board outline first" message, and without wxPython (e.g. NixOS) the report explains how to fix the plugin environment instead of saving the board silently.

## Install or update

Download `StripForge-0.2.1-pcm.zip` below. In KiCad's Plugin and Content Manager, click **Install from File…**, pick the zip, and restart KiCad. See the [README](https://github.com/SomerledDesign/StripForge#readme) for the full steps.

## Changes

### Added

- **Fail loudly (M4d, issue #1).** Every plugin action writes its report to
  `<name>-stripforge.log` next to the board (temp dir if there is no board) and prints one short
  line pointing to it, so a run is never silent. Without wxPython the report says why there is no
  dialog and how to fix it, the board is no longer saved silently, and an in-place Build strips is
  refused instead. Build/Analyze check for a missing Edge.Cuts outline (with no `origin_mm`/`rows`/
  `cols`) before saving, with a "draw the board outline first" message. An off-grid footprint is
  reported once (not once per pad) with its Move Exactly offset and the KiCad steps. README: draw
  the outline first, the log file, the venv path case, and a NixOS note.
- **Build sheet: tick the checklists off in the browser** (Kevin, building the TPI fixture). A
  click on a row or its box ticks it, strikes the row through except the Status, and sets the
  Status to "Cut" (cuts) or "Installed" (links and parts; parts get a Status column); click again
  to undo. Slot jobs, link lengths, off-board parts and nets tick and strike the same way. Ticks
  are kept in localStorage under `stripforge-sheet:<board>:<build hash>` (a hash of the
  checklists, so a reprint keeps them and a changed build starts afresh), with a progress count
  per list ("12/34 links") and a **Reset checklist** button. Print shows the ticks as they are.
  One inline script, no external files. The PDF stays static (Chrome's print-to-PDF makes no form
  fields): print it and tick by pen.

### Fixed

- Build sheet legend: the component-side key always said "wire link (dashed: proposed, not placed
  yet)", even when every link was placed (placed links were already drawn solid). It now has two
  line swatches: a solid blue "wire link", and a dashed blue "proposed (not placed yet)" shown only
  when the sheet has links that aren't placed. Same in the HTML and the PDF.

### Docs

- Roadmap: a new **M4** in Sketch.md §7 (one StripForge button and dialog; a Clean action that moves
  backups into `stripforge-backups/` and never deletes; diagonal links off by default with at most
  a one-hole shift). Pre-drilled mounting holes move to "Later". Plans only, no code.
- README and release notes: screenshots of Install from File in the Plugin and Content Manager.
- The footprint library path for a PCM install is `${KICAD10_3RD_PARTY}/plugins/com_github_somerleddesign_stripforge/...`:
  the Plugin and Content Manager names the folder with underscores, not dots as the docs said.


**sha256** of `StripForge-0.2.1-pcm.zip`: `c457fd31773a7742b88305fee3b3f941f7194295267dcea9d48e96bf40f19804`
