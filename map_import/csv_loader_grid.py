import csv

from core.schema import Cell, CellType
from core.grid import Grid


def load_csv_grid(filename):
    

    cells = []

    max_x = 0
    max_y = 0

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:

        reader = csv.DictReader(f)

        for row in reader:

            x = int(row["x"])
            y = int(row["y"])

            cell_type = CellType(
                row["type"]
            )

            cell = Cell(
                x=x,
                y=y,
                cell_type=cell_type
            )

            cells.append(cell)

            max_x = max(max_x, x)
            max_y = max(max_y, y)

    # ==========================
    # 1. 检查是否存在重复坐标
    # ==========================

    positions = set()

    for cell in cells:

        pos = (cell.x, cell.y)

        if pos in positions:
            raise ValueError(
                f"CSV中存在重复坐标：{pos}"
            )

        positions.add(pos)

    # ==========================
    # 2. 检查元胞数量是否完整
    # ==========================

    expected_count = (max_x + 1) * (max_y + 1)

    if len(cells) != expected_count:
        raise ValueError(
            f"CSV元胞数量错误！理论数量：{expected_count}，实际数量：{len(cells)}"
        )

    # ==========================
    # 3. 检查是否缺少坐标
    # ==========================

    for y in range(max_y + 1):

        for x in range(max_x + 1):

            if (x, y) not in positions:
                raise ValueError(
                    f"CSV缺少元胞坐标：({x}, {y})"
                )

    # ==========================
    # 4. 按行优先(Row-major)排序
    # 排序后：
    # (0,0),(1,0)...(width-1,0)
    # (0,1),(1,1)...(width-1,1)
    # ==========================

    cells.sort(
        key=lambda cell: (cell.y, cell.x)
    )

    # ==========================
    # 5. 构建Grid对象
    # ==========================

    grid = Grid(
        width=max_x + 1,
        height=max_y + 1,
        cell_size=0.5,
        cells=cells
    )

    return grid
