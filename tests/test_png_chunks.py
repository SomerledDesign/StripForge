# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""PNG chunks KiCad's libpng would see. The PCM package must not carry iCCP or cHRM.

libpng's "iCCP: cHRM chunk does not match sRGB" warning needs a bad profile or a cHRM
chunk that disagrees with sRGB. Toolbar icons and resources/icon.png are the only PNGs
in the PCM zip.
"""

from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIG = b"\x89PNG\r\n\x1a\n"


def chunk_types(data: bytes) -> list[str]:
    assert data.startswith(SIG), "not a PNG"
    types = []
    i = 8
    while i + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[i : i + 8])
        types.append(kind.decode("ascii"))
        i += 12 + length
        if kind == b"IEND":
            break
    return types


def test_packaged_pngs_have_no_iccp_or_chrm():
    packaged = sorted((ROOT / "plugins" / "icons").glob("*.png"))
    packaged.append(ROOT / "resources" / "icon.png")
    assert packaged, "no plugin icons"
    for path in packaged:
        types = chunk_types(path.read_bytes())
        assert "iCCP" not in types, path.name
        assert "cHRM" not in types, path.name
    assert (ROOT / "plugins" / "icons" / "dialog-24.png").is_file()
    assert (ROOT / "plugins" / "icons" / "dialog-48.png").is_file()


def test_pcm_zip_entries_do_not_include_doc_screenshots():
    spec = importlib.util.spec_from_file_location("make_pcm_zip", ROOT / "tools" / "make_pcm_zip.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    names = [name for _src, name in mod.entries(ROOT)]
    pngs = [name for name in names if name.lower().endswith(".png")]
    assert pngs == sorted(pngs)
    assert "resources/icon.png" in pngs
    assert any(name.startswith("plugins/icons/") for name in pngs)
    assert not any(name.startswith("docs/") for name in pngs)
    assert not any("1024" in name or name.startswith("assets/") for name in pngs)
