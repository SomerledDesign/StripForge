# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Minimal S-expression reader/writer for KiCad files (.kicad_pcb, .kicad_mod, .net).

A parsed node is a Python ``list`` (an :class:`SList`). Atoms are ``str``: bare tokens come back
as :class:`Sym` (a ``str`` subclass) and quoted strings as plain ``str``, so ``dumps`` can re-quote
exactly the atoms that were quoted. Numbers stay as their original text; convert them where they
are used (see :func:`mm_to_nm`).

Two writers:

* :func:`dumps` re-renders a tree in KiCad's tab-indented style. It keeps every node and the
  quoting but not the original whitespace.
* :class:`Document` (from :func:`parse`) keeps the source text and each node's span, and
  :meth:`Document.dumps` is **byte-exact** for anything left untouched: an unmodified
  parse-then-write returns the input unchanged. Edited nodes are re-emitted in place: unchanged
  children and the whitespace between them come from the source, replaced atoms are written
  fresh, and new child lists are rendered in KiCad's style (Sketch.md §4.7).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal

__all__ = [
    "Sym",
    "SList",
    "SExprError",
    "Document",
    "parse",
    "loads",
    "dumps",
    "head",
    "find",
    "find_all",
    "atom",
    "mm_to_nm",
    "nm_to_mm_text",
]


class Sym(str):
    """A bare (unquoted) atom such as ``footprint``, ``thru_hole`` or ``2.54``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"Sym({str.__repr__(self)})"


class SExprError(ValueError):
    pass


_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r"}


def _unescape(s: str) -> str:
    if "\\" not in s:
        return s
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m[1], m[1]), s, flags=re.S)


class SList(list):
    """A parsed list node. Remembers where it came from so :class:`Document` can write it back."""

    __slots__ = ("start", "end", "spans", "orig", "src")

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.start = -1  # offset of "(" in the source, -1 for a node built in code
        self.end = -1  # offset just past ")"
        self.spans: list[tuple[int, int]] = []  # source span of each original child
        self.orig: tuple = ()  # the original children, to detect edits
        self.src = ""  # the source text the spans refer to


def _parse(text: str) -> SList:
    stack: list[SList] = []
    result: SList | None = None
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise SExprError(f"unexpected character {text[pos]!r} at offset {pos}")
        pos = m.end()
        opn, cls, qstr, bare = m.groups()
        if opn:
            if result is not None and not stack:
                raise SExprError(f"more than one top-level expression (offset {m.start(1)})")
            node = SList()
            node.start = m.start(1)
            node.src = text
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif cls:
            if not stack:
                raise SExprError(f"unbalanced ')' at offset {m.start(2)}")
            done = stack.pop()
            done.end = m.end(2)
            done.orig = tuple(done)
            if stack:
                stack[-1].spans.append((done.start, done.end))
            else:
                result = done
        elif qstr is not None:
            if not stack:
                raise SExprError("atom outside of any list")
            stack[-1].append(_unescape(qstr))
            stack[-1].spans.append((m.start(3) - 1, m.end()))
        elif bare is not None:
            if not stack:
                raise SExprError("atom outside of any list")
            stack[-1].append(Sym(bare))
            stack[-1].spans.append((m.start(4), m.end(4)))
    if stack:
        raise SExprError(f"unexpected end of input: {len(stack)} unclosed '('")
    if result is None:
        raise SExprError("no S-expression found")
    return result


def loads(text: str) -> list:
    """Parse one top-level S-expression and return it as nested lists."""
    return _parse(text)


def parse(text: str) -> Document:
    """Parse text into a :class:`Document` that can be written back byte-exactly."""
    return Document(text, _parse(text))


def _atom_text(a: str) -> str:
    if isinstance(a, Sym):
        return str(a)
    return '"' + a.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def dumps(tree: list, indent: str = "\t") -> str:
    """Serialise nested lists back to text (KiCad-like layout, one child list per line)."""
    out: list[str] = []

    def emit(node: list, depth: int) -> None:
        pad = indent * depth
        atoms = [c for c in node if not isinstance(c, list)]
        if len(atoms) == len(node):
            out.append(pad + "(" + " ".join(_atom_text(a) for a in node) + ")")
            return
        # leading atoms on the opening line, then child lists (and any later atoms) indented
        i = 0
        first: list[str] = []
        while i < len(node) and not isinstance(node[i], list):
            first.append(_atom_text(node[i]))
            i += 1
        out.append(pad + "(" + " ".join(first))
        for child in node[i:]:
            if isinstance(child, list):
                emit(child, depth + 1)
            else:
                out.append(indent * (depth + 1) + _atom_text(child))
        out.append(pad + ")")

    emit(tree, 0)
    return "\n".join(out) + "\n"


def _render(node: list, depth: int, indent: str = "\t") -> str:
    """KiCad-style text for a node built in code (no source span), starting at ``depth``."""
    if all(not isinstance(c, list) for c in node):
        return "(" + " ".join(_atom_text(a) for a in node) + ")"
    parts = ["("]
    first = True
    for child in node:
        if isinstance(child, list):
            parts.append("\n" + indent * (depth + 1) + _render(child, depth + 1, indent))
        else:
            parts.append(("" if first else " ") + _atom_text(child))
        first = False
    parts.append("\n" + indent * depth + ")")
    return "".join(parts)


class Document:
    """A parsed file that keeps its source text, for byte-exact writing (Sketch.md §4.7).

    Edit ``root`` in place (replace atoms, insert or remove child lists), then call
    :meth:`dumps`. Untouched nodes are copied from the source verbatim.
    """

    def __init__(self, text: str, root: SList, indent: str = "\t") -> None:
        self.text = text
        self.root = root
        self.indent = indent

    @classmethod
    def load(cls, path) -> Document:
        with open(path, encoding="utf-8", newline="") as fh:
            return parse(fh.read())

    def save(self, path) -> None:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(self.dumps())

    def dumps(self) -> str:
        r = self.root
        if r.start < 0:
            return _render(r, 0, self.indent) + "\n"
        return self.text[: r.start] + self._emit(r, 0) + self.text[r.end :]

    def _emit(self, node: list, depth: int) -> str:
        if not isinstance(node, SList) or node.start < 0:
            return _render(node, depth, self.indent)
        # a node parsed from another text (moved between documents) carries its own source
        text, orig, spans = node.src, node.orig, node.spans
        ids = {id(c): i for i, c in enumerate(orig) if isinstance(c, list)}
        # For each current child: its index among the original children (None if new).
        pos: list[int | None] = []
        for k, child in enumerate(node):
            if isinstance(child, list):
                pos.append(ids.get(id(child)))
            elif k < len(orig) and not isinstance(orig[k], list):
                pos.append(k)  # an atom at an original atom position, edited or not
            else:
                pos.append(None)
        tail_src = text[spans[-1][1] : node.end] if orig else ")"
        multiline = "\n" in tail_src  # KiCad closes a multi-line node on its own line
        out = ["(" + (text[node.start + 1 : spans[0][0]] if orig and pos and pos[0] == 0 else "")]
        for k, child in enumerate(node):
            j = pos[k]
            if k > 0:
                pj = pos[k - 1]
                if j is not None and j > 0 and (pj == j - 1 or pj is None):
                    out.append(text[spans[j - 1][1] : spans[j][0]])
                elif isinstance(child, list) and multiline:
                    out.append("\n" + self.indent * (depth + 1))
                else:
                    out.append(" ")
            if isinstance(child, list):
                out.append(self._emit(child, depth + 1))
            elif j is not None and child == orig[j] and type(child) is type(orig[j]):
                out.append(text[spans[j][0] : spans[j][1]])
            else:
                out.append(_atom_text(child))
        if node and pos[-1] is not None and pos[-1] == len(orig) - 1:
            out.append(tail_src)
        elif multiline:
            out.append("\n" + self.indent * depth + ")")
        else:
            out.append(")")
        return "".join(out)


# --- query helpers ---------------------------------------------------------------------------


def head(node) -> str | None:
    """The first atom of a list node (its keyword), or None."""
    if isinstance(node, list) and node and not isinstance(node[0], list):
        return str(node[0])
    return None


def find_all(node: list, name: str) -> Iterator[list]:
    """Direct child lists whose keyword is ``name``."""
    for child in node:
        if isinstance(child, list) and head(child) == name:
            yield child


def find(node: list, name: str) -> list | None:
    """First direct child list whose keyword is ``name``, or None."""
    return next(find_all(node, name), None)


def atom(node: list | None, index: int = 1, default: str | None = None) -> str | None:
    """Atom at ``index`` of a list node (``(at 1 2)`` -> ``atom(n, 1) == "1"``)."""
    if node is None or index >= len(node) or isinstance(node[index], list):
        return default
    return str(node[index])


def mm_to_nm(value: str | float | int) -> int:
    """Convert a millimetre value (as written in a KiCad file) to integer nanometres, exactly."""
    return int((Decimal(str(value)) * 1_000_000).to_integral_value())


def nm_to_mm_text(nm: int) -> str:
    """Integer nanometres as KiCad writes millimetres: shortest exact decimal (``89.37``, ``100``)."""
    d = (Decimal(int(nm)) / 1_000_000).normalize()
    t = format(d, "f")
    return "0" if t in ("-0", "0") else t
