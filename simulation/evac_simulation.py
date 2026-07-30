import sys
from pathlib import Path

BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

from core.schema import ScenarioConfig, Person, Grid, Cell, CellType
from simulation.floor_field import FloorField
from simulation.smoke_model import SmokeSim
from simulation.risk_perception import RiskPerception
from simulation.conflict_solver import resolve_conflict
from scenarios.mock_data import build_base_scene


def get_cell(grid: Grid, x: int, y: int) -> Cell:
    """根据x,y坐标获取元胞对象，适配仓库一维cells存储"""
    idx = y * grid.width + x
    return grid.cells[idx]


class CaEvacSimulation:
    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.grid: Grid = config.grid
        self.persons: dict[int, Person] = {p.id: p for p in config.persons}
        # 自己维护撤离状态，兼容schema版本问题
        self._evacuated_status: dict[int, bool] = {pid: False for pid in self.persons.keys()}

        self.floor_field = FloorField(self.grid, config.exits)
        self.smoke_sim = SmokeSim(self.grid, config.smoke_sources)
        self.risk_perception = RiskPerception()

        self.current_step = 0
        self.max_step = 500
        self.neighbors_8 = [(-1, -1), (0, -1), (1, -1),
                            (-1, 0),          (1, 0),
                            (-1, 1),  (0, 1), (1, 1)]

    def init_simulation(self):
        self.floor_field.compute_distance_field()
        self.smoke_sim.init_smoke_matrix()
        print(f"初始化完成，行人状态：{self._evacuated_status}")

    def step(self):
        # 1 更新烟雾
        self.smoke_sim.step()
        smoke_matrix = self.smoke_sim.smoke_matrix

        move_candidate = {}

        # 2 每个行人计算候选移动位置，仅8邻域
        for pid, person in self.persons.items():
            if self._evacuated_status[pid]:
                continue
            px, py = person.x, person.y
            best_pos = (px, py)   # 默认留在原地
            best_util = -1e9

            for dx, dy in self.neighbors_8:
                nx = px + dx
                ny = py + dy
                # 边界校验
                if not (0 <= nx < self.grid.width and 0 <= ny < self.grid.height):
                    continue
                cell = get_cell(self.grid, nx, ny)
                # 墙体、障碍物不可走
                if cell.cell_type in (CellType.WALL, CellType.OBSTACLE):
                    continue

                dist_cost = self.floor_field.dist_field[ny][nx]
                smoke_cost = smoke_matrix[ny][nx]
                util = - dist_cost - 3.0 * smoke_cost

                if util > best_util:
                    best_util = util
                    best_pos = (nx, ny)
            move_candidate[pid] = best_pos

        # 调用重写好的冲突消解
        resolved_moves = resolve_conflict(move_candidate)

        # 4 执行移动，判断是否到达出口
        for pid, (nx, ny) in resolved_moves.items():
            if self._evacuated_status[pid]:
                continue
            p = self.persons[pid]
            p.x, p.y = nx, ny
            cell_now = get_cell(self.grid, nx, ny)
            if cell_now.cell_type == CellType.EXIT:
                self._evacuated_status[pid] = True
                print(f"pid {pid} 到达出口，完成撤离")

        self.current_step += 1

    def all_done(self) -> bool:
        result = all(val is True for val in self._evacuated_status.values())
        # print(f"step={self.current_step}, 是否全部撤离:{result}, status:{self._evacuated_status}")
        return result

    def print_text_map(self):
        """控制台打印文本地图用于观察仿真"""
        w = self.grid.width
        h = self.grid.height
        smoke = self.smoke_sim.smoke_matrix
        lines = []
        for y in range(h):
            line_chars = []
            for x in range(w):
                has_person = False
                for pid, p in self.persons.items():
                    if p.x == x and p.y == y and not self._evacuated_status[pid]:
                        has_person = True
                        break
                cell = get_cell(self.grid, x, y)
                if has_person:
                    line_chars.append("P")
                elif cell.cell_type == CellType.WALL:
                    line_chars.append("#")
                elif cell.cell_type == CellType.EXIT:
                    line_chars.append("E")
                elif smoke[y][x] > 0.3:
                    line_chars.append("S")
                else:
                    line_chars.append(".")
            lines.append("".join(line_chars))
        print(f"\n==== Step {self.current_step} ====")
        print("\n".join(lines))

    def run(self):
        self.init_simulation()
        while self.current_step < self.max_step and not self.all_done():
            self.step()
            if self.current_step % 20 == 0:
                self.print_text_map()
        print(f"\n====仿真结束，总步数 {self.current_step} ====")


if __name__ == "__main__":
    scene_cfg = build_base_scene()
    sim = CaEvacSimulation(scene_cfg)
    sim.run()