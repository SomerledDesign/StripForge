# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Kevin's current LedSwitch25 archive, the only circuit acceptance fixture.

The file ``tests/fixtures/ledswitch25/LedSwitch25-kicad.zip`` is the bytes of
``Niveus Dropbox/KiCad/LedSwitch25-kicad.zip`` (mtime 2026-11-08 19:58 MST, 26091 bytes,
sha256 below). It supersedes the 53157-byte archive kept beside it as
``LedSwitch25-kicad-2026-11-07-2158.zip``. Read the current file and check the hash. Extract a
new copy for each scenario. Do not write either zip, replace them, or pack a new one.
``__MACOSX``, ``.DS_Store``, and lock files are skipped on extract and are not deleted from the
archive.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "tests" / "fixtures" / "ledswitch25" / "LedSwitch25-kicad.zip"
ZIP_SHA256 = "b98f3c460938e215e9c3e6cbf87239d3cb8fce3c77d736c215f4c69600832209"
ZIP_SIZE = 26091
# LedSwitch25.kicad_pcb inside the current zip. This archive has no KiCad backup zip.
PCB_SHA256 = "67523a6f52b6d0ae74961c9b9216f4684ee65db5ad89f6560b55f8defff8d2a2"
HISTORICAL_ZIP = ARCHIVE.with_name("LedSwitch25-kicad-2026-11-07-2158.zip")
HISTORICAL_SHA256 = "a6686c6265ef05eb9a9ed1c85a405f472a3deab466ef3f64e4825496b0ba4592"
HISTORICAL_SIZE = 53157


def archive_bytes() -> bytes:
    """The canonical zip. Raises if the committed file is not Kevin's archive."""
    data = ARCHIVE.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != ZIP_SIZE or digest != ZIP_SHA256:
        raise AssertionError(
            f"{ARCHIVE} is {len(data)} bytes sha256 {digest}, not the LedSwitch25 acceptance archive "
            f"({ZIP_SIZE} bytes, {ZIP_SHA256})"
        )
    return data


def _skipped(name: str) -> bool:
    parts = PurePosixPath(name).parts
    if not parts or name.endswith("/"):
        return True
    if "__MACOSX" in parts or parts[0] == "__MACOSX":
        return True
    base = parts[-1]
    if base == ".DS_Store" or base.startswith("._"):
        return True
    if base.endswith(".lck") or base.startswith("~"):
        return True
    return False


def extract(dest: Path) -> Path:
    """Extract a new disposable project under ``dest``. Returns the project directory.

    ``dest`` is created. The canonical zip is only read, then checked again.
    """
    data = archive_bytes()
    dest.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            if _skipped(info.filename):
                continue
            rel = PurePosixPath(info.filename)
            if rel.is_absolute() or ".." in rel.parts:
                raise ValueError(f"archive entry {info.filename!r} points outside the extract")
            target = dest.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
    if ARCHIVE.read_bytes() != data:
        raise AssertionError("the LedSwitch25 acceptance zip changed while extracting")
    project = dest / "LedSwitch25"
    if not (project / "LedSwitch25.kicad_pcb").is_file():
        raise AssertionError("extract has no LedSwitch25.kicad_pcb")
    return project
