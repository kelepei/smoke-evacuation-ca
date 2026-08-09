from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from experiments.runner import SimulationRunner, default_simulation_factory


class SimulationRunnerTests(unittest.TestCase):
    def test_real_b_runtime_runs_to_configured_limit_and_writes_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = SimulationRunner(
                default_simulation_factory(42),
                output_root=temp_dir,
                run_id="runner_test",
                time_step_s=0.5,
                max_steps=50,
            )
            try:
                initial = runner.initialize()
                self.assertEqual(initial["step"], 0)
                self.assertEqual(initial["random_seed"], 42)
                final = runner.run_until_finished()
                self.assertTrue(runner.finished)
                # D must report B's result instead of asserting a fabricated
                # evacuation outcome. The current B movement policy may stop
                # at the configured limit with people still inside.
                self.assertEqual(final["step"], 50)

                people_path = Path(
                    temp_dir, "runner_test", "people_log.csv"
                )
                event_path = Path(temp_dir, "runner_test", "event_log.csv")
                with people_path.open(newline="", encoding="utf-8") as file:
                    people_rows = list(csv.DictReader(file))
                with event_path.open(newline="", encoding="utf-8") as file:
                    event_rows = list(csv.DictReader(file))

                self.assertEqual(
                    len(people_rows),
                    (final["step"] + 1) * len(final["people"]),
                )
                self.assertEqual(event_rows, [])
            finally:
                runner.close()

    def test_logging_failure_blocks_further_steps_until_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = SimulationRunner(
                default_simulation_factory(42),
                output_root=temp_dir,
                run_id="failure_test",
            )
            try:
                runner.initialize()
                runner.logger.record_snapshot = Mock(
                    side_effect=RuntimeError("simulated log failure")
                )
                with self.assertRaisesRegex(RuntimeError, "simulated log failure"):
                    runner.step()
                with self.assertRaisesRegex(RuntimeError, "runner failed"):
                    runner.step()

                reset_snapshot = runner.reset()
                self.assertEqual(reset_snapshot["step"], 0)
            finally:
                runner.close()

    def test_single_step_and_reset_create_separate_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = SimulationRunner(
                default_simulation_factory(42),
                output_root=temp_dir,
                run_id="reset_test",
            )
            try:
                runner.initialize()
                self.assertEqual(runner.step()["step"], 1)
                reset_snapshot = runner.reset()
                self.assertEqual(reset_snapshot["step"], 0)
                self.assertEqual(
                    reset_snapshot["run_id"], "reset_test_reset_1"
                )
                self.assertTrue(
                    Path(temp_dir, "reset_test", "people_log.csv").exists()
                )
                self.assertTrue(
                    Path(
                        temp_dir,
                        "reset_test_reset_1",
                        "people_log.csv",
                    ).exists()
                )
            finally:
                runner.close()


if __name__ == "__main__":
    unittest.main()
