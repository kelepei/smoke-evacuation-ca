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
from control.scene_config import SceneConfigGenerator

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

# ---------------------- 场景加载函数 ----------------------
def load_A_scene() -> ScenarioConfig:
    grid_path = project_root / "maps/edited_map.json"
    grid = load_grid(str(grid_path))
    print(f"地图加载完成：宽{grid.width} × 高{grid.height}")

    # 兜底出口定义
    fallback_exits = [("exit_01", 12, 10), ("exit_02", 42, 60)]
    if not hasattr(grid, "exits") or not grid.exits:
        grid.exits = fallback_exits

    scene = ScenarioConfig(
        scenario_id="classroom",
        grid=grid,
        persons=[],
        exits=fallback_exits,
        smoke_sources=[SmokeSource(x=5, y=5, intensity=10)]
    )
    return scene

# ---------------------- 主仿真入口 ----------------------
def main():
    # 1. 初始化地图
    ca_scene = load_A_scene()
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

    # 4. 绑定C模块行为参数
    scene_cfg = SceneConfigGenerator.get_preset("classroom")
    builder = SocialGraphBuilder.from_config(scene_cfg, profiles_json_path=PROFILE_JSON)
    social_graph, person_attr_map = builder.build()
    print("✅ 社交关系图谱构建完成")

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

    # 初始化各行为引擎
    info_state_engine = InformationStateEngine(builder)
    print("✅ C06 人员信息状态引擎就绪")

    info_diff_engine = InformationDiffusionEngine(builder, info_state_engine, grid_w, grid_h)
    print("✅ C07 信息传播引擎就绪")

    herd_engine = HerdingModel(builder, info_state_engine, exit_check_list)
    print("✅ C05 从众行为引擎就绪")

    group_engine = GroupBehaviorEngine(builder)
    print("✅ C04 结伴行为引擎就绪")

    # 全域引导员部署
    guide_engine = GuideAgentModel(builder, info_state_engine, grid_w, grid_h, exit_check_list)
    guide_engine.add_guide(x=29, y=10, profile="teacher", move_strategy=GuideMoveStrategy.FIXED)
    guide_engine.add_guide(x=12, y=10, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    guide_engine.add_guide(x=42, y=60, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    guide_engine.add_guide(x=22, y=30, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    print("✅ C09 全域引导员部署完成（4个点位）")

    # 全域指示牌部署
    signage_engine = SignageModel(exit_check_list)
    signage_engine.add_static_signage(x=10, y=9, target_exit="exit_01")
    signage_engine.add_static_signage(x=22, y=30, target_exit="exit_01")
    signage_engine.add_static_signage(x=40, y=60, target_exit="exit_02")
    print("✅ C08 静态指示牌部署完成（3个点位）")

    # 启动仿真器
    sim = EvacEngine(scene=ca_scene)
    max_frame = 600

    # ===================== D可视化接入 =====================
    # 使用时间戳生成唯一run_id，避免日志覆盖报错
    unique_run_id = f"exp_classroom_smoke_{int(time.time())}"
    d_view = DVisualizationEntry(
        simulation=sim,
        output_root="outputs/experiments",
        run_id=unique_run_id,
        time_step_s=0.5,
    )
    print(f"本次实验run_id：{unique_run_id}")

    try:
        d_view.start()

        # ---------------------- 仿真主循环 ----------------------
        for frame in range(max_frame):
            ped_dict = {p.id: p for p in sim.person_map.values()}
            ped_list = list(ped_dict.values())

            smoke_data = sim.smoke_engine.smoke_matrix
            info_diff_engine.update_all(ped_list, current_step=frame, smoke_grid=smoke_data)

            group_result = group_engine.update_all(ped_dict, frame)
            herd_result = herd_engine.update_all(ped_list, grid_w, grid_h, frame)
            guide_engine.update_guides(ped_list, exit_check_list, frame)
            guide_result = guide_engine.update_all(ped_list, frame)

            behavior_package = {
                "group": group_result,
                "herd": herd_result,
                "guide": guide_result,
                "signage": signage_engine
            }

            sim.run_one_step(c_step_data=behavior_package, signage_model=signage_engine)
            snapshot = d_view.capture()

            if sim.person_map:
                sample_ped = next(iter(sim.person_map.values()))
                print(f"帧{frame} | 行人{sample_ped.id} 坐标({sample_ped.x:.1f}, {sample_ped.y:.1f}) risk:{sample_ped.risk:.2f} dose:{sample_ped.dose:.2f}")

            if sim.is_all_evacuated():
                print(f"\n🎉 全员疏散完成，总仿真帧数：{frame}")
                print("结伴行为统计：", group_engine.get_statistics())
                print("从众行为统计：", herd_engine.get_statistics())
                break
        else:
            print("\n⏱ 达到最大仿真帧数，仿真结束")

    finally:
        d_view.close()
        print("D可视化资源释放完毕，实验结果已输出至 outputs/experiments")

    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)
    loader.export_evacuation_result(str(output_dir / "evacuation_result.json"))

if __name__ == "__main__":
    main()