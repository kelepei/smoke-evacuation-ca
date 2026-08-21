"""
A08 - 语义标签系统

功能：
1. 自动读取 map_editor 输出的最新地图 JSON；
2. 不再写死具体地图路径；
3. 读取用户编辑后的地图；
4. 统计已有 semantic 标签；
5. 保留 map_editor 已经设置的 semantic；
6. 对没有语义标签的可通行区域进行基础语义推断；
7. 输出 A08 处理后的统一地图 JSON；
8. 后续 A09 / A10 / 人员位置分配等模块直接读取输出结果。

数据流：

    用户上传地图
          ↓
      map_editor
          ↓
    编辑后的 JSON
          ↓
         A08
          ↓
    semantic_map.json
          ↓
    人员位置分配 / CA / 可视化
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from collections import Counter


# ============================================================
# 1. 项目根目录
# ============================================================

PROJECT_ROOT = Path(
    __file__
).resolve().parent.parent


# ============================================================
# 2. 地图目录候选位置
# ============================================================

MAP_DIRECTORIES = [

    # map_editor 如果将编辑结果放在这里
    PROJECT_ROOT / "maps" / "edited",

    # 用户上传后的地图
    PROJECT_ROOT / "maps" / "uploaded",

    # 以前的处理地图
    PROJECT_ROOT / "maps" / "processed",

    # 场景模板
    PROJECT_ROOT / "maps" / "templates",

    # maps 根目录
    PROJECT_ROOT / "maps",
]


# ============================================================
# 3. A08 输出目录
# ============================================================

OUTPUT_DIR = (
    PROJECT_ROOT
    / "maps"
    / "semantic"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)


# ============================================================
# 4. JSON读取
# ============================================================

def load_json(
    json_path: str | Path
) -> dict:

    json_path = Path(json_path)

    if not json_path.exists():

        raise FileNotFoundError(
            f"地图文件不存在: {json_path}"
        )

    if json_path.suffix.lower() != ".json":

        raise ValueError(
            f"A08 只处理 JSON 地图: {json_path}"
        )

    with open(
        json_path,
        "r",
        encoding="utf-8",
    ) as f:

        data = json.load(f)

    if not isinstance(data, dict):

        raise ValueError(
            "地图 JSON 顶层数据必须是 object"
        )

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

    return data


# ============================================================
# 5. 判断是否为有效地图 JSON
# ============================================================

def is_valid_map_json(
    path: Path
) -> bool:

    try:

        data = load_json(path)

        return (
            "width" in data
            and
            "height" in data
            and
            "cells" in data
        )

    except Exception:

        return False


# ============================================================
# 6. 自动寻找最新地图
# ============================================================

def find_latest_map() -> Path:
    """
    自动寻找最近修改的地图 JSON。

    优先级：

        maps/edited
        maps/uploaded
        maps/processed
        maps/templates
        maps

    在每个目录中寻找最近修改的合法 JSON。
    """

    candidates: list[Path] = []

    for directory in MAP_DIRECTORIES:

        if not directory.exists():
            continue

        for path in directory.glob("*.json"):

            # 排除 A08 自己的输出
            if (
                path.parent == OUTPUT_DIR
            ):
                continue

            # 排除一些明显不是地图的 JSON
            if path.name in {
                "people.json",
                "output_people.json",
            }:
                continue

            if is_valid_map_json(path):

                candidates.append(path)

    if not candidates:

        raise FileNotFoundError(
            "没有找到可用的地图 JSON。\n"
            "请先通过 map_editor 保存编辑后的地图。"
        )

    # 按最后修改时间排序
    candidates.sort(
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    return candidates[0]


# ============================================================
# 7. semantic 统计
# ============================================================

def count_semantics(
    map_data: dict
) -> Counter:

    counter = Counter()

    for cell in map_data["cells"]:

        semantic = cell.get(
            "semantic"
        )

        if semantic is None:

            counter["none"] += 1

        elif semantic == "":

            counter["none"] += 1

        else:

            counter[str(semantic)] += 1

    return counter


# ============================================================
# 8. 基础语义推断
# ============================================================

def infer_semantic(
    map_data: dict,
    cell: dict,
) -> str | None:
    """
    对没有 semantic 的元胞进行非常基础的推断。

    注意：

    A08 不应该覆盖 map_editor 已经标注的 semantic。

    如果 cell 已经存在 semantic，
    本函数不会被调用。

    对无法可靠判断的区域返回 None。
    """

    cell_type = cell.get(
        "type",
        "free"
    )

    # 墙体不需要房间语义
    if cell_type == "wall":

        return None

    # 障碍物同样不强制设置 semantic
    if cell_type == "obstacle":

        return None

    # 出口暂时不自动推断房间语义
    if cell_type == "exit":

        return None

    # 烟源不自动推断
    if cell_type == "smoke_source":

        return None

    # 其他 free 元胞无法仅凭地图可靠判断
    return None


# ============================================================
# 9. 处理 semantic
# ============================================================

def process_semantics(
    map_data: dict
) -> dict:
    """
    处理地图语义。

    原则：

        map_editor 已经标注
            ↓
        A08 保留

        没有标注
            ↓
        A08 尝试基础推断

        无法判断
            ↓
        保持 None
    """

    processed = dict(map_data)

    cells = []

    for original_cell in map_data["cells"]:

        cell = dict(
            original_cell
        )

        semantic = cell.get(
            "semantic"
        )

        # ----------------------------------------------------
        # 已经由 map_editor 标注
        # ----------------------------------------------------

        if (
            semantic is not None
            and
            semantic != ""
        ):

            cells.append(cell)

            continue

        # ----------------------------------------------------
        # 尝试自动推断
        # ----------------------------------------------------

        inferred = infer_semantic(
            map_data,
            cell,
        )

        if inferred is not None:

            cell["semantic"] = inferred

        else:

            # 保持统一形式
            cell["semantic"] = None

        cells.append(cell)

    processed["cells"] = cells

    return processed


# ============================================================
# 10. 保存 A08 输出
# ============================================================

def save_semantic_map(
    map_data: dict,
    source_path: Path,
) -> Path:
    """
    保存 A08 处理后的地图。

    文件名：

        原地图名_semantic.json
    """

    output_name = (
        source_path.stem
        + "_semantic.json"
    )

    output_path = (
        OUTPUT_DIR
        / output_name
    )

    with open(
        output_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            map_data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return output_path


# ============================================================
# 11. 打印统计信息
# ============================================================

def print_statistics(
    map_data: dict,
    source_path: Path,
    output_path: Path,
) -> None:

    print()
    print("=" * 60)
    print("A08 语义标签系统")
    print("=" * 60)

    print(
        f"项目根目录: {PROJECT_ROOT}"
    )

    print(
        f"读取地图: {source_path}"
    )

    print(
        f"输出地图: {output_path}"
    )

    print()

    print(
        "地图尺寸:"
        f" {map_data['width']} × "
        f"{map_data['height']}"
    )

    print(
        f"元胞数量: "
        f"{len(map_data['cells'])}"
    )

    print()
    print(
        "语义标签:"
    )

    counter = count_semantics(
        map_data
    )

    for semantic, count in counter.items():

        print(
            f"  {semantic:<20}"
            f"{count}"
        )

    print("=" * 60)


# ============================================================
# 12. 对外接口
# ============================================================

def process_latest_map() -> Path:
    """
    A08 对外主要接口。

    自动：

        1. 寻找最新编辑地图
        2. 读取地图
        3. 处理 semantic
        4. 输出语义地图

    返回：

        A08 输出地图路径
    """

    source_path = find_latest_map()

    map_data = load_json(
        source_path
    )

    processed_map = process_semantics(
        map_data
    )

    output_path = save_semantic_map(
        processed_map,
        source_path,
    )

    print_statistics(
        processed_map,
        source_path,
        output_path,
    )

    return output_path


# ============================================================
# 13. 从 map_editor 直接调用的接口
# ============================================================

def process_map(
    map_path: str | Path
) -> Path:
    """
    给 map_editor 调用。

    map_editor 保存地图以后可以直接：

        from semantic.semantic_lable import process_map

        output = process_map(saved_map_path)

    不需要用户再次选择文件。
    """

    source_path = Path(
        map_path
    ).resolve()

    map_data = load_json(
        source_path
    )

    processed_map = process_semantics(
        map_data
    )

    output_path = save_semantic_map(
        processed_map,
        source_path,
    )

    print_statistics(
        processed_map,
        source_path,
        output_path,
    )

    return output_path


# ============================================================
# 14. main
# ============================================================

def main():

    print("=" * 60)
    print("A08 语义标签系统")
    print("=" * 60)

    print(
        f"项目根目录: {PROJECT_ROOT}"
    )

    print()
    print(
        "正在寻找 map_editor 最新编辑地图..."
    )

    try:

        output_path = process_latest_map()

        print()
        print(
            "✓ A08 处理完成"
        )

        print(
            f"语义地图: {output_path}"
        )

    except Exception as e:

        print()
        print(
            f"✗ A08 处理失败: {e}"
        )


# ============================================================
# 程序入口
# ============================================================

if __name__ == "__main__":

    main()
