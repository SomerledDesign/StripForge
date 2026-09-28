# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""`stripforge link-symbols`: write the board's W links into the schematic as StripForge:Link symbols.

Links flow from the board to the schematic: the build places the ``StripForge:Link_P*`` /
``Link_D*`` footprints (``place_links = true``, or pass 2), and this step adds one
``StripForge:Link`` symbol per W footprint the schematic doesn't have yet. KiCad's own Update
Schematic from PCB can't do it: it only updates symbols that already exist (for a footprint with no
symbol it reports "Cannot find symbol for footprint").

For each W footprint on the board:

* the symbol gets the footprint's reference, Footprint (``StripForge:Link_P10.16``), Value and
  Description, and the uuid at the end of the footprint's schematic path (the build writes one),
  so pressing F8 afterwards matches the two up instead of adding a second copy of the link;
* it goes on the sheet of the link's net: a net named ``/<sheet path>/<name>`` gets a local label
  ``<name>`` on each pin on that sheet; any other net (``GND``, ``+5V``, a global label) gets a
  global label with the net's name, placed on the sheet the footprint's path names (else the sheet
  with the most parts). An unnamed net (``Net-(R1-Pad2)``) also gets a global label of that name
  on one of its existing pins, so the net keeps its name and the link joins it;
* the symbols are laid out in a grid to the right of everything on the sheet, under a note, and the
  paper is enlarged when the grid doesn't fit.

The schematic is written to a copy (``--out-dir``: every sheet of the hierarchy, the project file
and the library tables with ``${KIPRJMOD}`` pointing back at the original project, plus the board
under the project's name, so ``kicad-cli`` netlist/ERC/DRC parity run on the copy), or in place
(``--in-place``), in which case every sheet that changes is first backed up next to it as
``<sheet>-pre-links.kicad_sch``, rotating older backups to ``-1``, ``-2``, ... like the board's
``-pre-stripbuild`` backups (all renames happen before any sheet is written). The
``StripForge:Link`` symbol definition is embedded in each sheet (``lib_symbols``) as KiCad does, so
the schematic opens without the library configured and needs no sym-lib-table entry."""

from __future__ import annotations

import re
import shutil
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import resources
from .board import load_board
from .sexpr import Document, Sym, atom, find, find_all, head, loads

NS = uuid.UUID("7b1e6c1a-51f0-4e43-9b3a-5354524950f1")  # the writer's namespace (uuid5)
LINK_LIB_ID = f"{resources.LIB_NICKNAME}:Link"
LINK_RE = re.compile(r"^W\d+$")
GRID = 1.27  # schematic connection grid (mm)
LINKS_BACKUP_SUFFIX = "-pre-links"  # in place: <sheet>-pre-links.kicad_sch, rotated like the board backups
# landscape paper sizes (mm) KiCad knows, smallest first
PAPER = {
    "A4": (297, 210), "USLetter": (279.4, 215.9), "A": (279.4, 215.9), "USLegal": (355.6, 215.9),
    "A3": (420, 297), "USLedger": (431.8, 279.4), "B": (431.8, 279.4), "A2": (594, 420),
    "C": (558.8, 431.8), "A1": (841, 594), "D": (863.6, 558.8), "A0": (1189, 841), "E": (1117.6, 863.6),
}  # fmt: skip
GROW = ["A3", "USLedger", "A2", "C", "A1", "D", "A0", "E"]


class LinkSymbolError(ValueError):
    """The schematic can't be updated (bad input, refused output)."""


def _u(key: str) -> str:
    return str(uuid.uuid5(NS, key))


def _n(v: float) -> Sym:
    return Sym(f"{round(v, 4):g}")


def _snap(v: float) -> float:
    return round(round(v / GRID) * GRID, 4)


# --- reading -----------------------------------------------------------------------------------


@dataclass
class BoardLink:
    ref: str
    footprint: str  # StripForge:Link_P10.16
    net: str
    path: str | None  # the footprint's schematic path (/<sheet uuids>/<symbol uuid>)
    value: str
    description: str
    in_bom: bool
    in_pos: bool


@dataclass
class Sheet:
    file: Path
    doc: Document
    uuid_path: str  # "" for the root, "/<sheet uuid>/..." below it (as in a footprint's path)
    inst_path: str  # "/<root uuid>" + uuid_path (as in a symbol's instances)
    name_path: str  # "/" or "/SCHEMATIC DIAGRAM/" (the prefix of net names on that sheet)
    changed: bool = False


@dataclass
class AddedLink:
    ref: str
    sheet: str  # sheet file name
    footprint: str
    net: str
    label: str  # "local", "global"
    anchor: str = ""  # for an unnamed net: the pin the extra global label sits on


@dataclass
class LinkSymbolResult:
    added: list[AddedLink] = field(default_factory=list)
    already: list[str] = field(default_factory=list)  # W refs already in the schematic
    # W symbols that were already there (e.g. placed by hand) and were completed: ref -> what was done
    completed: dict[str, list[str]] = field(default_factory=dict)
    # W symbols whose pins are already wired to another net: left unchanged
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    backups: list[str] = field(default_factory=list)


def board_links(board_path: str | Path) -> tuple[list[BoardLink], list, list[str]]:
    """The W link footprints on a board (and all footprints, for anchors), with warnings."""
    board = load_board(board_path)
    out, warns = [], []
    for fp in board.footprints:
        if not (LINK_RE.match(fp.ref) and fp.lib_id.startswith(f"{resources.LIB_NICKNAME}:Link_")):
            continue
        nets = {p.net for p in fp.pads}
        if len(nets) != 1 or None in nets:
            got = ", ".join(sorted(map(str, nets)))
            warns.append(f"{fp.ref}: its pads are not on one net ({got}); skipped")
            continue
        node = fp.node or []
        props = {atom(p, 1): atom(p, 2, "") for p in find_all(node, "property")}
        attr = find(node, "attr") or []
        out.append(
            BoardLink(
                fp.ref,
                fp.lib_id,
                nets.pop(),
                atom(find(node, "path"), 1),
                props.get("Value", "Link") or "Link",
                props.get("Description", ""),
                "exclude_from_bom" not in attr,
                "exclude_from_pos_files" not in attr,
            )
        )
    out.sort(key=lambda b: (len(b.ref), b.ref))
    return out, board.footprints, warns


def load_hierarchy(root_file: str | Path) -> list[Sheet]:
    """Every sheet of a schematic, root first. A sheet file used more than once is refused."""
    root_file = Path(root_file)
    doc = Document.load(root_file)
    root_uuid = atom(find(doc.root, "uuid"), 1)
    if head(doc.root) != "kicad_sch" or not root_uuid:
        raise LinkSymbolError(f"{root_file} is not a KiCad schematic")
    sheets = [Sheet(root_file, doc, "", f"/{root_uuid}", "/")]
    seen = {root_file.resolve()}

    def walk(parent: Sheet) -> None:
        for node in find_all(parent.doc.root, "sheet"):
            props = {atom(p, 1): atom(p, 2, "") for p in find_all(node, "property")}
            name, sub = props.get("Sheetname", ""), props.get("Sheetfile", "")
            sid = atom(find(node, "uuid"), 1)
            if not sub or not sid:
                continue
            path = root_file.parent / sub
            if path.resolve() in seen:
                raise LinkSymbolError(f"sheet file {sub} is used more than once; not supported")
            seen.add(path.resolve())
            child = Sheet(
                path, Document.load(path), f"{parent.uuid_path}/{sid}",
                f"{parent.inst_path}/{sid}", f"{parent.name_path}{name}/",
            )  # fmt: skip
            sheets.append(child)
            walk(child)

    walk(sheets[0])
    return sheets


def _placed_symbols(sheet: Sheet):
    """(symbol node, reference) for every placed symbol on a sheet."""
    for node in find_all(sheet.doc.root, "symbol"):
        if find(node, "lib_id") is None:
            continue
        ref = None
        for proj in find_all(find(node, "instances") or [], "project"):
            for p in find_all(proj, "path"):
                if atom(p, 1) == sheet.inst_path:
                    ref = atom(find(p, "reference"), 1)
        if ref is None:
            ref = next((atom(p, 2) for p in find_all(node, "property") if atom(p, 1) == "Reference"), None)
        yield node, ref


def pin_points(sheet: Sheet, sym: list) -> dict[str, tuple[float, float]]:
    """Schematic coordinates (mm) of each pin's connection point of a placed symbol."""
    lib_id = atom(find(sym, "lib_id"), 1)
    libs = {atom(s, 1): s for s in find_all(find(sheet.doc.root, "lib_symbols") or [], "symbol")}
    lib = libs.get(lib_id)
    if lib is None:
        return {}
    at = find(sym, "at")
    x0, y0 = float(at[1]), float(at[2])
    angle = int(float(at[3])) if len(at) > 3 else 0
    mirror = atom(find(sym, "mirror"), 1)
    unit = int(atom(find(sym, "unit"), 1, "1"))
    style = int(atom(find(sym, "body_style"), 1, "1") or 1)
    out = {}
    for sub in find_all(lib, "symbol"):
        try:
            _, u, b = (atom(sub, 1) or "").rsplit("_", 2)
            u, b = int(u), int(b)
        except ValueError:
            continue
        if u not in (0, unit) or b not in (0, style):
            continue
        for pin in find_all(sub, "pin"):
            pat = find(pin, "at")
            x, y = float(pat[1]), -float(pat[2])  # library y is up, schematic y is down
            for _ in range((angle // 90) % 4):
                x, y = y, -x
            if mirror == "x":
                y = -y
            elif mirror == "y":
                x = -x
            out[atom(find(pin, "number"), 1) or ""] = (round(x0 + x, 4), round(y0 + y, 4))
    return out


def _extent(sheet: Sheet) -> tuple[float, float, float, float]:
    """Bounding box (mm) of every coordinate on a sheet (title block and libraries left out)."""
    xs, ys = [], []

    def walk(node, top):
        for c in node:
            if not isinstance(c, list):
                continue
            h = head(c)
            if top and h in ("lib_symbols", "title_block", "sheet_instances", "paper"):
                continue
            if top and h in ("text_box", "table", "text", "sheet"):
                _box(c)
            if h in ("at", "xy", "start", "end", "center", "mid") and len(c) >= 3:
                try:
                    xs.append(float(c[1]))
                    ys.append(float(c[2]))
                except ValueError:
                    pass
            walk(c, False)

    def _box(c: list) -> None:
        """The far corner of a text box / sheet (at + size) or a text's estimated extent."""
        at, size = find(c, "at"), find(c, "size")
        if at is None:
            return
        x, y = float(at[1]), float(at[2])
        if size is not None and len(size) >= 3:
            xs.append(x + float(size[1]))
            ys.append(y + float(size[2]))
        elif head(c) == "text":
            font = find(find(c, "effects") or [], "font") or []
            fsize = find(font, "size")
            em = float(fsize[1]) if fsize is not None else 1.27
            lines = str(atom(c, 1, "")).split("\n")
            width = max(len(s) for s in lines) * em * 0.9
            just = find(find(c, "effects") or [], "justify") or []
            if "right" in just:
                xs.append(x - width)
            elif "left" in just:
                xs.append(x + width)
            else:
                xs.extend((x - width / 2, x + width / 2))
            ys.append(y + len(lines) * em * 1.6)

    walk(sheet.doc.root, True)
    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _paper(sheet: Sheet) -> tuple[str, float, float, bool]:
    p = find(sheet.doc.root, "paper")
    if p is None:
        return "A4", 297.0, 210.0, False
    name = atom(p, 1, "A4")
    if name == "User":
        return name, float(p[2]), float(p[3]), False
    w, h = PAPER.get(name, (297.0, 210.0))
    portrait = "portrait" in p
    return name, (h if portrait else w), (w if portrait else h), portrait


# --- writing -----------------------------------------------------------------------------------


def _effects(justify: str | None = None, hide: bool = False) -> list:
    e = [Sym("effects"), [Sym("font"), [Sym("size"), Sym("1.27"), Sym("1.27")]]]
    if justify:
        e.append([Sym("justify"), *[Sym(j) for j in justify.split()]])
    if hide:
        e.append([Sym("hide"), Sym("yes")])
    return e


def _prop(name: str, value: str, x: float, y: float, hide: bool = False) -> list:
    return [Sym("property"), name, value, [Sym("at"), _n(x), _n(y), Sym("0")], _effects(hide=hide)]


def _label(kind: str, name: str, x: float, y: float, left: bool, key: str) -> list:
    """A local or global label whose connection point is (x, y), text running away from it."""
    angle, just = ("180", "right") if left else ("0", "left")
    node = [Sym(kind), name]
    if kind == "global_label":
        node.append([Sym("shape"), Sym("passive")])
    node += [[Sym("at"), _n(x), _n(y), Sym(angle)], _effects(just), [Sym("uuid"), _u(key)]]
    if kind == "global_label":
        node.append(
            [Sym("property"), "Intersheetrefs", "${INTERSHEET_REFS}",
             [Sym("at"), _n(x), _n(y), Sym("0")], _effects(hide=True)]
        )  # fmt: skip
    return node


def _symbol(link: BoardLink, sym_uuid: str, x: float, y: float, project: str, inst_path: str) -> list:
    return [
        Sym("symbol"),
        [Sym("lib_id"), LINK_LIB_ID],
        [Sym("at"), _n(x), _n(y), Sym("0")],
        [Sym("unit"), Sym("1")],
        [Sym("body_style"), Sym("1")],
        [Sym("exclude_from_sim"), Sym("yes")],
        [Sym("in_bom"), Sym("yes" if link.in_bom else "no")],
        [Sym("on_board"), Sym("yes")],
        [Sym("in_pos_files"), Sym("yes" if link.in_pos else "no")],
        [Sym("dnp"), Sym("no")],
        [Sym("uuid"), sym_uuid],
        _prop("Reference", link.ref, x, y - 3.81),
        _prop("Value", link.value, x, y + 2.54),
        _prop("Footprint", link.footprint, x, y, hide=True),
        _prop("Datasheet", "", x, y, hide=True),
        _prop("Description", link.description, x, y, hide=True),
        [Sym("pin"), "1", [Sym("uuid"), _u(f"{sym_uuid}/pin1")]],
        [Sym("pin"), "2", [Sym("uuid"), _u(f"{sym_uuid}/pin2")]],
        [
            Sym("instances"),
            [
                Sym("project"),
                project,
                [Sym("path"), inst_path, [Sym("reference"), link.ref], [Sym("unit"), Sym("1")]],
            ],
        ],
    ]


def _lib_definition(symbol_lib: Path) -> list:
    lib = loads(symbol_lib.read_text(encoding="utf-8"))
    for node in find_all(lib, "symbol"):
        if atom(node, 1) == "Link":
            node = [c for c in node]
            node[1] = LINK_LIB_ID
            return node
    raise LinkSymbolError(f"{symbol_lib} has no 'Link' symbol")


def _insert(root: list, nodes: list[list]) -> None:
    at = next((i for i, c in enumerate(root) if head(c) in ("sheet_instances", "embedded_fonts")), len(root))
    root[at:at] = nodes


def _layout(ph: float, n: int, x0: float, y0: float, dx: float, dy: float, longest: float):
    """(columns, width, height needed) for ``n`` symbols in rows ``dy`` apart on paper ``ph`` tall."""
    rows_max = max(1, int((ph - y0 - 50.8) // dy) + 1)
    cols = max(1, -(-n // rows_max))
    rows = -(-n // cols)
    return cols, x0 + (cols - 1) * dx + 5.08 + longest + 15.24, y0 + (rows - 1) * dy + 50.8


def _text_width(s: str) -> float:
    return len(s) * 1.27 * 0.8 + 3.0


def _project_name(sheets: list[Sheet]) -> str:
    for sh in sheets:
        for node in find_all(sh.doc.root, "symbol"):
            proj = find(find(node, "instances") or [], "project")
            if proj is not None and atom(proj, 1):
                return atom(proj, 1)
    return sheets[0].file.stem


def _near(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < 0.02 and abs(a[1] - b[1]) < 0.02


def _connection_points(sheet: Sheet, skip: list) -> list[tuple[float, float]]:
    """Points on a sheet where something connects: label anchors, wire ends, junctions, no-connect
    flags and the pins of every symbol but ``skip``."""
    pts = []
    root = sheet.doc.root
    for kind in ("label", "global_label", "hierarchical_label", "junction", "no_connect"):
        for n in find_all(root, kind):
            at = find(n, "at")
            if at is not None:
                pts.append((float(at[1]), float(at[2])))
    for w in find_all(root, "wire"):
        for xy in find_all(find(w, "pts") or [], "xy"):
            pts.append((float(xy[1]), float(xy[2])))
    for node, _ref in _placed_symbols(sheet):
        if node is not skip:
            pts += list(pin_points(sheet, node).values())
    return pts


def _pin_nets(sheet: Sheet, xy: tuple[float, float]) -> set[str]:
    """Names of the labels a pin at ``xy`` reaches: at the point itself or along wires from it."""
    root = sheet.doc.root
    wires = []
    for w in find_all(root, "wire"):
        ends = [(float(p[1]), float(p[2])) for p in find_all(find(w, "pts") or [], "xy")]
        if len(ends) == 2:
            wires.append(ends)
    seen, todo = [xy], [xy]
    while todo:
        pt = todo.pop()
        for a, b in wires:
            for p, q in ((a, b), (b, a)):
                if _near(p, pt) and not any(_near(q, s) for s in seen):
                    seen.append(q)
                    todo.append(q)
    names = set()
    for kind in ("label", "global_label", "hierarchical_label"):
        for n in find_all(root, kind):
            at = find(n, "at")
            if at is not None and any(_near((float(at[1]), float(at[2])), s) for s in seen):
                names.add(atom(n, 1))
    return names


def _label_on(net: str, sheet: Sheet) -> tuple[str | None, str]:
    """The label that puts a pin on ``sheet`` onto ``net``: a local label for a net of that sheet,
    a global label for a global net, or (None, "") for another sheet's local net."""
    if net.startswith("/"):
        if net.startswith(sheet.name_path) and "/" not in net[len(sheet.name_path) :]:
            return "label", net[len(sheet.name_path) :]
        return None, ""
    return "global_label", net


def _relink(lk: BoardLink, sheet: Sheet, node: list, res: LinkSymbolResult) -> None:
    """An existing W symbol (e.g. placed by hand): give it the uuid its placed footprint's path
    names, so F8 matches the two by path instead of adding a second footprint, and the board's
    Footprint when its own is empty or different."""
    prefix, _, fp_uuid = (lk.path or "").rpartition("/")
    uid = find(node, "uuid")
    done = res.completed.setdefault(lk.ref, [])
    if fp_uuid and uid is not None and atom(uid, 1) != fp_uuid:
        if prefix == sheet.uuid_path:
            uid[1] = fp_uuid
            done.append("re-linked to its placed footprint (uuid = the footprint's schematic path)")
        else:
            res.warnings.append(
                f"{lk.ref}: its symbol is on {sheet.file.name} but its board footprint's path names another "
                "sheet; F8 would add a second footprint: tick 'Re-link footprints to schematic symbols based "
                "on their reference designators' in F8, or move the symbol"
            )
    fp = next((p for p in find_all(node, "property") if atom(p, 1) == "Footprint"), None)
    if fp is not None and atom(fp, 2, "") != lk.footprint:
        old = atom(fp, 2, "")
        fp[2] = lk.footprint
        done.append(f"Footprint {old or '(empty)'} -> {lk.footprint}")
    if not done:
        del res.completed[lk.ref]


def add_link_symbols(
    schematic: str | Path,
    board_path: str | Path,
    out_dir: str | Path | None = None,
    in_place: bool = False,
    symbol_lib: str | Path | None = None,
    backup_keep: int = 0,
) -> LinkSymbolResult:
    """Add a StripForge:Link symbol for every W footprint on ``board_path`` that the schematic
    (root sheet ``schematic``) lacks. See the module docstring. Raises LinkSymbolError."""
    schematic = Path(schematic)
    if (out_dir is None) == (not in_place):
        raise LinkSymbolError("give either an output directory or in_place=True")
    if out_dir is not None and Path(out_dir).resolve() == schematic.parent.resolve():
        raise LinkSymbolError("the output directory must not be the project directory (use --in-place)")
    lib_file = resources.symbol_lib(symbol_lib)
    definition = _lib_definition(lib_file)
    sheets = load_hierarchy(schematic)
    links, footprints, warns = board_links(board_path)
    res = LinkSymbolResult(warnings=warns)
    project = _project_name(sheets)

    existing: dict[str, Sheet] = {}
    existing_node: dict[str, list] = {}
    for sh in sheets:
        for node, ref in _placed_symbols(sh):
            if ref:
                existing[ref] = sh
                existing_node[ref] = node
    by_uuid_path = {sh.uuid_path: sh for sh in sheets}
    by_name = sorted(sheets, key=lambda s: -len(s.name_path))
    fp_sheet: Counter = Counter()
    sheet_of: dict[str, str] = {}  # part reference -> its sheet's uuid path
    for fp in footprints:
        path = atom(find(fp.node or [], "path"), 1)
        if path and not LINK_RE.match(fp.ref):
            sheet_of[fp.ref] = path.rsplit("/", 1)[0]
            fp_sheet[sheet_of[fp.ref]] += 1
    busiest = by_uuid_path.get(max(fp_sheet, key=fp_sheet.get)) if fp_sheet else None
    busiest = busiest or max(sheets, key=lambda s: sum(1 for _ in _placed_symbols(s)))

    # which sheet, which label, which uuid
    plans: dict[str, list] = {}
    to_complete: list = []  # (link, sheet, symbol node, {pin: (x, y)} still unconnected)
    for lk in links:
        if lk.ref in existing:
            res.already.append(lk.ref)
            sh, node = existing[lk.ref], existing_node[lk.ref]
            _relink(lk, sh, node, res)
            pts = pin_points(sh, node)
            if not pts:
                res.warnings.append(f"{lk.ref}: its symbol has no pins StripForge can find; label it by hand")
                continue
            kind, name = _label_on(lk.net, sh)
            clash = {}
            for n, xy in sorted(pts.items()):
                other = sorted(x for x in _pin_nets(sh, xy) if x != name)
                if other and kind is not None:
                    clash[n] = other
            if clash:
                res.conflicts.append(
                    f"{lk.ref}: "
                    + "; ".join(f"pin {n} is wired to {', '.join(o)}" for n, o in clash.items())
                    + f", but its board footprint is on {lk.net}: left unchanged; fix the schematic or the "
                    "board"
                )
                continue
            busy = _connection_points(sh, node)
            free = {n: xy for n, xy in pts.items() if not any(_near(xy, b) for b in busy)}
            if free:
                to_complete.append((lk, sh, node, free))
            continue
        target = label_name = None
        kind = "global_label"
        if lk.net.startswith("/"):
            for sh in by_name:
                if lk.net.startswith(sh.name_path) and "/" not in lk.net[len(sh.name_path) :]:
                    target, label_name, kind = sh, lk.net[len(sh.name_path) :], "label"
                    break
        prefix, _, sym_uuid = (lk.path or "").rpartition("/")
        if target is None:
            on_net = Counter(
                sheet_of[fp.ref]
                for fp in footprints
                if fp.ref in sheet_of and any(p.net == lk.net for p in fp.pads)
            )
            by_net = by_uuid_path.get(max(on_net, key=on_net.get)) if on_net else None
            target = (by_uuid_path.get(prefix) if lk.path else None) or by_net or busiest
            label_name = lk.net
        if not sym_uuid or prefix != target.uuid_path:
            if lk.path:
                res.warnings.append(
                    f"{lk.ref}: its board path doesn't name the sheet its net is on; rebuild the board "
                    "(stripforge build) so F8 matches the new symbol, or tick 'Re-link footprints to "
                    "schematic symbols based on their reference designators' in F8"
                )
            sym_uuid = _u(f"link-symbol/{lk.ref}")
        plans.setdefault(str(target.file), []).append((lk, target, kind, label_name, sym_uuid))

    # unnamed nets: a global label of the same name on one of the net's existing pins
    anchors: dict[str, tuple[Sheet, float, float, str]] = {}
    unnamed = {lk.net for group in plans.values() for (lk, *_rest) in group if lk.net.startswith("Net-(")}
    unnamed |= {lk.net for (lk, *_rest) in to_complete if lk.net.startswith("Net-(")}
    for net in sorted(unnamed):
        parts = [fp for fp in footprints if not LINK_RE.match(fp.ref)]
        pads = [(fp.ref, p.number) for fp in parts for p in fp.pads if p.net == net]
        found = None
        for sh in sheets:
            for node, ref in _placed_symbols(sh):
                pts = pin_points(sh, node)
                for r, num in pads:
                    if r == ref and num in pts:
                        found = (sh, *pts[num], f"{r}.{num}")
                        break
                if found:
                    break
            if found:
                break
        if found is None:
            res.warnings.append(f"net {net}: no schematic pin found to name it; its links get labels only")
        else:
            anchors[net] = found

    for group in plans.values():
        sh = group[0][1]
        root = sh.doc.root
        libs = find(root, "lib_symbols")
        if libs is None:
            libs = [Sym("lib_symbols")]
            _insert(root, [libs])
        if not any(atom(s, 1) == LINK_LIB_ID for s in find_all(libs, "symbol")):
            libs.append(definition)
        x_min, y_min, x_max, y_max = _extent(sh)
        longest = max(_text_width(g[3]) for g in group)
        dx = _snap(max(33.02, 10.16 + 2 * longest + 7.62))
        dy = 12.7
        x0 = _snap(_snap(x_max + 12.7) + longest + 5.08)
        y0 = _snap(max(y_min, 25.4) + 12.7)
        n = len(group)
        name, w, h, _ = _paper(sh)

        geom = (n, x0, y0, dx, dy, longest)
        cols, need_w, need_h = _layout(h, *geom)
        if need_w > w or need_h > h:
            pick = None
            for cand in GROW:
                pw, ph = PAPER[cand]
                if pw * ph <= w * h:
                    continue
                c, nw, nh = _layout(ph, *geom)
                if nw <= pw and nh <= ph:
                    pick, cols = cand, c
                    break
            paper = find(root, "paper")
            new = [Sym("paper"), pick] if pick else [Sym("paper"), "User", _n(need_w), _n(need_h)]
            if paper is None:
                _insert(root, [new])
            else:
                paper[:] = new
        nodes: list[list] = [
            [
                Sym("text"),
                "StripForge wire links (W): written from the board by 'stripforge link-symbols'.\n"
                "Each is a StripForge:Link with the board's Link_P*/Link_D* footprint; do not move them to "
                "another net here, re-plan with 'stripforge build'.",
                [Sym("exclude_from_sim"), Sym("no")],
                [Sym("at"), _n(x0 - longest - 5.08), _n(y0 - 12.7), Sym("0")],
                _effects("left bottom"),
                [Sym("uuid"), _u(f"link-symbols/note/{sh.uuid_path}")],
            ]
        ]
        for i, (lk, _sh, kind, label_name, sym_uuid) in enumerate(group):
            x = x0 + (i % cols) * dx
            y = y0 + (i // cols) * dy  # row by row, cols across
            nodes.append(_symbol(lk, sym_uuid, x, y, project, sh.inst_path))
            nodes.append(_label(kind, label_name, x - 5.08, y, True, f"{sym_uuid}/label1"))
            nodes.append(_label(kind, label_name, x + 5.08, y, False, f"{sym_uuid}/label2"))
            anchor = anchors.get(lk.net)
            how = "local" if kind == "label" else "global"
            res.added.append(
                AddedLink(lk.ref, sh.file.name, lk.footprint, lk.net, how, anchor[3] if anchor else "")
            )
        _insert(root, nodes)
        sh.changed = True
    for lk, sh, node, free in to_complete:  # W symbols already there: label their bare pins
        kind, name = _label_on(lk.net, sh)
        if kind is None:
            res.warnings.append(
                f"{lk.ref}: its net {lk.net} belongs to another sheet than {sh.file.name}; "
                "label its pins by hand"
            )
            continue
        cx, cy = float(find(node, "at")[1]), float(find(node, "at")[2])
        uid = atom(find(node, "uuid"), 1) or _u(f"link-symbol/{lk.ref}")
        new = [
            _label(kind, name, x, y, x < cx or (x == cx and y < cy), f"{uid}/label{n}")
            for n, (x, y) in free.items()
        ]
        _insert(sh.doc.root, new)
        sh.changed = True
        res.completed.setdefault(lk.ref, []).append(
            f"{'local' if kind == 'label' else 'global'} label {name} on pin(s) {', '.join(sorted(free))}"
        )
    for net, (sh, x, y, _pin) in anchors.items():
        _insert(sh.doc.root, [_label("global_label", net, x, y, False, f"link-symbols/anchor/{net}")])
        sh.changed = True
    for ref in res.completed:
        existing[ref].changed = True

    if not any(sh.changed for sh in sheets):
        return res
    if in_place:
        from .writer import BuildError, rotate_backups

        changed = [sh for sh in sheets if sh.changed]
        for sh in changed:  # every backup first, then the writes
            try:
                rot = rotate_backups(sh.file, backup_keep, LINKS_BACKUP_SUFFIX)
            except BuildError as exc:
                raise LinkSymbolError(str(exc).replace("nothing built", "no sheet written")) from exc
            res.backups.append(str(rot.backup))
            res.warnings += rot.warnings
        for sh in changed:
            sh.doc.save(sh.file)
            res.outputs.append(str(sh.file))
        return res
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    src = schematic.parent
    for sh in sheets:
        dest = out / sh.file.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if sh.changed:
            sh.doc.save(dest)
        else:
            shutil.copyfile(sh.file, dest)
        res.outputs.append(str(dest))
    for suffix in (".kicad_pro", ".kicad_prl"):
        p = schematic.with_suffix(suffix)
        if p.exists():
            shutil.copyfile(p, out / p.name)
            res.outputs.append(str(out / p.name))
    board_path = Path(board_path)
    shutil.copyfile(board_path, out / (schematic.stem + ".kicad_pcb"))
    res.outputs.append(str(out / (schematic.stem + ".kicad_pcb")))
    dru = board_path.with_suffix(".kicad_dru")
    if dru.exists():
        shutil.copyfile(dru, out / (schematic.stem + ".kicad_dru"))
    absdir = str(src.resolve())
    for table in ("sym-lib-table", "fp-lib-table"):
        p = src / table
        empty = f"({table.replace('-', '_')}\n  (version 7)\n)\n"
        text = p.read_text(encoding="utf-8") if p.exists() else empty
        text = text.replace("${KIPRJMOD}", absdir)
        nick = f'(name "{resources.LIB_NICKNAME}")'
        if nick not in text:
            lib = lib_file if table == "sym-lib-table" else resources.library_dir()
            entry = (
                f'  (lib (name "{resources.LIB_NICKNAME}") (type "KiCad") (uri "{Path(lib).resolve()}") '
                '(options "") (descr "StripForge (added by stripforge link-symbols)"))\n'
            )
            text = text.rstrip().rstrip(")").rstrip() + "\n" + entry + ")\n"
        (out / table).write_text(text, encoding="utf-8")
        res.outputs.append(str(out / table))
    return res


def format_text(res: LinkSymbolResult) -> str:
    out = []
    if res.added:
        out.append(f"added {len(res.added)} StripForge:Link symbol(s):")
        for a in res.added:
            how = "local labels" if a.label == "local" else "global labels"
            extra = f" (net named at {a.anchor})" if a.anchor else ""
            out.append(f"  {a.ref:<4} {a.footprint:<24} {a.net}  [{a.sheet}, {how}{extra}]")
    else:
        out.append("no symbols added")
    if res.already:
        out.append(f"already in the schematic: {', '.join(res.already)}")
    for ref, what in res.completed.items():
        out.append(f"  {ref}: " + "; ".join(what))
    out += [f"CONFLICT: {c}" for c in res.conflicts]
    out += [f"warning: {w}" for w in res.warnings]
    out += [f"backup: {b}" for b in res.backups]
    return "\n".join(out)
