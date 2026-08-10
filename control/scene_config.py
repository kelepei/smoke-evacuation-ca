"""
C11: 场景参数配置模块 (scene_config.py)
提供用户可调的人群生成参数，实现不同场景下灵活的人群配置。

用户只需修改 config_template.yaml，然后双击运行此文件即可生成人群。
"""

import copy
import json
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np


# ============================================================
# 数据类定义
# ============================================================

@dataclass
class GroupConfig:
    """群体配置：定义各种类型群体的生成参数"""
    has_family_prob: float = 0.6
    family_size_range: Tuple[int, int] = (2, 5)
    family_relation_type: str = "family"

    has_friend_prob: float = 0.7
    friend_size_range: Tuple[int, int] = (2, 6)
    friend_relation_type: str = "friend"

    has_classmate_prob: float = 0.6
    classmate_size_range: Tuple[int, int] = (3, 8)
    classmate_relation_type: str = "classmate"

    has_colleague_prob: float = 0.5
    colleague_size_range: Tuple[int, int] = (2, 5)
    colleague_relation_type: str = "colleague"

    has_staff_customer_prob: float = 0.4
    has_doctor_patient_prob: float = 0.3

    stranger_ratio: float = 0.05
    intensity_scale: float = 1.0


@dataclass
class SceneConfig:
    """场景参数配置：决定一个人群场景的全部特性"""
    scene_name: str = "custom_scene"
    description: str = "用户自定义场景"
    random_seed: Optional[int] = None
    total_persons: int = 80

    profile_ratios: Dict[str, float] = field(default_factory=lambda: {
        "student": 0.8,
        "teacher": 0.1,
        "staff": 0.1,
    })

    group_config: GroupConfig = field(default_factory=GroupConfig)
    relation_intensity: float = 0.7
    enable_directional_override: bool = True
    auto_assign_single_groups: bool = True


# ============================================================
# 中文字段别名映射（用户可使用中文键名）
# ============================================================

FIELD_ALIASES = {
    "scene_name": ["场景名", "场景名称"],
    "description": ["描述", "说明"],
    "total_persons": ["总人数", "人数", "人员数量"],
    "relation_intensity": ["关系强度", "关系紧密程度", "亲密程度"],
    "random_seed": ["随机种子", "种子"],
    "profile_ratios": ["角色比例", "人员比例", "人群分布"],
    "group_config": ["群体配置", "群组配置"],
    "has_family_prob": ["家庭组概率", "家庭概率"],
    "family_size_range": ["家庭组大小", "家庭人数"],
    "has_friend_prob": ["朋友组概率", "朋友概率"],
    "friend_size_range": ["朋友组大小", "朋友人数"],
    "has_classmate_prob": ["同学组概率", "同学概率"],
    "classmate_size_range": ["同学组大小", "同学人数"],
    "has_colleague_prob": ["同事组概率", "同事概率"],
    "colleague_size_range": ["同事组大小", "同事人数"],
    "has_staff_customer_prob": ["员工顾客概率", "员工-顾客概率"],
    "has_doctor_patient_prob": ["医生病人概率", "医生-病人概率"],
    "stranger_ratio": ["陌生人比例", "陌生人占比"],
}


def normalize_field_name(key: str) -> str:
    """将中文字段别名转换为标准字段名"""
    for standard, aliases in FIELD_ALIASES.items():
        if key == standard or key in aliases:
            return standard
    return key


def normalize_profile_ratios(ratios: dict) -> dict:
    """将中文角色名转换为标准角色名"""
    mapping = {
        "学生": "student", "老师": "teacher", "教师": "teacher",
        "顾客": "customer", "员工": "staff", "保安": "security",
        "病人": "patient", "医生": "doctor",
        "家属": "family_member", "家人": "family_member",
        "儿童": "child", "小孩": "child",
        "老人": "elderly", "老年人": "elderly",
    }
    result = {}
    for key, value in ratios.items():
        result[mapping.get(key, key)] = value
    return result


# ============================================================
# 场景预设
# ============================================================

PRESET_SCENES = {
    "classroom": SceneConfig(
        scene_name="classroom",
        total_persons=40,
        profile_ratios={"student": 0.9, "teacher": 0.1},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.5,
            friend_size_range=(2, 4),
            has_classmate_prob=0.95,
            classmate_size_range=(3, 8),
            has_colleague_prob=0.0,
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.6,
    ),
    "shop": SceneConfig(
        scene_name="shop",
        total_persons=50,
        profile_ratios={
            "customer": 0.55,
            "staff": 0.25,
            "security": 0.05,
            "child": 0.08,
            "elderly": 0.07,
        },
        group_config=GroupConfig(
            has_family_prob=0.7,
            has_friend_prob=0.6,
            friend_size_range=(2, 4),
            has_classmate_prob=0.0,
            has_colleague_prob=0.8,
            has_staff_customer_prob=0.9,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.5,
    ),
    "hospital": SceneConfig(
        scene_name="hospital",
        total_persons=35,
        profile_ratios={
            "patient": 0.35,
            "family_member": 0.20,
            "doctor": 0.15,
            "staff": 0.20,
            "security": 0.10,
        },
        group_config=GroupConfig(
            has_family_prob=0.8,
            has_friend_prob=0.0,
            has_classmate_prob=0.0,
            has_colleague_prob=0.7,
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.9,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.7,
    ),
    "canteen": SceneConfig(
        scene_name="canteen",
        total_persons=45,
        profile_ratios={"student": 0.8, "staff": 0.15, "teacher": 0.05},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.6,
            friend_size_range=(2, 4),
            has_classmate_prob=0.5,
            has_colleague_prob=0.5,
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.5,
    ),
    "corridor": SceneConfig(
        scene_name="corridor",
        total_persons=30,
        profile_ratios={"student": 0.7, "teacher": 0.2, "staff": 0.1},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.5,
            friend_size_range=(2, 3),
            has_classmate_prob=0.4,
            has_colleague_prob=0.5,
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.1,
        ),
        relation_intensity=0.3,
    ),
    "dorm": SceneConfig(
        scene_name="dorm",
        total_persons=48,
        profile_ratios={"student": 1.0},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.98,
            friend_size_range=(6, 6),
            has_classmate_prob=0.0,
            has_colleague_prob=0.0,
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.0,
        ),
        relation_intensity=0.8,
    ),
}


# ============================================================
# 场景配置生成器
# ============================================================

class SceneConfigGenerator:
    @staticmethod
    def get_preset(scene_name: str) -> SceneConfig:
        if scene_name not in PRESET_SCENES:
            raise ValueError(f"未知场景: {scene_name}，可用场景: {list(PRESET_SCENES.keys())}")
        return copy.deepcopy(PRESET_SCENES[scene_name])

    @staticmethod
    def get_all_preset_names() -> List[str]:
        return list(PRESET_SCENES.keys())

    @staticmethod
    def create_custom_config(
        total_persons: int = 80,
        profile_ratios: Dict[str, float] = None,
        has_family_prob: float = 0.6,
        family_size_range: Tuple[int, int] = (2, 5),
        has_friend_prob: float = 0.7,
        friend_size_range: Tuple[int, int] = (2, 6),
        has_classmate_prob: float = 0.6,
        classmate_size_range: Tuple[int, int] = (3, 8),
        has_colleague_prob: float = 0.5,
        colleague_size_range: Tuple[int, int] = (2, 5),
        has_staff_customer_prob: float = 0.4,
        has_doctor_patient_prob: float = 0.3,
        stranger_ratio: float = 0.05,
        relation_intensity: float = 0.7,
        random_seed: Optional[int] = None,
    ) -> SceneConfig:
        if profile_ratios is None:
            profile_ratios = {"student": 0.8, "teacher": 0.1, "staff": 0.1}

        total = sum(profile_ratios.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"角色比例之和应为1.0，当前为{total}")

        group_config = GroupConfig(
            has_family_prob=has_family_prob,
            family_size_range=family_size_range,
            has_friend_prob=has_friend_prob,
            friend_size_range=friend_size_range,
            has_classmate_prob=has_classmate_prob,
            classmate_size_range=classmate_size_range,
            has_colleague_prob=has_colleague_prob,
            colleague_size_range=colleague_size_range,
            has_staff_customer_prob=has_staff_customer_prob,
            has_doctor_patient_prob=has_doctor_patient_prob,
            stranger_ratio=stranger_ratio,
        )

        return SceneConfig(
            scene_name="custom",
            total_persons=total_persons,
            profile_ratios=profile_ratios,
            group_config=group_config,
            relation_intensity=relation_intensity,
            random_seed=random_seed,
        )

    @staticmethod
    def random_variant(base_scene: str, variation: float = 0.2) -> SceneConfig:
        base_config = SceneConfigGenerator.get_preset(base_scene)
        if variation > 0:
            delta = int(base_config.total_persons * variation * np.random.uniform(-1, 1))
            base_config.total_persons = max(10, base_config.total_persons + delta)
            base_config.relation_intensity = max(
                0.1,
                min(1.0, base_config.relation_intensity + np.random.uniform(-variation, variation)),
            )
            gc = base_config.group_config
            gc.has_family_prob = max(0, min(1, gc.has_family_prob + np.random.uniform(-variation, variation)))
            gc.has_friend_prob = max(0, min(1, gc.has_friend_prob + np.random.uniform(-variation, variation)))
            gc.has_classmate_prob = max(0, min(1, gc.has_classmate_prob + np.random.uniform(-variation, variation)))
            gc.has_colleague_prob = max(0, min(1, gc.has_colleague_prob + np.random.uniform(-variation, variation)))
        base_config.random_seed = np.random.randint(0, 100000)
        return base_config

    @staticmethod
    def load_config_from_yaml(filepath: str) -> SceneConfig:
        try:
            import yaml
        except ImportError:
            raise ImportError("需要安装 pyyaml: pip install pyyaml")

        try:
            with open(filepath, "r", encoding="utf-8") as file_handle:
                data = yaml.safe_load(file_handle)
        except FileNotFoundError:
            raise FileNotFoundError(f"配置文件不存在: {filepath}")
        except Exception as e:
            raise ValueError(f"YAML 解析失败: {e}")

        return SceneConfigGenerator._dict_to_config(data)

    @staticmethod
    def load_config_from_json(filepath: str) -> SceneConfig:
        try:
            with open(filepath, "r", encoding="utf-8") as json_handle:
                data = json.load(json_handle)
        except FileNotFoundError:
            raise FileNotFoundError(f"配置文件不存在: {filepath}")
        except Exception as e:
            raise ValueError(f"JSON 解析失败: {e}")
        return SceneConfigGenerator._dict_to_config(data)

    @staticmethod
    def load_config_from_dict(data: dict) -> SceneConfig:
        return SceneConfigGenerator._dict_to_config(data)

    @staticmethod
    def _dict_to_config(data: dict) -> SceneConfig:
        if data is None:
            raise ValueError("配置数据为空")

        normalized = {}
        for key, value in data.items():
            normalized[normalize_field_name(key)] = value

        scene_name = normalized.get("scene_name", "custom_scene")
        description = normalized.get("description", "")
        total_persons = int(normalized.get("total_persons", 80))
        relation_intensity = float(normalized.get("relation_intensity", 0.7))

        random_seed = normalized.get("random_seed", None)
        if isinstance(random_seed, (int, str)):
            random_seed = int(random_seed)
        else:
            random_seed = None

        profile_ratios = normalized.get("profile_ratios", {"student": 0.8, "teacher": 0.1, "staff": 0.1})
        if isinstance(profile_ratios, dict):
            profile_ratios = normalize_profile_ratios(profile_ratios)
            total = sum(profile_ratios.values())
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"角色比例之和应为1.0，当前为{total}")

        gc_data = normalized.get("group_config", {})

        # 修复：确保 family_size_range 类型为 Tuple[int, int]
        family_size_range_raw = gc_data.get("family_size_range", [2, 5])
        if isinstance(family_size_range_raw, (list, tuple)) and len(family_size_range_raw) >= 2:
            family_size_range = (int(family_size_range_raw[0]), int(family_size_range_raw[1]))
        else:
            family_size_range = (2, 5)

        friend_size_range_raw = gc_data.get("friend_size_range", [2, 6])
        if isinstance(friend_size_range_raw, (list, tuple)) and len(friend_size_range_raw) >= 2:
            friend_size_range = (int(friend_size_range_raw[0]), int(friend_size_range_raw[1]))
        else:
            friend_size_range = (2, 6)

        classmate_size_range_raw = gc_data.get("classmate_size_range", [3, 8])
        if isinstance(classmate_size_range_raw, (list, tuple)) and len(classmate_size_range_raw) >= 2:
            classmate_size_range = (int(classmate_size_range_raw[0]), int(classmate_size_range_raw[1]))
        else:
            classmate_size_range = (3, 8)

        colleague_size_range_raw = gc_data.get("colleague_size_range", [2, 5])
        if isinstance(colleague_size_range_raw, (list, tuple)) and len(colleague_size_range_raw) >= 2:
            colleague_size_range = (int(colleague_size_range_raw[0]), int(colleague_size_range_raw[1]))
        else:
            colleague_size_range = (2, 5)

        group_config = GroupConfig(
            has_family_prob=float(gc_data.get("has_family_prob", 0.6)),
            family_size_range=family_size_range,
            has_friend_prob=float(gc_data.get("has_friend_prob", 0.7)),
            friend_size_range=friend_size_range,
            has_classmate_prob=float(gc_data.get("has_classmate_prob", 0.6)),
            classmate_size_range=classmate_size_range,
            has_colleague_prob=float(gc_data.get("has_colleague_prob", 0.5)),
            colleague_size_range=colleague_size_range,
            has_staff_customer_prob=float(gc_data.get("has_staff_customer_prob", 0.4)),
            has_doctor_patient_prob=float(gc_data.get("has_doctor_patient_prob", 0.3)),
            stranger_ratio=float(gc_data.get("stranger_ratio", 0.05)),
            intensity_scale=float(gc_data.get("intensity_scale", 1.0)),
        )

        return SceneConfig(
            scene_name=scene_name,
            description=description,
            total_persons=total_persons,
            profile_ratios=profile_ratios,
            group_config=group_config,
            relation_intensity=relation_intensity,
            random_seed=random_seed,
        )


# ============================================================
# 便捷函数
# ============================================================

def load_scene_config(filepath: str) -> SceneConfig:
    if filepath.endswith((".yaml", ".yml")):
        return SceneConfigGenerator.load_config_from_yaml(filepath)
    elif filepath.endswith(".json"):
        return SceneConfigGenerator.load_config_from_json(filepath)
    else:
        raise ValueError(f"不支持的文件格式: {filepath}")


# ============================================================
# 配置模板字符串
# ============================================================

CONFIG_TEMPLATE = """
# ============================================================
# 行人疏散仿真平台 - 场景配置文件
# ============================================================

scene_name: "my_classroom"
total_persons: 40
relation_intensity: 0.6
random_seed: 42

profile_ratios:
  student: 0.9
  teacher: 0.1

group_config:
  has_family_prob: 0.0
  family_size_range: [2, 5]
  has_friend_prob: 0.5
  friend_size_range: [2, 4]
  has_classmate_prob: 0.95
  classmate_size_range: [3, 8]
  has_colleague_prob: 0.0
  colleague_size_range: [2, 5]
  has_staff_customer_prob: 0.0
  has_doctor_patient_prob: 0.0
  stranger_ratio: 0.05
"""


# ============================================================
# 默认配置（模块加载时自动从 YAML 加载）
# ============================================================

_CONFIG_FILE_PATH = "config_template.yaml"

try:
    DEFAULT_CONFIG = SceneConfigGenerator.load_config_from_yaml(_CONFIG_FILE_PATH)
    print(f"✅ 自动加载 YAML 配置成功: {_CONFIG_FILE_PATH}")
except FileNotFoundError:
    print(f"⚠️ 未找到 {_CONFIG_FILE_PATH}，使用预设场景 'classroom'")
    DEFAULT_CONFIG = SceneConfigGenerator.get_preset("classroom")
except Exception as e:
    print(f"⚠️ YAML 加载失败: {e}，使用预设场景 'classroom'")
    DEFAULT_CONFIG = SceneConfigGenerator.get_preset("classroom")


# ============================================================
# 直接运行入口
# ============================================================

if __name__ == "__main__":

    def convert_numpy(obj):
        """递归将 numpy 类型转换为 Python 原生类型"""
        if isinstance(obj, dict):
            return {key: convert_numpy(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [convert_numpy(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(convert_numpy(item) for item in obj)
        elif isinstance(obj, (np.integer, np.int64, np.int32)):
            return int(obj)
        elif isinstance(obj, (np.floating, np.float64, np.float32)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, np.bool_):
            return bool(obj)
        else:
            return obj

    print("=" * 70)
    print("  C11 场景配置生成器")
    print("=" * 70)

    yaml_file = "config_template.yaml"
    if len(sys.argv) > 1:
        yaml_file = sys.argv[1]

    print(f"\n📂 读取配置: {yaml_file}")

    # ✅ 修复：先加载配置
    try:
        config = SceneConfigGenerator.load_config_from_yaml(yaml_file)
        print("✅ 配置加载成功")
        print(f"   场景名称: {config.scene_name}")
        print(f"   总人数: {config.total_persons}")
        print(f"   角色比例: {config.profile_ratios}")
        print(f"   关系紧密程度: {config.relation_intensity}")
        print(f"   随机种子: {config.random_seed}")
    except FileNotFoundError:
        print(f"⚠️ 未找到 {yaml_file}，使用预设场景 'classroom'")
        config = SceneConfigGenerator.get_preset("classroom")
    except Exception as e:
        print(f"❌ 配置加载失败: {e}")
        sys.exit(1)

    
   
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    print("\n🔨 生成社会关系图...")
    try:
        from social.social_graph import SocialGraphBuilder
        builder = SocialGraphBuilder.from_config(config)
        graph, persons = builder.build_with_config()
        builder.print_summary()

        output_data = {
            "metadata": convert_numpy(builder.summary()),
            "persons": [convert_numpy(p.to_dict()) for p in persons.values()],
            "relations": convert_numpy(builder.export_relations()),
        }
        with open("output_people.json", "w", encoding="utf-8") as json_file:
            json.dump(output_data, json_file, indent=2, ensure_ascii=False)
        print(f"\n✅ 已导出到 output_people.json")

    except Exception as e:
        print(f"❌ 生成失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 70)
    print("✅ 完成！")
