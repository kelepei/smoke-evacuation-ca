""" 疏散仿真引擎 管理行人状态、烟雾更新、移动计算 """
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
from .conflict_solver import resolve_conflict
from .exit_choice import ExitChooser
from .congestion import CongestionModel


class EvacEngine:
    """
    疏散仿真引擎
    适配A模块输出人员坐标
    改动：接入外部conflict_solver冲突消解、增加actual_exit出口记录；新增B03出口选择、B09拥堵模型
    新增：支持人员烟雾中毒死亡，死亡人员原地占用元胞，不参与移动
    新增B08：烟雾警报功能。场景存在alarm点位时检测报警器；无alarm点位自动回退检测烟源，快照输出警报状态
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

        # ===== 新增：读取仿真单步时间 =====
        self.time_step_s = scene.parameters.get("time_step_s", 1.0)

        # ===== 新增B08烟雾警报配置 =====
        self.alarm_threshold = 0.45  # 烟雾浓度触发阈值
        self.is_alarm_triggered = False  # 警报状态，一旦触发永久保持开启

        # 绑定场景seed，给外部冲突消解、出口选择、拥堵模型使用，保证仿真可复现
        self.random = random.Random(getattr(scene, "parameters", {}).get("random_seed"))

        # ===== B03出口选择、B09拥堵模型实例化 =====
        self.exit_chooser = ExitChooser(scene, rng=self.random)
        self.congestion_model = CongestionModel(grid=self.grid, neighbor_radius=2, density_threshold=0.35)

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
            weight_delta=0.6,
            weight_vis=1.2,
            death_dose_threshold=12.0
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
        self.alarm_points = getattr(scene, "alarm_points", []) # 新增：读取场景报警器点位

    def load_external_persons(self, persons):
        self.person_map.clear()
        for person in persons:
            self.person_map[person.id] = person

    def is_all_evacuated(self) -> bool:
        # 全部撤离 OR 全部死亡，仿真结束
        return all(getattr(p, "evacuated", False) or getattr(p, "is_dead", False) for p in self.person_map.values())

    def get_evacuated_count(self) -> int:
        return sum(1 for p in self.person_map.values() if getattr(p, "evacuated", False))

    def get_dead_count(self) -> int:
        return sum(1 for p in self.person_map.values() if getattr(p, "is_dead", False))

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

        # ====================== B08 烟雾警报判断【兼容新旧地图】 ======================
        if not self.is_alarm_triggered:
            alarm_points = getattr(self.scene, "alarm_points", [])
            if alarm_points and len(alarm_points) > 0:
                # 场景有报警器点位：检测alarm点
                for alarm in alarm_points:
                    ax, ay = int(alarm.x), int(alarm.y)
                    if 0 <= ax < self.width and 0 <= ay < self.height:
                        conc = smoke_mat[ay, ax]
                        if conc >= self.alarm_threshold:
                            self.is_alarm_triggered = True
                            print(f"[ALARM] 报警器触发！Step:{self.current_step}, 报警器坐标({ax},{ay}),浓度:{conc:.3f}")
                            break
            else:
                # 场景无alarm点位，回退旧逻辑，检测烟源
                for src in self.scene.smoke_sources:
                    sx, sy = int(src.x), int(src.y)
                    if 0 <= sx < self.width and 0 <= sy < self.height:
                        conc_at_source = smoke_mat[sy, sx]
                        if conc_at_source >= self.alarm_threshold:
                            self.is_alarm_triggered = True
                            print(f"[ALARM] 烟源触发警报！Step:{self.current_step},烟源浓度:{conc_at_source:.3f}")
                            break
        # ============================================================================

        # 2. 批量计算行人风险 ✅ 新增 time_step_s 参数
        risk_dict = self.risk_engine.batch_calc_all_risk(
            list(self.person_map.values()),
            smoke_mat,
            time_step_s=self.time_step_s
        )

        # 3. 更新烟雾累积剂量
        self.dose_recorder.update_all_dose(list(self.person_map.values()), smoke_mat)

        # 4. 标记占用坐标，避免行人重叠 ✅ 死亡人员保留占用元胞
        occupied_positions = set()
        for pid, person in self.person_map.items():
            if not getattr(person, "evacuated", False) and not getattr(person, "is_dead", False):
                occupied_positions.add((int(person.x), int(person.y)))
        alive_person_pos = occupied_positions

        # =========【修复】循环外提前构造出口列表，兼容Exit对象 / tuple元组 =========
        exit_list = []
        for e in self.exits:
            if isinstance(e, tuple):
                exit_list.append(e)
            else:
                exit_list.append((e.id, e.x, e.y))

        # 5. 预计算下一时刻位置 ✅ 死亡人员跳过移动计算
        next_positions = {}
        for pid, person in self.person_map.items():
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
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
                occupied_positions=occupied_positions,
                exit_list=exit_list,
                exit_chooser=self.exit_chooser,
                congestion_model=self.congestion_model,
                alive_person_pos=alive_person_pos,
                rng=self.random,
                person_map=self.person_map  # ✅迭代1：传入person_map给ca_model，为跟随预留
            )
            next_positions[pid] = (nx, ny)

        # -------- 调用外部conflict_solver做冲突消解 --------
        fixed_next_pos = resolve_conflict(next_positions, self.person_map, self.random)

        # 6. 更新坐标 & 判断是否撤离，记录 actual_exit ✅【修复这里！兼容tuple/对象】
        for pid, (nx, ny) in fixed_next_pos.items():
            person = self.person_map[pid]
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
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
                    # 兼容两种格式：Exit对象 或者 (id, x, y)元组
                    if isinstance(e, tuple):
                        eid, ex, ey = e
                    else:
                        ex, ey, eid = e.x, e.y, e.id
                    if ex == px and ey == py:
                        person.actual_exit = eid
                        break

        # 7. 更新统计
        self.evacuated_count = self.get_evacuated_count()
        dead_count = self.get_dead_count()
        self.current_step += 1

        # 内存日志（仅内部查看）
        if self.current_step % 10 == 0:
            self.step_log.append({
                "step": self.current_step,
                "evacuated": self.evacuated_count,
                "total": self.total_persons,
                "remaining": self.total_persons - self.evacuated_count - dead_count,
                "dead": dead_count,
                "alarm": self.is_alarm_triggered
            })

        return {
            "step": self.current_step,
            "evacuated": self.evacuated_count,
            "total": self.total_persons,
            "remaining": self.total_persons - self.evacuated_count - dead_count,
            "dead": dead_count,
            "alarm": self.is_alarm_triggered # 输出警报状态给D可视化
        }

    def get_person_positions(self) -> dict:
        return {
            pid: (int(p.x), int(p.y))
            for pid, p in self.person_map.items()
            if not getattr(p, "evacuated", False)
        }

    def get_evacuation_time(self, person_id: int = None) -> int:
        if person_id is None:
            evac_steps = [p.evac_step for p in self.person_map.values() if getattr(p, "evac_step", -1) >= 0]
            if len(evac_steps) == self.total_persons:
                return max(evac_steps)
            return -1
        else:
            p = self.person_map.get(person_id)
            return p.evac_step if (p and hasattr(p, "evac_step")) else -1
