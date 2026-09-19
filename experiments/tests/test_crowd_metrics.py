from __future__ import annotations

import unittest

from experiments.crowd_metrics import resolve_analysis_contract, trajectory_kinematics


def row(step: int, time_s: float, x: int, y: int, *, evacuated: bool = False) -> dict[str, object]:
    return {"person_id": 1, "step": step, "time_s": time_s, "x": x, "y": y, "evacuated": evacuated}


class CrowdMetricsTests(unittest.TestCase):
    def test_cells_per_second_are_available_without_physical_scale(self) -> None:
        contract = resolve_analysis_contract()
        rows = trajectory_kinematics([row(0, 0, 0, 0), row(1, .5, 1, 0)], physical_scale=contract["physical_scale"])
        self.assertEqual("unavailable", contract["physical_scale"]["source"])
        self.assertEqual(2.0, rows[1]["speed_cells_s"])
        self.assertEqual("NA", rows[1]["speed_m_s"])

    def test_explicit_scale_and_observed_time_gap(self) -> None:
        rows = trajectory_kinematics([row(0, 0, 0, 0), row(3, 1.5, 3, 0)], physical_scale={"value": .5})
        self.assertEqual("valid_step_gap", rows[1]["validity"])
        self.assertEqual(2.0, rows[1]["vx_cells_s"])
        self.assertEqual(1.0, rows[1]["vx_m_s"])

    def test_exit_arrival_is_kept_and_repeated_evacuation_is_excluded(self) -> None:
        rows = trajectory_kinematics([row(0, 0, 0, 0), row(1, .5, 1, 0, evacuated=True), row(2, 1, 1, 0, evacuated=True)])
        self.assertEqual("valid_to_exit", rows[1]["validity"])
        self.assertEqual("excluded_post_evacuation", rows[2]["validity"])

    def test_invalid_time_and_teleport_are_flagged_without_repair(self) -> None:
        duplicate = trajectory_kinematics([row(0, 1, 0, 0), row(1, 1, 1, 0)])
        teleport = trajectory_kinematics([row(0, 0, 0, 0), row(1, .5, 3, 0)])
        self.assertEqual("invalid_nonpositive_dt", duplicate[1]["validity"])
        self.assertEqual("flagged_teleport", teleport[1]["validity"])


if __name__ == "__main__":
    unittest.main()
