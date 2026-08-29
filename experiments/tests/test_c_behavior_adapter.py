from __future__ import annotations

import unittest
from types import SimpleNamespace

from experiments.c_behavior_adapter import CBehaviorAdapterError, CStepBehaviorAdapter


class CStepBehaviorAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = SimpleNamespace(
            person_map={
                1: SimpleNamespace(id=1, source_person_id=0),
                2: SimpleNamespace(id=2, source_person_id=1),
            }
        )

    def test_maps_zero_based_c_ids_to_runtime_ids(self) -> None:
        adapter = CStepBehaviorAdapter(
            lambda _engine: {"group": {0: {"is_waiting": True}}, "herd": {1: {"herding_influence": 0.5}}}
        )
        self.assertEqual(
            adapter(self.engine),
            {1: {"is_waiting": True}, 2: {"herding_influence": 0.5}},
        )

    def test_rejects_unknown_person(self) -> None:
        adapter = CStepBehaviorAdapter(lambda _engine: {9: {"is_waiting": True}})
        with self.assertRaises(CBehaviorAdapterError):
            adapter(self.engine)


if __name__ == "__main__":
    unittest.main()
