"""D-side runner for the current B cellular-automaton mock.

This module owns experiment control and logging only.  It does not modify the
movement, smoke, risk, map, or social-relation logic supplied by A/B/C.
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from experiments.csv_logger import CsvExperimentLogger
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


SimulationFactory = Callable[[], Any]


def default_simulation_factory(random_seed: int | None = 42) -> SimulationFactory:
    """Return a factory for B's current safer mock entry point."""

    def create_simulation() -> Any:
        if random_seed is not None:
            random.seed(random_seed)
            np.random.seed(random_seed)

        from scenarios.mock_data import build_base_scene
        # B 最新版本将可运行仿真实现放在 simulation.evac_simulation；
        # ca_model 仅保留 Grid/简单模型接口。D 只适配导入路径，不修改 B。
        from simulation.evac_simulation import CaEvacSimulation

        return CaEvacSimulation(build_base_scene())

    setattr(create_simulation, "_d_random_seed", random_seed)
    return create_simulation


def _default_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    return f"d_week3_{timestamp}"


def _validate_run_id(run_id: str) -> str:
    if (
        not run_id
        or Path(run_id).name != run_id
        or run_id in {".", ".."}
        or "/" in run_id
        or "\\" in run_id
    ):
        raise ValueError("run_id must be a single safe path component")
    return run_id


class SimulationRunner:
    """Control one simulation instance and write normalized D logs."""

    def __init__(
        self,
        simulation_factory: SimulationFactory,
        *,
        output_root: str | Path,
        run_id: str | None = None,
        time_step_s: float = 0.5,
        max_steps: int = 500,
        schema_version: str = "0.1-draft",
        random_seed: int | None = None,
    ) -> None:
        try:
            normalized_time_step = float(time_step_s)
        except (TypeError, ValueError) as exc:
            raise ValueError("time_step_s must be numeric") from exc
        if (
            isinstance(time_step_s, bool)
            or not math.isfinite(normalized_time_step)
            or normalized_time_step <= 0
        ):
            raise ValueError("time_step_s must be greater than zero")
        if isinstance(max_steps, bool) or not isinstance(max_steps, int):
            raise ValueError("max_steps must be a positive integer")
        if max_steps <= 0:
            raise ValueError("max_steps must be greater than zero")
        effective_random_seed = (
            random_seed
            if random_seed is not None
            else getattr(simulation_factory, "_d_random_seed", None)
        )
        if effective_random_seed is not None and (
            isinstance(effective_random_seed, bool)
            or not isinstance(effective_random_seed, int)
        ):
            raise ValueError("random_seed must be an integer or None")

        self.simulation_factory = simulation_factory
        self.output_root = Path(output_root).resolve()
        self.base_run_id = _validate_run_id(run_id or _default_run_id())
        self.time_step_s = normalized_time_step
        self.max_steps = int(max_steps)
        self.schema_version = schema_version
        self.random_seed = effective_random_seed

        self.simulation: Any | None = None
        self.adapter: CaSnapshotAdapter | None = None
        self.logger: CsvExperimentLogger | None = None
        self.current_snapshot: dict[str, Any] | None = None
        self.current_run_id: str | None = None
        self._reset_count = 0
        self._failed = False

    def initialize(self) -> dict[str, Any]:
        if self._failed:
            raise RuntimeError("runner failed; call reset() or create a new runner")
        if self.simulation is not None:
            raise RuntimeError("runner is already initialized; call reset instead")
        return self._create_run(self.base_run_id)

    def _create_run(self, run_id: str) -> dict[str, Any]:
        simulation = self.simulation_factory()
        if not hasattr(simulation, "init_simulation"):
            raise TypeError("simulation must provide init_simulation()")
        if not hasattr(simulation, "step") or not hasattr(simulation, "all_done"):
            raise TypeError("simulation must provide step() and all_done()")

        simulation.init_simulation()
        adapter = CaSnapshotAdapter(
            run_id=run_id,
            time_step_s=self.time_step_s,
            schema_version=self.schema_version,
            random_seed=self.random_seed,
        )
        snapshot = adapter.capture(simulation)
        scenario_id = str(snapshot["scenario_id"])
        logger = CsvExperimentLogger(
            self.output_root / run_id,
            run_id=run_id,
            scenario_id=scenario_id,
            random_seed=snapshot.get("random_seed"),
            time_step_s=self.time_step_s,
            schema_version=self.schema_version,
        )
        try:
            logger.start()
            logger.record_snapshot(snapshot)
        except Exception:
            logger.close()
            self._failed = True
            raise

        self.simulation = simulation
        self.adapter = adapter
        self.logger = logger
        self.current_snapshot = snapshot
        self.current_run_id = run_id
        self._failed = False
        return snapshot

    @property
    def initialized(self) -> bool:
        return self.simulation is not None

    @property
    def finished(self) -> bool:
        if self._failed:
            return True
        if self.simulation is None:
            return False
        return bool(
            self.simulation.all_done()
            or int(getattr(self.simulation, "current_step", 0)) >= self.max_steps
        )

    def step(self) -> dict[str, Any]:
        if self._failed:
            raise RuntimeError("runner failed; only close() or reset() is allowed")
        if self.simulation is None or self.adapter is None or self.logger is None:
            raise RuntimeError("runner must be initialized before stepping")
        if self.finished:
            assert self.current_snapshot is not None
            return self.current_snapshot

        try:
            previous_step = int(getattr(self.simulation, "current_step", 0))
            self.simulation.step()
            current_step = int(getattr(self.simulation, "current_step", 0))
            if current_step != previous_step + 1:
                raise RuntimeError(
                    "upstream simulation.step() must advance current_step "
                    "by exactly one"
                )

            snapshot = self.adapter.capture(self.simulation)
            self.logger.record_snapshot(snapshot)
        except Exception:
            self._failed = True
            self.logger.close()
            raise
        self.current_snapshot = snapshot
        return snapshot

    def run_until_finished(self) -> dict[str, Any]:
        if self._failed:
            raise RuntimeError("runner failed; only close() or reset() is allowed")
        if not self.initialized:
            self.initialize()
        while not self.finished:
            self.step()
        assert self.current_snapshot is not None
        return self.current_snapshot

    def reset(self) -> dict[str, Any]:
        self.close()
        self._reset_count += 1
        run_id = f"{self.base_run_id}_reset_{self._reset_count}"
        self._failed = False
        return self._create_run(run_id)

    def close(self) -> None:
        if self.logger is not None:
            self.logger.close()
        self.logger = None
        self.adapter = None
        self.simulation = None
        self.current_snapshot = None
        self.current_run_id = None

    def __enter__(self) -> "SimulationRunner":
        self.initialize()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run D's third-week real-step visualization and CSV logger."
    )
    parser.add_argument("--time-step", type=float, default=0.5)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--interval-ms", type=int, default=300)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("outputs") / "d_week3",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run to completion without opening the Matplotlib window",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    runner = SimulationRunner(
        default_simulation_factory(args.random_seed),
        output_root=args.output_root,
        time_step_s=args.time_step,
        max_steps=args.max_steps,
        random_seed=args.random_seed,
    )
    try:
        runner.initialize()
        if args.headless:
            final_snapshot = runner.run_until_finished()
            print(
                "run complete:",
                final_snapshot["run_id"],
                f"step={final_snapshot['step']}",
                f"output={runner.output_root / final_snapshot['run_id']}",
            )
        else:
            from visualization.visualizer import MatplotlibSimulationViewer

            viewer = MatplotlibSimulationViewer(
                runner,
                interval_ms=args.interval_ms,
            )
            viewer.show()
    finally:
        runner.close()


if __name__ == "__main__":
    main()
