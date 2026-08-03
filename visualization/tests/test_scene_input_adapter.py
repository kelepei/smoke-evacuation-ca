from __future__ import annotations

import csv
import tempfile
import textwrap
import unittest
from pathlib import Path

from visualization.scene_input_adapter import (
    SceneInputError,
    grid_to_static_snapshot,
    load_map_grid,
    load_population_config,
    load_population_output,
)


ROOT = Path(__file__).resolve().parents[2]


class SceneInputAdapterTests(unittest.TestCase):
    def test_json_map_loads_into_static_visualizer_snapshot(self) -> None:
        grid = load_map_grid(ROOT / "scenarios" / "simple_room.json")
        snapshot = grid_to_static_snapshot(
            grid, run_id="json_preview", scenario_id="simple_room"
        )
        self.assertEqual(snapshot["grid"]["width"], 20)
        self.assertEqual(snapshot["grid"]["height"], 20)
        self.assertEqual(len(snapshot["grid"]["cell_type"]), 20)
        self.assertEqual(snapshot["people"], [])
        self.assertTrue(snapshot["adapter_meta"]["preview_only"])

    def test_csv_map_loads_and_sparse_csv_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            good = root / "room.csv"
            with good.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["x", "y", "type"])
                for y in range(2):
                    for x in range(3):
                        writer.writerow([x, y, "exit" if (x, y) == (2, 1) else "free"])
            grid = load_map_grid(good)
            self.assertEqual((grid.width, grid.height), (3, 2))

            sparse = root / "sparse.csv"
            sparse.write_text(
                "x,y,type\n"
                "0,0,wall\n1,0,free\n2,0,free\n"
                "0,1,free\n2,1,exit\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(SceneInputError, r"6.*5"):
                load_map_grid(sparse)

    def test_c_yaml_loader_is_called_without_fabricating_people(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            module = root / "scene_config.py"
            module.write_text(
                textwrap.dedent(
                    """
                    from dataclasses import dataclass
                    import yaml

                    @dataclass
                    class Config:
                        scene_name: str
                        description: str
                        total_persons: int
                        profile_ratios: dict
                        relation_intensity: float
                        random_seed: int

                    class SceneConfigGenerator:
                        @staticmethod
                        def load_config_from_yaml(path):
                            with open(path, encoding='utf-8') as stream:
                                data = yaml.safe_load(stream)
                            return Config(
                                data['scene_name'], data['description'],
                                data['total_persons'], data['profile_ratios'],
                                data['relation_intensity'], data['random_seed']
                            )
                    """
                ),
                encoding="utf-8",
            )
            yaml_file = root / "config.yaml"
            yaml_file.write_text(
                "scene_name: classroom\n"
                "description: test\n"
                "total_persons: 40\n"
                "profile_ratios:\n  student: 0.9\n  teacher: 0.1\n"
                "relation_intensity: 0.6\nrandom_seed: 42\n",
                encoding="utf-8",
            )
            view = load_population_config(yaml_file, c_module_path=module)
            self.assertEqual(view.total_persons, 40)
            self.assertEqual(view.profile_ratios["student"], 0.9)
            self.assertEqual(view.random_seed, 42)
            self.assertFalse(view.has_person_output)
            self.assertFalse(view.has_relation_output)

    def test_c_output_people_is_mapped_from_zero_based_ids(self) -> None:
        view = load_population_output(ROOT / "docs" / "d_week3" / "examples" / "c_output_people.json")
        self.assertEqual(view.source_id_base, 0)
        self.assertEqual(len(view.persons), 40)
        self.assertEqual(len(view.relations), 160)
        self.assertEqual(view.persons[0]["source_person_id"], 0)
        self.assertEqual(view.persons[0]["person_id"], 1)
        self.assertEqual(view.persons[-1]["person_id"], 40)
        self.assertEqual(view.relations[0]["person_a_id"], 1)
        self.assertEqual(view.relations[0]["person_b_id"], 34)


if __name__ == "__main__":
    unittest.main()
