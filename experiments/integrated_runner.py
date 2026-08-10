"""D's guarded A + C input to B runtime entry point.

This module deliberately assembles only already-produced upstream data.  It
does not generate people or relations (C), and it does not allocate initial
positions (A).  A population record therefore must already contain a valid,
unique position before D will start B's cellular automaton.
"""

from __future__ import annotations

import argparse
import copy
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from core.schema import Exit, Person, ScenarioConfig, SmokeSource
from experiments.runner import SimulationRunner
from visualization.scene_input_adapter import (
    PopulationOutputView,
    SceneInputError,
    load_map_grid,
    load_population_output,
)


class IntegrationInputError(ValueError):
    """Raised when A/C input is not ready for B's existing runtime."""


def _cell_type(cell: Any) -> str:
    value = getattr(cell, "cell_type", None)
    return str(getattr(value, "value", value))


def _walkable_initial_cells(grid: Any) -> set[tuple[int, int]]:
    return {
        (int(cell.x), int(cell.y))
        for cell in grid.cells
        if _cell_type(cell) in {"free", "sign", "guide_zone"}
    }


def _connected_components(points: Iterable[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    """Return deterministic components for A-provided static exit cells."""

    remaining = set(points)
    components: list[list[tuple[int, int]]] = []
    while remaining:
        start = min(remaining, key=lambda point: (point[1], point[0]))
        remaining.remove(start)
        component = [start]
        queue: deque[tuple[int, int]] = deque([start])
        while queue:
            x, y = queue.popleft()
            for neighbour in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    component.append(neighbour)
                    queue.append(neighbour)
        components.append(sorted(component, key=lambda point: (point[1], point[0])))
    return components


def _int_coordinate(record: Mapping[str, Any], field: str, person_id: int) -> int:
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise IntegrationInputError(
            f"person {person_id} is missing A-assigned integer {field}"
        )
    return int(value)


def _make_person(record: Mapping[str, Any], *, grid: Any, occupied: set[tuple[int, int]]) -> Person:
    person_id = record.get("person_id")
    if isinstance(person_id, bool) or not isinstance(person_id, int) or person_id <= 0:
        raise IntegrationInputError("C population person_id must be a positive integer")
    person_id = int(person_id)
    x = _int_coordinate(record, "x", person_id)
    y = _int_coordinate(record, "y", person_id)
    position = (x, y)
    if not (0 <= x < int(grid.width) and 0 <= y < int(grid.height)):
        raise IntegrationInputError(
            f"person {person_id} A-assigned position {position} is outside the map"
        )
    if position not in _walkable_initial_cells(grid):
        raise IntegrationInputError(
            f"person {person_id} A-assigned position {position} is not a walkable initial cell"
        )
    if position in occupied:
        raise IntegrationInputError(
            f"person {person_id} overlaps another A-assigned initial position {position}"
        )
    occupied.add(position)

    # Current shared Person accepts only id/x/y in its dataclass constructor.
    # Preserve C's supplied attributes on the runtime instance without
    # inventing a value for any absent upstream field.
    person = Person(id=person_id, x=x, y=y)
    for source, target in (
        ("profile", "profile"),
        ("speed", "speed"),
        ("risk_sensitivity", "risk_sensitivity"),
        ("familiarity", "familiarity"),
        ("herding_tendency", "herding_tendency"),
        ("group_id", "group_id"),
        ("info_state", "info_state"),
    ):
        if source in record:
            setattr(person, target, record[source])
    return person


def build_runtime_scene(
    *,
    map_path: str | Path,
    population_path: str | Path,
    scenario_id: str | None = None,
    random_seed: int | None = None,
    source_id_base: int = 0,
) -> ScenarioConfig:
    """Build B's current ``ScenarioConfig`` from validated A/C artifacts.

    ``population_path`` is expected to be C's persons/relations output after
    A has attached initial ``x``/``y`` positions.  D never assigns those
    positions; invalid or incomplete input raises ``IntegrationInputError``.
    """

    try:
        grid = load_map_grid(map_path)
        population = load_population_output(
            population_path, source_id_base=source_id_base
        )
    except SceneInputError as exc:
        raise IntegrationInputError(str(exc)) from exc
    return _build_scene_from_views(
        grid=grid,
        population=population,
        scenario_id=scenario_id,
        random_seed=random_seed,
    )


def _build_scene_from_views(
    *,
    grid: Any,
    population: PopulationOutputView,
    scenario_id: str | None,
    random_seed: int | None,
) -> ScenarioConfig:
    exit_cells = {
        (int(cell.x), int(cell.y)) for cell in grid.cells if _cell_type(cell) == "exit"
    }
    if not exit_cells:
        raise IntegrationInputError("A map must contain at least one exit cell")
    smoke_sources = [
        SmokeSource(x=int(cell.x), y=int(cell.y))
        for cell in grid.cells
        if _cell_type(cell) == "smoke_source"
    ]
    exits = [
        Exit(id=f"a_exit_{index:02d}")
        for index, _component in enumerate(_connected_components(exit_cells), start=1)
    ]

    occupied: set[tuple[int, int]] = set()
    persons = [
        _make_person(record, grid=grid, occupied=occupied)
        for record in population.persons
    ]
    if not persons:
        raise IntegrationInputError("C population output must contain at least one person")

    # ``PopulationOutputView`` has already checked relation endpoints against
    # its canonical IDs.  Preserve its records read-only so D never redefines
    # C relation semantics.
    relation_records = [dict(relation) for relation in population.relations]
    actual_scenario_id = scenario_id or Path(map_path_label(population)).stem
    parameters: dict[str, Any] = {
        "random_seed": random_seed,
        "d_input_contract": "A-assigned positions + C persons/relations",
        "d_position_policy": "reject_missing_invalid_or_overlapping",
    }
    scene = ScenarioConfig(
        scenario_id=actual_scenario_id,
        grid=grid,
        exits=exits,
        persons=persons,
        relations=relation_records,
        smoke_sources=smoke_sources,
    )
    # The current shared dataclass exposes no ``parameters`` constructor
    # field.  Store D provenance only on this fresh runtime scene object;
    # neither A/B/C source nor any upstream artifact is changed.
    scene.parameters = parameters
    return scene


def map_path_label(population: PopulationOutputView) -> str:
    """Return a stable fallback label without claiming C owns the map path."""

    metadata = population.metadata
    candidate = metadata.get("scenario_id") or metadata.get("scene_name")
    return str(candidate or "a_c_input")


def integrated_simulation_factory(scene: ScenarioConfig):
    """Return fresh B simulations for runner initialization/reset cycles."""

    def create_simulation() -> Any:
        from simulation.evac_simulation import CaEvacSimulation

        return CaEvacSimulation(copy.deepcopy(scene))

    random_seed = scene.parameters.get("random_seed")
    setattr(create_simulation, "_d_random_seed", random_seed)
    return create_simulation


def create_integrated_runner(
    *,
    map_path: str | Path,
    population_path: str | Path,
    output_root: str | Path,
    scenario_id: str | None = None,
    random_seed: int | None = None,
    time_step_s: float = 0.5,
    max_steps: int = 500,
    source_id_base: int = 0,
) -> SimulationRunner:
    """Create a D runner without starting B or fabricating upstream input."""

    scene = build_runtime_scene(
        map_path=map_path,
        population_path=population_path,
        scenario_id=scenario_id,
        random_seed=random_seed,
        source_id_base=source_id_base,
    )
    return SimulationRunner(
        integrated_simulation_factory(scene),
        output_root=output_root,
        run_id=(
            f"d_week4_{scene.scenario_id}_"
            f"{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        ),
        time_step_s=time_step_s,
        max_steps=max_steps,
        random_seed=random_seed,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run B only after A positions and C population data are supplied."
    )
    parser.add_argument("--map", required=True, type=Path, help="A JSON/CSV map")
    parser.add_argument(
        "--population",
        required=True,
        type=Path,
        help="C persons/relations JSON with A-assigned x/y positions",
    )
    parser.add_argument("--output-root", type=Path, default=Path("outputs") / "d_week4")
    parser.add_argument("--scenario-id")
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--time-step", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--source-id-base", choices=(0, 1), type=int, default=0)
    parser.add_argument("--headless", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    runner = create_integrated_runner(
        map_path=args.map,
        population_path=args.population,
        output_root=args.output_root,
        scenario_id=args.scenario_id,
        random_seed=args.random_seed,
        time_step_s=args.time_step,
        max_steps=args.max_steps,
        source_id_base=args.source_id_base,
    )
    try:
        initial = runner.initialize()
        print(f"initialized: {initial['run_id']} step={initial['step']}")
        if args.headless:
            final = runner.run_until_finished()
            print(f"finished: {final['run_id']} step={final['step']}")
        else:
            from visualization.visualizer import MatplotlibSimulationViewer

            MatplotlibSimulationViewer(runner).show()
    finally:
        runner.close()


if __name__ == "__main__":
    main()
