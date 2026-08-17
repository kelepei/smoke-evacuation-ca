from __future__ import annotations

import unittest

from core.schema import Relation
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from scenarios.mock_data import build_base_scene
from simulation.evac_simulation import EvacEngine
from visualization.ca_snapshot_adapter import CaSnapshotAdapter, SnapshotAdapterError


class CaSnapshotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulation = EvacEngineRuntimeAdapter(EvacEngine(build_base_scene()))
        self.simulation.init_simulation()
        self.adapter = CaSnapshotAdapter(run_id="test_run", time_step_s=0.5)

    def test_initial_and_next_snapshot_use_public_b_runtime_state(self) -> None:
        initial = self.adapter.capture(self.simulation)
        self.assertEqual(initial["step"], 0)
        self.assertEqual(initial["time_s"], 0.0)
        self.assertEqual(len(initial["people"]), 3)
        self.assertEqual(initial["grid"]["width"], 20)
        self.assertEqual(initial["grid"]["height"], 12)
        self.assertEqual(len(initial["fields"]["smoke_field"]), 12)
        self.assertEqual(len(initial["fields"]["smoke_field"][0]), 20)

        self.simulation.step()
        next_snapshot = self.adapter.capture(self.simulation)
        self.assertEqual(next_snapshot["step"], 1)
        self.assertEqual(next_snapshot["time_s"], 0.5)
        self.assertEqual(len(next_snapshot["people"]), 3)
        self.assertEqual(
            next_snapshot["fields"]["smoke_field"], self.simulation.smoke_matrix
        )

    def test_missing_values_remain_null_and_public_evacuation_is_preserved(self) -> None:
        self.simulation.persons[1].evacuated = True
        snapshot = self.adapter.capture(self.simulation)
        person = next(person for person in snapshot["people"] if person["person_id"] == 1)
        self.assertTrue(person["evacuated"])
        self.assertIsNone(person["heading"])
        self.assertIsNone(person["target_exit"])
        self.assertIsNone(person["risk"])
        self.assertEqual(person["info_source_history"], [])
        self.assertEqual(snapshot["adapter_meta"]["private_fallbacks"], [])

    def test_person_smoke_matches_public_field_at_position(self) -> None:
        self.simulation.step()
        snapshot = self.adapter.capture(self.simulation)
        smoke = snapshot["fields"]["smoke_field"]
        for person in snapshot["people"]:
            self.assertEqual(person["smoke_concentration"], smoke[person["y"]][person["x"]])

    def test_rejects_non_row_major_grid(self) -> None:
        self.simulation.grid.cells[0], self.simulation.grid.cells[1] = (
            self.simulation.grid.cells[1], self.simulation.grid.cells[0]
        )
        with self.assertRaisesRegex(SnapshotAdapterError, "dense row-major order"):
            self.adapter.capture(self.simulation)

    def test_accepts_zero_based_person_id_and_rejects_negative_id(self) -> None:
        person = self.simulation.persons.pop(1)
        person.id = 0
        self.simulation.persons[0] = person
        snapshot = self.adapter.capture(self.simulation)
        self.assertIn(0, [person["person_id"] for person in snapshot["people"]])

        person = self.simulation.persons.pop(0)
        person.id = -1
        self.simulation.persons[-1] = person
        with self.assertRaisesRegex(SnapshotAdapterError, "non-negative integer"):
            self.adapter.capture(self.simulation)

    def test_rejects_unknown_relation(self) -> None:

        self.simulation = EvacEngineRuntimeAdapter(EvacEngine(build_base_scene()))
        self.simulation.config.relations = [Relation(1, 99)]
        with self.assertRaisesRegex(SnapshotAdapterError, "is not in people"):
            self.adapter.capture(self.simulation)


if __name__ == "__main__":
    unittest.main()
