from __future__ import annotations

import csv
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from experiments.result_package import create_result_package


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
            package = create_result_package(
                run_directory=run_dir, destination_directory=root / "packages",
                map_path=map_path, population_path=population_path,
            )
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"people_log.csv", "event_log.csv", "metrics.csv", "evacuation_curve.svg", "occupancy_heatmap.svg", "metadata.json", "inputs/map.json", "inputs/population.json"},
                )
                metadata = json.loads(archive.read("metadata.json"))
        self.assertEqual(metadata["metrics"]["evacuated_people"], 1)
        self.assertEqual(metadata["metrics"]["all_evacuated_time_s"], 0.5)
