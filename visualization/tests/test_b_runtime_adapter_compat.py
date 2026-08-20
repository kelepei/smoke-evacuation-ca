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
        self.smoke_matrix = [[0.0, 10.0]]
        self.current_step = 0

    def step(self) -> None:
        self.current_step += 1

    def all_done(self) -> bool:
        return False


class _RunOneStepEngine(_StepEngine):
    def __init__(self) -> None:
        super().__init__()
        self.draw_count = 0

    def draw_animation(self) -> None:
        self.draw_count += 1

    def run_one_step(self, _behavior: dict | None = None) -> None:
        self.current_step += 1
        self.draw_animation()

    def is_all_evacuated(self) -> bool:
        return False


class BRuntimeAdapterCompatibilityTests(unittest.TestCase):
    def test_supports_step_and_all_done_shape(self) -> None:
        engine = _StepEngine()
        adapter = EvacEngineRuntimeAdapter(engine)
        adapter.step()
        self.assertEqual(engine.current_step, 1)
        self.assertFalse(adapter.all_done())
        self.assertEqual(adapter.d_adapter_meta["b_runtime_api"], "EvacEngine.step()")

    def test_suppresses_duplicate_upstream_animation_by_default(self) -> None:
        engine = _RunOneStepEngine()
        adapter = EvacEngineRuntimeAdapter(engine)
        adapter.step()
        self.assertEqual(1, engine.current_step)
        self.assertEqual(0, engine.draw_count)
        engine.draw_animation()
        self.assertEqual(1, engine.draw_count)

    def test_can_opt_in_to_upstream_animation(self) -> None:
        engine = _RunOneStepEngine()
        adapter = EvacEngineRuntimeAdapter(engine, render_upstream_animation=True)
        adapter.step()
        self.assertEqual(1, engine.draw_count)


if __name__ == "__main__":
    unittest.main()
