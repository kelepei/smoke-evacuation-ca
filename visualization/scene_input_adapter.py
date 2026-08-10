"""D-side adapters for A map files and C population configuration.

The adapters deliberately keep ownership with the upstream modules:

* A remains responsible for parsing JSON/CSV/PNG into ``core.grid.Grid``.
* C remains responsible for parsing YAML into ``SceneConfig``.
* D validates the contracts it consumes and converts only the data needed by
  the visualizer.  It does not create people, relations, smoke sources, or
  simulation decisions that the upstream modules did not provide.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
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
    """Load and validate a JSON, CSV, or PNG map through A's public loader."""

    map_path = Path(path)
    if not map_path.is_file():
        raise SceneInputError(f"map file does not exist: {map_path}")
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
            return validate_grid(grid)
        else:
            raise SceneInputError("D map input supports only .json, .csv, and .png")
        grid = load_grid(str(map_path))
    except SceneInputError:
        raise
    except ValueError as exc:
        # Preserve A's validation message so callers can see whether the
        # input was sparse, malformed, or otherwise invalid.
        raise SceneInputError(str(exc)) from exc
    except (OSError, KeyError, TypeError) as exc:
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


@dataclass(frozen=True)
class PopulationOutputView:
    """D's validated view of C's optional people/relation JSON output."""

    source_path: Path
    source_id_base: int
    persons: tuple[dict[str, Any], ...]
    relations: tuple[dict[str, Any], ...]
    metadata: Mapping[str, Any]


def _source_person_id(value: Any, field: str, source_id_base: int) -> tuple[int, int]:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SceneInputError(f"{field} must be an integer")
    if source_id_base == 0 and value < 0:
        raise SceneInputError(f"{field} must be a non-negative integer")
    if source_id_base == 1 and value <= 0:
        raise SceneInputError(f"{field} must be a positive integer")
    return int(value), int(value) + (1 if source_id_base == 0 else 0)


def _relation_endpoint(
    item: Mapping[str, Any], canonical: str, alias: str, source_id_base: int
) -> tuple[int, int]:
    return _source_person_id(item.get(canonical, item.get(alias)), canonical, source_id_base)


def load_population_output(
    path: str | Path, *, source_id_base: int = 0
) -> PopulationOutputView:
    """Load C's output_people.json and map source IDs to D's positive IDs.

    C's current generator emits zero-based IDs.  D maps them to 1-based IDs
    while preserving source IDs for traceability.  A future one-based source
    can pass ``source_id_base=1`` explicitly.
    """

    if source_id_base not in (0, 1):
        raise SceneInputError("source_id_base must be 0 or 1")
    output_path = Path(path)
    if not output_path.is_file():
        raise SceneInputError(f"C population output does not exist: {output_path}")
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SceneInputError("C population output is not valid UTF-8 JSON") from exc
    if not isinstance(payload, Mapping):
        raise SceneInputError("C population output must be a JSON object")
    raw_persons = payload.get("persons")
    raw_relations = payload.get("relations", [])
    if not isinstance(raw_persons, list) or not isinstance(raw_relations, list):
        raise SceneInputError("C output must contain persons[] and relations[]")

    persons: list[dict[str, Any]] = []
    person_ids: set[int] = set()
    for index, raw in enumerate(raw_persons):
        if not isinstance(raw, Mapping):
            raise SceneInputError(f"persons[{index}] must be an object")
        source_id, person_id = _source_person_id(
            raw.get("person_id", raw.get("id")), "person_id", source_id_base
        )
        if person_id in person_ids:
            raise SceneInputError(f"duplicate person_id: {person_id}")
        person_ids.add(person_id)
        item = dict(raw)
        item.pop("id", None)
        item["source_person_id"] = source_id
        item["person_id"] = person_id
        persons.append(item)

    relations: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_relations):
        if not isinstance(raw, Mapping):
            raise SceneInputError(f"relations[{index}] must be an object")
        item = dict(raw)
        source_a, person_a = _relation_endpoint(item, "person_a_id", "from", source_id_base)
        source_b, person_b = _relation_endpoint(item, "person_b_id", "to", source_id_base)
        if person_a not in person_ids or person_b not in person_ids:
            raise SceneInputError(f"relations[{index}] references an unknown person_id")
        for field in ("strength", "trust", "wait_probability", "follow_probability"):
            if field in item and item[field] is not None:
                value = float(item[field])
                if not 0.0 <= value <= 1.0:
                    raise SceneInputError(f"relations[{index}].{field} must be within [0, 1]")
                item[field] = value
        item.pop("from", None)
        item.pop("to", None)
        item["source_person_a_id"] = source_a
        item["source_person_b_id"] = source_b
        item["person_a_id"] = person_a
        item["person_b_id"] = person_b
        relations.append(item)

    metadata = payload.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise SceneInputError("metadata must be an object when present")
    return PopulationOutputView(
        source_path=output_path,
        source_id_base=source_id_base,
        persons=tuple(persons),
        relations=tuple(relations),
        metadata=dict(metadata),
    )


def _load_module_from_path(module_path: Path) -> types.ModuleType:
    module_name = "_d_c_scene_config_" + str(abs(hash(module_path.resolve())))
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise SceneInputError(f"cannot import C scene module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    previous_cwd = Path.cwd()
    try:
        os.chdir(module_path.parent)
        with contextlib.redirect_stdout(io.StringIO()):
            spec.loader.exec_module(module)
    finally:
        os.chdir(previous_cwd)
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
