import csv

from core.schema import Cell, CellType
from core.grid import Grid



def load_csv_grid(filename):

    cells = []


    max_x = 0
    max_y = 0


    with open(
        filename,
        "r",
        encoding="utf-8"
    ) as f:


        reader = csv.DictReader(f)


        for row in reader:


            x = int(row["x"])

            y = int(row["y"])


            cell_type = CellType(
                row["type"]
            )


            cell = Cell(

                x=x,

                y=y,

                cell_type=cell_type

            )


            cells.append(cell)



            max_x = max(max_x,x)

            max_y = max(max_y,y)



    grid = Grid(

        width=max_x+1,

        height=max_y+1,

        cell_size=0.5,

        cells=cells

    )


    return grid
