"""
C02: 社会关系模板 (relation_templates.py)
按场景语义生成人员分配和关系

场景 semantic:
    classroom | corridor | stair | shop | hall | canteen | dorm | library | hospital

Relation 字段:
    relation_type: friend | classmate | family | colleague | stranger | staff_to_customer | doctor_patient
    strength: 0.0 - 1.0
    trust: 0.0 - 1.0
    wait_probability: 0.0 - 1.0
    follow_probability: 0.0 - 1.0
"""

import numpy as np
from typing import List, Tuple 


# ============================================================
# 场景 semantic → 角色比例 + 分组大小 + 人数范围
# ============================================================

SCENE_CONFIG = {
    "classroom": {
        "profiles": {"student": 0.9, "teacher": 0.1},
        "group_size": [2, 8],
        "count": [50, 80],
    },
    "corridor": {
        "profiles": {"student": 0.7, "teacher": 0.2, "staff": 0.1},
        "group_size": [1, 3],
        "count": [50, 70],
    },
    "stair": {
        "profiles": {"student": 0.6, "teacher": 0.2, "staff": 0.2},
        "group_size": [1, 2],
        "count": [50, 80],
    },
    "shop": {
        "profiles": {"customer": 0.7, "staff": 0.2, "security": 0.05, "child": 0.03, "elderly": 0.02},
        "group_size": [1, 5],
        "count": [60, 100],
    },
    "hall": {
        "profiles": {"customer": 0.55, "staff": 0.2, "security": 0.1, "child": 0.08, "elderly": 0.07},
        "group_size": [1, 4],
        "count": [60, 100],
    },
    "canteen": {
        "profiles": {"student": 0.8, "staff": 0.15, "teacher": 0.05},
        "group_size": [1, 4],
        "count": [50, 90],
    },
    "dorm": {
        "profiles": {"student": 1.0},
        "group_size": [2, 6],
        "count": [48, 96],          #  [48, 96]（6人间倍数）
    },
    "library": {
        "profiles": {"student": 0.8, "teacher": 0.1, "staff": 0.1},
        "group_size": [1, 3],
        "count": [50, 80],
    },
    "hospital": {
        "profiles": {"patient": 0.35, "family_member": 0.25, "doctor": 0.15, "staff": 0.15, "security": 0.1},
        "group_size": [1, 3],
        "count": [50, 80],
    },
}

# ============================================================
# 关系默认参数
# ============================================================

RELATION_DEFAULTS = {
    "family": {"strength": 0.95, "trust": 0.95, "wait_probability": 0.98, "follow_probability": 0.90},
    "friend": {"strength": 0.70, "trust": 0.75, "wait_probability": 0.70, "follow_probability": 0.65},
    "classmate": {"strength": 0.50, "trust": 0.60, "wait_probability": 0.50, "follow_probability": 0.55},
    "colleague": {"strength": 0.45, "trust": 0.55, "wait_probability": 0.30, "follow_probability": 0.40},
    "stranger": {"strength": 0.00, "trust": 0.10, "wait_probability": 0.00, "follow_probability": 0.05},
    "staff_to_customer": {"strength": 0.15, "trust": 0.35, "wait_probability": 0.00, "follow_probability": 0.30},
    "doctor_patient": {"strength": 0.30, "trust": 0.75, "wait_probability": 0.05, "follow_probability": 0.50},
}


# ============================================================
# 人员生成（原有）
# ============================================================

def get_scene_count(semantic: str) -> int:
    """返回该场景的随机人数"""
    cfg = SCENE_CONFIG.get(semantic, SCENE_CONFIG["corridor"])
    lo, hi = cfg["count"]
    return np.random.randint(lo, hi + 1)


def generate_profiles(semantic: str, n: int) -> List[str]:
    """给定场景 semantic 和人数，返回角色类型列表"""
    cfg = SCENE_CONFIG.get(semantic, SCENE_CONFIG["corridor"])
    dist = cfg["profiles"]
    types = list(dist.keys())
    probs = np.array(list(dist.values()))
    probs = probs / probs.sum()
    return [str(x) for x in np.random.choice(types, size=n, p=probs)]


def get_group_size_range(semantic: str) -> List[int]:
    """返回该场景的分组大小范围"""
    cfg = SCENE_CONFIG.get(semantic, SCENE_CONFIG["corridor"])
    return cfg["group_size"]


# ============================================================
# 工具函数
# ============================================================

def _add_clique_edges(indices: List[int], relation: str) -> List[Tuple[int, int, str]]:
    """给一组人两两之间添加关系"""
    r = []
    for i in range(len(indices)):
        for j in range(i + 1, len(indices)):
            r.append((indices[i], indices[j], relation))
    return r


def _random_groups(indices: List[int], size_range: Tuple[int, int]) -> List[List[int]]:
    """将索引列表随机分成指定大小范围的组"""
    shuffled = list(indices)
    np.random.shuffle(shuffled)
    lo, hi = size_range
    gs = np.random.randint(lo, hi + 1)
    return [shuffled[i:i + gs] for i in range(0, len(shuffled), gs)]


def _sparse_clique_edges(
        n: int, fraction: float, group_size: Tuple[int, int], relation: str
) -> List[Tuple[int, int, str]]:
    """抽取 fraction 比例的人，随机分组，组内全连接 relation"""
    r = []
    persons = list(range(n))
    np.random.shuffle(persons)
    grouped = persons[:int(n * fraction)]
    for g in _random_groups(grouped, group_size):
        r.extend(_add_clique_edges(g, relation))
    return r



def _filter_by_profile(profiles: List[str], profile_type: str) -> List[int]:
    """
    从 profiles 列表中筛选指定类型的索引列表
    用于替代多处重复的列表推导式
    """
    return [i for i, p in enumerate(profiles) if p == profile_type]


# ============================================================
# 新增：从 SceneConfig 生成角色列表（供 C11 使用）
# ============================================================

def generate_profiles_from_config(scene_config) -> List[str]:
    """
    根据 SceneConfig 生成角色列表
    """
    n = scene_config.total_persons
    dist = scene_config.profile_ratios
    types = list(dist.keys())
    probs = np.array(list(dist.values()))
    probs = probs / probs.sum()
    return [str(x) for x in np.random.choice(types, size=n, p=probs)]


# ============================================================
# 新增：从 SceneConfig 生成关系边（供 C11 使用）
# ============================================================

def _random_sample(excluded: set, n: int, size: int) -> List[int]:
    """
    从 [0, n) 中随机采样 size 个未被排除的索引
    如果可用人数不足 size，返回空列表（不生成该组）
    """
    available = [i for i in range(n) if i not in excluded]
    if len(available) < size:
        return []
    return list(np.random.choice(available, size=size, replace=False))


def generate_relations_from_config(scene_config, profiles: List[str]) -> List[Tuple[int, int, str]]:
    """
    根据 SceneConfig 生成关系边列表
    """
    n = len(profiles)
    group_config = scene_config.group_config
    relations = []
    assigned = set()
    scene_name = getattr(scene_config, 'scene_name', 'unknown')



    def force_sample(assigned_set, total_n, size):
        available = [i for i in range(total_n) if i not in assigned_set]
        if len(available) < size:
            return available
        return list(np.random.choice(available, size=size, replace=False))

    # ===== 1. 家庭组 =====
    if scene_name != "classroom" and np.random.random() < group_config.has_family_prob:
        size_range = group_config.family_size_range
        groups_generated = 0
        while groups_generated < 8:
            family_size = np.random.randint(size_range[0], size_range[1] + 1)
            family_members = force_sample(assigned, n, family_size)
            if len(family_members) >= 2:
                relations.extend(_add_clique_edges(family_members, "family"))
                assigned.update(family_members)
                groups_generated += 1

            else:
                break

    # ===== 2. 朋友组 =====
    if np.random.random() < group_config.has_friend_prob:
        size_range = group_config.friend_size_range
        groups_generated = 0
        while groups_generated < 15:
            friend_size = np.random.randint(size_range[0], size_range[1] + 1)
            friends = force_sample(assigned, n, friend_size)
            if len(friends) >= 2:
                relations.extend(_add_clique_edges(friends, "friend"))
                assigned.update(friends)
                groups_generated += 1

            else:
                break

    # ===== 3. 同学组 =====
    if np.random.random() < group_config.has_classmate_prob:
        size_range = group_config.classmate_size_range
        groups_generated = 0
        max_groups = 20 if scene_name == "classroom" else 10
        while groups_generated < max_groups:
            classmate_size = np.random.randint(size_range[0], size_range[1] + 1)
            classmates = force_sample(assigned, n, classmate_size)
            if len(classmates) >= 2:
                relations.extend(_add_clique_edges(classmates, "classmate"))
                assigned.update(classmates)
                groups_generated += 1

            else:
                break

    # ===== 4. 同事组 =====
    if hasattr(group_config, 'has_colleague_prob') and np.random.random() < group_config.has_colleague_prob:
        size_range = group_config.colleague_size_range
        groups_generated = 0
        while groups_generated < 10:
            colleague_size = np.random.randint(size_range[0], size_range[1] + 1)
            colleagues = force_sample(assigned, n, colleague_size)
            if len(colleagues) >= 2:
                relations.extend(_add_clique_edges(colleagues, "colleague"))
                assigned.update(colleagues)
                groups_generated += 1

            else:
                break

    # ===== 5. 教师-学生关系 =====
    if scene_name == "classroom":
        teacher_ids = _filter_by_profile(profiles, "teacher")
        student_ids = _filter_by_profile(profiles, "student")
        if teacher_ids and student_ids:
            for t in teacher_ids:
                num_students = min(np.random.randint(5, 11), len(student_ids))
                selected_students = np.random.choice(student_ids, size=num_students, replace=False)
                for s in selected_students:
                    relations.append((t, s, "classmate"))
                    relations.append((s, t, "classmate"))


    # ===== 6. 员工-顾客关系 =====
    if hasattr(group_config, 'has_staff_customer_prob') and np.random.random() < group_config.has_staff_customer_prob:
        staff_ids = _filter_by_profile(profiles, "staff")
        customer_ids = _filter_by_profile(profiles, "customer")
        if staff_ids and customer_ids:
            selected_staff = np.random.choice(staff_ids, size=min(3, len(staff_ids)), replace=False)
            selected_customers = np.random.choice(customer_ids, size=min(3, len(customer_ids)), replace=False)
            for s in selected_staff:
                for c in selected_customers:
                    relations.append((s, c, "staff_to_customer"))
                    relations.append((c, s, "staff_to_customer"))


    # ===== 7. 医生-病人关系 =====
    if hasattr(group_config, 'has_doctor_patient_prob') and np.random.random() < group_config.has_doctor_patient_prob:
        doctor_ids = _filter_by_profile(profiles, "doctor")
        patient_ids = _filter_by_profile(profiles, "patient")
        if doctor_ids and patient_ids:
            selected_doctors = np.random.choice(doctor_ids, size=min(3, len(doctor_ids)), replace=False)
            selected_patients = np.random.choice(patient_ids, size=min(3, len(patient_ids)), replace=False)
            for d in selected_doctors:
                for p in selected_patients:
                    relations.append((d, p, "doctor_patient"))
                    relations.append((p, d, "doctor_patient"))



    return relations


# ============================================================
# 原有关系生成器
# ============================================================

class RelationGenerator:
    """根据场景 semantic 和角色列表生成关系"""

    def __init__(self, semantic: str, profiles: List[str]):
        self.semantic = semantic
        self.profiles = profiles
        self.n = len(profiles)

    def generate(self) -> List[Tuple[int, int, str]]:
        rules = {
            "classroom": self._classroom,
            "corridor": lambda: self._sparse_groups(0.3),
            "stair": lambda: self._sparse_groups(0.3),
            "shop": self._shop,
            "hall": self._shop,
            "canteen": self._canteen,
            "dorm": self._dorm,
            "library": lambda: self._sparse_groups(0.4),
            "hospital": self._hospital,
        }
        rule = rules.get(self.semantic, self._generic)
        return rule()

    # ----------------------------------------------------------
    # classroom
    # ----------------------------------------------------------
    def _classroom(self) -> List[Tuple[int, int, str]]:
        r = []
        students = _filter_by_profile(self.profiles, "student")
        teachers = _filter_by_profile(self.profiles, "teacher")

        for g in _random_groups(students, (3, 8)):
            r.extend(_add_clique_edges(g, "classmate"))
            for i in range(len(g)):
                for j in range(i + 1, len(g)):
                    if np.random.random() < 0.3:
                        r.append((g[i], g[j], "friend"))

        for t in teachers:
            for s in students:
                r.append((t, s, "classmate"))

        return r

    # ----------------------------------------------------------
    # corridor / stair / library
    # ----------------------------------------------------------
    def _sparse_groups(self, fraction: float) -> List[Tuple[int, int, str]]:
        return _sparse_clique_edges(self.n, fraction, (3, 3), "classmate")

    # ----------------------------------------------------------
    # shop / hall
    # ----------------------------------------------------------
    def _shop(self) -> List[Tuple[int, int, str]]:
        r = []

        normal = _filter_by_profile(self.profiles, "customer")
        elderly = _filter_by_profile(self.profiles, "elderly")
        children = _filter_by_profile(self.profiles, "child")
        staff = _filter_by_profile(self.profiles, "staff")
        security = _filter_by_profile(self.profiles, "security")

        all_customers = normal + elderly + children
        all_workers = staff + security

        np.random.shuffle(normal)
        idx = 0
        normal_groups = []
        while idx < len(normal):
            gs = np.random.choice([1, 2, 2, 3, 3, 4, 5])
            g = normal[idx:idx + gs]
            idx += gs
            normal_groups.append(g)

        for person in elderly + children:
            if normal_groups:
                g = normal_groups[np.random.randint(0, len(normal_groups))]
                g.append(person)

        for g in normal_groups:
            has_dependent = any(
                self.profiles[i] in ("elderly", "child") for i in g
            )
            rel = "family" if has_dependent else (
                "family" if np.random.random() < 0.3 else "friend"
            )
            r.extend(_add_clique_edges(g, rel))

        r.extend(_add_clique_edges(all_workers, "colleague"))

        for w in all_workers:
            for c in np.random.choice(all_customers, size=min(5, len(all_customers)), replace=False):
                r.append((w, c, "staff_to_customer"))

        return r

    # ----------------------------------------------------------
    # canteen
    # ----------------------------------------------------------
    def _canteen(self) -> List[Tuple[int, int, str]]:
        r = []
        students = _filter_by_profile(self.profiles, "student")
        staff = _filter_by_profile(self.profiles, "staff")

        for g in _random_groups(students, (2, 4)):
            rel = "friend" if np.random.random() < 0.5 else "classmate"
            r.extend(_add_clique_edges(g, rel))

        r.extend(_add_clique_edges(staff, "colleague"))
        return r

    # ----------------------------------------------------------
    # dorm (6人间)
    # ----------------------------------------------------------
    def _dorm(self) -> List[Tuple[int, int, str]]:
        r = []
        persons = list(range(self.n))
        np.random.shuffle(persons)
        rooms = [persons[i:i + 6] for i in range(0, len(persons), 6)]

        for room in rooms:
            r.extend(_add_clique_edges(room, "friend"))

        for ri in range(len(rooms) - 1):
            for _ in range(np.random.randint(1, 3)):
                a = np.random.choice(rooms[ri])
                b = np.random.choice(rooms[ri + 1])
                r.append((a, b, "friend"))

        return r

    # ----------------------------------------------------------
    # hospital
    # ----------------------------------------------------------
    def _hospital(self) -> List[Tuple[int, int, str]]:
        r = []
        patients = _filter_by_profile(self.profiles, "patient")
        family = _filter_by_profile(self.profiles, "family_member")
        doctor = _filter_by_profile(self.profiles, "doctor")
        staff = _filter_by_profile(self.profiles, "staff")
        security = _filter_by_profile(self.profiles, "security")

        all_medical = doctor + staff + security
        all_others = patients + family

        np.random.shuffle(patients)
        np.random.shuffle(family)
        for p, f in zip(patients, family):
            r.append((p, f, "family"))

        for c in doctor:
            n_patients = np.random.randint(2, 5)
            for p in np.random.choice(patients, size=min(n_patients, len(patients)), replace=False):
                r.append((c, p, "doctor_patient"))

        r.extend(_add_clique_edges(all_medical, "colleague"))

        for w in all_medical:
            for o in np.random.choice(all_others, size=min(3, len(all_others)), replace=False):
                r.append((w, o, "staff_to_customer"))

        return r

    # ----------------------------------------------------------
    # generic
    # ----------------------------------------------------------
    def _generic(self) -> List[Tuple[int, int, str]]:
        return _sparse_clique_edges(self.n, 1.0, (2, 4), "classmate")


# ============================================================
# 关系参数生成
# ============================================================

def make_relation(relation_type: str, noise: float = 0.1) -> dict:
    defaults = RELATION_DEFAULTS.get(relation_type, RELATION_DEFAULTS["stranger"])

    def add_noise(v):
        return max(0.0, min(1.0, v + np.random.uniform(-noise, noise)))

    return {
        "relation_type": relation_type,
        "strength": add_noise(defaults["strength"]),
        "trust": add_noise(defaults["trust"]),
        "wait_probability": add_noise(defaults["wait_probability"]),
        "follow_probability": add_noise(defaults["follow_probability"]),
    }


def make_stranger_relation() -> dict:
    return dict(RELATION_DEFAULTS["stranger"])
