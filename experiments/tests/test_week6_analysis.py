from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.week6_analysis import analyze_run, compare_runs, write_analysis


class Week6AnalysisTests(unittest.TestCase):
    def _write_exit_run(self, root: Path, exits: dict[str, str | None]) -> None:
        fieldnames = ["run_id", "step", "time_s", "person_id", "evacuated", "actual_exit_entity"]
        with (root / "people_log.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            for person_id, exit_id in exits.items():
                writer.writerow({
                    "run_id": root.name,
                    "step": "1",
                    "time_s": "0.5",
                    "person_id": person_id,
                    "evacuated": "true" if exit_id else "false",
                    "actual_exit_entity": exit_id or "",
                })

    def _write_run(self, root: Path, *, time_scale: float = 1.0) -> None:
        rows = [
            {"run_id": root.name, "scenario_id": "week6", "step": "0", "time_s": "0", "person_id": "0", "x": "1", "y": "1", "evacuated": "false", "smoke": "2", "risk": "0.2", "dose": "0", "info_state": "UNKNOWN", "target_exit": "exit_1", "group_id": "g1", "congestion": "0.1"},
            {"run_id": root.name, "scenario_id": "week6", "step": "0", "time_s": "0", "person_id": "1", "x": "1", "y": "2", "evacuated": "false", "smoke": "4", "risk": "0.4", "dose": "0", "info_state": "ALERTED", "target_exit": "exit_1", "group_id": "g1", "congestion": "0.2"},
            {"run_id": root.name, "scenario_id": "week6", "step": "1", "time_s": str(0.5 * time_scale), "person_id": "0", "x": "2", "y": "1", "evacuated": "true", "smoke": "6", "risk": "0.3", "dose": "1", "info_state": "CONFIRMED", "actual_exit": "exit_1", "group_id": "g1", "congestion": "0.3"},
            {"run_id": root.name, "scenario_id": "week6", "step": "1", "time_s": str(0.5 * time_scale), "person_id": "1", "x": "1", "y": "2", "evacuated": "false", "smoke": "8", "risk": "0.5", "dose": "2", "info_state": "UNKNOWN", "target_exit": "exit_1", "group_id": "g1", "congestion": "0.4"},
        ]
        with (root / "people_log.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=sorted({key for row in rows for key in row}))
            writer.writeheader()
            writer.writerows(rows)

    def test_analyzes_zero_based_ids_and_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_run(root)
            metrics = analyze_run(root)
            self.assertEqual(metrics["evacuated_count"], 1)
            self.assertEqual(metrics["first_evacuation_time_s"], 0.5)
            self.assertEqual(metrics["max_smoke"], 8.0)
            self.assertEqual(metrics["overlap_cells"], 0)
            self.assertEqual(metrics["overlap_steps"], 0)
            self.assertEqual(metrics["total_persons"], 2)
            self.assertEqual(metrics["mean_evacuation_time_s"], 0.5)
            self.assertEqual(metrics["total_evacuation_time_s"], "NA")
            self.assertEqual(metrics["exit_utilization"], {"exit_1": 1.0})
            self.assertEqual(metrics["panic_peak"], "NA")
            self.assertEqual(metrics["urgent_peak"], "NA")
            self.assertEqual(metrics["q95_dose"], 1.95)
            write_analysis(metrics, root)
            self.assertTrue((root / "week6_metrics.json").is_file())
            self.assertTrue((root / "week6_metrics_summary.csv").is_file())
            self.assertEqual(json.loads((root / "week6_metrics.json").read_text(encoding="utf-8"))["run_id"], root.name)

    def test_compares_lower_is_better_metrics(self) -> None:
        baseline = {"total_time_s": 10, "avg_smoke": 4}
        strategy = {"total_time_s": 8, "avg_smoke": 5}
        result = compare_runs(baseline, strategy)
        self.assertEqual(result["total_time_s_improvement_rate"], 0.2)
        self.assertEqual(result["avg_smoke_improvement_rate"], -0.25)

    def test_exit_utilization_uses_recorded_evacuations_and_keeps_zero_use_exits(self) -> None:
        cases = (
            ("none", {"p1": None, "p2": None}, ["exit_1", "exit_2"], "NA", {"exit_1": 0, "exit_2": 0}),
            ("single", {"p1": "exit_1", "p2": None}, ["exit_1"], {"exit_1": 1.0}, {"exit_1": 1}),
            ("multiple", {"p1": "exit_1", "p2": "exit_1", "p3": "exit_2"}, ["exit_1", "exit_2"], {"exit_1": 0.666667, "exit_2": 0.333333}, {"exit_1": 2, "exit_2": 1}),
            ("zero-use", {"p1": "exit_1", "p2": "exit_2"}, ["exit_1", "exit_2", "exit_3"], {"exit_1": 0.5, "exit_2": 0.5}, {"exit_1": 1, "exit_2": 1, "exit_3": 0}),
        )
        for name, exits, valid_exit_ids, expected_utilization, expected_usage in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self._write_exit_run(root, exits)
                metrics = analyze_run(root, valid_exit_ids=valid_exit_ids)
                self.assertEqual(expected_utilization, metrics["exit_utilization"])
                self.assertEqual(expected_usage, metrics["exit_usage"])
                if name == "none":
                    self.assertEqual("NA", metrics["exit_distribution"])
                if name == "zero-use":
                    self.assertEqual({"exit_1": 1, "exit_2": 1}, metrics["exit_distribution"])
                    self.assertNotEqual("NA", metrics["exit_entropy_h"])
                    self.assertNotEqual("NA", metrics["exit_gini_g"])


if __name__ == "__main__":
    unittest.main()
