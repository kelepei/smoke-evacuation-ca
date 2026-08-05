from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.integrated_runner import (
    build_integrated_scenario,
    create_integrated_runner,
)


class IntegratedRuntimeTests(unittest.TestCase):
    def _write_inputs(self, root: Path) -> tuple[Path, Path]:
        width, height = 10, 6
        cells = []
        for y in range(height):
            for x in range(width):
                cell_type = "free"
                if x in (0, width - 1) or y in (0, height - 1):
                    cell_type = "wall"
                if (x, y) == (width - 1, 3):
                    cell_type = "exit"
                if (x, y) == (2, 2):
                    cell_type = "smoke_source"
                cells.append({"x": x, "y": y, "type": cell_type})
        map_path = root / "incoming_map.json"
        map_path.write_text(
            json.dumps(
                {"width": width, "height": height, "cell_size": 0.5, "cells": cells}
            ),
            encoding="utf-8",
        )
        people_path = root / "incoming_people.json"
        people_path.write_text(
            json.dumps(
                {
                    "metadata": {"persons": 4},
                    "persons": [
                        {
                            "id": index,
                            "x": 0,
                            "y": 0,
                            "profile": "student",
                            "speed": 1.0,
                            "info_state": "UNKNOWN",
                            "evacuated": False,
                        }
                        for index in range(4)
                    ],
                    "relations": [
                        {
                            "from": 0,
                            "to": 1,
                            "relation_type": "classmate",
                            "strength": 0.6,
                            "trust": 0.7,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return map_path, people_path

    def test_build_places_placeholder_people_on_unique_free_cells(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            map_path, people_path = self._write_inputs(Path(raw))
            scenario = build_integrated_scenario(
                map_path=map_path,
                population_path=people_path,
                random_seed=42,
            )
        positions = {(person.x, person.y) for person in scenario.config.persons}
        self.assertEqual(4, len(positions))
        self.assertEqual([1, 2, 3, 4], [person.id for person in scenario.config.persons])
        self.assertEqual(1, len(scenario.config.exits))
        self.assertEqual(1, scenario.smoke_source_count)
        self.assertEqual(1, len(scenario.config.relations))
        self.assertIn("deterministic placement", scenario.placement_mode)

    def test_runner_uses_b_ca_with_wait_in_place_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            map_path, people_path = self._write_inputs(root)
            runner = create_integrated_runner(
                map_path=map_path,
                population_path=people_path,
                output_root=root / "outputs",
                run_id="integrated_test",
                random_seed=42,
                max_steps=8,
            )
            try:
                initial = runner.initialize()
                self.assertEqual(4, len(initial["people"]))
                self.assertEqual(1, len(initial["relations"]))
                self.assertEqual("A map + C population + B CA", initial["adapter_meta"]["input_mode"])
                final = runner.run_until_finished()
                self.assertGreater(final["step"], 0)
                self.assertLessEqual(final["step"], 8)
                self.assertTrue((root / "outputs" / "integrated_test" / "people_log.csv").is_file())
                self.assertTrue((root / "outputs" / "integrated_test" / "event_log.csv").is_file())
            finally:
                runner.close()


if __name__ == "__main__":
    unittest.main()
