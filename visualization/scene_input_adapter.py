"""D-side adapters for A map files and C population configuration.

The adapters deliberately keep ownership with the upstream modules:

* A remains responsible for parsing JSON/CSV into ``core.grid.Grid``.
* C remains responsible for parsing YAML into ``SceneConfig``.
* D validates the contracts it consumes and converts only the data needed by
  the visualizer.  It does not create people, relations, smoke sources, or
  simulation decisions that the upstream modules did not provide.
"""

from __future__ import annotations

import importlib.util
import types
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class SceneInputError(ValueError):
    """Raised when an upstream input cannot be represented safely by D."""


def _enum_storage_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    return value


def _required_positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SceneInputError(f"{field} must be a positive integer")
    return int(value)


def validate_grid(grid: Any) -> Any:
    """Validate A's canonical dense row-major Grid without mutating it."""

    try:
        width = _required_positive_int(getattr(grid, "width"), "grid.width")
        height = _required_positive_int(getattr(grid, "height"), "grid.height")
        cell_size = float(getattr(grid, "cell_size"))
        cells = list(getattr(grid, "cells"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SceneInputError("A loader did not return a valid Grid") from exc

    if cell_size <= 0:
        raise SceneInputError("grid.cell_size must be greater than zero")
    expected = width * height
    if len(cells) != expected:
        raise SceneInputError(
            f"grid.cells must contain exactly {expected} cells; got {len(cells)}"
        )

    seen: set[tuple[int, int]] = set()
    for index, cell in enumerate(cells):
        x = getattr(cell, "x", None)
        y = getattr(cell, "y", None)
        if isinstance(x, bool) or not isinstance(x, int):
            raise SceneInputError(f"grid.cells[{index}].x must be an integer")
        if isinstance(y, bool) or not isinstance(y, int):
            raise SceneInputError(f"grid.cells[{index}].y must be an integer")
        if not (0 <= x < width and 0 <= y < height):
            raise SceneInputError(
                f"grid.cells[{index}] coordinate ({x}, {y}) is outside the grid"
            )
        if (x, y) in seen:
            raise SceneInputError(f"duplicate cell coordinate: ({x}, {y})")
        seen.add((x, y))
        expected_xy = (index % width, index // width)
        if (x, y) != expected_xy:
            raise SceneInputError(
                "grid.cells must use dense row-major order: "
                "grid.cells[y * width + x]"
            )
        if not hasattr(cell, "cell_type"):
            raise SceneInputError(f"grid.cells[{index}].cell_type is required")

    return grid


def load_map_grid(path: str | Path) -> Any:
    """Load and validate a JSON or CSV map through A's public loader."""

    map_path = Path(path)
    if not map_path.is_file():
        raise SceneInputError(f"map file does not exist: {map_path}")
    suffix = map_path.suffix.lower()
    try:
        if suffix == ".json":
            from map_import.map_loader_grid import load_grid
        elif suffix == ".csv":
            from map_import.csv_loader_grid import load_csv_grid as load_grid
        else:
            raise SceneInputError("D map input supports only .json and .csv")
        grid = load_grid(str(map_path))
    except SceneInputError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise SceneInputError(f"A map loader failed for {map_path}") from exc
    return validate_grid(grid)


def grid_to_snapshot_grid(grid: Any) -> dict[str, Any]:
    """Convert a validated Grid into the D snapshot grid shape."""

    validate_grid(grid)
    width = int(grid.width)
    height = int(grid.height)
    cell_type = [
        [
            _enum_storage_value(grid.cells[y * width + x].cell_type)
            for x in range(width)
        ]
        for y in range(height)
    ]
    return {
        "width": width,
        "height": height,
        "cell_size": float(grid.cell_size),
        "cell_type": cell_type,
    }


def grid_to_static_snapshot(
    grid: Any,
    *,
    run_id: str = "map_preview",
    scenario_id: str | None = None,
    schema_version: str = "0.1-draft",
) -> dict[str, Any]:
    """Create a map-only snapshot suitable for ``draw_snapshot``.

    This is a preview snapshot, not a simulation state.  It intentionally has
    no people, smoke field, events, or strategy decisions.
    """

    if not run_id or not str(run_id).strip():
        raise SceneInputError("run_id must not be empty")
    return {
        "schema_version": schema_version,
        "run_id": str(run_id),
        "scenario_id": scenario_id or str(run_id),
        "random_seed": None,
        "step": 0,
        "time_step": 0.5,
        "time_s": 0.0,
        "grid": grid_to_snapshot_grid(grid),
        "people": [],
        "exits": [],
        "fields": {"smoke_field": [], "risk_field": [], "congestion_field": []},
        "relations": [],
        "events": [],
        "strategy_state": {},
        "adapter_meta": {
            "source": "A map loader",
            "preview_only": True,
            "missing_fields_are_null": True,
        },
    }


@dataclass(frozen=True)
class PopulationConfigView:
    """D's read-only view of the C SceneConfig contract."""

    config: Any
    source_path: Path
    scene_name: str
    description: str
    total_persons: int
    profile_ratios: Mapping[str, float]
    relation_intensity: float
    random_seed: int | None
    has_person_output: bool
    has_relation_output: bool


def _load_module_from_path(module_path: Path) -> types.ModuleType:
    module_name = "_d_c_scene_config_" + str(abs(hash(module_path.resolve())))
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise SceneInputError(f"cannot import C scene module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_population_config(
    yaml_path: str | Path,
    *,
    c_module_path: str | Path | None = None,
) -> PopulationConfigView:
    """Call C's YAML loader and expose only its confirmed SceneConfig fields."""

    config_path = Path(yaml_path)
    if not config_path.is_file():
        raise SceneInputError(f"C YAML file does not exist: {config_path}")
    if c_module_path is None:
        try:
            from scene_config import SceneConfigGenerator  # type: ignore
        except ImportError as exc:
            raise SceneInputError(
                "C scene_config.py is not installed; pass c_module_path explicitly"
            ) from exc
    else:
        module_path = Path(c_module_path)
        if not module_path.is_file():
            raise SceneInputError(f"C scene module does not exist: {module_path}")
        try:
            module = _load_module_from_path(module_path)
            SceneConfigGenerator = module.SceneConfigGenerator
        except (AttributeError, ImportError, OSError) as exc:
            raise SceneInputError("C module lacks SceneConfigGenerator") from exc

    loader = getattr(SceneConfigGenerator, "load_config_from_yaml", None)
    if not callable(loader):
        raise SceneInputError("C SceneConfigGenerator lacks load_config_from_yaml")
    try:
        config = loader(str(config_path))
    except (OSError, TypeError, ValueError) as exc:
        raise SceneInputError("C YAML loader failed") from exc

    try:
        total_persons = _required_positive_int(
            getattr(config, "total_persons"), "SceneConfig.total_persons"
        )
        ratios = dict(getattr(config, "profile_ratios"))
        relation_intensity = float(getattr(config, "relation_intensity"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SceneInputError("C SceneConfig is missing required fields") from exc
    if not ratios or any(float(value) < 0 for value in ratios.values()):
        raise SceneInputError("SceneConfig.profile_ratios must be non-negative")
    if abs(sum(float(value) for value in ratios.values()) - 1.0) > 1e-6:
        raise SceneInputError("SceneConfig.profile_ratios must sum to 1.0")
    if not 0.0 <= relation_intensity <= 1.0:
        raise SceneInputError("SceneConfig.relation_intensity must be within [0, 1]")
    random_seed = getattr(config, "random_seed", None)
    if random_seed is not None and (
        isinstance(random_seed, bool) or not isinstance(random_seed, int)
    ):
        raise SceneInputError("SceneConfig.random_seed must be an integer or None")

    return PopulationConfigView(
        config=config,
        source_path=config_path,
        scene_name=str(getattr(config, "scene_name", "")),
        description=str(getattr(config, "description", "")),
        total_persons=total_persons,
        profile_ratios={key: float(value) for key, value in ratios.items()},
        relation_intensity=relation_intensity,
        random_seed=random_seed,
        has_person_output=hasattr(config, "persons"),
        has_relation_output=hasattr(config, "relations"),
    )
