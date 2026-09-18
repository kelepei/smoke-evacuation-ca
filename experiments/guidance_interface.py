"""D-side, recommendation-only evacuation guidance.

This module intentionally consumes normalized D snapshots and never writes a
target, position, or behavior back to B's CA runtime.  It gives a reviewable
baseline for future guidance research: each live person is compared against
physical (entity-level) exits using the real spatial smoke field and a
shortest feasible grid path.
"""

from __future__ import annotations

import csv
import heapq
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping


POLICY_ID = "smoke_distance_baseline_v1"
SCHEMA_VERSION = "guidance.v1"
DEFAULT_SMOKE_WEIGHT = 0.7
DEFAULT_DISTANCE_WEIGHT = 0.3

_NEIGHBORS = (
    (-1, -1, math.sqrt(2.0)),
    (0, -1, 1.0),
    (1, -1, math.sqrt(2.0)),
    (-1, 0, 1.0),
    (1, 0, 1.0),
    (-1, 1, math.sqrt(2.0)),
    (0, 1, 1.0),
    (1, 1, math.sqrt(2.0)),
)
_BLOCKED_CELL_TYPES = {"wall", "obstacle"}


class GuidanceError(ValueError):
    """Raised for an invalid D-normalized guidance input."""


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _normalise_field(raw: Any, width: int, height: int) -> list[list[float]] | None:
    if not isinstance(raw, list) or len(raw) != height:
        return None
    field: list[list[float]] = []
    for row in raw:
        if not isinstance(row, list) or len(row) != width:
            return None
        parsed: list[float] = []
        for value in row:
            numeric = _finite_number(value)
            if numeric is None or numeric < 0.0:
                return None
            parsed.append(numeric)
        field.append(parsed)
    return field


def _normalise_grid(snapshot: Mapping[str, Any]) -> tuple[int, int, list[str]]:
    grid = snapshot.get("grid")
    if not isinstance(grid, Mapping):
        raise GuidanceError("snapshot.grid is required")
    width = grid.get("width")
    height = grid.get("height")
    if isinstance(width, bool) or isinstance(height, bool):
        raise GuidanceError("snapshot.grid width and height must be integers")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise GuidanceError("snapshot.grid width and height must be positive integers")
    cell_types = grid.get("cell_type")
    if not isinstance(cell_types, list):
        raise GuidanceError("snapshot.grid.cell_type must be a dense layout")
    # CaSnapshotAdapter publishes rows; retain flat support for older D
    # snapshot fixtures without changing the row-major indexing contract.
    if len(cell_types) == height and all(isinstance(row, list) and len(row) == width for row in cell_types):
        return width, height, [str(value).strip().lower() for row in cell_types for value in row]
    if len(cell_types) == width * height:
        return width, height, [str(value).strip().lower() for value in cell_types]
    raise GuidanceError("snapshot.grid.cell_type must be a dense row-major layout")


def _entities(raw_entities: Any, width: int, height: int) -> list[tuple[str, tuple[tuple[int, int], ...]]]:
    if not isinstance(raw_entities, list):
        return []
    entities: list[tuple[str, tuple[tuple[int, int], ...]]] = []
    for raw in raw_entities:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("available") is False or raw.get("passable") is False:
            continue
        entity_id = raw.get("exit_entity_id")
        members = raw.get("member_cells")
        if entity_id in (None, "") or not isinstance(members, list):
            continue
        cells: list[tuple[int, int]] = []
        for member in members:
            if (
                not isinstance(member, (list, tuple))
                or len(member) != 2
                or isinstance(member[0], bool)
                or isinstance(member[1], bool)
                or not isinstance(member[0], int)
                or not isinstance(member[1], int)
            ):
                continue
            x, y = int(member[0]), int(member[1])
            if 0 <= x < width and 0 <= y < height:
                cells.append((x, y))
        if cells:
            entities.append((str(entity_id), tuple(sorted(set(cells), key=lambda cell: (cell[1], cell[0])))))
    return entities


def _is_traversable(x: int, y: int, width: int, height: int, cell_types: list[str]) -> bool:
    return 0 <= x < width and 0 <= y < height and cell_types[y * width + x] not in _BLOCKED_CELL_TYPES


def _shortest_path(
    *,
    start: tuple[int, int],
    goals: Iterable[tuple[int, int]],
    width: int,
    height: int,
    cell_types: list[str],
) -> tuple[list[tuple[int, int]], float] | None:
    goal_set = set(goals)
    if not goal_set or not _is_traversable(*start, width, height, cell_types):
        return None
    frontier: list[tuple[float, int, int]] = [(0.0, start[0], start[1])]
    distances = {start: 0.0}
    previous: dict[tuple[int, int], tuple[int, int]] = {}
    while frontier:
        distance, x, y = heapq.heappop(frontier)
        current = (x, y)
        if distance != distances.get(current):
            continue
        if current in goal_set:
            path = [current]
            while path[-1] != start:
                path.append(previous[path[-1]])
            path.reverse()
            return path, distance
        for dx, dy, cost in _NEIGHBORS:
            nx, ny = x + dx, y + dy
            if not _is_traversable(nx, ny, width, height, cell_types):
                continue
            # Do not let a diagonal move clip through two blocked orthogonal cells.
            if dx and dy and (
                not _is_traversable(x + dx, y, width, height, cell_types)
                or not _is_traversable(x, y + dy, width, height, cell_types)
            ):
                continue
            candidate = distance + cost
            neighbor = (nx, ny)
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                previous[neighbor] = current
                heapq.heappush(frontier, (candidate, nx, ny))
    return None


def _normalise_costs(candidates: list[dict[str, Any]], key: str, target: str) -> None:
    values = [float(candidate[key]) for candidate in candidates]
    minimum, maximum = min(values), max(values)
    if math.isclose(minimum, maximum, rel_tol=0.0, abs_tol=1e-12):
        for candidate in candidates:
            candidate[target] = 0.0
        return
    span = maximum - minimum
    for candidate in candidates:
        candidate[target] = (float(candidate[key]) - minimum) / span


def generate_guidance(
    snapshot: Mapping[str, Any],
    *,
    trigger_mode: str = "alarm",
    smoke_weight: float = DEFAULT_SMOKE_WEIGHT,
    distance_weight: float = DEFAULT_DISTANCE_WEIGHT,
    valid_for_steps: int = 1,
) -> dict[str, Any]:
    """Return deterministic entity-exit recommendations for one snapshot.

    In ``alarm`` mode, an explicit false alarm leaves the policy inactive.
    ``manual`` is intended for controlled tests/review and remains active even
    when the global alarm is false.  Neither mode mutates ``snapshot``.
    """

    if trigger_mode not in {"alarm", "manual"}:
        raise GuidanceError("trigger_mode must be 'alarm' or 'manual'")
    if not isinstance(valid_for_steps, int) or isinstance(valid_for_steps, bool) or valid_for_steps <= 0:
        raise GuidanceError("valid_for_steps must be a positive integer")
    weights = (float(smoke_weight), float(distance_weight))
    if any(not math.isfinite(value) or value < 0.0 for value in weights) or sum(weights) <= 0.0:
        raise GuidanceError("guidance weights must be finite, non-negative, and not both zero")

    step = snapshot.get("step")
    time_s = _finite_number(snapshot.get("time_s"))
    if not isinstance(step, int) or isinstance(step, bool) or step < 0 or time_s is None:
        raise GuidanceError("snapshot.step and snapshot.time_s are required")
    fields = snapshot.get("fields")
    if not isinstance(fields, Mapping):
        raise GuidanceError("snapshot.fields is required")
    alarm = fields.get("alarm_triggered")
    maximum = _finite_number(fields.get("max_smoke_concentration"))
    output: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "step": step,
        "time_s": time_s,
        "trigger": {"mode": trigger_mode, "alarm_triggered": alarm, "max_smoke_concentration": maximum},
        "policy_id": POLICY_ID,
        "policy": {
            "policy_id": POLICY_ID,
            "smoke_weight": weights[0],
            "distance_weight": weights[1],
            "parameter_source": "literature-informed project baseline; not a reproduction of paper odds ratios",
            "academic_fields_used": False,
        },
        "recommendations": [],
    }
    if trigger_mode == "alarm" and alarm is not True:
        output.update({"status": "inactive", "reason_code": "ALARM_NOT_TRIGGERED"})
        return output

    width, height, cell_types = _normalise_grid(snapshot)
    smoke_field = _normalise_field(fields.get("smoke_field"), width, height)
    if smoke_field is None:
        output.update({"status": "unavailable", "reason_code": "SMOKE_FIELD_UNAVAILABLE"})
        return output
    entities = _entities(snapshot.get("exit_entities"), width, height)
    if not entities:
        output.update({"status": "unavailable", "reason_code": "EXIT_ENTITIES_UNAVAILABLE"})
        return output

    output["status"] = "active"
    for person in sorted(snapshot.get("people", []), key=lambda item: int(item.get("person_id", -1)) if isinstance(item, Mapping) else -1):
        if not isinstance(person, Mapping) or person.get("evacuated") is True:
            continue
        person_id, x, y = person.get("person_id"), person.get("x"), person.get("y")
        if any(isinstance(value, bool) or not isinstance(value, int) for value in (person_id, x, y)):
            continue
        candidates: list[dict[str, Any]] = []
        for entity_id, member_cells in entities:
            route = _shortest_path(
                start=(x, y), goals=member_cells, width=width, height=height, cell_types=cell_types
            )
            if route is None:
                continue
            path, distance = route
            route_smoke = sum(smoke_field[py][px] for px, py in path) / len(path)
            candidates.append({
                "exit_entity_id": entity_id,
                "route_distance_cost": distance,
                "route_smoke_cost": route_smoke,
                "feasible": True,
            })
        if not candidates:
            output["recommendations"].append({
                "person_id": person_id,
                "recommended_exit_entity": None,
                "feasible": False,
                "reason_code": "NO_REACHABLE_ENTITY_EXIT",
                "reason_text": "没有找到可通行的实体出口路径。",
                "valid_until_step": step + valid_for_steps,
            })
            continue
        _normalise_costs(candidates, "route_distance_cost", "normalised_distance_cost")
        _normalise_costs(candidates, "route_smoke_cost", "normalised_smoke_cost")
        for candidate in candidates:
            candidate["combined_cost"] = (
                weights[0] * candidate["normalised_smoke_cost"]
                + weights[1] * candidate["normalised_distance_cost"]
            ) / sum(weights)
        chosen = min(candidates, key=lambda item: (item["combined_cost"], item["route_smoke_cost"], item["route_distance_cost"], item["exit_entity_id"]))
        nearest = min(candidates, key=lambda item: (item["route_distance_cost"], item["exit_entity_id"]))
        lower_smoke_than_nearest = chosen["exit_entity_id"] != nearest["exit_entity_id"] and chosen["route_smoke_cost"] < nearest["route_smoke_cost"]
        reason_code = "LOWER_SMOKE_ROUTE" if lower_smoke_than_nearest else "LOWEST_COMBINED_COST"
        reason_text = (
            "在可达实体出口中选择路径平均烟雾更低的方案；烟雾权重高于距离权重。"
            if lower_smoke_than_nearest
            else "在可达实体出口中选择烟雾与路径距离的加权成本最低方案。"
        )
        output["recommendations"].append({
            "person_id": person_id,
            "current_target_exit": person.get("target_exit"),
            "recommended_exit_entity": chosen["exit_entity_id"],
            "score": 1.0 - chosen["combined_cost"],
            "components": {
                "route_distance_cost": chosen["route_distance_cost"],
                "route_smoke_cost": chosen["route_smoke_cost"],
                "normalised_distance_cost": chosen["normalised_distance_cost"],
                "normalised_smoke_cost": chosen["normalised_smoke_cost"],
                "combined_cost": chosen["combined_cost"],
            },
            "feasible": True,
            "reason_code": reason_code,
            "reason_text": reason_text,
            "valid_until_step": step + valid_for_steps,
        })
    return output


def write_guidance_artifacts(guidance: Mapping[str, Any], output_dir: str | Path) -> None:
    """Persist a transparent recommendation artifact without touching B logs."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "guidance_recommendations.json").write_text(
        json.dumps(guidance, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    rows = guidance.get("recommendations")
    fieldnames = [
        "step", "time_s", "person_id", "recommended_exit_entity", "route_distance_cost",
        "route_smoke_cost", "combined_cost", "reason_code", "policy_id", "alarm_triggered",
        "max_smoke_concentration", "feasible",
    ]
    with (destination / "guidance_events.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for recommendation in rows if isinstance(rows, list) else []:
            if not isinstance(recommendation, Mapping):
                continue
            components = recommendation.get("components")
            components = components if isinstance(components, Mapping) else {}
            trigger = guidance.get("trigger")
            trigger = trigger if isinstance(trigger, Mapping) else {}
            policy = guidance.get("policy")
            policy = policy if isinstance(policy, Mapping) else {}
            writer.writerow({
                "step": guidance.get("step"), "time_s": guidance.get("time_s"),
                "person_id": recommendation.get("person_id"),
                "recommended_exit_entity": recommendation.get("recommended_exit_entity"),
                "route_distance_cost": components.get("route_distance_cost"),
                "route_smoke_cost": components.get("route_smoke_cost"),
                "combined_cost": components.get("combined_cost"),
                "reason_code": recommendation.get("reason_code"),
                "policy_id": policy.get("policy_id"),
                "alarm_triggered": trigger.get("alarm_triggered"),
                "max_smoke_concentration": trigger.get("max_smoke_concentration"),
                "feasible": recommendation.get("feasible"),
            })
