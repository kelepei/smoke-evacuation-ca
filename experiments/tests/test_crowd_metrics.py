from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from experiments.crowd_metrics import (
    resolve_analysis_contract,
    trajectory_kinematics,
    velocity_vector_field,
    voronoi_density_for_frame,
    write_velocity_vector_field,
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
    def grid(self, rows: list[list[str]]) -> dict[str, object]:
        return {"width": len(rows[0]), "height": len(rows), "cell_type": rows}

    def explicit_scale(self) -> dict[str, object]:
        return {"source": "explicit_map", "value": 1.0, "unit": "m"}

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

    def test_voronoi_density_requires_explicit_physical_scale(self) -> None:
        result = voronoi_density_for_frame(
            [{"person_id": 1, "x": 0, "y": 0}],
            grid=self.grid([["free"]]),
            physical_scale=resolve_analysis_contract()["physical_scale"],
        )
        self.assertEqual("unavailable_physical_scale", result["status"])
        self.assertEqual([], result["samples"])
        self.assertIsNone(result["unit"])

    def test_voronoi_density_clips_to_legal_domain_not_walls_or_exterior(self) -> None:
        result = voronoi_density_for_frame(
            [{"person_id": 1, "x": 0, "y": 0}],
            grid=self.grid([["free", "wall"], ["obstacle", "free"]]),
            physical_scale=self.explicit_scale(),
            time_s=2.5,
        )
        self.assertEqual("available", result["status"])
        self.assertEqual(2, result["legal_cell_count"])
        self.assertAlmostEqual(2.0, result["samples"][0]["voronoi_area_m2"])
        self.assertAlmostEqual(0.5, result["samples"][0]["density_persons_m2"])

    def test_voronoi_density_bottleneck_is_higher_than_open_room(self) -> None:
        # Two people in a 3x3 room versus four people queued through a
        # one-cell-wide egress neck: the neck's legal area per person is lower.
        grid = self.grid([
            ["free", "free", "free", "wall", "wall", "wall", "wall", "wall", "wall"],
            ["free", "free", "free", "wall", "free", "free", "free", "exit", "wall"],
            ["free", "free", "free", "wall", "wall", "wall", "wall", "wall", "wall"],
        ])
        result = voronoi_density_for_frame(
            [
                {"person_id": 1, "x": 0, "y": 0},
                {"person_id": 2, "x": 2, "y": 2},
                {"person_id": 3, "x": 4, "y": 1},
                {"person_id": 4, "x": 5, "y": 1},
                {"person_id": 5, "x": 6, "y": 1},
                {"person_id": 6, "x": 7, "y": 1},
            ],
            grid=grid,
            physical_scale=self.explicit_scale(),
        )
        by_id = {sample["person_id"]: sample for sample in result["samples"]}
        self.assertGreater(
            by_id[5]["density_persons_m2"], by_id[1]["density_persons_m2"]
        )

    def test_velocity_field_parallel_unidirectional_samples_keep_direction(self) -> None:
        field = velocity_vector_field([
            {"time_s": 0.5, "x": 1, "y": 0, "vx_cells_s": 2.0, "vy_cells_s": 0.0, "validity": "valid"},
            {"time_s": 0.5, "x": 2, "y": 0, "vx_cells_s": 2.0, "vy_cells_s": 0.0, "validity": "valid"},
        ], sampling_window_s=2.5, physical_scale=self.explicit_scale())
        self.assertEqual([2.0, 2.0], [record["vx_cells_s"] for record in field["records"]])

    def test_velocity_field_captures_bidirectional_and_static_samples(self) -> None:
        field = velocity_vector_field([
            {"time_s": 0.5, "x": 1, "y": 0, "vx_cells_s": 2.0, "vy_cells_s": 0.0, "validity": "valid"},
            {"time_s": 0.5, "x": 1, "y": 1, "vx_cells_s": -2.0, "vy_cells_s": 0.0, "validity": "valid"},
            {"time_s": 0.5, "x": 2, "y": 0, "vx_cells_s": 0.0, "vy_cells_s": 0.0, "validity": "valid"},
            {"time_s": 1.0, "x": 3, "y": 0, "vx_cells_s": "NA", "vy_cells_s": "NA", "validity": "flagged_teleport"},
        ], sampling_window_s=2.5, physical_scale=self.explicit_scale())
        by_cell = {(record["x"], record["y"]): record for record in field["records"]}
        self.assertEqual("available", field["status"])
        self.assertEqual(2.0, by_cell[(1, 0)]["vx_cells_s"])
        self.assertEqual(-2.0, by_cell[(1, 1)]["vx_cells_s"])
        self.assertEqual(0.0, by_cell[(2, 0)]["speed_cells_s"])
        self.assertNotIn((3, 0), by_cell)

    def test_velocity_field_keeps_unavailable_scale_grid_only_and_does_not_fill_gaps(self) -> None:
        field = velocity_vector_field([
            {"time_s": 5.1, "x": 2, "y": 3, "vx_cells_s": 1.0, "vy_cells_s": 0.0, "validity": "valid_step_gap"},
        ], sampling_window_s=2.5, physical_scale=resolve_analysis_contract()["physical_scale"])
        self.assertEqual("diagnostic_grid_space_only", field["status"])
        self.assertEqual(1, len(field["records"]))
        self.assertEqual(2, field["records"][0]["window_index"])
        self.assertEqual("NA", field["records"][0]["speed_m_s"])

    def test_stationary_people_have_zero_speed_while_voronoi_density_still_exists(self) -> None:
        density = voronoi_density_for_frame(
            [{"person_id": 1, "x": 0, "y": 0}, {"person_id": 2, "x": 1, "y": 0}],
            grid=self.grid([["free", "free"]]),
            physical_scale=self.explicit_scale(),
        )
        velocity = velocity_vector_field([
            {"time_s": 1.0, "x": 0, "y": 0, "vx_cells_s": 0.0, "vy_cells_s": 0.0, "validity": "valid"},
            {"time_s": 1.0, "x": 1, "y": 0, "vx_cells_s": 0.0, "vy_cells_s": 0.0, "validity": "valid"},
        ], sampling_window_s=2.5, physical_scale=self.explicit_scale())
        self.assertEqual("available", density["status"])
        self.assertTrue(all(sample["density_persons_m2"] > 0 for sample in density["samples"]))
        self.assertTrue(all(record["speed_cells_s"] == 0 for record in velocity["records"]))

    def test_velocity_field_json_is_written_from_kinematics_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            kinematics_path = root / "trajectory_kinematics.csv"
            with kinematics_path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=[
                    "time_s", "x", "y", "vx_cells_s", "vy_cells_s", "validity"
                ])
                writer.writeheader()
                writer.writerow({"time_s": 0.5, "x": 1, "y": 2, "vx_cells_s": 2, "vy_cells_s": 0, "validity": "valid"})
            summary = write_velocity_vector_field(
                kinematics_path=kinematics_path,
                output_path=root / "velocity_vector_field.json",
                analysis_contract=resolve_analysis_contract(),
            )
            self.assertEqual("diagnostic_grid_space_only", summary["status"])
            self.assertTrue((root / "velocity_vector_field.json").is_file())
            self.assertTrue((root / "velocity_vector_field.csv").is_file())


if __name__ == "__main__":
    unittest.main()
