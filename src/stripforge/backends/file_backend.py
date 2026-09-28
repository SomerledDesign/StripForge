# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Headless .kicad_pcb backend via the S-expression reader (v0 default).

Reads a board, moves footprints (M2 part A) and writes it back byte-exactly except for the
edited nodes. Strips, cuts and links are M2 part B.
"""

from __future__ import annotations

from pathlib import Path

from ..board import Board, Footprint, load_board, save_board


class FileBackend:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.board: Board = load_board(self.path)

    def read_footprints(self) -> list[Footprint]:
        return self.board.footprints

    def move_footprints(self, moves: dict[str, tuple[int, int]]) -> None:
        """Translate footprints by ``{ref: (dx_nm, dy_nm)}``."""
        for ref, (dx, dy) in moves.items():
            self.board.footprint(ref).move(dx, dy)

    def save(self, path: str | Path | None = None) -> None:
        save_board(self.board, path or self.path)
