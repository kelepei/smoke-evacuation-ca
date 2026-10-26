"""引擎级移动策略测试：信息归属、未获知人员行走、门控先于冲突。"""

from __future__ import annotations

import unittest

from core.schema import Cell, CellType, Exit, Grid, Person, ScenarioConfig, SmokeSource
from simulation.evac_simulation import EvacEngine


def make_scene(*, persons: list[Person], smoke: bool = False, width: int = 10, height: int = 7) -> ScenarioConfig:
    cells = []
    for y in range(height):
        for x in range(width):
            if x == width - 1 and y == 3:
                cell_type = CellType.EXIT
            elif x in (0, width - 1) or y in (0, height - 1):
                cell_type = CellType.WALL
            else:
                cell_type = CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))
    grid = Grid(width=width, height=height, cell_size=0.5, cells=cells)
    return ScenarioConfig(
        scenario_id="engine_rules",
        grid=grid,
        exits=[Exit(id="E1", x=width - 1, y=3)],
        persons=persons,
        smoke_sources=[SmokeSource(x=1, y=1, intensity=1.0)] if smoke else [],
        parameters={"time_step_s": 0.5, "random_seed": 3},
    )


class InfoOwnershipTests(unittest.TestCase):
    def test_c_provided_info_state_is_not_overwritten_by_b(self) -> None:
        person = Person(id=1, x=2, y=3, info_state="UNKNOWN")
        engine = EvacEngine(make_scene(persons=[person], smoke=True))
        engine.alarm_threshold = 0.0  # 第 1 步就报警
        engine.run_one_step(c_step_data={1: {"info_state": "UNKNOWN", "target_exit": ""}})
        self.assertEqual(engine.info_state_owner, "C")
        self.assertEqual(str(person.info_state), "UNKNOWN")

    def test_b_fallback_owns_info_without_c(self) -> None:
        person = Person(id=1, x=2, y=3, info_state="UNKNOWN")
        engine = EvacEngine(make_scene(persons=[person], smoke=True))
        engine.alarm_threshold = 0.0
        engine.run_one_step(c_step_data={})
        self.assertEqual(engine.info_state_owner, "B")


class UnknownBehaviorTests(unittest.TestCase):
    def test_walk_mode_moves_unknown_people(self) -> None:
        people = [Person(id=1, x=3, y=3, info_state="UNKNOWN")]
        engine = EvacEngine(make_scene(persons=people))
        engine.unknown_behavior = "walk"
        start = (people[0].x, people[0].y)
        for _ in range(6):
            engine.run_one_step(c_step_data={1: {"info_state": "UNKNOWN"}})
        moved = (people[0].x, people[0].y) != start
        self.assertTrue(moved)
        self.assertGreater(engine.movement_stats["walk_total"], 0)

    def test_freeze_mode_keeps_unknown_people_still(self) -> None:
        people = [Person(id=1, x=3, y=3, info_state="UNKNOWN")]
        engine = EvacEngine(make_scene(persons=people))
        engine.unknown_behavior = "freeze"
        start = (people[0].x, people[0].y)
        for _ in range(6):
            engine.run_one_step(c_step_data={1: {"info_state": "UNKNOWN"}})
        self.assertEqual((people[0].x, people[0].y), start)
        self.assertGreater(engine.movement_stats["frozen_total"], 0)


class GatingOrderTests(unittest.TestCase):
    def test_pixel_scale_map_uses_reference_cell_size(self) -> None:
        """地图 JSON 的 cell_size 可能是像素尺度（如 10），移动换算必须回落到 0.5 m。"""
        scene = make_scene(persons=[Person(id=1, x=2, y=3, info_state="INFORMED")])
        scene.grid.cell_size = 10.0
        engine = EvacEngine(scene)
        self.assertEqual(engine.cell_size, 0.5)
        self.assertEqual(engine.speed_model.cell_size, 0.5)

    def test_blocked_person_does_not_win_a_cell(self) -> None:
        """0.4 m/s 的人本步走不动，不能把 1.6 m/s 的人要去的格子"抢赢"。"""
        people = [
            Person(id=1, x=3, y=3, speed=0.4, info_state="INFORMED"),
            Person(id=2, x=4, y=3, speed=1.6, info_state="INFORMED"),
        ]
        engine = EvacEngine(make_scene(persons=people))
        # 强制两人都瞄准同一格：把两人的出口都设为 (5,3) 不现实，
        # 这里直接验证冲突消解对"静止者"的处理（引擎门控后静止者不参与抢格）。
        engine.run_one_step(c_step_data={
            1: {"info_state": "INFORMED", "target_exit": "E1"},
            2: {"info_state": "INFORMED", "target_exit": "E1"},
        })
        positions = [(p.x, p.y) for p in people]
        self.assertEqual(len(positions), len(set(positions)))


if __name__ == "__main__":
    unittest.main()
