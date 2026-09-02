from __future__ import annotations

import copy
import unittest

from experiments.auto_positioning import AutoPositioningError, allocate_map_data_positions


def _map_data() -> dict[str, object]:
    width, height = 6, 5
    cells = []
    for y in range(height):
        for x in range(width):
            cell_type = "free"
            if x in (0, width - 1) or y in (0, height - 1):
                cell_type = "wall"
            if (x, y) == (width - 1, 2):
                cell_type = "exit"
            if (x, y) == (2, 2):
                cell_type = "smoke_source"
            if (x, y) == (3, 2):
                cell_type = "obstacle"
            cells.append({"x": x, "y": y, "type": cell_type, "room_id": "r1"})
    return {"name": "edited", "width": width, "height": height, "cell_size": 0.5, "cells": cells}


def _people_data() -> dict[str, object]:
    return {
        "persons": [
            {"id": 1, "profile": "student", "group_id": "g1", "note": "preserve"},
            {"id": 2, "profile": "student", "group_id": "g1", "note": "preserve"},
            {"id": 3, "profile": "teacher", "group_id": None, "note": "preserve"},
        ],
        "relations": [{"from": 1, "to": 2, "relation_type": "classmate", "strength": 0.8}],
    }


class AutoPositioningTests(unittest.TestCase):
    def test_same_seed_is_reproducible_and_preserves_person_fields(self) -> None:
        map_data = _map_data()
        first = _people_data()
        second = copy.deepcopy(first)

        allocate_map_data_positions(map_data=map_data, people_data=first, random_seed=44)
        allocate_map_data_positions(map_data=map_data, people_data=second, random_seed=44)

        self.assertEqual(first, second)
        valid_free = {(cell["x"], cell["y"]) for cell in map_data["cells"] if cell["type"] == "free"}
        assigned = [(person["x"], person["y"]) for person in first["persons"]]
        self.assertEqual(len(assigned), len(set(assigned)))
        self.assertTrue(set(assigned).issubset(valid_free))
        self.assertEqual("preserve", first["persons"][0]["note"])
        self.assertEqual("g1", first["persons"][0]["group_id"])

    def test_edited_cell_type_is_used_as_the_allocation_constraint(self) -> None:
        map_data = _map_data()
        for cell in map_data["cells"]:
            if (cell["x"], cell["y"]) == (1, 1):
                cell["type"] = "wall"
        people_data = _people_data()

        allocate_map_data_positions(map_data=map_data, people_data=people_data, random_seed=44)

        self.assertNotIn((1, 1), {(person["x"], person["y"]) for person in people_data["persons"]})

    def test_invalid_population_shape_has_a_user_facing_error(self) -> None:
        with self.assertRaises(AutoPositioningError):
            allocate_map_data_positions(map_data=_map_data(), people_data={"persons": {}}, random_seed=44)


if __name__ == "__main__":
    unittest.main()
