import matplotlib.pyplot as plt
import matplotlib

from matplotlib.colors import ListedColormap

from core.schema import CellType


# Mac中文字体
matplotlib.rcParams["font.sans-serif"] = [
    "PingFang SC"
]

matplotlib.rcParams["axes.unicode_minus"] = False



class MapEditor:


    def __init__(self, grid):

        self.grid = grid


        # 当前编辑类型
        self.current_type = CellType.FREE


        self.fig, self.ax = plt.subplots(
            figsize=(12, 10)
        )


        self.fig.canvas.mpl_connect(
            "button_press_event",
            self.onclick
        )


        self.fig.canvas.mpl_connect(
            "key_press_event",
            self.keypress
        )


        self.draw()



    # ==================================
    # 绘制地图
    # ==================================

    def draw(self):

        self.ax.clear()


        image = []


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



        # 固定颜色

        cmap = ListedColormap(
            [
                "white",   # FREE
                "black",   # WALL
                "gray",    # OBSTACLE
                "green"    # EXIT
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



        # 网格

        self.ax.grid(
            True,
            linewidth=0.3
        )



        # 坐标显示优化

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



        self.ax.set_title(

            "CA Map Editor\n"
            "1-Free  2-Wall  "
            "3-Obstacle  4-Exit"

        )


        self.fig.canvas.draw()



    # ==================================
    # 点击修改
    # ==================================

    def onclick(self, event):


        if event.xdata is None:

            return



        x = int(event.xdata)

        y = int(event.ydata)



        if (

            x < 0

            or x >= self.grid.width

            or y < 0

            or y >= self.grid.height

        ):

            return



        cell = self.grid.get_cell(
            x,
            y
        )


        cell.cell_type = self.current_type



        print("================")

        print(
            "修改元胞:"
        )


        print(
            "x:",
            x,
            "y:",
            y
        )


        print(
            "类型:",
            self.current_type.value
        )


        print(
            "实际位置:",
            round(
                x * self.grid.cell_size,
                2
            ),
            "m ,",
            round(
                y * self.grid.cell_size,
                2
            ),
            "m"
        )


        print("================")



        self.draw()



    # ==================================
    # 键盘切换
    # ==================================

    def keypress(self,event):


        if event.key == "1":

            self.current_type = CellType.FREE

            print(
                "当前模式: 空地"
            )


        elif event.key == "2":

            self.current_type = CellType.WALL

            print(
                "当前模式: 墙"
            )


        elif event.key == "3":

            self.current_type = CellType.OBSTACLE

            print(
                "当前模式: 障碍物"
            )


        elif event.key == "4":

            self.current_type = CellType.EXIT

            print(
                "当前模式: 出口"
            )



    # ==================================
    # 显示
    # ==================================

    def show(self):

        plt.show()



    # ==================================
    # 保存JSON
    # ==================================

    def save_json(self, filename):

        import json


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

                "type": cell.cell_type.value,

                "room_id": cell.room_id,

                "smoke": cell.smoke,

                "risk": cell.risk,

                "guidance": cell.guidance

            }


            if cell.semantic is not None:

                item["semantic"] = cell.semantic.value


            data["cells"].append(item)



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


        print(
            "JSON保存成功:",
            filename
        )
