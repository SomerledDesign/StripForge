# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Somerled Design
"""KiCad action "StripForge: Dialog" (plugin.json). KiCad 10 passes no arguments to Python actions.

Temporary extra button for the shared window. The other toolbar buttons stay until that window
covers the workflow.
"""

import sys

from stripforge_plugin import run

if __name__ == "__main__":
    sys.exit(run("dialog"))
