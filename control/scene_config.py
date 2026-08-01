"""
C11: 场景参数配置模块 (scene_config.py)
提供用户可调的人群生成参数，实现不同场景下灵活的人群配置。

设计理念：
    1. 用户通过一组参数决定场景特性
    2. 结合随机生成，每次可产生不同的人群配置
    3. 同一个地图可以对应多组不同的人群

用户可调参数：
    - total_persons: 总人数
    - profile_ratios: 各角色比例
    - group_config: 群体配置（概率、大小范围、关系类型）
    - relation_intensity: 关系紧密程度
    - random_seed: 随机种子（保证可复现）

使用方式：
    config = SceneConfig(
        total_persons=100,
        profile_ratios={"student": 0.85, "teacher": 0.15},
        group_config=GroupConfig(
            has_family_prob=0.3,      # 30%概率有家庭组
            family_size_range=(2, 5),
            has_friend_prob=0.5,      # 50%概率有朋友组
            friend_size_range=(2, 6),
        ),
        relation_intensity=0.8,
        random_seed=42
    )
    builder = SocialGraphBuilder.from_config(config)
"""

import copy
import json
import numpy as np
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

# ============================================================
# 数据类定义
# ============================================================


@dataclass
class GroupConfig:
    """群体配置：定义各种类型群体的生成参数"""

    # === 各类群体独立概率 ===
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

    # 不对称关系（工作关系）
    has_staff_customer_prob: float = 0.4
    has_doctor_patient_prob: float = 0.3

    # 陌生人比例
    stranger_ratio: float = 0.05
    intensity_scale: float = 1.0


@dataclass
class SceneConfig:
    """场景参数配置：决定一个人群场景的全部特性"""

    # 基础信息
    scene_name: str = "custom_scene"
    description: str = "用户自定义场景"
    random_seed: Optional[int] = None

    # 人数
    total_persons: int = 80

    # 角色比例（必须和为1.0）
    profile_ratios: Dict[str, float] = field(default_factory=lambda: {
        "student": 0.8,
        "teacher": 0.1,
        "staff": 0.1,
    })

    # 群体配置
    group_config: GroupConfig = field(default_factory=GroupConfig)

    # 关系紧密程度 (0-1)
    # 影响所有关系的 strength 和 trust 的整体水平
    relation_intensity: float = 0.7

    # 是否启用方向性不对称覆盖（C03中的DIRECTIONAL_OVERRIDE）
    enable_directional_override: bool = True

    # 是否自动分配孤立者为单人组
    auto_assign_single_groups: bool = True


# ============================================================
# 场景预设
# ============================================================

PRESET_SCENES = {
    "classroom": SceneConfig(
        scene_name="classroom",
        description="教室场景：学生为主，少量教师",
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
        description="商场场景：顾客、员工、保安、老人小孩",
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
            family_size_range=(2, 5),
            has_friend_prob=0.6,
            friend_size_range=(2, 4),
            has_classmate_prob=0.0,
            has_colleague_prob=0.8,
            colleague_size_range=(2, 5),
            has_staff_customer_prob=0.9,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.5,
    ),
    "hospital": SceneConfig(
        scene_name="hospital",
        description="医院场景：病人、家属、医生、员工、保安",
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
        description="食堂场景：学生为主，少量教职工",
        total_persons=45,
        profile_ratios={"student": 0.8, "staff": 0.15, "teacher": 0.05},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.6,
            friend_size_range=(2, 4),
            has_classmate_prob=0.5,
            classmate_size_range=(2, 4),
            has_colleague_prob=0.5,
            colleague_size_range=(2, 4),
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.05,
        ),
        relation_intensity=0.5,
    ),
    "corridor": SceneConfig(
        scene_name="corridor",
        description="走廊场景：混合人群",
        total_persons=30,
        profile_ratios={"student": 0.7, "teacher": 0.2, "staff": 0.1},
        group_config=GroupConfig(
            has_family_prob=0.0,
            has_friend_prob=0.5,
            friend_size_range=(2, 3),
            has_classmate_prob=0.4,
            classmate_size_range=(2, 3),
            has_colleague_prob=0.5,
            colleague_size_range=(2, 3),
            has_staff_customer_prob=0.0,
            has_doctor_patient_prob=0.0,
            stranger_ratio=0.1,
        ),
        relation_intensity=0.3,
    ),
    "dorm": SceneConfig(
        scene_name="dorm",
        description="宿舍场景：6人间室友",
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
    """
    场景配置生成器：根据用户参数生成完整的场景配置
    支持：
        1. 使用预设场景
        2. 用户自定义参数
        3. 随机生成变体
        4. 从 YAML/JSON 文件加载
    """

    @staticmethod
    def get_preset(scene_name: str) -> SceneConfig:
        """获取预设场景配置"""
        if scene_name not in PRESET_SCENES:
            raise ValueError(
                f"未知场景: {scene_name}，可用场景: {list(PRESET_SCENES.keys())}"
            )
        return copy.deepcopy(PRESET_SCENES[scene_name])

    @staticmethod
    def get_all_preset_names() -> List[str]:
        """获取所有预设场景名称"""
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
        """用户自定义配置"""
        if profile_ratios is None:
            profile_ratios = {"student": 0.8, "teacher": 0.1, "staff": 0.1}

        # 验证比例和为1.0
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
            description="用户自定义场景",
            total_persons=total_persons,
            profile_ratios=profile_ratios,
            group_config=group_config,
            relation_intensity=relation_intensity,
            random_seed=random_seed,
        )

    @staticmethod
    def random_variant(base_scene: str, variation: float = 0.2) -> SceneConfig:
        """
        基于预设场景生成随机变体
        variation: 变异程度 (0-1)，控制参数偏移幅度
        """
        config = SceneConfigGenerator.get_preset(base_scene)

        if variation > 0:
            # 随机调整人数
            delta = int(config.total_persons * variation * np.random.uniform(-1, 1))
            config.total_persons = max(10, config.total_persons + delta)

            # 随机调整关系强度
            config.relation_intensity = max(
                0.1,
                min(1.0, config.relation_intensity + np.random.uniform(-variation, variation)),
            )

            # 随机调整群体概率
            gc = config.group_config
            gc.has_family_prob = max(
                0, min(1, gc.has_family_prob + np.random.uniform(-variation, variation))
            )
            gc.has_friend_prob = max(
                0, min(1, gc.has_friend_prob + np.random.uniform(-variation, variation))
            )
            gc.has_classmate_prob = max(
                0, min(1, gc.has_classmate_prob + np.random.uniform(-variation, variation))
            )
            gc.has_colleague_prob = max(
                0, min(1, gc.has_colleague_prob + np.random.uniform(-variation, variation))
            )

        # 生成新的随机种子
        config.random_seed = np.random.randint(0, 100000)

        return config

    @staticmethod
    def load_config_from_yaml(filepath: str) -> SceneConfig:
        """
        从 YAML 文件加载场景配置

        YAML 文件格式示例：
        ```yaml
        scene_name: "my_classroom"
        total_persons: 50
        profile_ratios:
          student: 0.85
          teacher: 0.15
        group_config:
          has_friend_prob: 0.5
          has_classmate_prob: 0.9
        relation_intensity: 0.7
        random_seed: 42"""