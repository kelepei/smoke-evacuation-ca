from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from visualization.scene_input_adapter import SceneInputError, load_population_output


class PopulationOutputAdapterTests(unittest.TestCase):
    def _write(self, payload: dict) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "output_people.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_normalizes_current_c_aliases_to_unified_names(self) -> None:
        path = self._write(
            {
                "metadata": {"persons": 2},
                "persons": [
                    {"id": 1, "profile": "student", "info_state": "UNKNOWN"},
                    {"id": 2, "profile": "teacher", "info_state": "ALERTED"},
                ],
                "relations": [
                    {
                        "from": 1,
                        "to": 2,
                        "relation_type": "friend",
                        "strength": 0.7,
                        "trust": 0.8,
                    }
                ],
            }
        )
        view = load_population_output(path, source_id_base=1)
        self.assertEqual([p["person_id"] for p in view.persons], [1, 2])
        self.assertEqual([p["source_person_id"] for p in view.persons], [1, 2])
        self.assertEqual(view.relations[0]["person_a_id"], 1)
        self.assertEqual(view.relations[0]["person_b_id"], 2)
        self.assertEqual(view.relations[0]["source_person_a_id"], 1)
        self.assertEqual(view.relations[0]["source_person_b_id"], 2)
        self.assertNotIn("from", view.relations[0])

    def test_maps_c_zero_based_ids_to_d_positive_ids(self) -> None:
        path = self._write(
            {
                "persons": [{"id": 0}, {"id": 1}],
                "relations": [{"from": 0, "to": 1, "relation_type": "friend"}],
            }
        )
        view = load_population_output(path)
        self.assertEqual(view.source_id_base, 0)
        self.assertEqual([p["person_id"] for p in view.persons], [1, 2])
        self.assertEqual(view.relations[0]["person_a_id"], 1)
        self.assertEqual(view.relations[0]["person_b_id"], 2)

    def test_rejects_negative_zero_based_ids(self) -> None:
        path = self._write({"persons": [{"id": -1}], "relations": []})
        with self.assertRaisesRegex(SceneInputError, "non-negative integer"):
            load_population_output(path)

    def test_rejects_unknown_relation_endpoint(self) -> None:
        path = self._write(
            {
                "persons": [{"id": 1}],
                "relations": [{"from": 1, "to": 2, "relation_type": "friend"}],
            }
        )
        with self.assertRaisesRegex(SceneInputError, "unknown person_id"):
            load_population_output(path)


if __name__ == "__main__":
    unittest.main()
