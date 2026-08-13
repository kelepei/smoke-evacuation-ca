"""Week-4 D-side integrated runtime demo.

This entry point favors a runnable demonstration while keeping A/B/C ownership
intact.  It loads A's Grid through A loaders, reads C-style scene parameters
when provided, tries B's CA runtime first, and falls back to a D-only demo
simulation if the upstream runtime cannot be initialized.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import random
import sys
import warnings
from collections import deque
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.schema import Exit, Person, ScenarioConfig, SmokeSource
from experiments.csv_logger import CsvExperimentLogger
from visualization.adapters.config_adapter import DSceneConfigView, load_d_scene_config
from visualization.adapters.map_adapter import (
    default_map_path,
    exit_cells,
    load_grid_via_a,
    smoke_source_cells,
    walkable_cells,
)
from visualization.adapters.snapshot_adapter import DWeek4SnapshotAdapter


def _safe_run_id(label: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in label)
    cleaned = cleaned.strip("_") or "demo"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"d_week4_{cleaned}_{timestamp}"


def _cell_type(cell: Any) -> str:
    value = getattr(cell, "cell_type", None)
    return str(getattr(value, "value", value))


def _distance_to_exit_sort_key(point: tuple[int, int], exits: list[tuple[int, int]]) -> tuple[int, int, int]:
    if not exits:
        return (0, point[1], point[0])
    distance = min(abs(point[0] - ex) + abs(point[1] - ey) for ex, ey in exits)
    return (-distance, point[1], point[0])


def _profile_sequence(config: DSceneConfigView) -> list[str]:
    total = config.total_persons
    ratios = dict(config.profile_ratios) or {"default": 1.0}
    items = sorted(ratios.items(), key=lambda item: item[0])
    counts: dict[str, int] = {}
    assigned = 0
    for index, (profile, ratio) in enumerate(items):
        if index == len(items) - 1:
            count = total - assigned
        else:
            count = int(round(total * float(ratio)))
            assigned += count
        counts[profile] = max(0, count)
    sequence: list[str] = []
    for profile, count in counts.items():
        sequence.extend([profile] * count)
    return (sequence + ["default"] * total)[:total]


def _build_people(grid: Any, config: DSceneConfigView) -> list[Person]:
    exits = exit_cells(grid)
    candidates = sorted(
        walkable_cells(grid),
        key=lambda point: _distance_to_exit_sort_key(point, exits),
    )
    total = min(config.total_persons, len(candidates))
    profiles = _profile_sequence(config)
    random.Random(config.random_seed).shuffle(candidates)
    candidates = sorted(
        candidates[: max(total * 2, total)],
        key=lambda point: _distance_to_exit_sort_key(point, exits),
    )

    group_size = None
    if config.group_config:
        raw_size = (
            config.group_config.get("group_size")
            or config.group_config.get("default_group_size")
            or config.group_config.get("size")
        )
        try:
            group_size = max(1, int(raw_size))
        except (TypeError, ValueError):
            group_size = 4

    people: list[Person] = []
    for index, (x, y) in enumerate(candidates[:total], start=1):
        person = Person(id=index, x=int(x), y=int(y))
        setattr(person, "profile", profiles[index - 1] if index - 1 < len(profiles) else "default")
        setattr(person, "info_state", None)
        if group_size is not None:
            setattr(person, "group_id", f"g_{(index - 1) // group_size + 1:02d}")
        people.append(person)
    return people


def build_scene_from_upstream_inputs(
    *,
    map_path: Path,
    config: DSceneConfigView,
) -> tuple[ScenarioConfig, dict[str, Any]]:
    grid = load_grid_via_a(map_path)
    exits = [Exit(id=f"exit_{index:02d}") for index, _ in enumerate(exit_cells(grid), start=1)]
    smoke_sources = [
        SmokeSource(x=x, y=y, intensity=1.0)
        for x, y in smoke_source_cells(grid)
    ]
    people = _build_people(grid, config)
    scene = ScenarioConfig(
        scenario_id=config.scene_name,
        grid=grid,
        exits=exits,
        persons=people,
        relations=[],
        smoke_sources=smoke_sources,
    )
    scene.parameters = {
        "random_seed": config.random_seed,
        "scene_name": config.scene_name,
        "total_persons": config.total_persons,
        "profile_ratios": dict(config.profile_ratios),
        "relation_intensity": config.relation_intensity,
        "group_config": dict(config.group_config),
        "d_note": "D placed demo persons from C total_persons because C formal population output is not present.",
    }
    status = {
        "map_path": str(map_path),
        "map_loader": "A JSON/CSV loader",
        "png_loader_status": "map_import/map_loader_image.py exists but is empty/not callable",
        "config": config.to_dict(),
        "person_init": {
            "requested": config.total_persons,
            "created": len(people),
            "policy": "D demo placement on A walkable free/sign/guide_zone cells; pending C confirmation of official persons output",
        },
        "smoke_sources_from_a": len(smoke_sources),
    }
    return scene, status


def _b_simulation_factory(scene: ScenarioConfig) -> Callable[[], Any]:
    def create() -> Any:
        copied_scene = deepcopy(scene)
        try:
            from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
            from simulation.evac_simulation import EvacEngine

            return EvacEngineRuntimeAdapter(EvacEngine(copied_scene))
        except (ImportError, AttributeError):
            from simulation.evac_simulation import CaEvacSimulation

            return CaEvacSimulation(copied_scene)

    return create


def _grid_distances(grid: Any) -> list[list[float]]:
    exits = exit_cells(grid)
    width = int(grid.width)
    height = int(grid.height)
    dist = [[9999.0 for _ in range(width)] for _ in range(height)]
    queue: deque[tuple[int, int]] = deque()
    blocked = {"wall", "obstacle"}
    for x, y in exits:
        dist[y][x] = 0.0
        queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx = x + dx
            ny = y + dy
            if not (0 <= nx < width and 0 <= ny < height):
                continue
            cell = grid.cells[ny * width + nx]
            if _cell_type(cell) in blocked:
                continue
            if dist[ny][nx] > dist[y][x] + 1:
                dist[ny][nx] = dist[y][x] + 1
                queue.append((nx, ny))
    return dist


class FallbackDemoSimulation:
    """D-only runtime used only when B cannot be called."""

    def __init__(self, scene: ScenarioConfig) -> None:
        self.config = scene
        self.grid = scene.grid
        self.persons = {person.id: person for person in scene.persons}
        self._evacuated_status = {person.id: False for person in scene.persons}
        self.current_step = 0
        self.smoke_sim = SimpleNamespace(
            smoke_matrix=np.zeros((int(self.grid.height), int(self.grid.width)), dtype=float)
        )
        self._dist = _grid_distances(self.grid)
        self.d_use_fallback_smoke = True
        sources = smoke_source_cells(self.grid)
        self.d_fallback_smoke_source = sources[0] if sources else (1, 1)

    def init_simulation(self) -> None:
        self.current_step = 0

    def all_done(self) -> bool:
        return all(self._evacuated_status.values())

    def _update_smoke(self) -> None:
        sx, sy = self.d_fallback_smoke_source
        strength = min(10.0, 0.8 + self.current_step * 0.35)
        field = np.zeros((int(self.grid.height), int(self.grid.width)), dtype=float)
        for y in range(int(self.grid.height)):
            for x in range(int(self.grid.width)):
                if _cell_type(self.grid.cells[y * int(self.grid.width) + x]) == "wall":
                    continue
                field[y][x] = max(0.0, strength * math.exp(-math.hypot(x - sx, y - sy) / 5.0))
        self.smoke_sim.smoke_matrix = np.clip(field, 0.0, 10.0)

    def step(self) -> None:
        width = int(self.grid.width)
        height = int(self.grid.height)
        occupied = {
            (person.x, person.y)
            for pid, person in self.persons.items()
            if not self._evacuated_status[pid]
        }
        proposed: dict[int, tuple[int, int]] = {}
        for pid, person in self.persons.items():
            if self._evacuated_status[pid]:
                continue
            best = (person.x, person.y)
            best_score = self._dist[person.y][person.x]
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx = person.x + dx
                ny = person.y + dy
                if not (0 <= nx < width and 0 <= ny < height):
                    continue
                if _cell_type(self.grid.cells[ny * width + nx]) in {"wall", "obstacle"}:
                    continue
                if (nx, ny) in occupied and _cell_type(self.grid.cells[ny * width + nx]) != "exit":
                    continue
                score = self._dist[ny][nx]
                if score < best_score:
                    best = (nx, ny)
                    best_score = score
            proposed[pid] = best

        target_counts: dict[tuple[int, int], int] = {}
        for target in proposed.values():
            target_counts[target] = target_counts.get(target, 0) + 1
        for pid, target in proposed.items():
            if target_counts[target] > 1 and _cell_type(self.grid.cells[target[1] * width + target[0]]) != "exit":
                continue
            person = self.persons[pid]
            person.x, person.y = target
            if _cell_type(self.grid.cells[person.y * width + person.x]) == "exit":
                self._evacuated_status[pid] = True
                setattr(person, "evacuated", True)
                setattr(person, "actual_exit", "exit_01")

        self.current_step += 1
        self._update_smoke()


class DWeek4Runner:
    def __init__(
        self,
        *,
        scene: ScenarioConfig,
        output_root: Path,
        run_id: str,
        time_step_s: float,
        max_steps: int,
        config_used: dict[str, Any],
    ) -> None:
        self.scene = scene
        self.output_root = output_root
        self.base_run_id = run_id
        self.time_step_s = time_step_s
        self.max_steps = max_steps
        self.config_used = config_used
        self.current_run_id: str | None = None
        self.current_snapshot: dict[str, Any] | None = None
        self.simulation: Any | None = None
        self.adapter: DWeek4SnapshotAdapter | None = None
        self.logger: CsvExperimentLogger | None = None
        self.runtime_mode = "unknown"
        self.interface_notes: list[str] = []
        self.evacuation_times: dict[int, float] = {}
        self._reset_count = 0

    @property
    def initialized(self) -> bool:
        return self.simulation is not None

    @property
    def finished(self) -> bool:
        if self.simulation is None:
            return False
        return bool(self.simulation.all_done() or self.current_snapshot["step"] >= self.max_steps)

    @property
    def output_dir(self) -> Path:
        if self.current_run_id is None:
            return self.output_root / self.base_run_id
        return self.output_root / self.current_run_id

    def initialize(self) -> dict[str, Any]:
        return self._start_run(self.base_run_id)

    def _new_simulation(self) -> Any:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                simulation = _b_simulation_factory(self.scene)()
                simulation.init_simulation()
            self.runtime_mode = (
                f"B.{simulation.__class__.__module__}.{simulation.__class__.__name__}"
            )
            self.interface_notes.append("B step/init_simulation/all_done successfully called.")
        except Exception as exc:
            simulation = FallbackDemoSimulation(deepcopy(self.scene))
            simulation.init_simulation()
            self.runtime_mode = "D.fallback_demo"
            self.interface_notes.append(
                f"B runtime failed ({type(exc).__name__}: {exc}); using D fallback demo. Pending B confirmation."
            )
        if not smoke_source_cells(simulation.grid):
            simulation.d_use_fallback_smoke = True
            simulation.d_fallback_smoke_source = (1, 1)
            self.interface_notes.append("No upstream smoke source/output; D fallback smoke heatmap enabled. Pending B confirmation.")
        return simulation

    def _start_run(self, run_id: str) -> dict[str, Any]:
        self.close()
        self.current_run_id = run_id
        self.interface_notes = []
        self.evacuation_times = {}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        simulation = self._new_simulation()
        adapter = DWeek4SnapshotAdapter(
            run_id=run_id,
            time_step_s=self.time_step_s,
            random_seed=self.scene.parameters.get("random_seed"),
        )
        snapshot = adapter.capture(simulation)
        logger = CsvExperimentLogger(
            self.output_dir,
            run_id=run_id,
            scenario_id=str(snapshot["scenario_id"]),
            random_seed=snapshot.get("random_seed"),
            time_step_s=self.time_step_s,
        )
        logger.start()
        logger.record_snapshot(snapshot)
        self.simulation = simulation
        self.adapter = adapter
        self.logger = logger
        self.current_snapshot = snapshot
        self._write_config_used()
        self._track_evacuation(snapshot)
        return snapshot

    def step(self) -> dict[str, Any]:
        if self.simulation is None or self.adapter is None or self.logger is None:
            raise RuntimeError("runner is not initialized")
        if self.finished:
            return self.current_snapshot
        try:
            self.simulation.step()
            snapshot = self.adapter.capture(self.simulation)
            self.logger.record_snapshot(snapshot)
        except Exception as exc:
            if not str(self.runtime_mode).startswith("B."):
                raise
            snapshot = self._recover_with_fallback_after_b_step(exc)
        self.current_snapshot = snapshot
        self._track_evacuation(snapshot)
        return snapshot

    def run_until_finished(self) -> dict[str, Any]:
        if not self.initialized:
            self.initialize()
        while not self.finished:
            self.step()
        return self.current_snapshot

    def reset(self) -> dict[str, Any]:
        self._reset_count += 1
        return self._start_run(f"{self.base_run_id}_reset_{self._reset_count}")

    def close(self) -> None:
        if self.logger is not None:
            self.logger.close()
        self.logger = None
        self.adapter = None
        self.simulation = None

    def _track_evacuation(self, snapshot: dict[str, Any]) -> None:
        for person in snapshot["people"]:
            pid = int(person["person_id"])
            if person["evacuated"] and pid not in self.evacuation_times:
                self.evacuation_times[pid] = float(snapshot["time_s"])

    def _recover_with_fallback_after_b_step(self, exc: Exception) -> dict[str, Any]:
        assert self.current_snapshot is not None
        assert self.adapter is not None
        assert self.logger is not None
        previous = self.current_snapshot
        fallback = FallbackDemoSimulation(deepcopy(self.scene))
        fallback.init_simulation()
        fallback.current_step = int(previous["step"])
        for person_row in previous["people"]:
            pid = int(person_row["person_id"])
            person = fallback.persons.get(pid)
            if person is None:
                continue
            person.x = int(person_row["x"])
            person.y = int(person_row["y"])
            fallback._evacuated_status[pid] = bool(person_row["evacuated"])
            if bool(person_row["evacuated"]):
                setattr(person, "evacuated", True)
        self.runtime_mode = "D.fallback_after_b_step"
        self.interface_notes.append(
            f"B step/snapshot failed ({type(exc).__name__}: {exc}); switched to D fallback. Pending B confirmation."
        )
        self.simulation = fallback
        fallback.step()
        snapshot = self.adapter.capture(fallback)
        self.logger.record_snapshot(snapshot)
        return snapshot

    def _write_config_used(self) -> None:
        payload = {
            **self.config_used,
            "run_id": self.current_run_id,
            "runtime_mode": self.runtime_mode,
            "interface_notes": self.interface_notes,
            "time_step_s": self.time_step_s,
            "max_steps": self.max_steps,
        }
        (self.output_dir / "config_used.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def write_metrics(self) -> dict[str, Any]:
        if self.current_snapshot is None:
            raise RuntimeError("no snapshot available")
        snapshot = self.current_snapshot
        people = snapshot["people"]
        evacuated_count = sum(1 for person in people if person["evacuated"])
        smoke_field = snapshot.get("fields", {}).get("smoke_field", [])
        smoke_values = [value for row in smoke_field for value in row]
        metrics = {
            "total_steps": int(snapshot["step"]),
            "total_time_s": float(snapshot["time_s"]),
            "evacuated_count": evacuated_count,
            "remaining_count": len(people) - evacuated_count,
            "evacuation_rate": evacuated_count / len(people) if people else 0.0,
            "first_evac_time_s": min(self.evacuation_times.values()) if self.evacuation_times else "NA",
            "last_evac_time_s": max(self.evacuation_times.values()) if self.evacuation_times else "NA",
            "max_smoke": max(smoke_values) if smoke_values else "NA",
            "avg_smoke": (sum(smoke_values) / len(smoke_values)) if smoke_values else "NA",
            "runtime_mode": self.runtime_mode,
        }
        (self.output_dir / "metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with (self.output_dir / "metrics_summary.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(metrics.keys()))
            writer.writeheader()
            writer.writerow(metrics)
        self._write_config_used()
        return metrics


def save_snapshot_png(snapshot: dict[str, Any], path: Path) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from matplotlib.patches import Patch

    codes = {
        "free": 0,
        "wall": 1,
        "obstacle": 2,
        "exit": 3,
        "smoke_source": 4,
        "sign": 5,
        "guide_zone": 6,
    }
    colors = ListedColormap(["#f7f7f7", "#30343b", "#777c84", "#2ca25f", "#bb4d00", "#ffcc33", "#6baed6"])
    grid = snapshot["grid"]
    cell_values = np.array(
        [[codes.get(str(value).lower(), 0) for value in row] for row in grid["cell_type"]],
        dtype=float,
    )
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.imshow(cell_values, origin="upper", interpolation="nearest", cmap=colors, vmin=0, vmax=len(codes) - 1)
    smoke = snapshot.get("fields", {}).get("smoke_field", [])
    if smoke:
        ax.imshow(
            np.ma.masked_less_equal(np.asarray(smoke, dtype=float), 0.0),
            origin="upper",
            interpolation="nearest",
            cmap="Reds",
            vmin=0,
            vmax=10,
            alpha=0.45,
        )
    active = [person for person in snapshot["people"] if not person["evacuated"]]
    evacuated = [person for person in snapshot["people"] if person["evacuated"]]
    if active:
        ax.scatter([p["x"] for p in active], [p["y"] for p in active], s=54, c="#2166ac", edgecolors="white", linewidths=0.8)
    if evacuated:
        ax.scatter([p["x"] for p in evacuated], [p["y"] for p in evacuated], s=58, c="#2ca25f", marker="x", linewidths=1.8)
    ax.set_title(
        f"D week-4 integrated demo | step={snapshot['step']} | evacuated={len(evacuated)}/{len(snapshot['people'])}"
    )
    ax.set_xlim(-0.5, int(grid["width"]) - 0.5)
    ax.set_ylim(int(grid["height"]) - 0.5, -0.5)
    ax.set_aspect("equal")
    ax.grid(color="#dddddd", linewidth=0.25)
    ax.legend(
        handles=[
            Patch(facecolor="#30343b", label="wall"),
            Patch(facecolor="#777c84", label="obstacle"),
            Patch(facecolor="#2ca25f", label="exit"),
            Patch(facecolor="#ef3b2c", alpha=0.45, label="smoke"),
        ],
        loc="upper left",
        bbox_to_anchor=(1.01, 1.0),
        fontsize=8,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def create_runner(args: argparse.Namespace) -> DWeek4Runner:
    map_path = args.map or default_map_path(REPO_ROOT)
    if args.population is not None:
        from experiments.integrated_runner import build_integrated_scenario

        integrated = build_integrated_scenario(
            map_path=map_path,
            population_path=args.population,
            yaml_path=args.config,
            c_module_path=args.c_module,
            random_seed=args.random_seed,
            source_id_base=args.source_id_base,
        )
        scene = integrated.config
        status = {
            "map_path": str(integrated.map_path),
            "map_loader": "A JSON/CSV/PNG loader via visualization.scene_input_adapter",
            "population_path": str(integrated.population_path),
            "yaml_path": None if integrated.yaml_path is None else str(integrated.yaml_path),
            "config": {
                "status": "C YAML loaded" if integrated.yaml_path is not None else "not supplied",
                "random_seed": scene.parameters.get("random_seed"),
            },
            "person_init": {
                "created": integrated.person_count,
                "policy": integrated.placement_mode,
            },
            "relation_count": integrated.relation_count,
            "smoke_sources_from_a": integrated.smoke_source_count,
            "runtime_contract": "A Grid + C positioned persons/relations + B EvacEngine",
        }
        run_id = args.run_id or _safe_run_id(str(scene.scenario_id))
        return DWeek4Runner(
            scene=scene,
            output_root=args.output_root,
            run_id=run_id,
            time_step_s=args.time_step,
            max_steps=args.max_steps,
            config_used=status,
        )

    config = load_d_scene_config(args.config)
    if args.random_seed is not None:
        config = DSceneConfigView(
            **{**config.to_dict(), "random_seed": args.random_seed}
        )
    if args.persons is not None:
        config = DSceneConfigView(
            **{**config.to_dict(), "total_persons": args.persons}
        )
    scene, status = build_scene_from_upstream_inputs(map_path=Path(map_path), config=config)
    run_id = args.run_id or _safe_run_id(config.scene_name)
    return DWeek4Runner(
        scene=scene,
        output_root=args.output_root,
        run_id=run_id,
        time_step_s=args.time_step,
        max_steps=args.max_steps,
        config_used=status,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run D week-4 integrated visualization demo.")
    parser.add_argument("--map", type=Path, help="A JSON/CSV/PNG map; defaults to maps/examples/simple_room.json")
    parser.add_argument("--config", type=Path, help="C YAML config_template/SceneConfig-compatible file")
    parser.add_argument("--population", type=Path, help="C persons/relations JSON with A-assigned x/y positions")
    parser.add_argument("--c-module", type=Path, default=Path("control") / "scene_config.py")
    parser.add_argument("--source-id-base", choices=(0, 1), type=int, default=0)
    parser.add_argument("--output-root", type=Path, default=Path("outputs") / "experiments")
    parser.add_argument("--run-id", help="safe run id; defaults to d_week4_<scene>_<timestamp>")
    parser.add_argument("--time-step", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int, default=80)
    parser.add_argument("--interval-ms", type=int, default=300)
    parser.add_argument("--random-seed", type=int)
    parser.add_argument("--persons", type=int, help="D demo override for C total_persons")
    parser.add_argument("--gui", action="store_true", help="open Matplotlib controls: Start/Pause/Single step/Reset")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.gui:
        import matplotlib

        matplotlib.use("Agg")
        warnings.filterwarnings("ignore", message="Glyph .* missing from font")
        warnings.filterwarnings("ignore", message="FigureCanvasAgg is non-interactive.*")

    runner = create_runner(args)
    try:
        initial = runner.initialize()
        if args.gui:
            from visualization.visualizer import MatplotlibSimulationViewer

            viewer = MatplotlibSimulationViewer(runner, interval_ms=args.interval_ms)
            viewer.show()
        else:
            final = runner.run_until_finished()
            save_snapshot_png(final, runner.output_dir / "final_frame.png")
            metrics = runner.write_metrics()
            print(f"run_id={final['run_id']}")
            print(f"runtime_mode={runner.runtime_mode}")
            print(f"output_dir={runner.output_dir}")
            print(f"total_steps={metrics['total_steps']} evacuated={metrics['evacuated_count']}/{len(final['people'])}")
            print("files=config_used.json, people_log.csv, event_log.csv, metrics.json, metrics_summary.csv, final_frame.png")
    finally:
        runner.close()


if __name__ == "__main__":
    main()
