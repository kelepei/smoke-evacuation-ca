"""
A09 - 场景模板管理

功能：
1. 提供教室、商场、食堂、宿舍标准场景；
2. 根据模板名称生成统一的 core.grid.Grid；
3. 使用项目已经统一的 Cell / CellType / SemanticType；
4. 支持保存为 JSON；
5. JSON 可以继续交给 A04 JSON loader；
6. 不修改 schema.py；
7. 出口设置在地图最外边界，作为边界墙缺口；
8. 宿舍采用“左侧房间 + 中央走廊 + 右侧房间”的结构；
9. 为 D 可视化模块提供统一的 Grid 获取接口。

支持场景：
    classroom
    mall
    canteen
    dormitory

生成：
    maps/templates/classroom.json
    maps/templates/mall.json
    maps/templates/canteen.json
    maps/templates/dormitory.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from core.grid import Grid
from core.schema import Cell, CellType, SemanticType


# ============================================================
# 1. 场景模板数据结构
# ============================================================

@dataclass(frozen=True)
class ScenarioTemplate:
    """
    一个场景模板。

    name:
        程序内部使用的名称。

    display_name:
        用于前端/可视化显示的名称。

    description:
        场景描述。

    builder:
        真正生成 Grid 的函数。
    """

    name: str
    display_name: str
    description: str
    builder: Callable[[], Grid]

    def build(self) -> Grid:
        return self.builder()


# ============================================================
# 2. SemanticType 兼容处理
# ============================================================

def _get_semantic(
    preferred_name: str,
    fallback_name: str | None = None,
) -> SemanticType | None:
    """
    从当前 schema.py 中动态获取 SemanticType。

    如果 schema.py 中不存在 preferred_name，
    则尝试使用 fallback_name。

    不修改 schema.py。
    """

    value = getattr(
        SemanticType,
        preferred_name,
        None,
    )

    if value is not None:
        return value

    if fallback_name is not None:
        value = getattr(
            SemanticType,
            fallback_name,
            None,
        )

    return value


# 当前项目使用的语义
SEM_CLASSROOM = _get_semantic("CLASSROOM")
SEM_CORRIDOR = _get_semantic("CORRIDOR")
SEM_SHOP = _get_semantic("SHOP")
SEM_HALL = _get_semantic("HALL")
SEM_CANTEEN = _get_semantic("CANTEEN")

# 如果 schema 中没有 DORM，则使用 CORRIDOR 作为兼容语义
SEM_DORM = _get_semantic(
    "DORM",
    "CORRIDOR",
)


# ============================================================
# 3. 创建基础 Grid
# ============================================================

def _create_grid(
    width: int,
    height: int,
    cell_size: float = 0.5,
) -> Grid:
    """
    创建全部为 FREE 的基础 Grid。

    cells 使用行优先存储：

        cells[y * width + x]
    """

    cells: list[Cell] = []

    for y in range(height):

        for x in range(width):

            cells.append(
                Cell(
                    x=x,
                    y=y,
                    cell_type=CellType.FREE,
                    room_id=None,
                    semantic=None,
                    smoke=0.0,
                    risk=0.0,
                    guidance=0.0,
                )
            )

    return Grid(
        width=width,
        height=height,
        cell_size=cell_size,
        cells=cells,
    )


# ============================================================
# 4. 获取 Cell
# ============================================================

def _get_cell(
    grid: Grid,
    x: int,
    y: int,
) -> Cell:

    if not (
        0 <= x < grid.width
        and 0 <= y < grid.height
    ):
        raise ValueError(
            f"坐标 ({x}, {y}) 超出地图范围 "
            f"{grid.width} x {grid.height}"
        )

    return grid.cells[
        y * grid.width + x
    ]


# ============================================================
# 5. 设置 Cell
# ============================================================

def _set_cell(
    grid: Grid,
    x: int,
    y: int,
    cell_type: CellType,
    *,
    room_id: str | None = None,
    semantic: SemanticType | None = None,
    smoke: float = 0.0,
    risk: float = 0.0,
    guidance: float = 0.0,
) -> None:

    cell = _get_cell(
        grid,
        x,
        y,
    )

    cell.cell_type = cell_type
    cell.room_id = room_id
    cell.semantic = semantic
    cell.smoke = float(smoke)
    cell.risk = float(risk)
    cell.guidance = float(guidance)


# ============================================================
# 6. 设置墙
# ============================================================

def _set_wall(
    grid: Grid,
    x: int,
    y: int,
) -> None:

    _set_cell(
        grid,
        x,
        y,
        CellType.WALL,
    )


# ============================================================
# 7. 设置障碍物
# ============================================================

def _set_obstacle(
    grid: Grid,
    x: int,
    y: int,
) -> None:

    _set_cell(
        grid,
        x,
        y,
        CellType.OBSTACLE,
    )


# ============================================================
# 8. 设置出口
# ============================================================

def _set_exit(
    grid: Grid,
    x: int,
    y: int,
) -> None:
    """
    设置地图出口。

    出口必须位于地图边界。
    """

    if not (
        x == 0
        or x == grid.width - 1
        or y == 0
        or y == grid.height - 1
    ):
        raise ValueError(
            f"出口 ({x}, {y}) 不在地图边界上"
        )

    _set_cell(
        grid,
        x,
        y,
        CellType.EXIT,
    )


# ============================================================
# 9. 设置语义
# ============================================================

def _set_semantic(
    grid: Grid,
    x: int,
    y: int,
    semantic: SemanticType | None,
    room_id: str | None = None,
) -> None:

    cell = _get_cell(
        grid,
        x,
        y,
    )

    cell.semantic = semantic

    if room_id is not None:
        cell.room_id = room_id


# ============================================================
# 10. 矩形区域设置语义
# ============================================================

def _set_rectangle_semantic(
    grid: Grid,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    semantic: SemanticType | None,
    room_id: str | None = None,
) -> None:

    for y in range(
        y1,
        y2 + 1,
    ):

        for x in range(
            x1,
            x2 + 1,
        ):

            if (
                0 <= x < grid.width
                and
                0 <= y < grid.height
            ):

                _set_semantic(
                    grid,
                    x,
                    y,
                    semantic,
                    room_id,
                )


# ============================================================
# 11. 外边界墙
# ============================================================

def _set_border_walls(
    grid: Grid,
) -> None:
    """
    设置四周边界墙。

    出口后续会直接替换边界墙元胞。
    """

    width = grid.width
    height = grid.height

    # 上下边界
    for x in range(width):

        _set_wall(
            grid,
            x,
            0,
        )

        _set_wall(
            grid,
            x,
            height - 1,
        )

    # 左右边界
    for y in range(height):

        _set_wall(
            grid,
            0,
            y,
        )

        _set_wall(
            grid,
            width - 1,
            y,
        )


# ============================================================
# 12. 教室模板
# ============================================================

def build_classroom_template() -> Grid:

    width = 20
    height = 12

    grid = _create_grid(
        width,
        height,
    )

    _set_border_walls(grid)

    # 教室区域
    _set_rectangle_semantic(
        grid,
        1,
        1,
        18,
        10,
        SEM_CLASSROOM,
        "classroom_01",
    )

    # 课桌
    desk_positions = [
        (4, 3),
        (7, 3),
        (10, 3),
        (13, 3),

        (4, 5),
        (7, 5),
        (10, 5),
        (13, 5),

        (4, 7),
        (7, 7),
        (10, 7),
        (13, 7),
    ]

    for x, y in desk_positions:

        _set_obstacle(
            grid,
            x,
            y,
        )

    # 讲台
    for x in range(8, 12):

        _set_obstacle(
            grid,
            x,
            9,
        )

    # 出口：底部边界
    _set_exit(
        grid,
        17,
        height - 1,
    )

    # 出口附近引导区域
    for x in range(15, 18):

        _set_cell(
            grid,
            x,
            10,
            CellType.FREE,
            semantic=SEM_CLASSROOM,
            room_id="classroom_01",
            guidance=1.0,
        )

    return grid


# ============================================================
# 13. 商场模板
# ============================================================

def build_mall_template() -> Grid:

    width = 30
    height = 20

    grid = _create_grid(
        width,
        height,
    )

    _set_border_walls(grid)

    # 整体商场
    _set_rectangle_semantic(
        grid,
        1,
        1,
        28,
        18,
        SEM_SHOP,
        "mall",
    )

    # 左上商铺
    _set_rectangle_semantic(
        grid,
        2,
        2,
        7,
        6,
        SEM_SHOP,
        "shop_01",
    )

    for y in range(2, 8):

        _set_wall(
            grid,
            8,
            y,
        )

    for x in range(2, 9):

        _set_wall(
            grid,
            x,
            7,
        )

    # 右上商铺
    _set_rectangle_semantic(
        grid,
        22,
        2,
        27,
        6,
        SEM_SHOP,
        "shop_02",
    )

    for y in range(2, 8):

        _set_wall(
            grid,
            21,
            y,
        )

    for x in range(21, 28):

        _set_wall(
            grid,
            x,
            7,
        )

    # 中央大厅
    _set_rectangle_semantic(
        grid,
        9,
        2,
        20,
        17,
        SEM_HALL,
        "mall_hall",
    )

    # 中央障碍物
    for x in (11, 14, 17):

        for y in (5, 10, 15):

            _set_obstacle(
                grid,
                x,
                y,
            )

    # 左下商铺
    _set_rectangle_semantic(
        grid,
        2,
        10,
        8,
        17,
        SEM_SHOP,
        "shop_03",
    )

    # 右下商铺
    _set_rectangle_semantic(
        grid,
        21,
        10,
        27,
        17,
        SEM_SHOP,
        "shop_04",
    )

    # 出口
    _set_exit(
        grid,
        0,
        9,
    )

    _set_exit(
        grid,
        width - 1,
        9,
    )

    _set_exit(
        grid,
        15,
        height - 1,
    )

    return grid


# ============================================================
# 14. 食堂模板
# ============================================================

def build_canteen_template() -> Grid:

    width = 24
    height = 16

    grid = _create_grid(
        width,
        height,
    )

    _set_border_walls(grid)

    # 整体食堂
    _set_rectangle_semantic(
        grid,
        1,
        1,
        22,
        14,
        SEM_CANTEEN,
        "canteen",
    )

    # 厨房
    _set_rectangle_semantic(
        grid,
        16,
        2,
        21,
        6,
        SEM_CANTEEN,
        "kitchen",
    )

    # 厨房左侧墙
    for y in range(2, 7):

        _set_wall(
            grid,
            15,
            y,
        )

    # 厨房设备
    for x in (17, 19):

        for y in (3, 5):

            _set_obstacle(
                grid,
                x,
                y,
            )

    # 餐桌
    table_positions = [
        (4, 4),
        (8, 4),
        (12, 4),

        (4, 8),
        (8, 8),
        (12, 8),

        (4, 12),
        (8, 12),
        (12, 12),
    ]

    for x, y in table_positions:

        _set_obstacle(
            grid,
            x,
            y,
        )

        if x + 1 < width - 1:

            _set_obstacle(
                grid,
                x + 1,
                y,
            )

    # 出口
    _set_exit(
        grid,
        0,
        8,
    )

    _set_exit(
        grid,
        width - 1,
        8,
    )

    _set_exit(
        grid,
        12,
        height - 1,
    )

    return grid


# ============================================================
# 15. 宿舍模板
# ============================================================

def build_dormitory_template() -> Grid:

    width = 24
    height = 20

    grid = _create_grid(
        width,
        height,
    )

    _set_border_walls(grid)

    # ========================================================
    # 中央走廊
    # ========================================================

    _set_rectangle_semantic(
        grid,
        9,
        1,
        14,
        18,
        SEM_CORRIDOR,
        "dorm_corridor",
    )

    # ========================================================
    # 左侧宿舍
    # ========================================================

    left_rooms = [
        (
            "dorm_left_01",
            1,
            1,
            8,
            5,
        ),
        (
            "dorm_left_02",
            1,
            7,
            8,
            11,
        ),
        (
            "dorm_left_03",
            1,
            13,
            8,
            18,
        ),
    ]

    for (
        room_id,
        x1,
        y1,
        x2,
        y2,
    ) in left_rooms:

        _set_rectangle_semantic(
            grid,
            x1,
            y1,
            x2,
            y2,
            SEM_DORM,
            room_id,
        )

        # 房间右边界
        for y in range(
            y1,
            y2 + 1,
        ):

            _set_wall(
                grid,
                x2,
                y,
            )

        # 房间门
        door_y = (
            y1 + y2
        ) // 2

        _set_cell(
            grid,
            x2,
            door_y,
            CellType.FREE,
            semantic=SEM_DORM,
            room_id=room_id,
        )

    # ========================================================
    # 右侧宿舍
    # ========================================================

    right_rooms = [
        (
            "dorm_right_01",
            15,
            1,
            22,
            5,
        ),
        (
            "dorm_right_02",
            15,
            7,
            22,
            11,
        ),
        (
            "dorm_right_03",
            15,
            13,
            22,
            18,
        ),
    ]

    for (
        room_id,
        x1,
        y1,
        x2,
        y2,
    ) in right_rooms:

        _set_rectangle_semantic(
            grid,
            x1,
            y1,
            x2,
            y2,
            SEM_DORM,
            room_id,
        )

        # 房间左边界
        for y in range(
            y1,
            y2 + 1,
        ):

            _set_wall(
                grid,
                x1,
                y,
            )

        # 房间门
        door_y = (
            y1 + y2
        ) // 2

        _set_cell(
            grid,
            x1,
            door_y,
            CellType.FREE,
            semantic=SEM_DORM,
            room_id=room_id,
        )

    # ========================================================
    # 左侧房间横向隔墙
    # ========================================================

    for x in range(1, 9):

        _set_wall(
            grid,
            x,
            6,
        )

    for x in range(1, 9):

        _set_wall(
            grid,
            x,
            12,
        )

    # ========================================================
    # 右侧房间横向隔墙
    # ========================================================

    for x in range(15, 23):

        _set_wall(
            grid,
            x,
            6,
        )

    for x in range(15, 23):

        _set_wall(
            grid,
            x,
            12,
        )

    # ========================================================
    # 床铺
    # ========================================================

    bed_positions = [

        # 左侧
        (3, 2),
        (6, 2),

        (3, 9),
        (6, 9),

        (3, 14),
        (6, 14),

        # 右侧
        (17, 2),
        (20, 2),

        (17, 9),
        (20, 9),

        (17, 14),
        (20, 14),
    ]

    for x, y in bed_positions:

        _set_obstacle(
            grid,
            x,
            y,
        )

        if y + 1 < grid.height - 1:

            _set_obstacle(
                grid,
                x,
                y + 1,
            )

    # ========================================================
    # 出口
    # ========================================================

    _set_exit(
        grid,
        11,
        0,
    )

    _set_exit(
        grid,
        12,
        height - 1,
    )

    return grid


# ============================================================
# 16. 场景模板管理器
# ============================================================

class ScenarioTemplateManager:
    """
    A09 场景模板管理器。

    统一管理四种场景。
    """

    def __init__(self) -> None:

        self._templates: dict[
            str,
            ScenarioTemplate,
        ] = {

            "classroom": ScenarioTemplate(
                name="classroom",
                display_name="Classroom",
                description=(
                    "Standard classroom "
                    "evacuation scene"
                ),
                builder=build_classroom_template,
            ),

            "mall": ScenarioTemplate(
                name="mall",
                display_name="Mall",
                description=(
                    "Multi-shop and "
                    "multi-exit mall scene"
                ),
                builder=build_mall_template,
            ),

            "canteen": ScenarioTemplate(
                name="canteen",
                display_name="Canteen",
                description=(
                    "Canteen with tables, "
                    "kitchen and exits"
                ),
                builder=build_canteen_template,
            ),

            "dormitory": ScenarioTemplate(
                name="dormitory",
                display_name="Dormitory",
                description=(
                    "Dormitory rooms with "
                    "a central corridor"
                ),
                builder=build_dormitory_template,
            ),
        }

    # ========================================================
    # 获取模板列表
    # ========================================================

    def list_templates(self) -> list[str]:

        return list(
            self._templates.keys()
        )

    # ========================================================
    # 获取模板
    # ========================================================

    def get_template(
        self,
        name: str,
    ) -> ScenarioTemplate:

        key = name.strip().lower()

        if key not in self._templates:

            available = ", ".join(
                self._templates.keys()
            )

            raise ValueError(
                f"Unknown scenario template: "
                f"{name!r}. "
                f"Available: {available}"
            )

        return self._templates[key]

    # ========================================================
    # 创建 Grid
    # ========================================================

    def create(
        self,
        name: str,
    ) -> Grid:

        template = self.get_template(
            name
        )

        return template.build()

    # ========================================================
    # 获取场景描述
    # ========================================================

    def describe(
        self,
        name: str,
    ) -> dict[str, str]:

        template = self.get_template(
            name
        )

        return {
            "name": template.name,
            "display_name": template.display_name,
            "description": template.description,
        }

    # ========================================================
    # 保存 JSON
    # ========================================================

    def save(
        self,
        name: str,
        output_path: str | Path,
    ) -> Path:

        grid = self.create(name)

        output = Path(
            output_path
        )

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        payload = {

            "name": name,

            "width": grid.width,

            "height": grid.height,

            "cell_size": grid.cell_size,

            "cells": [],
        }

        for cell in grid.cells:

            # CellType
            if hasattr(
                cell.cell_type,
                "value",
            ):

                cell_type = (
                    cell.cell_type.value
                )

            else:

                cell_type = str(
                    cell.cell_type
                )

            # SemanticType
            semantic = None

            if cell.semantic is not None:

                if hasattr(
                    cell.semantic,
                    "value",
                ):

                    semantic = (
                        cell.semantic.value
                    )

                else:

                    semantic = str(
                        cell.semantic
                    )

            payload["cells"].append(
                {
                    "x": cell.x,
                    "y": cell.y,
                    "type": cell_type,
                    "room_id": cell.room_id,
                    "semantic": semantic,
                    "smoke": cell.smoke,
                    "risk": cell.risk,
                    "guidance": cell.guidance,
                }
            )

        output.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        return output


# ============================================================
# 17. 创建全局模板管理器
# ============================================================


_template_manager = (
    ScenarioTemplateManager()
)


# ============================================================
# 18. 全局接口：获取模板列表
# ============================================================

def list_templates() -> list[str]:
    """
    获取所有可用场景。

    返回：

        [
            "classroom",
            "mall",
            "canteen",
            "dormitory"
        ]
    """

    return _template_manager.list_templates()


# ============================================================
# 19. 全局接口：创建场景模板
# ============================================================

def create_scenario_template(
    name: str,
) -> Grid:
    """
    创建指定场景。

    示例：

        grid = create_scenario_template(
            "classroom"
        )
    """

    return _template_manager.create(
        name
    )


# ============================================================
# 20. A09 → D 可视化接口
# ============================================================

def get_scene_for_visualization(
    name: str,
) -> Grid:
    """
    给 D 可视化模块使用。

    根据场景名称返回统一的 core.grid.Grid。

    支持：

        classroom
        mall
        canteen
        dormitory

    示例：

        grid = get_scene_for_visualization(
            "classroom"
        )

    D 不需要修改 A09。
    """

    return _template_manager.create(
        name
    )


# ============================================================
# 21. 获取场景信息
# ============================================================

def get_scene_info(
    name: str,
) -> dict[str, str]:
    """
    获取场景名称、显示名称和描述。
    """

    return _template_manager.describe(
        name
    )


# ============================================================
# 22. 保存场景模板
# ============================================================

def save_scenario_template(
    name: str,
    output_path: str | Path,
) -> Path:
    """
    创建并保存场景 JSON。

    示例：

        save_scenario_template(
            "classroom",
            "maps/templates/classroom.json"
        )
    """

    return _template_manager.save(
        name,
        output_path,
    )


# ============================================================
# 23. 批量生成四个场景
# ============================================================

def generate_all_templates(
    output_dir: str | Path = "maps/templates",
) -> list[Path]:
    """
    一次生成全部四个标准场景。

    返回生成的 JSON 路径列表。
    """

    output_dir = Path(
        output_dir
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    generated_files: list[Path] = []

    for name in list_templates():

        output_path = (
            output_dir
            / f"{name}.json"
        )

        saved_path = (
            save_scenario_template(
                name,
                output_path,
            )
        )

        generated_files.append(
            saved_path
        )

    return generated_files


# ============================================================
# 24. 场景统计
# ============================================================

def get_scene_statistics(
    grid: Grid,
) -> dict[str, int]:
    """
    统计 Grid 中不同类型元胞数量。
    """

    statistics = {

        "free": 0,

        "wall": 0,

        "obstacle": 0,

        "exit": 0,

        "smoke_source": 0,
    }

    for cell in grid.cells:

        cell_type = cell.cell_type

        if cell_type == CellType.FREE:

            statistics["free"] += 1

        elif cell_type == CellType.WALL:

            statistics["wall"] += 1

        elif cell_type == CellType.OBSTACLE:

            statistics["obstacle"] += 1

        elif cell_type == CellType.EXIT:

            statistics["exit"] += 1

        elif (
            cell_type
            == CellType.SMOKE_SOURCE
        ):

            statistics[
                "smoke_source"
            ] += 1

    return statistics


# ============================================================
# 25. 命令行测试
# ============================================================

def main() -> None:

    print("=" * 60)

    print(
        "A09 Scenario Template Manager"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # 显示模板
    # --------------------------------------------------------

    print()

    print(
        "Available templates:"
    )

    for name in list_templates():

        info = get_scene_info(
            name
        )

        print(
            f"  {info['name']:12s}"
            f" | {info['display_name']:12s}"
            f" | {info['description']}"
        )

    # --------------------------------------------------------
    # 输出目录
    # --------------------------------------------------------

    output_dir = (
        Path("maps")
        / "templates"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 生成全部模板
    # --------------------------------------------------------

    print()

    print(
        "Generating templates..."
    )

    for name in list_templates():

        grid = get_scene_for_visualization(
            name
        )

        output_path = (
            output_dir
            / f"{name}.json"
        )

        save_scenario_template(
            name,
            output_path,
        )

        statistics = (
            get_scene_statistics(
                grid
            )
        )

        print()

        print(
            f"{name}:"
        )

        print(
            f"  size: "
            f"{grid.width} x "
            f"{grid.height}"
        )

        print(
            f"  cells: "
            f"{len(grid.cells)}"
        )

        print(
            f"  free: "
            f"{statistics['free']}"
        )

        print(
            f"  walls: "
            f"{statistics['wall']}"
        )

        print(
            f"  obstacles: "
            f"{statistics['obstacle']}"
        )

        print(
            f"  exits: "
            f"{statistics['exit']}"
        )

        print(
            f"  smoke sources: "
            f"{statistics['smoke_source']}"
        )

        print(
            f"  JSON: "
            f"{output_path}"
        )

    print()

    print(
        "A09 template generation completed."
    )


# ============================================================
# 26. 程序入口
# ============================================================

if __name__ == "__main__":

    main()
