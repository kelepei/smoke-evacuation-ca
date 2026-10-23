""" CA 模型移动逻辑 计算行人下一步位置 """
import math
import numpy as np
from core.schema import Grid, CellType

DIRS = [(-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1)]


def _cell_type_value(cell) -> str:
    ct = getattr(cell, "cell_type", None)
    return str(getattr(ct, "value", ct)).lower()


def _distance_lookup(floor_field, exit_id, exit_list, tx: int, ty: int) -> float:
    """候选格到"目标出口"的距离；没有目标出口时退化为最近出口。"""
    if floor_field is not None:
        if exit_id is not None and getattr(floor_field, "exit_fields", None):
            field = floor_field.exit_fields.get(str(exit_id))
            if field is not None:
                value = float(field[ty][tx])
                if value < 9998:
                    return value
        if getattr(floor_field, "dist_field", None) is not None:
            return float(floor_field.dist_field[ty][tx])
    dist = float("inf")
    if exit_list is not None:
        for _eid, ex, ey in exit_list:
            d = math.hypot(tx - ex, ty - ey)
            if d < dist:
                dist = d
    return dist


def _target_exit_id(person, exit_chooser) -> str | None:
    eid = getattr(person, "target_exit_id", None)
    if eid:
        return str(eid)
    if exit_chooser is not None and hasattr(exit_chooser, "target_of"):
        chosen = exit_chooser.target_of(person)
        if chosen:
            setattr(person, "target_exit_id", str(chosen))
            return str(chosen)
    return None


def _extend_path(px, py, dx, dy, steps, grid, cell_index, occupied_positions,
                 smoke_matrix, max_x, max_y):
    """沿 (dx,dy) 方向最多走 steps 格，遇墙/占用/高浓度烟雾就停。"""
    best_x, best_y = px, py
    for _ in range(max(1, int(steps)) - 1):
        nx, ny = best_x + dx, best_y + dy
        if not (0 <= nx < max_x and 0 <= ny < max_y):
            break
        cell = cell_index.get((nx, ny))
        if cell is None or _cell_type_value(cell) in ("wall", "obstacle"):
            break
        if (nx, ny) in occupied_positions:
            break
        smoke = float(smoke_matrix[ny][nx]) if 0 <= ny < len(smoke_matrix) and 0 <= nx < len(smoke_matrix[0]) else 0.0
        if smoke > 0.9:
            break
        best_x, best_y = nx, ny
        if _cell_type_value(cell) == "exit":
            break
    return best_x, best_y


def calc_next_position(person, grid: Grid, smoke_matrix, risk_dict, single_behavior=None,
                       floor_field=None, signage_model=None, occupied_positions=None, exit_list=None,
                       exit_chooser=None, congestion_model=None, alive_person_pos=None, rng=None,
                       person_map: dict = None,
                       weights: dict = None,
                       use_softmax: bool = True,
                       softmax_lambda: float = 0.5,
                       max_steps: int = 1):
    """计算行人的下一个位置

    :param max_steps: 本步最多前进的元胞数（由速度模型给出，>=1）
    """
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

    # 目标出口：C 引导优先，否则用出口选择模块维护的出口
    target_exit = _target_exit_id(person, exit_chooser)
    if exit_chooser is not None and single_behavior is not None:
        forced = single_behavior.get("target_exit")
        if forced:
            target_exit = str(forced)
            person.target_exit_id = target_exit

    # 拥堵等待：危险感知高的人会"硬挤"，不再因为拥堵原地等
    danger_pressure = float(getattr(person, "danger_pressure", 0.0) or 0.0)
    danger_urgent = bool(getattr(person, "danger_urgent", False))
    if (congestion_model is not None and alive_person_pos is not None and rng is not None
            and not danger_urgent):
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
    w_lateral = weights.get("w_lateral", 2.0)
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

    # 危险感知：横向换道方向（垂直于"指向出口"的梯度）
    lateral_dirs: set[tuple[int, int]] = set()
    if danger_urgent and floor_field is not None and floor_field.dist_field is not None:
        h, w = floor_field.dist_field.shape
        x0, x1 = max(0, px - 1), min(w - 1, px + 1)
        y0, y1 = max(0, py - 1), min(h - 1, py + 1)
        gx = float(floor_field.dist_field[py][x1] - floor_field.dist_field[py][x0])
        gy = float(floor_field.dist_field[y1][px] - floor_field.dist_field[y0][px])
        if abs(gx) + abs(gy) > 1e-9:
            # 与"下坡方向"垂直的两个方向就是横向换道方向
            for vx, vy in ((-gy, gx), (gy, -gx)):
                sx = 0 if abs(vx) < 1e-9 else (1 if vx > 0 else -1)
                sy = 0 if abs(vy) < 1e-9 else (1 if vy > 0 else -1)
                if (sx, sy) in DIRS:
                    lateral_dirs.add((sx, sy))

    # 遍历 8 邻域
    all_candidates = []

    for dx, dy in DIRS:
        tx = px + dx
        ty = py + dy

        if not (0 <= tx < grid.width and 0 <= ty < grid.height):
            continue

        cell = cell_index.get((tx, ty))          # ← 用字典，不用 get_cell
        if cell is None or _cell_type_value(cell) in ("wall", "obstacle"):
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

        # 1. 到"目标出口"的距离
        dist = _distance_lookup(floor_field, target_exit, exit_list, tx, ty)
        if dist >= 9998:
            dist = 9998.0
        utility -= w_d * dist

        if _cell_type_value(cell) == "exit":
            utility += 2.0

        # 2. 烟雾惩罚（风险敏感度越高越怕烟）
        risk_sens = getattr(person, "risk_sensitivity", 0.5)
        w_s_eff = w_s * (0.5 + risk_sens)
        utility -= w_s_eff * (next_smoke ** 1.5)

        # 3. ΔS
        dS = next_smoke - current_cell_smoke
        if dS > 0:
            utility -= w_dS * dS

        # 4. 拥堵：危险感知高的人对拥堵的容忍度更高（更愿意往前挤）
        local_density = 0
        for ddx in (-1, 0, 1):
            for ddy in (-1, 0, 1):
                if (tx + ddx, ty + ddy) in occupied_positions:
                    local_density += 1
        w_q_eff = w_q * max(0.25, 1.0 - 0.8 * danger_pressure)
        utility -= w_q_eff * local_density

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
                if target_exit is not None and str(exit_id) == str(target_exit):
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

        # 9. 危险感知触发的横向换道（张琦 2022 模式1）
        if danger_urgent and (dx, dy) in lateral_dirs:
            utility += w_lateral * (0.75 + danger_pressure)

        all_candidates.append({"utility": utility, "x": tx, "y": ty, "dx": dx, "dy": dy})

    # 决策
    best_x, best_y = px, py
    if all_candidates:
        if use_softmax and rng is not None:
            U = np.array([c["utility"] for c in all_candidates], dtype=np.float64)
            U = U - U.max()
            p = np.exp(softmax_lambda * U)
            p = p / p.sum()
            idx = rng.choices(range(len(all_candidates)), weights=p.tolist(), k=1)[0]
            chosen = all_candidates[idx]
        else:
            chosen = max(all_candidates, key=lambda item: item["utility"])
        best_x, best_y = chosen["x"], chosen["y"]

        # 速度模型给出 >1 格时，沿同一方向继续前进
        if int(max_steps) > 1 and (best_x, best_y) != (px, py):
            best_x, best_y = _extend_path(
                best_x, best_y, chosen["dx"], chosen["dy"], int(max_steps),
                grid, cell_index, occupied_positions, smoke_matrix,
                grid.width, grid.height,
            )

    return best_x, best_y
