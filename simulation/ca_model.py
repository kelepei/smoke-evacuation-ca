"""
CA 模型移动逻辑
计算行人下一步位置
"""

import random
import math
from core.schema import Grid, CellType

# 8邻域方向
DIRS = [(-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1)]


def calc_next_position(person, grid: Grid, smoke_matrix, risk_dict, single_behavior=None,
                       floor_field=None, signage_model=None, occupied_positions=None, exit_list=None):
    """
    计算行人的下一个位置
    新增risk_dict：{person_id: 行人综合感知风险Risk_i(t)}
    新增exit_list入参，用于兜底计算出口距离
    """
    px, py = int(person.x), int(person.y)
    best_x, best_y = px, py
    max_utility = -9999.0
    # 拆分安全/高烟雾候选
    safe_candidates = []
    high_smoke_candidates = []

    # 权重参数
    w_d = 7.0      # 出口距离权重
    w_s = 1.0      # 烟雾惩罚权重（格子客观浓度）
    w_risk = 0.9   # 行人主观感知风险权重【新增】
    w_g = 3.0      # 指示牌/引导员权重
    w_h = 1.6      # 从众权重
    w_rel = 1.9    # 关系/结伴权重
    w_f = 1.0      # 熟悉度权重

    if occupied_positions is None:
        occupied_positions = set()

    # 获取当前行人综合感知风险
    person_risk = risk_dict.get(person.id, 0.0)

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

        # 1. 出口距离吸引力（核心兜底：兼容floor_field为空的情况）
        if floor_field is not None and floor_field.dist_field is not None:
            dist = floor_field.dist_field[ty][tx]
        else:
            # 手动计算到最近出口的欧氏距离，强制生效出口吸引力
            dist = float("inf")
            if exit_list is not None:
                for _, ex, ey in exit_list:
                    d = math.hypot(tx - ex, ty - ey)
                    if d < dist:
                        dist = d
        utility -= w_d * dist

        # 2. 出口格子额外奖励（让行人最终走出去）
        if cell.cell_type == CellType.EXIT:
            utility += 2.0

        # 3. 客观烟雾浓度惩罚
        smoke_cost = current_smoke * w_s
        utility -= smoke_cost

        # 【新增4】行人主观综合风险惩罚 Risk_i(t)
        # 行人感知风险越高，整体移动意愿下降，规避烟雾区域
        utility -= w_risk * person_risk

        # 5. 熟悉度偏好（C 组提供）
        if single_behavior:
            familiarity = getattr(person, 'familiarity', 0.5)
            utility += w_f * familiarity * 0.1

            # 出口偏好修正
            exit_pref = single_behavior.get("exit_preference", {})
            for exit_id, bonus in exit_pref.items():
                utility += bonus * 0.2

            # 从众影响
            herding_influence = single_behavior.get("herding_influence", 0.0)
            dominant_dir = single_behavior.get("dominant_direction", (0, 0))
            if herding_influence > 0.1:
                if dx == dominant_dir[0] and dy == dominant_dir[1]:
                    utility += w_h * herding_influence

            # 结伴影响
            is_following = single_behavior.get("is_following", False)
            follow_strength = single_behavior.get("follow_strength", 0.0)
            follow_target = single_behavior.get("follow_target")
            if is_following and follow_target is not None:
                utility += w_rel * follow_strength * 0.3

            # 引导影响
            guide_influence = single_behavior.get("guide_influence", 0.0)
            if guide_influence > 0.1:
                utility += w_g * guide_influence * 0.3

        # 6. 指示牌引导
        if signage_model is not None:
            guide_u = signage_model.get_guidance_utility(person, (tx, ty))
            utility += w_g * guide_u

        # 7. 行走惯性防抖
        if hasattr(person, 'prev_x') and hasattr(person, 'prev_y'):
            prev_dx = px - person.prev_x
            prev_dy = py - person.prev_y
            if prev_dx == dx and prev_dy == dy:
                utility += 0.1

        # 分类候选格子
        candidate_info = {"utility": utility, "x": tx, "y": ty}
        if current_smoke < 0.3:
            safe_candidates.append(candidate_info)
        else:
            high_smoke_candidates.append(candidate_info)

    # 择优选择移动目标
    if len(safe_candidates) > 0:
        safe_candidates.sort(key=lambda x: x["utility"], reverse=True)
        best_x, best_y = safe_candidates[0]["x"], safe_candidates[0]["y"]
    else:
        all_candidates = safe_candidates + high_smoke_candidates
        if len(all_candidates) > 0:
            all_candidates.sort(key=lambda x: x["utility"], reverse=True)
            best_x, best_y = all_candidates[0]["x"], all_candidates[0]["y"]

    return best_x, best_y