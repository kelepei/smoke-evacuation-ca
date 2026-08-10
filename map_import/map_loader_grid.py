import json

from core.schema import (
    Cell,
    CellType,
    SemanticType
)

from core.grid import Grid

from map_import.map_validator import validate_map



# ==================================================
# JSON -> Grid
# ==================================================

def load_grid(filename):
    """
    读取JSON地图文件

    返回:
        Grid对象
    """

    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:

        data = json.load(f)



    # 格式检查

    if not validate_map(data):

        raise ValueError(
            "地图JSON格式错误"
        )



    cells = []



    for item in data["cells"]:


        semantic = None


        if item.get("semantic"):

            semantic = SemanticType(
                item["semantic"]
            )



        cell = Cell(

            x=item["x"],

            y=item["y"],

            cell_type=CellType(
                item["type"]
            ),

            room_id=item.get(
                "room_id",
                ""
            ),

            semantic=semantic,

            smoke=item.get(
                "smoke",
                0.0
            ),

            risk=item.get(
                "risk",
                0.0
            ),

            guidance=item.get(
                "guidance",
                0.0
            )

        )


        cells.append(cell)



    grid = Grid(

        width=data["width"],

        height=data["height"],

        cell_size=data["cell_size"],

        cells=cells

    )


    return grid





# ==================================================
# Grid -> JSON
# ==================================================

def save_grid_json(
            grid,
            filename,
            name="png_import_map"
    ):
        """
        保存Grid为统一JSON格式
        """

        data = {

            "name": name,

            "width": grid.width,

            "height": grid.height,

            "cell_size": grid.cell_size,

            "cells": []

        }

        for cell in grid.cells:

            cell_data = {

                "x": cell.x,

                "y": cell.y,

                "type": cell.cell_type.value,

                "room_id": cell.room_id,

                "smoke": cell.smoke,

                "risk": cell.risk,

                "guidance": cell.guidance

            }

            
            if cell.semantic is not None:
                cell_data["semantic"] = (
                    cell.semantic.value
                )

            data["cells"].append(
                cell_data
            )

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
