"""Board configuration (stripboard.toml). Stub."""

from dataclasses import dataclass, field
from enum import StrEnum


class CutStyle(StrEnum):
    HOLE = "hole"  # spot-face cut at a free hole (default)
    KNIFE = "knife"  # score between two adjacent holes
    AUTO = "auto"  # hole when possible, else knife (+ warning)


@dataclass
class BoardConfig:
    rows: int = 25
    cols: int = 64
    origin_mm: tuple[float, float] = (100.0, 100.0)
    strip_width_mm: float = 1.8  # Mildrew to confirm
    cut_style: CutStyle = CutStyle.AUTO
    snap_tol_mm: float = 0.15
    cut_marker_layer: str = "User.1"  # renamed "Strip.Cuts" in the board file
    offboard_refs: list[str] = field(default_factory=list)


def load(path: str) -> BoardConfig:
    raise NotImplementedError
