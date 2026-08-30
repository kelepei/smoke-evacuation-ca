from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from experiments.result_package import build_result_package


class ResultPackageTests(unittest.TestCase):
    def test_packages_actual_logs_metrics_and_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_dir = root / "run_01"
            run_dir.mkdir()
            with (run_dir / "people_log.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["step", "time_s", "person_id", "x", "y", "evacuated"])
                writer.writeheader()
                writer.writerows([
                    {"step": 0, "time_s": 0, "person_id": 1, "x": 1, "y": 1, "evacuated": False},
                    {"step": 1, "time_s": 0.5, "person_id": 1, "x": 2, "y": 1, "evacuated": True},
                ])
            (run_dir / "event_log.csv").write_text("event_type\nevac_success\n", encoding="utf-8")
            map_path = root / "map.json"; map_path.write_text("{}", encoding="utf-8")
            population_path = root / "population.json"; population_path.write_text("{}", encoding="utf-8")
            package = build_result_package(
                output_dir=run_dir,
                final_snapshot={
                    "run_id": "run_01",
                    "scenario_id": "actual_input_test",
                    "schema_version": "0.1",
                    "random_seed": 17,
                    "time_step": 0.5,
                    "step": 1,
                    "grid": {"width": 4, "height": 3},
                },
                input_files={"map": map_path, "population": population_path},
                max_steps=10,
            )
            with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {
                        "run_01/people_log.csv", "run_01/event_log.csv", "run_01/metrics.csv",
                        "run_01/evacuation_curve.svg", "run_01/occupancy_heatmap.svg",
                        "run_01/week6_metrics.json", "run_01/week6_metrics_summary.csv",
                        "run_01/metadata.json", "run_01/config.json", "run_01/inputs/map.json",
                        "run_01/inputs/population.json",
                    },
                )
                metadata = json.loads(archive.read("run_01/metadata.json"))
                configuration = json.loads(archive.read("run_01/config.json"))
                self.assertIn("累计占用热力图", archive.read("run_01/occupancy_heatmap.svg").decode("utf-8"))
        self.assertEqual(metadata["summary"]["evacuated_count"], 1)
        self.assertEqual(metadata["summary"]["last_successful_exit_time"], 0.5)
        self.assertEqual(metadata["random_seed"], 17)
        self.assertEqual(configuration["random_seed"], 17)
