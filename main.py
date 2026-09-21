""" 主程序入口 - A+B+C+D 完整联调版本 功能：     1. A 模块加载地图     2. C 模块生成人群和社会关系     3. C 模块为行人按所选地图分配位置     4. C 行为引擎（结伴/从众/信息/引导/指示牌/错误信息）逐帧输出 c_step_data     5. B 模块 CA 仿真     6. D 模块记录 CSV 日志     7. 【新增】实时可视化渲染（可开关，不影响原有实验逻辑）  命令行（便于"开/关关系模型"与">=2 种引导策略"对比实验）：     python main.py --map maps/edited_map.json                     # 默认：关系模型开启 + 可视化开启     python main.py --social off --visual off                       # 基线：B纯CA，关闭可视化用于批量跑实验     python main.py --guide fixed / --guide patrol / --guide toward_exit ...     python main.py --misinfo off                                   # 关闭错误出口信息     python main.py --info off                                      # 关闭广播/局部口头传播     python main.py --signage off                                   # 关闭静态指示牌 """
import argparse
import random
import sys
import time

import numpy as np
from pathlib import Path
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# ====================== 迭代1测试总开关 ======================
ENABLE_ITER1_TEST = True    # True=开启is_waiting强制测试；测试完成改为False
TEST_WAIT_PID = 3          # 填写你场景真实存在的行人ID
# ============================================================

# D可视化接口
from visualization.runtime_entry import DVisualizationEntry

# A模块基础地图
from core.schema import Exit, ScenarioConfig, SmokeSource
from map_import.map_loader_grid import load_grid
from control.scene_config import SceneConfigGenerator, generate_population, resolve_map_file

# 全套C模块行为引擎
from social.social_graph import SocialGraphBuilder
from social.information_state import InformationStateEngine
from social.information_diffusion import InformationDiffusionEngine
from social.group_behavior import GroupBehaviorEngine
from social.herding_model import HerdingModel
from social.guide_agent import GuideAgentModel, GuideMoveStrategy
from control.signage_model import SignageModel

# 仿真引擎 + A人员位置加载器
from simulation.evac_simulation import EvacEngine
from simulation.ca_loader import CASimulationLoader

# json文件路径（C人员属性配置，使用绝对路径避免工作目录不同导致找不到）
PROFILE_JSON = str(project_root / "social" / "person_profiles.json")

# C09 引导员可选策略（至少两种可配置可对比）
GUIDE_STRATEGIES = {
    "none": None,
    "fixed": GuideMoveStrategy.FIXED,
    "patrol": GuideMoveStrategy.PATROL,
    "toward_exit": GuideMoveStrategy.TOWARD_EXIT,
    "toward_crowd": GuideMoveStrategy.TOWARD_CROWD,
    "escort": GuideMoveStrategy.ESCORT,
}


# ---------------------- 场景加载函数 ----------------------
def _nearest_free_cell(grid, x, y):
    """返回距离 (x, y) 最近的可通行 free 元胞；找不到时返回限制在地图内的原坐标。"""
    width = grid.width
    height = grid.height
    cx = max(0, min(int(x), width - 1))
    cy = max(0, min(int(y), height - 1))

    def is_free(px, py):
        cell_type = str(getattr(grid.cells[py * width + px].cell_type, "value", ""))
        return cell_type.lower() == "free"

    if is_free(cx, cy):
        return cx, cy
    for radius in range(1, max(width, height) + 1):
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                nx = cx + dx
                ny = cy + dy
                if 0 <= nx < width and 0 <= ny < height and is_free(nx, ny):
                    return nx, ny
    return cx, cy


def _deploy_point(grid, exits, exit_id, step=2):
    """返回出口向内偏移 step 格后最近的 free 点（用于部署引导员/指示牌）。"""
    ex = ey = None
    for eid, x, y in exits:
        if eid == exit_id:
            ex, ey = x, y
            break
    if ex is None:
        return None
    dx = -1 if ex >= grid.width // 2 else 1
    dy = -1 if ey >= grid.height // 2 else 1
    px = max(0, min(int(ex) + dx * step, grid.width - 1))
    py = max(0, min(int(ey) + dy * step, grid.height - 1))
    return _nearest_free_cell(grid, px, py)


def _nearest_exit(exits, x, y):
    best = None
    best_d = float("inf")
    for eid, ex, ey in exits:
        d = (ex - x) ** 2 + (ey - y) ** 2
        if d < best_d:
            best_d = d
            best = eid
    return best


def _far_exit(exits, x, y):
    """返回距离 (x, y) 最远的出口（用作错误出口信息的误导目标）。"""
    best = None
    best_d = -1.0
    for eid, ex, ey in exits:
        d = (ex - x) ** 2 + (ey - y) ** 2
        if d > best_d:
            best_d = d
            best = eid
    return best


def _build_patrol_points(grid, step=2):
    """在可通行区域内生成蛇形巡查路线（供引导员未发生火灾时流动巡查）。"""
    step = max(1, int(step))
    points = []
    row_index = 0
    for y in range(1, grid.height - 1, step):
        xs = list(range(1, grid.width - 1, step))
        if row_index % 2 == 1:
            xs.reverse()
        for x in xs:
            cell = grid.cells[y * grid.width + x]
            if str(getattr(cell.cell_type, "value", "")).lower() == "free":
                points.append((int(x), int(y)))
        row_index += 1
    return points


def load_A_scene(map_path) -> ScenarioConfig:
    grid_path = Path(map_path)
    if not grid_path.is_file():
        raise SystemExit(f"[ERROR] 地图文件不存在: {grid_path}")
    grid = load_grid(str(grid_path))
    print(f"地图加载完成：宽{grid.width} × 高{grid.height}")

    # 出口：优先使用地图自身的 exit 元胞（B 的 ExitChooser 会按网格出口顺序配对）
    exit_cells = [
        (cell.x, cell.y)
        for cell in grid.cells
        if str(getattr(cell.cell_type, "value", cell.cell_type)).lower() == "exit"
    ]
    fallback_exits = [("exit_01", 12, 10), ("exit_02", 42, 60)]
    if exit_cells:
        exit_entries = [
            (f"exit_{i + 1:02d}", int(x), int(y))
            for i, (x, y) in enumerate(exit_cells)
        ]
    else:
        exit_entries = [
            (f"exit_{i + 1:02d}", *_nearest_free_cell(grid, ex, ey))
            for i, (_, ex, ey) in enumerate(fallback_exits)
        ]

    exits_tuple_list = list(exit_entries)
    # 【BUG修复】不再把元组列表挂载到grid.exits，防止污染
    smoke_x, smoke_y = _nearest_free_cell(grid, 42, 90)

    scene = ScenarioConfig(
        scenario_id="classroom",
        grid=grid,
        persons=[],
        exits=[Exit(id=eid, x=x, y=y) for eid, x, y in exit_entries],
        smoke_sources=[SmokeSource(x=smoke_x, y=smoke_y, intensity=10)]
    )
    return scene


# ---------------------- 可视化渲染函数【新增】 ----------------------
class SimVisualizer:
    def __init__(self, grid_width, grid_height, enable=True):
        self.enable = enable
        if not self.enable:
            self.fig = None
            self.ax = None
            return
        plt.rcParams['font.sans-serif'] = ['SimHei']
        self.fig, self.ax = plt.subplots(figsize=(10, 8))
        self.w = grid_width
        self.h = grid_height
        self.ax.set_xlim(-1, self.w)
        self.ax.set_ylim(-1, self.h)
        self.ax.set_aspect("equal")
        self.ax.invert_yaxis()
        self.ax.set_title("疏散仿真实时可视化")
        self.ax.set_xlabel("X")
        self.ax.set_ylabel("Y")
        plt.tight_layout()

    def update(self, sim, exit_list, guide_engine, frame):
        if not self.enable:
            return
        self.ax.clear()
        self.ax.set_xlim(-1, self.w)
        self.ax.set_ylim(-1, self.h)
        self.ax.invert_yaxis()
        self.ax.set_title(f"疏散仿真 | 帧:{frame} | 已撤离:{sim.evacuated_count}/{sim.total_persons}")

        # 绘制烟雾
        smoke_mat = sim.smoke_matrix
        if smoke_mat is not None:
            smoke_np = np.array(smoke_mat)
            im = self.ax.imshow(smoke_np, cmap="gray_r", vmin=0, vmax=10, alpha=0.4, origin="lower")

        # 绘制出口（红色方块）
        for eid, ex, ey in exit_list:
            rect = Rectangle((ex - 0.4, ey - 0.4), 0.8, 0.8, color="red", alpha=0.7)
            self.ax.add_patch(rect)
            self.ax.text(ex + 0.3, ey, eid, color="red", fontsize=8)

        # 绘制行人
        for p in sim.person_map.values():
            if p.evacuated:
                continue
            if getattr(p, "is_dead", False):
                color = "black"
            else:
                info_state = getattr(p, "info_state", "UNKNOWN")
                if info_state == "MISINFORMED":
                    color = "orange"
                elif info_state == "GUIDED":
                    color = "blue"
                elif info_state == "INFORMED":
                    color = "green"
                else:
                    color = "deepskyblue"
            circ = Circle((p.x, p.y), 0.3, color=color)
            self.ax.add_patch(circ)

        # ==========【修复这里】guide_engine.guides 是 list，不再用 .items() ==========
        # 绘制引导员（紫色）
        if guide_engine is not None:
            for g in guide_engine.guides:
                circ = Circle((g.x, g.y), 0.4, color="magenta")
                self.ax.add_patch(circ)
                self.ax.text(g.x + 0.3, g.y, "G", color="magenta", fontweight="bold")

        plt.pause(0.01)

    def close(self):
        if self.enable and self.fig is not None:
            plt.close(self.fig)


# ---------------------- 主仿真入口 ----------------------
def main(options=None):
    options = options or {}
    yaml_path = project_root / "control" / "config_template.yaml"
    scene_cfg = SceneConfigGenerator.load_config_from_yaml(str(yaml_path))
    selected_map = options.get("map")
    map_path = Path(resolve_map_file(str(yaml_path), explicit_map=selected_map))
    print(f"[C11] 本次运行使用地图: {map_path}")
    if not map_path.is_file():
        raise SystemExit(f"[ERROR] 地图文件不存在: {map_path}")

    # 实验开关
    social_on = bool(options.get("social", True))
    info_on = bool(options.get("info", True))
    misinfo_on = bool(options.get("misinfo", True))
    signage_on = bool(options.get("signage", True))
    guide_key = str(options.get("guide", "patrol")).lower()
    guide_strategy = GUIDE_STRATEGIES.get(guide_key, GuideMoveStrategy.PATROL)
    max_frame = int(options.get("max_frames", 600))
    unique_run_id = options.get("run_id") or f"exp_classroom_smoke_{int(time.time())}"
    visual_on = bool(options.get("visual", True)) # 【新增可视化开关】

    # 信息延迟 / 警报 / 引导 / 速度模型参数（命令行优先，其次 YAML，最后默认值）
    ratio_opt = options.get("initial_informed_ratio")
    alarm_opt = options.get("alarm")
    threshold_opt = options.get("alarm_smoke_threshold")
    initial_informed_ratio = float(
        ratio_opt if ratio_opt is not None else getattr(scene_cfg, "initial_informed_ratio", 0.15)
    )
    alarm_on = bool(
        alarm_opt if alarm_opt is not None else getattr(scene_cfg, "alarm_enabled", True)
    )
    alarm_threshold = float(
        threshold_opt if threshold_opt is not None else getattr(scene_cfg, "alarm_smoke_threshold", 3.0)
    )
    guide_exit_id = options.get("guide_exit")
    guide_share = float(options.get("guide_share", 0.30))
    speed_model_on = bool(options.get("speed_model", True))
    congestion_radius = int(options.get("congestion_radius", 2))
    congestion_threshold = int(options.get("congestion_threshold", 4))
    patrol_step = int(options.get("patrol_step", 2))

    print("===== C 行为实验开关 =====")
    print(f"  social={social_on} info={info_on} misinfo={misinfo_on} "
          f"signage={signage_on} guide={guide_key} max_frames={max_frame} run_id={unique_run_id}")
    print(f"  初始知情比例={initial_informed_ratio} 警报={alarm_on}(阈值{alarm_threshold}) "
          f"引导比例={guide_share} 引导出口={guide_exit_id} 速度模型={speed_model_on}")
    print(f"  实时可视化：{'开启' if visual_on else '关闭'}")

    # 1. 初始化地图
    ca_scene = load_A_scene(map_path)
    grid_w = ca_scene.grid.width
    grid_h = ca_scene.grid.height
    max_valid_x = grid_w - 1
    max_valid_y = grid_h - 1
    map_center_x = max_valid_x / 2
    map_center_y = max_valid_y / 2
    print(f"坐标合法范围：X[0,{max_valid_x}] Y[0,{max_valid_y}]")

    # 出口识别（转为元组列表给C模块使用）
    exit_check_list = [(e.id, e.x, e.y) for e in ca_scene.exits]
    print("===== 场景出口列表 =====")
    for eid, ex, ey in exit_check_list:
        print(f"原生出口 {eid}: X={ex}, Y={ey}")
    print(f"出口总数：{len(exit_check_list)}")

    # ========== 按 config_template.yaml + 所选地图重新生成人群与位置文件 ==========
    people_output = project_root / "output_people.json"
    pos_output = project_root / "control" / "output_people_position.json"
    print("\n[C11] 依据 config_template.yaml 与所选地图重新生成人群与位置...")
    generate_population(
        yaml_file=str(yaml_path),
        people_output=str(people_output),
        map_file=str(map_path),
        position_output=str(pos_output),
    )
    print("[C11] 人群与位置文件已更新，继续加载 A 模块行人\n")

    # 2. 加载A模块行人点位
    loader = CASimulationLoader()
    loader.init_ca_model()
    external_person_list = loader.agent_list
    print(f"A模块原始行人总数：{len(external_person_list)}")

    # =========【修复】动态补齐缺失属性：is_dead / dose，解决属性缺失崩溃 =========
    for ped in external_person_list:
        if not hasattr(ped, "is_dead"):
            ped.is_dead = False
        if not hasattr(ped, "dose"):
            ped.dose = 0.0

    # 3. 越界行人修复
    fix_count = 0
    reset_count = 0
    for ped in external_person_list:
        ox = ped.x
        oy = ped.y
        if not hasattr(ped, "dose"):
            ped.dose = 0.0
        if not hasattr(ped, "risk"):
            ped.risk = 0.0
        if ox < -5 or ox > max_valid_x * 2 or oy < -5 or oy > max_valid_y * 2:
            ped.x = map_center_x
            ped.y = map_center_y
            reset_count += 1
        else:
            new_x = max(0.0, min(float(ox), float(max_valid_x)))
            new_y = max(0.0, min(float(oy), float(max_valid_y)))
            if abs(new_x - ox) > 0.01 or abs(new_y - oy) > 0.01:
                ped.x = new_x
                ped.y = new_y
                fix_count += 1
    if reset_count > 0:
        print(f"⚠️ 重度越界重置行人：{reset_count}人")
    if fix_count > 0:
        print(f"✅ 边缘裁剪修正行人：{fix_count}人")
    if reset_count == 0 and fix_count == 0:
        print("✅ 所有行人坐标正常，无越界修正")

    # 4. 固定随机种子（保证开/关对比可复现）；scene_cfg 已在函数开头加载
    seed_value = getattr(scene_cfg, "random_seed", None)
    if seed_value is not None:
        random.seed(seed_value)
        np.random.seed(seed_value)

    # 静态疏散指示牌：按出口与地图自动部署（不写死坐标）
    signage_engine = None
    if signage_on:
        signage_engine = SignageModel(exit_check_list)
        for eid, ex, ey in exit_check_list:
            pt = _deploy_point(ca_scene.grid, exit_check_list, eid, step=2)
            if pt is not None:
                signage_engine.add_static_signage(x=pt[0], y=pt[1], target_exit=eid)
        center_pt = _nearest_free_cell(ca_scene.grid, grid_w // 2, grid_h // 2)
        center_target = _nearest_exit(exit_check_list, center_pt[0], center_pt[1])
        if center_target is not None:
            signage_engine.add_static_signage(x=center_pt[0], y=center_pt[1], target_exit=center_target)
        print(f"✅ C08 静态指示牌按地图自动部署完成（{len(signage_engine.signages)} 块）")

    # C 行为引擎（关系模型关闭时全部不建，走 B 纯 CA）
    info_state_engine = info_diff_engine = herd_engine = group_engine = guide_engine = None
    false_exit_id = None
    if social_on:
        builder = SocialGraphBuilder.from_config(scene_cfg, profiles_json_path=PROFILE_JSON)
        social_graph, person_attr_map = builder.build_with_config()
        print("✅ C03 社交关系图谱构建完成（朋友/同学/家庭/强关系，与 output_people.json 同源）")

        valid_ped = []
        for ped in external_person_list:
            attr = person_attr_map.get(ped.id)
            if attr is None:
                continue
            ped.group_id = attr.group_id
            ped.herding_tendency = attr.herding_tendency
            ped.risk_sensitivity = attr.risk_sensitivity
            ped.info_state = attr.info_state
            ped.target_exit = attr.target_exit
            valid_ped.append(ped)
        ca_scene.persons = valid_ped
        print(f"✅ 行为属性绑定完成，有效行人数量：{len(ca_scene.persons)}")

        # C06 信息状态 + C07 信息传播（广播 / 局部口头 / 关系传播 / 错误信息）
        info_state_engine = InformationStateEngine(builder)
        info_diff_engine = InformationDiffusionEngine(builder, info_state_engine, grid_w, grid_h)
        info_diff_engine.broadcast_params["enabled"] = False
        info_diff_engine.wom_params["enabled"] = info_on
        info_diff_engine.rel_params["enabled"] = info_on
        info_diff_engine.misinfo_params["enabled"] = bool(info_on and misinfo_on)
        if not info_diff_engine.misinfo_params["enabled"]:
            info_diff_engine.misinfo_active = False
        print(f"✅ C06/C07 信息引擎就绪（广播={info_on} 局部传播={info_on} 错误信息={info_on and misinfo_on}）")

        # C05 从众（对视野内陌生人同样生效）
        herd_engine = HerdingModel(builder, info_state_engine, exit_check_list)
        # C04 结伴（强关系等待/跟随）
        group_engine = GroupBehaviorEngine(builder)

        # C09 引导员：按地图出口自动部署；未发生火灾时流动巡查，报警后带人前往指定出口
        guide_engine = GuideAgentModel(builder, info_state_engine, grid_w, grid_h, exit_check_list)
        patrol_points = _build_patrol_points(ca_scene.grid, step=patrol_step)
        if guide_strategy is not None:
            for index, (eid, ex, ey) in enumerate(exit_check_list):
                pt = _deploy_point(ca_scene.grid, exit_check_list, eid, step=1)
                if pt is None:
                    continue
                if guide_strategy == GuideMoveStrategy.PATROL and patrol_points:
                    route = patrol_points[index::max(1, len(exit_check_list))] or patrol_points
                    agent_id = guide_engine.add_guide_with_patrol(
                        x=pt[0], y=pt[1], patrol_points=route,
                        profile="security" if eid == exit_check_list[0][0] else "staff")
                    guide_engine.guides[agent_id].strategy = GuideMoveStrategy.PATROL
                else:
                    guide_engine.add_guide(x=pt[0], y=pt[1], profile="staff",
                                           move_strategy=guide_strategy)
            print(f"✅ C09 引导员按地图自动部署完成（策略={guide_key}，"
                  f"{len(guide_engine.guides)} 名，巡查点={len(patrol_points)}）")

        # 选定"较远的出口"作为引导目标
        if guide_exit_id:
            valid_ids = {eid for eid, _, _ in exit_check_list}
            if guide_exit_id not in valid_ids:
                raise SystemExit(f"[ERROR] --guide-exit {guide_exit_id} 不在出口列表中: {sorted(valid_ids)}")
        else:
            guide_exit_id = _far_exit(exit_check_list, grid_w / 2.0, grid_h / 2.0)

        # 只引导"部分人群"
        all_ids = [p.id for p in external_person_list]
        guide_count = max(1, int(round(len(all_ids) * max(0.0, min(1.0, guide_share))))) if all_ids else 0
        guided_ids = set(random.sample(all_ids, guide_count)) if guide_count else set()
        if signage_engine is not None:
            signage_engine.set_guided_exit(guide_exit_id, guided_ids)
        print(f"✅ 引导目标出口={guide_exit_id}（较远出口），计划引导 {len(guided_ids)}/{len(all_ids)} 人")

        # 错误出口信息
        if misinfo_on and len(exit_check_list) >= 2:
            false_exit_id = options.get("misinfo_exit") or _far_exit(
                exit_check_list, grid_w / 2.0, grid_h / 2.0)
            inject = info_diff_engine.misinfo_params.get("inject", {})
            if isinstance(inject, dict):
                inject["message"] = f"Exit {false_exit_id} is the only safe exit! Head there now!"
            if signage_engine is not None:
                signage_engine.set_misleading_exit(false_exit_id)
            print(f"✅ 错误出口信息：宣称安全出口={false_exit_id}（误导行人绕远/拥堵）")
    else:
        ca_scene.persons = external_person_list
        print("✅ 关系模型已关闭（--social off）：运行 B 纯 CA 基线，无 C 行为输入")

    # 5. 启动仿真器与 D 日志
    sim = EvacEngine(scene=ca_scene)
    print(f"本次实验run_id：{unique_run_id}")

    # 火灾初期：让一定比例的人员先知道险情
    if social_on and info_on and initial_informed_ratio > 0:
        sim_persons = list(sim.person_map.values())
        seeded = info_diff_engine.initialize_initial_informed(
            sim_persons, current_step=0, ratio=initial_informed_ratio)
        print(f"✅ 初始知情人员：{seeded}/{len(sim_persons)}"
              f"（比例 {initial_informed_ratio}），其余靠局部口头/关系传播获知")

    # 初始化可视化【新增】
    visualizer = SimVisualizer(grid_w, grid_h, enable=visual_on)

    d_view = DVisualizationEntry(
        simulation=sim,
        output_root="outputs/experiments",
        run_id=unique_run_id,
        time_step_s=0.5,
    )
    try:
        d_view.start()
        print("✅ D CSV 日志已启动")

        # 速度差异 + 拥堵减速
        move_credit = {}
        speed_stats = {"blocked_total": 0, "congested_total": 0}
        _speeds = [float(getattr(p, "speed", 1.0) or 1.0) for p in sim.person_map.values()]
        mean_speed = (sum(_speeds) / len(_speeds)) if _speeds else 1.0
        if mean_speed <= 0:
            mean_speed = 1.0

        # ---------------------- 仿真主循环 ----------------------
        for frame in range(max_frame):
            ped_dict = {p.id: p for p in sim.person_map.values()}
            ped_list = list(ped_dict.values())

            raw_smoke = sim.smoke_matrix
            if raw_smoke is not None and isinstance(raw_smoke, list):
                smoke_data = np.array(raw_smoke)
            else:
                smoke_data = raw_smoke

            c_step_data = {}
            if social_on:
                if alarm_on and not info_diff_engine.alarm_triggered and smoke_data is not None:
                    try:
                        max_smoke = float(np.max(np.asarray(smoke_data, dtype=float)))
                    except Exception:
                        max_smoke = 0.0
                    if max_smoke >= alarm_threshold:
                        notified = info_diff_engine.trigger_alarm(ped_list, frame)
                        if guide_engine is not None and guide_engine.guides:
                            guide_engine.activate_guidance(guide_exit_id)
                        print(f"🚨 帧{frame} 烟雾峰值 {max_smoke:.3f} >= {alarm_threshold}，"
                              f"警报广播通知 {notified} 人；引导员转为引导出口 {guide_exit_id}")
                info_diff_engine.update_all(ped_list, current_step=frame, smoke_grid=smoke_data)
                group_result = group_engine.update_all(ped_dict, frame)
                herd_result = herd_engine.update_all(ped_list, grid_w, grid_h, frame)
                if guide_engine is not None and guide_engine.guides:
                    guide_engine.update_guides(ped_list, exit_check_list, frame)
                    guide_result = guide_engine.update_all(ped_list, frame)
                else:
                    guide_result = {}

                for pid, person in ped_dict.items():
                    info_state = info_state_engine.get_state_value(pid)
                    group_beh = group_result.get(pid, {}) or {}
                    herd_beh = herd_result.get(pid, {}) or {}
                    guide_beh = guide_result.get(pid, {}) or {}

                    exit_pref = dict(group_beh.get("exit_preference", {}) or {})
                    for k, v in (herd_beh.get("exit_preference", {}) or {}).items():
                        exit_pref[k] = exit_pref.get(k, 0.0) + v

                    target_exit = getattr(person, "target_exit", "") or ""
                    if misinfo_on and false_exit_id and info_state == "MISINFORMED":
                        target_exit = false_exit_id
                        exit_pref[false_exit_id] = max(exit_pref.get(false_exit_id, 0.0), 1.5)
                    elif info_state == "GUIDED" and pid in guided_ids and guide_exit_id:
                        target_exit = guide_exit_id
                        exit_pref[guide_exit_id] = max(exit_pref.get(guide_exit_id, 0.0), 1.5)

                    c_step_data[pid] = {
                        "target_exit": target_exit,
                        "exit_preference": exit_pref,
                        "herding_influence": herd_beh.get("herding_influence", 0.0),
                        "dominant_direction": herd_beh.get("dominant_direction", (0, 0)),
                        "guide_influence": guide_beh.get("guide_influence", 0.0),
                        "info_state": info_state,
                        "is_following": group_beh.get("is_following", False),
                        "follow_target": group_beh.get("follow_target"),
                        "follow_strength": group_beh.get("follow_strength", 0.0),
                        "is_waiting": group_beh.get("is_waiting", False),
                        "waiting_for": group_beh.get("waiting_for"),
                        "group_id": getattr(person, "group_id", ""),
                    }

                    person.info_state = info_state
                    person.info_source = info_state_engine.get_info_source(pid)
                    receive_step = info_state_engine.get_receive_step(pid)
                    person.receive_time = receive_step if (receive_step is not None and receive_step >= 0) else None
                    person.info_source_history = info_state_engine.get_info_source_history(pid)
                    person.follow_target = group_beh.get("follow_target")
                    person.is_waiting = group_beh.get("is_waiting", False)
                    person.target_exit = target_exit
                    person.exit_preference = exit_pref
                move_allowed = None
            if speed_model_on and ped_list:
                move_allowed = {}
                active_positions = [(p.x, p.y) for p in ped_list if not p.evacuated]
                for person in ped_list:
                    if person.evacuated:
                        continue
                    speed = float(getattr(person, "speed", 1.0) or 1.0)
                    density = 0
                    for qx, qy in active_positions:
                        if abs(qx - person.x) <= congestion_radius and abs(qy - person.y) <= congestion_radius:
                            density += 1
                    congestion_factor = 1.0
                    if density >= congestion_threshold > 0:
                        congestion_factor = max(0.3, congestion_threshold / float(density))
                        speed_stats["congested_total"] += 1
                    relative_speed = speed / mean_speed
                    credit = move_credit.get(person.id, 0.0) + relative_speed * congestion_factor
                    if credit >= 1.0:
                        move_allowed[person.id] = True
                        credit -= 1.0
                    else:
                        move_allowed[person.id] = False
                        speed_stats["blocked_total"] += 1
                    move_credit[person.id] = credit

            # 执行仿真
            sim.run_one_step(
                c_step_data=c_step_data,
                signage_model=signage_engine if signage_on else None,
            )

            if move_allowed is not None:
                for person in ped_list:
                    if person.evacuated:
                        continue
                    if not move_allowed.get(person.id, True):
                        person.x = getattr(person, "prev_x", person.x)
                        person.y = getattr(person, "prev_y", person.y)

            # 【新增】刷新可视化窗口
            visualizer.update(sim, exit_check_list, guide_engine, frame)

            d_view.capture()

            if frame % 20 == 0 or sim.is_all_evacuated():
                informed_now = sum(
                    1 for p in ped_list
                    if str(getattr(p, "info_state", "UNKNOWN")) != "UNKNOWN"
                )
                print(f"帧{frame} | 已撤离 {sim.evacuated_count}/{sim.total_persons}"
                      f" | 已知情 {informed_now}/{len(ped_list)}")
            if sim.is_all_evacuated():
                print(f"\n🎉 全员疏散完成，总仿真帧数：{frame}")
                break
        else:
            print(f"\n⏱ 达到最大仿真帧数 {max_frame}，仿真结束")
    finally:
        visualizer.close() # 关闭可视化窗口
        d_view.close()
        print(f"D 日志已关闭，结果输出至 outputs/experiments/{unique_run_id}")

    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)
    loader.export_evacuation_result(str(output_dir / "evacuation_result.json"))

    print("\n===== 运行结束汇总 =====")
    print(f"run_id: {unique_run_id}")
    print(f"地图: {map_path}")
    print(f"疏散人数: {sim.get_evacuated_count()} / {sim.total_persons}")
    if social_on:
        print("信息状态统计:", info_state_engine.get_statistics())
        print("信息传播:", info_diff_engine.get_propagation_summary())
        print("结伴行为统计:", group_engine.get_statistics())
        print("从众行为统计:", herd_engine.get_statistics())
        if guide_engine is not None:
            print("引导员统计:", guide_engine.get_statistics())
    if signage_engine is not None:
        print("指示牌统计:", signage_engine.get_statistics())
    if social_on:
        print(f"警报: triggered={info_diff_engine.alarm_triggered} "
              f"step={info_diff_engine.alarm_trigger_step} 阈值={alarm_threshold}")
        print(f"引导目标出口: {guide_exit_id} 计划引导人数: {len(guided_ids)}")
        if guide_engine is not None:
            print("引导员标记(供 B/D 上色):", [
                {"id": g.id, "x": g.x, "y": g.y, "color": getattr(g, "to_dict")().get("color")}
                for g in guide_engine.guides
            ])
    if speed_model_on:
        print(f"速度/拥堵模型: 本步累计被拥堵影响次数={speed_stats['congested_total']} "
              f"累计原地等待人次={speed_stats['blocked_total']}")
    print(f"输出目录: outputs/experiments/{unique_run_id}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A+B+C+D 完整联调主程序（C 行为可开关、可对比，带实时可视化）")
    parser.add_argument(
        "--map",
        default=None,
        help="所选 A 地图 JSON 路径；不传时优先读取 YAML 的 map_file，其次使用 maps/edited_map.json",
    )
    parser.add_argument("--social", choices=["on", "off"], default="on",
                        help="开启/关闭关系与结伴/信息等 C 行为；off = B 纯 CA 基线")
    parser.add_argument("--info", choices=["on", "off"], default="on",
                        help="开启/关闭广播警报与局部口头/关系信息传播")
    parser.add_argument("--misinfo", choices=["on", "off"], default="on",
                        help="开启/关闭错误出口信息（误导行人绕远/拥堵）")
    parser.add_argument("--signage", choices=["on", "off"], default="on",
                        help="开启/关闭静态疏散指示牌")
    parser.add_argument("--guide",
                        choices=["none", "fixed", "patrol", "toward_exit", "toward_crowd", "escort"],
                        default="patrol",
                        help="引导员部署策略（至少两种可配置可对比）")
    parser.add_argument("--max-frames", type=int, default=600, help="最大仿真帧数")
    parser.add_argument("--run-id", default=None, help="自定义输出 run_id")
    parser.add_argument("--misinfo-exit", default=None,
                        help="错误信息宣称的安全出口 id（默认取距地图中心最远出口）")
    parser.add_argument("--initial-informed-ratio", type=float, default=None,
                        help="火灾初期已知道险情的人员比例（不传则用 YAML 的 initial_informed_ratio）")
    parser.add_argument("--alarm", choices=["on", "off"], default=None,
                        help="烟雾达阈值时是否触发警报广播（不传则用 YAML 的 alarm_enabled）")
    parser.add_argument("--alarm-smoke-threshold", type=float, default=None,
                        help="触发警报的烟雾浓度阈值（不传则用 YAML 的 alarm_smoke_threshold）")
    parser.add_argument("--guide-exit", default=None,
                        help="引导员要引导人群前往的较远出口 id（默认取距地图中心最远出口）")
    parser.add_argument("--guide-share", type=float, default=0.30,
                        help="接受引导的人群比例（只引导部分人群）")
    parser.add_argument("--patrol-step", type=int, default=2,
                        help="巡查路线采样间隔（越小巡查点越密）")
    parser.add_argument("--speed-model", choices=["on", "off"], default="on",
                        help="是否启用速度差异与拥堵减速模型")
    parser.add_argument("--congestion-radius", type=int, default=2,
                        help="拥堵密度统计半径（元胞）")
    parser.add_argument("--congestion-threshold", type=int, default=4,
                        help="达到该人数视为拥堵并开始减速")
    parser.add_argument("--visual", choices=["on", "off"], default="on",
                        help="【新增】开启/关闭matplotlib实时可视化窗口，批量实验建议off")
    args = parser.parse_args()
    main(options={
        "map": args.map,
        "social": args.social == "on",
        "info": args.info == "on",
        "misinfo": args.misinfo == "on",
        "signage": args.signage == "on",
        "guide": args.guide,
        "max_frames": args.max_frames,
        "run_id": args.run_id,
        "misinfo_exit": args.misinfo_exit,
        "initial_informed_ratio": args.initial_informed_ratio,
        "alarm": None if args.alarm is None else args.alarm == "on",
        "alarm_smoke_threshold": args.alarm_smoke_threshold,
        "guide_exit": args.guide_exit,
        "guide_share": args.guide_share,
        "patrol_step": args.patrol_step,
        "speed_model": args.speed_model == "on",
        "congestion_radius": args.congestion_radius,
        "congestion_threshold": args.congestion_threshold,
        "visual": args.visual == "on",
    })
