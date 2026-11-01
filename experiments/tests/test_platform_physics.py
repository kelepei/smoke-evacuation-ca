from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from experiments.crowd_metrics import resolve_analysis_contract, with_runtime_dt
from experiments.platform_physics import (
    PLATFORM_PHYSICAL_CELL_SIZE_M,
    platform_physical_cell_size,
)
from visualization.scene_input_adapter import load_map_grid


ROOT = Path(__file__).resolve().parents[2]


class PlatformPhysicsTests(unittest.TestCase):
    def assert_platform_scale(self, grid) -> None:
        contract = resolve_analysis_contract(
            map_analysis=getattr(grid, "d_map_analysis", {}),
        )
        self.assertEqual(
            "platform_simulation_assumption",
            contract["physical_scale"]["source"],
        )
        self.assertEqual(
            PLATFORM_PHYSICAL_CELL_SIZE_M,
            contract["physical_scale"]["value"],
        )
        self.assertEqual("m", contract["physical_scale"]["unit"])

    def test_platform_scale_is_fixed_and_independent_of_time_step(self) -> None:
        self.assertEqual(0.5, platform_physical_cell_size(None))
        self.assertEqual(0.5, platform_physical_cell_size(""))
        with self.assertRaisesRegex(ValueError, "fixed at 0.5"):
            platform_physical_cell_size(0.4)
        contract = with_runtime_dt(resolve_analysis_contract(), dt_s=0.25)
        self.assertEqual(0.5, contract["physical_scale"]["value"])
        self.assertEqual(0.25, contract["dt_s"]["value"])
        legacy = with_runtime_dt(
            {
                "physical_scale": {
                    "source": "unavailable",
                    "value": None,
                    "unit": "m",
                }
            },
            dt_s=0.5,
        )
        self.assertEqual(
            "platform_simulation_assumption",
            legacy["physical_scale"]["source"],
        )
        self.assertEqual(0.5, legacy["physical_scale"]["value"])

    def test_json_csv_and_png_maps_share_platform_scale(self) -> None:
        json_grid = load_map_grid(ROOT / "scenarios" / "simple_room.json")
        self.assert_platform_scale(json_grid)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            csv_path = root / "room.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["x", "y", "type"])
                for y in range(3):
                    for x in range(4):
                        writer.writerow(
                            [x, y, "exit" if (x, y) == (3, 1) else "free"]
                        )
            self.assert_platform_scale(load_map_grid(csv_path))

            png_path = root / "room.png"
            image = Image.new("L", (40, 30), color=0)
            ImageDraw.Draw(image).rectangle((0, 0, 39, 29), outline=255, width=5)
            image.save(png_path)
            self.assert_platform_scale(load_map_grid(png_path))


if __name__ == "__main__":
    unittest.main()
