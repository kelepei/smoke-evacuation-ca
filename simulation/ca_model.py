from core.grid import Grid
from core.schema import CellType
from typing import Dict
from dataclasses import dataclass


@dataclass
class SimplePerson:
    """临时行人类，后续对接C模块时替换为schema.Person"""
    pid: int
    x: int
    y: int


class CAModel:
    def __init__(self, grid: Grid):
        # 严格按照文档：CA模型仅接收Grid对象
        self.grid: Grid = grid
        self.persons: Dict[int, SimplePerson] = {}
        # B模块内部定义烟源参数
        self.smoke_sources = [{"x": 6, "y": 6, "strength": 1.0}]
        # 烟雾浓度矩阵
        self.smoke_matrix = [[0.0 for _ in range(grid.width)] for _ in range(grid.height)]

    def show_map_info(self):
        """打印地图信息，验证Grid读取正常"""
        print("地图尺寸：", self.grid.width, self.grid.height)
        for cell in self.grid.cells:
            print(cell.x, cell.y, cell.cell_type)

    def get_neighbors(self, x: int, y: int):
        """获取当前元胞8邻域"""
        return self.grid.get_neighbors(x, y)

    def add_person(self, pid: int, x: int, y: int):
        """新增行人"""
        self.persons[pid] = SimplePerson(pid, x, y)

    def _update_smoke(self):
        """烟雾扩散逻辑"""
        for source in self.smoke_sources:
            sx, sy = source["x"], source["y"]
            strength = source["strength"]
            if 0 <= sx < self.grid.width and 0 <= sy < self.grid.height:
                self.smoke_matrix[sy][sx] = min(1.0, self.smoke_matrix[sy][sx] + strength * 0.05)

    def step(self):
        """单步仿真：烟雾更新 + CA行人移动规则"""
        self._update_smoke()

        # 行人移动逻辑，后续替换为你的效用函数模型
        for person in self.persons.values():
            neighbors = self.get_neighbors(person.x, person.y)
            candidates = []
            for cell in neighbors:
                # 仅允许移动到空地
                if cell.cell_type == CellType.FREE:
                    candidates.append(cell)

            if candidates:
                target_cell = candidates[0]
                person.x = target_cell.x
                person.y = target_cell.y

    def run(self, max_step=500):
        """持续运行仿真"""
        for step in range(max_step):
            self.step()
            # 每20步打印状态
            if step % 20 == 0:
                for p in self.persons.values():
                    print(f"Step {step} 行人{p.pid} 坐标({p.x},{p.y})")
