from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from experiments.congestion_level import (
    congestion_level_field,
    resolve_congestion_level_contract,
    write_congestion_level_field,
)


def grid(width: int = 7, height: int = 7) -> dict[str, object]:
    return {
        "width": width,
        "height": height,
        "cell_type": [["free" for _ in range(width)] for _ in range(height)],
    }


def row(x: int, y: int, vx: float, vy: float, *, time_s: float = 0.5) -> dict[str, object]:
    return {
        "time_s": time_s, "x": x, "y": y,
        "vx_cells_s": vx, "vy_cells_s": vy, "validity": "valid",
    }


def contract(*, mesh: float = 1.0, radius: float = 2.1, min_cells: int = 3) -> dict[str, object]:
    physical = {"source": "explicit_map", "value": 1.0, "unit": "m"}
    return {
        "physical_scale": physical,
        "sampling_window_s": {"source": "literature_reference", "value": 2.5, "unit": "s"},
        "congestion_level": resolve_congestion_level_contract(
            physical_scale=physical,
            map_analysis={"congestion_level": {
                "analysis_mesh_size_m": mesh,
                "roi_radius_m": radius,
                "minimum_valid_roi_cells": min_cells,
                "minimum_velocity_samples": 1,
                "parameter_source": "literature_reference",
            }},
        ),
    }


class CongestionLevelTests(unittest.TestCase):
    def uniform_rows(self, *, vx: float = 1.0, vy: float = 0.0) -> list[dict[str, object]]:
        return [row(x, y, vx, vy) for y in range(7) for x in range(7)]

    def valid_cls(self, result: dict[str, object]) -> list[float]:
        return [
            record["cl_m_inv"] for record in result["records"]
            if record["validity"] == "valid"
        ]

    def test_contract_keeps_mesh_roi_and_literature_provenance_explicit(self) -> None:
        settings = contract()["congestion_level"]
        self.assertEqual("configured", settings["status"])
        self.assertEqual(1.0, settings["analysis_mesh_size_m"]["value"])
        self.assertEqual("literature_reference", settings["analysis_mesh_size_m"]["source"])
        self.assertEqual(2.1, settings["roi"]["radius_m"]["value"])

    def test_uniform_flow_has_zero_curl_and_zero_cl(self) -> None:
        result = congestion_level_field(self.uniform_rows(), grid=grid(), analysis_contract=contract())
        valid = self.valid_cls(result)
        curls = [record["curl_z_s_inv"] for record in result["records"] if record["validity"] == "valid"]
        self.assertEqual("available", result["status"])
        self.assertTrue(valid)
        self.assertTrue(all(abs(value) < 1e-12 for value in valid))
        self.assertTrue(all(abs(value) < 1e-12 for value in curls))

    def test_uniform_rotation_has_nonzero_curl_but_near_zero_cl_variation(self) -> None:
        # v=(-y, x) has uniform curl_z=2 in this unit mesh; uniform rotation
        # alone therefore has no curl range inside any supported ROI.
        rows = [row(x, y, -(y - 3), x - 3) for y in range(7) for x in range(7)]
        result = congestion_level_field(rows, grid=grid(), analysis_contract=contract())
        valid = self.valid_cls(result)
        curls = [record["curl_z_s_inv"] for record in result["records"] if record["validity"] == "valid"]
        self.assertTrue(curls)
        self.assertTrue(all(abs(value - 2.0) < 1e-12 for value in curls))
        self.assertTrue(all(abs(value) < 1e-12 for value in valid))

    def test_crossing_direction_change_has_higher_cl_than_uniform_flow(self) -> None:
        uniform = congestion_level_field(self.uniform_rows(), grid=grid(), analysis_contract=contract())
        crossing_rows = [
            row(x, y, 1.0 if y <= 2 else -1.0, 0.0)
            for y in range(7) for x in range(7)
        ]
        crossing = congestion_level_field(crossing_rows, grid=grid(), analysis_contract=contract())
        self.assertGreater(max(self.valid_cls(crossing)), max(self.valid_cls(uniform)))

    def test_bottleneck_like_local_detour_raises_cl_above_uniform(self) -> None:
        uniform = congestion_level_field(self.uniform_rows(), grid=grid(), analysis_contract=contract())
        detour_rows = [
            row(x, y, 1.0, 1.0 if 2 <= x <= 4 and y == 3 else 0.0)
            for y in range(7) for x in range(7)
        ]
        detour = congestion_level_field(detour_rows, grid=grid(), analysis_contract=contract())
        self.assertGreater(max(self.valid_cls(detour)), max(self.valid_cls(uniform)))

    def test_missing_neighbour_is_na_not_zero_filled(self) -> None:
        rows = [
            row(1, 1, 1.0, 0.0), row(2, 1, 1.0, 0.0), row(1, 0, 1.0, 0.0),
            # (0, 1) is deliberately missing: central curl has no west support.
            row(1, 2, 1.0, 0.0),
        ]
        result = congestion_level_field(rows, grid=grid(3, 3), analysis_contract=contract(radius=1.0, min_cells=1))
        centre = next(record for record in result["records"] if record["analysis_x"] == 1 and record["analysis_y"] == 1)
        self.assertEqual("insufficient_velocity_support", centre["validity"])
        self.assertEqual("NA", centre["curl_z_s_inv"])

    def test_static_crowd_returns_near_zero_mean_speed_not_infinite_cl(self) -> None:
        result = congestion_level_field(self.uniform_rows(vx=0.0, vy=0.0), grid=grid(), analysis_contract=contract())
        interior = [record for record in result["records"] if record["curl_z_s_inv"] != "NA"]
        self.assertTrue(interior)
        self.assertTrue(all(record["validity"] == "near_zero_mean_speed" for record in interior))
        self.assertTrue(all(record["cl_m_inv"] == "NA" for record in interior))

    def test_walls_and_outside_domain_are_not_velocity_or_curl_support(self) -> None:
        blocked = grid(3, 3)
        blocked["cell_type"][1][0] = "wall"
        blocked["cell_type"][0][1] = "obstacle"
        result = congestion_level_field(
            [row(x, y, 1.0, 0.0) for y in range(3) for x in range(3) if (x, y) not in {(0, 1), (1, 0)}],
            grid=blocked, analysis_contract=contract(radius=1.0, min_cells=1),
        )
        centre = next(record for record in result["records"] if record["analysis_x"] == 1 and record["analysis_y"] == 1)
        self.assertEqual("insufficient_velocity_support", centre["validity"])

    def test_unavailable_physical_scale_has_no_formal_cl_and_writer_keeps_reason(self) -> None:
        unavailable = {
            "physical_scale": {"source": "unavailable", "value": None, "unit": "m"},
            "sampling_window_s": {"source": "literature_default", "value": 2.5, "unit": "s"},
            "congestion_level": resolve_congestion_level_contract(
                physical_scale={"source": "unavailable", "value": None, "unit": "m"}
            ),
        }
        result = congestion_level_field(self.uniform_rows(), grid=grid(), analysis_contract=unavailable)
        self.assertEqual("unavailable_physical_scale", result["status"])
        self.assertEqual([], result["records"])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "kinematics.csv"
            with source.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(self.uniform_rows()[0]))
                writer.writeheader(); writer.writerows(self.uniform_rows())
            summary = write_congestion_level_field(
                kinematics_path=source, grid=grid(), analysis_contract=unavailable,
                json_path=root / "congestion_level_field.json", csv_path=root / "congestion_level_field.csv",
            )
            payload = json.loads((root / "congestion_level_field.json").read_text(encoding="utf-8"))
            self.assertEqual("unavailable_physical_scale", summary["status"])
            self.assertIn("explicit physical_cell_size_m", payload["reason"])
            self.assertTrue((root / "congestion_level_field.csv").is_file())


if __name__ == "__main__":
    unittest.main()
