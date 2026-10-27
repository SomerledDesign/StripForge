from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# The M1 TPI fixture (25 footprints, 36 nets; commit cea21cd), frozen so the M1/M2A tests keep
# their exact numbers. examples/tpi-fixture/ is the real-parts board (see test_real_fixture.py).
FIXTURE = ROOT / "tests" / "fixtures" / "tpi-m1"
REAL_FIXTURE = ROOT / "examples" / "tpi-fixture"
# M4c: diagonal links are off by default now. The real-fixture regression tests keep their numbers
# (and their diagonal links) with examples/x56.toml plus these lines, written to a temp file.
LEGACY_DIAGONALS = "\ndiagonal_links = true\nmax_diagonal_shift = 32\n"


def _x56_diagonals() -> Path:
    import tempfile

    path = Path(tempfile.mkdtemp(prefix="sf-x56-")) / "x56-diagonals.toml"
    path.write_text((ROOT / "examples" / "x56.toml").read_text() + LEGACY_DIAGONALS)
    return path


X56_DIAGONALS = _x56_diagonals()


@pytest.fixture
def tpi_board_path() -> Path:
    return FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb"


@pytest.fixture
def tpi_netlist_path() -> Path:
    return FIXTURE / "ATtiny10_TPI_Fixture.net"


@pytest.fixture
def real_board_path() -> Path:
    return REAL_FIXTURE / "ATtiny10_TPI_Fixture.kicad_pcb"


@pytest.fixture
def real_netlist_path() -> Path:
    return REAL_FIXTURE / "ATtiny10_TPI_Fixture.net"
