# Manual test: the StripForge plugin in KiCad 10

Clicks in KiCad can't be automated, so run these by hand after an install (README, "Install in
KiCad"). Use a **copy** of a project, because Build strips writes files next to the board.

For a circuit check, unzip `tests/fixtures/ledswitch25/LedSwitch25-kicad.zip` (sha256
`b98f3c460938e215e9c3e6cbf87239d3cb8fce3c77d736c215f4c69600832209`, 26091 bytes) to a new folder.
Ignore `__MACOSX`, `.DS_Store`, and lock files. Do not use
`LedSwitch25-kicad-2026-11-07-2158.zip`, a 9022-byte LedSwitch zip, or the TPI fixture for this
check. The toml comments describe 25 by 25 and a 63.5 mm outline at (1.27, 1.27) mm. The
settings are 13 by 13, origin `[129.54, 104.14]`, `output = "in_place"`,
`link_lead_allowance_in = 0.15`, move, `backup_keep = 0`. Do not rewrite them. The pcb
Edge.Cuts span is 33.020 mm, not 38.1 mm. SW1 is on the hole grid, so Build strips does not
need a `[bend]` line. The steps below are the plugin click-through on whatever project you opened.

1. Quit KiCad fully (KiCad > Quit), then start it again so it scans the plugin folders.
2. Preferences > Plugins: **Enable KiCad API** is ticked. Keep the default interpreter (KiCad's
   bundled Python).
3. Open the project and its board in the PCB editor. Wait about a minute: the first time, KiCad
   creates the plugin's environment in
   `~/Library/Caches/KiCad/10.0/python-environments/com.github.somerleddesign.stripforge`
   (macOS) and installs kicad-python.
4. **Expect one StripForge button** on the PCB editor toolbar: Dialog. Analyze, Build strips,
   Add links, Run DRC, Build sheet and Restore last are inside that window. The Report wraps
   long lines. If the button is missing: check the status-bar warnings, and Preferences >
   Plugins > "Recreate Plugin Environment", then reopen the PCB editor.
5. If the project has an X56-style config, copy it next to the board as `stripboard.toml` (a single other `*.toml`, e.g. `X56.toml`, is also picked up; the report says which config was used).
6. Click **StripForge: Dialog**.
   - Expect **Larger text**, **Smaller text**, and **Reset text size** at the top. Larger text
     should make the labels, fields, buttons, and report bigger, and the size should still be
     bigger the next time the dialog opens. Unsaved board numbers must still be there after a
     size change. Command-plus, Command-minus, and Command-0 do the same on a Mac.
   - Expect a **Board** tab and a **Backup** tab. **Edit stripboard.toml** opens the same file in
     a resizable window (Save, Reload, Cancel). `[bend]` is millimetres. Invalid text leaves the
     file unchanged and keeps what you typed. Cancel discards unsaved text. The window does not
     open while the Board fields or the Backup choice have unsaved edits. Origin X/Y are the centre of hole A1, not the
     corner of the Edge.Cuts rectangle. Change origin, rows, columns, or pitch and click
     **Save board settings**. An invalid value leaves the file unchanged. **Reload** discards
     unsaved edits. With unsaved edits, Analyze and Build strips do not run.
   - The Backup tab still offers move (the default; save points are kept) and retention.
   - Expect action buttons for Analyze, Build strips, Add links, Run DRC, Build sheet and Restore
     last, plus Move / Retention and a Keep value.
   - Choose Retention, set Keep to 2, click **Save backup policy**. Expect the report to say it
     was saved, and expect no zip to disappear yet. Set it back to Move and save again if this
     project should keep every save point. The board form must be saved or reloaded first if it
     says there are unsaved edits.
   - Click **Analyze** in the window. Expect the report pane (not a second copy of the settings)
     to start with `Board:` and include hole labels (`A1`…).
7. The toolbar does not have a separate Analyze button. Use **Analyze** in the Dialog. The
   Report pane should include `Config:` and `Netlist:` when a schematic is present, and hole
   labels (`A1`…). Long lines wrap; there is no horizontal scrollbar.
8. Click **Build strips** in the Dialog (open the board from the KiCad project manager, so F8 works).
   - Expect a "Save and build" dialog (OK/Cancel), then the report with "Built <name>.kicad_pcb in
     place. Reloaded the built board in the PCB editor." and "Backup: …/stripforge/<Project>-stripforge-backups/
     <Project>-stripforge-YYYY-MM-DD_HHMMSS.zip (save point: …)".
   - The open board now shows B.Cu strips, the cut markers on User.1 and the hole pads.
   - With `place_links = true` (the default) the W links are already on their holes. Click
     **Add links** in the Dialog: expect "Added N StripForge:Link symbol(s)", a
     "Backup: ….zip (save point: the sheets just before this …)" line and the "close the Schematic Editor WITHOUT saving and
     open it again" note. Reopen the schematic and check the W symbols (right of the drawing),
     then press F8: expect no new or moved footprints.
   - Click **Build strips** again (a rebuild). A second zip appears in the backups folder; the
     first is unchanged (nothing is renamed).
   - Click **Restore last** in the Dialog. Expect a "Step back to this save point?" confirmation
     naming the newest zip, then "Loaded save point …" and "Reloaded the restored board in the PCB
     editor: respawned where you left off". The board is back to just before the rebuild. A
     `*-stripforge-safety-*.zip` appears beside the save points; the save-point zips are unchanged.
     Click it again: the next earlier save point loads (the board from before the first build, when
     that is the only older one). One more click reports "nothing further to restore" and leaves
     the board and the zips as they are. `stripforge restore <board> --list` / `--backup <zip>`
     still names a zip directly.
9. Click **Run DRC** in the Dialog.
   - Expect "parity: checked against the schematic" (the board is `<name>.kicad_pcb`, so kicad-cli
     uses `<name>.kicad_sch` directly).
   - After pass 1, unconnected equals the links still to add. After pass 2 it should be 0.
10. Click **Build sheet** in the Dialog. Expect the browser to open `stripforge/<Project>-stripforge.sheet.html`,
   with a `.pdf` beside it if Google Chrome is installed. Print one page to check the scale ruler.
11. Error path: close the board (or start the action from an unsaved new board). Expect a readable
    message, not a silent failure.

Report anything unexpected along with the dialog text ("Copy report" button). To uninstall a hand
install, delete `~/Documents/KiCad/10.0/plugins/com.github.somerleddesign.stripforge/`. KiCad
recreates nothing once it's gone; its cached environment under `~/Library/Caches/KiCad/10.0/`
can be deleted too.
