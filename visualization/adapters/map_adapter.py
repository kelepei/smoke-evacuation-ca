"""A-map adapter for D's week-4 integrated runtime."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any


class DMapAdapterError(ValueError):
    pass


def _cell_type(cell: Any) -> str:
    value = getattr(cell, "cell_type", None)
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def default_map_path(repo_root: Path) -> Path:
    for candidate in (
        repo_root / "maps" / "examples" / "simple_room.json",
        repo_root / "scenarios" / "simple_room.json",
    ):
        if candidate.is_file():
            return candidate
    raise DMapAdapterError("no default A JSON map found")


def load_grid_via_a(path: str | Path) -> Any:
    """Load A's Grid without reimplementing JSON/CSV parsing."""

    map_path = Path(path)
    if not map_path.is_file():
        raise DMapAdapterError(f"map file does not exist: {map_path}")
    suffix = map_path.suffix.lower()
    try:
        if suffix == ".json":
            from map_import.map_loader_grid import load_grid
        elif suffix == ".csv":
            from map_import.csv_loader_grid import load_csv_grid as load_grid
        elif suffix == ".png":
            from map_import.binary_to_grid import binary_to_grid
            from map_import.map_loader_image import load_image

            image_map = load_image(str(map_path))
            grid = binary_to_grid(image_map.binary)
            validate_grid_shape(grid)
            return grid
        else:
            raise DMapAdapterError("supported map formats: .json, .csv, .png")
        grid = load_grid(str(map_path))
    except (ImportError, AttributeError) as exc:
        raise DMapAdapterError(
            f"A loader for {suffix or 'unknown'} maps is not callable yet"
        ) from exc
    except Exception as exc:
        raise DMapAdapterError(f"A map loader failed: {type(exc).__name__}: {exc}") from exc

    validate_grid_shape(grid)
    return grid


def validate_grid_shape(grid: Any) -> None:
    width = int(getattr(grid, "width"))
    height = int(getattr(grid, "height"))
    cells = list(getattr(grid, "cells"))
    if width <= 0 or height <= 0:
        raise DMapAdapterError("grid width/height must be positive")
    if len(cells) != width * height:
        raise DMapAdapterError(
            f"grid.cells length must be width*height ({width * height}), got {len(cells)}"
        )
    for index, cell in enumerate(cells):
        x = int(getattr(cell, "x"))
        y = int(getattr(cell, "y"))
        if (x, y) != (index % width, index // width):
            raise DMapAdapterError("grid.cells must be dense row-major order")


def exit_cells(grid: Any) -> list[tuple[int, int]]:
    return [
        (int(cell.x), int(cell.y))
        for cell in grid.cells
        if _cell_type(cell) == "exit"
    ]


def smoke_source_cells(grid: Any) -> list[tuple[int, int]]:
    return [
        (int(cell.x), int(cell.y))
        for cell in grid.cells
        if _cell_type(cell) == "smoke_source"
    ]


def walkable_cells(grid: Any, *, include_exit: bool = False) -> list[tuple[int, int]]:
    allowed = {"free", "sign", "guide_zone"}
    if include_exit:
        allowed.add("exit")
    return [
        (int(cell.x), int(cell.y))
        for cell in grid.cells
        if _cell_type(cell) in allowed
    ]
