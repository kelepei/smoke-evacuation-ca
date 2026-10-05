"""
A 模块：Spawn Area 自动识别

============================================================
功能
============================================================

根据地图中的最外层墙体，自动判断哪些 FREE 元胞允许生成人员。

规则：

    1. 只有 FREE 元胞可以作为人员出生位置；
    2. 最外墙之外的 FREE 元胞禁止生成人员；
    3. 最外墙之内的 FREE 元胞允许生成人员；
    4. WALL / OBSTACLE / EXIT / SMOKE_SOURCE 等均不能生成人员；
    5. 不针对任何具体地图写死坐标；
    6. 适配不同大小、不同形状的地图；
    7. 同时支持：
           - core.grid.Grid
           - 网页传入的 JSON dict

============================================================
对外接口
============================================================

    generate_spawn_mask(map_data)

输入：

    Grid
    或
    JSON dict

返回：

    {
        (x, y): True,
        (x, y): False,
        ...
    }

True：
    允许生成人员

False：
    不允许生成人员

============================================================
核心思想
============================================================

普通的 flood fill 有一个问题：

如果建筑外墙存在门、出口或者 PNG 转换产生的小缺口：

    外部
      ↓
    门洞
      ↓
    建筑内部

那么 flood fill 会把整个建筑内部也判断成“外部”。

因此本模块采用：

    原始墙体
       ↓
    自动封闭小墙体缺口
       ↓
    从地图边界 flood fill
       ↓
    找到真正的外部 FREE 区域
       ↓
    FREE - 外部 FREE
       ↓
    Spawn Area

注意：

    这里只是为了判断 Spawn Area，
    不会修改原始 Grid，
    也不会修改 JSON。
"""

from collections import deque
from typing import Any, Dict, Iterable, Optional, Tuple

import numpy as np

from core.schema import CellType


# ============================================================
# 基础类型
# ============================================================

Coordinate = Tuple[int, int]
SpawnMask = Dict[Coordinate, bool]


# ============================================================
# Cell 类型辅助函数
# ============================================================

def _cell_type_value(cell: Any) -> Optional[str]:
    """
    获取 cell 的 type。

    同时兼容：

        Grid Cell:
            cell.cell_type

        JSON:
            cell["type"]

        某些版本：
            cell["cell_type"]
    """

    if cell is None:
        return None

    # --------------------------------------------------------
    # JSON dict
    # --------------------------------------------------------

    if isinstance(cell, dict):

        value = cell.get("type")

        if value is None:
            value = cell.get("cell_type")

        if hasattr(value, "value"):
            return str(value.value)

        if value is None:
            return None

        return str(value)

    # --------------------------------------------------------
    # Grid Cell
    # --------------------------------------------------------

    value = getattr(
        cell,
        "cell_type",
        None
    )

    if hasattr(value, "value"):
        return str(value.value)

    if value is None:
        return None

    return str(value)


def _is_free(cell: Any) -> bool:
    """判断是否为 FREE。"""

    return (
        _cell_type_value(cell)
        == CellType.FREE.value
    )


def _is_wall(cell: Any) -> bool:
    """判断是否为 WALL。"""

    return (
        _cell_type_value(cell)
        == CellType.WALL.value
    )


# ============================================================
# 地图统一访问器
# ============================================================

class _MapView:
    """
    将：

        Grid
        JSON dict

    统一成同一种访问方式。

    后面的算法只使用：

        width
        height
        get_cell(x, y)
    """

    def __init__(self, data: Any):

        self.original = data

        # ----------------------------------------------------
        # 情况 1：Grid
        # ----------------------------------------------------

        if (
            hasattr(data, "width")
            and hasattr(data, "height")
            and hasattr(data, "get_cell")
        ):

            self.width = int(
                data.width
            )

            self.height = int(
                data.height
            )

            self._is_grid = True

            self._cells = None

            return

        # ----------------------------------------------------
        # 情况 2：JSON dict
        # ----------------------------------------------------

        if isinstance(data, dict):

            if "width" not in data:
                raise ValueError(
                    "地图 JSON 缺少 width"
                )

            if "height" not in data:
                raise ValueError(
                    "地图 JSON 缺少 height"
                )

            if "cells" not in data:
                raise ValueError(
                    "地图 JSON 缺少 cells"
                )

            self.width = int(
                data["width"]
            )

            self.height = int(
                data["height"]
            )

            cells = data["cells"]

            if not isinstance(cells, list):
                raise ValueError(
                    "地图 JSON 的 cells 必须是 list"
                )

            self._is_grid = False

            self._cells = {}

            # ------------------------------------------------
            # 根据 x/y 建立索引
            # ------------------------------------------------

            for index, cell in enumerate(cells):

                if not isinstance(cell, dict):
                    continue

                x = cell.get("x")
                y = cell.get("y")

                # ------------------------------------------------
                # 如果 JSON 没有 x/y，
                # 按 row-major 自动补齐
                # ------------------------------------------------

                if x is None or y is None:

                    x = index % self.width

                    y = index // self.width

                try:
                    x = int(x)
                    y = int(y)
                except (
                    TypeError,
                    ValueError
                ):
                    continue

                if (
                    0 <= x < self.width
                    and
                    0 <= y < self.height
                ):

                    self._cells[
                        (x, y)
                    ] = cell

            return

        # ----------------------------------------------------
        # 不支持的类型
        # ----------------------------------------------------

        raise TypeError(
            "generate_spawn_mask() 只支持 "
            "Grid 对象或 JSON dict，"
            f"当前类型：{type(data).__name__}"
        )

    # ========================================================
    # 获取 cell
    # ========================================================

    def get_cell(
        self,
        x: int,
        y: int
    ) -> Any:

        if (
            x < 0
            or x >= self.width
            or y < 0
            or y >= self.height
        ):
            return None

        if self._is_grid:

            return self.original.get_cell(
                x,
                y
            )

        return self._cells.get(
            (x, y)
        )

    # ========================================================
    # 遍历所有 cell
    # ========================================================

    def iter_cells(
        self
    ) -> Iterable[Tuple[int, int, Any]]:

        for y in range(
            self.height
        ):

            for x in range(
                self.width
            ):

                yield (
                    x,
                    y,
                    self.get_cell(
                        x,
                        y
                    )
                )


# ============================================================
# 创建地图类型 Mask
# ============================================================

def _build_type_masks(
    map_view: _MapView
):
    """
    建立：

        free_mask
        wall_mask
        blocked_mask

    blocked_mask 用于判断外部区域。

    注意：

        EXIT 必须视为边界封闭点。

    因为出口在地图语义上是门洞，
    但 Spawn Area 判断不能让 flood fill
    通过出口直接进入建筑内部。
    """

    width = map_view.width
    height = map_view.height

    free_mask = np.zeros(
        (height, width),
        dtype=bool
    )

    wall_mask = np.zeros(
        (height, width),
        dtype=bool
    )

    blocked_mask = np.zeros(
        (height, width),
        dtype=bool
    )

    for y in range(height):

        for x in range(width):

            cell = map_view.get_cell(
                x,
                y
            )

            cell_type = (
                _cell_type_value(cell)
            )

            if cell_type == CellType.FREE.value:

                free_mask[
                    y,
                    x
                ] = True

            elif cell_type == CellType.WALL.value:

                wall_mask[
                    y,
                    x
                ] = True

                blocked_mask[
                    y,
                    x
                ] = True

            else:

                # ------------------------------------------------
                # EXIT / OBSTACLE / SMOKE_SOURCE 等：
                #
                # 都不是人员出生点。
                #
                # 但只有 EXIT 需要作为“外墙封口”
                # 来阻止外部 flood fill 穿进去。
                # ------------------------------------------------

                if cell_type == CellType.EXIT.value:

                    blocked_mask[
                        y,
                        x
                    ] = True

    return (
        free_mask,
        wall_mask,
        blocked_mask
    )


# ============================================================
# 纯 NumPy 二值膨胀
# ============================================================

def _binary_dilate(
    image: np.ndarray,
    radius: int
) -> np.ndarray:
    """
    二值膨胀。

    不依赖 scipy。

    radius=1：

        3 × 3

    radius=2：

        5 × 5
    """

    if radius <= 0:
        return image.copy()

    height, width = image.shape

    padded = np.pad(
        image.astype(np.uint8),
        (
            (radius, radius),
            (radius, radius)
        ),
        mode="constant",
        constant_values=0
    )

    integral = np.pad(
        padded,
        (
            (1, 0),
            (1, 0)
        ),
        mode="constant",
        constant_values=0
    ).cumsum(
        axis=0
    ).cumsum(
        axis=1
    )

    size = (
        2 * radius
        + 1
    )

    sums = (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )

    return (
        sums > 0
    )


# ============================================================
# 纯 NumPy 二值腐蚀
# ============================================================

def _binary_erode(
    image: np.ndarray,
    radius: int
) -> np.ndarray:
    """
    二值腐蚀。
    """

    if radius <= 0:
        return image.copy()

    height, width = image.shape

    padded = np.pad(
        image.astype(np.uint8),
        (
            (radius, radius),
            (radius, radius)
        ),
        mode="constant",
        constant_values=0
    )

    integral = np.pad(
        padded,
        (
            (1, 0),
            (1, 0)
        ),
        mode="constant",
        constant_values=0
    ).cumsum(
        axis=0
    ).cumsum(
        axis=1
    )

    size = (
        2 * radius
        + 1
    )

    sums = (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )

    window_area = (
        size * size
    )

    return (
        sums == window_area
    )


# ============================================================
# 墙体闭运算
# ============================================================

def _close_wall_gaps(
    wall_mask: np.ndarray,
    radius: int
) -> np.ndarray:
    """
    对墙体进行闭运算：

        膨胀 → 腐蚀

    作用：

        自动封闭小型墙体缺口。

    注意：

        不修改真正的 wall 数据，
        只生成 Spawn Area 判断专用 mask。
    """

    if radius <= 0:
        return wall_mask.copy()

    dilated = _binary_dilate(
        wall_mask,
        radius
    )

    closed = _binary_erode(
        dilated,
        radius
    )

    return closed


# ============================================================
# 从地图边界 Flood Fill
# ============================================================

def _flood_external(
    blocked_mask: np.ndarray
) -> np.ndarray:
    """
    从地图四周开始 flood fill。

    True：
        可以从地图外部到达

    False：
        被墙体封闭

    这里使用 4 邻域。
    """

    height, width = (
        blocked_mask.shape
    )

    external = np.zeros(
        (height, width),
        dtype=bool
    )

    queue = deque()

    # --------------------------------------------------------
    # 上边界
    # --------------------------------------------------------

    for x in range(width):

        if not blocked_mask[
            0,
            x
        ]:

            if not external[
                0,
                x
            ]:

                external[
                    0,
                    x
                ] = True

                queue.append(
                    (x, 0)
                )

    # --------------------------------------------------------
    # 下边界
    # --------------------------------------------------------

    if height > 1:

        for x in range(width):

            if not blocked_mask[
                height - 1,
                x
            ]:

                if not external[
                    height - 1,
                    x
                ]:

                    external[
                        height - 1,
                        x
                    ] = True

                    queue.append(
                        (
                            x,
                            height - 1
                        )
                    )

    # --------------------------------------------------------
    # 左边界
    # --------------------------------------------------------

    for y in range(height):

        if not blocked_mask[
            y,
            0
        ]:

            if not external[
                y,
                0
            ]:

                external[
                    y,
                    0
                ] = True

                queue.append(
                    (0, y)
                )

    # --------------------------------------------------------
    # 右边界
    # --------------------------------------------------------

    if width > 1:

        for y in range(height):

            if not blocked_mask[
                y,
                width - 1
            ]:

                if not external[
                    y,
                    width - 1
                ]:

                    external[
                        y,
                        width - 1
                    ] = True

                    queue.append(
                        (
                            width - 1,
                            y
                        )
                    )

    # --------------------------------------------------------
    # 四邻域搜索
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

            if (
                nx < 0
                or nx >= width
                or ny < 0
                or ny >= height
            ):
                continue

            if external[
                ny,
                nx
            ]:
                continue

            if blocked_mask[
                ny,
                nx
            ]:
                continue

            external[
                ny,
                nx
            ] = True

            queue.append(
                (
                    nx,
                    ny
                )
            )

    return external


# ============================================================
# 统计连通区域
# ============================================================

def _count_components(
    mask: np.ndarray
) -> int:
    """
    统计 True 区域的连通分量数量。
    """

    height, width = mask.shape

    visited = np.zeros(
        (height, width),
        dtype=bool
    )

    count = 0

    directions = (
        (1, 0),
        (-1, 0),
        (0, 1),
        (0, -1)
    )

    for y in range(height):

        for x in range(width):

            if not mask[
                y,
                x
            ]:
                continue

            if visited[
                y,
                x
            ]:
                continue

            count += 1

            queue = deque()

            queue.append(
                (x, y)
            )

            visited[
                y,
                x
            ] = True

            while queue:

                cx, cy = queue.popleft()

                for dx, dy in directions:

                    nx = cx + dx
                    ny = cy + dy

                    if (
                        nx < 0
                        or nx >= width
                        or ny < 0
                        or ny >= height
                    ):
                        continue

                    if visited[
                        ny,
                        nx
                    ]:
                        continue

                    if not mask[
                        ny,
                        nx
                    ]:
                        continue

                    visited[
                        ny,
                        nx
                    ] = True

                    queue.append(
                        (
                            nx,
                            ny
                        )
                    )

    return count


# ============================================================
# 选择合适的墙体缺口修复尺度
# ============================================================

def _select_wall_closing(
    wall_mask: np.ndarray,
    free_mask: np.ndarray,
    blocked_mask: np.ndarray
):
    """
    自动选择墙体缺口修复尺度。

    不针对任何具体地图写死。

    尝试：

        radius = 0
        radius = 1
        radius = 2
        radius = 3
        radius = 4
        radius = 5

    然后根据：

        1. Spawn Area 是否存在
        2. Spawn Area 是否稳定
        3. 是否出现整个 FREE 都被吞掉
        4. 是否明显只剩少量零碎区域

    选择稳定结果。

    重点：

        不直接选择最大的 Spawn Area。
        防止把地图外部的大面积区域误认为建筑内部。
    """

    free_count = int(
        np.sum(free_mask)
    )

    if free_count == 0:

        return (
            blocked_mask.copy(),
            0,
            np.zeros_like(
                free_mask,
                dtype=bool
            )
        )

    candidates = []

    # --------------------------------------------------------
    # 候选尺度
    # --------------------------------------------------------

    radii = (
        0,
        1,
        2,
        3,
        4,
        5
    )

    for radius in radii:

        # ----------------------------------------------------
        # radius=0：
        # 不修复墙体
        # ----------------------------------------------------

        if radius == 0:

            closed_wall = wall_mask.copy()

        else:

            closed_wall = _close_wall_gaps(
                wall_mask,
                radius
            )

        # ----------------------------------------------------
        # EXIT 本身也需要作为封闭边界
        # ----------------------------------------------------

        barrier = (
            closed_wall
            |
            blocked_mask
        )

        # ----------------------------------------------------
        # 外部区域
        # ----------------------------------------------------

        external = _flood_external(
            barrier
        )

        # ----------------------------------------------------
        # 只允许 FREE
        # ----------------------------------------------------

        spawn = (
            free_mask
            &
            (~external)
        )

        spawn_count = int(
            np.sum(spawn)
        )

        components = _count_components(
            spawn
        )

        ratio = (
            spawn_count
            /
            max(
                free_count,
                1
            )
        )

        candidates.append(
            {
                "radius": radius,
                "barrier": barrier,
                "external": external,
                "spawn": spawn,
                "spawn_count": spawn_count,
                "components": components,
                "ratio": ratio
            }
        )

    # ========================================================
    # 选择策略
    # ========================================================

    # --------------------------------------------------------
    # 1. 如果某个尺度已经得到合理的内部区域，
    #    并且后续变化很小，优先选择较小尺度。
    # --------------------------------------------------------

    for i in range(
        1,
        len(candidates)
    ):

        previous = candidates[
            i - 1
        ]

        current = candidates[
            i
        ]

        previous_count = (
            previous["spawn_count"]
        )

        current_count = (
            current["spawn_count"]
        )

        if previous_count <= 0:
            continue

        increase = (
            current_count
            - previous_count
        )

        increase_ratio = (
            increase
            /
            previous_count
        )

        # ----------------------------------------------------
        # 如果增加已经很小，
        # 说明墙体缺口已经基本封闭。
        # ----------------------------------------------------

        if (
            current_count >= previous_count
            and
            increase_ratio < 0.03
        ):

            return (
                current["barrier"],
                current["radius"],
                current["spawn"]
            )

    # --------------------------------------------------------
    # 2. 如果前面的尺度都没有稳定，
    #    从后面的结果中选择一个合理结果。
    #
    #    不能直接选择 100% FREE。
    # --------------------------------------------------------

    reasonable = [
        item
        for item in candidates
        if (
            item["spawn_count"] > 0
            and
            item["ratio"] < 0.98
        )
    ]

    if reasonable:

        # 选择 Spawn Area 最大的合理结果
        best = max(
            reasonable,
            key=lambda item: (
                item["spawn_count"],
                -item["components"]
            )
        )

        return (
            best["barrier"],
            best["radius"],
            best["spawn"]
        )

    # --------------------------------------------------------
    # 3. 如果所有结果都不理想，
    #    选择 Spawn 数量最大的结果。
    # --------------------------------------------------------

    best = max(
        candidates,
        key=lambda item: item[
            "spawn_count"
        ]
    )

    return (
        best["barrier"],
        best["radius"],
        best["spawn"]
    )


# ============================================================
# 生成 Spawn Mask
# ============================================================

def generate_spawn_mask(
    map_data: Any
) -> SpawnMask:
    """
    ========================================================
    对外正式接口
    ========================================================

    支持：

        Grid
        JSON dict

    例如：

        spawn_mask = generate_spawn_mask(grid)

    或：

        spawn_mask = generate_spawn_mask(map_data)

    返回：

        {
            (x, y): True / False
        }

    True：
        允许生成人员

    False：
        禁止生成人员
    """

    # ========================================================
    # 1. 统一地图接口
    # ========================================================

    map_view = _MapView(
        map_data
    )

    width = map_view.width
    height = map_view.height

    # ========================================================
    # 2. 建立地图 Mask
    # ========================================================

    (
        free_mask,
        wall_mask,
        blocked_mask
    ) = _build_type_masks(
        map_view
    )

    # ========================================================
    # 3. 自动选择外墙缺口修复尺度
    # ========================================================

    (
        barrier,
        selected_radius,
        spawn_array
    ) = _select_wall_closing(
        wall_mask,
        free_mask,
        blocked_mask
    )

    # ========================================================
    # 4. 转成 dict
    # ========================================================

    spawn_mask: SpawnMask = {}

    for y in range(height):

        for x in range(width):

            spawn_mask[
                (x, y)
            ] = bool(
                spawn_array[
                    y,
                    x
                ]
            )

    # ========================================================
    # 5. 最终安全检查
    # ========================================================

    # --------------------------------------------------------
    # 任何非 FREE 元胞绝对不能 Spawn
    # --------------------------------------------------------

    for y in range(height):

        for x in range(width):

            cell = map_view.get_cell(
                x,
                y
            )

            if not _is_free(cell):

                spawn_mask[
                    (x, y)
                ] = False

    # ========================================================
    # 6. 统计
    # ========================================================

    total_cells = (
        width
        * height
    )

    free_count = int(
        np.sum(free_mask)
    )

    wall_count = int(
        np.sum(wall_mask)
    )

    spawn_count = sum(
        1
        for value in spawn_mask.values()
        if value
    )

    external_free_count = (
        free_count
        - spawn_count
    )

    # ========================================================
    # 7. 输出调试信息
    # ========================================================

    print()
    print(
        "=" * 70
    )

    print(
        "A Spawn Area 自动识别"
    )

    print(
        "=" * 70
    )

    print(
        f"[INFO] 地图尺寸："
        f"{width} × {height}"
    )

    print(
        f"[INFO] 总元胞："
        f"{total_cells}"
    )

    print(
        f"[INFO] FREE 元胞："
        f"{free_count}"
    )

    print(
        f"[INFO] WALL 元胞："
        f"{wall_count}"
    )

    print(
        f"[INFO] 外部 FREE："
        f"{external_free_count}"
    )

    print(
        f"[INFO] Spawn Cells："
        f"{spawn_count}"
    )

    print(
        f"[INFO] 自动墙体缺口修复尺度："
        f"{selected_radius}"
    )

    if free_count > 0:

        print(
            f"[INFO] Spawn 比例："
            f"{spawn_count / free_count:.2%}"
        )

    print(
        "=" * 70
    )

    return spawn_mask


# ============================================================
# 兼容函数
# ============================================================

def spawn_mask_to_array(
    spawn_mask: SpawnMask,
    width: int,
    height: int
) -> np.ndarray:
    """
    将 generate_spawn_mask() 返回的 dict
    转换成二维 numpy bool 数组。

    这是辅助函数。

    不改变 generate_spawn_mask() 的接口。
    """

    result = np.zeros(
        (height, width),
        dtype=bool
    )

    for (
        x,
        y
    ), value in spawn_mask.items():

        if (
            0 <= x < width
            and
            0 <= y < height
        ):

            result[
                y,
                x
            ] = bool(value)

    return result


# ============================================================
# 获取 Spawn Cell 坐标
# ============================================================

def get_spawn_cells(
    map_data: Any
):
    """
    返回所有允许生成的位置：

        [
            (x1, y1),
            (x2, y2),
            ...
        ]
    """

    mask = generate_spawn_mask(
        map_data
    )

    return [
        coordinate
        for coordinate, allowed
        in mask.items()
        if allowed
    ]


# ============================================================
# 单独测试
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 70)
    print(
        "spawn_area.py"
    )
    print("=" * 70)
    print()
    print(
        "正式接口："
    )
    print()
    print(
        "    generate_spawn_mask(map_data)"
    )
    print()
    print(
        "支持："
    )
    print(
        "    1. core.grid.Grid"
    )
    print(
        "    2. JSON dict"
    )
    print()
    print(
        "返回："
    )
    print(
        "    {(x, y): True / False}"
    )
    print()
