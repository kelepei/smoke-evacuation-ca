"""Week-4 snapshot compatibility wrapper."""

from __future__ import annotations

import math
from typing import Any

from visualization.ca_snapshot_adapter import CaSnapshotAdapter


def _empty_or_zero(field: list[list[float]]) -> bool:
    return not field or all(abs(value) <= 1e-12 for row in field for value in row)


def _fallback_smoke_field(
    *,
    width: int,
    height: int,
    step: int,
    source: tuple[int, int],
) -> list[list[float]]:
    sx, sy = source
    strength = min(1.0, 0.08 + step * 0.035)
    rows: list[list[float]] = []
    for y in range(height):
        row: list[float] = []
        for x in range(width):
            distance = math.hypot(x - sx, y - sy)
            row.append(round(max(0.0, strength * math.exp(-distance / 5.0)), 4))
        rows.append(row)
    return rows


class DWeek4SnapshotAdapter(CaSnapshotAdapter):
    """Normalize B snapshots and add D-only fallback smoke when needed."""

    def capture(self, simulation: Any) -> dict[str, Any]:
        snapshot = super().capture(simulation)
        grid = snapshot["grid"]
        smoke_field = snapshot.get("fields", {}).get("smoke_field", [])

        if getattr(simulation, "d_use_fallback_smoke", False) and _empty_or_zero(smoke_field):
            source = getattr(simulation, "d_fallback_smoke_source", (1, 1))
            smoke_field = _fallback_smoke_field(
                width=int(grid["width"]),
                height=int(grid["height"]),
                step=int(snapshot["step"]),
                source=source,
            )
            snapshot["fields"]["smoke_field"] = smoke_field
            snapshot.setdefault("adapter_meta", {}).setdefault("fallbacks", []).append(
                "D fallback smoke heatmap; pending B confirmation of official smoke output"
            )

        persons_by_id = {}
        raw_persons = getattr(simulation, "persons", {})
        if hasattr(raw_persons, "items"):
            persons_by_id = {int(pid): person for pid, person in raw_persons.items()}
        else:
            persons_by_id = {
                int(getattr(person, "id")): person for person in raw_persons
            }

        for person in snapshot["people"]:
            x = int(person["x"])
            y = int(person["y"])
            smoke = smoke_field[y][x] if smoke_field else None
            person["smoke"] = smoke
            person["smoke_concentration"] = smoke
            raw_person = persons_by_id.get(int(person["person_id"]))
            person["group_id"] = (
                None if raw_person is None else getattr(raw_person, "group_id", None)
            )

        return snapshot
