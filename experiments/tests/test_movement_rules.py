"""文献化移动规则单元测试（恐慌阈值 / 加速 / 多出口 / 危险感知 / 抢格）"""

from __future__ import annotations

import unittest

import numpy as np

from core.schema import Cell, CellType, Exit, Grid, Person, Relation, RelationType, ScenarioConfig
from simulation.conflict_solver import conflict_priority, resolve_conflict
from simulation.danger_perception import DangerPerceptionModel
from simulation.exit_choice import ExitChooser
from simulation.floor_field import FloorField
from simulation.movement_config import MovementConfig
from simulation.panic_model import PanicModel
from simulation.speed_model import SpeedModel


def make_grid(width: int = 12, height: int = 8, exits=((11, 2), (11, 6))) -> Grid:
    cells = []
    exit_set = {(int(x), int(y)) for x, y in exits}
    for y in range(height):
        for x in range(width):
            if (x, y) in exit_set:
                cell_type = CellType.EXIT
            elif x in (0, width - 1) or y in (0, height - 1):
                cell_type = CellType.WALL
            else:
                cell_type = CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))
    return Grid(width=width, height=height, cell_size=0.5, cells=cells)


class PanicModelTests(unittest.TestCase):
    def test_smoke_pushes_person_over_threshold_and_gives_1_2_speed(self) -> None:
        model = PanicModel(MovementConfig().panic, time_step_s=0.5)
        person = Person(id=1, x=2, y=2, info_state="INFORMED")
        smoke = np.full((5, 5), 0.9, dtype=np.float32)
        for _ in range(6):
            model.update([person], smoke, {1: 1.0}, {(2, 2): 1})
        self.assertTrue(person.is_panicked)
        self.assertGreaterEqual(person.panic, MovementConfig().panic.threshold)
        self.assertAlmostEqual(person.speed_multiplier, 1.2, places=6)

    def test_clean_air_keeps_person_calm(self) -> None:
        model = PanicModel(MovementConfig().panic, time_step_s=0.5)
        person = Person(id=1, x=2, y=2, info_state="INFORMED")
        smoke = np.zeros((5, 5), dtype=np.float32)
        for _ in range(10):
            model.update([person], smoke, {1: 0.0}, {(2, 2): 1})
        self.assertFalse(person.is_panicked)
        self.assertLess(person.panic, 1e-6)

    def test_paper_threshold_is_half(self) -> None:
        import random

        cfg = MovementConfig().panic
        low_model = PanicModel(cfg, time_step_s=0.5, rng=random.Random(2))
        high_model = PanicModel(cfg, time_step_s=0.5, rng=random.Random(2))
        low = Person(id=1, x=2, y=2, info_state="INFORMED")
        high = Person(id=2, x=2, y=2, info_state="INFORMED")
        low_model.reset([low])
        high_model.reset([high])
        low_field = np.full((5, 5), 0.2, dtype=np.float32)
        high_field = np.full((5, 5), 0.4, dtype=np.float32)
        for _ in range(20):
            low_model.update([low], low_field, {1: 0.2}, {(2, 2): 1})
            high_model.update([high], high_field, {2: 0.4}, {(2, 2): 2})
        self.assertFalse(low.is_panicked)
        self.assertTrue(high.is_panicked)


class DangerModelPaperTests(unittest.TestCase):
    def test_impatience_rises_when_blocked_and_stays_low_when_moving(self) -> None:
        cfg = MovementConfig().danger
        model = DangerPerceptionModel(cfg, time_step_s=0.5, cell_size=0.5)
        blocked = Person(id=1, x=2, y=2, speed=1.0, info_state="INFORMED")
        mover = Person(id=2, x=2, y=2, speed=1.0, info_state="INFORMED")
        model.reset([blocked, mover])
        for step in range(12):
            model.update([blocked], {1: 0.0}, {1: True})
            mover.x = 2 + step  # 每步 1 格 = 1 m/s，正好等于期望速度
            model.update([mover], {2: 0.0}, {2: False})
        self.assertGreater(blocked.danger_pressure, 0.5)
        self.assertTrue(blocked.danger_urgent)
        self.assertLess(mover.danger_pressure, 0.3)


class SpeedModelTests(unittest.TestCase):
    def test_accelerates_and_accumulates_distance(self) -> None:
        cfg = MovementConfig()
        cfg.speed.accel_model = "constant"
        cfg.speed.accel = 1.2
        cfg.speed.accel_sd = 0.0
        cfg.speed.initial_velocity_ratio = 0.0
        cfg.speed.initial_credit = 0.0
        model = SpeedModel(cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5)
        person = Person(id=1, x=1, y=1, speed=1.0, info_state="INFORMED")
        first = model.update(person, 0.0, 0.0, 1, False)
        self.assertFalse(first["allow_move"])
        self.assertAlmostEqual(first["velocity"], 0.6, places=6)  # a*dt = 1.2*0.5
        second = model.update(person, 0.0, 0.0, 1, False)
        self.assertTrue(second["allow_move"])

    def test_xiong_acceleration_is_sampled_from_empirical_distribution(self) -> None:
        import random

        cfg = MovementConfig()
        cfg.speed.accel_model = "xiong2025"
        cfg.speed.initial_velocity_ratio = 0.0
        cfg.speed.initial_credit = 0.0
        model = SpeedModel(
            cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5, rng=random.Random(7)
        )
        values = []
        for index in range(60):
            person = Person(id=index + 1, x=1, y=1, speed=1.0, info_state="INFORMED")
            model.update(person, 0.0, 0.0, 1, False)
            values.append(float(person.accel_current))
        # Xiong 2025 Table 1：v≈0.5~1.0 时 μA≈1.30~1.43，σA≈0.60~0.73
        mean = sum(values) / len(values)
        self.assertGreater(mean, 0.9)
        self.assertLess(mean, 1.9)
        self.assertGreater(max(values), 2.0)

    def test_xiong_deceleration_distance_formula(self) -> None:
        self.assertAlmostEqual(SpeedModel.deceleration_distance(0.0), 0.0, places=6)
        self.assertAlmostEqual(SpeedModel.deceleration_distance(5.0), 10.0, places=6)
        self.assertAlmostEqual(SpeedModel.deceleration_distance(1.0), 10.0 - 80.0 ** 0.5, places=6)

    def test_approaching_slower_person_decelerates_before_contact(self) -> None:
        import random

        cfg = MovementConfig()
        cfg.speed.accel_model = "xiong2025"
        cfg.speed.initial_velocity_ratio = 1.0
        cfg.speed.initial_credit = 0.0
        model = SpeedModel(
            cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5, rng=random.Random(3)
        )
        person = Person(id=1, x=1, y=1, speed=1.2, info_state="INFORMED")
        state = model.update(
            person, 0.0, 0.0, 1, False, free_distance_m=0.3, leader_speed=0.5
        )
        self.assertAlmostEqual(state["target_velocity"], 0.5, places=6)
        self.assertLess(state["velocity"], 1.2)

    def test_panic_speed_gain_is_applied(self) -> None:
        cfg = MovementConfig()
        model = SpeedModel(cfg.speed, movement=cfg, time_step_s=0.5, cell_size=0.5)
        person = Person(id=1, x=1, y=1, speed=1.0, info_state="INFORMED")
        calm = model.update(person, 0.0, 0.0, 1, False)["target_velocity"]
        person.is_panicked = True
        panicked = model.update(person, 0.0, 0.0, 1, False)["target_velocity"]
        self.assertAlmostEqual(panicked / calm, 1.2, places=5)


class DangerPerceptionTests(unittest.TestCase):
    def test_blocked_person_in_smoke_accumulates_pressure(self) -> None:
        cfg = MovementConfig().danger
        model = DangerPerceptionModel(cfg, time_step_s=0.5)
        person = Person(id=1, x=2, y=2, info_state="INFORMED")
        for _ in range(8):
            model.update([person], {1: 1.0}, {1: True})
        self.assertTrue(person.danger_urgent)
        self.assertGreaterEqual(person.danger_pressure, cfg.threshold)


class ConflictTests(unittest.TestCase):
    def test_faster_panicked_person_wins_the_cell(self) -> None:
        cfg = MovementConfig()
        cfg.conflict.tie_noise = 0.0
        cfg.conflict.friction_enabled = False
        fast = Person(id=1, x=1, y=1, speed=1.8, info_state="INFORMED")
        fast.panic = 0.9
        fast.is_panicked = True
        slow = Person(id=2, x=1, y=3, speed=0.6, info_state="INFORMED")
        people = {1: fast, 2: slow}
        result = resolve_conflict(
            {1: (2, 2), 2: (2, 2)}, people, __import__("random").Random(0),
            priorities={1: conflict_priority(fast, cfg.conflict),
                        2: conflict_priority(slow, cfg.conflict)},
            config=cfg.conflict,
        )
        self.assertEqual(result[1], (2, 2))
        self.assertEqual(result[2], (1, 3))

    def test_stationary_occupant_keeps_cell_and_mover_loses(self) -> None:
        cfg = MovementConfig()
        cfg.conflict.tie_noise = 0.0
        cfg.conflict.friction_enabled = False
        stationary = Person(id=1, x=5, y=3, speed=1.0, info_state="UNKNOWN")
        mover = Person(id=2, x=4, y=3, speed=1.8, info_state="INFORMED")
        result = resolve_conflict(
            {1: (5, 3), 2: (5, 3)}, {1: stationary, 2: mover},
            __import__("random").Random(0), config=cfg.conflict,
        )
        self.assertEqual(result[1], (5, 3))
        self.assertEqual(result[2], (4, 3))

    def test_loser_does_not_get_stacked_when_winner_moves_into_its_cell(self) -> None:
        """A 想进 B 的格子、B 抢格失败留在原地：A 必须被挡下，不能两人叠格。"""
        cfg = MovementConfig()
        cfg.conflict.tie_noise = 0.0
        cfg.conflict.friction_enabled = False
        a = Person(id=1, x=2, y=2, speed=1.0, info_state="INFORMED")
        b = Person(id=2, x=3, y=2, speed=1.0, info_state="INFORMED")
        c = Person(id=3, x=5, y=2, speed=1.8, info_state="INFORMED")
        people = {1: a, 2: b, 3: c}
        result = resolve_conflict(
            {1: (3, 2), 2: (3, 3), 3: (3, 3)}, people,
            __import__("random").Random(0),
            priorities={1: 1.0, 2: 0.5, 3: 2.0},
            config=cfg.conflict,
        )
        self.assertEqual(result[1], (2, 2))
        self.assertEqual(result[2], (3, 2))
        self.assertEqual(result[3], (3, 3))

    def test_speed_and_panic_add_up(self) -> None:
        cfg = MovementConfig().conflict
        calm_fast = Person(id=1, x=0, y=0, speed=1.8)
        slow_panic = Person(id=2, x=0, y=0, speed=0.6)
        slow_panic.panic = 0.9
        self.assertGreater(
            conflict_priority(calm_fast, cfg), conflict_priority(slow_panic, cfg)
        )
        panic_fast = Person(id=3, x=0, y=0, speed=1.8)
        panic_fast.panic = 0.9
        self.assertGreater(
            conflict_priority(panic_fast, cfg), conflict_priority(calm_fast, cfg)
        )

    def test_xiong_friction_can_stop_both_competitors(self) -> None:
        import random

        cfg = MovementConfig()
        cfg.conflict.friction_enabled = True
        cfg.conflict.friction_m = 1.0
        cfg.conflict.friction_vmax = 5.0
        a = Person(id=1, x=1, y=1, speed=4.5, info_state="INFORMED")
        b = Person(id=2, x=1, y=3, speed=4.5, info_state="INFORMED")
        # rng.Random(1).random() ≈ 0.134 < μ1=(4.5/5)=0.9 → 双方都进不去
        result = resolve_conflict(
            {1: (2, 2), 2: (2, 2)}, {1: a, 2: b}, random.Random(1), config=cfg.conflict
        )
        self.assertEqual(result[1], (1, 1))
        self.assertEqual(result[2], (1, 3))

    def test_xiong_speed_exponent_favors_faster_person(self) -> None:
        import random

        rng = random.Random(5)
        cfg = MovementConfig()
        cfg.conflict.friction_enabled = True
        cfg.conflict.friction_m = 1.0
        cfg.conflict.friction_vmax = 1000.0  # μ1≈0，只看 μ2
        cfg.conflict.speed_exponent_k = 8.0
        wins = 0
        for _ in range(20):
            fast = Person(id=1, x=1, y=1, speed=2.0, info_state="INFORMED")
            slow = Person(id=2, x=1, y=3, speed=1.0, info_state="INFORMED")
            result = resolve_conflict(
                {1: (2, 2), 2: (2, 2)}, {1: fast, 2: slow}, rng, config=cfg.conflict
            )
            if result[1] == (2, 2):
                wins += 1
        self.assertGreaterEqual(wins, 15)


class ExitChoiceTests(unittest.TestCase):
    def _scene(self) -> tuple[ScenarioConfig, list[Person]]:
        grid = make_grid()
        scene = ScenarioConfig(
            scenario_id="two_exits",
            grid=grid,
            exits=[Exit(id="E_near", x=11, y=2), Exit(id="E_far", x=11, y=6)],
            persons=[],
            parameters={"time_step_s": 0.5, "random_seed": 7},
        )
        floor = FloorField(grid, scene.exits)
        floor.compute_distance_field()
        self.floor = floor
        persons = [Person(id=1, x=5, y=2, info_state="INFORMED")]
        return scene, persons

    def test_per_exit_distance_fields_are_distinct(self) -> None:
        scene, _persons = self._scene()
        self.assertLess(self.floor.distance_to("E_near", 5, 2), self.floor.distance_to("E_far", 5, 2))

    def test_preference_bonus_changes_exit_choice(self) -> None:
        scene, persons = self._scene()
        import random

        smoke = np.zeros((scene.grid.height, scene.grid.width), dtype=np.float32)
        baseline = ExitChooser(scene, random.Random(3), self.floor)
        unbias = baseline.update_all(
            persons, 0, smoke, {}, exit_preferences={1: {"E_far": 0.0}}
        )
        self.assertEqual("E_near", unbias[1])

        biased = ExitChooser(scene, random.Random(3), self.floor)
        preferred = biased.update_all(
            persons, 0, smoke, {}, exit_preferences={1: {"E_far": 0.6}}
        )
        self.assertEqual("E_far", preferred[1])

    def test_crowding_near_exit_triggers_switch(self) -> None:
        scene, persons = self._scene()
        import random

        cfg = MovementConfig().exit_choice
        cfg.sigma = 0.0
        cfg.retention = 0.0
        # 测试用小出口区域（R=1 m），否则 12×8 的小场景里两个出口会同时"看到"同一群人
        cfg.region_radius_m = 1.0
        chooser = ExitChooser(scene, random.Random(3), self.floor, cfg, 0.5)
        # 出口区域容量约 64 人（R=3.2 m、0.5 m 元胞），40 人聚集才形成明显拥挤
        crowd = [Person(id=100 + i, x=9 + (i % 2), y=1 + (i % 3), info_state="INFORMED") for i in range(40)]
        smoke = np.zeros((scene.grid.height, scene.grid.width), dtype=np.float32)

        first = chooser.update_all(persons, 0, smoke, {})
        self.assertEqual(first[1], "E_near")

        # 近端出口区域挤满人之后，按原文 ΔE≥σ 判定应换到远端出口
        switched = chooser.update_all(persons + crowd, 10, smoke, {})
        self.assertEqual(switched[1], "E_far")
        self.assertGreaterEqual(chooser.switch_total, 1)

class RelationPanicTests(unittest.TestCase):
    def _panic_with_relation(self, relation) -> float:
        import random

        cfg = MovementConfig().panic
        model = PanicModel(
            cfg, time_step_s=0.5, rng=random.Random(3), relations=[relation]
        )
        calm = Person(id=1, x=2, y=2, info_state="INFORMED")
        source = Person(id=2, x=3, y=2, info_state="INFORMED")
        # 第三个邻居保持平静且没有关系，用来提供分母权重；
        # 只有一个邻居时加权比例恒为 1，测不出关系差异。
        bystander = Person(id=3, x=2, y=3, info_state="INFORMED")
        model.reset([calm, source, bystander])
        smoke = np.zeros((5, 5), dtype=np.float32)
        positions = {(2, 2): 1, (3, 2): 2, (2, 3): 3}
        for _ in range(12):
            source.panic = 1.0
            source.is_panicked = True
            model.update([calm, source, bystander], smoke, {}, positions)
        return float(calm.panic)

    def test_family_contagion_is_stronger_than_stranger(self) -> None:
        family = Relation(
            person_a_id=1,
            person_b_id=2,
            relation_type=RelationType.FAMILY,
            strength=0.95,
            trust=0.95,
        )
        stranger = Relation(
            person_a_id=1,
            person_b_id=2,
            relation_type=RelationType.STRANGER,
            strength=0.0,
            trust=0.1,
        )
        family_panic = self._panic_with_relation(family)
        stranger_panic = self._panic_with_relation(stranger)
        self.assertGreater(family_panic, stranger_panic + 0.05)

    def test_relation_contagion_can_be_disabled(self) -> None:
        import random

        def run(relation_type) -> float:
            cfg = MovementConfig().panic
            cfg.relation_contagion = False
            relation = Relation(
                person_a_id=1,
                person_b_id=2,
                relation_type=relation_type,
                strength=0.95,
                trust=0.95,
            )
            model = PanicModel(
                cfg, time_step_s=0.5, rng=random.Random(3), relations=[relation]
            )
            calm = Person(id=1, x=2, y=2, info_state="INFORMED")
            source = Person(id=2, x=3, y=2, info_state="INFORMED")
            model.reset([calm, source])
            smoke = np.zeros((5, 5), dtype=np.float32)
            positions = {(2, 2): 1, (3, 2): 2}
            for _ in range(12):
                source.panic = 1.0
                source.is_panicked = True
                model.update([calm, source], smoke, {}, positions)
            return float(calm.panic)

        self.assertAlmostEqual(run(RelationType.FAMILY), run(RelationType.STRANGER), places=9)


class SourceHazardTests(unittest.TestCase):
    def test_source_proximity_raises_panic_without_smoke(self) -> None:
        import random

        cfg = MovementConfig().panic
        model = PanicModel(cfg, time_step_s=0.5, rng=random.Random(1))
        person = Person(id=1, x=2, y=2, info_state="INFORMED")
        model.reset([person])
        smoke = np.zeros((5, 5), dtype=np.float32)
        for _ in range(12):
            model.update(
                [person], smoke, {}, {(2, 2): 1}, source_proximity={1: 1.0}
            )
        self.assertGreater(person.panic, 0.1)

    def test_source_proximity_adds_danger_pressure(self) -> None:
        cfg = MovementConfig().danger
        model = DangerPerceptionModel(cfg, time_step_s=0.5, cell_size=0.5)
        person = Person(id=1, x=2, y=2, speed=1.0, info_state="INFORMED")
        model.reset([person])
        for _ in range(12):
            model.update([person], {1: 0.0}, {1: False}, source_proximity={1: 1.0})
        self.assertGreater(person.danger_pressure, 0.2)


if __name__ == "__main__":
    unittest.main()

