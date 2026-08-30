from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.experiment_history import (
    ExperimentHistoryError,
    discover_experiments,
    load_experiment_detail,
)


class ExperimentHistoryTests(unittest.TestCase):
    def _write_run(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True)
        rows = [
            {"run_id": "real_run", "scenario_id": "scene_a", "random_seed": 41, "step": 0, "time_s": 0, "person_id": 1, "x": 1, "y": 1, "evacuated": "false"},
            {"run_id": "real_run", "scenario_id": "scene_a", "random_seed": 41, "step": 1, "time_s": 0.5, "person_id": 1, "x": 2, "y": 1, "evacuated": "true"},
        ]
        with (run_dir / "people_log.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        (run_dir / "event_log.csv").write_text("event_type\nevac_success\n", encoding="utf-8")
        (run_dir / "config_used.json").write_text(
            json.dumps(
                {
                    "grid": {"width": 4, "height": 3},
                    "input_files": {"yaml": "config.yaml"},
                }
            ),
            encoding="utf-8",
        )

    def test_discovers_only_log_backed_runs_and_loads_real_figures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "experiments"
            run_dir = root / "real_run"
            self._write_run(run_dir)
            (root / "empty_run").mkdir()

            experiments = discover_experiments([root])
            self.assertEqual(1, len(experiments))
            self.assertEqual("complete", experiments[0]["status"])
            self.assertEqual(0.5, experiments[0]["total_evacuation_time_s"])
            self.assertEqual("buildable", experiments[0]["result_package"]["status"])

            detail = load_experiment_detail(experiments[0]["id"], [root])
            self.assertIn("真实日志", detail["evacuation_curve_svg"])
            self.assertIn("累计占用热力图", detail["occupancy_heatmap_svg"])
            self.assertEqual("config_used.grid", detail["metadata"]["figure_grid"])

    def test_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ExperimentHistoryError):
                load_experiment_detail("experiments/../secret", [Path(temporary) / "experiments"])

    def test_preserves_invalid_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "experiments"
            failed = root / "failed_run"
            failed.mkdir(parents=True)
            (failed / "batch_failure.json").write_text(json.dumps({"status": "failed", "error": "real failure"}), encoding="utf-8")
            experiments = discover_experiments([root])
            self.assertEqual(1, len(experiments))
            self.assertEqual("failed", experiments[0]["status"])
            self.assertEqual("real failure", experiments[0]["error"])


if __name__ == "__main__":
    unittest.main()
