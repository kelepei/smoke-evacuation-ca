""" CA 模型移动逻辑 计算行人下一步位置 """
import math
import numpy as np
from core.schema import Grid, CellType

DIRS = [(-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1)]


def calc_next_position(person, grid: Grid, smoke_matrix, risk_dict, single_behavior=None,
                       floor_field=None, signage_model=None, occupied_positions=None, exit_list=None,
                       exit_chooser=None, congestion_model=None, alive_person_pos=None, rng=None,
                       person_map: dict = None,
                       weights: dict = None,
                       use_softmax: bool = True,
                       softmax_lambda: float = 0.5):
    """计算行人的下一个位置"""
    px, py = int(person.x), int(person.y)

    # ===== 不改 schema：内部建坐标索引 =====
    cell_index = getattr(grid, "_cell_index", None)
    if cell_index is None:
        cell_index = {(c.x, c.y): c for c in grid.cells}
        try:
            grid._cell_index = cell_index
        except Exception:
            pass

    # 死亡/已撤离
    if getattr(person, "is_dead", False) or getattr(person, "evacuated", False):
        return px, py

    # 等待
    if single_behavior is not None and single_behavior.get("is_waiting", False):
        return px, py

    # 不知情
    if isinstance(single_behavior, dict):
        is_informed = single_behavior.get("is_informed", False)
    else:
        is_informed = getattr(single_behavior, "is_informed", False)

    if not is_informed:
        info_state = getattr(person, "info_state", "UNKNOWN")
        if info_state != "UNKNOWN":
            is_informed = True

    if not is_informed:
        return px, py

    # 出口选择
    if exit_chooser is not None and single_behavior is not None:
        if "target_exit" in single_behavior:
            target_exit = exit_chooser.select_exit(
                person, mode="guided",
                guided_exit_id=single_behavior["target_exit"]
            )
        else:
            target_exit = exit_chooser.select_exit(person, mode="nearest")
        if target_exit is not None:
            person.target_exit_id = target_exit[2]

    # 拥堵等待
    if congestion_model is not None and alive_person_pos is not None and rng is not None:
        if congestion_model.need_congestion_wait(person, alive_person_pos, rng):
            return px, py

    # 权重
    weights = weights or {}
    w_d      = weights.get("w_d", 7.0)
    w_s      = weights.get("w_s", 12.0)
    w_q      = weights.get("w_q", 0.8)
    w_g      = weights.get("w_g", 3.0)
    w_h      = weights.get("w_h", 1.6)
    w_rel    = weights.get("w_rel", 1.9)
    w_f      = weights.get("w_f", 1.0)
    w_follow = weights.get("w_follow", 1.2)
    w_dS     = weights.get("w_dS", 4.0)
    follow_max_dist = weights.get("follow_max_dist", 6.0)

    if occupied_positions is None:
        occupied_positions = set()

    # 当前格烟雾
    current_cell_smoke = 0.0
    if 0 <= py < len(smoke_matrix) and 0 <= px < len(smoke_matrix[0]):
        current_cell_smoke = float(smoke_matrix[py][px])

    # 同伴质心
    follow_target_x, follow_target_y = None, None
    follow_weight_scale = 1.0
    if person_map is not None and alive_person_pos is not None:
        neighbor_positions = []
        for pid, alive_person in person_map.items():
            if pid == person.id:
                continue
            if getattr(alive_person, "is_dead", False) or getattr(alive_person, "evacuated", False):
                continue
            ax, ay = alive_person.x, alive_person.y
            if math.hypot(ax - px, ay - py) <= follow_max_dist:
                neighbor_positions.append((ax, ay))
        if neighbor_positions:
            xs = [p[0] for p in neighbor_positions]
            ys = [p[1] for p in neighbor_positions]
            follow_target_x = sum(xs) / len(xs)
            follow_target_y = sum(ys) / len(ys)

            gx = int(round(follow_target_x))
            gy = int(round(follow_target_y))
            if 0 <= gy < len(smoke_matrix) and 0 <= gx < len(smoke_matrix[0]):
                group_smoke_val = smoke_matrix[gy][gx]
                follow_weight_scale = max(0.0, 1.0 - group_smoke_val * 2.5)

            herd_preference = getattr(person, "herd_preference", 1.0)
            follow_weight_scale *= herd_preference

    # 遍历 8 邻域
    all_candidates = []

    for dx, dy in DIRS:
        tx = px + dx
        ty = py + dy

        if not (0 <= tx < grid.width and 0 <= ty < grid.height):
            continue

        cell = cell_index.get((tx, ty))          # ← 用字典，不用 get_cell
        if cell is None or cell.cell_type in [CellType.WALL, CellType.OBSTACLE]:
            continue

        if (tx, ty) in occupied_positions:
            continue

        next_smoke = 0.0
        if 0 <= ty < len(smoke_matrix) and 0 <= tx < len(smoke_matrix[0]):
            next_smoke = float(smoke_matrix[ty][tx])

        # 极端浓度硬约束
        if next_smoke > 0.9:
            continue

        utility = 0.0

        # 1. 出口距离
        if floor_field is not None and floor_field.dist_field is not None:
            dist = floor_field.dist_field[ty][tx]
        else:
            dist = float("inf")
            if exit_list is not None:
                for _, ex, ey in exit_list:
                    d = math.hypot(tx - ex, ty - ey)
                    if d < dist:
                        dist = d
        utility -= w_d * dist

        if cell.cell_type == CellType.EXIT:
            utility += 2.0

        # 2. 烟雾惩罚
        risk_sens = getattr(person, "risk_sensitivity", 0.5)
        w_s_eff = w_s * (0.5 + risk_sens)
        utility -= w_s_eff * (next_smoke ** 1.5)

        # 3. ΔS
        dS = next_smoke - current_cell_smoke
        if dS > 0:
            utility -= w_dS * dS

        # 4. 拥堵
        local_density = 0
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                if (tx + ddx, ty + ddy) in occupied_positions:
                    local_density += 1
        utility -= w_q * local_density

        # 5. 同伴跟随
        if follow_target_x is not None and follow_target_y is not None:
            dist_to_group = math.hypot(tx - follow_target_x, ty - follow_target_y)
            utility += w_follow * follow_weight_scale / (dist_to_group + 1e-6)

        # 6. C模块行为
        if single_behavior:
            familiarity = getattr(person, 'familiarity', 0.5)
            utility += w_f * familiarity * 0.1

            exit_pref = single_behavior.get("exit_preference", {})
            for exit_id, bonus in exit_pref.items():
                utility += bonus * 0.2

            herding_influence = single_behavior.get("herding_influence", 0.0)
            dominant_dir = single_behavior.get("dominant_direction", (0, 0))
            if herding_influence > 0.1:
                if dx == dominant_dir[0] and dy == dominant_dir[1]:
                    utility += w_h * herding_influence

            is_following = single_behavior.get("is_following", False)
            follow_strength = single_behavior.get("follow_strength", 0.0)
            follow_target = single_behavior.get("follow_target")
            if is_following and follow_target is not None:
                utility += w_rel * follow_strength * 0.3

            guide_influence = single_behavior.get("guide_influence", 0.0)
            if guide_influence > 0.1:
                utility += w_g * guide_influence * 0.3

        # 7. 指示牌
        if signage_model is not None:
            guide_u = signage_model.get_guidance_utility(person, (tx, ty))
            utility += w_g * guide_u

        # 8. 惯性
        if hasattr(person, 'prev_x') and hasattr(person, 'prev_y'):
            if px - person.prev_x == dx and py - person.prev_y == dy:
                utility += 0.1

        all_candidates.append({"utility": utility, "x": tx, "y": ty})

    # 决策
    best_x, best_y = px, py
    if all_candidates:
        if use_softmax and rng is not None:
            U = np.array([c["utility"] for c in all_candidates], dtype=np.float64)
            U = U - U.max()
            p = np.exp(softmax_lambda * U)
            p = p / p.sum()
            idx = rng.choices(range(len(all_candidates)), weights=p.tolist(), k=1)[0]
            best_x = all_candidates[idx]["x"]
            best_y = all_candidates[idx]["y"]
        else:
            all_candidates.sort(key=lambda x: x["utility"], reverse=True)
            best_x = all_candidates[0]["x"]
            best_y = all_candidates[0]["y"]

    return best_x, best_y