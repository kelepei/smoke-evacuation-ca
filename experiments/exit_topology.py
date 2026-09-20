"""D-side topology for grouping contiguous exit cells into physical exits.

The B runtime keeps one traversable cell per exit cell.  This module never
changes that representation: it only supplies a stable entity-level view for
D logs, analysis, and future guidance.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable


_FOUR_CONNECTED = ((0, -1), (-1, 0), (1, 0), (0, 1))


def _cell_type_value(cell: Any) -> str:
    value = getattr(cell, "cell_type", None)
    return str(getattr(value, "value", value)).strip().lower()


@dataclass(frozen=True)
class ExitEntity:
    """One physical exit represented by a 4-connected set of exit cells."""

    exit_entity_id: str
    member_cells: tuple[tuple[int, int], ...]
    centroid: tuple[float, float]
    cell_count: int
    width_m: float | None
    width_note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_entity_id": self.exit_entity_id,
            "member_cells": [[x, y] for x, y in self.member_cells],
            "centroid": [self.centroid[0], self.centroid[1]],
            "cell_count": self.cell_count,
            "width_m": self.width_m,
            "width_note": self.width_note,
        }


def _width_for_component(
    member_cells: tuple[tuple[int, int], ...], cell_size_m: float | None
) -> tuple[float | None, str | None]:
    if cell_size_m is None:
        return None, "width_m unavailable: no validated cell_size_m was supplied"
    if not math.isfinite(cell_size_m) or cell_size_m <= 0:
        raise ValueError("cell_size_m must be a finite positive number when supplied")

    xs = {x for x, _ in member_cells}
    ys = {y for _, y in member_cells}
    if len(xs) == 1 or len(ys) == 1:
        return float(len(member_cells) * cell_size_m), None
    return (
        None,
        "width_m unavailable: a non-linear exit component has no unambiguous straight width",
    )


def extract_exit_entities(
    grid: Any, *, cell_size_m: float | None = None
) -> tuple[ExitEntity, ...]:
    """Return deterministic 4-connected exit entities from a dense Grid.

    ``grid.cell_size`` is deliberately not treated as a validated physical
    scale.  Callers must pass ``cell_size_m`` explicitly before width is
    emitted in metres; this avoids silently converting an unverified map-scale
    metadata value into a physical measurement.
    """

    width = int(getattr(grid, "width"))
    height = int(getattr(grid, "height"))
    cells = list(getattr(grid, "cells"))
    if width <= 0 or height <= 0 or len(cells) != width * height:
        raise ValueError("grid must contain a dense width * height cell layout")

    exit_cells = {
        (x, y)
        for y in range(height)
        for x in range(width)
        if _cell_type_value(cells[y * width + x]) == "exit"
    }
    entities: list[ExitEntity] = []
    while exit_cells:
        seed = min(exit_cells, key=lambda point: (point[1], point[0]))
        queue: deque[tuple[int, int]] = deque([seed])
        exit_cells.remove(seed)
        component: list[tuple[int, int]] = []
        while queue:
            x, y = queue.popleft()
            component.append((x, y))
            for dx, dy in _FOUR_CONNECTED:
                neighbor = (x + dx, y + dy)
                if neighbor in exit_cells:
                    exit_cells.remove(neighbor)
                    queue.append(neighbor)

        member_cells = tuple(sorted(component, key=lambda point: (point[1], point[0])))
        width_m, width_note = _width_for_component(member_cells, cell_size_m)
        entities.append(
            ExitEntity(
                exit_entity_id=f"exit_entity_{len(entities) + 1:02d}",
                member_cells=member_cells,
                centroid=(
                    sum(x for x, _ in member_cells) / len(member_cells),
                    sum(y for _, y in member_cells) / len(member_cells),
                ),
                cell_count=len(member_cells),
                width_m=width_m,
                width_note=width_note,
            )
        )
    return tuple(entities)


def entity_id_by_cell(entities: Iterable[ExitEntity]) -> dict[tuple[int, int], str]:
    """Build the cell-level to entity-level mapping without changing B IDs."""

    result: dict[tuple[int, int], str] = {}
    for entity in entities:
        for cell in entity.member_cells:
            if cell in result:
                raise ValueError(f"exit cell {cell!r} belongs to multiple entities")
            result[cell] = entity.exit_entity_id
    return result
