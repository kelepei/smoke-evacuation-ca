"""
主程序入口 - A+B+C+D 完整联调版本

功能：
    1. A 模块加载地图
    2. C 模块生成人群和社会关系
    3. C 模块为行人按所选地图分配位置
    4. C 行为引擎（结伴/从众/信息/引导/指示牌/错误信息）逐帧输出 c_step_data
    5. B 模块 CA 仿真
    6. D 模块记录 CSV 日志

命令行（便于"开/关关系模型"与">=2 种引导策略"对比实验）：
    python main.py --map maps/edited_map.json                     # 默认：关系模型开启
    python main.py --social off                                   # 基线：B 纯 CA（无 C 行为）
    python main.py --guide fixed / --guide patrol / --guide toward_exit ...
    python main.py --misinfo off                                  # 关闭错误出口信息
    python main.py --info off                                     # 关闭广播/局部口头传播
    python main.py --signage off                                  # 关闭静态指示牌
"""

import argparse
import random
import sys
import time

import numpy as np
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# D可视化接口
from visualization.runtime_entry import DVisualizationEntry

# A模块基础地图
from core.schema import ScenarioConfig, SmokeSource
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

# json文件路径（C人员属性配置）
PROFILE_JSON = "social/person_profiles.json"

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


def load_A_scene(map_path) -> ScenarioConfig:
    grid_path = Path(map_path)
    if not grid_path.is_file():
        raise SystemExit(f"[ERROR] 地图文件不存在: {grid_path}")
    grid = load_grid(str(grid_path))
    print(f"地图加载完成：宽{grid.width} × 高{grid.height}")

    # 出口：默认兜底出口全部在地图内时沿用（保持原行为）；否则使用地图自身的 exit 元胞
    exit_cells = [
        (cell.x, cell.y)
        for cell in grid.cells
        if str(getattr(cell.cell_type, "value", cell.cell_type)).lower() == "exit"
    ]
    fallback_exits = [("exit_01", 12, 10), ("exit_02", 42, 60)]
    fallback_in_bounds = all(
        0 <= ex < grid.width and 0 <= ey < grid.height
        for _, ex, ey in fallback_exits
    )
    if not hasattr(grid, "exits") or not grid.exits:
        if fallback_in_bounds:
            grid.exits = [
                (f"exit_{i + 1:02d}", *_nearest_free_cell(grid, ex, ey))
                for i, (_, ex, ey) in enumerate(fallback_exits)
            ]
        elif exit_cells:
            grid.exits = [
                (f"exit_{i + 1:02d}", x, y) for i, (x, y) in enumerate(exit_cells)
            ]
        else:
            grid.exits = [
                (f"exit_{i + 1:02d}", *_nearest_free_cell(grid, ex, ey))
                for i, (_, ex, ey) in enumerate(fallback_exits)
            ]

    exits = list(getattr(grid, "exits", []) or fallback_exits)

    # 烟源放在离默认位置最近的可通行元胞上，避免所选地图较小时落在墙体/越界
    smoke_x, smoke_y = _nearest_free_cell(grid, 42, 90)

    scene = ScenarioConfig(
        scenario_id="classroom",
        grid=grid,
        persons=[],
        exits=exits,
        smoke_sources=[SmokeSource(x=smoke_x, y=smoke_y, intensity=10)]
    )
    return scene


# ---------------------- 主仿真入口 ----------------------
def main(options=None):
    options = options or {}
    yaml_path = project_root / "control" / "config_template.yaml"
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
    print("===== C 行为实验开关 =====")
    print(f"  social={social_on} info={info_on} misinfo={misinfo_on} "
          f"signage={signage_on} guide={guide_key} max_frames={max_frame} run_id={unique_run_id}")

    # 1. 初始化地图
    ca_scene = load_A_scene(map_path)
    grid_w = ca_scene.grid.width
    grid_h = ca_scene.grid.height
    max_valid_x = grid_w - 1
    max_valid_y = grid_h - 1
    map_center_x = max_valid_x / 2
    map_center_y = max_valid_y / 2
    print(f"坐标合法范围：X[0,{max_valid_x}] Y[0,{max_valid_y}]")

    # 出口识别
    print("===== 场景出口列表 =====")
    exit_check_list = []
    if hasattr(ca_scene.grid, "exits") and ca_scene.grid.exits:
        for exit_info in ca_scene.grid.exits:
            eid, ex, ey = exit_info
            exit_check_list.append((eid, ex, ey))
            print(f"原生出口 {eid}: X={ex}, Y={ey}")
    else:
        exit_check_list = [("exit_01", 12, 10), ("exit_02", 42, 60)]
        print("未读取到原生出口，启用双出口兜底配置")
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

    # 4. 读取场景配置并固定随机种子（保证开/关对比可复现）
    scene_cfg = SceneConfigGenerator.load_config_from_yaml(str(yaml_path))
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
        info_diff_engine.broadcast_params["enabled"] = info_on
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

        # C09 引导员：按地图出口自动部署，策略可配置（至少两种可对比）
        guide_engine = GuideAgentModel(builder, info_state_engine, grid_w, grid_h, exit_check_list)
        if guide_strategy is not None:
            for eid, ex, ey in exit_check_list:
                pt = _deploy_point(ca_scene.grid, exit_check_list, eid, step=1)
                if pt is None:
                    continue
                guide_engine.add_guide(x=pt[0], y=pt[1], profile="staff",
                                       move_strategy=guide_strategy)
            print(f"✅ C09 引导员按地图自动部署完成（策略={guide_key}，{len(guide_engine.guides)} 名）")

        # 错误出口信息：默认把距地图中心最远的出口当作"被宣称的安全出口"
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
    d_view = DVisualizationEntry(
        simulation=sim,
        output_root="outputs/experiments",
        run_id=unique_run_id,
        time_step_s=0.5,
    )

    try:
        d_view.start()
        print("✅ D CSV 日志已启动")

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
                # 更新信息传播（广播警报 / 局部口头 / 关系传播 / 错误信息）
                info_diff_engine.update_all(ped_list, current_step=frame, smoke_grid=smoke_data)

                # 更新行为引擎：结伴（等待/跟随）、从众、引导
                group_result = group_engine.update_all(ped_dict, frame)
                herd_result = herd_engine.update_all(ped_list, grid_w, grid_h, frame)
                if guide_engine is not None and guide_engine.guides:
                    guide_engine.update_guides(ped_list, exit_check_list, frame)
                    guide_result = guide_engine.update_all(ped_list, frame)
                else:
                    guide_result = {}

                # 构造 c_step_data 并把 C 每步状态写回 B 行人对象（供 D 日志记录）
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
                        # 被错误信息误导：切换目标出口到"假安全出口"
                        target_exit = false_exit_id
                        exit_pref[false_exit_id] = max(exit_pref.get(false_exit_id, 0.0), 1.5)

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

                    # 同步到行人对象：D 日志能记录"每人接收信息的时间/来源/跟随目标"
                    person.info_state = info_state
                    person.info_source = info_state_engine.get_info_source(pid)
                    person.receive_time = info_state_engine.get_receive_step(pid)
                    person.info_source_history = info_state_engine.get_info_source_history(pid)
                    person.follow_target = group_beh.get("follow_target")
                    person.is_waiting = group_beh.get("is_waiting", False)
                    person.target_exit = target_exit
                    person.exit_preference = exit_pref

            # 执行仿真（B 正式入口 run_one_step）
            sim.run_one_step(
                c_step_data=c_step_data,
                signage_model=signage_engine if signage_on else None,
            )

            # 捕获快照给 D 日志
            d_view.capture()

            if frame % 20 == 0 or sim.is_all_evacuated():
                print(f"帧{frame} | 已撤离 {sim.evacuated_count}/{sim.total_persons}")

            # 全员疏散完成
            if sim.is_all_evacuated():
                print(f"\n🎉 全员疏散完成，总仿真帧数：{frame}")
                break
        else:
            print(f"\n⏱ 达到最大仿真帧数 {max_frame}，仿真结束")

    finally:
        d_view.close()
        print(f"D 日志已关闭，结果输出至 outputs/experiments/{unique_run_id}")

    # 导出结果（保留）
    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)
    loader.export_evacuation_result(str(output_dir / "evacuation_result.json"))

    # C 行为汇总（便于"开/关"与"引导策略"对比）
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
    print(f"输出目录: outputs/experiments/{unique_run_id}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A+B+C+D 完整联调主程序（C 行为可开关、可对比）")
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
    })
