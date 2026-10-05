"""
行人生成区域识别模块

功能：
1. 根据地图外墙识别建筑内部区域
2. 生成 spawn_mask
3. 支持 Grid 和地图 JSON 两种输入
4. 不修改 Cell / Grid 原有接口

规则：

True:
    建筑内部，可以生成行人

False:
    建筑外部，不能生成行人
"""

from collections import deque

from core.schema import CellType


def _get_cell_type(cell):
    """
    兼容两种 cell：

    1. core.schema.Cell
    2. JSON中的dict
    """

    if isinstance(cell, dict):
        return str(
            cell.get("type", "")
        ).strip().lower()

    cell_type = getattr(
        cell,
        "cell_type",
        ""
    )

    if hasattr(cell_type, "value"):
        return str(
            cell_type.value
        ).strip().lower()

    return str(
        cell_type
    ).strip().lower()


def _get_cell_position(cell):
    """
    获取 cell 的 x、y。
    """

    if isinstance(cell, dict):

        return (
            int(cell["x"]),
            int(cell["y"])
        )

    return (
        int(cell.x),
        int(cell.y)
    )


def generate_spawn_mask(map_data):
    """
    根据地图生成行人生成区域。

    参数：
        map_data:
            可以是：
            1. core.grid.Grid
            2. 地图 JSON 对应的 dict

    返回：

        {
            (x, y): True / False
        }

    True：
        建筑内部，可生成行人

    False：
        建筑外部、墙、障碍、出口等，不作为普通随机生成位置
    """

    # ========================================================
    # 1. 获取地图尺寸和 cells
    # ========================================================

    if isinstance(map_data, dict):

        width = int(
            map_data["width"]
        )

        height = int(
            map_data["height"]
        )

        cells = map_data["cells"]

    else:

        width = int(
            map_data.width
        )

        height = int(
            map_data.height
        )

        cells = map_data.cells


    # ========================================================
    # 2. 建立 FREE 地图
    # ========================================================

    free_map = {}

    for cell in cells:

        x, y = _get_cell_position(
            cell
        )

        cell_type = _get_cell_type(
            cell
        )

        free_map[(x, y)] = (
            cell_type == "free"
        )


    # ========================================================
    # 3. 从地图边界寻找外部 FREE 区域
    #
    # 与地图边界连通的 FREE：
    #     建筑外部
    #
    # 未与边界连通的 FREE：
    #     建筑内部
    # ========================================================

    outside = set()

    queue = deque()


    # 地图上边界、下边界

    for x in range(width):

        queue.append(
            (x, 0)
        )

        queue.append(
            (x, height - 1)
        )


    # 地图左边界、右边界

    for y in range(height):

        queue.append(
            (0, y)
        )

        queue.append(
            (width - 1, y)
        )


    # ========================================================
    # 4. Flood Fill
    # ========================================================

    while queue:

        x, y = queue.popleft()


        if (x, y) in outside:
            continue


        # 只有 FREE 才能继续向外扩散

        if not free_map.get(
            (x, y),
            False
        ):
            continue


        outside.add(
            (x, y)
        )


        for dx, dy in (
            (1, 0),
            (-1, 0),
            (0, 1),
            (0, -1),
        ):

            nx = x + dx
            ny = y + dy


            if (
                0 <= nx < width
                and
                0 <= ny < height
            ):

                if (
                    nx,
                    ny
                ) not in outside:

                    queue.append(
                        (
                            nx,
                            ny
                        )
                    )


    # ========================================================
    # 5. 生成 spawn_mask
    # ========================================================

    spawn_mask = {}


    for cell in cells:

        x, y = _get_cell_position(
            cell
        )

        cell_type = _get_cell_type(
            cell
        )


        # 只有：
        #
        # FREE
        # +
        # 不属于建筑外部
        #
        # 才允许生成行人

        if (
            cell_type == "free"
            and
            (x, y) not in outside
        ):

            spawn_mask[
                (x, y)
            ] = True

        else:

            spawn_mask[
                (x, y)
            ] = False


    return spawn_mask
