# Submitting StripForge to KiCad's official Plugin and Content Manager (PCM)

StripForge is **not** submitted yet: the repository is private. This is the checklist for when it
goes public. Checked on 2026-09-27 against:

- Metadata repository README: <https://gitlab.com/kicad/addons/metadata/-/blob/main/README.md>
  (raw: <https://gitlab.com/kicad/addons/metadata/-/raw/main/README.md>)
- Its validator, `ci/validate/package.py`:
  <https://gitlab.com/kicad/addons/metadata/-/blob/main/ci/validate/package.py>
- KiCad add-on docs (packaging, `metadata.json`, schemas): <https://dev-docs.kicad.org/en/addons/>
- PCM schemas: <https://go.kicad.org/pcm/schemas/v1> and <https://go.kicad.org/pcm/schemas/v2>
- IPC plugin docs: <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/index.html>
  and the `plugin.json` schema <https://go.kicad.org/api/schemas/v1>

## What the package is

| Field | Value | Why |
|---|---|---|
| `type` | `plugin` | IPC plugins have no special type (v1 enum: `plugin`, `library`, `colortheme`) |
| `versions[].runtime` | `ipc` | marks a KiCad 9.0.1+ IPC plugin (the other value is `swig`) |
| `versions[].kicad_version` | `10.0` | required; StripForge needs KiCad 10 (kicad-cli 10 DRC JSON, IPC API) |
| `identifier` | `com.github.somerleddesign.stripforge` | must be reverse-DNS on the code host (`com.github.<user>.<package>`), unique, 2–50 characters; the schema allows letters, digits, dots and dashes (no `_`) |
| `license` | `GPL-3.0` | the schema's licence enum has no `-or-later` form; `GPL-3.0` is the closest value, the LICENSE file says "or later" |
| `$schema` | v2 | the add-on docs say new packages should target v2; `plugin` is a v1 type too, so the official repository also serves it in its v1 lists (KiCad 10.0.4 ships both `pcm.v1` and `pcm.v2` schemas) |

Archive layout (`tools/make_pcm_zip.py`): `metadata.json`, `resources/icon.png` (64 × 64 PNG),
`plugins/**` (`plugin.json`, `requirements.txt`, entry scripts, icons, the `stripforge` package,
the CUT/Link footprints and the DRC rules). For a `plugin` package the validator allows only
`/metadata.json`, `/resources/icon.png` and `/plugins/**`.

**The footprint library can't be registered from the plugin package.** `footprints/*.pretty` is
allowed only in a `library`-type package, and only library packages get added to the footprint
library table. StripForge's own output does not need the table (it embeds the CUT and Link
footprints in the board). But the schematic `W` jumpers name `StripForge:Link_P…` footprints for
F8. A user either adds `StripForge.pretty` to their table by hand or installs a separate
`library` package, e.g. `com.github.somerleddesign.stripforge-library`. PCM-installed libraries
get a nickname prefix (`PCM_` by default, set in Preferences > Plugin and Content Manager), so the
link report would need to print `PCM_StripForge:Link_…`. That's open.

## Requirements (dev-docs.kicad.org/en/addons, "Submission to the official repository")

1. **Public, trackable source.** The `download_url` must be publicly accessible, and the source must be hosted somewhere with issue tracking (GitHub is fine). Make
   `github.com/SomerledDesign/StripForge` public first.
2. **Open-source licence.** Code packages need an open-source licence compatible with the GNU GPL. GPL-3.0-or-later qualifies.
3. **Issue tracker / contact.** Give a way to report problems (`resources.Issues` → GitHub issues)
   and a maintainer contact.
4. **English metadata.** `description` has at most 150 characters (ours has 136); `description_full` is required.
5. **Direct download.** Host the release zip where it has a stable direct-download URL (a GitHub
   release asset works; not a page that needs a click-through). Give its `download_sha256`,
   `download_size` and `install_size` in the version entry. The validator downloads the zip and
   checks the sha256, and both sizes to within ±1024 bytes.
6. **Package metadata consistency.** The `metadata.json` inside the zip must have the same
   identifier, exactly one version, the same `version` / `status` / `kicad_version` / `platforms`,
   and no `download_sha256` (`tools/make_pcm_zip.py` strips every `download_*` field).
7. **Never change a published version.** Once a version's zip is published, its sha256 and sizes
   are frozen. Fixes need a new version number.

## Steps

1. Bump the version in `pyproject.toml`, `src/stripforge/__init__.py` and `metadata.json`
   (`tools/make_pcm_zip.py` refuses to build if they differ). Set the version `status` to
   `stable` (or keep `testing`), then update the CHANGELOG.
2. Build the zip and note the numbers:

   ```sh
   python tools/make_pcm_zip.py --url https://github.com/SomerledDesign/StripForge/releases/download/v0.2.0/StripForge-0.2.0-pcm.zip
   ```

   It prints the sha256, download_size, install_size and a ready `versions` entry.
3. Test it locally: in KiCad, Plugin and Content Manager > **Install from File…**, pick the zip,
   restart KiCad and run through [KICAD-PLUGIN-TEST.md](KICAD-PLUGIN-TEST.md).
4. Tag and publish a GitHub release `v0.2.0` with the zip as an asset. Check that the asset URL
   downloads the exact same bytes (`shasum -a 256`).
5. Fork <https://gitlab.com/kicad/addons/metadata> on GitLab and create a branch (not `main`) in
   the fork, e.g. `add-stripforge`.
6. Add `packages/com.github.somerleddesign.stripforge/metadata.json` (the repo's `metadata.json`
   plus the version entry from step 2, with `download_url`, `download_sha256`, `download_size`,
   `install_size`) and `packages/com.github.somerleddesign.stripforge/icon.png` (copy of
   `resources/icon.png`).
7. Push the branch. The fork's CI pipeline (validate, then build) runs `ci/validate/package.py`
   (schema, allowed files, sha256/sizes, icon) and builds a test repository. Its job output gives
   a test repository URL: add it in KiCad's PCM (Manage repositories) and install from there.
8. Open a **merge request** from the branch to `kicad/addons/metadata` `main`. Describe the add-on
   and link the source repository. Answer the review; maintainers merge it, and the package then
   appears in the official repository.
9. Later versions: append a new entry to `versions` in the same `metadata.json` (never edit old
   entries) and open a new merge request.

A local dry run of the validator was done for 0.1.0 by serving the zip over `http://127.0.0.1`
and running `python ci/validate-package.py com.github.somerleddesign.stripforge metadata.json`
from a clone of the metadata repository. The result was **Validation passed**.
