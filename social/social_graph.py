"""
C03: 关系图生成 (social_graph.py)
基于场景语义生成 NetworkX 社会关系图

依赖:
    - C01: person_profiles.json
    - C02: relation_templates.py
    - C11: scene_config.py (在 control/ 下)

输出:
    - NetworkX DiGraph (有向图)
    - Person 对象字典 (person_id -> Person)
"""

import sys
import os
# 确保项目根目录在 sys.path 中，以便使用绝对导入
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import json
from typing import Dict, List, Tuple
import networkx as nx
import numpy as np

# 导入 C02 模块（同包，相对导入）
from .relation_templates import (
    RelationGenerator,
    make_relation,
    generate_profiles,
    get_scene_count,
)

# [OK]  使用绝对导入（项目根目录下的 control 模块）
from control.scene_config import SceneConfig

# 导入 C02 生成函数（同包，相对导入）
from .relation_templates import generate_profiles_from_config, generate_relations_from_config

STRONG_RELATION_TYPES = ["family", "friend", "classmate", "colleague", "staff_to_customer", "doctor_patient"]
STRONG_RELATION_THRESHOLD = 0.3
# 结伴组类型：C04 结伴行为仅对 family/friend 生效，group_id 也按此划分，
# 避免 classroom 等场景因 classmate 关系把全班连成一个大组
COMPANION_GROUP_TYPES = ["family", "friend"]

# ============================================================
# 方向性关系权重覆盖表
# ============================================================
DIRECTIONAL_OVERRIDE = {
    ("staff", "customer"): {
        "strength": 0.20, "trust": 0.40, "wait_probability": 0.00, "follow_probability": 0.35
    },
    ("customer", "staff"): {
        "strength": 0.10, "trust": 0.20, "wait_probability": 0.00, "follow_probability": 0.05
    },
    ("doctor", "patient"): {
        "strength": 0.35, "trust": 0.80, "wait_probability": 0.05, "follow_probability": 0.60
    },
    ("patient", "doctor"): {
        "strength": 0.15, "trust": 0.50, "wait_probability": 0.00, "follow_probability": 0.10
    },
    ("security", "customer"): {
        "strength": 0.25, "trust": 0.80, "wait_probability": 0.00, "follow_probability": 0.65
    },
    ("security", "staff"): {
        "strength": 0.35, "trust": 0.85, "wait_probability": 0.00, "follow_probability": 0.50
    },
    ("security", "patient"): {
        "strength": 0.30, "trust": 0.85, "wait_probability": 0.00, "follow_probability": 0.70
    },
    ("security", "family_member"): {
        "strength": 0.25, "trust": 0.80, "wait_probability": 0.00, "follow_probability": 0.60
    },
    ("teacher", "student"): {
        "strength": 0.40, "trust": 0.70, "wait_probability": 0.05, "follow_probability": 0.50
    },
    ("student", "teacher"): {
        "strength": 0.30, "trust": 0.60, "wait_probability": 0.00, "follow_probability": 0.30
    },
}


# ============================================================
# Person 类
# ============================================================
class Person:
    __slots__ = (
        "id", "x", "y", "speed", "group_id", "profile",
        "risk_sensitivity", "familiarity", "herding_tendency",
        "target_exit", "info_state", "evacuated", "dose",
        "prev_x", "prev_y", "_locked_alert",
    )

    def __init__(self, pid: int, profile: str, defaults: dict):
        self.id = pid
        self.x = 0
        self.y = 0
        self.speed = defaults["speed"] + np.random.uniform(-0.1, 0.1)
        self.group_id = ""
        self.profile = profile
        self.risk_sensitivity = defaults["risk_sensitivity"]
        self.familiarity = defaults["familiarity"]
        self.herding_tendency = defaults["herding_tendency"]
        self.target_exit = ""
        self.info_state = "UNKNOWN"
        self.evacuated = False
        self.dose = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "speed": self.speed,
            "group_id": self.group_id,
            "profile": self.profile,
            "risk_sensitivity": self.risk_sensitivity,
            "familiarity": self.familiarity,
            "herding_tendency": self.herding_tendency,
            "target_exit": self.target_exit,
            "info_state": self.info_state,
            "evacuated": self.evacuated,
            "dose": self.dose,
        }


# ============================================================
# 社会关系图构建器
# ============================================================
class SocialGraphBuilder:
    """
    构建基于场景语义的社会关系有向图

    原有用法：
        builder = SocialGraphBuilder("classroom", person_count=40, seed=42)
        graph, persons = builder.build()

    新增用法（配合C11场景配置）：
        config = SceneConfigGenerator.get_preset("classroom")
        builder = SocialGraphBuilder.from_config(config)
        graph, persons = builder.build_with_config()
    """

    def __init__(self, semantic: str, person_count: int = None,
                 profiles_json_path: str = None,  # [OK]  改为 None，自动查找
                 seed: int = None):
        """
        初始化 SocialGraphBuilder
        """
        self.semantic = semantic
        self.seed = seed
        if seed is not None:
            np.random.seed(seed)

        # [OK]  修复：自动查找 person_profiles.json 路径
        if profiles_json_path is None:
            # 获取 social_graph.py 所在目录（即 social/）
            current_dir = os.path.dirname(os.path.abspath(__file__))
            # 查找顺序：social/ → 项目根目录 → 当前工作目录
            possible_paths = [
                os.path.join(current_dir, "person_profiles.json"),  # social/person_profiles.json
                os.path.join(os.path.dirname(current_dir), "person_profiles.json"),  # 项目根目录
                "person_profiles.json",  # 当前工作目录
            ]
            found = False
            for path in possible_paths:
                if os.path.exists(path):
                    profiles_json_path = path
                    found = True
                    print(f"[OK]  找到 person_profiles.json: {path}")
                    break
            if not found:
                raise FileNotFoundError(
                    "未找到 person_profiles.json，请确保文件在 social/ 目录下或项目根目录中"
                )

        with open(profiles_json_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        self.person_defaults = config["person_types"]

        # 确定人数
        if person_count is None:
            self.num_persons = get_scene_count(semantic)
        else:
            self.num_persons = person_count

        # 生成角色列表
        self.profiles_list = generate_profiles(semantic, self.num_persons)

        # 内部数据结构
        self.graph = nx.DiGraph()
        self.persons: Dict[int, Person] = {}

        # 场景配置存储（供 from_config 使用）
        self.scene_config = None

    def build(self) -> Tuple[nx.DiGraph, Dict[int, Person]]:
        """原有构建方法，完全保持不变"""
        self._create_persons()
        self._create_relations()
        self._assign_groups()
        return self.graph, self.persons

    # ============================================================
    # 从场景配置构建（类方法）
    # ============================================================
    @classmethod
    def from_config(cls, scene_config: 'SceneConfig', profiles_json_path: str = None):
        """
        从 SceneConfig 构建社会关系图

        使用方式：
            from control.scene_config import SceneConfigGenerator
            config = SceneConfigGenerator.get_preset("classroom")
            builder = SocialGraphBuilder.from_config(config)
            graph, persons = builder.build_with_config()
        """
        builder = cls(
            semantic=scene_config.scene_name,
            person_count=scene_config.total_persons,
            profiles_json_path=profiles_json_path,  # 传入 None，让 __init__ 自动查找
            seed=scene_config.random_seed
        )

        builder.scene_config = scene_config

        from .relation_templates import generate_profiles_from_config
        builder.profiles_list = generate_profiles_from_config(scene_config)
        builder.num_persons = len(builder.profiles_list)

        return builder

    # ============================================================
    # 使用场景配置构建关系
    # ============================================================
    def build_with_config(self) -> Tuple[nx.DiGraph, Dict[int, Person]]:
        """
        使用场景配置构建关系图（不分配位置，x=0, y=0 占位）
        A 组负责根据地图语义分配位置
        """
        if self.scene_config is None:
            return self.build()

        self._create_persons()

        from .relation_templates import generate_relations_from_config
        raw_relations = generate_relations_from_config(
            self.scene_config,
            self.profiles_list
        )

        for u, v, rel_type in raw_relations:
            base_params = make_relation(rel_type)
            params_uv = self._directional_params(u, v, rel_type, base_params)
            self.graph.add_edge(u, v, **params_uv)

            if self.scene_config.enable_directional_override:
                params_vu = self._directional_params(v, u, rel_type, base_params)
            else:
                params_vu = base_params.copy()
                params_vu["relation_type"] = rel_type
            self.graph.add_edge(v, u, **params_vu)

        self._assign_groups()

        return self.graph, self.persons

    # ============================================================
    # 内部方法
    # ============================================================
    def _create_persons(self):
        """创建所有 Person 节点"""
        for i in range(self.num_persons):
            profile = self.profiles_list[i]
            defaults = self.person_defaults[profile]
            person = Person(i, profile, defaults)
            self.persons[i] = person
            self.graph.add_node(i, **person.to_dict())

    def _create_relations(self):
        """生成所有关系边"""
        gen = RelationGenerator(self.semantic, self.profiles_list)
        raw_relations = gen.generate()

        for u, v, rel_type in raw_relations:
            base_params = make_relation(rel_type)
            params_uv = self._directional_params(u, v, rel_type, base_params)
            self.graph.add_edge(u, v, **params_uv)
            params_vu = self._directional_params(v, u, rel_type, base_params)
            self.graph.add_edge(v, u, **params_vu)

    def _directional_params(self, from_id: int, to_id: int,
                            rel_type: str, base_params: dict) -> dict:
        """根据方向覆盖表调整关系参数"""
        from_profile = self.persons[from_id].profile
        to_profile = self.persons[to_id].profile
        key = (from_profile, to_profile)

        if key in DIRECTIONAL_OVERRIDE:
            override = DIRECTIONAL_OVERRIDE[key]
            params = base_params.copy()
            params.update(override)
            params["relation_type"] = rel_type
            return params
        else:
            return base_params

    def _assign_groups(self):
        """分配群组 ID（按强关系小团体 family/friend 划分）"""
        strong_graph = nx.Graph()
        strong_graph.add_nodes_from(range(self.num_persons))

        for u, v, data in self.graph.edges(data=True):
            rel_type = data.get("relation_type", "")
            if (rel_type in COMPANION_GROUP_TYPES
                    and data.get("strength", 0) >= STRONG_RELATION_THRESHOLD):
                strong_graph.add_edge(u, v)

        group_id = 0
        for comp in nx.connected_components(strong_graph):
            group_str = str(group_id)
            for node in comp:
                self.persons[node].group_id = group_str
                self.graph.nodes[node]["group_id"] = group_str
            group_id += 1

        for i in range(self.num_persons):
            if self.persons[i].group_id == "":
                group_str = str(group_id)
                self.persons[i].group_id = group_str
                self.graph.nodes[i]["group_id"] = group_str
                group_id += 1

        self.num_groups = group_id

    # ============================================================
    # 查询与导出接口
    # ============================================================
    def get_relation(self, u: int, v: int) -> dict:
        if self.graph.has_edge(u, v):
            return dict(self.graph[u][v])
        return {
            "relation_type": "stranger",
            "strength": 0.0,
            "trust": 0.1,
            "wait_probability": 0.0,
            "follow_probability": 0.05,
        }

    def get_group_members(self, person_id: int) -> List[int]:
        gid = self.persons[person_id].group_id
        return [pid for pid, p in self.persons.items() if p.group_id == gid]

    def export_persons(self) -> List[dict]:
        return [p.to_dict() for p in self.persons.values()]

    def export_relations(self) -> List[dict]:
        rels = []
        for u, v, data in self.graph.edges(data=True):
            rels.append({"from": u, "to": v, **data})
        return rels

    def summary(self) -> dict:
        edge_types = {}
        for _, _, data in self.graph.edges(data=True):
            rt = data["relation_type"]
            edge_types[rt] = edge_types.get(rt, 0) + 1

        profile_counts = {}
        for p in self.persons.values():
            profile_counts[p.profile] = profile_counts.get(p.profile, 0) + 1

        return {
            "semantic": self.semantic,
            "persons": self.num_persons,
            "directed_edges": self.graph.number_of_edges(),
            "groups": self.num_groups,
            "edge_types": edge_types,
            "profiles": profile_counts,
        }

    def print_summary(self):
        s = self.summary()
        print(f"\n{'='*50}")
        print(f"场景: {s['semantic']} | {s['persons']}人 | "
              f"{s['directed_edges']}条有向边 | {s['groups']}组")
        print(f"角色: {s['profiles']}")
        print(f"关系分布: {s['edge_types']}")
        print(f"{'='*50}")


# ============================================================
# 便捷函数
# ============================================================
def build_social_graph(semantic: str, person_count: int = None,
                       profiles_json: str = None,  # [OK]  改为 None
                       seed: int = None) -> Tuple[nx.DiGraph, Dict[int, Person]]:
    """原有便捷函数"""
    builder = SocialGraphBuilder(semantic, person_count, profiles_json, seed)
    return builder.build()


def build_social_graph_from_config(scene_config, profiles_json_path: str = None):
    """新增便捷函数：从场景配置构建"""
    builder = SocialGraphBuilder.from_config(scene_config, profiles_json_path)
    return builder.build_with_config()
