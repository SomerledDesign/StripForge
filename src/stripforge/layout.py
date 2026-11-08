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
Older zips are deleted only when ``backup_policy`` is ``"retention"`` and ``backup_keep`` is 1 or
more (``config.prune_keep``). ``"move"`` (the default, and a file that never sets the policy) keeps
every zip. The ``-stripforge-`` prefix keeps them apart from
KiCad's own ``<Project>-YYYY-MM-DD_HHMMSS.zip`` backups.

Restore last walks those zips from newest to oldest, one per call, until the first save point (the
board from before StripForge wrote strips, holes and tracks). It then stops and changes nothing.
Before it replaces any file it writes a ``*-stripforge-safety-*.zip`` of the current bytes. That
copy is kept and is not a step in the walk, so the next Restore last cannot toggle back onto it.
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
CURSOR_NAME = "restore-cursor.json"
SAFETY_ACTION = "safety"
ZIP_RE = re.compile(r"^(?P<p>.+)-stripforge-(?P<ts>\d{4}-\d\d-\d\d_\d{6})(?:-(?P<n>\d+))?\.zip$")
SAFETY_RE = re.compile(r"^(?P<p>.+)-stripforge-safety-(?P<ts>\d{4}-\d\d-\d\d_\d{6})(?:-(?P<n>\d+))?\.zip$")


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
               now: float | None = None, safety: bool = False) -> SavePoint:  # fmt: skip
    """Zip ``files`` (the ones about to be written) into a new
    ``<Project>-stripforge-YYYY-MM-DD_HHMMSS.zip``; missing ones are recorded as absent. Never
    overwrites: a second zip in the same second gets ``-2``, ``-3``, ... Raises OSError.

    ``safety=True`` writes ``<Project>-stripforge-safety-YYYY-MM-DD_HHMMSS.zip`` instead, does not
    prune, and is not a Restore last step. A safety copy in the same second still gets ``-2``.
    """
    root = project_dir(board)
    d = backup_dir(board, cfg)
    d.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(now))
    infix = f"{TAG}-safety-" if safety else f"{TAG}-"
    base = f"{project_name(board)}{infix}{stamp}"
    path, n = d / f"{base}.zip", 1
    while path.exists():
        n += 1
        path = d / f"{base}-{n}.zip"
    sp = SavePoint(path)
    for f in dict.fromkeys(Path(f).absolute() for f in files):
        (sp.files if f.is_file() else sp.absent).append(_rel(f, root))
    manifest = {
        "stripforge_backup": 1,
        "action": SAFETY_ACTION if safety else action,
        "created": stamp,
        "files": sp.files,
        "absent": sp.absent,
    }
    tmp = path.with_name(path.name + ".part")
    with zipfile.ZipFile(tmp, "x", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps(manifest, indent=2) + "\n")
        for rel in sp.files:
            z.write(root / rel, rel)
    tmp.rename(path)  # a fresh name: never replaces an existing file
    if safety:
        return sp
    from .config import prune_keep

    keep = prune_keep(cfg, keep)
    if keep > 0:
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
    safety: Path | None = None  # current bytes, zipped before anything was replaced
    earlier: int = 0  # save points still older than where this walk now sits
    warnings: list[str] = field(default_factory=list)


class RestoreExhausted(Exception):
    """Restore last is already at the oldest save point. The board was not changed."""

    def __init__(self, oldest: Path):
        self.oldest = oldest
        super().__init__(f"already at the first save point ({oldest.name}); nothing earlier was saved")


def _safe(root: Path, rel: str) -> Path:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts:
        raise ValueError(f"backup entry {rel!r} points outside the project; not restoring")
    return root / Path(*p.parts)


def _cursor_path(board: str | Path, cfg=None) -> Path:
    return backup_dir(board, cfg) / CURSOR_NAME


def read_restore_cursor(board: str | Path, cfg=None) -> dict | None:
    """``{"at": zip name, "seen": [zip names]}`` or None. ``at`` is the save point the board now
    matches in the walk. ``seen`` is every save point that existed then, plus any newer one already
    undone, so a later build is loaded once and the walk does not jump forward again."""
    path = _cursor_path(board, cfg)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("at"), str):
        return None
    seen = data.get("seen")
    if not isinstance(seen, list):
        seen = []
    return {"at": data["at"], "seen": [s for s in seen if isinstance(s, str)]}


def _write_restore_cursor(board: str | Path, cfg, record: dict) -> None:
    path = _cursor_path(board, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def select_restore_zip(board: str | Path, cfg=None) -> Path:
    """The save point the next Restore last loads, oldest-last.

    No cursor yet: the newest save point. After a restore, the next call is the save point just
    older than that, through to the first. A save point created afterwards (another build) is
    taken once, next, and does not restart the walk in the middle of history. Raises
    FileNotFoundError when nothing has been saved, RestoreExhausted at the first save point.
    """
    found = backups(board, cfg)
    if not found:
        raise FileNotFoundError(f"no save point yet in {backup_dir(board, cfg)}")
    rec = read_restore_cursor(board, cfg)
    names = {p.name for p in found}
    if rec is None or rec["at"] not in names:
        return found[-1]
    pending = [p for p in found if p.name not in set(rec["seen"])]
    if pending:
        return pending[-1]
    idx = next(i for i, p in enumerate(found) if p.name == rec["at"])
    if idx == 0:
        raise RestoreExhausted(found[0])
    return found[idx - 1]


def _remember_restore(board: str | Path, cfg, target: Path) -> int:
    """Record a successful restore. Returns how many save points are still older than the walk."""
    found = backups(board, cfg)
    names = [p.name for p in found]
    rec = read_restore_cursor(board, cfg)
    if target.name not in names:
        if rec is None or rec["at"] not in set(names):
            return 0
        return names.index(rec["at"])
    if rec is None or rec["at"] not in set(names):
        _write_restore_cursor(board, cfg, {"at": target.name, "seen": names})
        return names.index(target.name)
    seen = list(dict.fromkeys([*rec["seen"], target.name]))
    if target.name not in rec["seen"]:
        _write_restore_cursor(board, cfg, {"at": rec["at"], "seen": seen})
        return names.index(rec["at"])
    _write_restore_cursor(board, cfg, {"at": target.name, "seen": seen})
    return names.index(target.name)


def safety_copies(board: str | Path, cfg=None) -> list[Path]:
    """Safety-copy zips, oldest first. Not save points: Restore last does not load them."""
    d = backup_dir(board, cfg)
    name = project_name(board)
    found = []
    if d.is_dir():
        for f in d.iterdir():
            m = SAFETY_RE.match(f.name)
            if m and m.group("p") == name and f.is_file():
                found.append(((m.group("ts"), int(m.group("n") or 0)), f))
    return [f for _, f in sorted(found)]


def _load_plan(root: Path, zip_path: Path):
    """Read a save point completely. Raises before the caller has changed any project file."""
    man = read_manifest(zip_path)
    if not isinstance(man, dict):
        raise ValueError(f"backup {zip_path.name} has no save-point manifest")
    files = man.get("files") if isinstance(man.get("files"), list) else None
    absent = man.get("absent") if isinstance(man.get("absent"), list) else None
    if files is None or absent is None:
        raise ValueError(f"backup {zip_path.name} has no save-point manifest")
    blobs: dict[str, bytes] = {}
    with zipfile.ZipFile(zip_path) as z:
        have = set(z.namelist())
        for rel in files:
            if not isinstance(rel, str):
                raise ValueError(f"backup entry {rel!r} is not a path")
            _safe(root, rel)
            if rel not in have:
                raise ValueError(f"backup {zip_path.name} is missing {rel}")
            blobs[rel] = z.read(rel)
    targets = [(rel, _safe(root, rel), blobs[rel]) for rel in files]
    gone = []
    for rel in absent:
        if not isinstance(rel, str):
            raise ValueError(f"backup entry {rel!r} is not a path")
        gone.append((rel, _safe(root, rel)))
    return man, targets, gone


def _safety_inputs(root: Path, board: str | Path, targets, gone) -> list[Path]:
    """Files whose current bytes must survive a failed replace: the board, and anything this
    restore will overwrite or delete."""
    out: list[Path] = []
    seen: set[Path] = set()
    for _rel_name, path, _blob in targets:
        if path.is_file() and path.resolve() not in seen:
            out.append(path)
            seen.add(path.resolve())
    for _rel_name, path in gone:
        if path.is_file() and path.resolve() not in seen:
            out.append(path)
            seen.add(path.resolve())
    b = Path(board)
    if b.is_file() and b.resolve() not in seen:
        try:
            _rel(b, root)
        except ValueError:
            pass
        else:
            out.append(b)
    return out


def _rollback(root: Path, safety: Path, rels: list[str]) -> None:
    man = read_manifest(safety)
    saved = set(man.get("files") or [])
    with zipfile.ZipFile(safety) as z:
        for rel in rels:
            path = _safe(root, rel)
            if rel in saved:
                tmp = path.with_name(path.name + ".stripforge-restore-tmp")
                tmp.write_bytes(z.read(rel))
                tmp.replace(path)
            elif path.is_file():
                path.unlink()


def _undo_partial(root: Path, safety: SavePoint | None, written: list[str]) -> None:
    if not written:
        return
    if safety is None:
        for rel in written:
            path = _safe(root, rel)
            if path.is_file():
                path.unlink()
        return
    _rollback(root, safety.path, written)


def _apply(targets, gone, res: Restored) -> list[str]:
    written: list[str] = []
    for rel, path, blob in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".stripforge-restore-tmp")
        try:
            tmp.write_bytes(blob)
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()
        written.append(rel)
        res.restored.append(rel)
    for rel, path in gone:
        if not path.is_file():
            continue
        try:
            path.unlink()
        except OSError as exc:
            res.warnings.append(f"could not remove {rel} ({exc})")
        else:
            res.removed.append(rel)
    return written


def restore(board: str | Path, cfg=None, zip_path: str | Path | None = None) -> Restored:
    """Load the next earlier save point, or ``zip_path`` when it is given.

    The chosen zip is read fully first. The current board (and any file this restore will
    overwrite or delete) is safety-copied, then the saved bytes are put back under their own
    names and files the save point recorded as not yet there are removed. Existing save-point
    zips are not rewritten or deleted. A safety copy is not the next Restore last step.

    Raises FileNotFoundError when there is no save point, RestoreExhausted when the walk is
    already at the first one (nothing is changed), ValueError for a bad zip (also before any
    project file is changed).
    """
    root = project_dir(board)
    if zip_path is None:
        zip_path = select_restore_zip(board, cfg)  # may raise before any copy is made
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise FileNotFoundError(f"no save point at {zip_path}")
    man, targets, gone = _load_plan(root, zip_path)
    pre = _safety_inputs(root, board, targets, gone)
    safety = save_point(board, pre, cfg, safety=True) if pre else None
    res = Restored(zip_path, str(man.get("action", "unknown")))
    res.safety = None if safety is None else safety.path
    try:
        _apply(targets, gone, res)
    except Exception as exc:
        try:
            _undo_partial(root, safety, res.restored)
        except Exception as roll:
            raise OSError(
                f"{exc}; putting the previous bytes back from "
                f"{None if safety is None else safety.path.name} also failed ({roll})"
            ) from exc
        raise
    res.earlier = _remember_restore(board, cfg, zip_path)
    return res


def format_restore(res: Restored) -> str:
    lines = [f"Loaded save point {res.zip.name} (made before: {res.action})."]
    if res.action == SAFETY_ACTION:
        lines.append("This zip is a safety copy of a board Restore last was about to replace.")
    elif res.earlier == 0:
        lines.append(
            "This is the first save point: the board from before StripForge added its strips, "
            "holes and tracks. Restore last again will stop here without changing the board."
        )
    elif res.earlier == 1:
        lines.append(
            "1 earlier save point remains, back to the first (the board before StripForge built it)."
        )
    else:
        lines.append(
            f"{res.earlier} earlier save points remain, back to the first "
            "(the board before StripForge built it)."
        )
    if res.safety is not None:
        lines.append(
            f"Safety copy: {res.safety.name} (the board just before this restore; "
            "Restore last will not load it)."
        )
    if res.restored:
        lines.append("Restored: " + ", ".join(res.restored))
    if res.removed:
        lines.append("Removed (written since that save): " + ", ".join(res.removed))
    for warning in res.warnings:
        lines.append(f"WARNING: {warning}")
    lines.append("Every save point is kept; older ones are in the same folder.")
    return "\n".join(lines)


def format_exhausted(board: str | Path, oldest: Path, cfg=None) -> str:
    folder = backup_dir(board, cfg)
    return (
        "Result: nothing further to restore\n\n"
        f"Restore last is already at the first save point ({oldest.name}): "
        "the board from before StripForge's strips, holes and tracks. "
        f"Nothing was changed. Every save point is still in {folder}."
    )


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
