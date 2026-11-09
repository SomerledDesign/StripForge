"""tools/make_pcm_zip.py: two PCM archives, plugin and library, each within KiCad's file rules."""

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
    assert v["pyproject.toml"] == "0.3.0"


def test_repo_metadata_is_an_ipc_plugin(mk):
    meta = json.loads((ROOT / "metadata.json").read_text())
    assert meta["type"] == "plugin" and meta["identifier"] == "com.github.somerleddesign.stripforge"
    assert meta["license"] == "GPL-3.0"
    (ver,) = meta["versions"]
    assert ver["runtime"] == "ipc" and ver["kicad_version"] == "10.0"
    assert not any(k.startswith("download_") for k in ver)
    plugin = json.loads((ROOT / "plugins" / "plugin.json").read_text())
    assert plugin["identifier"] == meta["identifier"]
    lib = mk.library_metadata()
    assert lib["type"] == "library" and lib["identifier"] == mk._library_id()
    assert "runtime" not in lib["versions"][0]
    assert lib["versions"][0]["version"] == ver["version"]
    assert lib["versions"][0]["kicad_version"] == "10.0"


def test_zip_layout_and_determinism(mk, tmp_path):
    first = mk.build(tmp_path / "a")
    second = mk.build(tmp_path / "b")
    for kind, allowed in (("plugin", mk.PLUGIN_FILES), ("library", mk.LIBRARY_FILES)):
        out, info = first[kind]
        out2, info2 = second[kind]
        assert info == info2 and out.read_bytes() == out2.read_bytes()
        assert info["download_size"] == out.stat().st_size
        with zipfile.ZipFile(out) as z:
            names = z.namelist()
            assert info["install_size"] == sum(i.file_size for i in z.infolist())
            meta = json.loads(z.read("metadata.json"))
            assert all(any(r.match(n) for r in allowed) for n in names)
            assert not any("__pycache__" in n or n.endswith(".pyc") for n in names)
            if kind == "plugin":
                for required in (
                    "resources/icon.png",
                    "plugins/plugin.json",
                    "plugins/stripforge_plugin.py",
                    "plugins/stripforge/__init__.py",
                    "plugins/footprints/StripForge.pretty/Link_P2.54.kicad_mod",
                    "plugins/symbols/StripForge.kicad_sym",
                    "plugins/rules/stripforge.kicad_dru",
                ):
                    assert required in names
                assert not any(n.startswith(("footprints/", "symbols/", "3dmodels/")) for n in names)
                assert meta["type"] == "plugin" and meta["versions"][0]["runtime"] == "ipc"
            else:
                for required in (
                    "resources/icon.png",
                    "footprints/StripForge.pretty/Link_P2.54.kicad_mod",
                    "footprints/StripForge.pretty/CUT_Hole.kicad_mod",
                    "symbols/StripForge.kicad_sym",
                    "3dmodels/StripForge.3dshapes/Link_P2.54.step",
                    "3dmodels/StripForge.3dshapes/Link_P2.54.wrl",
                    "3dmodels/StripForge.3dshapes/Link_P81.28.step",
                    "3dmodels/StripForge.3dshapes/Link_P81.28.wrl",
                ):
                    assert required in names
                assert not any(n.startswith("plugins/") for n in names)
                models = [n for n in names if n.startswith("3dmodels/")]
                assert len([n for n in models if n.endswith(".wrl")]) == 32
                assert len([n for n in models if n.endswith(".step")]) == 32
                assert not any("Dropbox" in n or n.startswith("/") for n in names)
                private = "plugins/footprints/StripForge.pretty/Link_P2.54.kicad_mod"
                public = "footprints/StripForge.pretty/Link_P2.54.kicad_mod"
                with zipfile.ZipFile(first["plugin"][0]) as plugin_zip:
                    assert plugin_zip.read(private) == z.read(public)
                assert meta["type"] == "library" and "runtime" not in meta["versions"][0]
                assert meta["identifier"].replace(".", "_") in z.read(public).decode()
        assert len(meta["versions"]) == 1 and "download_sha256" not in meta["versions"][0]
