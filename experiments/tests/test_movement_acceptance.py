"""验收测试：烟雾阈值 / 剂量 / 拥堵 / 窄出口 / 恐慌阈值（对应 c.docx 的验收清单）。"""

from __future__ import annotations

import unittest

import numpy as np

from core.schema import Cell, CellType, Exit, Grid, Person, ScenarioConfig
from simulation.evac_simulation import EvacEngine
from simulation.movement_config import MovementConfig
from simulation.panic_model import PanicModel
from simulation.speed_model import SpeedModel


def make_grid(width: int, height: int, exit_cells: list[tuple[int, int]]) -> Grid:
    exits = set(exit_cells)
    cells = []
    for y in range(height):
        for x in range(width):
            if (x, y) in exits:
                cell_type = CellType.EXIT
            elif x in (0, width - 1) or y in (0, height - 1):
                cell_type = CellType.WALL
            else:
                cell_type = CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))
    return Grid(width=width, height=height, cell_size=0.5, cells=cells)


def make_engine(persons: list[Person], width: int, height: int, exit_cells, smoke=None) -> EvacEngine:
    from core.schema import SmokeSource

    grid = make_grid(width, height, exit_cells)
    scene = ScenarioConfig(
        scenario_id="acceptance",
        grid=grid,
        exits=[Exit(id=f"E{i + 1}", x=x, y=y) for i, (x, y) in enumerate(exit_cells)],
        persons=persons,
        smoke_sources=[SmokeSource(x=smoke[0], y=smoke[1], intensity=1.0)] if smoke else [],
        parameters={"time_step_s": 0.5, "random_seed": 11},
    )
    return EvacEngine(scene)


class SmokeThresholdTests(unittest.TestCase):
    def test_speed_decreases_with_smoke_concentration(self) -> None:
        cfg = MovementConfig()
        model = SpeedModel(cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5)
        person = Person(id=1, x=1, y=1, speed=1.2, info_state="INFORMED")
        targets = []
        for smoke in (0.05, 0.5, 0.65, 0.9):
            state = model.update(person, smoke, 0.0, 1, False)
            targets.append(state["target_velocity"])
        self.assertEqual(targets, sorted(targets, reverse=True))
        self.assertGreater(targets[0] - targets[-1], 0.2)

    def test_high_smoke_pushes_panic_over_threshold(self) -> None:
        cfg = MovementConfig()
        hot = PanicModel(cfg.panic, time_step_s=0.5)
        calm = PanicModel(cfg.panic, time_step_s=0.5)
        person_hot = Person(id=1, x=2, y=2, info_state="INFORMED")
        person_calm = Person(id=2, x=2, y=2, info_state="INFORMED")
        hot_field = np.full((5, 5), 0.9, dtype=np.float32)
        calm_field = np.full((5, 5), 0.05, dtype=np.float32)
        for _ in range(6):
            hot.update([person_hot], hot_field, {1: 1.2}, {(2, 2): 1})
            calm.update([person_calm], calm_field, {2: 0.05}, {(2, 2): 2})
        self.assertTrue(person_hot.is_panicked)
        self.assertFalse(person_calm.is_panicked)


class DoseTests(unittest.TestCase):
    def test_high_dose_slows_person(self) -> None:
        cfg = MovementConfig()
        model = SpeedModel(cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5)
        person = Person(id=1, x=1, y=1, speed=1.2, info_state="INFORMED")
        low = model.update(person, 0.0, 0.0, 1, False)["target_velocity"]
        high = model.update(person, 0.0, 20.0, 1, False)["target_velocity"]
        self.assertLess(high, low)


class CongestionTests(unittest.TestCase):
    def test_dense_3x3_never_stacks_two_people(self) -> None:
        people = []
        pid = 1
        for x in range(4, 7):
            for y in range(3, 6):
                people.append(Person(id=pid, x=x, y=y, speed=1.0, info_state="INFORMED"))
                pid += 1
        engine = make_engine(people, 12, 8, [(11, 4)])
        behavior = {p.id: {"info_state": "INFORMED", "target_exit": "E1"} for p in people}
        for _ in range(10):
            engine.run_one_step(c_step_data=behavior)
            # 已撤离人员会停留在出口格，出口格允许后续人员进入，比较时排除
            positions = [
                (int(p.x), int(p.y))
                for p in engine.person_map.values()
                if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)
            ]
            self.assertEqual(len(positions), len(set(positions)))

    def test_narrow_exit_still_evacuates(self) -> None:
        people = [
            Person(id=i + 1, x=2 + (i % 3), y=2 + (i // 3), speed=1.1, info_state="INFORMED")
            for i in range(9)
        ]
        engine = make_engine(people, 16, 6, [(15, 2)])
        behavior = {p.id: {"info_state": "INFORMED", "target_exit": "E1"} for p in people}
        for _ in range(120):
            engine.run_one_step(c_step_data=behavior)
            if engine.is_all_evacuated():
                break
        self.assertGreaterEqual(engine.get_evacuated_count(), 1)


class UnknownBehaviorAcceptanceTests(unittest.TestCase):
    def test_unknown_evacuate_mode_moves_to_exit_without_smoke(self) -> None:
        person = Person(id=1, x=2, y=3, info_state="UNKNOWN")
        engine = make_engine([person, Person(id=2, x=3, y=3, info_state="UNKNOWN")], 10, 7, [(9, 3)])
        engine.unknown_behavior = "evacuate"
        start = (person.x, person.y)
        for _ in range(20):
            engine.run_one_step(c_step_data={1: {"info_state": "UNKNOWN"}})
        self.assertNotEqual((person.x, person.y), start)


if __name__ == "__main__":
    unittest.main()
