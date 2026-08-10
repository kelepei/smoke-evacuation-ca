from core.schema import Grid, CellType

# 8邻域方向
DIRS = [(-1,-1),(-1,0),(-1,1),
        (0,-1),        (0,1),
        (1,-1), (1,0), (1,1)]

def calc_next_position(person, grid: Grid, smoke_matrix, single_behavior, signage_model=None):
    px, py = int(person.x), int(person.y)
    best_x, best_y = px, py
    max_utility = -9999.0

    w_g = 0.5  # 指示牌引导权重，和signage_model参数对齐

    for dx, dy in DIRS:
        tx = px + dx
        ty = py + dy
        # 边界判断
        if not (0 <= tx < grid.width and 0 <= ty < grid.height):
            continue
        cell = grid.get_cell(tx, ty)
        if cell is None or cell.cell_type == CellType.WALL:
            continue

        # ========== 基础效用项 ==========
        # 1.烟雾惩罚
        smoke_cost = smoke_matrix[ty][tx] * 1.0
        utility = -smoke_cost

        # 2.出口正向奖励
        if cell.cell_type == CellType.EXIT:
            utility += 1.2

        # ========== C08 指示牌引导效用 ==========
        if signage_model is not None:
            guide_u = signage_model.get_guidance_utility(person, (tx, ty))
            utility += w_g * guide_u

        # ========== 原有社交行为（从众、结伴、引导员）可在此叠加 ==========
        # 后续你可以把herding/group/guide行为效用继续叠加在这里

        # 更新最优格子
        if utility > max_utility:
            max_utility = utility
            best_x, best_y = tx, ty

    return best_x, best_y