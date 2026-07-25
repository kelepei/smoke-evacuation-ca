import json


from core.schema import Cell,CellType

from core.grid import Grid




def load_grid(filename):


    with open(

        filename,

        "r",

        encoding="utf-8"

    ) as f:


        data=json.load(f)




    cells=[]



    for item in data["cells"]:


        cell=Cell(

            x=item["x"],

            y=item["y"],

            cell_type=CellType(
                item["type"]
            )

        )


        cells.append(cell)




    grid=Grid(

        width=data["width"],

        height=data["height"],

        cell_size=data["cell_size"],

        cells=cells

    )



    return grid
