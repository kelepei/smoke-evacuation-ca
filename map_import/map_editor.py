import json

import matplotlib.pyplot as plt
import matplotlib

from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle

from core.schema import (
    CellType,
    SemanticType
)


# Mac中文显示

matplotlib.rcParams["font.sans-serif"] = [
    "PingFang SC"
]

matplotlib.rcParams["axes.unicode_minus"] = False



class MapEditor:


    def __init__(self, grid):

        self.grid = grid


        # ==========================
        # 编辑状态
        # ==========================

        # type / semantic
        self.edit_mode = "type"


        self.current_type = CellType.FREE


        self.current_semantic = None



        # ==========================
        # 语义框选
        # ==========================

        self.drag_start = None

        self.drag_end = None



        # ==========================
        # 窗口
        # ==========================

        self.fig, self.ax = plt.subplots(
            figsize=(12,10)
        )



        # 鼠标事件

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


        self.fig.canvas.mpl_connect(
            "key_press_event",
            self.keypress
        )


        self.draw()



    # =================================
    # 绘制
    # =================================

    def draw(self):


        self.ax.clear()


        image=[]



        for y in range(self.grid.height):

            row=[]


            for x in range(self.grid.width):


                cell=self.grid.get_cell(
                    x,
                    y
                )


                if cell.cell_type==CellType.FREE:

                    row.append(0)


                elif cell.cell_type==CellType.WALL:

                    row.append(1)


                elif cell.cell_type==CellType.OBSTACLE:

                    row.append(2)


                elif cell.cell_type==CellType.EXIT:

                    row.append(3)


                else:

                    row.append(0)


            image.append(row)



        cmap=ListedColormap(
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



        # ==========================
        # 显示语义
        # ==========================

        for cell in self.grid.cells:


            if cell.semantic is not None:


                if hasattr(
                    cell.semantic,
                    "value"
                ):

                    text=cell.semantic.value[:3]

                else:

                    text=str(
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



        # ==========================
        # 显示拖动框
        # ==========================

        if (

            self.drag_start is not None

            and

            self.drag_end is not None

        ):


            x1,y1=self.drag_start

            x2,y2=self.drag_end



            xmin=min(x1,x2)

            ymin=min(y1,y2)


            width=abs(x2-x1)+1

            height=abs(y2-y1)+1



            rect=Rectangle(

                (

                    xmin-0.5,

                    ymin-0.5

                ),

                width,

                height,

                fill=False,

                linewidth=2

            )


            self.ax.add_patch(rect)



        # 网格

        self.ax.grid(
            True,
            linewidth=0.3
        )



        step_x=max(
            1,
            self.grid.width//15
        )


        step_y=max(
            1,
            self.grid.height//15
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

            f"Cell Size={self.grid.cell_size}m\n"

            "类型:1空地 2墙 3障碍 4出口\n"

            "语义:Q教室 W走廊 E楼梯 R商店\n"

            "T大厅 Y食堂 U宿舍 I图书馆 O医院"

        )


        self.fig.canvas.draw_idle()




    # =================================
    # 鼠标按下
    # =================================

    def on_press(self,event):


        if event.xdata is None:

            return



        x=int(event.xdata)

        y=int(event.ydata)



        if self.edit_mode=="semantic":


            self.drag_start=(

                x,

                y

            )


        else:


            self.modify_type(
                x,
                y
            )




    # =================================
    # 鼠标移动
    # =================================

    def on_move(self,event):


        if self.edit_mode!="semantic":

            return



        if self.drag_start is None:

            return



        if event.xdata is None:

            return



        self.drag_end=(

            int(event.xdata),

            int(event.ydata)

        )


        self.draw()




    # =================================
    # 鼠标释放
    # =================================

    def on_release(self,event):


        if self.edit_mode!="semantic":

            return



        if self.drag_start is None:

            return



        if event.xdata is None:

            return



        self.drag_end=(

            int(event.xdata),

            int(event.ydata)

        )



        x1,y1=self.drag_start

        x2,y2=self.drag_end



        xmin=min(x1,x2)

        xmax=max(x1,x2)

        ymin=min(y1,y2)

        ymax=max(y1,y2)



        for y in range(

            ymin,

            ymax+1

        ):


            for x in range(

                xmin,

                xmax+1

            ):



                if (

                    0<=x<self.grid.width

                    and

                    0<=y<self.grid.height

                ):


                    cell=self.grid.get_cell(
                        x,
                        y
                    )


                    if cell.cell_type==CellType.FREE:


                        cell.semantic=(

                            self.current_semantic

                        )



        print("================")

        print(

            "设置区域语义:",

            self.current_semantic.value

        )

        print(

            "区域:",

            xmin,

            ymin,

            xmax,

            ymax

        )

        print("================")



        self.drag_start=None

        self.drag_end=None



        self.draw()




    # =================================
    # 类型修改
    # =================================

    def modify_type(
            self,
            x,
            y
    ):


        if (

            x<0

            or x>=self.grid.width

            or y<0

            or y>=self.grid.height

        ):

            return



        cell=self.grid.get_cell(
            x,
            y
        )


        cell.cell_type=self.current_type



        print(

            "修改类型:",

            self.current_type.value,

            "位置:",

            x,

            y

        )


        self.draw()




    # =================================
    # 键盘
    # =================================

    def keypress(self,event):


        key=event.key.lower()


        print(
            "按键:",
            key
        )


        # 类型


        if key=="1":

            self.edit_mode="type"

            self.current_type=CellType.FREE

            print("当前模式:空地")



        elif key=="2":

            self.edit_mode="type"

            self.current_type=CellType.WALL

            print("当前模式:墙")



        elif key=="3":

            self.edit_mode="type"

            self.current_type=CellType.OBSTACLE

            print("当前模式:障碍物")



        elif key=="4":

            self.edit_mode="type"

            self.current_type=CellType.EXIT

            print("当前模式:出口")



        # semantic


        semantic_map={


            "q":SemanticType.CLASSROOM,

            "w":SemanticType.CORRIDOR,

            "e":SemanticType.STAIR,

            "r":SemanticType.SHOP,

            "t":SemanticType.HALL,

            "y":SemanticType.CANTEEN,

            "u":SemanticType.DORM,

            "i":SemanticType.LIBRARY,

            "o":SemanticType.HOSPITAL

        }



        if key in semantic_map:


            self.edit_mode="semantic"

            self.current_semantic=semantic_map[key]


            print(

                "当前语义:",

                self.current_semantic.value

            )




    # =================================
    # 显示
    # =================================

    def show(self):

        plt.show()




    # =================================
    # 保存JSON
    # =================================

    def save_json(
            self,
            filename
    ):


        data={

            "name":"edited_map",

            "width":self.grid.width,

            "height":self.grid.height,

            "cell_size":self.grid.cell_size,

            "cells":[]

        }



        for cell in self.grid.cells:


            item={


                "x":cell.x,

                "y":cell.y,

                "type":cell.cell_type.value,

                "room_id":cell.room_id,

                "smoke":cell.smoke,

                "risk":cell.risk,

                "guidance":cell.guidance

            }



            if cell.semantic is not None:


                if hasattr(
                    cell.semantic,
                    "value"
                ):

                    item["semantic"]=(
                        cell.semantic.value
                    )

                else:

                    item["semantic"]=str(
                        cell.semantic
                    )



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
