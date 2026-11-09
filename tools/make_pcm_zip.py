# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Build the two KiCad PCM packages KiCad's addon guide allows.

    python tools/make_pcm_zip.py [--out dist]

https://dev-docs.kicad.org/en/addons/ describes a plugin archive and a library archive as
different packages. The file rules are the ones in
https://gitlab.com/kicad/addons/metadata/-/blob/main/ci/validate/package.py

``dist/StripForge-<version>-pcm.zip`` is type ``plugin``:

    metadata.json, resources/icon.png, plugins/**

The running plugin keeps a private copy of the footprints, the symbol and the DRC rules under
``plugins/``. That copy is how the plugin reads them. It is not a footprint library KiCad registers.

``dist/StripForge-libraries-<version>-pcm.zip`` is type ``library``:

    metadata.json, resources/icon.png
    footprints/StripForge.pretty/*.kicad_mod
    symbols/StripForge.kicad_sym
    3dmodels/StripForge.3dshapes/*.{step,wrl}

KiCad extracts a library package to ``${KICAD10_3RD_PARTY}/<directory>/<library id>/``. The
straight-link footprints name that 3D path. The KiCad manual says a library package's libraries
are added to the global tables, with the ``PCM_`` nickname prefix by default. This script does
not run KiCad, so that registration is not checked.

Both zips are deterministic. Nothing is uploaded.
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
# Same members as ALLOWED_FILES in the KiCad metadata repository validator.
PLUGIN_FILES = (
    re.compile(r"^metadata\.json$"),
    re.compile(r"^resources/icon\.png$"),
    re.compile(r"^plugins/.+"),
)
LIBRARY_FILES = (
    re.compile(r"^metadata\.json$"),
    re.compile(r"^resources/icon\.png$"),
    re.compile(r"^footprints/[^/]+\.pretty/[^/]+\.kicad_mod$"),
    re.compile(r"^symbols/[^/]+\.kicad_sym$"),
    re.compile(r"^3dmodels/[^/]+\.3dshapes/[^/]+\.(?:stp|step|stpz|wrl|wrz|iges)$"),
)


def _library_id() -> str:
    """The library package id. One definition, shared with the footprint model path."""
    src = str(ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from stripforge.resources import LIBRARY_PCM_ID

    return LIBRARY_PCM_ID


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


def _plugin_entries(root: Path) -> list[tuple[Path, str]]:
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


def _library_entries(root: Path) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = [(root / "resources/icon.png", "resources/icon.png")]
    items += _files(root / "footprints/StripForge.pretty", "footprints/StripForge.pretty")
    items += _files(root / "symbols/StripForge.kicad_sym", "symbols/StripForge.kicad_sym")
    items += _files(root / "3dmodels/StripForge.3dshapes", "3dmodels/StripForge.3dshapes")
    return sorted(items, key=lambda t: t[1])


def package_metadata(root: Path = ROOT) -> dict:
    """Plugin metadata.json for inside the plugin zip: one version, no download_* fields."""
    meta = json.loads((root / "metadata.json").read_text())
    if len(meta["versions"]) != 1:
        raise SystemExit("metadata.json must list exactly one version for the package archive")
    meta["versions"] = [{k: v for k, v in meta["versions"][0].items() if not k.startswith("download_")}]
    return meta


def library_metadata(root: Path = ROOT) -> dict:
    """Library metadata. Same version as the plugin, no plugin runtime, its own identifier."""
    meta = package_metadata(root)
    (ver,) = meta["versions"]
    ver.pop("runtime", None)
    meta["versions"] = [ver]
    meta["identifier"] = _library_id()
    meta["type"] = "library"
    meta["name"] = "StripForge Libraries"
    meta["description"] = (
        "StripForge footprint library, Link symbol, and straight-link 3D models. "
        "Install this as well as the StripForge plugin."
    )
    meta["description_full"] = (
        "Footprints for StripForge wire links and cut markers (StripForge.pretty), "
        "the StripForge:Link symbol, and one STEP and one WRL model for each straight "
        "Link_P footprint. The footprint names the STEP under ${KICAD10_3RD_PARTY}/3dmodels/. "
        "KiCad's manual says a library package is added to the global library tables with the "
        "PCM_ nickname prefix by default. Rename that nickname to StripForge if it appears, "
        "so it matches the footprints the plugin writes. This package does not run the plugin."
    )
    return meta


def _info(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, STAMP)
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o100644 << 16
    zi.create_system = 3  # unix, so the permissions above are used
    return zi


def _zip(out: Path, meta: dict, items: list[tuple[Path, str]], allowed: tuple) -> dict:
    for _, name in items:
        if not any(r.match(name) for r in allowed):
            raise SystemExit(f"not allowed in {out.name}: {name}")
    raw = json.dumps(meta, indent=4, ensure_ascii=False).encode() + b"\n"
    install = len(raw)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(_info("metadata.json"), raw)
        for src, name in items:
            data = src.read_bytes()
            install += len(data)
            z.writestr(_info(name), data)
    blob = out.read_bytes()
    return {
        "version": meta["versions"][0]["version"],
        "download_sha256": hashlib.sha256(blob).hexdigest(),
        "download_size": len(blob),
        "install_size": install,
    }


def build(out_dir: Path, root: Path = ROOT) -> dict[str, tuple[Path, dict]]:
    """Write the plugin zip and the library zip. Return ``{"plugin": (path, info), "library": ...}``."""
    vers = versions(root)
    if len(set(vers.values())) != 1:
        raise SystemExit(f"version mismatch: {vers}")
    version = vers["metadata.json"]
    if _library_id() != LIBRARY_ID_EXPECTED:
        raise SystemExit("library package id does not match stripforge.resources.LIBRARY_PCM_ID")
    out_dir.mkdir(parents=True, exist_ok=True)
    plugin = out_dir / f"StripForge-{version}-pcm.zip"
    library = out_dir / f"StripForge-libraries-{version}-pcm.zip"
    return {
        "plugin": (plugin, _zip(plugin, package_metadata(root), _plugin_entries(root), PLUGIN_FILES)),
        "library": (library, _zip(library, library_metadata(root), _library_entries(root), LIBRARY_FILES)),
    }


# Compared with resources.LIBRARY_PCM_ID inside build(), so the two cannot drift unnoticed.
LIBRARY_ID_EXPECTED = "com.github.somerleddesign.stripforge.libraries"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / "dist"), help="output directory (default: dist/)")
    ap.add_argument("--url", help="download URL to put in the printed versions entry")
    args = ap.parse_args(argv)
    built = build(Path(args.out))
    for kind in ("library", "plugin"):
        out, info = built[kind]
        print(f"wrote {out}")
        print(f"sha256        {info['download_sha256']}")
        print(f"download_size {info['download_size']}")
        print(f"install_size  {info['install_size']}")
        print(json.dumps(info, indent=4))
    return 0


if __name__ == "__main__":
    sys.exit(main())
