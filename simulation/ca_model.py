"""
CA 模型移动逻辑
计算行人下一步位置
"""

import random
from core.schema import Grid, CellType

# 8邻域方向
DIRS = [(-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1)]


def calc_next_position(person, grid: Grid, smoke_matrix, single_behavior=None,
                       floor_field=None, signage_model=None, occupied_positions=None):
    """
    计算行人的下一个位置

    Args:
        person: Person 对象
        grid: Grid 地图
        smoke_matrix: 烟雾浓度矩阵 (height x width)
        single_behavior: C 模块提供的单个人行为数据
        floor_field: FloorField 距离场对象
        signage_model: C08 指示牌模型
        occupied_positions: 已被占用的位置集合 (用于冲突避免)

    Returns:
        (best_x, best_y): 行人下一步位置
    """
    px, py = int(person.x), int(person.y)
    best_x, best_y = px, py
    max_utility = -9999.0
    # 新增：拆分候选格子：低烟雾安全候选、高烟雾候选，优先走安全格
    safe_candidates = []
    high_smoke_candidates = []

    # 【关键权重修改：贴合任务书，拉高出口吸引力，压低烟雾惩罚强度】
    w_d = 7.0      # 出口距离权重（大幅提升，逃生为第一优先级）
    w_s = 1.0      # 烟雾惩罚权重（适度提升，仅规避浓烟，淡烟不阻碍逃生）
    w_g = 2.2      # 指示牌权重
    w_h = 1.6      # 从众权重
    w_r = 1.9      # 关系/结伴权重
    w_f = 1.0      # 熟悉度权重

    if occupied_positions is None:
        occupied_positions = set()

    for dx, dy in DIRS:
        tx = px + dx
        ty = py + dy

        # 边界检查
        if not (0 <= tx < grid.width and 0 <= ty < grid.height):
            continue

        cell = grid.get_cell(tx, ty)
        if cell is None or cell.cell_type in [CellType.WALL, CellType.OBSTACLE]:
            continue

        # 冲突检查：目标格已被占用
        if (tx, ty) in occupied_positions:
            continue

        # ========== 计算效用 ==========
        utility = 0.0
        current_smoke = 0.0
        # 读取当前格子烟雾浓度
        if 0 <= ty < len(smoke_matrix) and 0 <= tx < len(smoke_matrix[0]):
            current_smoke = smoke_matrix[ty][tx]

        # 1. 出口距离吸引力（核心）
        if floor_field is not None and floor_field.dist_field is not None:
            dist = floor_field.dist_field[ty][tx]
            utility -= w_d * dist
        else:
            # 回退：出口格子奖励
            if cell.cell_type == CellType.EXIT:
                utility += 1.2

        # 2. 出口格子额外奖励（让行人最终走出去）
        if cell.cell_type == CellType.EXIT:
            utility += 2.0

        # 3. 烟雾惩罚（使用烟雾浓度直接计算风险惩罚，匹配5.4风险场）
        smoke_cost = current_smoke * w_s
        utility -= smoke_cost

        # 4. 熟悉度偏好（C 组提供）
        if single_behavior:
            familiarity = getattr(person, 'familiarity', 0.5)
            # 熟悉度影响：熟悉的位置有微小正向加成
            utility += w_f * familiarity * 0.1

            # 出口偏好修正（C04 共同出口）
            exit_pref = single_behavior.get("exit_preference", {})
            for exit_id, bonus in exit_pref.items():
                utility += bonus * 0.2

            # 从众影响（C05）
            herding_influence = single_behavior.get("herding_influence", 0.0)
            dominant_dir = single_behavior.get("dominant_direction", (0, 0))
            if herding_influence > 0.1:
                if dx == dominant_dir[0] and dy == dominant_dir[1]:
                    utility += w_h * herding_influence

            # 结伴影响（C04）
            is_following = single_behavior.get("is_following", False)
            follow_strength = single_behavior.get("follow_strength", 0.0)
            follow_target = single_behavior.get("follow_target")
            if is_following and follow_target is not None:
                utility += w_r * follow_strength * 0.3

            # 引导影响（C09）
            guide_influence = single_behavior.get("guide_influence", 0.0)
            if guide_influence > 0.1:
                utility += w_g * guide_influence * 0.3

        # 5. 指示牌引导（C08）
        if signage_model is not None:
            guide_u = signage_model.get_guidance_utility(person, (tx, ty))
            utility += w_g * guide_u

        # 6. 惯性：倾向于保持原方向（减少抖动）
        if hasattr(person, 'prev_x') and hasattr(person, 'prev_y'):
            prev_dx = px - person.prev_x
            prev_dy = py - person.prev_y
            if prev_dx == dx and prev_dy == dy:
                utility += 0.1

        # 拆分安全/高烟候选：烟雾浓度<0.3为安全绕行区域
        candidate_info = {"utility": utility, "x": tx, "y": ty}
        if current_smoke < 0.3:
            safe_candidates.append(candidate_info)
        else:
            high_smoke_candidates.append(candidate_info)

    # 绕行逻辑：优先在低烟雾格子里选最优效用，无安全格再走浓烟区域逃生
    if len(safe_candidates) > 0:
        safe_candidates.sort(key=lambda x: x["utility"], reverse=True)
        best_x, best_y = safe_candidates[0]["x"], safe_candidates[0]["y"]
    else:
        # 全部是浓烟，直接选效用最高（最靠近出口）的格子强制逃生
        all_candidates = safe_candidates + high_smoke_candidates
        if len(all_candidates) > 0:
            all_candidates.sort(key=lambda x: x["utility"], reverse=True)
            best_x, best_y = all_candidates[0]["x"], all_candidates[0]["y"]

    return best_x, best_y