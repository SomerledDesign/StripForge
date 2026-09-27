# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Minimal S-expression reader/writer for KiCad files (.kicad_pcb, .kicad_mod, .net).

A parsed node is a Python ``list``. Atoms are ``str``: bare tokens come back as :class:`Sym`
(a ``str`` subclass) and quoted strings as plain ``str``, so ``dumps`` can re-quote exactly the
atoms that were quoted. Numbers stay as their original text; convert them where they are used
(see :func:`mm_to_nm`). ``dumps`` writes KiCad-style tab-indented text. It preserves every node
but not the original whitespace; byte-exact round-trips are an M2 concern (Sketch.md §4.7).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from decimal import Decimal

__all__ = ["Sym", "SExprError", "loads", "dumps", "head", "find", "find_all", "atom", "mm_to_nm"]


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


def loads(text: str) -> list:
    """Parse one top-level S-expression and return it as nested lists."""
    stack: list[list] = []
    result: list | None = None
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
            node: list = []
            if stack:
                stack[-1].append(node)
            stack.append(node)
        elif cls:
            if not stack:
                raise SExprError(f"unbalanced ')' at offset {m.start(2)}")
            done = stack.pop()
            if not stack:
                result = done
        elif qstr is not None:
            if not stack:
                raise SExprError("atom outside of any list")
            stack[-1].append(_unescape(qstr))
        elif bare is not None:
            if not stack:
                raise SExprError("atom outside of any list")
            stack[-1].append(Sym(bare))
    if stack:
        raise SExprError(f"unexpected end of input: {len(stack)} unclosed '('")
    if result is None:
        raise SExprError("no S-expression found")
    return result


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
