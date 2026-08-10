"""
C09: 引导员模型 (guide_agent.py)
实现引导员对疏散过程的主动引导。

核心机制（任务书第5.2节、第5.5节）：
    1. 引导员是特殊的行人（profile: teacher/staff/security）
    2. 影响半径内的行人会受到引导（状态变为 GUIDED）
    3. 可信度影响行人是否听从引导（受社会关系影响）
    4. 引导员可以固定位置，也可以主动移动引导

职责：
    1. 定义引导员属性（位置、影响半径、可信度、移动策略）
    2. 计算引导影响力（给B组的移动概率修正）
    3. 通过 C06 将行人状态更新为 GUIDED
    4. 提供查询接口给 B 组和 D 组

依赖:
    - C03: SocialGraphBuilder (获取行人信息、关系)
    - C06: InformationStateEngine (更新状态为 GUIDED)
    - C08: SignageModel.quantize_direction (方向量化工具)
    - B组: 行人位置
"""

import numpy as np
from typing import Dict, List, Tuple
from collections import defaultdict
from enum import Enum
from types import SimpleNamespace
from .information_state import InfoState
from control.signage_model import SignageModel


# ============================================================
# 引导员移动策略
# ============================================================
class GuideMoveStrategy(Enum):
    FIXED = "fixed"
    PATROL = "patrol"
    TOWARD_EXIT = "toward_exit"
    TOWARD_CROWD = "toward_crowd"
    ESCORT = "escort"


# ============================================================
# 引导员参数
# ============================================================
GUIDE_PARAMS = {
    "influence": {
        "radius": 5.0,
        "decay_rate": 0.5,
        "base_utility": 1.0,
    },
    "trust": {
        "base_trust": 0.7,
        "relation_boost": {
            "family": 0.3,
            "friend": 0.2,
            "classmate": 0.15,
            "colleague": 0.1,
            "stranger": 0.0,
            "staff_to_customer": 0.25,
            "doctor_patient": 0.3,
        },
        "profile_boost": {
            "teacher": 0.2,
            "staff": 0.1,
            "security": 0.25,
        },
        "trust_threshold": 0.4,
    },
    "movement": {
        "strategy": "fixed",
        "speed": 1.0,
        "patrol_points": [],
        "escort_speed": 0.8,
    },
    "state": {
        "enabled": True,
        "target_state": "GUIDED",
    },
}


class GuideAgent:
    """单个引导员"""

    def __init__(self, agent_id: int, x: int, y: int,
                 profile: str = "staff",
                 move_strategy: GuideMoveStrategy = GuideMoveStrategy.FIXED):
        self.id = agent_id
        self.x = x
        self.y = y
        self.profile = profile
        self.strategy = move_strategy

        self.radius = GUIDE_PARAMS["influence"]["radius"]
        self.base_trust = GUIDE_PARAMS["trust"]["base_trust"]
        self.speed = GUIDE_PARAMS["movement"]["speed"]

        self.active = True
        self.escort_target = None
        self.patrol_index = 0
        self.path_history = [(x, y)]

        self.guided_count = 0
        self.total_guided = 0

    def set_position(self, x: int, y: int):
        self.x = x
        self.y = y
        self.path_history.append((x, y))
        if len(self.path_history) > 100:
            self.path_history = self.path_history[-100:]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "profile": self.profile,
            "strategy": self.strategy.value,
            "radius": self.radius,
            "active": self.active,
            "guided_count": self.guided_count,
            "path_history": self.path_history[-20:],
        }


class GuideAgentModel:
    """引导员模型"""

    def __init__(self, social_graph, info_engine,
                 grid_width: int, grid_height: int,
                 exits: List = None):
        self.graph = social_graph
        self.persons = social_graph.persons
        self.info_engine = info_engine
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.exits = exits or []

        self.influence_params = GUIDE_PARAMS["influence"]
        self.trust_params = GUIDE_PARAMS["trust"]
        self.movement_params = GUIDE_PARAMS["movement"]
        self.state_params = GUIDE_PARAMS["state"]

        self.guides: List[GuideAgent] = []
        self.step_stats = defaultdict(int)
        self.guide_influence_cache: Dict[int, dict] = {}

    # ============================================================
    # 引导员管理
    # ============================================================

    def add_guide(self, x: int, y: int,
                  profile: str = "staff",
                  move_strategy: GuideMoveStrategy = GuideMoveStrategy.FIXED) -> int:
        agent_id = len(self.guides)
        guide = GuideAgent(agent_id, x, y, profile, move_strategy)
        self.guides.append(guide)
        return agent_id

    def add_guide_with_patrol(self, x: int, y: int,
                              patrol_points: List[Tuple[int, int]],
                              profile: str = "staff") -> int:
        agent_id = len(self.guides)
        guide = GuideAgent(agent_id, x, y, profile, GuideMoveStrategy.PATROL)
        guide.patrol_points = patrol_points
        self.guides.append(guide)
        return agent_id

    def remove_guide(self, agent_id: int):
        for i, guide in enumerate(self.guides):
            if guide.id == agent_id:
                guide.active = False
                self.guides.pop(i)
                return True
        return False

    # ============================================================
    # 引导员移动更新
    # ============================================================

    def update_guides(self, all_persons: List,
                      exits: List = None,
                      current_step: int = 0):
        exits = exits or self.exits or []
        for guide in self.guides:
            if not guide.active:
                continue

            move_strategy = guide.strategy

            if move_strategy == GuideMoveStrategy.FIXED:
                continue

            elif move_strategy == GuideMoveStrategy.PATROL:
                self._move_patrol(guide, current_step)

            elif move_strategy == GuideMoveStrategy.TOWARD_EXIT:
                self._move_toward_exit(guide, exits, current_step)

            elif move_strategy == GuideMoveStrategy.TOWARD_CROWD:
                self._move_toward_crowd(guide, all_persons, current_step)

            elif move_strategy == GuideMoveStrategy.ESCORT:
                self._move_escort(guide, all_persons, exits, current_step)

    @staticmethod
    def _move_toward_point(guide: GuideAgent, target_x: int, target_y: int, speed_scale: float = 1.0):
        dx = target_x - guide.x
        dy = target_y - guide.y
        dist = (dx ** 2 + dy ** 2) ** 0.5

        if dist < 0.5:
            return

        step = min(guide.speed * speed_scale, dist)
        if dist > 0:
            guide.x += int(dx / dist * step)
            guide.y += int(dy / dist * step)

    def _move_patrol(self, guide: GuideAgent, _current_step: int):
        patrol_points = getattr(guide, 'patrol_points', [])
        if not patrol_points:
            return

        target_x, target_y = patrol_points[guide.patrol_index]
        self._move_toward_point(guide, target_x, target_y)

        dist = ((target_x - guide.x) ** 2 + (target_y - guide.y) ** 2) ** 0.5
        if dist < 0.5:
            guide.patrol_index = (guide.patrol_index + 1) % len(patrol_points)

    def _move_toward_exit(self, guide: GuideAgent, exits: List, _current_step: int):
        if not exits:
            return

        nearest_exit = min(exits, key=lambda e: (e[1] - guide.x) ** 2 + (e[2] - guide.y) ** 2)
        _, target_x, target_y = nearest_exit
        self._move_toward_point(guide, target_x, target_y)

    def _move_toward_crowd(self, guide: GuideAgent, all_persons: List, _current_step: int):
        if not all_persons:
            return

        center_x = np.mean([p.x for p in all_persons])
        center_y = np.mean([p.y for p in all_persons])
        self._move_toward_point(guide, int(center_x), int(center_y))

    def _move_escort(self, guide: GuideAgent, all_persons: List, exits: List, _current_step: int):
        if not all_persons or not exits:
            return

        nearest_person = min(all_persons, key=lambda p: (p.x - guide.x) ** 2 + (p.y - guide.y) ** 2)
        dist_to_person = ((nearest_person.x - guide.x) ** 2 + (nearest_person.y - guide.y) ** 2) ** 0.5

        if dist_to_person > 3.0:
            self._move_toward_point(guide, nearest_person.x, nearest_person.y, 0.8)
        else:
            nearest_exit = min(exits, key=lambda e: (e[1] - guide.x) ** 2 + (e[2] - guide.y) ** 2)
            _, exit_x, exit_y = nearest_exit
            self._move_toward_point(guide, exit_x, exit_y, 0.6)

    # ============================================================
    # 引导影响力计算
    # ============================================================

    def update_all(self, all_persons: List, current_step: int) -> Dict[int, dict]:
        results = {}

        for person in all_persons:
            pid = person.id
            result = self._calc_person_guidance(person, current_step)
            results[pid] = result

            if result["guide_influence"] > self.trust_params["trust_threshold"]:
                if self.state_params["enabled"]:
                    current_state = self.info_engine.get_state_value(pid)
                    if current_state != "GUIDED":
                        self.info_engine.transition_state(
                            pid,
                            InfoState.GUIDED,
                            current_step,
                            source=result["nearest_guide_id"],
                            method="guide"
                        )
                        self.step_stats["guided_activated"] += 1

            self.guide_influence_cache[pid] = result

        self.step_stats["total_guided"] = sum(1 for r in results.values() if r["is_guided"])
        return results

    def _calc_person_guidance(self, person, _current_step: int) -> dict:
        pid = person.id

        active_guides = [g for g in self.guides if g.active]

        if not active_guides:
            return {
                "guide_influence": 0.0,
                "nearest_guide_id": None,
                "is_guided": False,
                "guide_trust": 0.0,
            }

        best_influence = 0.0
        best_guide_id = None
        best_trust = 0.0

        for guide in active_guides:
            dist = ((guide.x - person.x) ** 2 + (guide.y - person.y) ** 2) ** 0.5

            if dist > guide.radius:
                continue

            dist_factor = 1.0 - (dist / guide.radius) * self.influence_params["decay_rate"]
            dist_factor = max(0.0, min(1.0, dist_factor))

            trust = self._calc_trust(pid, guide)

            influence = dist_factor * trust * self.influence_params["base_utility"]
            influence = max(0.0, min(1.0, influence))

            if influence > best_influence:
                best_influence = influence
                best_guide_id = guide.id
                best_trust = trust

        is_guided = best_influence > self.trust_params["trust_threshold"]

        return {
            "guide_influence": best_influence,
            "nearest_guide_id": best_guide_id,
            "is_guided": is_guided,
            "guide_trust": best_trust,
        }

    def _calc_trust(self, person_id: int, guide: GuideAgent) -> float:
        base_trust = self.trust_params["base_trust"]

        # 1. 关系加成
        relation_boost = self.trust_params.get("relation_boost", {})
        rel = self.graph.get_relation(person_id, guide.id)
        if isinstance(rel, dict) and isinstance(relation_boost, dict):
            rel_type = rel.get("relation_type", "stranger")
            trust_boost = relation_boost.get(rel_type, 0.0)
        else:
            trust_boost = 0.0

        # 2. 引导员角色加成
        profile_boost = self.trust_params.get("profile_boost", {})
        if isinstance(profile_boost, dict):
            profile_add = profile_boost.get(guide.profile, 0.0)
        else:
            profile_add = 0.0

        # 3. 行人自身状态影响
        if self.info_engine.is_misinformed(person_id):
            profile_add *= 0.5

        trust = base_trust + trust_boost + profile_add
        return max(0.0, min(1.0, trust))

    # ============================================================
    # 给B组的查询接口
    # ============================================================

    def get_guide_influence(self, person_id: int) -> float:
        state = self.guide_influence_cache.get(person_id, {})
        return state.get("guide_influence", 0.0)

    def get_guide_utility(self, person, target_cell: Tuple[int, int]) -> float:
        dx = target_cell[0] - person.x
        dy = target_cell[1] - person.y

        if dx == 0 and dy == 0:
            return 0.0

        move_dir = SignageModel.quantize_direction(dx, dy)

        max_utility = 0.0
        for guide in self.guides:
            if not guide.active:
                continue

            dist = ((guide.x - target_cell[0]) ** 2 + (guide.y - target_cell[1]) ** 2) ** 0.5
            if dist > guide.radius:
                continue

            gdx = target_cell[0] - guide.x
            gdy = target_cell[1] - guide.y
            if gdx == 0 and gdy == 0:
                continue

            guide_dir = SignageModel.quantize_direction(gdx, gdy)

            dir_similarity = 1.0 if move_dir == guide_dir else 0.0
            dist_factor = 1.0 - (dist / guide.radius) * self.influence_params["decay_rate"]
            trust = self._calc_trust(person.id, guide)

            utility = dir_similarity * dist_factor * trust * self.influence_params["base_utility"] * 0.5
            utility = max(0.0, min(1.0, utility))

            if utility > max_utility:
                max_utility = utility

        return max_utility

    def is_person_guided(self, person_id: int) -> bool:
        state = self.guide_influence_cache.get(person_id, {})
        return state.get("is_guided", False)

    # ============================================================
    # 给D组的查询接口
    # ============================================================

    def get_all_guides(self) -> List[dict]:
        return [g.to_dict() for g in self.guides]

    def get_active_guides(self) -> List[dict]:
        return [g.to_dict() for g in self.guides if g.active]

    def get_statistics(self) -> dict:
        active_count = sum(1 for g in self.guides if g.active)
        total_guided = self.step_stats.get("total_guided", 0)
        activated = self.step_stats.get("guided_activated", 0)

        return {
            "total_guides": len(self.guides),
            "active_guides": active_count,
            "guided_persons": total_guided,
            "guided_activated": activated,
            "params": GUIDE_PARAMS,
        }

    def get_guide_influence_map(self, grid_width: int, grid_height: int) -> np.ndarray:
        influence_map = np.zeros((grid_height, grid_width))

        for y in range(grid_height):
            for x in range(grid_width):
                temp_person = SimpleNamespace(id=-1, x=x, y=y)
                max_util = 0.0
                for ddx, ddy in [(1, 0), (0, 1), (-1, 0), (0, -1)]:
                    tx, ty = x + ddx, y + ddy
                    if 0 <= tx < grid_width and 0 <= ty < grid_height:
                        util = self.get_guide_utility(temp_person, (tx, ty))
                        if util > max_util:
                            max_util = util
                influence_map[y, x] = max_util

        return influence_map


# ============================================================
# 便捷函数
# ============================================================
def create_guide_model(social_graph, info_engine,
                       grid_width: int, grid_height: int,
                       exits: List = None) -> GuideAgentModel:
    return GuideAgentModel(social_graph, info_engine, grid_width, grid_height, exits)


if __name__ == "__main__":
    print("C09: 引导员模型 (guide_agent.py)")
    print("\n引导员移动策略:")
    for strategy in GuideMoveStrategy:
        print(f"  {strategy.value}: {strategy.value}")

    print("\n参数配置:")
    for key, value in GUIDE_PARAMS.items():
        print(f"  {key}: {value}")

    print("\n使用方式:")
    print("  from guide_agent import create_guide_model")
    print("  model = create_guide_model(social_graph, info_engine, grid_w, grid_h, exits)")
    print("  guide_id = model.add_guide(25, 25, 'teacher', GuideMoveStrategy.ESCORT)")
    print("  results = model.update_all(all_persons, current_step)")
    print("  utility = model.get_guide_utility(person, target_cell)")
