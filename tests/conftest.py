from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples" / "tpi-fixture"


@pytest.fixture
def tpi_board_path() -> Path:
    return FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb"


@pytest.fixture
def tpi_netlist_path() -> Path:
    return FIXTURE / "ATtiny10_TPI_Fixture.net"
