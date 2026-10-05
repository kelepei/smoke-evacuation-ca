import numpy as np
from collections import deque

from core.schema import CellType


# ============================================================
# Spawn Area
#
# 功能：
#
#   自动识别地图中的“最外层建筑区域”，
#   只有最外墙以内的 FREE 元胞允许生成人。
#
# 规则：
#
#   最外墙之外 FREE
#       -> False
#
#   最外墙之内 FREE
#       -> True
#
#   WALL
#       -> False
#
#   OBSTACLE
#       -> False
#
#   EXIT
#       -> False
#
# 对外接口：
#
#       generate_spawn_mask(grid)
#
# 返回：
#
#       {
#           (x, y): True / False,
#           ...
#       }
#
# True  = 可以生成人
# False = 不可以生成人
#
# ============================================================


# ============================================================
# 1. CellType 判断
# ============================================================

def _get_cell_type(cell):
    """
    获取 cell 的类型值。
    """

    if cell is None:
        return None

    cell_type = getattr(
        cell,
        "cell_type",
        None
    )

    if hasattr(cell_type, "value"):
        return cell_type.value

    return cell_type


def _is_wall(cell):
    return _get_cell_type(cell) == CellType.WALL.value


def _is_free(cell):
    return _get_cell_type(cell) == CellType.FREE.value


# ============================================================
# 2. Grid -> WALL Mask
# ============================================================

def _build_wall_mask(grid):
    """
    构造墙体二值图。

    True：
        WALL

    False：
        非 WALL
    """

    height = int(grid.height)
    width = int(grid.width)

    wall_mask = np.zeros(
        (height, width),
        dtype=bool
    )

    for y in range(height):

        for x in range(width):

            cell = grid.get_cell(x, y)

            if cell is not None and _is_wall(cell):

                wall_mask[y, x] = True

    return wall_mask


# ============================================================
# 3. 简单形态学膨胀
# ============================================================

def _dilate(mask, radius):
    """
    对 mask 做方形膨胀。

    不依赖 cv2 / scipy。
    """

    if radius <= 0:
        return mask.copy()

    height, width = mask.shape

    padded = np.pad(
        mask,
        radius,
        mode="constant",
        constant_values=False
    )

    result = np.zeros_like(mask)

    size = radius * 2 + 1

    for dy in range(size):

        for dx in range(size):

            result |= padded[
                dy:dy + height,
                dx:dx + width
            ]

    return result


# ============================================================
# 4. 简单形态学腐蚀
# ============================================================

def _erode(mask, radius):
    """
    对 mask 做方形腐蚀。
    """

    if radius <= 0:
        return mask.copy()

    height, width = mask.shape

    padded = np.pad(
        mask,
        radius,
        mode="constant",
        constant_values=False
    )

    result = np.ones_like(mask)

    size = radius * 2 + 1

    for dy in range(size):

        for dx in range(size):

            result &= padded[
                dy:dy + height,
                dx:dx + width
            ]

    return result


# ============================================================
# 5. 墙体闭运算
# ============================================================

def _close_walls(wall_mask, radius):
    """
    墙体闭运算：

        dilation
            ↓
        erosion

    用于修复：

        - 外墙门洞
        - 外墙识别断裂
        - 图片像素造成的小缺口
        - 出入口断开

    注意：

        这里只用于识别“最外墙”。

        不会修改 Grid。
    """

    if radius <= 0:
        return wall_mask.copy()

    expanded = _dilate(
        wall_mask,
        radius
    )

    closed = _erode(
        expanded,
        radius
    )

    return closed


# ============================================================
# 6. Flood Fill
# ============================================================

def _flood_outside(wall_mask):
    """
    从地图四周寻找“墙外区域”。

    只允许穿过非墙元胞。

    返回：

        outside[y, x] = True

            表示该位置属于墙外。
    """

    height, width = wall_mask.shape

    outside = np.zeros(
        (height, width),
        dtype=bool
    )

    queue = deque()

    # --------------------------------------------------------
    # 地图四条边作为起点
    # --------------------------------------------------------

    for x in range(width):

        if not wall_mask[0, x]:

            outside[0, x] = True
            queue.append(
                (x, 0)
            )

        if height > 1:

            if not wall_mask[height - 1, x]:

                if not outside[height - 1, x]:

                    outside[
                        height - 1,
                        x
                    ] = True

                    queue.append(
                        (x, height - 1)
                    )

    for y in range(height):

        if not wall_mask[y, 0]:

            if not outside[y, 0]:

                outside[y, 0] = True

                queue.append(
                    (0, y)
                )

        if width > 1:

            if not wall_mask[y, width - 1]:

                if not outside[
                    y,
                    width - 1
                ]:

                    outside[
                        y,
                        width - 1
                    ] = True

                    queue.append(
                        (width - 1, y)
                    )

    # --------------------------------------------------------
    # 四邻域 Flood Fill
    # --------------------------------------------------------

    directions = (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1)
    )

    while queue:

        x, y = queue.popleft()

        for dx, dy in directions:

            nx = x + dx
            ny = y + dy

            if nx < 0 or nx >= width:
                continue

            if ny < 0 or ny >= height:
                continue

            if outside[ny, nx]:
                continue

            if wall_mask[ny, nx]:
                continue

            outside[ny, nx] = True

            queue.append(
                (nx, ny)
            )

    return outside


# ============================================================
# 7. 统计内部 FREE
# ============================================================

def _count_inside_free(grid, outside):
    """
    统计：

        FREE
        且
        不属于 outside

    的数量。
    """

    count = 0

    for y in range(grid.height):

        for x in range(grid.width):

            if outside[y, x]:
                continue

            cell = grid.get_cell(x, y)

            if cell is not None and _is_free(cell):

                count += 1

    return count


# ============================================================
# 8. 统计地图 FREE
# ============================================================

def _count_free(grid):
    """
    统计全部 FREE 元胞。
    """

    count = 0

    for cell in grid.cells:

        if _is_free(cell):
            count += 1

    return count


# ============================================================
# 9. 判断某个方案是否合理
# ============================================================

def _evaluate_candidate(
    grid,
    wall_mask,
    total_free
):
    """
    对一个墙体方案进行评价。

    返回：

        outside
        inside_free
        score
    """

    outside = _flood_outside(
        wall_mask
    )

    inside_free = _count_inside_free(
        grid,
        outside
    )

    # --------------------------------------------------------
    # 如果没有内部 FREE
    # --------------------------------------------------------

    if inside_free <= 0:

        return (
            outside,
            0,
            -1
        )

    # --------------------------------------------------------
    # 建筑内部比例
    # --------------------------------------------------------

    inside_ratio = (
        inside_free /
        max(total_free, 1)
    )

    # --------------------------------------------------------
    # 理想情况：
    #
    # 建筑内部占 FREE 的主要部分，
    # 但不能把整个地图全部当建筑。
    #
    # --------------------------------------------------------

    score = 0.0

    if 0.05 <= inside_ratio <= 0.98:

        score += 100.0

    # 内部比例越大，一般越符合
    # “最外墙以内都可以生成人”的要求

    score += inside_ratio * 50.0

    # --------------------------------------------------------
    # 如果内部几乎为全部 FREE，
    # 说明可能把地图边缘也包含进来了。
    # --------------------------------------------------------

    if inside_ratio > 0.995:

        score -= 100.0

    return (
        outside,
        inside_free,
        score
    )


# ============================================================
# 10. 自动寻找最合适的外墙闭合程度
# ============================================================

def _find_outer_boundary(
    grid,
    wall_mask
):
    """
    自动寻找最外层建筑边界。

    不固定某个地图。

    会根据当前地图尺寸自动生成多个候选
    闭合程度。

    然后选择最合理的方案。
    """

    total_free = _count_free(
        grid
    )

    # --------------------------------------------------------
    # 地图尺寸
    # --------------------------------------------------------

    width = int(grid.width)
    height = int(grid.height)

    min_dimension = min(
        width,
        height
    )

    # --------------------------------------------------------
    # 根据地图尺寸自动生成候选半径。
    #
    # 小地图：
    #   小半径
    #
    # 大地图：
    #   允许更大的外墙缺口修复
    # --------------------------------------------------------

    max_radius = max(
        3,
        min(
            15,
            int(min_dimension * 0.15)
        )
    )

    radii = list(
        range(
            0,
            max_radius + 1
        )
    )

    best = None

    # --------------------------------------------------------
    # 尝试每一个半径
    # --------------------------------------------------------

    for radius in radii:

        candidate_wall = _close_walls(
            wall_mask,
            radius
        )

        (
            outside,
            inside_free,
            score
        ) = _evaluate_candidate(
            grid,
            candidate_wall,
            total_free
        )

        if best is None:

            best = {
                "radius": radius,
                "wall": candidate_wall,
                "outside": outside,
                "inside_free": inside_free,
                "score": score
            }

            continue

        # ----------------------------------------------------
        # 分数更高则替换
        # ----------------------------------------------------

        if score > best["score"]:

            best = {
                "radius": radius,
                "wall": candidate_wall,
                "outside": outside,
                "inside_free": inside_free,
                "score": score
            }

    return (
        best["wall"],
        best["outside"],
        best["radius"],
        best["inside_free"]
    )


# ============================================================
# 11. 最终生成 Spawn Mask
# ============================================================

def generate_spawn_mask(grid):
    """
    生成 Spawn Area。

    ========================================================

    对外接口：

        generate_spawn_mask(grid)

    ========================================================

    允许：

        最外墙以内的 FREE

    禁止：

        最外墙之外的 FREE
        WALL
        OBSTACLE
        EXIT

    ========================================================
    """

    width = int(grid.width)
    height = int(grid.height)

    # --------------------------------------------------------
    # 1. WALL Mask
    # --------------------------------------------------------

    wall_mask = _build_wall_mask(
        grid
    )

    # --------------------------------------------------------
    # 2. 自动识别最外墙
    # --------------------------------------------------------

    (
        closed_wall,
        outside,
        close_radius,
        inside_free_count
    ) = _find_outer_boundary(
        grid,
        wall_mask
    )

    # --------------------------------------------------------
    # 3. 生成结果
    # --------------------------------------------------------

    spawn_mask = {}

    for y in range(height):

        for x in range(width):

            cell = grid.get_cell(
                x,
                y
            )

            can_spawn = False

            if cell is None:

                spawn_mask[
                    (x, y)
                ] = False

                continue

            # ------------------------------------------------
            # 必须 FREE
            # ------------------------------------------------

            if not _is_free(cell):

                spawn_mask[
                    (x, y)
                ] = False

                continue

            # ------------------------------------------------
            # 必须位于最外墙内部
            # ------------------------------------------------

            if outside[y, x]:

                spawn_mask[
                    (x, y)
                ] = False

                continue

            # ------------------------------------------------
            # 最终允许
            # ------------------------------------------------

            spawn_mask[
                (x, y)
            ] = True

    # --------------------------------------------------------
    # 4. 统计
    # --------------------------------------------------------

    total_cells = (
        width *
        height
    )

    total_free = _count_free(
        grid
    )

    outside_count = int(
        np.sum(outside)
    )

    spawn_count = sum(
        1
        for value in spawn_mask.values()
        if value
    )

    wall_count = int(
        np.sum(wall_mask)
    )

    # --------------------------------------------------------
    # 5. 输出信息
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("A Spawn Area 自动识别")
    print("=" * 70)

    print(
        f"[INFO] 地图尺寸："
        f"{width} × {height}"
    )

    print(
        f"[INFO] 总元胞："
        f"{total_cells}"
    )

    print(
        f"[INFO] FREE："
        f"{total_free}"
    )

    print(
        f"[INFO] WALL："
        f"{wall_count}"
    )

    print(
        f"[INFO] 自动闭合半径："
        f"{close_radius}"
    )

    print(
        f"[INFO] 最外墙之外："
        f"{outside_count}"
    )

    print(
        f"[INFO] 最外墙以内 FREE："
        f"{inside_free_count}"
    )

    print(
        f"[INFO] 最终 Spawn Cells："
        f"{spawn_count}"
    )

    print("=" * 70)

    return spawn_mask


# ============================================================
# 12. 单独运行提示
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("spawn_area.py")
    print("=" * 60)

    print(
        "对外接口："
    )

    print(
        "    generate_spawn_mask(grid)"
    )

    print()
