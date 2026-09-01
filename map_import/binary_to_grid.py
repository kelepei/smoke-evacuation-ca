"""
二值图片 -> CA Grid

============================================================

功能：

    binary
       ↓
    CA Grid

统一规则：

    binary == 255
        ↓
       WALL

    binary == 0
        ↓
       FREE

============================================================

比例尺：

项目规定：

    1 个 CA 元胞 = 0.5m × 0.5m

但是：

    PNG 像素
    和
    CA 元胞

不是同一个概念。

因此：

    cell_size

表示：

    PNG 中多少像素对应一个 CA 元胞。

例如：

    cell_size = 10

表示：

    10 × 10 PNG 像素
        ↓
    1 个 CA 元胞

同时：

    Grid.cell_size = 0.5

表示：

    1 个 CA 元胞
        =
    0.5m × 0.5m

============================================================

接口保持不变：

    binary_to_grid(
        binary,
        cell_size=10
    )
"""

import cv2
import numpy as np

from core.grid import Grid
from core.schema import Cell, CellType


# ============================================================
# 项目统一的 CA 元胞物理尺寸
# ============================================================

REAL_CELL_SIZE = 0.5


# ============================================================
# 二值图 -> Grid
# ============================================================

def binary_to_grid(
        binary,
        cell_size=10
):
    """
    二值图转换为 CA 元胞 Grid。

    参数：

        binary：
            OpenCV 二值图片。

        cell_size：
            PNG 像素采样大小。

            例如：

                cell_size=10

            表示：

                10×10 PNG 像素
                →
                1个CA元胞

            用户可以根据地图实际比例
            传入不同的数值。

    返回：

        Grid

    注意：

        返回 Grid 的 cell_size
        永远为 0.5m。
    """

    # ========================================================
    # 1. 检查 binary
    # ========================================================

    if binary is None:

        raise ValueError(
            "binary 不能为空"
        )

    if not isinstance(
        binary,
        np.ndarray
    ):

        raise TypeError(
            "binary 必须是 numpy.ndarray"
        )

    if binary.ndim != 2:

        raise ValueError(
            "binary 必须是单通道图片"
        )

    # ========================================================
    # 2. 检查 cell_size
    # ========================================================

    try:

        cell_size = float(
            cell_size
        )

    except (
        TypeError,
        ValueError
    ) as exc:

        raise ValueError(
            "cell_size 必须是正数"
        ) from exc

    if cell_size <= 0:

        raise ValueError(
            "cell_size 必须大于 0"
        )

    # ========================================================
    # 3. 确保 binary 为真正的二值图
    #
    # 如果外部传进来的不是：
    #
    #     0 / 255
    #
    # 则自动使用 OTSU 二值化。
    # ========================================================

    unique_values = np.unique(
        binary
    )

    is_binary = np.all(
        np.isin(
            unique_values,
            [0, 255]
        )
    )

    if not is_binary:

        _, binary = cv2.threshold(
            binary,
            0,
            255,
            cv2.THRESH_BINARY
            +
            cv2.THRESH_OTSU
        )

    # ========================================================
    # 4. 获取图片尺寸
    # ========================================================

    height, width = binary.shape

    # ========================================================
    # 5. 计算 CA Grid 尺寸
    #
    # 使用 ceil：
    #
    # 保留图片边缘不足一个 cell_size
    # 的区域。
    #
    # 例如：
    #
    #     width = 125
    #     cell_size = 10
    #
    #     125 / 10 = 12.5
    #
    #     Grid width = 13
    #
    # 不会直接丢掉最后 5 个像素。
    # ========================================================

    grid_width = int(
        np.ceil(
            width / cell_size
        )
    )

    grid_height = int(
        np.ceil(
            height / cell_size
        )
    )

    # ========================================================
    # 6. 创建 Cell
    # ========================================================

    cells = []

    for y in range(
        grid_height
    ):

        for x in range(
            grid_width
        ):

            # ------------------------------------------------
            # 当前 CA 元胞对应的 PNG 范围
            # ------------------------------------------------

            x1 = int(
                round(
                    x * cell_size
                )
            )

            y1 = int(
                round(
                    y * cell_size
                )
            )

            x2 = int(
                round(
                    (x + 1)
                    *
                    cell_size
                )
            )

            y2 = int(
                round(
                    (y + 1)
                    *
                    cell_size
                )
            )

            # ------------------------------------------------
            # 防止超出图片
            # ------------------------------------------------

            x1 = max(
                0,
                min(
                    x1,
                    width
                )
            )

            x2 = max(
                0,
                min(
                    x2,
                    width
                )
            )

            y1 = max(
                0,
                min(
                    y1,
                    height
                )
            )

            y2 = max(
                0,
                min(
                    y2,
                    height
                )
            )

            # ------------------------------------------------
            # 无效区域直接跳过
            # ------------------------------------------------

            if (
                x2 <= x1
                or
                y2 <= y1
            ):

                continue

            # ------------------------------------------------
            # 获取当前元胞对应的图片区域
            # ------------------------------------------------

            block = binary[
                y1:y2,
                x1:x2
            ]

            # ------------------------------------------------
            # 计算墙体比例
            #
            # 白色 = WALL
            # 黑色 = FREE
            # ------------------------------------------------

            white_ratio = float(
                np.mean(
                    block == 255
                )
            )

            # ------------------------------------------------
            # 元胞类型判断
            #
            # 白色超过一半：
            #
            #     WALL
            #
            # 否则：
            #
            #     FREE
            # ------------------------------------------------

            if white_ratio > 0.5:

                cell_type = (
                    CellType.WALL
                )

            else:

                cell_type = (
                    CellType.FREE
                )

            # ------------------------------------------------
            # 创建统一 Cell
            # ------------------------------------------------

            cells.append(
                Cell(
                    x=x,
                    y=y,
                    cell_type=cell_type
                )
            )

    # ========================================================
    # 7. 创建统一 Grid
    #
    # 非常重要：
    #
    #     cell_size
    #
    # 是 PNG 像素采样大小。
    #
    # 而：
    #
    #     REAL_CELL_SIZE
    #
    # 才是 CA 元胞物理尺寸。
    # ========================================================

    grid = Grid(
        width=grid_width,
        height=grid_height,
        cell_size=REAL_CELL_SIZE,
        cells=cells
    )

    return grid
