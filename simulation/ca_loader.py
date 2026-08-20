import json
import os
import numpy as np

# 自定义JSON编码器，兼容numpy数值类型，解决float32无法序列化报错
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, np.floating):
            return float(obj)
        if isinstance(obj, np.integer):
            return int(obj)
        return super().default(obj)


# 自动定位项目根目录，解决工作目录导致的路径找不到问题
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
# 拼接完整文件路径
CELL_SIZE = 0.5
PEOPLE_FILE_PATH = os.path.join(BASE_DIR, "control", "output_people_position.json")
MAP_FILE_PATH = os.path.join(BASE_DIR, "maps", "edited_map.json")


class Person:
    """CA疏散模型行人Agent，完全对齐A/C模块约定JSON字段"""
    def __init__(self, person_data):
        # ========== C模块生成只读字段 ==========
        self.id = person_data["id"]
        self.profile = person_data["profile"]
        self.group_id = person_data["group_id"]
        self.speed = person_data["speed"]
        self.risk_sensitivity = person_data["risk_sensitivity"]
        self.familiarity = person_data["familiarity"]
        self.herding_tendency = person_data["herding_tendency"]

        # ========== A模块生成网格坐标 ==========
        self.x = person_data["x"]  # 元胞列坐标
        self.y = person_data["y"]  # 元胞行坐标

        # ========== B模块仿真运行时自主维护字段 ==========
        self.target_exit_id = person_data.get("target_exit", None)
        self.info_state = person_data.get("info_state", "UNKNOWN")
        self.evacuated = person_data.get("evacuated", False)
        self.dose = person_data.get("dose", 0.0)
        self.prev_x = self.x
        self.prev_y = self.y

    def get_real_coordinate(self):
        """网格坐标转为真实米制坐标（仅用于结果导出，CA计算直接用网格xy）"""
        real_x = self.x * CELL_SIZE
        real_y = self.y * CELL_SIZE
        return real_x, real_y


class CASimulationLoader:
    """数据加载核心类：加载地图+A模块行人数据、坐标校验、结果导出"""
    def __init__(self):
        self.metadata = {}          # 场景全局元数据
        self.agent_list = []        # 全部行人Agent列表
        self.map_data = {}          # 原始地图JSON数据
        self.free_cells = set()     # 合法可通行网格集合 (x,y)

    def load_map(self):
        """加载地图文件，提取所有可通行网格用于坐标合法性校验"""
        if not os.path.exists(MAP_FILE_PATH):
            raise FileNotFoundError(f"地图文件不存在：{MAP_FILE_PATH}")

        with open(MAP_FILE_PATH, "r", encoding="utf-8") as f:
            self.map_data = json.load(f)

        # 适配地图JSON结构，提取可行走格子，根据你的地图字段微调key
        for cell in self.map_data.get("free_cells", []):
            cx = cell["x"]
            cy = cell["y"]
            self.free_cells.add((cx, cy))
        print(f"✅ 地图加载完成，合法通行网格总数：{len(self.free_cells)}")

    def load_person_position(self):
        """读取A模块输出的人员位置JSON，生成行人Agent并校验点位"""
        if not os.path.exists(PEOPLE_FILE_PATH):
            raise FileNotFoundError(f"行人位置文件不存在：{PEOPLE_FILE_PATH}")

        with open(PEOPLE_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.metadata = data["metadata"]
        person_raw_list = data["persons"]
        valid_count = 0
        invalid_count = 0

        for p_data in person_raw_list:
            px = p_data["x"]
            py = p_data["y"]
            # 坐标合法性校验：必须在可行走网格内
            if self.free_cells and (px, py) not in self.free_cells:
                print(f"⚠️ 非法点位过滤：行人ID={p_data['id']} 坐标({px},{py})处于墙体/障碍物，跳过")
                invalid_count += 1
                continue

            agent = Person(p_data)
            self.agent_list.append(agent)
            valid_count += 1

        print(f"✅ 行人数据加载完成 | 文件总人数：{len(person_raw_list)} | 合法可用人数：{valid_count} | 非法过滤：{invalid_count}")
        print(f"场景名称：{self.metadata.get('scene_name', '未命名场景')} 元胞尺寸：{CELL_SIZE}m")

    def init_ca_model(self):
        """一键初始化：地图+行人全流程加载（对外调用入口）"""
        self.load_map()
        self.load_person_position()

    def export_evacuation_result(self, save_path="evacuation_result.json"):
        """仿真结束导出标准化结果JSON"""
        result_data = {
            "metadata": self.metadata,
            "cell_size_m": CELL_SIZE,
            "agents": []
        }
        for agent in self.agent_list:
            real_x, real_y = agent.get_real_coordinate()
            agent_info = {
                "id": agent.id,
                "grid_x": agent.x,
                "grid_y": agent.y,
                "real_x": real_x,
                "real_y": real_y,
                "profile": agent.profile,
                "group_id": agent.group_id,
                "move_speed": agent.speed,
                "target_exit": agent.target_exit_id,
                "info_state": agent.info_state,
                "is_evacuated": agent.evacuated,
                "smoke_dose": agent.dose
            }
            result_data["agents"].append(agent_info)

        # 自动创建输出文件夹
        output_dir = os.path.dirname(save_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=2, cls=NumpyEncoder)
        print(f"✅ 仿真结果已导出至：{os.path.abspath(save_path)}")


# 独立测试入口（直接运行本文件可测试加载功能）
if __name__ == "__main__":
    try:
        ca_loader = CASimulationLoader()
        ca_loader.init_ca_model()
        # 简单打印前3个行人信息验证
        for idx, ped in enumerate(ca_loader.agent_list[:3]):
            print(f"测试行人{idx+1} | ID:{ped.id} 网格坐标:({ped.x},{ped.y}) 角色:{ped.profile}")
    except Exception as e:
        print(f"❌ 加载失败：{str(e)}")