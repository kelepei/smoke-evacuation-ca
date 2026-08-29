"""Tests for D-owned run artifact output."""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.run_artifacts import write_run_artifacts


class RunArtifactTests(unittest.TestCase):
    def test_writes_real_snapshot_metrics_and_preserves_missing_values(self) -> None:
        snapshot = {
            "run_id": "artifact-test",
            "scenario_id": "test-scene",
            "schema_version": "0.1-draft",
            "random_seed": 7,
            "step": 3,
            "time_s": 1.5,
            "people": [
                {"person_id": 0, "evacuated": True},
                {"person_id": 1, "evacuated": False},
            ],
            "fields": {"smoke_field": [[0.0, 2.0], [1.0, None]]},
        }
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary)
            with (output_dir / "event_log.csv").open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=["event_type", "time_s"])
                writer.writeheader()
                writer.writerow({"event_type": "evac_success", "time_s": "1.0"})

            metrics = write_run_artifacts(
                snapshot,
                output_dir,
                input_files={"map": "map.json", "population": "people.json"},
                save_frame=False,
            )

            self.assertEqual(metrics["evacuated_count"], 1)
            self.assertEqual(metrics["remaining_count"], 1)
            self.assertEqual(metrics["first_evac_time_s"], 1.0)
            self.assertEqual(metrics["max_smoke"], 2.0)
            self.assertAlmostEqual(metrics["avg_smoke"], 1.0)
            self.assertTrue((output_dir / "config_used.json").is_file())
            self.assertTrue((output_dir / "metrics.json").is_file())
            self.assertTrue((output_dir / "metrics_summary.csv").is_file())
            self.assertEqual(
                json.loads((output_dir / "config_used.json").read_text(encoding="utf-8"))["run_id"],
                "artifact-test",
            )


if __name__ == "__main__":
    unittest.main()
