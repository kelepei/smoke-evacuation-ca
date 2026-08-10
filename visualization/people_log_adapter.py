"""Read B's per-person CSV log without inventing absent state.

The adapter accepts B's current 11-column interchange format.  It preserves
empty optional values and same-cell people exactly as logged so D replay can
show upstream state rather than a repaired demonstration.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


REQUIRED_COLUMNS = ("step", "time_s", "person_id", "x", "y", "evacuated")
OPTIONAL_COLUMNS = ("heading", "risk", "dose", "conflict", "exit_switch")


class PeopleLogError(ValueError):
    """Raised when a B people-log cannot be represented safely by D."""


def _int(value: str | None, field: str, *, minimum: int) -> int:
    if value is None or value.strip() == "":
        raise PeopleLogError(f"{field} is required")
    try:
        result = int(value)
    except ValueError as exc:
        raise PeopleLogError(f"{field} must be an integer") from exc
    if result < minimum:
        raise PeopleLogError(f"{field} must be at least {minimum}")
    return result


def _float(value: str | None, field: str) -> float:
    if value is None or value.strip() == "":
        raise PeopleLogError(f"{field} is required")
    try:
        result = float(value)
    except ValueError as exc:
        raise PeopleLogError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise PeopleLogError(f"{field} must be finite")
    return result


def _bool(value: str | None, field: str) -> bool:
    if value is None:
        raise PeopleLogError(f"{field} is required")
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise PeopleLogError(f"{field} must be true/false or 1/0")


@dataclass(frozen=True)
class PeopleLogRow:
    """One B-provided person state; optional values stay ``None`` when empty."""

    step: int
    time_s: float
    person_id: int
    x: int
    y: int
    evacuated: bool
    heading: str | None
    risk: float | None
    dose: float | None
    conflict: str | None
    exit_switch: str | None

    def as_person_snapshot(self) -> dict[str, Any]:
        """Expose only fields explicitly present in B's interchange record."""

        return {
            "person_id": self.person_id,
            "x": self.x,
            "y": self.y,
            "evacuated": self.evacuated,
            "heading": self.heading,
            "risk": self.risk,
            "dose": self.dose,
            "conflict": self.conflict,
            "exit_switch": self.exit_switch,
        }


def _optional_float(value: str | None, field: str) -> float | None:
    if value is None or value.strip() == "":
        return None
    return _float(value, field)


def _optional_text(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return value


def load_people_log(path: str | Path) -> tuple[PeopleLogRow, ...]:
    """Read B's CSV log in source order without changing its movement data."""

    csv_path = Path(path)
    if not csv_path.is_file():
        raise PeopleLogError(f"people log does not exist: {csv_path}")
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as file_handle:
            reader = csv.DictReader(file_handle)
            fieldnames = reader.fieldnames or []
            missing = [field for field in REQUIRED_COLUMNS if field not in fieldnames]
            if missing:
                raise PeopleLogError(
                    "people log is missing required column(s): " + ", ".join(missing)
                )
            rows = []
            seen_step_person: set[tuple[int, int]] = set()
            for row_number, raw in enumerate(reader, start=2):
                try:
                    row = PeopleLogRow(
                        step=_int(raw.get("step"), "step", minimum=0),
                        time_s=_float(raw.get("time_s"), "time_s"),
                        person_id=_int(raw.get("person_id"), "person_id", minimum=1),
                        x=_int(raw.get("x"), "x", minimum=0),
                        y=_int(raw.get("y"), "y", minimum=0),
                        evacuated=_bool(raw.get("evacuated"), "evacuated"),
                        heading=_optional_text(raw.get("heading")),
                        risk=_optional_float(raw.get("risk"), "risk"),
                        dose=_optional_float(raw.get("dose"), "dose"),
                        conflict=_optional_text(raw.get("conflict")),
                        exit_switch=_optional_text(raw.get("exit_switch")),
                    )
                except PeopleLogError as exc:
                    raise PeopleLogError(f"people log row {row_number}: {exc}") from exc
                identity = (row.step, row.person_id)
                if identity in seen_step_person:
                    raise PeopleLogError(
                        f"people log row {row_number}: duplicate step/person_id {identity}"
                    )
                seen_step_person.add(identity)
                rows.append(row)
    except OSError as exc:
        raise PeopleLogError(f"cannot read people log: {csv_path}") from exc
    return tuple(rows)


def rows_for_step(rows: tuple[PeopleLogRow, ...], step: int) -> tuple[PeopleLogRow, ...]:
    """Return source-order rows for a step, including intentional overlaps."""

    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise PeopleLogError("step must be a non-negative integer")
    return tuple(row for row in rows if row.step == step)


def replay_people(rows: tuple[PeopleLogRow, ...], step: int) -> tuple[Mapping[str, Any], ...]:
    """Return D replay records, retaining B-provided empty optional fields."""

    return tuple(row.as_person_snapshot() for row in rows_for_step(rows, step))
