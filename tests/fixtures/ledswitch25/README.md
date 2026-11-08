# LedSwitch25 acceptance archive

`LedSwitch25-kicad.zip` is the only current StripForge circuit acceptance project. It is the
bytes of Kevin's `Niveus Dropbox/KiCad/LedSwitch25-kicad.zip` from 2026-11-07 23:22:26 EST.

- File: `LedSwitch25-kicad.zip`
- Size: 25859 bytes
- sha256: `cf5b92d393f2172d25f48329f92281ed4a41d2f8a8290fcf72f68dd8cbb09e6c`

Check that hash before use. Extract a new copy for each run. Do not edit, replace, or re-zip
this file. On extract, skip `__MACOSX`, `.DS_Store`, and lock files (`*.lck`, names starting
with `~`). Leave those entries inside the zip. This archive has no KiCad backup folder.

The previous acceptance zip is kept next to it and is not current:

- File: `LedSwitch25-kicad-2026-11-07-2158.zip`
- Size: 53157 bytes
- sha256: `a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592`
- Source mtime: 2026-11-07 21:58:07 EST

The `stripboard.toml` comments still describe a 25 by 25 board with hole A1 at (1.27, 1.27) mm.
Those comments are stale. The settings in the file are `rows = 13`, `cols = 13`,
`origin_mm = [129.54, 104.14]`, `pitch_mm = 2.54`, `output = "separate"`,
`link_lead_allowance_in = 0.15` (inches a leg), `backup_policy = "move"`, and `backup_keep = 0`.
Do not rewrite the file so the comments match, and do not turn on retention.
`tests/ledswitch_fixture.py` is how tests open the current zip.

`examples/tpi-fixture/` and `tests/fixtures/tpi-m1/` stay unit fixtures. They are not a
substitute for this archive, and neither is an older LedSwitch zip. The 9022-byte
`LedSwitch25-kicad.zip` that has appeared under agent artifacts is not this file.

On this placement SW1 is at (134.62, 129.08) mm. Its third pad is 0.920 mm off the nearest
hole. R2, R3, and R5 are 10.16 mm pad to pad. `stripforge build` with the archived
`output = "separate"` exits 2 and writes nothing. The previously approved working-copy line
`[bend]` / `SW1 = 0.46` (millimetres only, not in the zip) also exits 2: the part is then
0.460 mm off the grid, and `SW1 = 0.92` is over the loader's 0.5 mm maximum. No other
reference is bent.
