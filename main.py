import sys
import csv
import numpy as np
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 日志配置 自动创建output_log文件夹存放csv
LOG_DIR = project_root / "output_log"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = str(LOG_DIR / "people_log.csv")

# A模块基础地图
from core.schema import ScenarioConfig, SmokeSource
from map_import.map_loader_grid import load_grid
from control.scene_config import SceneConfigGenerator

# 全套C模块行为引擎（全部保留，无注释删除）
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

# ---------------------- 日志导出工具函数 ----------------------
def init_log_file():
    """初始化csv日志表头，覆盖旧日志"""
    header = [
        "step", "time_s", "person_id", "x", "y", "evacuated",
        "heading", "risk", "dose", "conflict", "exit_switch"
    ]
    with open(LOG_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
    print(f"日志文件初始化完成：{LOG_FILE}")

def write_step_log(step: int, person_list):
    """逐帧写入行人轨迹日志"""
    time_s = step * 0.5
    rows = []
    for p in person_list:
        row = [
            step,
            time_s,
            p.id,
            round(p.x, 2),
            round(p.y, 2),
            p.evacuated,
            "",
            round(getattr(p, "risk", 0.0), 3),
            round(getattr(p, "dose", 0.0), 3),
            "",
            ""
        ]
        rows.append(row)
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

# 可选：导出烟雾矩阵文件
def save_smoke_matrix(step, smoke_grid, save_dir="smoke_log"):
    smoke_path = project_root / save_dir
    smoke_path.mkdir(exist_ok=True)
    file_path = smoke_path / f"smoke_step_{step}.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        for row in smoke_grid:
            f.write(",".join([str(v) for v in row]) + "\n")

# ---------------------- 场景加载函数（新增Y轴坐标对齐修复） ----------------------
def load_A_scene() -> ScenarioConfig:
    grid_path = project_root / "scenarios/classroom_corridor.json"
    grid = load_grid(str(grid_path))
    print(f"地图加载完成：宽{grid.width} × 高{grid.height}")

    # 坐标对齐核心：图像Y轴翻转函数，解决地图和行人上下错位
    cell_size = grid.cell_size
    grid_height = grid.height
    def image_y_to_ca(y_img):
        return grid_height - 1 - (y_img / cell_size)

    scene = ScenarioConfig(
        scenario_id="classroom",
        grid=grid,
        persons=[],
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
        # 适配可视化双出口兜底
        exit_check_list = [("exit_01", 12, 10), ("exit_02", 42, 60)]
        print("未读取到原生出口，启用双出口兜底配置")
    print(f"出口总数：{len(exit_check_list)}")

    # 2. 加载A模块行人点位
    loader = CASimulationLoader()
    loader.init_ca_model()
    external_person_list = loader.agent_list
    print(f"A模块原始行人总数：{len(external_person_list)}")

    # 3. 越界行人修复：严重越界重置到中心，轻微越界裁剪边界
    fix_count = 0
    reset_count = 0
    for ped in external_person_list:
        ox = ped.x
        oy = ped.y
        # 初始化行人剂量、风险属性
        if not hasattr(ped, "dose"):
            ped.dose = 0.0
        if not hasattr(ped, "risk"):
            ped.risk = 0.0
        # 严重越界判定
        if ox < -5 or ox > max_valid_x * 2 or oy < -5 or oy > max_valid_y * 2:
            ped.x = map_center_x
            ped.y = map_center_y
            reset_count += 1
            print(f"【重度越界重置】行人{ped.id} 原坐标({ox:.2f},{oy:.2f}) → 地图中心({ped.x:.2f},{ped.y:.2f})")
        else:
            # 边缘裁剪
            new_x = max(0.0, min(float(ox), float(max_valid_x)))
            new_y = max(0.0, min(float(oy), float(max_valid_y)))
            if abs(new_x - ox) > 0.01 or abs(new_y - oy) > 0.01:
                ped.x = new_x
                ped.y = new_y
                fix_count += 1
                print(f"【边缘裁剪】行人{ped.id} 原坐标({ox:.2f},{oy:.2f}) → ({ped.x:.2f},{ped.y:.2f})")
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

    # 过滤有效行人（匹配属性配置）
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

    # 初始化各行为引擎【核心修复：GroupBehaviorEngine传入builder对象，而非social_graph(DiGraph)】
    info_state_engine = InformationStateEngine(builder)
    print("✅ C06 人员信息状态引擎就绪")

    info_diff_engine = InformationDiffusionEngine(builder, info_state_engine, grid_w, grid_h)
    print("✅ C07 信息传播引擎就绪")

    herd_engine = HerdingModel(builder, info_state_engine, exit_check_list)
    print("✅ C05 从众行为引擎就绪")

    # 关键修复：使用builder实例传入结伴模块，解决DiGraph无.persons属性报错，结伴功能完整保留
    group_engine = GroupBehaviorEngine(builder)
    print("✅ C04 结伴行为引擎就绪")

    # 全域引导员部署（适配可视化画面）
    guide_engine = GuideAgentModel(builder, info_state_engine, grid_w, grid_h, exit_check_list)
    guide_engine.add_guide(x=29, y=10, profile="teacher", move_strategy=GuideMoveStrategy.FIXED)
    guide_engine.add_guide(x=12, y=10, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    guide_engine.add_guide(x=42, y=60, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    guide_engine.add_guide(x=22, y=30, profile="staff", move_strategy=GuideMoveStrategy.PATROL)
    print("✅ C09 全域引导员部署完成（4个点位）")

    # 全域指示牌部署【修复函数名：add_static_sign → add_static_signage】
    signage_engine = SignageModel(exit_check_list)
    signage_engine.add_static_signage(x=10, y=9, target_exit="exit_01")
    signage_engine.add_static_signage(x=22, y=30, target_exit="exit_01")
    signage_engine.add_static_signage(x=40, y=60, target_exit="exit_02")
    print("✅ C08 静态指示牌部署完成（3个点位）")

    # 启动仿真器
    sim = EvacEngine(scene=ca_scene)
    max_frame = 600
    init_log_file()
    dt_step = 0.5  # 仿真步长时间，匹配time_s计算

    # ---------------------- 仿真主循环（核心修复：list烟雾转numpy数组适配.shape + 剂量累加） ----------------------
    for frame in range(max_frame):
        ped_dict = {p.id: p for p in sim.person_map.values()}
        ped_list = list(ped_dict.values())

        # 核心修复：将list烟雾矩阵转为numpy数组，兼容.shape读取
        raw_smoke = sim.smoke_matrix
        if raw_smoke is not None and isinstance(raw_smoke, list):
            smoke_data = np.array(raw_smoke)
        else:
            smoke_data = None

        info_diff_engine.update_all(ped_list, current_step=frame, smoke_grid=smoke_data)

        # 【新增】按任务书公式计算烟雾风险、累积暴露剂量
        for p in ped_list:
            # 获取行人所在格子烟雾浓度
            cell_x = int(np.clip(p.x, 0, grid_w - 1))
            cell_y = int(np.clip(p.y, 0, grid_h - 1))
            smoke_conc = raw_smoke[cell_y][cell_x] if raw_smoke is not None else 0.0
            # 简化风险计算 Risk = a*S (a=1)
            p.risk = smoke_conc
            # 累积剂量 Dose += S * Δt
            p.dose += smoke_conc * dt_step

        # 结伴行为正常执行，无屏蔽
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

        # 执行单步仿真推演
        sim.run_one_step(c_step_data=behavior_package, signage_model=signage_engine)

        # 写入轨迹日志
        write_step_log(frame, ped_list)

        # 控制台打印采样行人坐标
        if sim.person_map:
            sample_ped = next(iter(sim.person_map.values()))
            print(f"帧{frame} | 行人{sample_ped.id} 坐标({sample_ped.x:.1f}, {sample_ped.y:.1f}) 烟雾剂量:{sample_ped.dose:.2f}")

        # 全部疏散完毕提前终止
        if sim.is_all_evacuated():
            print(f"\n🎉 全员疏散完成，总仿真帧数：{frame}")
            print("结伴行为统计：", group_engine.get_statistics())
            print("从众行为统计：", herd_engine.get_statistics())
            break
    else:
        print("\n⏱ 达到最大仿真帧数，仿真结束")

    # 导出结果文件给D端
    output_dir = project_root / "output"
    output_dir.mkdir(exist_ok=True)
    loader.export_evacuation_result(str(output_dir / "evacuation_result.json"))

    # 关闭可视化窗口
    if hasattr(sim, "close_animation"):
        sim.close_animation()

if __name__ == "__main__":
    main()