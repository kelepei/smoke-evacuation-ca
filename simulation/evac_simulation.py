""" 疏散仿真引擎：管理行人状态、烟雾更新、移动计算

【移动规则文献化改造】
四类调研规则全部收进 B（本引擎），C 只提供行为/信息输入，不再自己算恐慌和速度门控：
1. 恐慌阈值 + 恐慌传染（panic_model）：烟雾/风险把行人推过阈值 α 后进入恐慌，恐慌者速度 1.2 倍；
2. 加速 + 多速度（speed_model）：速度不是瞬时最大，受阻后重新起步要加速，速度差异用距离亏欠补偿；
3. 多出口动态选择（exit_choice + floor_field 每出口距离场）：距离 + 出口拥挤 + 出口烟雾，
   用模糊度 σ 与出口保持率 Pm 控制换出口；
4. 危险感知（danger_perception，替代"不耐烦"）：烟雾越浓、越走不动，压力越大，
   触发横向绕道、抢格优先、换出口。
抢格规则：速度 + 恐慌值（+ 危险压力）叠加最高者获胜。

执行顺序（关键）：
    速度/恐慌/危险感知 → 出口选择 → 只让"允许移动"的人参与 calc_next_position
    → 不允许移动的人作为静止占用者 → resolve_conflict → 更新坐标。
这样不会出现"C 先把人拉回、却已经赢了格子"的问题。

参数默认值在 movement_config.py，可用 scene.parameters["movement"] 覆盖。
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
from .conflict_solver import resolve_conflict, conflict_priority
from .exit_choice import ExitChooser
from .congestion import CongestionModel
from .movement_config import MovementConfig
from .panic_model import PanicModel
from .speed_model import SpeedModel
from .danger_perception import DangerPerceptionModel


class EvacEngine:
    """
    疏散仿真引擎
    适配A模块输出人员坐标
    改动：接入外部conflict_solver冲突消解、增加actual_exit出口记录；新增B03出口选择、B09拥堵模型
    新增：支持人员烟雾中毒死亡，死亡人员原地占用元胞，不参与移动
    新增B08：烟雾警报功能。场景存在alarm点位时检测报警器；无alarm点位自动回退检测烟源，快照输出警报状态
    【新增迭代2】自动知情机制：警报触发后行人有概率接收警报；烟雾剂量达到阈值强制知情，不会永久原地不动
    【本次改动】不改 schema：内部用 cell_index 字典替代 grid.get_cell
    【本次改动】calc_next_position 传入 weights / use_softmax / softmax_lambda
    【本次改动】移动规则：恐慌阈值/加速/多出口/危险感知/速度+恐慌抢格
    """
    MAX_SIM_STEP = 2000  # 最大仿真步数，防止死循环

    def __init__(self, scene: ScenarioConfig):
        self.scene: ScenarioConfig = scene
        self.grid: Grid = scene.grid
        self.width = self.grid.width
        self.height = self.grid.height
        self.current_step = 0

        # 读取仿真单步时间
        self.time_step_s = float(scene.parameters.get("time_step_s", 1.0) or 1.0)
        raw_cell_size = float(getattr(self.grid, "cell_size", 0.5) or 0.5)
        # 地图 JSON 的 cell_size 可能是像素尺度（例如 10），不是米。
        # 移动换算只接受合理的米制元胞尺寸，否则按 0.5 m 参考，避免"一步走 1/20 格"。
        self.cell_size = raw_cell_size if 0.2 <= raw_cell_size <= 2.0 else 0.5

        # 移动规则配置（默认全开，可在 parameters["movement"] 覆盖）
        self.movement = MovementConfig.from_scene(scene)
        self.handles_movement_speed = bool(self.movement.enabled)
        # 未获知险情人员行为（C 桥/main.py 可通过 engine.unknown_behavior 覆盖，保持单一来源）
        self.unknown_behavior = str(self.movement.unknown_behavior or "walk").lower()
        self.unknown_speed_factor = float(self.movement.unknown_speed_factor)
        self._walk_credit: dict[int, float] = {}
        self._walk_dir: dict[int, tuple[int, int]] = {}
        self._walk_total = 0
        self._frozen_total = 0
        self._congested_total = 0
        self._same_cell_total = 0
        self.info_state_owner = "auto"

        # B08 烟雾警报配置
        self.alarm_threshold = 0.45
        self.is_alarm_triggered = False

        # 绑定场景seed
        self.random = random.Random(getattr(scene, "parameters", {}).get("random_seed"))

        # 1. 初始化距离场（含每个出口的独立距离场，多出口选择依赖它）
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

        # 3. 移动规则模型：出口选择 / 恐慌 / 速度 / 危险感知
        self.exit_chooser = ExitChooser(
            scene,
            rng=self.random,
            floor_field=self.floor_field,
            config=self.movement.exit_choice,
            time_step_s=self.time_step_s,
            cell_size=self.cell_size,
        )
        self.panic_model = PanicModel(
            self.movement.panic,
            time_step_s=self.time_step_s,
            rng=self.random,
            relations=getattr(scene, "relations", None),
        )
        self.speed_model = SpeedModel(
            self.movement.speed,
            movement=self.movement,
            time_step_s=self.time_step_s,
            cell_size=self.cell_size,
            rng=self.random,
        )
        self.danger_model = DangerPerceptionModel(
            self.movement.danger, time_step_s=self.time_step_s, cell_size=self.cell_size
        )

        # 4. 拥堵模型（B09）：仅在关闭文献化移动规则时作为旧版概率等待使用
        self.congestion_model = CongestionModel(grid=self.grid, neighbor_radius=2, density_threshold=0.35)

        # 5. 烟雾模块
        self.smoke_engine = SmokeDiffusionModel(grid=self.grid, diffuse_coeff=0.24, decay_coeff=0.03)
        for src in scene.smoke_sources:
            self.smoke_engine.add_smoke_source(src)

        # 6. 风险感知模型
        self.risk_engine = SmokeRiskPerception(
            weight_conc=1.0,
            weight_delta=0.6,
            weight_vis=1.2,
            death_dose_threshold=12.0
        )

        # 7. 烟雾剂量记录
        self.dose_recorder = SmokeDoseRecorder(delta_t=0.5)
        self.dose_recorder.init_person_dose(list(self.person_map.values()))

        # 8. 规则内部状态初始化
        persons = list(self.person_map.values())
        self.panic_model.reset(persons)
        self.speed_model.reset(persons)
        self.danger_model.reset(persons)
        # pid -> 上一步是否被堵住（用于危险感知累积）
        self._blocked_last: dict[int, bool] = {}
        # pid -> 上一步是否遭遇"真实阻挡"（占位/抢格失败）；距离亏欠导致的停留不算
        self._obstructed_last: dict[int, bool] = {}
        self.movement_stats = {
            "enabled": self.handles_movement_speed,
            "blocked_total": 0,
            "congested_total": 0,
            "same_cell_congested_total": 0,
            "conflict_total": 0,
            "conflict_lost_total": 0,
            "switch_total": 0,
            "panic_count": 0,
            "urgent_count": 0,
            "mean_panic": 0.0,
            "max_panic": 0.0,
            "mean_impatience": 0.0,
            "max_impatience": 0.0,
            "mean_speed": 0.0,
            "max_speed": 0.0,
            "walk_total": 0,
            "frozen_total": 0,
            "unknown_behavior": self.unknown_behavior,
            "info_state_owner": self.info_state_owner,
        }

        # 统计变量
        self.total_persons = len(self.person_map)
        self.evacuated_count = 0
        self.step_log = []

        # 适配D可视化适配器新增属性
        self.smoke_matrix = self.smoke_engine.smoke_matrix
        self.smoke_sources = scene.smoke_sources
        self._source_distance = self._build_source_distance()
        self.exits = scene.exits
        self.alarm_points = getattr(scene, "alarm_points", [])

    def load_external_persons(self, persons):
        self.person_map.clear()
        for person in persons:
            self.person_map[person.id] = person

    def is_all_evacuated(self) -> bool:
        return all(getattr(p, "evacuated", False) or getattr(p, "is_dead", False) for p in self.person_map.values())

    def get_evacuated_count(self) -> int:
        return sum(1 for p in self.person_map.values() if getattr(p, "evacuated", False))

    def get_dead_count(self) -> int:
        return sum(1 for p in self.person_map.values() if getattr(p, "is_dead", False))

    # ------------------------------------------------------------------ 主循环
    def run_one_step(self, c_step_data: dict = None, signage_model=None):
        if c_step_data is None:
            c_step_data = {}

        # ===== 不改 schema：内部建坐标索引（只用一次，缓存在 grid 上） =====
        if not hasattr(self.grid, "_cell_index"):
            self.grid._cell_index = {(c.x, c.y): c for c in self.grid.cells}
        cell_index = self.grid._cell_index

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
        self.smoke_matrix = smoke_mat

        # 同步烟雾浓度到网格对象
        for cell in self.grid.cells:
            y, x = cell.y, cell.x
            if 0 <= y < self.height and 0 <= x < self.width:
                cell.smoke = smoke_mat[y][x]

        # ====================== B08 烟雾警报判断 ======================
        if not self.is_alarm_triggered:
            alarm_points = getattr(self.scene, "alarm_points", [])
            if alarm_points and len(alarm_points) > 0:
                for alarm in alarm_points:
                    ax, ay = int(alarm.x), int(alarm.y)
                    if 0 <= ax < self.width and 0 <= ay < self.height:
                        conc = smoke_mat[ay, ax]
                        if conc >= self.alarm_threshold:
                            self.is_alarm_triggered = True
                            print(f"[ALARM] 报警器触发！Step:{self.current_step}, 报警器坐标({ax},{ay}),浓度:{conc:.3f}")
                            break
            else:
                for src in self.scene.smoke_sources:
                    sx, sy = int(src.x), int(src.y)
                    if 0 <= sx < self.width and 0 <= sy < self.height:
                        conc_at_source = smoke_mat[sy, sx]
                        if conc_at_source >= self.alarm_threshold:
                            self.is_alarm_triggered = True
                            print(f"[ALARM] 烟源触发警报！Step:{self.current_step},烟源浓度:{conc_at_source:.3f}")
                            break
        # ==============================================================

        # 2. 批量计算行人风险
        risk_dict = self.risk_engine.batch_calc_all_risk(
            list(self.person_map.values()),
            smoke_mat,
            time_step_s=self.time_step_s
        )

        # 3. 更新烟雾累积剂量
        self.dose_recorder.update_all_dose(list(self.person_map.values()), smoke_mat)

        # ============= 自动知情逻辑（仅在 C 不提供信息状态时启用） =============
        c_provides_info = any(
            isinstance(behavior, dict) and ("info_state" in behavior)
            for behavior in c_step_data.values()
        )
        self.info_state_owner = "C" if c_provides_info else "B"
        base_alert_prob = 0.006
        smoke_alert_scale = 0.022
        dose_force_alert = 8.0

        if not c_provides_info:
            for pid, person in self.person_map.items():
                if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                    continue
                px, py = int(person.x), int(person.y)
                smoke_val = smoke_mat[py, px] if (0 <= py < self.height and 0 <= px < self.width) else 0
                person_dose = self.dose_recorder.get_dose(pid)

                if person_dose >= dose_force_alert:
                    person.info_state = "INFORMED"
                    continue

                if getattr(person, "info_state", "UNKNOWN") == "UNKNOWN":
                    if self.is_alarm_triggered:
                        alert_prob = base_alert_prob + smoke_val * smoke_alert_scale
                        if self.random.random() < alert_prob:
                            person.info_state = "INFORMED"
        # ========================================

        # 4. 标记占用坐标
        alive = [
            person
            for person in self.person_map.values()
            if not getattr(person, "evacuated", False) and not getattr(person, "is_dead", False)
        ]
        occupied_positions = {(int(p.x), int(p.y)) for p in alive}
        alive_person_pos = occupied_positions
        position_to_pid = {(int(p.x), int(p.y)): int(p.id) for p in alive}

        # 构造出口列表
        exit_list = []
        for e in self.exits:
            if isinstance(e, tuple):
                exit_list.append(e)
            elif hasattr(e, "id"):
                exit_list.append((e.id, e.x, e.y))

        # 5. 恐慌 / 危险感知 / 出口选择
        if self.handles_movement_speed:
            source_proximity = self._source_proximity(alive)
            self.panic_model.update(
                alive,
                smoke_mat,
                risk_dict,
                position_to_pid,
                dt=self.time_step_s,
                source_proximity=source_proximity,
            )
            self.danger_model.update(
                alive,
                risk_dict,
                self._blocked_last,
                dt=self.time_step_s,
                source_proximity=source_proximity,
            )
            forced_targets = {}
            for person in alive:
                behavior = c_step_data.get(int(person.id)) or {}
                forced = behavior.get("target_exit") if isinstance(behavior, dict) else None
                if forced:
                    forced_targets[int(person.id)] = str(forced)
            interval = max(1, int(self.movement.exit_choice.reevaluate_interval))
            if self.current_step == 0 or self.current_step % interval == 0 or forced_targets:
                chosen = self.exit_chooser.update_all(
                    alive, self.current_step, smoke_mat, forced_targets
                )
                for pid, eid in chosen.items():
                    person = self.person_map.get(pid)
                    if person is not None:
                        person.target_exit_id = eid
            for pid, eid in forced_targets.items():
                person = self.person_map.get(pid)
                if person is not None:
                    person.target_exit_id = eid

        # 6. 速度状态与移动决策
        next_positions = {}
        priorities = {}
        local_density = self._local_density(occupied_positions)
        position_speed = {
            (int(p.x), int(p.y)): float(getattr(p, "speed_current", getattr(p, "speed", 1.0)) or 0.0)
            for p in alive
        }
        ca_weights = dict(self.scene.parameters.get("ca_weights", {}) or {})
        ca_weights.setdefault("w_lateral", self.movement.danger.lateral_bonus)
        for person in alive:
            pid = int(person.id)
            single_behavior = c_step_data.get(pid, {}) or {}
            behavior_target = single_behavior.get("target_exit") if isinstance(single_behavior, dict) else None
            if behavior_target:
                person.target_exit_id = str(behavior_target)

            if not self.handles_movement_speed:
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
                    person_map=self.person_map,
                    weights=ca_weights,
                    use_softmax=True,
                    softmax_lambda=0.5,
                    max_steps=1,
                )
                next_positions[pid] = (nx, ny)
                person.can_move = (nx, ny) != (int(person.x), int(person.y))
                continue

            px, py = int(person.x), int(person.y)
            person_dose = self.dose_recorder.get_dose(pid)
            # 未知情 / 群体等待中的人不参与疏散移动，也不积累速度距离
            if not self._is_evacuating(person, single_behavior):
                next_positions[pid] = (px, py)
                person.can_move = False
                self.speed_model.freeze(person)
                person.speed_current = 0.0
                continue
            smoke_now = float(smoke_mat[py, px]) if (0 <= py < self.height and 0 <= px < self.width) else 0.0
            ahead = self._free_distance_ahead(person, occupied_positions, position_speed)
            free_distance, leader_speed = ahead if ahead is not None else (None, None)
            speed_state = self.speed_model.update(
                person,
                smoke_concentration=smoke_now,
                dose=person_dose,
                local_density=local_density.get((px, py), 1),
                blocked_last_step=self._obstructed_last.get(pid, False),
                dt=self.time_step_s,
                free_distance_m=free_distance,
                leader_speed=leader_speed,
            )
            person.speed_current = float(speed_state["velocity"])
            priorities[pid] = (
                conflict_priority(person, self.movement.conflict)
                + self.movement.danger.priority_gain * float(getattr(person, "danger_pressure", 0.0) or 0.0)
            )

            if not speed_state["allow_move"]:
                next_positions[pid] = (px, py)
                person.can_move = False
                continue

            # B 已判定该行人本步参与疏散（含 unknown_behavior="evacuate"），
            # 显式告诉 calc_next_position，避免它再按 info_state 把人拦下。
            calc_behavior = dict(single_behavior) if isinstance(single_behavior, dict) else {}
            calc_behavior["is_informed"] = True

            nx, ny = calc_next_position(
                person,
                self.grid,
                smoke_matrix=smoke_mat,
                risk_dict=risk_dict,
                single_behavior=calc_behavior,
                floor_field=self.floor_field,
                signage_model=signage_model,
                occupied_positions=occupied_positions,
                exit_list=exit_list,
                exit_chooser=self.exit_chooser,
                # 拥堵等待只保留一套：B 的确定性速度门控接管后不再调用概率等待
                congestion_model=None if self.handles_movement_speed else self.congestion_model,
                alive_person_pos=alive_person_pos,
                rng=self.random,
                person_map=self.person_map,
                weights=ca_weights,
                use_softmax=True,
                softmax_lambda=0.5,
                max_steps=int(speed_state["cells"]),
            )
            next_positions[pid] = (nx, ny)
            person.can_move = (nx, ny) != (px, py)

        # 7. 冲突消解：速度 + 恐慌值（+危险压力）最高者获胜
        target_counts = {}
        for pos in next_positions.values():
            target_counts[pos] = target_counts.get(pos, 0) + 1
        conflicts = sum(count - 1 for count in target_counts.values() if count > 1)
        fixed_next_pos = resolve_conflict(
            next_positions, self.person_map, self.random,
            priorities=priorities,
            config=self.movement.conflict,
        )

        # 8. 更新坐标 & 判断是否撤离
        blocked_now = 0
        lost_conflicts = 0
        for pid, (nx, ny) in fixed_next_pos.items():
            person = self.person_map[pid]
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            px, py = int(person.x), int(person.y)
            wanted = next_positions.get(pid, (px, py))
            if (nx, ny) == (px, py) and wanted != (px, py):
                lost_conflicts += 1

            person.prev_x = person.x
            person.prev_y = person.y
            person.x = nx
            person.y = ny

            moved = max(abs(int(nx) - px), abs(int(ny) - py))
            real_block = moved <= 0 and wanted != (px, py)
            self._obstructed_last[pid] = bool(real_block)
            if moved <= 0:
                blocked_now += 1
                self._blocked_last[pid] = True
                if self.handles_movement_speed and real_block:
                    self.speed_model.on_conflict_lost(person)
                    person.speed_current = 0.0
            else:
                self._blocked_last[pid] = False
                if self.handles_movement_speed:
                    self.speed_model.consume(person, moved)

            cell = cell_index.get((int(nx), int(ny)))     # ← 用字典，不用 get_cell
            if cell and cell.cell_type == CellType.EXIT:
                person.evacuated = True
                person.evac_step = self.current_step
                px = int(nx)
                py = int(ny)
                for e in self.exits:
                    if isinstance(e, tuple):
                        eid, ex, ey = e
                    else:
                        ex, ey, eid = e.x, e.y, e.id
                    if ex == px and ey == py:
                        person.actual_exit = eid
                        break

        # 9. 未获知人员的日常行走（walk）：由 B 统一处理，避免 C 与 B 各走一遍
        if self.handles_movement_speed and self.unknown_behavior == "walk":
            self._daily_walk(alive)
        elif self.handles_movement_speed and self.unknown_behavior == "freeze":
            self._frozen_total += sum(
                1 for p in alive if str(getattr(p, "info_state", "UNKNOWN")).upper() == "UNKNOWN"
            )

        # 10. 更新统计
        self.evacuated_count = self.get_evacuated_count()
        dead_count = self.get_dead_count()
        self.current_step += 1

        panic_count = sum(1 for p in alive if getattr(p, "is_panicked", False))
        urgent_count = sum(1 for p in alive if getattr(p, "danger_urgent", False))
        speeds = [float(getattr(p, "speed_current", 0.0) or 0.0) for p in alive]
        panics = [float(getattr(p, "panic_level", 0.0) or 0.0) for p in alive]
        impatience = [float(getattr(p, "impatience", 0.0) or 0.0) for p in alive]
        self._congested_total += sum(
            1 for p in alive if local_density.get((int(p.x), int(p.y)), 1) > 1
        )
        self._same_cell_total += sum(
            max(0, count - 1) for count in target_counts.values() if count > 1
        ) if target_counts else 0
        self.movement_stats = {
            "enabled": self.handles_movement_speed,
            "blocked_total": int(self.movement_stats.get("blocked_total", 0)) + blocked_now,
            "congested_total": int(self._congested_total),
            "same_cell_congested_total": int(self._same_cell_total),
            "conflict_total": int(self.movement_stats.get("conflict_total", 0)) + conflicts,
            "conflict_lost_total": int(self.movement_stats.get("conflict_lost_total", 0)) + lost_conflicts,
            "switch_total": int(getattr(self.exit_chooser, "switch_total", 0)),
            "panic_count": panic_count,
            "urgent_count": urgent_count,
            "mean_panic": (sum(panics) / len(panics)) if panics else 0.0,
            "max_panic": max(panics) if panics else 0.0,
            "mean_impatience": (sum(impatience) / len(impatience)) if impatience else 0.0,
            "max_impatience": max(impatience) if impatience else 0.0,
            "mean_speed": (sum(speeds) / len(speeds)) if speeds else 0.0,
            "max_speed": max(speeds) if speeds else 0.0,
            "blocked_now": blocked_now,
            "walk_total": int(self._walk_total),
            "frozen_total": int(self._frozen_total),
            "unknown_behavior": self.unknown_behavior,
            "info_state_owner": self.info_state_owner,
        }

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
            "alarm": self.is_alarm_triggered
        }

    # ------------------------------------------------------------------ 辅助
    def _is_evacuating(self, person, single_behavior) -> bool:
        """本步是否应参与疏散移动：已获知险情、且不在群体等待。

        未获知者的行为由 B 的 unknown_behavior 统一决定：
        - walk / freeze：不参与疏散路径，交由 B 的日常行走逻辑；
        - evacuate：按疏散处理（等价于已获知）。
        """
        if isinstance(single_behavior, dict) and single_behavior.get("is_waiting", False):
            return False
        info_state = None
        if isinstance(single_behavior, dict):
            info_state = single_behavior.get("info_state")
        if info_state is None:
            info_state = getattr(person, "info_state", "UNKNOWN")
        informed = str(getattr(info_state, "value", info_state)).upper() != "UNKNOWN"
        if informed:
            return True
        return str(getattr(self, "unknown_behavior", "walk")).lower() == "evacuate"

    def _daily_walk(self, alive: list) -> None:
        """未获知险情者的日常行走（walk）：按自身速度随机游走，不参与疏散、不进出口。"""
        if not alive:
            return
        cell_index = getattr(self.grid, "_cell_index", None) or {}
        occupied = {
            (int(p.x), int(p.y))
            for p in self.person_map.values()
            if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)
        }
        walkers = [
            p for p in alive
            if str(getattr(p, "info_state", "UNKNOWN")).upper() == "UNKNOWN"
        ]
        walkers.sort(key=lambda p: -float(getattr(p, "speed", 1.0) or 1.0))
        for person in walkers:
            pid = int(person.id)
            speed = max(0.1, float(getattr(person, "speed", 1.0) or 1.0)) * self.unknown_speed_factor
            credit = self._walk_credit.get(pid, 0.0) + speed * self.time_step_s / self.cell_size
            if credit < 1.0:
                self._walk_credit[pid] = credit
                continue
            px, py = int(person.x), int(person.y)
            last = self._walk_dir.get(pid)
            candidates = []
            for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                nx, ny = px + dx, py + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                cell = cell_index.get((nx, ny))
                if cell is None or str(getattr(cell.cell_type, "value", cell.cell_type)).lower() != "free":
                    continue
                if (nx, ny) in occupied:
                    continue
                candidates.append((dx, dy, nx, ny))
            if not candidates:
                continue
            chosen = None
            if last is not None:
                for cand in candidates:
                    if (cand[0], cand[1]) == last:
                        chosen = cand
                        break
            if chosen is None:
                chosen = self.random.choice(candidates)
            dx, dy, nx, ny = chosen
            occupied.discard((px, py))
            person.prev_x, person.prev_y = px, py
            person.x, person.y = nx, ny
            occupied.add((nx, ny))
            self._walk_dir[pid] = (dx, dy)
            self._walk_credit[pid] = credit - 1.0
            self._walk_total += 1

    def _build_source_distance(self):
        """多源 BFS：每个元胞到最近烟源的距离（元胞），用于烟源危险感知。"""
        if not self.smoke_sources:
            return None
        from collections import deque

        cell_index = getattr(self.grid, "_cell_index", None)
        if cell_index is None:
            cell_index = {(cell.x, cell.y): cell for cell in self.grid.cells}
        distance = np.full((self.height, self.width), np.inf, dtype=np.float32)
        queue = deque()
        for source in self.smoke_sources:
            sx, sy = int(source.x), int(source.y)
            if 0 <= sx < self.width and 0 <= sy < self.height:
                distance[sy, sx] = 0.0
                queue.append((sx, sy))
        while queue:
            x, y = queue.popleft()
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                cell = cell_index.get((nx, ny))
                if cell is None:
                    continue
                kind = str(getattr(cell.cell_type, "value", cell.cell_type)).lower()
                if kind in {"wall", "obstacle"}:
                    continue
                if distance[ny, nx] > distance[y, x] + 1:
                    distance[ny, nx] = distance[y, x] + 1
                    queue.append((nx, ny))
        return distance

    def _source_proximity(self, persons):
        """到烟源的高斯邻近度：烟源格=1，按 exp(-d^2/(2σ^2)) 衰减。"""
        if self._source_distance is None:
            return None
        sigma = max(0.1, float(getattr(self.movement, "source_sigma_m", 1.5)))
        scale = self.cell_size
        proximity = {}
        for person in persons:
            x, y = int(person.x), int(person.y)
            distance = float(self._source_distance[y, x])
            if not np.isfinite(distance):
                proximity[int(person.id)] = 0.0
            else:
                metres = distance * scale
                proximity[int(person.id)] = float(np.exp(-(metres * metres) / (2.0 * sigma * sigma)))
        return proximity

    def _local_density(self, occupied_positions: set[tuple[int, int]]) -> dict[tuple[int, int], int]:
        density: dict[tuple[int, int], int] = {}
        for (x, y) in occupied_positions:
            count = 0
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if (x + dx, y + dy) in occupied_positions:
                        count += 1
            density[(x, y)] = max(1, count)
        return density

    def _free_distance_ahead(
        self,
        person,
        occupied_positions: set[tuple[int, int]],
        position_speed: dict[tuple[int, int], float],
    ) -> tuple[float, float] | None:
        """Xiong 2025 Rule 2：沿行进方向找最近障碍/慢行者，返回 (距离 m, 对方速度)。

        方向优先用上一步的位移；站着不动时用目标出口距离场的下坡方向。
        找不到方向或前方在 lookahead 内没有阻挡时返回 None（不额外限速）。
        """
        cfg = self.movement.speed
        if not cfg.use_deceleration_distance:
            return None
        px, py = int(person.x), int(person.y)
        prev_x = int(getattr(person, "prev_x", px))
        prev_y = int(getattr(person, "prev_y", py))
        dx = (px > prev_x) - (px < prev_x)
        dy = (py > prev_y) - (py < prev_y)
        if dx == 0 and dy == 0:
            exit_id = getattr(person, "target_exit_id", None)
            if not exit_id:
                return None
            left = self.floor_field.distance_to(exit_id, px - 1, py)
            right = self.floor_field.distance_to(exit_id, px + 1, py)
            up = self.floor_field.distance_to(exit_id, px, py - 1)
            down = self.floor_field.distance_to(exit_id, px, py + 1)
            if not all(np.isfinite(v) for v in (left, right, up, down)):
                return None
            gradient_x = right - left
            gradient_y = down - up
            dx = -1 if gradient_x > 0 else (1 if gradient_x < 0 else 0)
            dy = -1 if gradient_y > 0 else (1 if gradient_y < 0 else 0)
        if dx == 0 and dy == 0:
            return None

        cell_size = max(1e-6, float(self.cell_size))
        max_cells = max(1, int(np.ceil(float(cfg.lookahead_m) / cell_size)))
        cell_index = getattr(self.grid, "_cell_index", None) or {}
        for step in range(1, max_cells + 1):
            nx, ny = px + dx * step, py + dy * step
            if not (0 <= nx < self.width and 0 <= ny < self.height):
                return (step - 0.5) * cell_size, 0.0
            cell = cell_index.get((nx, ny))
            kind = str(getattr(cell.cell_type, "value", cell.cell_type)).lower() if cell else "wall"
            if kind in {"wall", "obstacle"}:
                return (step - 0.5) * cell_size, 0.0
            if (nx, ny) in occupied_positions:
                return step * cell_size, float(position_speed.get((nx, ny), 0.0))
        return None

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
