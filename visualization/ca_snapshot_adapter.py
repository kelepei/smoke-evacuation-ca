"""D-side adapter from B runtimes to the shared snapshot shape.

This module deliberately uses structural (duck-typed) reads so the
visualization layer does not modify or depend on concrete A/B/C classes. B's
current public ``EvacEngine`` fields are preferred; old private state is only
a compatibility fallback.
"""

from __future__ import annotations

from enum import Enum
from numbers import Integral, Real
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = "0.1-draft"


class SnapshotAdapterError(ValueError):
    """Raised when an upstream runtime cannot be represented safely."""


def _enum_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.name
    return value


def _enum_storage_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _heading_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = _enum_storage_value(value)
    return str(raw).strip().lower().replace("_", "-")


def _optional_attr(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _person_items(persons: Any) -> list[tuple[Any, Any]]:
    if isinstance(persons, Mapping):
        items = list(persons.items())
    elif isinstance(persons, Iterable):
        items = [(getattr(person, "id", None), person) for person in persons]
    else:
        raise SnapshotAdapterError("simulation.persons must be a mapping or iterable")

    if any(person_id is None for person_id, _ in items):
        raise SnapshotAdapterError("every person must expose a non-null id")
    return sorted(
        items,
        key=lambda item: (
            0 if isinstance(item[0], Integral) else 1,
            int(item[0]) if isinstance(item[0], Integral) else str(item[0]),
        ),
    )


def _matrix_to_lists(
    matrix: Any,
    *,
    width: int,
    height: int,
    field_name: str,
) -> list[list[float]]:
    if matrix is None:
        return []

    raw = matrix.tolist() if hasattr(matrix, "tolist") else matrix
    rows = [list(row) for row in raw]
    if len(rows) != height or any(len(row) != width for row in rows):
        raise SnapshotAdapterError(
            f"{field_name} shape must be ({height}, {width}), "
            f"got ({len(rows)}, {len(rows[0]) if rows else 0})"
        )

    normalized: list[list[float]] = []
    for row in rows:
        normalized_row: list[float] = []
        for value in row:
            if not isinstance(value, Real):
                raise SnapshotAdapterError(
                    f"{field_name} must contain only numeric values"
                )
            normalized_row.append(float(value))
        normalized.append(normalized_row)
    return normalized


def _relation_to_dict(relation: Any) -> dict[str, Any]:
    return {
        "person_a_id": _optional_attr(
            relation, "person_a_id", "from", "source_person_id"
        ),
        "person_b_id": _optional_attr(
            relation, "person_b_id", "to", "target_person_id"
        ),
        "relation_type": _enum_storage_value(
            _optional_attr(relation, "relation_type")
        ),
        "strength": _optional_attr(relation, "strength"),
        "trust": _optional_attr(relation, "trust"),
    }


class CaSnapshotAdapter:
    """Read-only adapter for the current B simulation object."""

    def __init__(
        self,
        *,
        run_id: str,
        time_step_s: float = 0.5,
        schema_version: str = SCHEMA_VERSION,
        random_seed: int | None = None,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")
        if time_step_s <= 0:
            raise ValueError("time_step_s must be greater than zero")

        self.run_id = run_id
        self.time_step_s = float(time_step_s)
        self.schema_version = schema_version
        self.random_seed = random_seed

    def capture(self, simulation: Any) -> dict[str, Any]:
        """Capture one immutable-by-convention D snapshot.

        Missing upstream fields remain ``None`` or empty containers.  The
        adapter never fabricates target exits, risks, information states,
        congestion, conflicts, or strategy decisions.
        """

        grid = getattr(simulation, "grid", None)
        if grid is None:
            raise SnapshotAdapterError("simulation.grid is required")

        width = int(getattr(grid, "width"))
        height = int(getattr(grid, "height"))
        if width <= 0 or height <= 0:
            raise SnapshotAdapterError("grid width and height must be positive")
        cells = list(getattr(grid, "cells", []))
        if len(cells) != width * height:
            raise SnapshotAdapterError(
                f"grid.cells must contain exactly {width * height} cells"
            )

        cell_type: list[list[Any]] = []
        for y in range(height):
            row: list[Any] = []
            for x in range(width):
                cell = cells[y * width + x]
                if int(getattr(cell, "x")) != x or int(getattr(cell, "y")) != y:
                    raise SnapshotAdapterError(
                        "grid.cells must use dense row-major order: "
                        "grid.cells[y * width + x]"
                    )
                row.append(_enum_storage_value(getattr(cell, "cell_type")))
            cell_type.append(row)

        step = int(getattr(simulation, "current_step", 0))
        if step < 0:
            raise SnapshotAdapterError("current_step must be non-negative")

        config = getattr(simulation, "config", None)
        scenario_id = _optional_attr(config, "scenario_id")
        if scenario_id is None or str(scenario_id).strip() == "":
            raise SnapshotAdapterError("simulation.config.scenario_id is required")
        random_seed = self.random_seed
        if random_seed is None:
            random_seed = _optional_attr(config, "random_seed")
        parameters = getattr(config, "parameters", None)
        if random_seed is None and isinstance(parameters, Mapping):
            random_seed = parameters.get("random_seed")

        smoke_sim = getattr(simulation, "smoke_sim", None)
        public_smoke_matrix = _optional_attr(simulation, "smoke_matrix")
        smoke_field = _matrix_to_lists(
            public_smoke_matrix
            if public_smoke_matrix is not None
            else getattr(smoke_sim, "smoke_matrix", None),
            width=width,
            height=height,
            field_name="smoke_field",
        )
        risk_field = _matrix_to_lists(
            _optional_attr(simulation, "risk_field", "risk_matrix"),
            width=width,
            height=height,
            field_name="risk_field",
        )
        congestion_field = _matrix_to_lists(
            _optional_attr(simulation, "congestion_field", "congestion_matrix"),
            width=width,
            height=height,
            field_name="congestion_field",
        )
        if any(value < 0.0 for row in smoke_field for value in row):
            raise SnapshotAdapterError("smoke_field values must be non-negative")
        if any(
            value < 0.0 or value > 1.0
            for row in congestion_field
            for value in row
        ):
            raise SnapshotAdapterError(
                "congestion_field values must be within [0, 1]"
            )

        evacuated_fallback = getattr(simulation, "_evacuated_status", {})
        if not isinstance(evacuated_fallback, Mapping):
            evacuated_fallback = {}

        people: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        for mapping_id, person in _person_items(getattr(simulation, "persons", None)):
            person_id = getattr(person, "id", mapping_id)
            if isinstance(getattr(simulation, "persons", None), Mapping):
                if mapping_id != person_id:
                    raise SnapshotAdapterError(
                        "simulation.persons key must match person.id"
                    )
            if (
                isinstance(person_id, bool)
                or not isinstance(person_id, Integral)
                or int(person_id) <= 0
            ):
                raise SnapshotAdapterError(
                    "person_id must be a globally unique positive integer"
                )
            person_id = int(person_id)
            if person_id in seen_ids:
                raise SnapshotAdapterError(f"duplicate person id: {person_id!r}")
            seen_ids.add(person_id)

            x = int(getattr(person, "x"))
            y = int(getattr(person, "y"))
            if not (0 <= x < width and 0 <= y < height):
                raise SnapshotAdapterError(
                    f"person {person_id!r} position ({x}, {y}) is outside the grid"
                )

            raw_evacuated = _optional_attr(person, "evacuated")
            if raw_evacuated is None and person_id in evacuated_fallback:
                raw_evacuated = evacuated_fallback[person_id]
            if raw_evacuated is None:
                raw_evacuated = False
            evacuated = bool(raw_evacuated)

            target_exit = _optional_attr(person, "target_exit_id", "target_exit")
            if target_exit is not None and hasattr(target_exit, "id"):
                target_exit = target_exit.id

            smoke_concentration = (
                smoke_field[y][x] if smoke_field else None
            )
            status = _optional_attr(person, "status")
            if status is None and evacuated:
                status = "EVACUATED"

            info_history = _optional_attr(person, "info_source_history")
            if info_history is None:
                normalized_info_history: list[Any] = []
            elif isinstance(info_history, (list, tuple)):
                normalized_info_history = list(info_history)
            else:
                normalized_info_history = [info_history]
            people.append(
                {
                    "person_id": person_id,
                    "x": x,
                    "y": y,
                    "heading": _heading_value(_optional_attr(person, "heading")),
                    "status": _enum_value(status),
                    "target_exit": target_exit,
                    "actual_exit": _optional_attr(person, "actual_exit"),
                    "evacuated": evacuated,
                    "smoke": smoke_concentration,
                    "smoke_concentration": smoke_concentration,
                    "risk": _optional_attr(person, "risk", "risk_value"),
                    "dose": _optional_attr(person, "dose", "smoke_dose"),
                    "group_id": _optional_attr(person, "group_id"),
                    "info_state": _enum_value(_optional_attr(person, "info_state")),
                    "info_source": _optional_attr(person, "info_source"),
                    "info_source_history": normalized_info_history,
                    "receive_time": _optional_attr(
                        person, "receive_time", "first_alert_time"
                    ),
                    "follow_target": _optional_attr(person, "follow_target"),
                }
            )

        exits: list[dict[str, Any]] = []
        for exit_obj in getattr(config, "exits", []) if config is not None else []:
            exits.append(
                {
                    "exit_id": _optional_attr(exit_obj, "id", "exit_id"),
                    "queue_length": _optional_attr(exit_obj, "queue_length"),
                }
            )

        relations: list[dict[str, Any]] = []
        for index, relation in enumerate(
            getattr(config, "relations", []) if config is not None else []
        ):
            normalized_relation = _relation_to_dict(relation)
            for endpoint in ("person_a_id", "person_b_id"):
                endpoint_id = normalized_relation[endpoint]
                if (
                    isinstance(endpoint_id, bool)
                    or not isinstance(endpoint_id, Integral)
                    or int(endpoint_id) <= 0
                ):
                    raise SnapshotAdapterError(
                        f"relations[{index}].{endpoint} must be a positive integer"
                    )
                endpoint_id = int(endpoint_id)
                if endpoint_id not in seen_ids:
                    raise SnapshotAdapterError(
                        f"relations[{index}].{endpoint} is not in people"
                    )
                normalized_relation[endpoint] = endpoint_id
            if (
                normalized_relation["person_a_id"]
                == normalized_relation["person_b_id"]
            ):
                raise SnapshotAdapterError(
                    f"relations[{index}] must connect two different people"
                )
            if normalized_relation["relation_type"] in (None, ""):
                raise SnapshotAdapterError(
                    f"relations[{index}].relation_type is required"
                )
            relations.append(normalized_relation)

        raw_cell_size = _optional_attr(grid, "cell_size")
        if raw_cell_size is None or float(raw_cell_size) <= 0:
            raise SnapshotAdapterError("grid.cell_size must be greater than zero")

        adapter_meta = {
            "simulation_module": simulation.__class__.__module__,
            "grid_layout_assumption": (
                "temporary B mock: cells[y * width + x], fields[y][x], "
                "display origin upper; shared A/B/D rule is not frozen"
            ),
            "derived_fields": (
                ["people.status"] if any(
                    person["status"] == "EVACUATED" for person in people
                ) else []
            ),
            "private_fallbacks": (
                ["simulation._evacuated_status"]
                if any(
                    _optional_attr(person, "evacuated") is None
                    for _, person in _person_items(getattr(simulation, "persons", None))
                )
                else []
            ),
            "missing_fields_are_null": True,
            "missing_values_are_not_inferred": True,
            "smoke_value_domain": "B raw dimensionless concentration in [0, 10]; smoke_matrix[y][x]",
        }
        extra_meta = getattr(simulation, "d_adapter_meta", None)
        if isinstance(extra_meta, Mapping):
            adapter_meta.update(dict(extra_meta))

        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "scenario_id": scenario_id,
            "random_seed": random_seed,
            "step": step,
            "time_step": self.time_step_s,
            "time_s": step * self.time_step_s,
            "grid": {
                "width": width,
                "height": height,
                "cell_size": float(raw_cell_size),
                "cell_type": cell_type,
            },
            "people": people,
            "exits": exits,
            "fields": {
                "smoke_field": smoke_field,
                "risk_field": risk_field,
                "congestion_field": congestion_field,
            },
            "relations": relations,
            "events": [],
            "strategy_state": {},
            "adapter_meta": adapter_meta,
        }
