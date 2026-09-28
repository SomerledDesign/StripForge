"""Helpers for the build/link tests: a synthetic 10x5 stripboard with one-pad parts."""

from __future__ import annotations

from pathlib import Path

from kicad_text import fp, pad, pcb
from stripforge.board import load_board
from stripforge.sexpr import atom, find, head


def hole(col: int, row: int) -> str:
    return f"{1.27 + 2.54 * col:.2f} {1.27 + 2.54 * row:.2f}"


def one_pad_board(tmp: Path, parts: list[tuple[str, int, int, str | None]], extra: str = "") -> Path:
    """Board with a one-pad part per (ref, col, row, net) on the default 10x5 grid."""
    text = pcb(*[fp(ref, hole(c, r), pad("1", "0 0", net)) for ref, c, r, net in parts])
    if extra:
        text = text[:-1] + " " + extra + ")"
    path = tmp / "in.kicad_pcb"
    path.write_text(text, encoding="utf-8")
    return path


def segments(path: Path) -> list[tuple[tuple[float, float], tuple[float, float], float, str | None]]:
    """(start, end, width, net) of every segment in a board file, in mm."""
    out = []
    for n in load_board(path).doc.root:
        if head(n) != "segment":
            continue
        s, e = find(n, "start"), find(n, "end")
        net = find(n, "net")
        out.append(
            (
                (float(atom(s, 1)), float(atom(s, 2))),
                (float(atom(e, 1)), float(atom(e, 2))),
                float(atom(find(n, "width"), 1)),
                atom(net, 1) if net else None,
            )
        )
    return out


def xy(col: int, row: int) -> tuple[float, float]:
    return (round(1.27 + 2.54 * col, 4), round(1.27 + 2.54 * row, 4))


def footprints(path: Path, prefix: str) -> list:
    return [f for f in load_board(path).footprints if f.ref.startswith(prefix)]
