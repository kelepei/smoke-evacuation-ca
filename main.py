import sys
import csv
import os
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# 日志配置 自动创建output_log文件夹存放csv
LOG_DIR = project_root / "output_log"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = str(LOG_DIR / "people_log.csv")

# A模块
from core.schema import ScenarioConfig, SmokeSource, Person
from map_import.map_loader_grid import load_grid
from control.scene_config import SceneConfigGenerator

# 全套C模块
from social.social_graph import SocialGraphBuilder
from social.information_state import InformationStateEngine
from social.information_diffusion import InformationDiffusionEngine
from social.group_behavior import GroupBehaviorEngine
from social.herding_model import HerdingModel
from social.guide_agent import GuideAgentModel, GuideMoveStrategy
from control.signage_model import SignageModel

# 仿真引擎
from simulation.evac_simulation import EvacEngine

# json文件路径
PROFILE_JSON = "social/person_profiles.json"

# ---------------------- 日志导出工具函数 ----------------------
def init_log_file():
    """初始化csv，写入表头，清空旧文件"""
    header = [
        "step", "time_s", "person_id", "x", "y", "evacuated",
        "heading", "risk", "dose", "conflict", "exit_switch"
    ]
    # w模式：每次运行覆盖旧日志
    with open(LOG_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
    print(f"日志文件初始化完成，路径：{LOG_FILE}")

def write_step_log(step: int, person_list):
    """每一步写入所有行人数据，无数据字段留空"""
    time_s = step * 0.5
    rows = []
    for p in person_list:
        row = [
            step,
            time_s,
            p.id,
            p.x,
            p.y,
            p.evacuated,
            "",    # heading 暂未提供
            "",    # risk 暂未提供
            "",    # dose 暂未提供
            "",    # conflict 暂未提供
            ""     # exit_switch 暂未提供
        ]
        rows.append(row)
    # 追加写入当前步所有行人
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

# 可选：保存每一步烟雾场矩阵
def save_smoke_matrix(step, smoke_grid, save_dir="smoke_log"):
    smoke_path = project_root / save_dir
    smoke_path.mkdir(exist_ok=True)
    file_path = str(smoke_path / f"smoke_step_{step}.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        for row in smoke_grid:
            f.write(",".join([str(v) for v in row]) + "\n")

# ---------------------- 场景加载函数 ----------------------
def load_A_scene() -> ScenarioConfig:
    grid_path = project_root / "scenarios/classroom_corridor.json"
    grid = load_grid(str(grid_path))
    print(f"加载网格成功: {grid.width}x{grid.height} 网格大小")
    scene = ScenarioConfig(
        scenario_id="classroom",
        grid=grid,
        persons=[],
        smoke_sources=[SmokeSource(x=5, y=5, intensity=10)]
    )
    return scene

# ---------------------- 主仿真函数 ----------------------
def main():
    # 加载A基础CA地图
    ca_scene = load_A_scene()
    grid_w = ca_scene.grid.width
    grid_h = ca_scene.grid.height
    print("✅ A模块：基础网格加载完成")
    print(f"地图尺寸: {grid_w} × {grid_h}")

    # 加载教室预设配置
    scene_cfg = SceneConfigGenerator.get_preset("classroom")
    print("✅ 场景配置读取完成")

    # 社交图构建
    builder = SocialGraphBuilder.from_config(scene_cfg, profiles_json_path=PROFILE_JSON)
    social_graph, person_dict = builder.build_with_config()
    print("✅ C03：社交关系图构建成功")

    # 出口容错处理
    exit_list = []
    if hasattr(ca_scene.grid, "exits") and ca_scene.grid.exits:
        for exit_info in ca_scene.grid.exits:
            eid, ex, ey = exit_info
            exit_list.append((eid, ex, ey))
    else:
        exit_list = [("exit_01", grid_w - 1, grid_h // 2)]
        print("⚠️ 警告：地图缺少 exits 属性，使用默认出口")
    print(f"找到出口数量: {len(exit_list)}")

    # C模块初始化
    info_state = InformationStateEngine(builder)
    print("✅ C06 信息状态引擎就绪")

    info_diff = InformationDiffusionEngine(builder, info_state, grid_w, grid_h)
    print("✅ C07 信息扩散引擎就绪")

    herd = HerdingModel(builder, info_state, exit_list)
    print("✅ C05 从众模型就绪")

    group = GroupBehaviorEngine(builder)
    print("✅ C04 结伴行为就绪")

    guide = GuideAgentModel(builder, info_state, grid_w, grid_h, exit_list)
    guide.add_guide(x=24, y=22, profile="teacher", move_strategy=GuideMoveStrategy.FIXED)
    print("✅ C09 引导员模型就绪")

    # 指示牌模型初始化
    signage = SignageModel(exit_list)
    signage.add_static_signage(x=10, y=9, target_exit="exit_01")
    print("✅ C08 指示牌模型就绪")

    # A模块与C模块行人数据融合
    ca_person_list = []
    for pid, c_person in person_dict.items():
        new_p = Person(id=pid, x=c_person.x, y=c_person.y)
        new_p.group_id = c_person.group_id
        new_p.herding_tendency = c_person.herding_tendency
        new_p.risk_sensitivity = c_person.risk_sensitivity
        new_p.info_state = c_person.info_state
        new_p.target_exit = c_person.target_exit
        new_p.evacuated = False
        new_p.dose = 0.0
        ca_person_list.append(new_p)
    ca_scene.persons = ca_person_list
    print(f"✅ A+C融合完毕，总行人{len(ca_person_list)}")

    # 仿真启动
    sim = EvacEngine(scene=ca_scene)
    max_step = 600

    # ============ 新增：初始化日志文件 ============
    init_log_file()

    # 仿真主循环
    for step in range(max_step):
        # 构造 {行人ID: Person实例} 字典，适配GroupBehaviorEngine入参要求
        person_id_dict = {person.id: person for person in sim.person_map.values()}
        person_list = list(person_id_dict.values())

        info_diff.update_all(person_list, current_step=step, smoke_grid=None)
        group_data = group.update_all(person_id_dict, step)
        herd_data = herd.update_all(person_list, grid_w, grid_h, step)
        guide.update_guides(person_list, exit_list, step)
        guide_data = guide.update_all(person_list, step)

        c_step_data = {
            "group": group_data,
            "herd": herd_data,
            "guide": guide_data,
            "signage": signage
        }
        sim.run_one_step(c_step_data=c_step_data)

        # ============ 新增：每一步写入人员日志 ============
        write_step_log(step, person_list)

        # 可选：开启保存烟雾场矩阵（取消下面注释即可启用）
        # save_smoke_matrix(step, sim.smoke_matrix)

        # 控制台打印样例行人位置
        if sim.person_map:
            sample = list(sim.person_map.values())[0]
            print(f"帧{step} | 行人{sample.id} 坐标({sample.x},{sample.y})")

        # 全部疏散完成提前终止
        if sim.is_all_evacuated():
            print(f"\n🎉 全部人员疏散完成，总步数：{step}")
            print("结伴统计：", group.get_statistics())
            print("从众统计：", herd.get_statistics())
            break
    else:
        print("\n⏱ 达到最大仿真时长，程序结束")

    # 关闭动画窗口（已删动画，空函数无影响）
    sim.close_anim()

if __name__ == "__main__":
    main()