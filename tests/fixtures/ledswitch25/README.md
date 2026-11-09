# LedSwitch25 acceptance archive

`LedSwitch25-kicad.zip` is the only current StripForge circuit acceptance project. It is the
bytes of Kevin's `Niveus Dropbox/KiCad/LedSwitch25-kicad.zip` from 2026-11-08 19:58 MST.

- File: `LedSwitch25-kicad.zip`
- Size: 26091 bytes
- sha256: `b98f3c460938e215e9c3e6cbf87239d3cb8fce3c77d736c215f4c69600832209`

Check that hash before use. Extract a new copy for each run. Do not edit, replace, or re-zip
this file. On extract, skip `__MACOSX`, `.DS_Store`, and lock files (`*.lck`, names starting
with `~`). Leave those entries inside the zip. This archive has no KiCad backup folder.

The previous acceptance zip is kept next to it and is not current:

- File: `LedSwitch25-kicad-2026-11-07-2158.zip`
- Size: 53157 bytes
- sha256: `a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592`
- Source mtime: 2026-11-07 21:58:07 EST

The `stripboard.toml` comments still describe a 25 by 25 board and a 63.5 mm outline, with hole
A1 at (1.27, 1.27) mm. Those comments are stale. The settings in the file are `rows = 13`,
`cols = 13`, `origin_mm = [129.54, 104.14]`, `pitch_mm = 2.54`, `output = "in_place"`,
`link_lead_allowance_in = 0.15` (inches a leg), `backup_policy = "move"`, and `backup_keep = 0`.
Do not rewrite the file so the comments match, and do not turn on retention.
`tests/ledswitch_fixture.py` is how tests open the current zip.

Kevin described this board as 13 by 13 holes on a 1.5 in (38.1 mm) square outline. The archived
Edge.Cuts rectangle is not that. It runs from (127.00, 101.60) mm to (160.02, 134.62) mm, so
both spans are 33.020 mm (1.300 in). Hole A1 is 2.54 mm inside the near edges. The last hole
centres of the 13 by 13 grid sit on the far edges (margin 0). The file was not edited to enlarge
the outline. Because those far holes are on the edge, a build writes copper only for the holes
inside the outline (A1-L12), not a full 13 by 13.

`examples/tpi-fixture/` and `tests/fixtures/tpi-m1/` stay unit fixtures. They are not a
substitute for this archive, and neither is an older LedSwitch zip. The 9022-byte
`LedSwitch25-kicad.zip` that has appeared under agent artifacts is not this file.

SW1 is `Button_Switch_THT:SW_Slide-03_Wuerth-WS-SLTV_10x2.5x6.4_P2.54mm` at (129.54, 127.00) mm.
Its pads are one pitch apart and land on holes, so the archived build does not need a `[bend]`
line. R2, R3, and R5 are 10.16 mm pad to pad. `stripforge build` follows `output = "in_place"`
and exits 0. No bend override is applied.
