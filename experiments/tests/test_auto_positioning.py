from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from control.position_allocator import validate_allocated_positions
from experiments.auto_positioning import allocate_uploaded_positions


ROOT = Path(__file__).resolve().parents[2]
MAP_PATH = ROOT / "maps" / "edited_map.json"
PEOPLE_PATH = ROOT / "control" / "output_people_position.json"


class AutoPositioningTests(unittest.TestCase):
    def _allocate(self, root: Path, name: str, seed: int) -> dict:
        people_path = root / name
        people_path.write_text(PEOPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        result = allocate_uploaded_positions(
            map_path=MAP_PATH, people_path=people_path, random_seed=seed
        )
        payload = json.loads(people_path.read_text(encoding="utf-8"))
        self.assertEqual(40, result["person_count"])
        validate_allocated_positions(payload["persons"], json.loads(MAP_PATH.read_text(encoding="utf-8")))
        return payload

    def test_real_inputs_are_valid_and_reproducible_for_one_seed(self) -> None:
        original = json.loads(PEOPLE_PATH.read_text(encoding="utf-8"))["persons"]
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = self._allocate(root, "first.json", 42)["persons"]
            second = self._allocate(root, "second.json", 42)["persons"]

        self.assertEqual(
            [(person["id"], person["x"], person["y"]) for person in first],
            [(person["id"], person["x"], person["y"]) for person in second],
        )
        self.assertEqual(len(original), len(first))
        for before, after in zip(original, first, strict=True):
            without_positions_before = copy.deepcopy(before)
            without_positions_after = copy.deepcopy(after)
            without_positions_before.pop("x", None)
            without_positions_before.pop("y", None)
            without_positions_after.pop("x", None)
            without_positions_after.pop("y", None)
            self.assertEqual(without_positions_before, without_positions_after)

    def test_a_validator_rejects_duplicate_and_non_free_positions(self) -> None:
        map_data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
        people = [
            {"id": 1, "x": 0, "y": 0},
            {"id": 2, "x": 0, "y": 0},
        ]
        with self.assertRaisesRegex(ValueError, "不是 free|位置重复"):
            validate_allocated_positions(people, map_data)
