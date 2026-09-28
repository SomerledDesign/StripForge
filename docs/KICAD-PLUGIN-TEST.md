# Manual test: the StripForge plugin in KiCad 10

Clicks in KiCad can't be automated, so run these by hand after an install (README, "Install in
KiCad"). Use a **copy** of a project, because Build strips writes files next to the board.

1. Quit KiCad fully (KiCad > Quit), then start it again so it scans the plugin folders.
2. Preferences > Plugins: **Enable KiCad API** is ticked. Keep the default interpreter (KiCad's
   bundled Python).
3. Open the project and its board in the PCB editor. Wait about a minute: the first time, KiCad
   creates the plugin's environment in
   `~/Library/Caches/KiCad/10.0/python-environments/com.github.somerleddesign.stripforge`
   (macOS) and installs kicad-python.
4. **Expect four StripForge buttons** on the PCB editor toolbar ("S" icon with A / B / D / S badges).
   If they are missing: check the status-bar warnings, and Preferences > Plugins > "Recreate Plugin
   Environment", then reopen the PCB editor.
5. If the project has an X56-style config, copy it next to the board as `stripboard.toml` (a single other `*.toml`, e.g. `X56.toml`, is also picked up; the report says which config was used).
6. Click **StripForge: Analyze**.
   - Expect a "Save the board first?" dialog, then a report dialog that starts with `Board:`,
     `Config:` and `Netlist: exported from <project>.kicad_sch`.
   - The report lists the cuts and the links needed, using hole labels (A1…).
7. Click **StripForge: Build strips** (open the board from the KiCad project manager, so F8 works).
   - Expect a "Save and build" dialog (OK/Cancel), then the report with "Built <name>.kicad_pcb in
     place. Reloaded the built board in the PCB editor." and "Backup: …/<name>-pre-stripbuild.kicad_pcb
     (made before this build)".
   - The open board now shows B.Cu strips, the cut markers on User.1 and the hole pads.
   - Press F8 after adding the W links to the schematic (or with `place_links`, after
     `stripforge link-symbols <board> --in-place`), then click **Build strips** again: the links
     are placed and the report says the backup was "kept as it was". The backup's bytes don't change.
   - To undo: close the board, delete `<name>.kicad_pcb`, rename `<name>-pre-stripbuild.kicad_pcb`
     back.
8. Click **StripForge: Run DRC**.
   - Expect "parity: checked against the schematic" (the board is `<name>.kicad_pcb`, so kicad-cli
     uses `<name>.kicad_sch` directly).
   - After pass 1, unconnected equals the links still to add. After pass 2 it should be 0.
9. Click **StripForge: Build sheet**. Expect the browser to open `<name>-stripforge.sheet.html`,
   with a `.pdf` beside it if Google Chrome is installed. Print one page to check the scale ruler.
10. Error path: close the board (or start the action from an unsaved new board). Expect a readable
    message, not a silent failure.

Report anything unexpected along with the dialog text ("Copy report" button). To uninstall a hand
install, delete `~/Documents/KiCad/10.0/plugins/com.github.somerleddesign.stripforge/`. KiCad
recreates nothing once it's gone; its cached environment under `~/Library/Caches/KiCad/10.0/`
can be deleted too.
