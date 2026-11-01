"""Canonical physical assumptions shared by every D runtime entry point."""

from __future__ import annotations

import math
from typing import Any


PLATFORM_PHYSICAL_CELL_SIZE_M = 0.5
PLATFORM_PHYSICAL_SCALE_SOURCE = "platform_simulation_assumption"
PLATFORM_PHYSICAL_SCALE_NOTE = (
    "Uniform platform simulation assumption; this is not a surveyed building "
    "measurement. Map-declared real-world calibration, when present, is "
    "preserved separately in analysis_contract."
)


def platform_physical_cell_size(value: Any = None, *, field: str = "physical_cell_size_m") -> float:
    """Return the fixed simulation scale, rejecting API-requested alternatives."""

    if value in (None, ""):
        return PLATFORM_PHYSICAL_CELL_SIZE_M
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric or empty")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{field} must be finite")
    if not math.isclose(
        normalized,
        PLATFORM_PHYSICAL_CELL_SIZE_M,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError(
            f"{field} is fixed at {PLATFORM_PHYSICAL_CELL_SIZE_M:g} m per cell "
            "for this platform; an explicit conflicting value is not allowed"
        )
    return PLATFORM_PHYSICAL_CELL_SIZE_M
