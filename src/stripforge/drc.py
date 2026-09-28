# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""`stripforge drc`: run KiCad's DRC through ``kicad-cli`` and classify the results (Sketch.md §4.8).

KiCad 10's IPC API can't run DRC (Sketch.md §3), so this wraps
``kicad-cli pcb drc --format json --severity-all [--schematic-parity] -o <report> <board>``
(the flags of kicad-cli 10.0.4). The JSON has ``violations``, ``unconnected_items`` and
``schematic_parity`` lists; each item has a ``type``, ``severity``, ``description`` and ``items``.

Buckets:

* **shorts**: ``shorting_items``, ``tracks_crossing``;
* **clearance**: ``clearance``, ``copper_edge_clearance``, ``hole_clearance``, ``hole_to_hole``;
* **unconnected**: the ``unconnected_items`` list (a missing link or an extra cut);
* **parity**: the ``schematic_parity`` list (a W link out of sync with the schematic, …);
* **stripboard rules**: violations of a custom ``SF …`` rule from ``stripforge.kicad_dru``;
* **filtered** (expected noise, counted but not shown): ``track_dangling`` (dead strip ends are
  normal on stripboard) and ``lib_footprint_issues`` saying a library is not in the library table
  (StripForge embeds its footprints, so the StripForge library need not be configured);
* **link courtyards** (reported, not failing): ``courtyards_overlap`` / ``pth_inside_courtyard``
  involving a ``W`` link. A wire link lies flat on the component side and may run under a part;
  the link planner already avoids crossing courtyards where it can, so what is left is for review;
* **other**: everything else, e.g. silk warnings.

Real problems are shorts, clearance, unconnected, parity, stripboard-rule violations and any
other *error*-severity item; ``stripforge drc`` exits 1 when there are any.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

KICAD_CLI_MAC = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"

SHORT_TYPES = {"shorting_items", "tracks_crossing"}
CLEARANCE_TYPES = {"clearance", "copper_edge_clearance", "hole_clearance", "hole_to_hole"}
DANGLING_TYPES = {"track_dangling"}
MISSING_LIB_RE = re.compile(r"does not include the footprint library '([^']+)'")
SF_RULE_RE = re.compile(r"rule '(SF [^']+)'")
COURTYARD_TYPES = {"courtyards_overlap", "pth_inside_courtyard", "npth_inside_courtyard"}
LINK_ITEM_RE = re.compile(r"(?:Footprint|of) W\d+\b")
PARITY_FAILED_RE = re.compile(r"Failed to fetch schematic netlist|require a fully annotated schematic")

REAL = ("shorts", "clearance", "unconnected", "parity", "stripboard_rules")


class KiCadCliMissing(RuntimeError):
    pass


def find_kicad_cli(explicit: str | None = None) -> str | None:
    """``--kicad-cli``, then ``$KICAD_CLI``, then ``kicad-cli`` on PATH, then the macOS app bundle."""
    for cand in (explicit, os.environ.get("KICAD_CLI")):
        if cand:
            return cand if Path(cand).exists() or shutil.which(cand) else None
    found = shutil.which("kicad-cli")
    if found:
        return found
    return KICAD_CLI_MAC if Path(KICAD_CLI_MAC).exists() else None


@dataclass
class DrcResult:
    report: dict
    shorts: list[dict] = field(default_factory=list)
    clearance: list[dict] = field(default_factory=list)
    unconnected: list[dict] = field(default_factory=list)
    parity: list[dict] = field(default_factory=list)
    stripboard_rules: list[dict] = field(default_factory=list)
    link_courtyard: list[dict] = field(default_factory=list)
    other: list[dict] = field(default_factory=list)
    filtered_dangling: int = 0
    filtered_missing_library: dict[str, int] = field(default_factory=dict)
    parity_checked: bool | None = None  # None: not requested
    log: str = ""

    @property
    def other_errors(self) -> list[dict]:
        return [v for v in self.other if v.get("severity") == "error"]

    @property
    def real_count(self) -> int:
        return sum(len(getattr(self, k)) for k in REAL) + len(self.other_errors)

    @property
    def ok(self) -> bool:
        return self.real_count == 0

    def counts(self) -> dict:
        return {
            "shorts": len(self.shorts),
            "clearance": len(self.clearance),
            "unconnected": len(self.unconnected),
            "parity": len(self.parity),
            "stripboard_rules": len(self.stripboard_rules),
            "link_courtyard": len(self.link_courtyard),
            "other_errors": len(self.other_errors),
            "other_warnings": len(self.other) - len(self.other_errors),
            "filtered_track_dangling": self.filtered_dangling,
            "filtered_missing_library": sum(self.filtered_missing_library.values()),
            "parity_checked": self.parity_checked,
        }

    def unconnected_nets(self) -> dict[str, int]:
        """``{net: unconnected items}`` from the item descriptions (``PTH pad 1 [GND] of J1``)."""
        out: dict[str, int] = {}
        for v in self.unconnected:
            descs = [it.get("description", "") for it in v.get("items", [])]
            nets = {m.group(1) for d in descs if (m := re.search(r"\[(.*)\]", d))}
            for n in sorted(nets)[:1]:
                out[n] = out.get(n, 0) + 1
        return dict(sorted(out.items()))


def classify(report: dict, parity_requested: bool = False, log: str = "") -> DrcResult:
    res = DrcResult(report=report, log=log)
    for v in report.get("violations", []):
        t, desc = v.get("type", ""), v.get("description", "")
        if t in DANGLING_TYPES:
            res.filtered_dangling += 1
        elif t == "lib_footprint_issues" and (m := MISSING_LIB_RE.search(desc)):
            res.filtered_missing_library[m.group(1)] = res.filtered_missing_library.get(m.group(1), 0) + 1
        elif t in SHORT_TYPES:
            res.shorts.append(v)
        elif SF_RULE_RE.search(desc):
            res.stripboard_rules.append(v)
        elif t in CLEARANCE_TYPES:
            res.clearance.append(v)
        elif t in COURTYARD_TYPES and any(
            LINK_ITEM_RE.search(it.get("description", "")) for it in v.get("items", [])
        ):
            res.link_courtyard.append(v)
        else:
            res.other.append(v)
    res.unconnected = list(report.get("unconnected_items", []))
    res.parity = list(report.get("schematic_parity", []))
    if parity_requested:
        res.parity_checked = not PARITY_FAILED_RE.search(log)
    return res


PROJECT_FILES = ("sym-lib-table", "fp-lib-table")


def shadow_project(board_path: str | Path, schematic: str | Path, dest: str | Path) -> Path:
    """Copy ``board_path`` into ``dest`` under the schematic's name, with the schematic beside it.

    kicad-cli's schematic parity only looks for ``<board stem>.kicad_sch``, but a built board is a
    sibling such as ``fixture-stripforge.kicad_pcb``. This makes ``dest/<sch stem>.kicad_pcb`` (and its
    ``.kicad_dru``) next to copies of every ``.kicad_sch`` in the schematic's folder (sub-sheets),
    ``<sch stem>.kicad_pro`` and the project library tables, and returns the board copy's path.
    Nothing next to the real board or schematic is touched.
    """
    board, sch, dest = Path(board_path), Path(schematic), Path(dest)
    if not sch.is_file():
        raise FileNotFoundError(f"schematic not found: {sch}")
    dest.mkdir(parents=True, exist_ok=True)
    for f in sorted(sch.parent.glob("*.kicad_sch")):
        shutil.copy2(f, dest / f.name)
    for name in (sch.stem + ".kicad_pro", *PROJECT_FILES):
        if (sch.parent / name).is_file():
            shutil.copy2(sch.parent / name, dest / name)
    out = dest / (sch.stem + ".kicad_pcb")
    shutil.copy2(board, out)
    dru = board.with_suffix(".kicad_dru")
    if dru.is_file():
        shutil.copy2(dru, out.with_suffix(".kicad_dru"))
    return out


def run_drc(
    board_path: str | Path,
    kicad_cli: str | None = None,
    parity: bool = True,
    report_path: str | Path | None = None,
    schematic: str | Path | None = None,
) -> DrcResult:
    """Run kicad-cli DRC on ``board_path`` and classify it. Raises KiCadCliMissing or RuntimeError.

    ``schematic``: check parity against this ``.kicad_sch`` even though the board has another name
    (the DRC then runs on a copy, see :func:`shadow_project`).
    """
    cli = find_kicad_cli(kicad_cli)
    if cli is None:
        raise KiCadCliMissing("kicad-cli not found (set KICAD_CLI or pass --kicad-cli)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(report_path) if report_path else Path(tmp) / "drc.json"
        target = Path(board_path)
        if parity and schematic and Path(schematic).stem != target.stem:
            target = shadow_project(target, schematic, Path(tmp) / "project")
        cmd = [cli, "pcb", "drc", "--format", "json", "--severity-all", "--units", "mm"]
        if parity:
            cmd.append("--schematic-parity")
        cmd += ["-o", str(out), str(target)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        log = "\n".join(
            line for line in (proc.stdout + proc.stderr).splitlines() if not line.startswith("Fontconfig")
        )
        if proc.returncode != 0 or not out.exists():
            raise RuntimeError(f"kicad-cli pcb drc failed (exit {proc.returncode}):\n{log}")
        report = json.loads(out.read_text(encoding="utf-8"))
    return classify(report, parity_requested=parity, log=log)


def _where(v: dict, label=None) -> str:
    items = v.get("items", [])
    parts = []
    for it in items[:2]:
        pos = it.get("pos", {})
        at = f"({pos.get('x', 0):.2f}, {pos.get('y', 0):.2f})"
        if label is not None:
            at += f" near {label(pos.get('x', 0), pos.get('y', 0))}"
        parts.append(f"{it.get('description', '')} {at}")
    return "; ".join(parts)


def format_text(res: DrcResult, board: str, label=None, expected_links: int | None = None) -> str:
    c = res.counts()
    out = [f"StripForge DRC: {board} (kicad-cli {res.report.get('kicad_version', '?')})"]
    out.append(
        f"  shorts {c['shorts']}, clearance {c['clearance']}, unconnected {c['unconnected']}, "
        f"parity {c['parity']}, stripboard rules {c['stripboard_rules']}, other errors {c['other_errors']}"
    )
    libs = ", ".join(f"{k} x{n}" for k, n in sorted(res.filtered_missing_library.items()))
    out.append(
        f"  filtered (expected): {res.filtered_dangling} track_dangling (dead strip ends)"
        + (f", {c['filtered_missing_library']} library-not-configured ({libs})" if libs else "")
    )
    if res.link_courtyard:
        out.append(
            f"  link courtyards (review, not failing): {len(res.link_courtyard)} item(s) with a W link "
            "over a part courtyard"
        )
    if res.parity_checked is False:
        out.append("  parity: NOT checked (kicad-cli found no schematic next to the board; pass --schematic)")
    elif res.parity_checked:
        out.append("  parity: checked against the schematic")
    for name in REAL:
        items = getattr(res, name)
        if name == "unconnected":
            if items:
                nets = res.unconnected_nets()
                line = f"  unconnected: {len(items)} item(s) on {len(nets)} net(s)"
                if expected_links is not None:
                    line += f" (the link proposal needs {expected_links} link(s) in all)"
                out.append(line)
                out += [f"    {n}: {k}" for n, k in nets.items()]
            continue
        for v in items:
            out.append(f"  {name.upper()}: {v.get('type')}: {v.get('description')} [{_where(v, label)}]")
    for v in res.other_errors:
        out.append(f"  error: {v.get('type')}: {v.get('description')} [{_where(v, label)}]")
    warn: dict[str, int] = {}
    for v in res.other:
        if v.get("severity") != "error":
            warn[v.get("type", "?")] = warn.get(v.get("type", "?"), 0) + 1
    if warn:
        out.append("  other warnings: " + ", ".join(f"{k} x{n}" for k, n in sorted(warn.items())))
    out.append("Result: " + ("clean" if res.ok else f"{res.real_count} real problem(s)"))
    return "\n".join(out) + "\n"
