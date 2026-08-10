import numpy as np

from core.grid import Grid
from core.schema import Cell, CellType



def binary_to_grid(
        binary,
        cell_size=10
):
    """
    二值图转换为CA元胞Grid

    约定：
    白色(255) -> WALL
    黑色(0)   -> FREE

    参数:
        binary:
            OpenCV二值图

        cell_size:
            一个元胞对应多少像素

    返回:
        Grid
    """


    height, width = binary.shape


    # 元胞数量
    grid_width = width // cell_size
    grid_height = height // cell_size


    cells = []


    for y in range(grid_height):

        for x in range(grid_width):


            # 当前元胞对应图片区域

            block = binary[
                y * cell_size:(y + 1) * cell_size,
                x * cell_size:(x + 1) * cell_size
            ]


            # 计算白色比例

            white_ratio = np.mean(
                block == 255
            )


            if white_ratio > 0.5:

                cell_type = CellType.WALL

            else:

                cell_type = CellType.FREE



            cells.append(
                Cell(
                    x=x,
                    y=y,
                    cell_type=cell_type
                )
            )


    return Grid(
        width=grid_width,
        height=grid_height,
        cell_size=cell_size,
        cells=cells
    )
