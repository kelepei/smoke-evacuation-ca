from __future__ import annotations

import unittest

from core.schema import Relation
from scenarios.mock_data import build_base_scene
from simulation.evac_simulation import CaEvacSimulation
from visualization.ca_snapshot_adapter import (
    CaSnapshotAdapter,
    SnapshotAdapterError,
)


class CaSnapshotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.simulation = CaEvacSimulation(build_base_scene())
        self.simulation.init_simulation()
        self.adapter = CaSnapshotAdapter(run_id="test_run", time_step_s=0.5)

    def test_initial_and_next_snapshot_use_real_runtime_state(self) -> None:
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
        self.assertAlmostEqual(
            max(max(row) for row in next_snapshot["fields"]["smoke_field"]),
            0.08,
        )

    def test_missing_upstream_fields_remain_null(self) -> None:
        snapshot = self.adapter.capture(self.simulation)
        person = snapshot["people"][0]
        self.assertIsNone(person["heading"])
        self.assertIsNone(person["target_exit"])
        self.assertIsNone(person["risk"])
        self.assertIsNone(person["dose"])
        self.assertIsNone(person["info_state"])
        self.assertEqual(person["info_source_history"], [])

    def test_all_people_remain_in_final_snapshot(self) -> None:
        for _ in range(500):
            if self.simulation.all_done():
                break
            self.simulation.step()
        self.assertTrue(self.simulation.all_done(), "mock did not finish in 500 steps")
        snapshot = self.adapter.capture(self.simulation)
        self.assertEqual(len(snapshot["people"]), 3)
        self.assertTrue(all(person["evacuated"] for person in snapshot["people"]))

    def test_person_smoke_matches_field_at_position(self) -> None:
        self.simulation.step()
        snapshot = self.adapter.capture(self.simulation)
        smoke = snapshot["fields"]["smoke_field"]
        for person in snapshot["people"]:
            self.assertEqual(
                person["smoke_concentration"],
                smoke[person["y"]][person["x"]],
            )

    def test_rejects_non_row_major_grid(self) -> None:
        self.simulation.grid.cells[0], self.simulation.grid.cells[1] = (
            self.simulation.grid.cells[1],
            self.simulation.grid.cells[0],
        )
        with self.assertRaisesRegex(
            SnapshotAdapterError, "dense row-major order"
        ):
            self.adapter.capture(self.simulation)

    def test_rejects_non_positive_person_id(self) -> None:
        person = self.simulation.persons.pop(1)
        person.id = 0
        self.simulation.persons[0] = person
        with self.assertRaisesRegex(
            SnapshotAdapterError, "positive integer"
        ):
            self.adapter.capture(self.simulation)

    def test_rejects_mapping_key_and_person_id_mismatch(self) -> None:
        self.simulation.persons[1].id = 4
        with self.assertRaisesRegex(
            SnapshotAdapterError, "key must match person.id"
        ):
            self.adapter.capture(self.simulation)

    def test_rejects_relation_endpoint_not_in_people(self) -> None:
        self.simulation.config.relations = [Relation(1, 99)]
        with self.assertRaisesRegex(
            SnapshotAdapterError, "is not in people"
        ):
            self.adapter.capture(self.simulation)


if __name__ == "__main__":
    unittest.main()
