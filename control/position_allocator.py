from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog


# ============================================================
# profile → semantic
# ============================================================

PROFILE_SEMANTIC = {
    "student": [
        "classroom",
        "dorm",
        "library",
    ],

    "teacher": [
        "classroom",
    ],

    "staff": [
        "corridor",
        "hall",
    ],

    "security": [
        "hall",
        "corridor",
    ],

    "customer": [
        "shop",
        "hall",
    ],

    "child": [
        "shop",
        "hall",
    ],

    "elderly": [
        "hall",
        "hospital",
    ],

    "patient": [
        "hospital",
    ],

    "doctor": [
        "hospital",
    ],

    "family_member": [
        "hospital",
    ],
}


# ============================================================
# JSON读取
# ============================================================

def load_json(path: str | Path) -> dict:
    path = Path(path)

    if not path.is_file():
        raise FileNotFoundError(
            f"JSON文件不存在: {path}"
        )

    with path.open(
        "r",
        encoding="utf-8"
    ) as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(
            f"JSON顶层必须是对象: {path}"
        )

    return data


# ============================================================
# 人员数据检查
# ============================================================

def validate_people_data(
        people_data: dict
) -> list[dict]:

    if "persons" not in people_data:
        raise ValueError(
            "人员JSON缺少 persons 字段"
        )

    people = people_data["persons"]

    if not isinstance(people, list):
        raise ValueError(
            "persons 必须是列表"
        )

    for index, person in enumerate(people):

        if not isinstance(person, dict):
            raise ValueError(
                f"persons[{index}] 必须是对象"
            )

        for field in (
            "id",
            "profile",
            "group_id",
        ):
            if field not in person:
                raise ValueError(
                    f"persons[{index}] 缺少字段: {field}"
                )

    return people


# ============================================================
# 地图数据检查
# ============================================================

def validate_map_data(
        map_data: dict
) -> None:

    for field in (
        "width",
        "height",
        "cells",
    ):
        if field not in map_data:
            raise ValueError(
                f"地图JSON缺少字段: {field}"
            )

    width = map_data["width"]
    height = map_data["height"]
    cells = map_data["cells"]

    if not isinstance(width, int) or width <= 0:
        raise ValueError(
            "地图 width 必须是正整数"
        )

    if not isinstance(height, int) or height <= 0:
        raise ValueError(
            "地图 height 必须是正整数"
        )

    if not isinstance(cells, list):
        raise ValueError(
            "地图 cells 必须是列表"
        )

    expected = width * height

    if len(cells) != expected:
        raise ValueError(
            f"地图 cells 数量错误: "
            f"期望 {expected}，实际 {len(cells)}"
        )

    for index, cell in enumerate(cells):

        if not isinstance(cell, dict):
            raise ValueError(
                f"cells[{index}] 必须是对象"
            )

        for field in (
            "x",
            "y",
            "type",
        ):
            if field not in cell:
                raise ValueError(
                    f"cells[{index}] 缺少字段: {field}"
                )


# ============================================================
# 获取所有 free 元胞
# ============================================================

def get_available_cells(
        map_data: dict
) -> list[tuple[int, int, str]]:

    cells = []

    for cell in map_data["cells"]:

        if str(cell["type"]).lower() != "free":
            continue

        x = int(cell["x"])
        y = int(cell["y"])

        semantic = cell.get(
            "semantic",
            ""
        )

        if semantic is None:
            semantic = ""

        semantic = str(
            semantic
        ).strip().lower()

        cells.append(
            (
                x,
                y,
                semantic,
            )
        )

    return cells


# ============================================================
# profile → semantic
# ============================================================

def get_target_semantics(
        profile: str
) -> list[str]:

    profile = str(
        profile
    ).strip().lower()

    return PROFILE_SEMANTIC.get(
        profile,
        ["hall"],
    )


# ============================================================
# 根据 profile 筛选元胞
# ============================================================

def select_cells_for_profile(
        cells,
        profile
) -> list[tuple[int, int]]:

    target_semantics = get_target_semantics(
        profile
    )

    result = []

    for x, y, semantic in cells:

        if semantic in target_semantics:
            result.append(
                (
                    x,
                    y,
                )
            )

    # 如果地图没有对应 semantic
    # 退回全部 free 元胞

    if not result:

        result = [
            (
                x,
                y,
            )
            for x, y, _ in cells
        ]

    return result


# ============================================================
# 元胞距离
# ============================================================

def cell_distance(
        cell_a,
        cell_b
) -> float:

    ax, ay = cell_a
    bx, by = cell_b

    return math.sqrt(
        (ax - bx) ** 2
        +
        (ay - by) ** 2
    )


# ============================================================
# 获取附近元胞
# ============================================================

def get_nearby_cells(
        center,
        candidates,
        number
) -> list[tuple[int, int]]:

    distance_cells = []

    for cell in candidates:

        distance = cell_distance(
            center,
            cell
        )

        distance_cells.append(
            (
                distance,
                cell
            )
        )

    distance_cells.sort(
        key=lambda item: item[0]
    )

    return [
        cell
        for _, cell
        in distance_cells[:number]
    ]


# ============================================================
# 群体位置生成
# ============================================================

def generate_group_position(
        members,
        candidates,
        occupied
) -> list[tuple[int, int]]:

    group_size = len(members)

    available_candidates = [
        cell
        for cell in candidates
        if cell not in occupied
    ]

    if len(available_candidates) < group_size:
        return []

    centers = list(
        available_candidates
    )

    random.shuffle(
        centers
    )

    nearby_number = max(
        group_size * 5,
        group_size
    )

    for center in centers:

        nearby = get_nearby_cells(
            center,
            available_candidates,
            nearby_number
        )

        if len(nearby) < group_size:
            continue

        selected = random.sample(
            nearby,
            group_size
        )

        return selected

    return random.sample(
        available_candidates,
        group_size
    )


# ============================================================
# 核心位置分配接口
# ============================================================

def allocate_positions(
        people,
        map_data
) -> None:
    """
    平台正式调用接口。

    people:
        C生成的人物列表。

    map_data:
        A的MapEditor编辑完成后的地图数据。

    只修改：

        x
        y
    """

    if not isinstance(
        people,
        list
    ):
        raise ValueError(
            "people 必须是列表"
        )

    validate_map_data(
        map_data
    )

    cells = get_available_cells(
        map_data
    )

    if not cells:
        raise ValueError(
            "地图中没有 type == 'free' 的元胞"
        )

    occupied = set()

    groups = defaultdict(list)

    for person in people:

        group_id = str(
            person.get(
                "group_id",
                ""
            )
        )

        groups[group_id].append(
            person
        )

    for group_id, members in groups.items():

        profile = str(
            members[0].get(
                "profile",
                ""
            )
        )

        candidates = select_cells_for_profile(
            cells,
            profile
        )

        positions = generate_group_position(
            members,
            candidates,
            occupied
        )

        # 当前语义区域不足
        # 退回全部 free 元胞

        if len(positions) != len(members):

            all_free = [
                (
                    x,
                    y
                )
                for x, y, _
                in cells
                if (
                    x,
                    y
                ) not in occupied
            ]

            if len(all_free) < len(members):

                raise ValueError(
                    f"group_id={group_id!r} "
                    f"需要 {len(members)} 个位置，"
                    f"但剩余 free 元胞只有 "
                    f"{len(all_free)} 个"
                )

            positions = random.sample(
                all_free,
                len(members)
            )

        for person, position in zip(
            members,
            positions
        ):

            x, y = position

            person["x"] = x
            person["y"] = y

            occupied.add(
                position
            )


# ============================================================
# 分配结果检查
# ============================================================

def validate_allocated_positions(
        people,
        map_data
) -> None:

    width = int(
        map_data["width"]
    )

    height = int(
        map_data["height"]
    )

    free_cells = {
        (
            int(cell["x"]),
            int(cell["y"])
        )
        for cell in map_data["cells"]
        if str(cell["type"]).lower() == "free"
    }

    occupied = set()

    for person in people:

        person_id = person.get(
            "id",
            "unknown"
        )

        x = person.get("x")
        y = person.get("y")

        if not isinstance(x, int):
            raise ValueError(
                f"人物 {person_id} 的 x 必须是整数"
            )

        if not isinstance(y, int):
            raise ValueError(
                f"人物 {person_id} 的 y 必须是整数"
            )

        if not (
            0 <= x < width
            and
            0 <= y < height
        ):
            raise ValueError(
                f"人物 {person_id} 坐标越界: "
                f"({x}, {y})"
            )

        if (
            x,
            y
        ) not in free_cells:
            raise ValueError(
                f"人物 {person_id} "
                f"位置不是 free 元胞: "
                f"({x}, {y})"
            )

        if (
            x,
            y
        ) in occupied:
            raise ValueError(
                f"人物 {person_id} "
                f"与其他人员位置重复: "
                f"({x}, {y})"
            )

        occupied.add(
            (
                x,
                y
            )
        )


# ============================================================
# 文件模式接口
# ============================================================

def allocate_people_position(
        people_file,
        map_file,
        output_file
) -> Path:
    """
    文件模式。

    C人员JSON
        +
    A编辑后的地图JSON
        ↓
    人员位置分配
        ↓
    输出JSON
    """

    people_data = load_json(
        people_file
    )

    people = validate_people_data(
        people_data
    )

    map_data = load_json(
        map_file
    )

    validate_map_data(
        map_data
    )

    allocate_positions(
        people,
        map_data
    )

    validate_allocated_positions(
        people,
        map_data
    )

    output_path = Path(
        output_file
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    with output_path.open(
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            people_data,
            f,
            indent=4,
            ensure_ascii=False
        )

    print("=" * 60)
    print("A 人员初始位置分配完成")
    print("=" * 60)

    print(
        f"人员数量: {len(people)}"
    )

    print(
        f"地图尺寸: "
        f"{map_data['width']} x "
        f"{map_data['height']}"
    )

    print(
        f"可用 free 元胞: "
        f"{sum(1 for cell in map_data['cells'] if str(cell['type']).lower() == 'free')}"
    )

    print(
        f"输出文件: {output_path}"
    )

    print("=" * 60)

    return output_path


# ============================================================
# 人员位置预览
# ============================================================

def print_people_preview(
        people_file,
        limit=10
) -> None:

    data = load_json(
        people_file
    )

    people = data.get(
        "persons",
        []
    )

    print()
    print("人员位置预览:")
    print("-" * 60)

    for person in people[:limit]:

        print(
            f"id={person.get('id')}"
            f" | profile={person.get('profile')}"
            f" | group_id={person.get('group_id')}"
            f" | position="
            f"({person.get('x')}, "
            f"{person.get('y')})"
        )

    if len(people) > limit:

        print(
            f"... 共 {len(people)} 人，"
            f"仅显示前 {limit} 人"
        )

    print("-" * 60)


# ============================================================
# 本机选择 JSON
# ============================================================

def select_json(
        title
) -> Path | None:

    root = tk.Tk()
    root.withdraw()

    file_path = filedialog.askopenfilename(
        title=title,
        filetypes=[
            (
                "JSON文件",
                "*.json"
            ),
            (
                "所有文件",
                "*.*"
            )
        ]
    )

    root.destroy()

    if not file_path:
        return None

    return Path(
        file_path
    )


# ============================================================
# 测试
# ============================================================

def main():

    print("=" * 60)
    print("A 人员初始位置分配测试")
    print("=" * 60)

    project_root = (
        Path(__file__)
        .resolve()
        .parent
        .parent
    )

    # --------------------------------------------------------
    # 选择 C 的人员 JSON
    # --------------------------------------------------------

    print()
    print("请选择 C 生成的人员 JSON")

    people_file = select_json(
        "请选择 C 生成的人员 JSON"
    )

    if people_file is None:

        print(
            "未选择人员文件，程序结束。"
        )

        return

    # --------------------------------------------------------
    # 选择 A 编辑完成的地图 JSON
    # --------------------------------------------------------

    print()
    print("请选择 A 的 MapEditor 编辑完成后的地图 JSON")

    map_file = select_json(
        "请选择 A 编辑完成后的地图 JSON"
    )

    if map_file is None:

        print(
            "未选择地图文件，程序结束。"
        )

        return

    # --------------------------------------------------------
    # 输出文件
    # --------------------------------------------------------

    output_file = (
        project_root
        / "output"
        / "people_with_positions.json"
    )

    print()
    print("=" * 60)

    print(
        f"C/平台人员文件: "
        f"{people_file}"
    )

    print(
        f"当前地图文件: "
        f"{map_file}"
    )

    print(
        f"输出文件: "
        f"{output_file}"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # 执行
    # --------------------------------------------------------

    try:

        result = allocate_people_position(
            people_file=people_file,
            map_file=map_file,
            output_file=output_file
        )

        print()
        print("人员位置分配完成:")
        print(result)

        print_people_preview(
            result
        )

    except Exception as e:

        print()
        print("=" * 60)
        print("人员位置分配失败")
        print("=" * 60)

        print(
            f"{type(e).__name__}: {e}"
        )

        raise


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":
    main()
