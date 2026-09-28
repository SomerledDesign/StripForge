# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""KiCad action "StripForge: Build strips" (plugin.json). KiCad 10 passes no arguments to Python actions."""

import sys

from stripforge_plugin import run

if __name__ == "__main__":
    sys.exit(run("build"))
