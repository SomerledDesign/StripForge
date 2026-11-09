# LedSwitch25 acceptance archive

`LedSwitch25-kicad.zip` is the only current StripForge circuit acceptance project. Kevin
approved this baseline from his 2026-11-08 archive with only `rows = 12` and `cols = 12`
changed in `stripboard.toml`. Every other member, including `__MACOSX` and `.DS_Store`, is
byte-identical to sha256 `b98f3c460938e215e9c3e6cbf87239d3cb8fce3c77d736c215f4c69600832209`.

- File: `LedSwitch25-kicad.zip`
- Size: 25819 bytes
- sha256: `4b6641a48ca721d24437f30cdbdc85c4168e3854628f75778c3d98b421883e3c`

Check that hash before use. Extract a new copy for each run. Do not edit, replace, or re-zip
this file. On extract, skip `__MACOSX`, `.DS_Store`, and lock files (`*.lck`, names starting
with `~`). Leave those entries inside the zip. This archive has no KiCad backup folder.

The previous acceptance zip is kept next to it and is not current:

- File: `LedSwitch25-kicad-2026-11-07-2158.zip`
- Size: 53157 bytes
- sha256: `a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592`
- Source mtime: 2026-11-07 21:58:07 EST

The `stripboard.toml` comments still describe a 25 by 25 board and a 63.5 mm outline, with hole
A1 at (1.27, 1.27) mm. Those comments are stale. Kevin left them as they are. The settings in
the file are `rows = 12`, `cols = 12`, `origin_mm = [129.54, 104.14]`, `pitch_mm = 2.54`,
`output = "in_place"`, `link_lead_allowance_in = 0.15` (inches a leg), `backup_policy = "move"`,
and `backup_keep = 0`. Do not rewrite the comments, and do not turn on retention.
`tests/ledswitch_fixture.py` is how tests open the current zip.

The board is 12 by 12 holes at 2.54 mm on a 1.3 in (33.02 mm) square. Edge.Cuts runs from
(127.00, 101.60) mm to (160.02, 134.62) mm. Hole A1 is 2.54 mm inside the left and top edges.
The last hole, L12, is 2.54 mm inside the right and bottom edges (100 mil margins all around).
A build writes that whole A1–L12 grid. The copper stays inside Edge.Cuts.

`examples/tpi-fixture/` and `tests/fixtures/tpi-m1/` stay unit fixtures. They are not a
substitute for this archive, and neither is an older LedSwitch zip. The 9022-byte
`LedSwitch25-kicad.zip` that has appeared under agent artifacts is not this file.

SW1 is `Button_Switch_THT:SW_Slide-03_Wuerth-WS-SLTV_10x2.5x6.4_P2.54mm` at (129.54, 127.00) mm.
Its pads are one pitch apart and land on holes, so the archived build does not need a `[bend]`
line. R2, R3, and R5 are 10.16 mm pad to pad. `stripforge build` follows `output = "in_place"`
and exits 0. No bend override is applied.
