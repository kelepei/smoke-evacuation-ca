"""
C05: 从众行为模块 (herding_model.py)
根据局部可见人群的方向，影响行人的出口选择和移动方向。

核心机制（任务书第5.5节）：
    从众概率: P_i(follow) = σ(k(n_same/n_visible - θ_i))
    其中 n_same 是主流方向人数，n_visible 是可见总人数

    1. 观察视野范围内（摩尔邻居）其他行人的移动方向
    2. 统计主流方向（出现频率最高的方向）
    3. 根据从众倾向和主流方向占比，计算从众影响力
    4. 将影响力输出给B组，用于修正移动概率和出口选择

时间因子机制：
    从众行为随仿真进程动态变化：
    - 疏散初期（0-10步）：信息混乱，从众倾向中等
    - 疏散高峰（20-40步）：恐慌和从众达到峰值
    - 疏散后期（50+步）：信息充分，从众倾向回落

依赖:
    - C01: person_profiles.json (herding_tendency 字段)
    - C03: SocialGraphBuilder (获取行人信息、出口列表)
    - C06: InformationStateEngine (获取信息状态)
    - C08: SignageModel.quantize_direction (方向量化工具)
    - B组: 行人位置、移动方向

输出给B组:
    - herding_influence: float (对移动方向的吸引力，0-1)
    - exit_preference: Dict[exit_id, float] (对出口选择的修正)
    - dominant_direction: Tuple[int, int] (主流方向，8方向)
    - is_herding: bool (是否正在从众)

输出给D组:
    - 从众日志: 主流方向、从众强度、是否跟随主流
"""

import math
import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import Counter

# 从 C08 导入方向量化工具
from signage_model import SignageModel


# ============================================================
# 从众行为参数
# ============================================================
HERDING_PARAMS = {
    # ====== 视野范围 ======
    "view": {
        "radius": 3,                # 视野半径（格），3格 = 1.5m
        "use_moore": True,          # True=8邻域，False=4邻域
    },

    # ====== 从众触发条件（对应任务书公式 P_i(follow) = σ(k(n_same/n_visible - θ_i))） ======
    "trigger": {
        "min_visible": 2,           # 至少看到 N 个人才考虑从众
        "majority_ratio": 0.5,      # 主流方向占比 >= 50% 才触发从众 (公式中的 θ)
        "k_factor": 2.0,            # 公式中的 k，陡峭度因子
        "info_states_trigger":      # 这些信息状态下更容易从众
            ["UNKNOWN", "MISINFORMED"],
        "herding_threshold": 0.4,   # herding_tendency >= 0.4 倾向从众 (公式中的 θ_i)
    },

    # ====== 从众强度计算 ======
    "strength": {
        "base_scale": 0.8,          # 从众影响力基础缩放
        "max_influence": 1.0,       # 最大影响力上限
        "distance_decay": 0.5,      # 距离衰减系数
    },

    # ====== 出口偏好修正 ======
    "exit_bias": {
        "enabled": True,            # 是否使用从众修正出口选择
        "bonus": 0.2,               # 对主流方向出口的偏好加成
    },

    # ====== 时间因子（让从众行为随时间动态变化） ======
    "time": {
        "enabled": True,            # 是否启用时间因子
        "peak_step": 30,            # 从众高峰出现在第30步（约15秒）
        "window": 20,               # 窗口期半径（步数）
        "max_boost": 0.3,           # 最大增强幅度
        "decay_rate": 0.01,         # 后期衰减速度
    },
}


class HerdingModel:
    """
    从众行为模型
    在仿真每步中，为每个行人计算从众影响力
    """

    def __init__(self, social_graph, info_engine, exits: List = None):
        """
        :param social_graph: C03 构建的 SocialGraphBuilder 实例
        :param info_engine: C06 的 InformationStateEngine 实例（用于获取信息状态）
        :param exits: 出口列表，格式: [(exit_id, x, y), ...]
        """
        self.graph = social_graph
        self.persons = social_graph.persons
        self.info_engine = info_engine
        self.exits = exits or []

        # 加载参数
        self.view_params = HERDING_PARAMS["view"]
        self.trigger_params = HERDING_PARAMS["trigger"]
        self.strength_params = HERDING_PARAMS["strength"]
        self.exit_params = HERDING_PARAMS["exit_bias"]
        self.time_params = HERDING_PARAMS.get("time", {"enabled": False})

        # 每步的状态缓存
        self.person_states = {}

    def update_all(self, all_persons: List, grid_width: int, grid_height: int,
                   current_step: int = 0) -> Dict[int, dict]:
        """
        更新所有行人的从众状态

        :param all_persons: 所有行人对象列表（含位置）
        :param grid_width: 网格宽度
        :param grid_height: 网格高度
        :param current_step: 当前仿真步数（用于时间因子计算）
        :return: {person_id: {
            "dominant_direction": Tuple[int, int],  # 主流方向 (dx, dy)
            "majority_count": int,                  # 主流方向人数
            "total_visible": int,                   # 可见总人数
            "majority_ratio": float,                # 主流方向占比
            "herding_influence": float,             # 从众影响力（0-1）
            "exit_preference": Dict[exit_id, float],# 出口偏好修正
            "is_herding": bool,                     # 是否正在从众
        }}
        """
        results = {}

        # 构建位置索引，方便快速查找
        pos_index = self._build_position_index(all_persons)

        for person in all_persons:
            pid = person.id

            # 如果已撤离，跳过
            if pid in self.persons and self.persons[pid].evacuated:
                results[pid] = self._no_behavior()
                continue

            # 获取可见范围内的其他行人
            visible_persons = self._get_visible_persons(
                person, all_persons, pos_index, grid_width, grid_height
            )

            if len(visible_persons) < self.trigger_params["min_visible"]:
                results[pid] = self._no_behavior()
                continue

            # 统计移动方向
            direction_counts = self._count_directions(visible_persons)

            if not direction_counts:
                results[pid] = self._no_behavior()
                continue

            # 计算主流方向
            dominant_dir, count = direction_counts.most_common(1)[0]
            total_visible = len(visible_persons)
            majority_ratio = count / total_visible

            # 判断是否触发从众（传入 current_step）
            should_herd = self._should_herd(
                person, majority_ratio, total_visible, current_step
            )

            if should_herd:
                # 计算从众影响力（传入 current_step）
                influence = self._calc_influence(
                    person, majority_ratio, current_step
                )

                # 计算出口偏好修正
                exit_pref = self._calc_exit_preference(
                    person, dominant_dir, influence
                )

                results[pid] = {
                    "dominant_direction": dominant_dir,
                    "majority_count": count,
                    "total_visible": total_visible,
                    "majority_ratio": majority_ratio,
                    "herding_influence": influence,
                    "exit_preference": exit_pref,
                    "is_herding": True,
                }
            else:
                results[pid] = {
                    "dominant_direction": dominant_dir,
                    "majority_count": count,
                    "total_visible": total_visible,
                    "majority_ratio": majority_ratio,
                    "herding_influence": 0.0,
                    "exit_preference": {},
                    "is_herding": False,
                }

        self.person_states = results
        return results

    def _build_position_index(self, all_persons: List) -> Dict[Tuple[int, int], int]:
        """构建位置→ID索引，用于快速查找"""
        index = {}
        for p in all_persons:
            if not (p.id in self.persons and self.persons[p.id].evacuated):
                index[(p.x, p.y)] = p.id
        return index

    def _get_visible_persons(self, person, all_persons: List,
                             pos_index: Dict, grid_w: int, grid_h: int) -> List:
        """
        获取视野范围内的其他行人
        使用摩尔邻居（8邻域）或4邻域
        """
        visible = []
        radius = self.view_params["radius"]
        use_moore = self.view_params["use_moore"]

        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if dx == 0 and dy == 0:
                    continue

                # 4邻域模式下跳过对角
                if not use_moore and abs(dx) + abs(dy) != 1:
                    continue

                nx, ny = person.x + dx, person.y + dy

                # 边界检查
                if nx < 0 or nx >= grid_w or ny < 0 or ny >= grid_h:
                    continue

                # 查找该位置是否有行人
                if (nx, ny) in pos_index:
                    neighbor_id = pos_index[(nx, ny)]
                    if neighbor_id != person.id:
                        neighbor = all_persons[neighbor_id]
                        # 检查是否已撤离
                        if not (neighbor_id in self.persons and
                                self.persons[neighbor_id].evacuated):
                            visible.append(neighbor)

        return visible

    def _count_directions(self, visible_persons: List) -> Counter:
        """统计可见行人的移动方向（8方向量化）"""
        directions = []
        for p in visible_persons:
            # 方式1: 使用 prev_x, prev_y 计算移动方向
            if hasattr(p, 'prev_x') and hasattr(p, 'prev_y'):
                dx = p.x - p.prev_x
                dy = p.y - p.prev_y
                if dx != 0 or dy != 0:
                    dir_vec = SignageModel.quantize_direction(dx, dy)
                    directions.append(dir_vec)
            # 方式2: 使用目标出口方向估算
            elif hasattr(p, 'target_exit') and p.target_exit:
                exit_pos = self._get_exit_position(p.target_exit)
                if exit_pos:
                    ex, ey = exit_pos
                    dx = ex - p.x
                    dy = ey - p.y
                    if dx != 0 or dy != 0:
                        dir_vec = SignageModel.quantize_direction(dx, dy)
                        directions.append(dir_vec)

        return Counter(directions)

    def _get_exit_position(self, exit_id) -> Optional[Tuple[int, int]]:
        """根据出口ID获取出口位置"""
        for eid, x, y in self.exits:
            if eid == exit_id:
                return x, y
        return None

    def _should_herd(self, person, majority_ratio: float, total_visible: int,
                     current_step: int) -> bool:
        """
        判断是否应触发从众行为

        :param person: 行人对象
        :param majority_ratio: 主流方向占比
        :param total_visible: 可见总人数
        :param current_step: 当前仿真步数
        :return: 是否触发从众
        """
        if total_visible < self.trigger_params["min_visible"]:
            return False

        if majority_ratio < self.trigger_params["majority_ratio"]:
            return False

        theta_i = self.trigger_params["herding_threshold"]

        # 如果主流占比超过个人从众阈值，触发
        if majority_ratio > theta_i:
            return True

        # 检查信息状态触发条件（通过 C06 接口查询）
        person_info_state = self.info_engine.get_state_value(person.id) if self.info_engine else "UNKNOWN"
        if person_info_state in self.trigger_params["info_states_trigger"]:
            return True

        # 检查从众倾向
        if person.herding_tendency >= theta_i:
            return True

        # ===== 时间因素：特定时间窗口内降低从众阈值 =====
        if self.time_params.get("enabled", False):
            peak = self.time_params.get("peak_step", 30)
            window = self.time_params.get("window", 20)
            # 在高峰窗口期内，即使从众倾向较低也触发
            if abs(current_step - peak) < window:
                if majority_ratio > 0.3:  # 窗口期内降低阈值到0.3
                    return True

        return False

    def _calc_influence(self, person, majority_ratio: float, current_step: int) -> float:
        """
        计算从众影响力

        使用任务书公式的 sigmoid 形式:
        influence = σ(k * (majority_ratio - θ_i)) × herding_tendency × time_factor

        其中 σ(x) = 1 / (1 + e^(-x))
        time_factor 随时间动态变化，模拟疏散不同阶段的从众强度
        """
        k = self.trigger_params["k_factor"]
        theta_i = self.trigger_params["herding_threshold"]

        # 计算 sigmoid 值
        x = k * (majority_ratio - theta_i)
        sigmoid = 1.0 / (1.0 + math.exp(-x))

        # ===== 时间因子：先升后降，模拟真实疏散中的从众变化 =====
        time_factor = 1.0
        if self.time_params.get("enabled", False):
            peak = self.time_params.get("peak_step", 30)
            max_boost = self.time_params.get("max_boost", 0.3)
            decay = self.time_params.get("decay_rate", 0.01)

            # 高斯型：在 peak_step 达到最大值
            # 公式: 1 + max_boost * exp(-(step - peak)^2 / (2 * (peak/2)^2))
            time_factor = 1.0 + max_boost * math.exp(-((current_step - peak) ** 2) / (2 * (peak / 2) ** 2))

            # 后期衰减（防止从众永不消退）
            time_factor = time_factor * math.exp(-decay * max(0.0, current_step - peak * 1.5))

            # 限制范围 0.5 ~ 1.5
            time_factor = max(0.5, min(1.5, time_factor))

        # 最终影响力
        influence = (
            sigmoid *
            person.herding_tendency *
            self.strength_params["base_scale"] *
            time_factor
        )
        return min(1.0, influence)

    def _calc_exit_preference(self, person, dominant_dir: Tuple[int, int],
                              influence: float) -> Dict[str, float]:
        """
        计算从众对出口选择的修正

        如果主流方向指向某个出口，增加该出口的偏好权重
        """
        if not self.exit_params["enabled"]:
            return {}

        if not self.exits:
            return {}

        exit_pref = {}
        for exit_id, ex, ey in self.exits:
            # 计算从当前位置到出口的方向
            dx = ex - person.x
            dy = ey - person.y
            if dx == 0 and dy == 0:
                continue

            exit_dir = SignageModel.quantize_direction(dx, dy)

            # 如果出口方向与主流方向一致，增加偏好
            if exit_dir == dominant_dir:
                bonus = self.exit_params["bonus"] * influence
                exit_pref[exit_id] = bonus

        return exit_pref

    @staticmethod
    def _no_behavior() -> dict:
        return {
            "dominant_direction": (0, 0),
            "majority_count": 0,
            "total_visible": 0,
            "majority_ratio": 0.0,
            "herding_influence": 0.0,
            "exit_preference": {},
            "is_herding": False,
        }

    # ============================================================
    # 给B组的查询接口
    # ============================================================
    def get_herding_influence(self, person_id: int) -> float:
        """查询某人的从众影响力"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["herding_influence"]

    def get_dominant_direction(self, person_id: int) -> Tuple[int, int]:
        """查询某人周围的主流方向"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["dominant_direction"]

    def get_exit_preference(self, person_id: int) -> Dict[str, float]:
        """查询从众对出口选择的修正"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["exit_preference"]

    def is_herding(self, person_id: int) -> bool:
        """查询某人是否正在从众"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["is_herding"]

    def get_majority_ratio(self, person_id: int) -> float:
        """查询某人周围的主流方向占比"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["majority_ratio"]

    # ============================================================
    # 给D组的统计接口
    # ============================================================
    def get_statistics(self) -> dict:
        """获取从众行为统计数据"""
        total = len(self.person_states)
        herding = sum(1 for s in self.person_states.values() if s["is_herding"])

        # 平均从众影响力
        influences = [s["herding_influence"] for s in self.person_states.values()]
        avg_influence = np.mean(influences) if influences else 0

        # 主流方向分布
        dir_counts = Counter()
        for s in self.person_states.values():
            if s["dominant_direction"] != (0, 0):
                dir_counts[s["dominant_direction"]] += 1

        return {
            "total_persons": total,
            "herding_persons": herding,
            "herding_ratio": herding / total if total > 0 else 0,
            "avg_herding_influence": avg_influence,
            "dominant_direction_distribution": dict(dir_counts),
        }


# ============================================================
# 便捷函数
# ============================================================
def create_herding_model(social_graph, info_engine, exits: List = None):
    """创建从众行为模型"""
    return HerdingModel(social_graph, info_engine, exits)


# ============================================================
# 演示
# ============================================================
if __name__ == "__main__":
    print("C05: 从众行为模块 (herding_model.py)")
    print("\n参数配置:")
    for key, value in HERDING_PARAMS.items():
        print(f"  {key}: {value}")
    print("\n核心逻辑（任务书第5.5节公式）:")
    print("  P_i(follow) = σ(k * (n_same/n_visible - θ_i))")
    print("  where σ(x) = 1 / (1 + e^(-x))")
    print("\n时间因子:")
    print("  从众行为随仿真进程动态变化（先升后降）")
    print("\n使用方式:")
    print("  from herding_model import create_herding_model")
    print("  model = create_herding_model(social_graph, info_engine, exits)")
    print("  states = model.update_all(all_persons, grid_w, grid_h, step)")
    print("  influence = model.get_herding_influence(person_id)")
