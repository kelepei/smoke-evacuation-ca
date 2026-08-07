import matplotlib.pyplot as plt
import json

from core.schema import CellType


class MapEditor:

    def __init__(self, grid):

        self.grid = grid

        # 默认编辑障碍物
        self.current_type = CellType.OBSTACLE


        self.fig, self.ax = plt.subplots(
            figsize=(12, 8)
        )


        # 键盘提示
        self.title = (
            "Map Editor | "
            "1:FREE  2:WALL  3:OBSTACLE  "
            "4:EXIT  5:SMOKE"
        )


        self.draw()


        # 鼠标点击
        self.fig.canvas.mpl_connect(
            "button_press_event",
            self.onclick
        )


        # 键盘
        self.fig.canvas.mpl_connect(
            "key_press_event",
            self.onkey
        )



    def draw(self):

        self.ax.clear()


        for cell in self.grid.cells:


            if cell.cell_type == CellType.WALL:

                color = "black"


            elif cell.cell_type == CellType.FREE:

                color = "white"


            elif cell.cell_type == CellType.OBSTACLE:

                color = "red"


            elif cell.cell_type == CellType.EXIT:

                color = "green"


            elif cell.cell_type == CellType.SMOKE_SOURCE:

                color = "orange"


            else:

                color = "white"



            self.ax.scatter(

                cell.x,

                cell.y,

                c=color,

                s=8
            )



        # 坐标方向和图片一致

        self.ax.invert_yaxis()


        self.ax.set_title(
            self.title
        )


        self.ax.set_aspect(
            "equal"
        )


        self.fig.canvas.draw()



    def onclick(self,event):


        if event.xdata is None:
            return

        if event.ydata is None:
            return



        x=int(round(event.xdata))

        y=int(round(event.ydata))


        cell=self.grid.get_cell(
            x,
            y
        )


        if cell is None:

            return



        cell.cell_type = self.current_type



        print(
            "修改:",
            x,
            y,
            self.current_type.value
        )


        self.draw()




    def onkey(self,event):


        if event.key == "1":

            self.current_type = CellType.FREE


        elif event.key == "2":

            self.current_type = CellType.WALL


        elif event.key == "3":

            self.current_type = CellType.OBSTACLE


        elif event.key == "4":

            self.current_type = CellType.EXIT


        elif event.key == "5":

            self.current_type = CellType.SMOKE_SOURCE


        else:

            return



        print(
            "当前模式:",
            self.current_type.value
        )




    def save_json(self, filename):


        data = {

            "name": "edited_map",

            "width": self.grid.width,

            "height": self.grid.height,

            "cell_size": self.grid.cell_size,

            "cells": []

        }



        for cell in self.grid.cells:


            data["cells"].append({

                "x": cell.x,

                "y": cell.y,

                "type": cell.cell_type.value,

                "room_id": cell.room_id,

                "semantic":
                    cell.semantic.value
                    if cell.semantic
                    else None,

                "smoke": cell.smoke,

                "risk": cell.risk,

                "guidance": cell.guidance

            })



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
            "保存完成:",
            filename
        )



    def show(self):

        plt.show()
