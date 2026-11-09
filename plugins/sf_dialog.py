# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""KiCad action "StripForge: Dialog" (plugin.json). KiCad 10 passes no arguments to Python actions.

The only StripForge toolbar button. Analyze, Build strips, Add links, Run DRC, Build sheet and
Restore last are inside this window.
"""

import sys

from stripforge_plugin import run

if __name__ == "__main__":
    sys.exit(run("dialog"))
