"""Wrap `kicad-cli pcb drc --format json --severity-all --schematic-parity --exit-code-violations`.

KiCad 10 IPC cannot run DRC (Sketch.md §3), so the CLI is the only path. Exit code 0 = clean,
5 = violations. Classify into short / open / parity / stripboard-rule / other. Stub.
"""

KICAD_CLI_MAC = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


def run_drc(board_path: str, kicad_cli: str = "kicad-cli") -> dict:
    raise NotImplementedError


def classify(report: dict) -> dict[str, list]:
    raise NotImplementedError
