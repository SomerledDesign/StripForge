# StripForge 0.3.0

The PCB editor toolbar has one StripForge button, **Dialog**. Analyze, Build strips, Add links, Run DRC, Build sheet and Restore last are inside that window. The Report wraps long lines.

## Install

Install both packages with Plugin and Content Manager > Install from File, libraries first, then the plugin. Restart KiCad.

1. `StripForge-libraries-0.3.0-pcm.zip` — sha256 `64b69707a27b35c354e3a17c23b40c1767811e7546e6e4f71ce2cf1d0292b4f8`, 10826948 bytes
2. `StripForge-0.3.0-pcm.zip` — sha256 `f6d388031b0e317aae9268924bcd68abe17e048a18a50e039e9c1bd9c7da8783`, 611642 bytes

KiCad's manual says a library package is added to the global tables with the `PCM_` nickname prefix by default. That was not clicked for this release. If the rows are `PCM_StripForge`, rename them to `StripForge`. If they are missing, add nickname `StripForge` at:

- `${KICAD10_3RD_PARTY}/footprints/com_github_somerleddesign_stripforge_libraries/StripForge.pretty`
- `${KICAD10_3RD_PARTY}/symbols/com_github_somerleddesign_stripforge_libraries/StripForge.kicad_sym`

The straight-link STEP and WRL files are installed with the libraries package. Each `Link_P*` footprint names the matching `.step`.

## Changes

- Shared dialog with Board and Backup tabs. Backup policy defaults to move, which never deletes save points. Retention is opt-in.
- Raw `stripboard.toml` editor on the Board tab, including `[bend]`.
- Restore last walks back through earlier save points to the first one.
- Text size from 100% to 300%. 100% is the system font (at least 13pt on macOS). Trackpad pinch zoom works on a Mac.
- Add links keeps the footprint's symbol uuid.
- Separate libraries package with straight-link 3D models.
- The Report word-wraps. It stays read-only and copyable, and follows the text-size setting.

Checksums are in `SHA256SUMS` attached to this release.
