from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments.crowd_metrics import (
    resolve_analysis_contract,
    trajectory_kinematics,
    write_trajectory_kinematics,
)


def row(
    step: int,
    time_s: float,
    x: int,
    y: int,
    *,
    person_id: int = 1,
    evacuated: bool = False,
) -> dict[str, object]:
    return {
        "person_id": person_id,
        "step": step,
        "time_s": time_s,
        "x": x,
        "y": y,
        "evacuated": evacuated,
    }


class CrowdMetricsTests(unittest.TestCase):
    def test_normal_straight_motion_and_static_motion(self) -> None:
        samples = trajectory_kinematics([
            row(0, 0.0, 1, 1), row(1, 0.5, 2, 1),
            row(2, 1.0, 2, 1),
        ])
        self.assertEqual("initial_sample", samples[0]["validity"])
        self.assertEqual("valid", samples[1]["validity"])
        self.assertEqual(1, samples[1]["dx_cells"])
        self.assertEqual(2.0, samples[1]["vx_cells_s"])
        self.assertEqual("valid", samples[2]["validity"])
        self.assertEqual(0.0, samples[2]["speed_cells_s"])

    def test_diagonal_motion_and_explicit_physical_scale(self) -> None:
        samples = trajectory_kinematics(
            [row(0, 0.0, 0, 0), row(1, 1.0, 1, 1)],
            physical_scale={"source": "explicit_map", "value": 0.5, "unit": "m"},
        )
        self.assertAlmostEqual(2 ** 0.5, samples[1]["speed_cells_s"])
        self.assertAlmostEqual(0.5, samples[1]["vx_m_s"])
        self.assertAlmostEqual((0.5 ** 2 + 0.5 ** 2) ** 0.5, samples[1]["speed_m_s"])

    def test_missing_steps_uses_observed_time_delta_without_filling_frames(self) -> None:
        samples = trajectory_kinematics([
            row(0, 0.0, 0, 0), row(3, 1.5, 3, 0),
        ])
        self.assertEqual("valid_step_gap", samples[1]["validity"])
        self.assertEqual(1.5, samples[1]["dt_s"])
        self.assertEqual(2.0, samples[1]["vx_cells_s"])

    def test_repeated_and_backward_time_are_invalid(self) -> None:
        repeated = trajectory_kinematics([
            row(0, 1.0, 0, 0), row(1, 1.0, 1, 0),
        ])
        backward = trajectory_kinematics([
            row(0, 1.0, 0, 0), row(1, 0.5, 1, 0),
        ])
        self.assertEqual("invalid_nonpositive_dt", repeated[1]["validity"])
        self.assertEqual("invalid_nonpositive_dt", backward[1]["validity"])
        self.assertEqual("NA", repeated[1]["speed_cells_s"])

    def test_last_move_to_exit_is_kept_but_repeated_exit_logs_are_excluded(self) -> None:
        samples = trajectory_kinematics([
            row(0, 0.0, 0, 0), row(1, 0.5, 1, 0, evacuated=True),
            row(2, 1.0, 1, 0, evacuated=True),
        ])
        self.assertEqual("valid_to_exit", samples[1]["validity"])
        self.assertEqual(2.0, samples[1]["speed_cells_s"])
        self.assertEqual("excluded_post_evacuation", samples[2]["validity"])
        self.assertEqual("NA", samples[2]["speed_cells_s"])

    def test_unavailable_physical_scale_keeps_metre_rates_na(self) -> None:
        contract = resolve_analysis_contract()
        samples = trajectory_kinematics(
            [row(0, 0.0, 0, 0), row(1, 1.0, 1, 0)],
            physical_scale=contract["physical_scale"],
        )
        self.assertEqual("unavailable", contract["physical_scale"]["source"])
        self.assertEqual("NA", samples[1]["vx_m_s"])
        self.assertEqual("NA", samples[1]["speed_m_s"])

    def test_teleport_and_impassable_position_are_reported_not_corrected(self) -> None:
        teleport = trajectory_kinematics([
            row(0, 0.0, 0, 0), row(1, 0.5, 3, 0),
        ])
        wall_grid = {"width": 3, "height": 1, "cell_type": [["free", "wall", "exit"]]}
        impassable = trajectory_kinematics([
            row(0, 0.0, 0, 0), row(1, 0.5, 1, 0),
        ], grid=wall_grid)
        self.assertEqual("flagged_teleport", teleport[1]["validity"])
        self.assertEqual("invalid_impassable_position", impassable[1]["validity"])

    def test_validation_csv_is_written_from_real_people_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            people_log = root / "people_log.csv"
            with people_log.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(row(0, 0, 0, 0)))
                writer.writeheader()
                writer.writerows([row(0, 0.0, 0, 0), row(1, 0.5, 1, 0)])
            summary = write_trajectory_kinematics(
                people_log_path=people_log,
                output_path=root / "trajectory_kinematics.csv",
                analysis_contract=resolve_analysis_contract(),
            )
            with (root / "trajectory_kinematics.csv").open(newline="", encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(2, summary["row_count"])
            self.assertEqual("NA", rows[1]["speed_m_s"])
            self.assertEqual("valid", rows[1]["validity"])

    def test_explicit_map_and_runtime_contracts_are_distinguished(self) -> None:
        map_contract = resolve_analysis_contract(
            map_analysis={
                "physical_cell_size_m": 0.4,
                "sampling_window_s": 1.2,
                "velocity_method": "consecutive_valid_log_samples",
            }
        )
        runtime_contract = resolve_analysis_contract(
            runtime_physical_cell_size_m=0.4, runtime_sampling_window_s=1.5
        )
        self.assertEqual("explicit_map", map_contract["physical_scale"]["source"])
        self.assertEqual("explicit_map", map_contract["sampling_window_s"]["source"])
        self.assertEqual("runtime_config", runtime_contract["physical_scale"]["source"])
        self.assertEqual("runtime_config", runtime_contract["sampling_window_s"]["source"])
        self.assertEqual(
            "literature_default", resolve_analysis_contract()["sampling_window_s"]["source"]
        )
        with self.assertRaisesRegex(ValueError, "disagree"):
            resolve_analysis_contract(
                map_analysis={"physical_cell_size_m": 0.4},
                runtime_physical_cell_size_m=0.5,
            )


if __name__ == "__main__":
    unittest.main()
