import json


from core.schema import (
    Cell,
    CellType,
    SemanticType
)


from core.grid import Grid


from map_import.map_validator import validate_map




def load_grid(filename):

    """
    读取JSON地图文件

    返回:
        Grid对象
    """



    # ==========================
    # 读取JSON
    # ==========================

    with open(

            filename,

            "r",

            encoding="utf-8"

    ) as f:


        data = json.load(f)




    # ==========================
    # 格式检查
    # ==========================

    if not validate_map(data):

        raise ValueError(
            "地图JSON格式错误"
        )




    cells = []



    # ==========================
    # JSON -> Cell
    # ==========================


    for item in data["cells"]:



        # semantic转换

        semantic = None


        if "semantic" in item:


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




    # ==========================
    # Cell列表 -> Grid
    # ==========================


    grid = Grid(


        width=data["width"],


        height=data["height"],


        cell_size=data["cell_size"],


        cells=cells

    )



    return grid
