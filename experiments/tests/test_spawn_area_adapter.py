from __future__ import annotations

import copy
import unittest

from experiments.spawn_area_adapter import prepare_spawn_area_map


def _map() -> dict[str, object]:
    width, height = 7, 7
    cells = []
    for y in range(height):
        for x in range(width):
            cell_type = "wall" if x in (0, width - 1) or y in (0, height - 1) else "free"
            if (x, y) == (3, 3):
                cell_type = "obstacle"
            if (x, y) == (6, 3):
                cell_type = "exit"
            cells.append({"x": x, "y": y, "type": cell_type, "semantic": "classroom"})
    return {"width": width, "height": height, "cells": cells}


class SpawnAreaAdapterTests(unittest.TestCase):
    def test_generated_mask_is_attached_only_to_the_allocation_copy(self) -> None:
        source = _map()
        original = copy.deepcopy(source)
        prepared, metadata = prepare_spawn_area_map(source)

        self.assertEqual(source, original)
        self.assertTrue(metadata["generated"])
        self.assertEqual("map_import.spawn_area.generate_spawn_mask", metadata["source"])
        allowed = {(cell["x"], cell["y"]) for cell in prepared["cells"] if cell["spawn_area"]}
        self.assertIn((1, 1), allowed)
        self.assertNotIn((0, 0), allowed)
        self.assertNotIn((3, 3), allowed)
        self.assertNotIn((6, 3), allowed)

    def test_explicit_map_annotation_is_not_overwritten(self) -> None:
        source = _map()
        source["cells"][8]["spawn_area"] = False
        prepared, metadata = prepare_spawn_area_map(source)

        self.assertFalse(metadata["generated"])
        self.assertEqual("explicit_map_spawn_area", metadata["source"])
        self.assertNotIn("spawn_area", prepared["cells"][9])


if __name__ == "__main__":
    unittest.main()
