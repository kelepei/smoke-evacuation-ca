from __future__ import annotations

import unittest

from core.schema import Cell, CellType, Grid, Person, ScenarioConfig
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter


class _StepEngine:
    def __init__(self) -> None:
        self.grid = Grid(
            width=2,
            height=1,
            cell_size=0.5,
            cells=[
                Cell(x=0, y=0, cell_type=CellType.FREE),
                Cell(x=1, y=0, cell_type=CellType.EXIT),
            ],
        )
        self.scene = ScenarioConfig(scenario_id="step_api", grid=self.grid)
        self.person_map = {1: Person(id=1, x=0, y=0)}
        self.smoke_matrix = [[0.0, 0.1]]
        self.current_step = 0

    def step(self) -> None:
        self.current_step += 1

    def all_done(self) -> bool:
        return False


class BRuntimeAdapterCompatibilityTests(unittest.TestCase):
    def test_supports_step_and_all_done_shape(self) -> None:
        engine = _StepEngine()
        adapter = EvacEngineRuntimeAdapter(engine)
        adapter.step()
        self.assertEqual(engine.current_step, 1)
        self.assertFalse(adapter.all_done())
        self.assertEqual(adapter.d_adapter_meta["b_runtime_api"], "EvacEngine.step()")


if __name__ == "__main__":
    unittest.main()
