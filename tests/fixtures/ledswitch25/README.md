# LedSwitch25 acceptance archive

This zip is the only StripForge circuit acceptance project. It is the bytes of Kevin's
`Niveus Dropbox/KiCad/LedSwitch25-kicad.zip` from 2026-11-07 21:58:07 EST.

- File: `LedSwitch25-kicad.zip`
- Size: 53157 bytes
- sha256: `a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592`

Check that hash before use. Extract a new copy for each run. Do not edit, replace, or re-zip
this file. On extract, skip `__MACOSX`, `.DS_Store`, and lock files (`*.lck`, names starting
with `~`). Leave those entries inside the zip.

The `stripboard.toml` comments still describe a 25 by 25 board with hole A1 at (1.27, 1.27) mm.
The settings in the file are `rows = 13`, `cols = 13`, `origin_mm = [129.54, 104.14]`,
`backup_policy = "move"`, `backup_keep = 0`. Do not rewrite the file so the comments match, and
do not turn on retention. `tests/ledswitch_fixture.py` is how tests open it.

`examples/tpi-fixture/` and `tests/fixtures/tpi-m1/` stay unit fixtures. They are not a
substitute for this archive, and neither is an older LedSwitch zip.

Kevin (2026-11-08) approved one working-copy change, not a new archive: after a fresh extract,
append only

```toml
[bend]
SW1 = 0.46
```

`0.46` is millimetres. No other reference is bent. Comments, `rows = 13`, `cols = 13`,
`origin_mm = [129.54, 104.14]`, `backup_policy = "move"`, and `backup_keep = 0` stay. With that
line, `stripforge build --in-place` writes the board (exit 1: `Net-(D2-K)` is still unlinkable).
Without it, build exits 2 and writes nothing.
