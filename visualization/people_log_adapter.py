"""Read B's portable ``people_log.csv`` for D-side replay and inspection.

This adapter is intentionally separate from D's richer CSV writer. It accepts
the exact B columns received in week four and keeps empty optional values as
``None``. It never alters reported positions or invents events.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("step", "time_s", "person_id", "x", "y", "evacuated")


class PeopleLogError(ValueError):
    """Raised when a B people log cannot be safely replayed."""


@dataclass(frozen=True)
class LoggedPerson:
    person_id: int
    x: int
    y: int
    evacuated: bool
    heading: str | None
    risk: float | None
    dose: float | None
    conflict: str | None
    exit_switch: str | None


@dataclass(frozen=True)
class PeopleLogFrame:
    step: int
    time_s: float
    people: tuple[LoggedPerson, ...]

    @property
    def evacuated_count(self) -> int:
        return sum(person.evacuated for person in self.people)


@dataclass(frozen=True)
class PeopleLog:
    frames: tuple[PeopleLogFrame, ...]


def _integer(value: Any, *, field: str, row_number: int) -> int:
    try:
        result = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise PeopleLogError(f"row {row_number}: {field} must be an integer") from exc
    if result < 0:
        raise PeopleLogError(f"row {row_number}: {field} must be non-negative")
    return result


def _number_or_none(value: Any, *, field: str, row_number: int) -> float | None:
    if value is None or not str(value).strip():
        return None
    try:
        result = float(str(value).strip())
    except ValueError as exc:
        raise PeopleLogError(f"row {row_number}: {field} must be numeric") from exc
    if not math.isfinite(result):
        raise PeopleLogError(f"row {row_number}: {field} must be finite")
    return result


def _text_or_none(value: Any) -> str | None:
    result = "" if value is None else str(value).strip()
    return result or None


def _boolean(value: Any, *, row_number: int) -> bool:
    result = str(value).strip().lower()
    if result in {"true", "1"}:
        return True
    if result in {"false", "0"}:
        return False
    raise PeopleLogError(f"row {row_number}: evacuated must be true/false (or 1/0)")


def load_people_log(path: str | Path) -> PeopleLog:
    """Load B CSV frames without inferring missing people or values."""

    source = Path(path)
    try:
        stream = source.open(newline="", encoding="utf-8-sig")
    except OSError as exc:
        raise PeopleLogError(f"cannot read people log: {source}") from exc
    with stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise PeopleLogError("people log must include a header row")
        fields = [field.strip() for field in reader.fieldnames]
        if len(set(fields)) != len(fields):
            raise PeopleLogError("people log header contains duplicate fields")
        missing = [field for field in REQUIRED_FIELDS if field not in fields]
        if missing:
            raise PeopleLogError("people log is missing required fields: " + ", ".join(missing))

        grouped: dict[int, dict[str, Any]] = {}
        for row_number, raw in enumerate(reader, start=2):
            if None in raw:
                raise PeopleLogError(f"row {row_number}: more cells than header fields")
            row = {str(key).strip(): value for key, value in raw.items()}
            if not any((value or "").strip() for value in row.values()):
                continue
            step = _integer(row.get("step"), field="step", row_number=row_number)
            time_s = _number_or_none(row.get("time_s"), field="time_s", row_number=row_number)
            if time_s is None or time_s < 0:
                raise PeopleLogError(f"row {row_number}: time_s must be non-negative")
            person = LoggedPerson(
                person_id=_integer(row.get("person_id"), field="person_id", row_number=row_number),
                x=_integer(row.get("x"), field="x", row_number=row_number),
                y=_integer(row.get("y"), field="y", row_number=row_number),
                evacuated=_boolean(row.get("evacuated"), row_number=row_number),
                heading=_text_or_none(row.get("heading")),
                risk=_number_or_none(row.get("risk"), field="risk", row_number=row_number),
                dose=_number_or_none(row.get("dose"), field="dose", row_number=row_number),
                conflict=_text_or_none(row.get("conflict")),
                exit_switch=_text_or_none(row.get("exit_switch")),
            )
            frame = grouped.setdefault(step, {"time_s": time_s, "people": {}})
            if not math.isclose(frame["time_s"], time_s, rel_tol=0.0, abs_tol=1e-12):
                raise PeopleLogError(f"row {row_number}: all rows in step {step} must share time_s")
            if person.person_id in frame["people"]:
                raise PeopleLogError(f"row {row_number}: duplicate person_id {person.person_id} in step {step}")
            frame["people"][person.person_id] = person

    if not grouped:
        raise PeopleLogError("people log does not contain any data rows")
    previous_time: float | None = None
    frames: list[PeopleLogFrame] = []
    for step in sorted(grouped):
        frame = grouped[step]
        if previous_time is not None and frame["time_s"] < previous_time:
            raise PeopleLogError("time_s must not decrease as step increases")
        previous_time = frame["time_s"]
        frames.append(PeopleLogFrame(step, frame["time_s"], tuple(frame["people"][key] for key in sorted(frame["people"]))))
    return PeopleLog(tuple(frames))
