# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""Headless .kicad_pcb backend via the S-expression reader (v0 default).

M1 reads only; writing strips, cuts and links is M2.
"""

from ..board import Footprint, load_board


class FileBackend:
    def __init__(self, path: str):
        self.path = path

    def read_footprints(self) -> list[Footprint]:
        return load_board(self.path).footprints
