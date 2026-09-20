from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.academic_crowd import academic_crowd_fields, write_academic_crowd_fields
from experiments.congestion_level import resolve_congestion_level_contract


def make_grid(size: int, *, offset: int = 0) -> dict[str, object]:
    # ``cell_size`` intentionally remains 10: academic fields must use only
    # the explicit 1 m scale and 1 m analysis mesh supplied below.
    return {
        "width": size, "height": size, "cell_size": 10,
        "cell_type": [["free" for _ in range(size)] for _ in range(size)],
        "offset": offset,
    }


def contract() -> dict[str, object]:
    physical = {"source": "explicit_map", "value": 1.0, "unit": "m"}
    return {
        "physical_scale": physical,
        "sampling_window_s": {"source": "literature_reference", "value": 2.5, "unit": "s"},
        "congestion_level": resolve_congestion_level_contract(
            physical_scale=physical,
            map_analysis={"congestion_level": {
                "analysis_mesh_size_m": 1.0,
                "roi_radius_m": 2.1,
                "minimum_valid_roi_cells": 3,
                "minimum_velocity_samples": 1,
                "parameter_source": "literature_reference",
            }},
        ),
    }


def people_and_kinematics(size: int, velocity, *, offset: int = 0, time_s: float = 0.5):
    people, kinematics = [], []
    person_id = 0
    for y in range(offset, offset + size):
        for x in range(offset, offset + size):
            vx, vy = velocity(x - offset, y - offset)
            people.append({"person_id": person_id, "step": 1, "time_s": time_s, "x": x, "y": y, "evacuated": False})
            kinematics.append({"person_id": person_id, "step": 1, "time_s": time_s, "x": x, "y": y, "vx_cells_s": vx, "vy_cells_s": vy, "validity": "valid"})
            person_id += 1
    return people, kinematics


class AcademicCrowdTests(unittest.TestCase):
    def _field(self, velocity, *, size: int = 7, grid_size: int | None = None, offset: int = 0, time_s: float = 0.5):
        people, kinematics = people_and_kinematics(size, velocity, offset=offset, time_s=time_s)
        return academic_crowd_fields(
            people, grid=make_grid(grid_size or size), analysis_contract=contract(), kinematic_rows=kinematics
        )

    @staticmethod
    def valid(records, field: str):
        validity = {"cn": "cn_validity", "crowd_danger_persons_m3": "crowd_danger_validity"}.get(field, "cl_validity")
        return [record[field] for record in records if record[validity] == "valid"]

    def test_uniform_unidirectional_flow_has_zero_cl_cn_and_crowd_danger(self) -> None:
        result = self._field(lambda x, y: (1.0, 0.0))
        self.assertEqual("available", result["status"])
        self.assertTrue(self.valid(result["records"], "cn"))
        self.assertTrue(all(value == 0.0 for value in self.valid(result["records"], "cn")))
        self.assertTrue(all(value == 0.0 for value in self.valid(result["records"], "crowd_danger_persons_m3")))

    def test_cn_uses_analysis_mesh_not_legacy_map_cell_size_and_is_dimensionless(self) -> None:
        result = self._field(lambda x, y: (1.0 if y <= 2 else -1.0, 0.0))
        record = next(record for record in result["records"] if record["cn_validity"] == "valid" and record["cn"] > 0)
        self.assertAlmostEqual(record["cl_m_inv"] * 1.0 / 6.0, record["cn"])
        self.assertNotAlmostEqual(record["cl_m_inv"] * 10.0 / 6.0, record["cn"])
        self.assertEqual("dimensionless", result["units"]["cn"])

    def test_crossing_flow_cn_is_higher_than_regular_flow(self) -> None:
        regular = self._field(lambda x, y: (1.0, 0.0))
        crossing = self._field(lambda x, y: (1.0 if y <= 2 else -1.0, 0.0))
        self.assertGreater(max(self.valid(crossing["records"], "cn")), max(self.valid(regular["records"], "cn")))

    def test_crowd_danger_is_cl_times_density_not_cn_times_density(self) -> None:
        result = self._field(lambda x, y: (1.0 if y <= 2 else -1.0, 0.0))
        record = next(record for record in result["records"] if record["crowd_danger_validity"] == "valid" and record["cl_m_inv"] > 0)
        self.assertAlmostEqual(record["cl_m_inv"] * record["density_persons_m2"], record["crowd_danger_persons_m3"])
        self.assertNotAlmostEqual(record["cn"] * record["density_persons_m2"], record["crowd_danger_persons_m3"])

    def test_high_density_regular_flow_is_not_high_cn(self) -> None:
        high_density_regular = self._field(lambda x, y: (1.0, 0.0))
        density = [record["density_persons_m2"] for record in high_density_regular["records"] if record["density_validity"] == "valid"]
        self.assertTrue(density and max(density) > 0)
        self.assertTrue(all(value == 0.0 for value in self.valid(high_density_regular["records"], "cn")))

    def test_high_density_disrupted_flow_has_higher_crowd_danger_than_low_density_disrupted_and_regular(self) -> None:
        high_disrupted = self._field(lambda x, y: (1.0 if y <= 2 else -1.0, 0.0))
        high_regular = self._field(lambda x, y: (1.0, 0.0))
        # Sparse positions use a larger legal domain; the separately sampled
        # velocity field still covers the same analysis frame for curl support.
        _, low_kinematics = people_and_kinematics(7, lambda x, y: (1.0 if y <= 2 else -1.0, 0.0), offset=4)
        low_people = [
            {"person_id": index, "step": 1, "time_s": 0.5, "x": x, "y": y, "evacuated": False}
            for index, (x, y) in enumerate(((5, 5), (9, 5), (5, 9), (9, 9), (7, 7)))
        ]
        low_disrupted = academic_crowd_fields(low_people, grid=make_grid(15), analysis_contract=contract(), kinematic_rows=low_kinematics)
        self.assertGreater(max(self.valid(high_disrupted["records"], "crowd_danger_persons_m3")), max(self.valid(high_regular["records"], "crowd_danger_persons_m3")))
        self.assertGreater(max(self.valid(high_disrupted["records"], "crowd_danger_persons_m3")), max(self.valid(low_disrupted["records"], "crowd_danger_persons_m3")))

    def test_same_analysis_window_is_required_for_crowd_danger(self) -> None:
        people, kinematics = people_and_kinematics(7, lambda x, y: (1.0 if y <= 2 else -1.0, 0.0))
        for record in kinematics:
            record["time_s"] = 3.0  # velocity window 1; density stays in window 0
        result = academic_crowd_fields(people, grid=make_grid(7), analysis_contract=contract(), kinematic_rows=kinematics)
        self.assertTrue(all(record["crowd_danger_validity"] != "valid" for record in result["records"]))

    def test_near_zero_speed_invalid_cl_keeps_cn_and_crowd_danger_na(self) -> None:
        result = self._field(lambda x, y: (0.0, 0.0))
        relevant = [record for record in result["records"] if record["cl_validity"] == "near_zero_mean_speed"]
        self.assertTrue(relevant)
        self.assertTrue(all(record["cn"] == "NA" and record["crowd_danger_persons_m3"] == "NA" for record in relevant))

    def test_unavailable_scale_keeps_all_formal_academic_metrics_unavailable(self) -> None:
        people, _ = people_and_kinematics(3, lambda x, y: (1.0, 0.0))
        unavailable = {"physical_scale": {"source": "unavailable", "value": None, "unit": "m"}}
        result = academic_crowd_fields(people, grid=make_grid(3), analysis_contract=unavailable)
        self.assertEqual("unavailable_physical_scale", result["status"])
        self.assertEqual([], result["records"])

    def test_artifact_writes_unavailable_reason_without_fake_rows(self) -> None:
        people, _ = people_and_kinematics(3, lambda x, y: (1.0, 0.0))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "people_log.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(people[0]))
                writer.writeheader(); writer.writerows(people)
            summary = write_academic_crowd_fields(
                people_log_path=source, grid=make_grid(3),
                analysis_contract={"physical_scale": {"source": "unavailable", "value": None, "unit": "m"}},
                json_path=root / "academic_crowd_fields.json", csv_path=root / "academic_crowd_fields.csv",
            )
            payload = json.loads((root / "academic_crowd_fields.json").read_text(encoding="utf-8"))
            self.assertEqual("unavailable_physical_scale", summary["status"])
            self.assertEqual([], payload["records"])

    def test_enhanced_ui_declares_selector_and_unavailable_no_fake_heatmap_path(self) -> None:
        html = Path("visualization/prototype/final_platform_map_editor.html").read_text(encoding="utf-8")
        self.assertIn('id="academicHeatmapSelect"', html)
        for label in ("Density", "CL", "CN", "Crowd Danger"):
            self.assertIn(label, html)
        self.assertIn("unavailable_physical_scale", html)
        self.assertIn("不会生成伪热图", html)
        self.assertIn("openFigureModal", html)


if __name__ == "__main__":
    unittest.main()
