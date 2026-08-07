# 基础结构体从core统一读取
from core.schema import ScenarioConfig, Grid, Person, CellType
from simulation.ca_model import calc_next_position
from simulation.smoke_model import update_smoke

class EvacEngine:
    def __init__(self, scene: ScenarioConfig):
        # scene是ScenarioConfig，场景.grid才是网格对象
        self.scene: ScenarioConfig = scene
        self.grid: Grid = scene.grid
        self.person_map: dict[int, Person] = {p.id: p for p in scene.persons}
        self.width = self.grid.width
        self.height = self.grid.height
        self.smoke_matrix = [[0.0 for _ in range(self.width)] for _ in range(self.height)]
        self.current_step = 0

        # 初始化烟源
        for smoke_source in scene.smoke_sources:
            if 0 <= smoke_source.x < self.width and 0 <= smoke_source.y < self.height:
                self.smoke_matrix[smoke_source.y][smoke_source.x] += smoke_source.intensity

    def is_all_evacuated(self) -> bool:
        return all(p.evacuated for p in self.person_map.values())

    def run_one_step(self, c_step_data: dict):
        # 1、烟雾更新
        self.smoke_matrix = update_smoke(
            smoke_mat=self.smoke_matrix,
            width=self.width,
            height=self.height,
            smoke_sources=self.scene.smoke_sources
        )

        # 网格烟雾同步
        for cell in self.grid.cells:
            cell.smoke_density = self.smoke_matrix[cell.y][cell.x]

        # 行人移动计算
        for pid, person in self.person_map.items():
            if person.evacuated:
                continue
            single_behavior = c_step_data.get(pid, {})
            if "target_exit" in single_behavior:
                person.target_exit = single_behavior["target_exit"]

            nx, ny = calc_next_position(person, self.grid, self.smoke_matrix, single_behavior)
            person.x = nx
            person.y = ny
            person.dose += self.smoke_matrix[ny][nx]

            # 判断出口撤离
            cell_idx = ny * self.width + nx
            if self.grid.cells[cell_idx].cell_type == CellType.EXIT:
                person.evacuated = True

        self.current_step += 1

    def close_anim(self):
        # 动画已删除，保留空方法兼容原有main调用，不会报错
        pass