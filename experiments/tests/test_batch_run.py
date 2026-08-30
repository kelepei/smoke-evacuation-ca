from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiments.batch_run import batch_boxplot_svg, calculate_batch_statistics, run_batch


class BatchRunTests(unittest.TestCase):
    def test_statistics_use_only_real_available_values(self) -> None:
        rows = [
            {
                "total_persons": 4,
                "evacuated_count": 4,
                "simulation_steps": 20,
                "simulation_time_s": 10.0,
                "total_evacuation_time_s": 10.0,
                "mean_evacuation_time_s": 6.0,
                "t90_time_s": 9.0,
                "evacuation_rate": 1.0,
                "remaining_count": 0,
            },
            {
                "total_persons": 4,
                "evacuated_count": 4,
                "simulation_steps": 24,
                "simulation_time_s": 12.0,
                "total_evacuation_time_s": 12.0,
                "mean_evacuation_time_s": 8.0,
                "t90_time_s": 11.0,
                "evacuation_rate": 1.0,
                "remaining_count": 0,
            },
            {
                "total_persons": 4,
                "evacuated_count": 2,
                "simulation_steps": 30,
                "simulation_time_s": 15.0,
                "total_evacuation_time_s": "NA",
                "mean_evacuation_time_s": 10.0,
                "t90_time_s": "NA",
                "evacuation_rate": 0.5,
                "remaining_count": 2,
            },
        ]
        statistics = {row["metric_name"]: row for row in calculate_batch_statistics(rows)}
        self.assertEqual(statistics["total_evacuation_time_s"]["n"], 2)
        self.assertEqual(statistics["total_evacuation_time_s"]["mean"], 11.0)
        self.assertAlmostEqual(statistics["total_evacuation_time_s"]["std"], 2 ** 0.5)
        self.assertEqual(statistics["mean_evacuation_time_s"]["mean"], 8.0)
        self.assertIn("真实运行", batch_boxplot_svg(statistics.values()) or "")

    def test_fewer_than_three_runs_do_not_produce_a_boxplot(self) -> None:
        statistics = calculate_batch_statistics(
            [
                {"total_evacuation_time_s": 10, "evacuation_rate": 1, "remaining_count": 0},
                {"total_evacuation_time_s": 12, "evacuation_rate": 1, "remaining_count": 0},
            ]
        )
        self.assertIsNone(batch_boxplot_svg(statistics))

    def test_failed_seed_is_preserved_in_batch_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch("experiments.batch_run.create_integrated_runner", side_effect=RuntimeError("runtime unavailable")):
                result = run_batch(
                    map_path=root / "map.json",
                    population_path=root / "people.json",
                    yaml_path=None,
                    seeds=[40, 41],
                    output_root=root / "outputs",
                    batch_id="failure_case",
                )
            self.assertEqual(["failed", "failed"], [row["status"] for row in result["runs"]])
            self.assertTrue((root / "outputs" / "failure_case" / "failure_case_seed_40" / "batch_failure.json").is_file())


if __name__ == "__main__":
    unittest.main()
