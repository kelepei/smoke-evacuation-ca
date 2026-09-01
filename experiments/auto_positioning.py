"""D-side bridge to A's in-memory initial-position allocation API."""

from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any, Mapping

from control.position_allocator import allocate_positions, validate_allocated_positions


class AutoPositioningError(ValueError):
    """Raised when uploaded JSON cannot enter A's allocation contract."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutoPositioningError(f"{label} must be valid UTF-8 JSON") from exc
    if not isinstance(data, Mapping):
        raise AutoPositioningError(f"{label} must be a JSON object")
    return dict(data)


def allocate_uploaded_positions(
    *, map_path: str | Path, people_path: str | Path, random_seed: int | None
) -> dict[str, Any]:
    """Allocate uploaded JSON people on their uploaded A-edited JSON map.

    The A API mutates ``people`` in place.  This bridge retains that exact
    behavior, validates the result through A, and writes only the temporary
    session copy of the people JSON used by D's existing runner.
    """

    resolved_map = Path(map_path)
    resolved_people = Path(people_path)
    if resolved_map.suffix.lower() != ".json":
        raise AutoPositioningError("automatic position allocation currently supports JSON maps only")

    map_data = _read_object(resolved_map, "map_file")
    people_data = _read_object(resolved_people, "population_file")
    people = people_data.get("persons")
    if not isinstance(people, list) or not all(isinstance(person, dict) for person in people):
        raise AutoPositioningError("population_file must contain persons as a list of objects")

    before_without_positions = []
    for person in people:
        preserved = copy.deepcopy(person)
        preserved.pop("x", None)
        preserved.pop("y", None)
        before_without_positions.append(preserved)

    if random_seed is not None:
        random.seed(random_seed)
    try:
        allocate_positions(people, map_data)
        validate_allocated_positions(people, map_data)
    except (TypeError, ValueError) as exc:
        raise AutoPositioningError(str(exc)) from exc

    after_without_positions = []
    for person in people:
        preserved = copy.deepcopy(person)
        preserved.pop("x", None)
        preserved.pop("y", None)
        after_without_positions.append(preserved)
    if after_without_positions != before_without_positions:
        raise AutoPositioningError("A allocation changed non-position person fields")

    resolved_people.write_text(
        json.dumps(people_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "person_count": len(people),
        "random_seed": random_seed,
        "source": "control.position_allocator.allocate_positions",
    }
