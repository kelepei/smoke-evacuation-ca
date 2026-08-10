from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

from experiments.runner import SimulationRunner, default_simulation_factory
from visualization.visualizer import MatplotlibSimulationViewer


class MatplotlibSimulationViewerTests(unittest.TestCase):
    def test_controls_draw_and_save_without_gui(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            runner = SimulationRunner(
                default_simulation_factory(42),
                output_root=temp_dir,
                run_id="viewer_test",
            )
            runner.initialize()
            viewer = MatplotlibSimulationViewer(runner, interval_ms=100)
            try:
                viewer.single_step()
                self.assertEqual(runner.current_snapshot["step"], 1)

                viewer.start()
                self.assertTrue(viewer.running)
                step_before_timer = runner.current_snapshot["step"]
                viewer._on_timer()
                self.assertEqual(
                    runner.current_snapshot["step"], step_before_timer + 1
                )
                viewer.pause()
                self.assertFalse(viewer.running)
                paused_step = runner.current_snapshot["step"]
                viewer._on_timer()
                self.assertEqual(runner.current_snapshot["step"], paused_step)

                viewer.speed_slider.set_val(2.0)
                self.assertEqual(viewer._timer.interval, 50)

                invalid_snapshot = copy.deepcopy(runner.current_snapshot)
                invalid_snapshot["grid"]["cell_type"][1][1] = "unknown_type"
                with self.assertRaisesRegex(ValueError, "unknown cell_type"):
                    viewer.draw_snapshot(invalid_snapshot)

                viewer.reset()
                self.assertEqual(runner.current_snapshot["step"], 0)
                self.assertEqual(
                    runner.current_snapshot["run_id"],
                    "viewer_test_reset_1",
                )

                screenshot = viewer.save_screenshot(
                    Path(temp_dir, "viewer.png")
                )
                self.assertTrue(screenshot.exists())
                self.assertGreater(screenshot.stat().st_size, 0)
            finally:
                viewer.close()


if __name__ == "__main__":
    unittest.main()
