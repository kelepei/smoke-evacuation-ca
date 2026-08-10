from dataclasses import dataclass
from typing import List

from core.schema import Cell




@dataclass
class Grid:


    # x方向数量
    width:int


    # y方向数量
    height:int


    # 一个格子实际大小
    cell_size:float


    # 所有Cell
    cells:List[Cell]



    def get_cell(self,x,y):

        """
        根据坐标查找格子
        """

        for cell in self.cells:

            if cell.x==x and cell.y==y:

                return cell


        return None
