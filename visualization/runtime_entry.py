"""Public D-side entry for attaching visualization and logs to a live run.

This module is intentionally small. A/B/C may keep ownership of map loading,
social decisions and CA stepping; D attaches to the existing runtime and
records the normalized snapshots that drive the page, CSV logs, and result
package. It never writes back to the supplied simulation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from experiments.csv_logger import CsvExperimentLogger
from experiments.result_package import ResultPackage, build_result_package
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


class DVisualizationEntryError(RuntimeError):
    """Raised when a live A+B+C runtime cannot be recorded safely by D."""


class DVisualizationEntry:
    """Record snapshots from an already-created B-compatible simulation.

    Typical A-side integration::

        d_view = DVisualizationEntry(sim, output_root="outputs", run_id="run_001")
        d_view.start()
        sim.run_one_step(c_step_data=behaviour, signage_model=signage)
        snapshot = d_view.capture()
        d_view.close()

    ``capture`` must be called once at step 0 and then once after each B step.
    Missing B/C fields remain null/empty in D output rather than being invented.
    """

    def __init__(
        self,
        simulation: Any,
        *,
        output_root: str | Path,
        run_id: str,
        time_step_s: float = 0.5,
        schema_version: str = "0.1-draft",
        random_seed: int | None = None,
    ) -> None:
        if not run_id or Path(run_id).name != run_id:
            raise DVisualizationEntryError("run_id must be one safe path component")
        if time_step_s <= 0:
            raise DVisualizationEntryError("time_step_s must be greater than zero")
        # Raw B exposes scene/person_map; D snapshots use config/persons.
        if not hasattr(simulation, "config") and hasattr(simulation, "scene"):
            from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter

            simulation = EvacEngineRuntimeAdapter(simulation)
        self.simulation = simulation
        self.output_root = Path(output_root).resolve()
        self.run_id = run_id
        self.time_step_s = float(time_step_s)
        self.schema_version = schema_version
        self.random_seed = random_seed
        self._adapter = CaSnapshotAdapter(
            run_id=run_id,
            time_step_s=self.time_step_s,
            schema_version=schema_version,
            random_seed=random_seed,
        )
        self._logger: CsvExperimentLogger | None = None
        self.current_snapshot: dict[str, Any] | None = None

    @property
    def started(self) -> bool:
        return self._logger is not None

    @property
    def output_dir(self) -> Path:
        return self.output_root / self.run_id

    def start(self) -> dict[str, Any]:
        """Capture mandatory initial step=0 snapshot and open D logs."""
        if self._logger is not None:
            raise DVisualizationEntryError("D visualization entry is already started")
        snapshot = self._adapter.capture(self.simulation)
        if snapshot["step"] != 0:
            raise DVisualizationEntryError(
                "D visualization entry must start before B advances beyond step 0"
            )
        logger = CsvExperimentLogger(
            self.output_dir,
            run_id=self.run_id,
            scenario_id=str(snapshot["scenario_id"]),
            random_seed=snapshot.get("random_seed"),
            time_step_s=self.time_step_s,
            schema_version=self.schema_version,
        )
        try:
            logger.start()
            logger.record_snapshot(snapshot)
        except Exception:
            logger.close()
            raise
        self._logger = logger
        self.current_snapshot = snapshot
        return snapshot

    def capture(self) -> dict[str, Any]:
        """Capture one already-advanced B step and append the D CSV logs."""
        if self._logger is None:
            raise DVisualizationEntryError("call start() before capture()")
        snapshot = self._adapter.capture(self.simulation)
        self._logger.record_snapshot(snapshot)
        self.current_snapshot = snapshot
        return snapshot

    def capture_after_step(self) -> dict[str, Any]:
        """Explicit alias for B loops: call this immediately after one B step."""

        return self.capture()

    def export_result_package(
        self, *, input_files: Mapping[str, Path], max_steps: int
    ) -> ResultPackage:
        """Build a reproducible D ZIP from the real logs recorded so far."""
        if self.current_snapshot is None:
            raise DVisualizationEntryError("start() before exporting results")
        return build_result_package(
            output_dir=self.output_dir,
            final_snapshot=self.current_snapshot,
            input_files=input_files,
            max_steps=max_steps,
        )

    def close(self) -> None:
        """Close D-owned log handles; the A/B/C simulation stays untouched."""
        if self._logger is not None:
            self._logger.close()
        self._logger = None

    def __enter__(self) -> "DVisualizationEntry":
        self.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
