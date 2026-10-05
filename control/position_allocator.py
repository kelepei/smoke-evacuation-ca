# control/position_allocator.py

import json
import random
from collections import defaultdict
from pathlib import Path

from core.schema import CellType


# ============================================================
# 1. profile -> semantic
# ============================================================

PROFILE_SEMANTIC = {
    "student": ["classroom"],
    "teacher": ["classroom"],
    "staff": ["classroom"],

    "shopper": ["shop"],
    "customer": ["shop"],

    "diner": ["canteen"],
    "visitor": ["hall"],

    "resident": ["dorm"],
    "dormitory": ["dorm"],

    "reader": ["library"],

    "patient": ["hospital"],
}


# ============================================================
# 2. JSON
# ============================================================

def load_json(filename):
    filename = Path(filename).resolve()

    if not filename.exists():
        raise FileNotFoundError(
            f"文件不存在：{filename}"
        )

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:
        return json.load(f)


# ============================================================
# 3. 人员属性兼容
# ============================================================

def _get_person_attribute(
        person,
        attribute,
        default=None
):
    """
    同时支持：

    1. dict
    2. Person 对象
    """

    if isinstance(person, dict):
        return person.get(
            attribute,
            default
        )

    return getattr(
        person,
        attribute,
        default
    )


def _set_person_attribute(
        person,
        attribute,
        value
):
    """
    只用于修改 A 负责的人员位置 x/y。
    """

    if isinstance(person, dict):
        person[attribute] = value
    else:
        setattr(
            person,
            attribute,
            value
        )


# ============================================================
# 4. 人员数据检查
# ============================================================

def validate_people_data(people):

    if not isinstance(
        people,
        list
    ):
        raise ValueError(
            "人员数据必须是 list"
        )

    for index, person in enumerate(people):

        if (
            not isinstance(person, dict)
            and not hasattr(person, "__dict__")
        ):
            raise ValueError(
                f"第 {index} 个人员数据格式错误"
            )

        person_id = _get_person_attribute(
            person,
            "id",
            None
        )

        if person_id is None:
            raise ValueError(
                f"第 {index} 个人员缺少 id"
            )


# ============================================================
# 5. 地图数据检查
# ============================================================

def validate_map_data(map_data):

    if not isinstance(
        map_data,
        dict
    ):
        raise ValueError(
            "地图数据必须是 dict"
        )

    required_fields = [
        "width",
        "height",
        "cells"
    ]

    for field in required_fields:

        if field not in map_data:
            raise ValueError(
                f"地图缺少字段：{field}"
            )

    width = int(
        map_data["width"]
    )

    height = int(
        map_data["height"]
    )

    cells = map_data["cells"]

    if width <= 0 or height <= 0:
        raise ValueError(
            "地图 width / height 必须大于 0"
        )

    if not isinstance(
        cells,
        list
    ):
        raise ValueError(
            "地图 cells 必须是 list"
        )

    expected = width * height

    if len(cells) != expected:

        raise ValueError(
            f"地图 cells 数量错误："
            f"实际 {len(cells)}，"
            f"应该是 {expected}"
        )


# ============================================================
# 6. CellType
# ============================================================

def _get_cell_type(cell):

    if isinstance(
        cell,
        dict
    ):
        value = cell.get(
            "type"
        )
    else:
        value = getattr(
            cell,
            "cell_type",
            None
        )

    if isinstance(
        value,
        CellType
    ):
        return value

    if value is None:
        return None

    try:
        return CellType(value)

    except ValueError:
        return None


# ============================================================
# 7. 坐标
# ============================================================

def _get_cell_coordinate(cell):

    if isinstance(
        cell,
        dict
    ):
        return (
            int(cell.get("x", 0)),
            int(cell.get("y", 0))
        )

    return (
        int(getattr(cell, "x", 0)),
        int(getattr(cell, "y", 0))
    )


# ============================================================
# 8. semantic
# ============================================================

def _get_cell_semantic(cell):

    if isinstance(
        cell,
        dict
    ):
        semantic = cell.get(
            "semantic"
        )

    else:
        semantic = getattr(
            cell,
            "semantic",
            None
        )

    if semantic is None:
        return None

    if hasattr(
        semantic,
        "value"
    ):
        return semantic.value

    return str(
        semantic
    )


# ============================================================
# 9. spawn_area
# ============================================================

def _parse_spawn_area_value(value):
    """
    把各种可能的 spawn_area 表达方式统一成：

        True  = 可以生成
        False = 不可以生成
        None  = 没有提供 spawn_area 信息
    """

    if value is None:
        return None

    # bool
    if isinstance(
        value,
        bool
    ):
        return value

    # 数字
    if isinstance(
        value,
        (int, float)
    ):
        if value == 1:
            return True

        if value == 0:
            return False

    # 字符串
    if isinstance(
        value,
        str
    ):

        text = value.strip().lower()

        true_values = {
            "true",
            "1",
            "yes",
            "y",
            "inside",
            "interior",
            "gray",
            "grey",
            "spawn",
            "spawn_area",
            "allowed",
            "allow",
            "building"
        }

        false_values = {
            "false",
            "0",
            "no",
            "n",
            "outside",
            "exterior",
            "white",
            "none",
            "forbidden",
            "forbid",
            "blocked",
            "not_spawn"
        }

        if text in true_values:
            return True

        if text in false_values:
            return False

    return None


def _get_cell_spawn_area(cell):
    """
    从一个 cell 中读取 spawn_area。

    支持：

        "spawn_area": true

    或：

        "spawn_area": false

    或：

        "spawn_area": "gray"

    或：

        "spawn_area": "white"

    同时兼容一些可能的字段名称。
    """

    if not isinstance(
        cell,
        dict
    ):
        return None

    possible_keys = [
        "spawn_area",
        "spawn",
        "spawnable",
        "can_spawn",
        "inside"
    ]

    for key in possible_keys:

        if key not in cell:
            continue

        result = _parse_spawn_area_value(
            cell.get(key)
        )

        if result is not None:
            return result

    return None


# ============================================================
# 10. 地图级 spawn_area
# ============================================================

def _get_map_spawn_area(map_data):
    """
    兼容地图整体存在 spawn_area 的情况。

    支持：

        "spawn_area": {
            "x1": ...,
            "y1": ...,
            "x2": ...,
            "y2": ...
        }

    或者：

        "spawn_area": [
            [x, y],
            [x, y],
            ...
        ]

    如果没有则返回 None。
    """

    value = map_data.get(
        "spawn_area"
    )

    if value is None:
        return None

    return value


# ============================================================
# 11. 判断地图级 spawn_area
# ============================================================

def _is_in_map_spawn_area(
        x,
        y,
        spawn_area
):
    """
    判断一个坐标是否属于地图级 spawn_area。

    如果地图没有提供地图级 spawn_area：
        返回 None

    None 表示：
        不使用这个条件。
    """

    if spawn_area is None:
        return None

    # --------------------------------------------------------
    # 情况一：
    # spawn_area 是坐标列表
    #
    # [
    #   [1, 2],
    #   [1, 3],
    #   [2, 2]
    # ]
    # --------------------------------------------------------

    if isinstance(
        spawn_area,
        list
    ):

        coordinates = set()

        for item in spawn_area:

            if (
                isinstance(item, (list, tuple))
                and len(item) >= 2
            ):

                coordinates.add(
                    (
                        int(item[0]),
                        int(item[1])
                    )
                )

        return (
            x,
            y
        ) in coordinates

    # --------------------------------------------------------
    # 情况二：
    # spawn_area 是矩形
    # --------------------------------------------------------

    if isinstance(
        spawn_area,
        dict
    ):

        if all(
            key in spawn_area
            for key in [
                "x1",
                "y1",
                "x2",
                "y2"
            ]
        ):

            x1 = int(
                spawn_area["x1"]
            )

            y1 = int(
                spawn_area["y1"]
            )

            x2 = int(
                spawn_area["x2"]
            )

            y2 = int(
                spawn_area["y2"]
            )

            xmin = min(
                x1,
                x2
            )

            xmax = max(
                x1,
                x2
            )

            ymin = min(
                y1,
                y2
            )

            ymax = max(
                y1,
                y2
            )

            return (
                xmin <= x <= xmax
                and
                ymin <= y <= ymax
            )

    return None


# ============================================================
# 12. 判断一个 cell 是否允许出生
# ============================================================

def is_spawnable_cell(
        cell,
        map_data
):
    """
    这是本版本最核心的变化。

    规则：

    1. 必须是 FREE
    2. 如果 cell 自己有 spawn_area：
           按 cell 的 spawn_area 判断
    3. 如果 cell 没有 spawn_area：
           看地图整体有没有 spawn_area
    4. 如果地图也没有 spawn_area：
           不额外限制

    因此：

    老地图：
        没有 spawn_area
        → 完全保持原来的行为

    新地图：
        有 spawn_area
        → 才启用灰白区域限制
    """

    cell_type = _get_cell_type(
        cell
    )

    if cell_type != CellType.FREE:
        return False

    x, y = _get_cell_coordinate(
        cell
    )

    # --------------------------------------------------------
    # 优先使用 cell 自己的 spawn_area
    # --------------------------------------------------------

    cell_spawn_area = _get_cell_spawn_area(
        cell
    )

    if cell_spawn_area is not None:
        return cell_spawn_area

    # --------------------------------------------------------
    # 如果 cell 没有，
    # 尝试地图整体 spawn_area
    # --------------------------------------------------------

    map_spawn_area = _get_map_spawn_area(
        map_data
    )

    if map_spawn_area is not None:

        result = _is_in_map_spawn_area(
            x,
            y,
            map_spawn_area
        )

        if result is not None:
            return result

    # --------------------------------------------------------
    # 完全没有 spawn_area
    #
    # 保持旧逻辑：
    # FREE 就可以作为候选
    # --------------------------------------------------------

    return True


# ============================================================
# 13. 获取可用位置
# ============================================================

def get_available_cells(
        map_data
):
    """
    获取人员可生成位置。

    与旧版本相比：

    不再 flood fill。

    只根据：
        FREE
        +
        spawn_area
    判断。
    """

    available_cells = []

    for cell in map_data["cells"]:

        if not is_spawnable_cell(
            cell,
            map_data
        ):
            continue

        x, y = _get_cell_coordinate(
            cell
        )

        available_cells.append(
            (
                x,
                y,
                cell
            )
        )

    return available_cells


# ============================================================
# 14. profile -> semantic
# ============================================================

def get_target_semantics(
        profile
):
    if profile is None:
        return []

    profile = str(
        profile
    ).strip().lower()

    return PROFILE_SEMANTIC.get(
        profile,
        []
    )


# ============================================================
# 15. 根据 profile 筛选位置
# ============================================================

def select_cells_for_profile(
        cells,
        profile
):
    """
    先寻找 profile 对应 semantic。

    如果找不到：
        返回全部候选。

    注意：
    cells 已经提前经过 spawn_area 筛选，
    所以 fallback 仍然不会跑到 spawn_area 外面。
    """

    target_semantics = get_target_semantics(
        profile
    )

    if not target_semantics:
        return list(cells)

    matched = []

    for x, y, cell in cells:

        semantic = _get_cell_semantic(
            cell
        )

        if semantic in target_semantics:

            matched.append(
                (
                    x,
                    y,
                    cell
                )
            )

    if matched:
        return matched

    return list(cells)


# ============================================================
# 16. 距离
# ============================================================

def cell_distance(
        pos1,
        pos2
):
    x1, y1 = pos1
    x2, y2 = pos2

    return (
        abs(x1 - x2)
        +
        abs(y1 - y2)
    )


# ============================================================
# 17. 附近位置
# ============================================================

def get_nearby_cells(
        cells,
        center,
        max_distance=3
):
    result = []

    for x, y, cell in cells:

        distance = cell_distance(
            (x, y),
            center
        )

        if distance <= max_distance:

            result.append(
                (
                    x,
                    y,
                    cell
                )
            )

    return result


# ============================================================
# 18. group 位置生成
# ============================================================

def generate_group_position(
        candidates,
        count,
        occupied,
        rng,
        max_group_distance=3
):
    """
    同一个 group 尽量安排在一起。
    """

    available = [
        (
            x,
            y,
            cell
        )
        for x, y, cell in candidates
        if (
            x,
            y
        ) not in occupied
    ]

    if len(available) < count:
        return []

    # --------------------------------------------------------
    # 单人
    # --------------------------------------------------------

    if count == 1:

        selected = rng.choice(
            available
        )

        return [
            (
                selected[0],
                selected[1]
            )
        ]

    # --------------------------------------------------------
    # 尝试寻找紧凑区域
    # --------------------------------------------------------

    shuffled = list(
        available
    )

    rng.shuffle(
        shuffled
    )

    for center_x, center_y, _ in shuffled:

        center = (
            center_x,
            center_y
        )

        nearby = get_nearby_cells(
            available,
            center,
            max_distance=max_group_distance
        )

        nearby = [
            item
            for item in nearby
            if (
                item[0],
                item[1]
            ) not in occupied
        ]

        if len(nearby) < count:
            continue

        rng.shuffle(
            nearby
        )

        selected = nearby[
            :count
        ]

        return [
            (
                x,
                y
            )
            for x, y, _ in selected
        ]

    # --------------------------------------------------------
    # 无法形成紧凑区域
    # 随机分配
    # --------------------------------------------------------

    rng.shuffle(
        available
    )

    selected = available[
        :count
    ]

    return [
        (
            x,
            y
        )
        for x, y, _ in selected
    ]


# ============================================================
# 19. 核心：位置分配
# ============================================================

def allocate_positions(
        people,
        map_data,
        seed=None
):
    """
    A 的核心位置分配。

    输入：

        people
            C 输出的人员数据

        map_data
            A 地图数据

    输出：

        原 people

    只修改：

        x
        y
    """

    validate_people_data(
        people
    )

    validate_map_data(
        map_data
    )

    rng = random.Random(
        seed
    )

    # --------------------------------------------------------
    # 获取候选位置
    #
    # 这里不再 flood fill。
    # --------------------------------------------------------

    available_cells = get_available_cells(
        map_data
    )

    if not available_cells:

        raise ValueError(
            "地图中没有可用于人员初始位置的 FREE 元胞。"
            "请检查地图或 spawn_area 设置。"
        )

    # --------------------------------------------------------
    # 打印候选位置数量
    # --------------------------------------------------------

    print(
        f"[A] 地图总元胞："
        f"{int(map_data['width']) * int(map_data['height'])}"
    )

    print(
        f"[A] 可分配元胞："
        f"{len(available_cells)}"
    )

    # --------------------------------------------------------
    # group
    # --------------------------------------------------------

    groups = defaultdict(
        list
    )

    for person in people:

        group_id = _get_person_attribute(
            person,
            "group_id",
            ""
        )

        if group_id is None:
            group_id = ""

        groups[
            str(group_id)
        ].append(
            person
        )

    # --------------------------------------------------------
    # 检查总容量
    # --------------------------------------------------------

    if len(available_cells) < len(people):

        raise ValueError(
            f"可分配位置不足："
            f"需要 {len(people)} 个，"
            f"只有 {len(available_cells)} 个。"
        )

    occupied = set()

    # ========================================================
    # 逐 group 分配
    # ========================================================

    for group_id, members in groups.items():

        profile = _get_person_attribute(
            members[0],
            "profile",
            ""
        )

        # ----------------------------------------------------
        # semantic 优先
        # ----------------------------------------------------

        candidates = select_cells_for_profile(
            available_cells,
            profile
        )

        # ----------------------------------------------------
        # 尝试在 semantic 区域内安排
        # ----------------------------------------------------

        positions = generate_group_position(
            candidates=candidates,
            count=len(members),
            occupied=occupied,
            rng=rng,
            max_group_distance=3
        )

        # ----------------------------------------------------
        # semantic 区域不够
        #
        # 退回“所有 spawn_area 内的位置”
        #
        # 这里非常重要：
        # 不能退回所有 FREE。
        # ----------------------------------------------------

        if len(positions) < len(members):

            fallback_cells = [
                (
                    x,
                    y,
                    cell
                )
                for x, y, cell in available_cells
                if (
                    x,
                    y
                ) not in occupied
            ]

            if len(fallback_cells) < len(members):

                raise ValueError(
                    f"无法为 group_id={group_id} "
                    f"分配 {len(members)} 个位置。"
                    f"spawn_area 内剩余可用位置只有 "
                    f"{len(fallback_cells)} 个。"
                )

            positions = generate_group_position(
                candidates=fallback_cells,
                count=len(members),
                occupied=occupied,
                rng=rng,
                max_group_distance=3
            )

        # ----------------------------------------------------
        # 写入 x/y
        # ----------------------------------------------------

        if len(positions) < len(members):

            raise ValueError(
                f"group_id={group_id} "
                f"位置分配失败。"
            )

        for person, position in zip(
            members,
            positions
        ):

            x, y = position

            _set_person_attribute(
                person,
                "x",
                int(x)
            )

            _set_person_attribute(
                person,
                "y",
                int(y)
            )

            occupied.add(
                (
                    int(x),
                    int(y)
                )
            )

    print(
        f"[A] 成功为 {len(people)} 人分配初始位置"
    )

    return people


# ============================================================
# 20. 分配结果验证
# ============================================================

def validate_allocated_positions(
        people,
        map_data
):
    """
    验证：

    1. x/y 存在
    2. 不越界
    3. FREE
    4. spawn_area 内
    5. 不重复
    """

    validate_people_data(
        people
    )

    validate_map_data(
        map_data
    )

    cell_map = {}

    for cell in map_data["cells"]:

        x, y = _get_cell_coordinate(
            cell
        )

        cell_map[
            (x, y)
        ] = cell

    occupied = set()

    errors = []

    for person in people:

        person_id = _get_person_attribute(
            person,
            "id",
            "unknown"
        )

        x = _get_person_attribute(
            person,
            "x",
            None
        )

        y = _get_person_attribute(
            person,
            "y",
            None
        )

        if x is None or y is None:

            errors.append(
                f"人员 {person_id} 没有 x/y"
            )

            continue

        x = int(x)
        y = int(y)

        # ----------------------------------------------------
        # 越界
        # ----------------------------------------------------

        if (
            x < 0
            or x >= int(map_data["width"])
            or y < 0
            or y >= int(map_data["height"])
        ):

            errors.append(
                f"人员 {person_id} 位置越界："
                f"({x}, {y})"
            )

            continue

        cell = cell_map.get(
            (x, y)
        )

        if cell is None:

            errors.append(
                f"人员 {person_id} "
                f"位置不存在：({x}, {y})"
            )

            continue

        # ----------------------------------------------------
        # FREE
        # ----------------------------------------------------

        if _get_cell_type(cell) != CellType.FREE:

            errors.append(
                f"人员 {person_id} "
                f"位于非 FREE 元胞："
                f"({x}, {y})"
            )

            continue

        # ----------------------------------------------------
        # spawn_area
        # ----------------------------------------------------

        if not is_spawnable_cell(
            cell,
            map_data
        ):

            errors.append(
                f"人员 {person_id} "
                f"位于 spawn_area 外："
                f"({x}, {y})"
            )

            continue

        # ----------------------------------------------------
        # 重复
        # ----------------------------------------------------

        if (
            x,
            y
        ) in occupied:

            errors.append(
                f"人员 {person_id} "
                f"位置重复：({x}, {y})"
            )

        occupied.add(
            (
                x,
                y
            )
        )

    return errors


# ============================================================
# 21. C -> A 原有文件接口
# ============================================================

def allocate_people_position(
        people_file,
        map_file,
        output_file,
        seed=None
):
    """
    C -> A 原有接口。

    C：

        scene_config.py
                ↓
        output_people.json
                ↓
                A

    A：

        output_people.json
                +
             地图 JSON
                ↓
             分配 x/y
                ↓
          output_file
    """

    people_file = Path(
        people_file
    ).resolve()

    map_file = Path(
        map_file
    ).resolve()

    output_file = Path(
        output_file
    ).resolve()

    # --------------------------------------------------------
    # 读取人员
    # --------------------------------------------------------

    people_data = load_json(
        people_file
    )

    # --------------------------------------------------------
    # 兼容人员 JSON
    # --------------------------------------------------------

    if isinstance(
        people_data,
        dict
    ):

        if "people" in people_data:

            people = people_data[
                "people"
            ]

        elif "persons" in people_data:

            people = people_data[
                "persons"
            ]

        else:

            raise ValueError(
                "人员 JSON 中没有 people/persons"
            )

    elif isinstance(
        people_data,
        list
    ):

        people = people_data

    else:

        raise ValueError(
            "人员 JSON 格式错误"
        )

    # --------------------------------------------------------
    # 读取地图
    # --------------------------------------------------------

    map_data = load_json(
        map_file
    )

    # --------------------------------------------------------
    # A 分配位置
    # --------------------------------------------------------

    allocate_positions(
        people,
        map_data,
        seed=seed
    )

    # --------------------------------------------------------
    # 验证
    # --------------------------------------------------------

    errors = validate_allocated_positions(
        people,
        map_data
    )

    if errors:

        print(
            "[ERROR] 人员位置验证失败："
        )

        for error in errors:

            print(
                "  ",
                error
            )

        raise ValueError(
            f"人员位置验证失败，"
            f"共 {len(errors)} 个问题。"
        )

    # --------------------------------------------------------
    # 保持原有 JSON 结构
    # --------------------------------------------------------

    if isinstance(
        people_data,
        dict
    ):

        if "people" in people_data:

            people_data[
                "people"
            ] = people

        elif "persons" in people_data:

            people_data[
                "persons"
            ] = people

    else:

        people_data = people

    # --------------------------------------------------------
    # 输出
    # --------------------------------------------------------

    output_file.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        output_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            people_data,
            f,
            ensure_ascii=False,
            indent=4
        )

    print(
        f"[OK] 人员位置分配完成："
        f"{output_file}"
    )

    return output_file


# ============================================================
# 22. 结果预览
# ============================================================

def print_people_preview(
        people
):
    print()
    print(
        "=" * 70
    )
    print(
        "人员位置分配结果"
    )
    print(
        "=" * 70
    )

    for person in people:

        person_id = _get_person_attribute(
            person,
            "id",
            "unknown"
        )

        profile = _get_person_attribute(
            person,
            "profile",
            ""
        )

        group_id = _get_person_attribute(
            person,
            "group_id",
            ""
        )

        x = _get_person_attribute(
            person,
            "x",
            None
        )

        y = _get_person_attribute(
            person,
            "y",
            None
        )

        print(
            f"id={person_id:<4} "
            f"profile={str(profile):<12} "
            f"group={str(group_id):<6} "
            f"position=({x}, {y})"
        )

    print(
        "=" * 70
    )


# ============================================================
# 23. 独立测试
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "A：人员初始位置分配"
    )

    print(
        "=" * 70
    )

    project_root = (
        Path(__file__)
        .resolve()
        .parent
        .parent
    )

    # --------------------------------------------------------
    # C 的输出
    # --------------------------------------------------------

    people_file = (
        project_root
        / "control"
        / "output_people.json"
    )

    if not people_file.exists():

        print(
            "[ERROR] 未找到："
        )

        print(
            people_file
        )

        print()
        print(
            "请先运行："
        )

        print(
            "python control/scene_config.py"
        )

        return

    # --------------------------------------------------------
    # 找地图
    # --------------------------------------------------------

    maps_dir = (
        project_root
        / "maps"
    )

    map_candidates = []

    if maps_dir.exists():

        for path in maps_dir.rglob(
            "*.json"
        ):

            map_candidates.append(
                path
            )

    if not map_candidates:

        print(
            "[ERROR] maps 下没有 JSON 地图"
        )

        return

    print()

    print(
        "可用地图："
    )

    for index, path in enumerate(
        map_candidates,
        start=1
    ):

        print(
            f"{index}. {path}"
        )

    print()

    try:

        choice = int(
            input(
                "请选择地图编号："
            )
        )

        if (
            choice < 1
            or choice > len(
                map_candidates
            )
        ):

            print(
                "[ERROR] 地图编号无效"
            )

            return

        map_file = map_candidates[
            choice - 1
        ]

    except ValueError:

        print(
            "[ERROR] 请输入数字"
        )

        return

    # --------------------------------------------------------
    # 输出
    # --------------------------------------------------------

    output_file = (
        project_root
        / "control"
        / "positioned_people.json"
    )

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------

    allocate_people_position(
        people_file=people_file,
        map_file=map_file,
        output_file=output_file,
        seed=42
    )

    # --------------------------------------------------------
    # 读取结果
    # --------------------------------------------------------

    result_data = load_json(
        output_file
    )

    if isinstance(
        result_data,
        dict
    ):

        if "people" in result_data:

            people = result_data[
                "people"
            ]

        elif "persons" in result_data:

            people = result_data[
                "persons"
            ]

        else:

            people = []

    else:

        people = result_data

    print_people_preview(
        people
    )

    print()

    print(
        "[OK] A 测试完成"
    )

    print(
        f"[OK] 输出文件："
        f"{output_file}"
    )


# ============================================================
# 24. 入口
# ============================================================

if __name__ == "__main__":
    main()
