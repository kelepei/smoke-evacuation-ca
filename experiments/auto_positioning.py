"""D bridge for A's in-memory initial-position allocation API."""

from __future__ import annotations

import copy
import random
from typing import Any, Mapping

from control.position_allocator import allocate_positions, validate_allocated_positions


class AutoPositioningError(ValueError):
    """Raised when browser JSON cannot enter A's allocation contract."""


def allocate_map_data_positions(
    *, map_data: Mapping[str, Any], people_data: Mapping[str, Any], random_seed: int | None
) -> dict[str, Any]:
    """Allocate positions on the caller's edited map without changing other C data."""

    if not isinstance(map_data, Mapping):
        raise AutoPositioningError("map_data must be a JSON object")
    if not isinstance(people_data, Mapping):
        raise AutoPositioningError("population_file must be a JSON object")
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
        allocate_positions(people, dict(map_data))
        validate_allocated_positions(people, dict(map_data))
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

    return {
        "person_count": len(people),
        "random_seed": random_seed,
        "source": "control.position_allocator.allocate_positions",
    }
