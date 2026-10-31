from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

import numpy as np

from core.schema import CellType, Relation
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from scenarios.mock_data import build_base_scene
from simulation.evac_simulation import EvacEngine
from visualization.ca_snapshot_adapter import (
    CaSnapshotAdapter,
    SnapshotAdapterError,
    _matrix_to_lists,
)


class MatrixToListsTests(unittest.TestCase):
    def test_numpy_real_matrices_preserve_shape_values_and_float_output(self) -> None:
        for dtype in (np.bool_, np.int32, np.uint16, np.float32, np.float64):
            with self.subTest(dtype=dtype):
                matrix = np.asarray([[0, 1], [2, 3]], dtype=dtype)
                result = _matrix_to_lists(
                    matrix, width=2, height=2, field_name="test_field"
                )
                self.assertEqual(matrix.astype(float).tolist(), result)
                self.assertTrue(
                    all(isinstance(value, float) for row in result for value in row)
                )

    def test_none_and_generic_numeric_rows_keep_existing_output_contract(self) -> None:
        self.assertEqual(
            [],
            _matrix_to_lists(None, width=2, height=2, field_name="test_field"),
        )
        self.assertEqual(
            [[1.0, 2.5], [3.0, 4.0]],
            _matrix_to_lists(
                [[1, 2.5], [np.int64(3), np.float32(4)]],
                width=2,
                height=2,
                field_name="test_field",
            ),
        )

    def test_rejects_numpy_and_generic_non_finite_values(self) -> None:
        for value in (np.nan, np.inf, -np.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    SnapshotAdapterError, "finite numeric values"
                ):
                    _matrix_to_lists(
                        np.asarray([[0.0, value]], dtype=np.float64),
                        width=2,
                        height=1,
                        field_name="test_field",
                    )
                with self.assertRaisesRegex(
                    SnapshotAdapterError, "finite numeric values"
                ):
                    _matrix_to_lists(
                        [[0.0, value]],
                        width=2,
                        height=1,
                        field_name="test_field",
                    )

    def test_rejects_wrong_shape_with_existing_error_contract(self) -> None:
        with self.assertRaisesRegex(
            SnapshotAdapterError,
            r"test_field shape must be \(2, 2\), got \(2, 3\)",
        ):
            _matrix_to_lists(
                np.zeros((2, 3), dtype=np.float32),
                width=2,
                height=2,
                field_name="test_field",
            )

    def test_non_matrix_numpy_dimensions_keep_generic_type_error(self) -> None:
        for invalid in (np.asarray(1.0), np.asarray([1.0, 2.0])):
            with self.subTest(shape=invalid.shape):
                with self.assertRaises(TypeError):
                    _matrix_to_lists(
                        invalid,
                        width=2,
                        height=1,
                        field_name="test_field",
                    )

    def test_object_complex_and_non_numeric_values_use_strict_generic_path(self) -> None:
        object_matrix = np.asarray([[1, np.float32(2.5)]], dtype=object)
        self.assertEqual(
            [[1.0, 2.5]],
            _matrix_to_lists(
                object_matrix, width=2, height=1, field_name="test_field"
            ),
        )
        for invalid in (
            np.asarray([[1 + 2j]], dtype=np.complex64),
            np.asarray([["not-a-number"]], dtype=object),
        ):
            with self.subTest(dtype=invalid.dtype):
                with self.assertRaisesRegex(
                    SnapshotAdapterError, "only numeric values"
                ):
                    _matrix_to_lists(
                        invalid, width=1, height=1, field_name="test_field"
                    )


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
            next_snapshot["fields"]["smoke_field"],
            self.simulation.smoke_matrix.tolist(),
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

    def test_normalizes_numpy_scalars_for_browser_and_csv_boundaries(self) -> None:
        person = self.simulation.persons[1]
        person.risk = np.float32(0.25)
        person.dose = np.float64(1.5)
        person.info_source_history = [np.int64(7)]

        snapshot = self.adapter.capture(self.simulation)
        captured = next(item for item in snapshot["people"] if item["person_id"] == 1)

        self.assertIsInstance(captured["risk"], float)
        self.assertIsInstance(captured["dose"], float)
        self.assertEqual([7], captured["info_source_history"])
        json.dumps(snapshot, ensure_ascii=False)

    def test_reads_b_runtime_smoke_sources_for_browser_overlay(self) -> None:
        self.simulation._engine.smoke_sources = [
            SimpleNamespace(x=2, y=3, intensity=np.float32(1.5))
        ]

        snapshot = self.adapter.capture(self.simulation)

        self.assertEqual(
            [{"x": 2, "y": 3, "intensity": 1.5}],
            snapshot["fields"]["smoke_sources"],
        )

    def test_rejects_non_row_major_grid(self) -> None:
        self.simulation.grid.cells[0], self.simulation.grid.cells[1] = (
            self.simulation.grid.cells[1], self.simulation.grid.cells[0]
        )
        with self.assertRaisesRegex(SnapshotAdapterError, "dense row-major order"):
            self.adapter.capture(self.simulation)

    def test_new_adapter_rebuilds_static_grid_for_a_new_runtime_map(self) -> None:
        first = self.adapter.capture(self.simulation)
        self.assertEqual("wall", first["grid"]["cell_type"][0][0])

        # A map import starts a new D runtime and therefore a new adapter.
        # Its cache must be instance-local rather than a cross-map global.
        self.simulation.grid.cells[0].cell_type = CellType.FREE
        reimported_adapter = CaSnapshotAdapter(run_id="reimported", time_step_s=0.5)
        second = reimported_adapter.capture(self.simulation)
        self.assertEqual("free", second["grid"]["cell_type"][0][0])

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
