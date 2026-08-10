"""D-side initial runtime for real A map, B CA, and C population inputs.

This module owns only the integration boundary.  It never edits A/B/C source
files: it reads A's public map loaders, C's exported JSON/YAML, and B's
existing CA class, then records the normal D snapshots and CSV logs.

Supported inputs are deliberately contract-based rather than hard-coded to a
demo scenario:

* Any dense row-major JSON/CSV map accepted by A's loader;
* Any C ``output_people.json`` with ``persons`` and ``relations``;
* Optional C YAML settings, used for reproducibility validation.

Initial positions are deliberately not generated here: A assigns positions
according to map-room semantics before this D-side runtime is started.
"""

from __future__ import annotations

import argparse
import random
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from core.schema import CellType, Exit, Person, Relation, ScenarioConfig, SmokeSource
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from experiments.runner import SimulationRunner
from visualization.scene_input_adapter import (
    PopulationConfigView,
    PopulationOutputView,
    SceneInputError,
    load_map_grid,
    load_population_config,
    load_population_output,
)


class IntegratedRuntimeError(ValueError):
    """Raised when real A/B/C inputs cannot form one safe runtime scenario."""


PASSABLE_CELL_TYPES = {"free", "sign", "guide_zone"}


def _cell_type_value(cell: Any) -> str:
    value = getattr(cell, "cell_type", None)
    return str(getattr(value, "value", value)).strip().lower()


def _as_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegratedRuntimeError("C person coordinates must be integers when present")
    return int(value)


def _require_a_assigned_positions(
    people: list[Mapping[str, Any]], grid: Any
) -> list[tuple[int, int]]:
    """Validate positions assigned by A; D must never invent them."""

    width = int(grid.width)
    height = int(grid.height)
    positions: list[tuple[int, int]] = []
    occupied: set[tuple[int, int]] = set()
    for person in people:
        person_id = person.get("person_id")
        x = _as_optional_int(person.get("x"))
        y = _as_optional_int(person.get("y"))
        if x is None or y is None:
            raise IntegratedRuntimeError(
                f"person {person_id!r} has no A-assigned initial x/y position"
            )
        if not (0 <= x < width and 0 <= y < height):
            raise IntegratedRuntimeError(
                f"person {person_id!r} A-assigned position ({x}, {y}) is outside the map"
            )
        if (x, y) in occupied:
            raise IntegratedRuntimeError(
                f"person {person_id!r} overlaps another A-assigned position ({x}, {y})"
            )
        cell = grid.cells[y * width + x]
        if _cell_type_value(cell) not in PASSABLE_CELL_TYPES:
            raise IntegratedRuntimeError(
                f"person {person_id!r} A-assigned position ({x}, {y}) is not a passable initial cell"
            )
        occupied.add((x, y))
        positions.append((x, y))
    return positions


def _copy_c_attributes(target: Person, source: Mapping[str, Any]) -> None:
    """Attach C attributes without requiring changes to the shared schema."""

    for field in (
        "profile",
        "speed",
        "risk_sensitivity",
        "familiarity",
        "herding_tendency",
        "group_id",
        "info_state",
        "dose",
    ):
        if field in source:
            setattr(target, field, source[field])
    target.target_exit_id = source.get("target_exit") or None
    target.evacuated = bool(source.get("evacuated", False))
    target.source_person_id = source["source_person_id"]


@dataclass(frozen=True)
class IntegratedScenario:
    """D's assembled read-only view of a runnable A+B+C scenario."""

    config: ScenarioConfig
    map_path: Path
    population_path: Path
    yaml_path: Path | None
    placement_mode: str
    source_id_base: int
    person_count: int
    relation_count: int
    smoke_source_count: int


def build_integrated_scenario(
    *,
    map_path: str | Path,
    population_path: str | Path,
    yaml_path: str | Path | None = None,
    c_module_path: str | Path = Path("control") / "scene_config.py",
    scenario_id: str | None = None,
    random_seed: int | None = None,
    source_id_base: int = 0,
) -> IntegratedScenario:
    """Read A/C files and assemble B's existing ``ScenarioConfig`` input.

    ``population_path`` must contain the C persons/relations output after A
    has assigned valid, distinct initial ``x``/``y`` locations.  Missing or
    invalid positions stop the run; this module does not choose locations.
    """

    try:
        grid = load_map_grid(map_path)
        population = load_population_output(
            population_path, source_id_base=source_id_base
        )
    except SceneInputError as exc:
        raise IntegratedRuntimeError(str(exc)) from exc

    config_view: PopulationConfigView | None = None
    if yaml_path is not None:
        try:
            config_view = load_population_config(
                yaml_path, c_module_path=c_module_path
            )
        except SceneInputError as exc:
            raise IntegratedRuntimeError(str(exc)) from exc
        if config_view.total_persons != len(population.persons):
            raise IntegratedRuntimeError(
                "C YAML total_persons does not match output_people.json persons"
            )

    effective_seed = random_seed
    if effective_seed is None and config_view is not None:
        effective_seed = config_view.random_seed

    if not population.persons:
        raise IntegratedRuntimeError("C population output contains no persons")
    positions = _require_a_assigned_positions(population.persons, grid)
    placement_mode = "A-assigned positions preserved by D"

    persons: list[Person] = []
    for source, (x, y) in zip(population.persons, positions, strict=True):
        person = Person(id=int(source["person_id"]), x=x, y=y)
        _copy_c_attributes(person, source)
        persons.append(person)

    relations: list[Relation] = []
    for source in population.relations:
        relation = Relation(
            person_a_id=int(source["person_a_id"]),
            person_b_id=int(source["person_b_id"]),
        )
        for field in (
            "relation_type",
            "strength",
            "trust",
            "wait_probability",
            "follow_probability",
        ):
            if field in source:
                setattr(relation, field, source[field])
        relation.source_person_a_id = source["source_person_a_id"]
        relation.source_person_b_id = source["source_person_b_id"]
        relations.append(relation)

    exits = [
        Exit(id=f"exit_{index + 1}")
        for index, cell in enumerate(grid.cells)
        if _cell_type_value(cell) == CellType.EXIT.value
    ]
    smoke_sources = [
        SmokeSource(x=int(cell.x), y=int(cell.y), intensity=1.0)
        for cell in grid.cells
        if _cell_type_value(cell) == CellType.SMOKE_SOURCE.value
    ]
    if not exits:
        raise IntegratedRuntimeError("map must contain at least one cell with type=exit")

    resolved_scenario_id = scenario_id or (
        config_view.scene_name if config_view is not None else Path(map_path).stem
    )
    runtime_config = ScenarioConfig(
        scenario_id=str(resolved_scenario_id),
        grid=grid,
        exits=exits,
        persons=persons,
        relations=relations,
        smoke_sources=smoke_sources,
    )
    # ``parameters`` is not yet a constructor field in the shared schema.
    # Adding an instance attribute here preserves A/B/C code unchanged.
    runtime_config.parameters = {  # type: ignore[attr-defined]
        "random_seed": effective_seed,
        "d_input_sources": {
            "map": str(Path(map_path)),
            "population": str(Path(population_path)),
            "yaml": None if yaml_path is None else str(Path(yaml_path)),
        },
        "d_placement_mode": placement_mode,
    }

    return IntegratedScenario(
        config=runtime_config,
        map_path=Path(map_path),
        population_path=Path(population_path),
        yaml_path=None if yaml_path is None else Path(yaml_path),
        placement_mode=placement_mode,
        source_id_base=source_id_base,
        person_count=len(persons),
        relation_count=len(relations),
        smoke_source_count=len(smoke_sources),
    )


def integrated_simulation_factory(
    scenario: IntegratedScenario,
) -> Callable[[], EvacEngineRuntimeAdapter]:
    """Return a reset-safe D adapter around B's current public runtime."""

    def create() -> EvacEngineRuntimeAdapter:
        from simulation.evac_simulation import EvacEngine

        seed = scenario.config.parameters.get("random_seed")  # type: ignore[attr-defined]
        if seed is not None:
            random.seed(seed)
        wrapped = EvacEngineRuntimeAdapter(
            EvacEngine(scenario.config),
            adapter_meta={
                "map_path": str(scenario.map_path),
                "population_path": str(scenario.population_path),
                "yaml_path": None
                if scenario.yaml_path is None
                else str(scenario.yaml_path),
                "placement_mode": scenario.placement_mode,
                "person_count": scenario.person_count,
                "relation_count": scenario.relation_count,
                "smoke_source_count": scenario.smoke_source_count,
            },
        )
        return wrapped

    setattr(
        create,
        "_d_random_seed",
        scenario.config.parameters.get("random_seed"),  # type: ignore[attr-defined]
    )
    return create


def create_integrated_runner(
    *,
    map_path: str | Path,
    population_path: str | Path,
    output_root: str | Path,
    yaml_path: str | Path | None = None,
    c_module_path: str | Path = Path("control") / "scene_config.py",
    run_id: str = "d_integrated_run",
    random_seed: int | None = None,
    max_steps: int = 500,
) -> SimulationRunner:
    scenario = build_integrated_scenario(
        map_path=map_path,
        population_path=population_path,
        yaml_path=yaml_path,
        c_module_path=c_module_path,
        random_seed=random_seed,
    )
    return SimulationRunner(
        integrated_simulation_factory(scenario),
        output_root=output_root,
        run_id=run_id,
        time_step_s=0.5,
        max_steps=max_steps,
        random_seed=scenario.config.parameters.get("random_seed"),  # type: ignore[attr-defined]
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a D-integrated A-map + B-CA + C-population scenario."
    )
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--yaml", type=Path)
    parser.add_argument("--c-module", type=Path, default=Path("control") / "scene_config.py")
    parser.add_argument("--output-root", type=Path, default=Path("outputs") / "integrated")
    parser.add_argument("--run-id", default="d_integrated_run")
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.headless:
        import matplotlib

        matplotlib.use("Agg")
        warnings.filterwarnings("ignore", message="Glyph .* missing from font")
        warnings.filterwarnings("ignore", message="FigureCanvasAgg is non-interactive.*")
    runner = create_integrated_runner(
        map_path=args.map,
        population_path=args.population,
        yaml_path=args.yaml,
        c_module_path=args.c_module,
        output_root=args.output_root,
        run_id=args.run_id,
        random_seed=args.random_seed,
        max_steps=args.max_steps,
    )
    try:
        runner.initialize()
        if args.headless:
            snapshot = runner.run_until_finished()
            print(
                f"integrated run complete: step={snapshot['step']} "
                f"output={runner.output_root / snapshot['run_id']}"
            )
        else:
            from visualization.visualizer import MatplotlibSimulationViewer

            MatplotlibSimulationViewer(runner).show()
    finally:
        runner.close()


if __name__ == "__main__":
    main()
