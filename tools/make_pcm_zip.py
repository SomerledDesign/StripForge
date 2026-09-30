# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Build the KiCad Plugin and Content Manager (PCM) package: ``dist/StripForge-<version>-pcm.zip``.

    python tools/make_pcm_zip.py [--out dist] [--url https://.../StripForge-0.2.0-pcm.zip]

Archive layout (what the PCM expects for a ``plugin`` package; https://dev-docs.kicad.org/en/addons/):

    metadata.json            the repo's metadata.json with exactly one version, no download_* fields
    resources/icon.png       64 x 64 icon shown by the PCM
    plugins/plugin.json      the KiCad 10 IPC plugin (runtime python), five actions
    plugins/requirements.txt kicad-python etc., installed by KiCad into the plugin's venv
    plugins/sf_*.py, plugins/stripforge_plugin.py, plugins/icons/
    plugins/stripforge/      the StripForge package (src/stripforge, no caches)
    plugins/footprints/StripForge.pretty/, plugins/rules/stripforge.kicad_dru, plugins/LICENSE
    plugins/symbols/StripForge.kicad_sym  the generic StripForge:Link symbol (stripforge link-symbols)

The zip is deterministic (sorted entries, fixed timestamps and permissions), so the same tree always
gives the same sha256. It prints the sha256, download_size and install_size for the ``versions``
entry of the metadata in the PCM repository. Nothing is uploaded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STAMP = (2026, 1, 1, 0, 0, 0)
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
# What ci/validate/package.py in gitlab.com/kicad/addons/metadata allows in a plugin package.
ALLOWED = (re.compile(r"^metadata\.json$"), re.compile(r"^resources/icon\.png$"), re.compile(r"^plugins/.+"))


def versions(root: Path = ROOT) -> dict[str, str]:
    """The version in pyproject.toml, src/stripforge/__init__.py and metadata.json (must agree)."""
    py = re.search(r'^version\s*=\s*"([^"]+)"', (root / "pyproject.toml").read_text(), re.M)
    init = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "src/stripforge/__init__.py").read_text())
    meta = json.loads((root / "metadata.json").read_text())["versions"]
    return {
        "pyproject.toml": py.group(1) if py else "",
        "__init__.py": init.group(1) if init else "",
        "metadata.json": ",".join(v["version"] for v in meta),
    }


def _files(src: Path, dest: str) -> list[tuple[Path, str]]:
    if src.is_file():
        return [(src, dest)]
    out = []
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if p.is_dir() or SKIP_DIRS & set(rel.parts) or p.suffix in SKIP_SUFFIXES or p.name == ".DS_Store":
            continue
        out.append((p, f"{dest}/{rel.as_posix()}"))
    return out


def entries(root: Path = ROOT) -> list[tuple[Path, str]]:
    """(source file, archive path) for everything in the package, sorted by archive path."""
    plugin = [
        p
        for p in sorted((root / "plugins").iterdir())
        if p.name not in SKIP_DIRS and p.suffix not in SKIP_SUFFIXES and p.name != ".DS_Store"
    ]
    items: list[tuple[Path, str]] = [(root / "resources/icon.png", "resources/icon.png")]
    for p in plugin:
        items += _files(p, f"plugins/{p.name}")
    items += _files(root / "src/stripforge", "plugins/stripforge")
    items += _files(root / "footprints/StripForge.pretty", "plugins/footprints/StripForge.pretty")
    items += _files(root / "rules/stripforge.kicad_dru", "plugins/rules/stripforge.kicad_dru")
    items += _files(root / "symbols/StripForge.kicad_sym", "plugins/symbols/StripForge.kicad_sym")
    items += _files(root / "LICENSE", "plugins/LICENSE")
    return sorted(items, key=lambda t: t[1])


def package_metadata(root: Path = ROOT) -> dict:
    """metadata.json for inside the zip: a single version and no download_* fields."""
    meta = json.loads((root / "metadata.json").read_text())
    if len(meta["versions"]) != 1:
        raise SystemExit("metadata.json must list exactly one version for the package archive")
    meta["versions"] = [{k: v for k, v in meta["versions"][0].items() if not k.startswith("download_")}]
    return meta


def _info(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, STAMP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o100644 << 16
    zi.create_system = 3  # unix, so the permissions above are used
    return zi


def build(out_dir: Path, root: Path = ROOT) -> tuple[Path, dict]:
    """Write the zip; return its path and the numbers for the metadata ``versions`` entry."""
    vers = versions(root)
    if len(set(vers.values())) != 1:
        raise SystemExit(f"version mismatch: {vers}")
    version = vers["metadata.json"]
    meta = json.dumps(package_metadata(root), indent=4, ensure_ascii=False).encode() + b"\n"
    items = entries(root)
    for _, name in items:
        if not any(r.match(name) for r in ALLOWED):
            raise SystemExit(f"not allowed in a PCM plugin package: {name}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"StripForge-{version}-pcm.zip"
    install = len(meta)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(_info("metadata.json"), meta)
        for src, name in items:
            data = src.read_bytes()
            install += len(data)
            z.writestr(_info(name), data)
    data = out.read_bytes()
    info = {
        "version": version,
        "download_sha256": hashlib.sha256(data).hexdigest(),
        "download_size": len(data),
        "install_size": install,
    }
    return out, info


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory (default: dist/)")
    ap.add_argument("--url", help="download URL to put in the printed versions entry")
    args = ap.parse_args(argv)
    out, info = build(Path(args.out))
    meta = package_metadata()
    entry = {**meta["versions"][0], **({"download_url": args.url} if args.url else {}), **info}
    print(f"wrote {out}")
    print(f"sha256        {info['download_sha256']}")
    print(f"download_size {info['download_size']}")
    print(f"install_size  {info['install_size']}")
    print("versions entry for the PCM repository metadata:")
    print(json.dumps(entry, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
