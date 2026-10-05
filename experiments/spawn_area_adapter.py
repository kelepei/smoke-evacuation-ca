"""D-side bridge for A's spawn-area mask without changing the source map."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from map_import.spawn_area import generate_spawn_mask


_CELL_SPAWN_KEYS = ("spawn_area", "spawn", "spawnable", "can_spawn", "inside")


def _has_explicit_spawn_area(map_data: Mapping[str, Any]) -> bool:
    if "spawn_area" in map_data:
        return True
    cells = map_data.get("cells")
    return isinstance(cells, list) and any(
        isinstance(cell, Mapping) and any(key in cell for key in _CELL_SPAWN_KEYS)
        for cell in cells
    )


def prepare_spawn_area_map(map_data: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return an allocation-only map copy carrying A's ``spawn_area`` contract.

    Explicit map annotations always win.  Otherwise A's own flood-fill helper
    derives a mask and D materializes it only on the temporary map copy passed
    into A's allocator.  The uploaded/source map object is never mutated.
    """
    if not isinstance(map_data, Mapping):
        raise ValueError("map_data must be an object")
    cells = map_data.get("cells")
    if not isinstance(cells, list):
        raise ValueError("map_data.cells must be a list")

    prepared = deepcopy(dict(map_data))
    if _has_explicit_spawn_area(prepared):
        return prepared, {"source": "explicit_map_spawn_area", "generated": False}

    spawn_mask = generate_spawn_mask(prepared)
    allowed = 0
    for cell in prepared["cells"]:
        if not isinstance(cell, dict):
            raise ValueError("map_data.cells must contain objects")
        coordinate = (int(cell["x"]), int(cell["y"]))
        allowed_here = bool(spawn_mask.get(coordinate, False))
        cell["spawn_area"] = allowed_here
        allowed += int(allowed_here)
    return prepared, {
        "source": "map_import.spawn_area.generate_spawn_mask",
        "generated": True,
        "spawnable_cell_count": allowed,
    }
