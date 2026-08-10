"""
C08: 疏散标识模块 (signage_model.py)
实现静态指示牌和动态指示牌对路径选择的影响。

核心机制（任务书第5.2节）：
    移动概率公式中的 Guidance(c) 项：
    U_i(c) = -w_d*D_exit(c) - w_s*Smoke(c) - w_q*Congestion(c)
             + w_g*Guidance(c) + w_f*Familiarity_i(c) + w_r*Relation_i(c)
             + w_h*Herding_i(c) + ε

    指示牌通过提供 Guidance(c) 值影响行人的移动选择。

职责：
    1. 定义静态指示牌（方向固定）
    2. 定义动态指示牌（根据环境动态调整方向）
    3. 计算每个元胞的 guidance_utility 值
    4. 提供查询接口给 B 组

依赖:
    - C03: SocialGraphBuilder (获取行人位置、出口信息)
    - B组: 行人位置、移动方向

输出给B组:
    - guidance_utility: Dict[(x, y), float] 每个元胞的引导效用值
    - 或通过 get_guidance_utility(person_id, target_cell) 查询

输出给D组:
    - 指示牌位置、方向、类型
    - 动态指示牌的方向变化历史
"""

from collections import defaultdict
from enum import Enum
from typing import Dict, List, Optional, Tuple
from types import SimpleNamespace
import numpy as np


# ============================================================
# 指示牌类型
# ============================================================
class SignageType(Enum):
    STATIC = "static"       # 静态指示牌：方向固定
    DYNAMIC = "dynamic"     # 动态指示牌：根据环境变化


# ============================================================
# 指示牌参数
# ============================================================
SIGNAGE_PARAMS = {
    # ====== 静态指示牌 ======
    "static": {
        "enabled": True,
        "influence_radius": 5.0,        # 影响半径（格）
        "base_utility": 0.8,            # 基础引导效用值
        "decay_rate": 0.5,              # 距离衰减系数
    },

    # ====== 动态指示牌 ======
    "dynamic": {
        "enabled": True,
        "influence_radius": 8.0,        # 影响半径（格）
        "base_utility": 1.0,            # 基础引导效用值
        "decay_rate": 0.3,              # 距离衰减系数
        "update_interval": 10,          # 每10步更新一次方向
        "strategy": "smoke_avoid",      # smoke_avoid | congestion_avoid | balanced
        "smoke_weight": 0.6,            # 烟雾规避权重
        "congestion_weight": 0.4,       # 拥堵规避权重
    },

    # ====== 通用 ======
    "general": {
        "guidance_weight": 0.5,         # B组效用公式中的 w_g
        "min_utility": 0.0,             # 最小效用值
        "max_utility": 1.0,             # 最大效用值
    },
}


class Signage:
    """单个指示牌"""

    def __init__(self, signage_id: int, x: int, y: int,
                 signage_type: SignageType,
                 target_exit: Optional[str] = None,
                 direction: Optional[Tuple[int, int]] = None):
        self.id = signage_id
        self.x = x
        self.y = y
        self.type = signage_type
        self.target_exit = target_exit
        self.direction = direction

        # 动态指示牌属性
        self.last_update_step = 0
        self.direction_history = []  # [(step, direction), ...]

    def set_direction(self, direction: Tuple[int, int], step: int):
        """设置指向方向（动态指示牌使用）"""
        self.direction = direction
        self.direction_history.append((step, direction))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "type": self.type.value,
            "target_exit": self.target_exit,
            "direction": self.direction,
            "direction_history": self.direction_history[-10:],  # 最近10次变化
        }


class SignageModel:
    """
    疏散标识模型
    管理所有指示牌，计算每个位置的引导效用值
    """

    def __init__(self, exit_list: List = None):  
        """
        :param exit_list: 出口列表，格式: [(exit_id, x, y), ...]
        """
        self.exits = exit_list or []

        # 加载参数
        self.static_params = SIGNAGE_PARAMS["static"]
        self.dynamic_params = SIGNAGE_PARAMS["dynamic"]
        self.general_params = SIGNAGE_PARAMS["general"]

        # 指示牌列表
        self.signages: List[Signage] = []

        # 引导效用缓存: {(x, y): utility}
        self.utility_cache: Dict[Tuple[int, int], float] = {}

        # 每步统计
        self.step_stats = defaultdict(int)

    # ============================================================
    # 指示牌管理
    # ============================================================

    def add_static_signage(self, x: int, y: int, target_exit: str) -> int:
        """
        添加静态指示牌

        :param x: 指示牌 x 坐标
        :param y: 指示牌 y 坐标
        :param target_exit: 指向的出口ID
        :return: 指示牌ID
        """
        signage_id = len(self.signages)
        direction = self._get_direction_to_exit(x, y, target_exit)
        signage = Signage(signage_id, x, y, SignageType.STATIC,
                          target_exit=target_exit, direction=direction)
        self.signages.append(signage)
        return signage_id

    def add_dynamic_signage(self, x: int, y: int,
                            initial_exit: Optional[str] = None) -> int:
        """
        添加动态指示牌

        :param x: 指示牌 x 坐标
        :param y: 指示牌 y 坐标
        :param initial_exit: 初始指向出口（可选）
        :return: 指示牌ID
        """
        signage_id = len(self.signages)
        direction = None
        if initial_exit:
            direction = self._get_direction_to_exit(x, y, initial_exit)
        signage = Signage(signage_id, x, y, SignageType.DYNAMIC,
                          target_exit=initial_exit, direction=direction)
        self.signages.append(signage)
        return signage_id

    def update_dynamic_signages(self, all_persons: List,
                                smoke_grid: Optional[np.ndarray],
                                _congestion_grid: Optional[np.ndarray],  
                                current_step: int):
        """
        更新所有动态指示牌的方向

        :param all_persons: 所有行人列表
        :param smoke_grid: 烟雾网格
        :param _congestion_grid: 拥堵网格
        :param current_step: 当前步数
        """
        if not self.dynamic_params["enabled"]:
            return

        update_interval = self.dynamic_params["update_interval"]

        for signage in self.signages:
            if signage.type != SignageType.DYNAMIC:
                continue

            if current_step - signage.last_update_step < update_interval:
                continue

            best_dir = self._calculate_best_direction(
                signage.x, signage.y,
                all_persons, smoke_grid, _congestion_grid
            )

            if best_dir:
                signage.set_direction(best_dir, current_step)
                signage.last_update_step = current_step
                self.step_stats["dynamic_updates"] += 1

    # ============================================================
    # 方向计算
    # ============================================================

    def _get_direction_to_exit(self, x: int, y: int, exit_id: str) -> Optional[Tuple[int, int]]:
        """计算从(x,y)到指定出口的方向（8方向量化）"""
        for eid, ex, ey in self.exits:
            if eid == exit_id:
                dx = ex - x
                dy = ey - y
                if dx == 0 and dy == 0:
                    return None
                return self.quantize_direction(dx, dy)
        return None

    def _get_exit_position(self, exit_id: str) -> Optional[Tuple[int, int]]:
        """根据出口ID获取位置"""
        for eid, x, y in self.exits:
            if eid == exit_id:
                return x, y  
        return None

    def _calculate_best_direction(self, x: int, y: int,
                                  all_persons: List,
                                  smoke_grid: Optional[np.ndarray],
                                  _congestion_grid: Optional[np.ndarray]) -> Optional[Tuple[int, int]]:
        """
        计算动态指示牌的最佳指向方向
        策略: smoke_avoid | congestion_avoid | balanced
        """
        strategy = self.dynamic_params["strategy"]

        directions = [
            (1, 0), (1, 1), (0, 1), (-1, 1),
            (-1, 0), (-1, -1), (0, -1), (1, -1)
        ]

        if strategy == "smoke_avoid":
            scores = self._evaluate_smoke_directions(x, y, directions, smoke_grid)
        elif strategy == "congestion_avoid":
            scores = self._evaluate_congestion_directions(x, y, directions, all_persons)
        else:  # balanced
            smoke_scores = self._evaluate_smoke_directions(x, y, directions, smoke_grid)
            cong_scores = self._evaluate_congestion_directions(x, y, directions, all_persons)
            w_smoke = self.dynamic_params["smoke_weight"]
            w_cong = self.dynamic_params["congestion_weight"]
            scores = {
                d: w_smoke * smoke_scores.get(d, 0) + w_cong * cong_scores.get(d, 0)
                for d in directions
            }

        if not scores:
            return None

        best_dir = max(scores, key=scores.get)
        return best_dir

    @staticmethod  
    def _evaluate_smoke_directions(x: int, y: int,
                                   directions: List[Tuple[int, int]],
                                   smoke_grid: Optional[np.ndarray]) -> Dict[Tuple[int, int], float]:
        """评估每个方向的烟雾浓度（越低越好）"""
        scores = {}
        if smoke_grid is None:
            return {d: 0.5 for d in directions}

        for dx, dy in directions:
            total_smoke = 0
            count = 0
            for step in range(1, 4):
                nx = int(x + dx * step)
                ny = int(y + dy * step)
                if 0 <= nx < smoke_grid.shape[1] and 0 <= ny < smoke_grid.shape[0]:
                    total_smoke += float(smoke_grid[ny, nx])
                    count += 1
            avg_smoke = total_smoke / count if count > 0 else 0
            scores[(dx, dy)] = 1.0 - min(1.0, avg_smoke)

        return scores

    @staticmethod  
    def _evaluate_congestion_directions(x: int, y: int,
                                        directions: List[Tuple[int, int]],
                                        all_persons: List) -> Dict[Tuple[int, int], float]:
        """评估每个方向的人群密度（越低越好）"""
        scores = {}
        pos_count = defaultdict(int)
        for p in all_persons:
            pos_count[(int(p.x), int(p.y))] += 1

        for dx, dy in directions:
            total_people = 0
            for step in range(1, 4):
                nx = int(x + dx * step)
                ny = int(y + dy * step)
                total_people += pos_count.get((nx, ny), 0)
            scores[(dx, dy)] = 1.0 - min(1.0, total_people / 10.0)

        return scores

    # ============================================================
    # 公开方向量化方法（供 C05 和 C09 调用）
    # ============================================================
    @staticmethod
    def quantize_direction(dx: int, dy: int) -> Tuple[int, int]:
        """将方向量化为8方向之一（公开方法）"""
        if dx == 0 and dy == 0:
            return 0, 0  

        angle = np.arctan2(dy, dx) * 180 / np.pi
        angle = angle % 360

        if 22.5 <= angle < 67.5:
            return 1, 1  
        elif 67.5 <= angle < 112.5:
            return 0, 1
        elif 112.5 <= angle < 157.5:
            return -1, 1
        elif 157.5 <= angle < 202.5:
            return -1, 0
        elif 202.5 <= angle < 247.5:
            return -1, -1
        elif 247.5 <= angle < 292.5:
            return 0, -1
        elif 292.5 <= angle < 337.5:
            return 1, -1
        else:
            return 1, 0

    # ============================================================
    # 核心查询接口（给B组）
    # ============================================================

    def get_guidance_utility(self, person, target_cell: Tuple[int, int]) -> float:
        """
        计算某个行人在目标元胞的引导效用值

        效用 = 基础效用 × 方向一致性 × 距离衰减

        :param person: 行人对象（需要有 x, y 属性）
        :param target_cell: 目标元胞 (tx, ty)
        :return: guidance_utility (0-1)
        """
        if not self.signages:
            return 0.0

        px, py = person.x, person.y
        tx, ty = target_cell

        dx = tx - px
        dy = ty - py
        if dx == 0 and dy == 0:
            return 0.0
        move_dir = self.quantize_direction(dx, dy)

        max_utility = 0.0

        for signage in self.signages:
            if signage.direction is None:
                continue

            dist = ((signage.x - tx) ** 2 + (signage.y - ty) ** 2) ** 0.5

            if signage.type == SignageType.STATIC:
                radius = self.static_params["influence_radius"]
                base_util = self.static_params["base_utility"]
                decay = self.static_params["decay_rate"]
            else:
                radius = self.dynamic_params["influence_radius"]
                base_util = self.dynamic_params["base_utility"]
                decay = self.dynamic_params["decay_rate"]

            if dist > radius:
                continue

            dir_similarity = 1.0 if move_dir == signage.direction else 0.0
            dist_factor = 1.0 - (dist / radius) * decay
            utility = base_util * dir_similarity * dist_factor
            utility = max(0.0, min(1.0, utility))

            if utility > max_utility:
                max_utility = utility

        return max_utility

    def get_guidance_utility_grid(self, grid_width: int, grid_height: int) -> np.ndarray:
        """
        计算整个网格的引导效用值（用于可视化）
        :return: (height, width) 的二维数组
        """
        utility_grid = np.zeros((grid_height, grid_width))

        for y in range(grid_height):
            for x in range(grid_width):
                temp_person = SimpleNamespace(x=x, y=y)

                max_util = 0.0
                for dx, dy in [(1, 0), (0, 1), (-1, 0), (0, -1),
                               (1, 1), (1, -1), (-1, 1), (-1, -1)]:
                    tx, ty = x + dx, y + dy
                    if 0 <= tx < grid_width and 0 <= ty < grid_height:
                        util = self.get_guidance_utility(temp_person, (tx, ty))
                        if util > max_util:
                            max_util = util
                utility_grid[y, x] = max_util

        return utility_grid

    # ============================================================
    # 给D组的查询接口
    # ============================================================

    def get_all_signages(self) -> List[dict]:
        """获取所有指示牌信息"""
        return [s.to_dict() for s in self.signages]

    def get_static_signages(self) -> List[dict]:
        """获取静态指示牌"""
        return [s.to_dict() for s in self.signages if s.type == SignageType.STATIC]

    def get_dynamic_signages(self) -> List[dict]:
        """获取动态指示牌"""
        return [s.to_dict() for s in self.signages if s.type == SignageType.DYNAMIC]

    def get_statistics(self) -> dict:
        """获取统计信息"""
        static_count = sum(1 for s in self.signages if s.type == SignageType.STATIC)
        dynamic_count = sum(1 for s in self.signages if s.type == SignageType.DYNAMIC)

        return {
            "total_signages": len(self.signages),
            "static_count": static_count,
            "dynamic_count": dynamic_count,
            "dynamic_updates": self.step_stats["dynamic_updates"],
            "params": SIGNAGE_PARAMS,
        }

    def get_signage_influence_map(self, grid_width: int, grid_height: int) -> np.ndarray:
        """获取指示牌影响力热力图（供D组可视化）"""
        return self.get_guidance_utility_grid(grid_width, grid_height)


# ============================================================
# 便捷函数
# ============================================================
def create_signage_model(exit_list: List = None) -> SignageModel:  
    """创建疏散标识模型"""
    return SignageModel(exit_list)


# ============================================================
# 演示
# ============================================================
if __name__ == "__main__":
    print("C08: 疏散标识模块 (signage_model.py)")

    # 模拟出口
    exits = [("exit_01", 50, 10), ("exit_02", 10, 50), ("exit_03", 90, 50)]

    # 创建模型
    model = create_signage_model(exits)

    # 添加静态指示牌
    model.add_static_signage(20, 20, "exit_01")
    model.add_static_signage(80, 20, "exit_02")

    # 添加动态指示牌
    model.add_dynamic_signage(50, 50, "exit_03")

    print(f"\n添加了 {len(model.signages)} 个指示牌")
    print("静态指示牌: 2个 (指向 exit_01, exit_02)")
    print("动态指示牌: 1个 (初始指向 exit_03)")

    print("\n使用方式:")
    print("  from signage_model import create_signage_model")
    print("  model = create_signage_model(exits)")
    print("  utility = model.get_guidance_utility(person, target_cell)")
    print("  utility_grid = model.get_guidance_utility_grid(grid_w, grid_h)")
