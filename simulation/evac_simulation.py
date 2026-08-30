"""
疏散仿真引擎
管理行人状态、烟雾更新、移动计算
"""

import sys
from pathlib import Path
BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

import random
import numpy as np
from core.schema import ScenarioConfig, Grid, Person, CellType
from .ca_model import calc_next_position
from .smoke_model import SmokeDiffusionModel
from .risk_perception import SmokeRiskPerception
from .risk_metrics import SmokeDoseRecorder
from .floor_field import FloorField


class EvacEngine:
    """
    疏散仿真引擎
    适配A模块输出人员坐标
    改动：接入冲突消解、增加actual_exit出口记录
    """
    MAX_SIM_STEP = 2000  # 最大仿真步数，防止死循环

    def __init__(self, scene: ScenarioConfig):
        """
        初始化仿真引擎
        Args:
            scene: ScenarioConfig 对象，scene.persons 是ca_loader加载完成、带x/y坐标的行人列表
        """
        self.scene: ScenarioConfig = scene
        self.grid: Grid = scene.grid
        self.width = self.grid.width
        self.height = self.grid.height
        self.current_step = 0

        # 1. 初始化距离场
        self.floor_field = FloorField(self.grid, scene.exits)
        self.floor_field.compute_distance_field()

        if self.floor_field.dist_field is not None:
            print(f"[OK] 距离场计算完成，出口数量: {len(scene.exits)}")
        else:
            print("[WARN] 距离场计算失败，行人将无法寻找出口！")

        # 2. 加载外部行人
        self.person_map: dict[int, Person] = {}
        self.load_external_persons(scene.persons)
        print(f"[OK] 载入外部行人 {len(self.person_map)} 个，使用A模块点位")

        # 烟雾模块初始化
        self.smoke_engine = SmokeDiffusionModel(grid=self.grid, diffuse_coeff=0.24, decay_coeff=0.03)
        for src in scene.smoke_sources:
            self.smoke_engine.add_smoke_source(src)

        # 风险感知模型
        self.risk_engine = SmokeRiskPerception(
            weight_conc=1.0,
            weight_delta=0.5,
            weight_vis=1.2
        )

        # 烟雾剂量记录
        self.dose_recorder = SmokeDoseRecorder(delta_t=0.5)
        self.dose_recorder.init_person_dose(list(self.person_map.values()))

        # 统计变量
        self.total_persons = len(self.person_map)
        self.evacuated_count = 0
        self.step_log = []

        # ========= 适配D可视化适配器新增属性 =========
        self.smoke_matrix = self.smoke_engine.smoke_matrix
        self.smoke_sources = scene.smoke_sources
        self.exits = scene.exits

    def load_external_persons(self, persons):
        self.person_map.clear()
        for person in persons:
            self.person_map[person.id] = person

    def _resolve_move_conflict(self, candidate_pos: dict[int, tuple[int, int]]):
        """
        冲突消解：多人预移动到同一个元胞，冲突行人保留原地不动
        :param candidate_pos: {pid: (nx, ny)} 预计算的候选位置
        :return: {pid: (final_x, final_y)} 冲突修正后的位置
        """
        pos_group = {}
        for pid, pos in candidate_pos.items():
            if pos not in pos_group:
                pos_group[pos] = []
            pos_group[pos].append(pid)

        final = {}
        for pos, pid_list in pos_group.items():
            if len(pid_list) == 1:
                final[pid_list[0]] = pos
            else:
                # 冲突，行人留在上一步坐标
                for pid in pid_list:
                    p = self.person_map[pid]
                    final[pid] = (int(p.x), int(p.y))
        return final

    def is_all_evacuated(self) -> bool:
        return all(p.evacuated for p in self.person_map.values())

    def get_evacuated_count(self) -> int:
        return sum(1 for p in self.person_map.values() if p.evacuated)

    def run_one_step(self, c_step_data: dict = None, signage_model=None):
        if c_step_data is None:
            c_step_data = {}

        # 1. 更新烟雾场
        try:
            self.smoke_engine.update_smoke()
        except Exception as e:
            import traceback
            print("\n==================== 烟雾模块异常 ====================")
            traceback.print_exc()
            print("======================================================")
            raise e
        smoke_mat = self.smoke_engine.smoke_matrix
        self.smoke_matrix = smoke_mat   # 同步更新给可视化适配器

        # 同步烟雾浓度到网格对象
        for cell in self.grid.cells:
            y, x = cell.y, cell.x
            if 0 <= y < self.height and 0 <= x < self.width:
                cell.smoke = smoke_mat[y][x]

        # 2. 批量计算行人风险
        risk_dict = self.risk_engine.batch_calc_all_risk(list(self.person_map.values()), smoke_mat)

        # 3. 更新烟雾累积剂量
        self.dose_recorder.update_all_dose(list(self.person_map.values()), smoke_mat)

        # 4. 标记占用坐标，避免行人重叠
        occupied_positions = set()
        for pid, person in self.person_map.items():
            if not person.evacuated:
                occupied_positions.add((int(person.x), int(person.y)))

        # 5. 预计算下一时刻位置
        next_positions = {}
        for pid, person in self.person_map.items():
            if person.evacuated:
                continue
            single_behavior = c_step_data.get(pid, {})
            if "target_exit" in single_behavior:
                person.target_exit_id = single_behavior["target_exit"]

            nx, ny = calc_next_position(
                person,
                self.grid,
                smoke_matrix=smoke_mat,
                risk_dict=risk_dict,
                single_behavior=single_behavior,
                floor_field=self.floor_field,
                signage_model=signage_model,
                occupied_positions=occupied_positions
            )
            next_positions[pid] = (nx, ny)

        # -------- 新增：冲突消解，修正抢占重叠 --------
        fixed_next_pos = self._resolve_move_conflict(next_positions)

        # 6. 更新坐标 & 判断是否撤离，记录 actual_exit
        for pid, (nx, ny) in fixed_next_pos.items():
            person = self.person_map[pid]
            if person.evacuated:
                continue
            person.prev_x = person.x
            person.prev_y = person.y
            person.x = nx
            person.y = ny

            cell = self.grid.get_cell(int(nx), int(ny))
            if cell and cell.cell_type == CellType.EXIT:
                person.evacuated = True
                person.evac_step = self.current_step
                px = int(nx)
                py = int(ny)
                for e in self.exits:
                    ex, ey, eid = e
                    if ex == px and ey == py:
                        person.actual_exit = eid
                        break

        # 7. 更新统计
        self.evacuated_count = self.get_evacuated_count()
        self.current_step += 1

        # 内存日志（仅内部查看）
        if self.current_step % 10 == 0:
            self.step_log.append({
                "step": self.current_step,
                "evacuated": self.evacuated_count,
                "total": self.total_persons,
                "remaining": self.total_persons - self.evacuated_count
            })

        return {
            "step": self.current_step,
            "evacuated": self.evacuated_count,
            "total": self.total_persons,
            "remaining": self.total_persons - self.evacuated_count,
        }

    def get_person_positions(self) -> dict:
        return {
            pid: (int(p.x), int(p.y))
            for pid, p in self.person_map.items()
            if not p.evacuated
        }

    def get_evacuation_time(self, person_id: int = None) -> int:
        if person_id is None:
            evac_steps = [p.evac_step for p in self.person_map.values() if p.evac_step >= 0]
            if len(evac_steps) == self.total_persons:
                return max(evac_steps)
            return -1
        else:
            p = self.person_map.get(person_id)
            return p.evac_step if p else -1