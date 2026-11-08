"""tools/make_pcm_zip.py: the PCM package archive (layout, metadata, determinism)."""

from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def mk():
    spec = importlib.util.spec_from_file_location("make_pcm_zip", ROOT / "tools" / "make_pcm_zip.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["make_pcm_zip"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_versions_agree(mk):
    v = mk.versions()
    assert len(set(v.values())) == 1, v
    assert v["pyproject.toml"] == "0.2.1"


def test_repo_metadata_is_an_ipc_plugin(mk):
    meta = json.loads((ROOT / "metadata.json").read_text())
    assert meta["type"] == "plugin" and meta["identifier"] == "com.github.somerleddesign.stripforge"
    assert meta["license"] == "GPL-3.0"
    (ver,) = meta["versions"]
    assert ver["runtime"] == "ipc" and ver["kicad_version"] == "10.0"
    assert not any(k.startswith("download_") for k in ver)
    plugin = json.loads((ROOT / "plugins" / "plugin.json").read_text())
    assert plugin["identifier"] == meta["identifier"]


def test_zip_layout_and_determinism(mk, tmp_path):
    out, info = mk.build(tmp_path / "a")
    out2, info2 = mk.build(tmp_path / "b")
    assert out.name == "StripForge-0.2.1-pcm.zip"
    assert info == info2 and out.read_bytes() == out2.read_bytes()
    assert info["download_size"] == out.stat().st_size
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        assert info["install_size"] == sum(i.file_size for i in z.infolist())
        meta = json.loads(z.read("metadata.json"))
        assert all(any(r.match(n) for r in mk.ALLOWED) for n in names)
        assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
        for required in (
            "resources/icon.png",
            "plugins/plugin.json",
            "plugins/requirements.txt",
            "plugins/stripforge_plugin.py",
            "plugins/sf_build.py",
            "plugins/stripforge/__init__.py",
            "plugins/stripforge/buildsheet.py",
            "plugins/footprints/StripForge.pretty/CUT_Hole.kicad_mod",
            "plugins/rules/stripforge.kicad_dru",
            "plugins/symbols/StripForge.kicad_sym",
            "plugins/LICENSE",
            "footprints/StripForge.pretty/CUT_Hole.kicad_mod",
            "footprints/StripForge.pretty/Link_P2.54.kicad_mod",
            "symbols/StripForge.kicad_sym",
            "3dmodels/StripForge.3dshapes/Link_P2.54.wrl",
            "3dmodels/StripForge.3dshapes/Link_P2.54.step",
            "3dmodels/StripForge.3dshapes/Link_P81.28.wrl",
            "3dmodels/StripForge.3dshapes/Link_P81.28.step",
        ):
            assert required in names
        models = [n for n in names if n.startswith("3dmodels/")]
        assert len([n for n in models if n.endswith(".wrl")]) == 32
        assert len([n for n in models if n.endswith(".step")]) == 32
        assert not any(n.startswith("/") or ":\\" in n or "Dropbox" in n for n in models)
        pretty = [n for n in names if n.startswith("footprints/")]
        private = [n for n in names if n.startswith("plugins/footprints/")]
        assert len(pretty) == len(private) == 339
        for name in pretty:
            assert z.read(name) == z.read("plugins/" + name)
    assert len(meta["versions"]) == 1 and "download_sha256" not in meta["versions"][0]
