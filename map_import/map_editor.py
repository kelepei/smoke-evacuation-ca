import json
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib

from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle

from core.schema import (
    CellType,
    SemanticType
)


# ============================================================
# Mac 中文显示
# ============================================================

matplotlib.rcParams["font.sans-serif"] = [
    "PingFang SC"
]

matplotlib.rcParams["axes.unicode_minus"] = False


# ============================================================
# 项目根目录
# ============================================================

PROJECT_ROOT = (
    Path(__file__).resolve().parent
)


# ============================================================
# MapEditor
# ============================================================

class MapEditor:

    def __init__(self, grid):

        self.grid = grid

        # ==========================
        # 编辑状态
        # ==========================

        self.edit_mode = "type"

        self.current_type = CellType.FREE

        self.current_semantic = None

        # ==========================
        # 拖拽状态
        # ==========================

        self.drag_start = None

        self.drag_end = None

        # ==========================
        # 是否正在拖拽
        # ==========================

        self.is_dragging = False

        # ==========================
        # 窗口
        # ==========================

        self.fig, self.ax = plt.subplots(
            figsize=(12, 10)
        )

        # ==========================
        # 鼠标事件
        # ==========================

        self.fig.canvas.mpl_connect(
            "button_press_event",
            self.on_press
        )

        self.fig.canvas.mpl_connect(
            "motion_notify_event",
            self.on_move
        )

        self.fig.canvas.mpl_connect(
            "button_release_event",
            self.on_release
        )

        # ==========================
        # 键盘事件
        # ==========================

        self.fig.canvas.mpl_connect(
            "key_press_event",
            self.keypress
        )

        self.draw()

    # ========================================================
    # 坐标转换
    # ========================================================

    def get_cell_position(self, event):
        """
        将鼠标位置转换成元胞坐标。

        返回：

            (x, y)

        如果鼠标不在地图区域：

            None
        """

        if event.xdata is None:
            return None

        if event.ydata is None:
            return None

        x = int(event.xdata)
        y = int(event.ydata)

        if (
            x < 0
            or x >= self.grid.width
            or y < 0
            or y >= self.grid.height
        ):
            return None

        return x, y

    # ========================================================
    # 绘制
    # ========================================================

    def draw(self):

        self.ax.clear()

        image = []

        # ====================================================
        # 生成地图图像
        # ====================================================

        for y in range(self.grid.height):

            row = []

            for x in range(self.grid.width):

                cell = self.grid.get_cell(
                    x,
                    y
                )

                if cell.cell_type == CellType.FREE:

                    row.append(0)

                elif cell.cell_type == CellType.WALL:

                    row.append(1)

                elif cell.cell_type == CellType.OBSTACLE:

                    row.append(2)

                elif cell.cell_type == CellType.EXIT:

                    row.append(3)

                else:

                    row.append(0)

            image.append(row)

        # ====================================================
        # 地图颜色
        # ====================================================

        cmap = ListedColormap(
            [
                "white",
                "black",
                "gray",
                "green"
            ]
        )

        self.ax.imshow(
            image,
            cmap=cmap,
            interpolation="nearest",
            origin="upper",
            vmin=0,
            vmax=3
        )

        # ====================================================
        # 显示语义
        # ====================================================

        for cell in self.grid.cells:

            if cell.semantic is not None:

                if hasattr(
                    cell.semantic,
                    "value"
                ):

                    text = (
                        cell.semantic.value[:3]
                    )

                else:

                    text = str(
                        cell.semantic
                    )[:3]

                self.ax.text(
                    cell.x,
                    cell.y,
                    text,
                    ha="center",
                    va="center",
                    fontsize=5
                )

        # ====================================================
        # 显示拖拽区域
        # ====================================================

        if (
            self.drag_start is not None
            and
            self.drag_end is not None
        ):

            x1, y1 = self.drag_start

            x2, y2 = self.drag_end

            xmin = min(x1, x2)

            ymin = min(y1, y2)

            width = abs(x2 - x1) + 1

            height = abs(y2 - y1) + 1

            rect = Rectangle(
                (
                    xmin - 0.5,
                    ymin - 0.5
                ),
                width,
                height,
                fill=False,
                linewidth=2
            )

            self.ax.add_patch(
                rect
            )

        # ====================================================
        # 网格
        # ====================================================

        self.ax.grid(
            True,
            linewidth=0.3
        )

        step_x = max(
            1,
            self.grid.width // 15
        )

        step_y = max(
            1,
            self.grid.height // 15
        )

        self.ax.set_xticks(
            range(
                0,
                self.grid.width,
                step_x
            )
        )

        self.ax.set_yticks(
            range(
                0,
                self.grid.height,
                step_y
            )
        )

        # ====================================================
        # 当前模式显示
        # ====================================================

        if self.edit_mode == "type":

            current_mode_text = (
                f"当前模式：元胞类型 → "
                f"{self.current_type.value}"
            )

        elif self.edit_mode == "semantic":

            if self.current_semantic is not None:

                current_mode_text = (
                    f"当前模式：语义 → "
                    f"{self.current_semantic.value}"
                )

            else:

                current_mode_text = (
                    "当前模式：语义"
                )

        else:

            current_mode_text = (
                "当前模式：未知"
            )

        # ====================================================
        # 标题
        # ====================================================

        self.ax.set_title(

            "CA Map Editor\n"

            f"Cell Size={self.grid.cell_size}m\n"

            f"{current_mode_text}\n"

            "类型："
            "1空地 2墙 3障碍 4出口\n"

            "语义："
            "W教室 E走廊 R楼梯 T商店 "
            "Y大厅 U食堂 I宿舍 O图书馆 P医院\n"

            "鼠标左键拖拽框选区域"

        )

        self.fig.canvas.draw_idle()

    # ========================================================
    # 鼠标按下
    # ========================================================

    def on_press(self, event):

        # ----------------------------------------------------
        # 只处理鼠标左键
        # ----------------------------------------------------

        if event.button != 1:

            return

        position = self.get_cell_position(
            event
        )

        if position is None:

            return

        x, y = position

        # ----------------------------------------------------
        # 开始拖拽
        #
        # 类型和语义现在统一使用拖拽。
        # ----------------------------------------------------

        self.drag_start = (
            x,
            y
        )

        self.drag_end = (
            x,
            y
        )

        self.is_dragging = True

        # ----------------------------------------------------
        # 立即显示起始元胞
        # ----------------------------------------------------

        self.draw()

    # ========================================================
    # 鼠标移动
    # ========================================================

    def on_move(self, event):

        # ----------------------------------------------------
        # 没有按下鼠标
        # ----------------------------------------------------

        if not self.is_dragging:

            return

        if self.drag_start is None:

            return

        position = self.get_cell_position(
            event
        )

        if position is None:

            return

        x, y = position

        # ----------------------------------------------------
        # 更新拖拽终点
        # ----------------------------------------------------

        self.drag_end = (
            x,
            y
        )

        # ----------------------------------------------------
        # 重新绘制拖拽框
        # ----------------------------------------------------

        self.draw()

    # ========================================================
    # 鼠标释放
    # ========================================================

    def on_release(self, event):

        # ----------------------------------------------------
        # 只处理左键
        # ----------------------------------------------------

        if event.button != 1:

            return

        if not self.is_dragging:

            return

        if self.drag_start is None:

            self.is_dragging = False

            return

        # ----------------------------------------------------
        # 如果释放位置有效，
        # 更新最终位置
        # ----------------------------------------------------

        position = self.get_cell_position(
            event
        )

        if position is not None:

            self.drag_end = position

        # ----------------------------------------------------
        # 如果没有终点
        # ----------------------------------------------------

        if self.drag_end is None:

            self.is_dragging = False

            self.drag_start = None

            return

        x1, y1 = self.drag_start

        x2, y2 = self.drag_end

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

        # ====================================================
        # 类型模式
        # ====================================================

        if self.edit_mode == "type":

            self.apply_type_to_region(
                xmin,
                ymin,
                xmax,
                ymax
            )

        # ====================================================
        # 语义模式
        # ====================================================

        elif self.edit_mode == "semantic":

            self.apply_semantic_to_region(
                xmin,
                ymin,
                xmax,
                ymax
            )

        # ====================================================
        # 清除拖拽状态
        # ====================================================

        self.drag_start = None

        self.drag_end = None

        self.is_dragging = False

        # ====================================================
        # 重新绘制
        # ====================================================

        self.draw()

    # ========================================================
    # 类型区域修改
    # ========================================================

    def apply_type_to_region(
        self,
        xmin,
        ymin,
        xmax,
        ymax
    ):
        """
        将选中的整片区域修改为当前元胞类型。

        支持：

            FREE
            WALL
            OBSTACLE
            EXIT
        """

        changed = 0

        for y in range(
            ymin,
            ymax + 1
        ):

            for x in range(
                xmin,
                xmax + 1
            ):

                if (
                    0 <= x < self.grid.width
                    and
                    0 <= y < self.grid.height
                ):

                    cell = self.grid.get_cell(
                        x,
                        y
                    )

                    cell.cell_type = (
                        self.current_type
                    )

                    # ------------------------------------------------
                    # 如果修改成 WALL / OBSTACLE，
                    # 原来的 semantic 没有实际意义。
                    #
                    # 这里暂时不删除 semantic，
                    # 保持原来的数据结构和接口行为。
                    # ------------------------------------------------

                    changed += 1

        print(
            "=============================="
        )

        print(
            "批量修改元胞类型"
        )

        print(
            "类型:",
            self.current_type.value
        )

        print(
            "区域:",
            xmin,
            ymin,
            xmax,
            ymax
        )

        print(
            "修改元胞数量:",
            changed
        )

        print(
            "=============================="
        )

    # ========================================================
    # 语义区域修改
    # ========================================================

    def apply_semantic_to_region(
        self,
        xmin,
        ymin,
        xmax,
        ymax
    ):
        """
        将选中的 FREE 元胞批量设置为当前 semantic。

        注意：

            只有 FREE 元胞可以设置 semantic。
        """

        if self.current_semantic is None:

            print(
                "⚠ 当前没有选择语义"
            )

            return

        changed = 0

        skipped = 0

        for y in range(
            ymin,
            ymax + 1
        ):

            for x in range(
                xmin,
                xmax + 1
            ):

                if (
                    0 <= x < self.grid.width
                    and
                    0 <= y < self.grid.height
                ):

                    cell = self.grid.get_cell(
                        x,
                        y
                    )

                    # ------------------------------------------------
                    # 只有 FREE 可以标记 semantic
                    # ------------------------------------------------

                    if cell.cell_type == CellType.FREE:

                        cell.semantic = (
                            self.current_semantic
                        )

                        changed += 1

                    else:

                        skipped += 1

        print(
            "=============================="
        )

        print(
            "批量设置区域语义"
        )

        print(
            "语义:",
            self.current_semantic.value
        )

        print(
            "区域:",
            xmin,
            ymin,
            xmax,
            ymax
        )

        print(
            "成功标注:",
            changed
        )

        print(
            "跳过非 FREE 元胞:",
            skipped
        )

        print(
            "=============================="
        )

    # ========================================================
    # 单个类型修改接口
    # ========================================================

    def modify_type(
        self,
        x,
        y
    ):

        if (
            x < 0
            or
            x >= self.grid.width
            or
            y < 0
            or
            y >= self.grid.height
        ):

            return

        cell = self.grid.get_cell(
            x,
            y
        )

        cell.cell_type = (
            self.current_type
        )

        print(
            "修改类型:",
            self.current_type.value,
            "位置:",
            x,
            y
        )

        self.draw()

    # ========================================================
    # 键盘
    # ========================================================

    def keypress(self, event):

        if event.key is None:

            return

        key = event.key.lower()

        print(
            "按键:",
            key
        )

        # ====================================================
        # 类型
        # ====================================================

        if key == "1":

            self.edit_mode = "type"

            self.current_type = (
                CellType.FREE
            )

            self.current_semantic = None

            print(
                "当前模式: 空地"
            )

        elif key == "2":

            self.edit_mode = "type"

            self.current_type = (
                CellType.WALL
            )

            self.current_semantic = None

            print(
                "当前模式: 墙"
            )

        elif key == "3":

            self.edit_mode = "type"

            self.current_type = (
                CellType.OBSTACLE
            )

            self.current_semantic = None

            print(
                "当前模式: 障碍物"
            )

        elif key == "4":

            self.edit_mode = "type"

            self.current_type = (
                CellType.EXIT
            )

            self.current_semantic = None

            print(
                "当前模式: 出口"
            )

        # ====================================================
        # semantic
        # ====================================================

        semantic_map = {

            "w": SemanticType.CLASSROOM,

            "e": SemanticType.CORRIDOR,

            "r": SemanticType.STAIR,

            "t": SemanticType.SHOP,

            "y": SemanticType.HALL,

            "u": SemanticType.CANTEEN,

            "i": SemanticType.DORM,

            "o": SemanticType.LIBRARY,

            "p": SemanticType.HOSPITAL

        }

        if key in semantic_map:

            self.edit_mode = (
                "semantic"
            )

            self.current_semantic = (
                semantic_map[key]
            )

            print(
                "当前语义:",
                self.current_semantic.value
            )

        # ====================================================
        # ESC
        # ====================================================

        elif key == "escape":

            self.drag_start = None

            self.drag_end = None

            self.is_dragging = False

            self.edit_mode = "type"

            print(
                "取消当前操作"
            )

        # ====================================================
        # 重新绘制
        # ====================================================

        self.draw()

    # ========================================================
    # 显示
    # ========================================================

    def show(self):

        plt.show()

    # ========================================================
    # 构造 JSON
    # ========================================================

    def build_json_data(self):

        data = {

            "name": "edited_map",

            "width": self.grid.width,

            "height": self.grid.height,

            "cell_size": self.grid.cell_size,

            "cells": []

        }

        for cell in self.grid.cells:

            item = {

                "x": cell.x,

                "y": cell.y,

                "type": (
                    cell.cell_type.value
                ),

                "room_id": cell.room_id,

                "smoke": cell.smoke,

                "risk": cell.risk,

                "guidance": cell.guidance

            }

            if cell.semantic is not None:

                if hasattr(
                    cell.semantic,
                    "value"
                ):

                    item["semantic"] = (
                        cell.semantic.value
                    )

                else:

                    item["semantic"] = str(
                        cell.semantic
                    )

            else:

                item["semantic"] = None

            data["cells"].append(
                item
            )

        return data

    # ========================================================
    # 保存 JSON
    # ========================================================

    def save_json(
        self,
        filename
    ):
        """
        保存编辑后的地图。

        注意：
        这里仍然保留你原来的接口。

        新增功能：

            保存成功后自动调用 A08。
        """

        filename = Path(
            filename
        ).resolve()

        # ====================================================
        # 创建目录
        # ====================================================

        filename.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        # ====================================================
        # 构造地图数据
        # ====================================================

        data = self.build_json_data()

        # ====================================================
        # 保存
        # ====================================================

        with open(
            filename,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=4,
                ensure_ascii=False
            )

        print()
        print(
            "=============================="
        )

        print(
            "JSON保存成功:"
        )

        print(
            filename
        )

        print(
            "=============================="
        )

        # ====================================================
        # 自动进入 A08
        # ====================================================

        try:

            from semantic.semantic_lable import (
                process_map
            )

            print()
            print(
                "正在自动进入 A08 语义标签系统..."
            )

            semantic_path = (
                process_map(
                    filename
                )
            )

            print()
            print(
                "A08 处理完成:"
            )

            print(
                semantic_path
            )

            print(
                "=============================="
            )

            return semantic_path

        except Exception as e:

            # =================================================
            # A08 出错不能导致地图保存失败
            # =================================================

            print()
            print(
                "⚠ A08 自动处理失败:"
            )

            print(
                type(e).__name__,
                e
            )

            print()
            print(
                "但原始编辑地图已经成功保存:"
            )

            print(
                filename
            )

            print(
                "=============================="
            )

            return filename

    # ========================================================
    # 推荐的保存接口
    # ========================================================

    def save_and_process(
        self,
        filename=None
    ):
        """
        推荐给外部程序调用。

        如果没有指定 filename，
        自动保存到：

            maps/edited/edited_map.json
        """

        if filename is None:

            filename = (
                PROJECT_ROOT
                / "maps"
                / "edited"
                / "edited_map.json"
            )

        return self.save_json(
            filename
        )


# ============================================================
# 对外便捷函数
# ============================================================

def save_edited_map(
    editor: MapEditor,
    filename=None
):
    """
    给其他模块使用。

    示例：

        result = save_edited_map(editor)

    返回：

        A08 输出的地图路径
    """

    return editor.save_and_process(
        filename
    )
