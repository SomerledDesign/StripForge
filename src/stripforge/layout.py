# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Where StripForge keeps its files (M4b): everything in ``<project>/stripforge/``.

The project folder is the one holding the ``.kicad_pro`` (KiCad's ``${KIPRJMOD}``), normally the
board's own folder. StripForge writes there only the board itself (built in place) and its
``.kicad_dru`` (KiCad reads the rules next to the board); everything else goes to
``<project>/stripforge/`` (``output_dir``), named ``<Project>-stripforge.<kind>``:

    stripforge/stripboard.toml                       the config (also looked for at the project root)
    stripforge/<Project>-stripforge.log              the last report of the KiCad plugin
    stripforge/<Project>-stripforge.links.json/.csv/.txt
    stripforge/<Project>-stripforge.sheet.html/.pdf/.copper.svg/.component.svg/.cuts.csv/.*.png
    stripforge/<Project>-stripforge.kicad_pcb        output = "separate" only
    stripforge/<Project>-stripforge-backups/         (``backup_dir``) the save points:
        <Project>-stripforge-YYYY-MM-DD_HHMMSS.zip

Each save point is a new zip, never a renamed one (renames make Dropbox "conflicted copies"); its
``stripforge-backup.json`` names the files it holds (paths relative to the project folder) and the
ones that did not exist yet, so :func:`restore` can put the project back exactly as it was.
``backup_keep`` = N keeps the newest N zips. The ``-stripforge-`` prefix keeps them apart from
KiCad's own ``<Project>-YYYY-MM-DD_HHMMSS.zip`` backups.
"""

from __future__ import annotations

import json
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

OUTPUT_DIR = "stripforge"
TAG = "-stripforge"
CONFIG_NAME = "stripboard.toml"
MANIFEST = "stripforge-backup.json"
ZIP_RE = re.compile(r"^(?P<p>.+)-stripforge-(?P<ts>\d{4}-\d\d-\d\d_\d{6})(?:-(?P<n>\d+))?\.zip$")


def check_relative(value: str, key: str) -> str:
    """``output_dir`` / ``backup_dir``: a relative path inside the project, no ``..``."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a folder name relative to the project, got {value!r}")
    p = PurePosixPath(value.replace("\\", "/"))
    if p.is_absolute() or re.match(r"^[A-Za-z]:", value) or ".." in p.parts:
        raise ValueError(f"{key} must be relative to the project folder (not absolute, no ..), got {value!r}")
    return p.as_posix()


def _stem(board: Path) -> str:
    from .writer import base_stem

    return base_stem(board)


def project_dir(board: str | Path) -> Path:
    """The folder with the ``.kicad_pro``: the board's folder, or a parent (a board in
    ``stripforge/``); else the board's folder (or its parent, for ``.../stripforge/x.kicad_pcb``)."""
    b = Path(board).absolute()
    for d in (b.parent, b.parent.parent, b.parent.parent.parent):
        if any(d.glob("*.kicad_pro")):
            return d
    return b.parent.parent if b.parent.name == OUTPUT_DIR else b.parent


def project_name(board: str | Path) -> str:
    """``LoadMan``: the ``.kicad_pro`` that matches the board (or the only one), else the board's name."""
    b = Path(board)
    stem = _stem(b)
    pros = sorted(project_dir(b).glob("*.kicad_pro"))
    for p in pros:
        if p.stem == stem:
            return stem
    if len(pros) == 1:
        return pros[0].stem
    return stem


def _cfg_dirs(cfg) -> tuple[str, str | None]:
    out = getattr(cfg, "output_dir", None) or OUTPUT_DIR
    return out, getattr(cfg, "backup_dir", None)


def output_dir(board: str | Path, cfg=None) -> Path:
    return project_dir(board) / _cfg_dirs(cfg)[0]


def backup_dir(board: str | Path, cfg=None) -> Path:
    out, bak = _cfg_dirs(cfg)
    if bak:
        return project_dir(board) / bak
    return project_dir(board) / out / f"{project_name(board)}{TAG}-backups"


def out_file(board: str | Path, kind: str, cfg=None) -> Path:
    """``<project>/stripforge/<Project>-stripforge.<kind>`` (``kind``: "log", "links.json", ...)."""
    return output_dir(board, cfg) / f"{project_name(board)}{TAG}.{kind}"


def legacy_file(board: str | Path, kind: str) -> Path:
    """Where 0.2.x put the same file: next to the board, ``<name>-stripforge.<kind>``."""
    b = Path(board)
    return b.with_name(_stem(b) + TAG + "." + kind)


def find_config(board: str | Path) -> Path | None:
    """``stripforge/stripboard.toml``, then ``stripboard.toml`` at the project root (or next to the board)."""
    b = Path(board)
    for d in dict.fromkeys([project_dir(b) / OUTPUT_DIR, project_dir(b), b.parent]):
        if (d / CONFIG_NAME).is_file():
            return d / CONFIG_NAME
    return None


# --- save points ---------------------------------------------------------------------------------


@dataclass
class SavePoint:
    path: Path
    files: list[str] = field(default_factory=list)  # in the zip (relative to the project folder)
    absent: list[str] = field(default_factory=list)  # did not exist: restore deletes them
    pruned: list[str] = field(default_factory=list)  # older zips deleted by backup_keep
    warnings: list[str] = field(default_factory=list)


def _rel(path: Path, root: Path) -> str:
    try:
        return Path(path).absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        raise ValueError(f"{path} is outside the project folder {root}") from exc


def backups(board: str | Path, cfg=None) -> list[Path]:
    """The save-point zips, oldest first."""
    d = backup_dir(board, cfg)
    name = project_name(board)
    found = []
    if d.is_dir():
        for f in d.iterdir():
            m = ZIP_RE.match(f.name)
            if m and m.group("p") == name and f.is_file():
                found.append(((m.group("ts"), int(m.group("n") or 0)), f))
    return [f for _, f in sorted(found)]


def save_point(board: str | Path, files, cfg=None, action: str = "build", keep: int | None = None,
               now: float | None = None) -> SavePoint:  # fmt: skip
    """Zip ``files`` (the ones about to be written) into a new
    ``<Project>-stripforge-YYYY-MM-DD_HHMMSS.zip``; missing ones are recorded as absent. Never
    overwrites: a second zip in the same second gets ``-2``, ``-3``, ... Raises OSError."""
    root = project_dir(board)
    d = backup_dir(board, cfg)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(now))
    base = f"{project_name(board)}{TAG}-{stamp}"
    path, n = d / f"{base}.zip", 1
    while path.exists():
        n += 1
        path = d / f"{base}-{n}.zip"
    sp = SavePoint(path)
    for f in dict.fromkeys(Path(f).absolute() for f in files):
        (sp.files if f.is_file() else sp.absent).append(_rel(f, root))
    manifest = {"stripforge_backup": 1, "action": action, "created": stamp, "files": sp.files,
                "absent": sp.absent}  # fmt: skip
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "x", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=2) + "\n")
        for rel in sp.files:
            z.write(root / rel, rel)
    tmp.rename(path)  # a fresh name: never replaces an existing file
    keep = getattr(cfg, "backup_keep", 0) if keep is None else keep
    if keep and keep > 0:
        for old in backups(board, cfg)[:-keep]:
            try:
                old.unlink()
                sp.pruned.append(old.name)
            except OSError as exc:
                sp.warnings.append(f"backup_keep: could not delete {old.name} ({exc})")
    return sp


def read_manifest(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        if MANIFEST in z.namelist():
            return json.loads(z.read(MANIFEST).decode("utf-8"))
        names = [n for n in z.namelist() if not n.endswith("/")]
    return {"files": names, "absent": [], "action": "unknown"}


def read_member(zip_path: Path, rel: str) -> str | None:
    with zipfile.ZipFile(zip_path) as z:
        try:
            return z.read(rel).decode("utf-8")
        except KeyError:
            return None


@dataclass
class Restored:
    zip: Path
    action: str
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)


def _safe(root: Path, rel: str) -> Path:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"backup entry {rel!r} points outside the project; not restoring")
    return root / Path(*p.parts)


def restore(board: str | Path, cfg=None, zip_path: str | Path | None = None) -> Restored:
    """Load a save point (the newest unless ``zip_path``): delete the files it recorded as not yet
    there (StripForge's work since), and unzip the rest under their own names. The zip is kept.
    Raises FileNotFoundError when there is no save point, ValueError for a bad zip."""
    root = project_dir(board)
    if zip_path is None:
        found = backups(board, cfg)
        if not found:
            raise FileNotFoundError(f"no save point yet in {backup_dir(board, cfg)}")
        zip_path = found[-1]
    zip_path = Path(zip_path)
    man = read_manifest(zip_path)
    res = Restored(zip_path, str(man.get("action", "unknown")))
    targets = [(rel, _safe(root, rel)) for rel in man.get("files", [])]  # check all before touching any
    gone = [(rel, _safe(root, rel)) for rel in man.get("absent", [])]
    for rel, p in gone:
        if p.is_file():
            p.unlink()
            res.removed.append(rel)
    with zipfile.ZipFile(zip_path) as z:
        for rel, p in targets:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(z.read(rel))
            res.restored.append(rel)
    return res


def format_restore(res: Restored) -> str:
    lines = [f"Loaded save point {res.zip.name} (made before: {res.action})."]
    if res.restored:
        lines.append("Restored: " + ", ".join(res.restored))
    if res.removed:
        lines.append("Removed (written since that save): " + ", ".join(res.removed))
    lines.append("The save point is kept; older ones are in the same folder.")
    return "\n".join(lines)


def legacy_note(board: str | Path) -> str | None:
    """A note when 0.2.x files sit next to the board (they are left where they are)."""
    b = Path(board)
    stem = _stem(b)
    old = sorted(
        p.name
        for p in b.parent.glob(glob_escape(stem) + "-*")
        if p.is_file() and (p.name.startswith((stem + TAG + ".", stem + "-pre-stripbuild")))
    )
    if not old:
        return None
    more = ", ..." if len(old) > 6 else ""
    return (
        f"NOTE: files from an older StripForge sit next to the board ({', '.join(old[:6])}{more}); "
        f"they were left where they are. New files go to {OUTPUT_DIR}/ and save points to "
        f"{backup_dir(b).relative_to(project_dir(b)).as_posix()}/."
    )


def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)
