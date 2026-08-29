"""
C04: 结伴行为模块 (group_behavior.py)
强关系人群（family/friend）的结伴行为：
    1. 跟随（following）：主动跟随特定同伴移动，同伴走哪我跟哪
    2. 等待（waiting）：强关系成员放慢速度或原地等待，直到同伴跟上
    3. 靠近（approaching）：主动向落单或偏离的同伴移动
    4. 共同出口（co-exit）：强关系群体倾向于选择同一个出口

依赖:
    - C03: SocialGraphBuilder (查询关系、群组成员)
    - B组: 行人位置、移动方向、出口选择

输出给B组:
    - 跟随状态: is_following, follow_target, follow_strength
    - 等待标志: is_waiting, waiting_for
    - 靠近目标: approach_target
    - 出口偏好修正: exit_preference

输出给D组:
    - 结伴日志: 等待次数、同伴分离次数、共同出口比例 
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import Counter


# ============================================================
# 结伴行为参数（全部集中在此，无需外部YAML文件）
# ============================================================
GROUP_PARAMS = {
    # 强关系类型（任务书5.5节）
    "strong_relation_types": ["family", "friend"],

    # ====== 跟随行为（主动跟特定同伴） ======
    "follow": {
        "follow_range": 3.0,            # 触发跟随的最大距离（格，0.5m/格 → 1.5m）
        "follow_strength_scale": 1.0,   # 跟随强度缩放系数
        "min_follow_prob": 0.4,         # 最低跟随概率阈值
    },

    # ====== 等待行为 ======
    "wait": {
        "max_wait_steps": 10,           # 最大等待步数（10步 = 5秒）
        "wait_trigger_dist": 3.0,       # 同伴偏离超过3格触发等待
        "wait_abandon_dist": 6.0,       # 同伴偏离超过6格放弃等待
    },

    # ====== 靠近行为 ======
    "approach": {
        "max_speed_ratio": 0.8,         # 靠近时速度上限为正常速度的80%
        "approach_trigger_dist": 4.0,   # 偏离组中心超过4格触发靠近
        "min_approach_dist": 1.0,       # 靠近到1格内视为已会合
    },

    # ====== 共同出口 ======
    "co_exit": {
        "min_group_size": 2,            # 至少2人才能触发共同出口
        "majority_ratio": 0.5,          # 半数以上选择同一出口才触发
        "preference_bonus": 0.3,        # 出口偏好修正值（加到B组效用公式）
    },

    # ====== 组分散容差 ======
    "max_group_spread": 4.0,            # 组内最大可接受分散距离（格）
}


class GroupBehaviorEngine:
    """
    强关系结伴行为引擎
    在仿真每步中，为每个强关系成员计算结伴状态
    """

    def __init__(self, social_graph):
        """
        :param social_graph: C03 构建的 SocialGraphBuilder 实例
        """
        self.graph = social_graph
        self.persons = social_graph.persons

        # 从 GROUP_PARAMS 加载配置
        self.strong_relations = GROUP_PARAMS["strong_relation_types"]
        self.follow_params = GROUP_PARAMS["follow"]
        self.wait_params = GROUP_PARAMS["wait"]
        self.approach_params = GROUP_PARAMS["approach"]
        self.co_exit_params = GROUP_PARAMS["co_exit"]
        self.max_group_spread = GROUP_PARAMS["max_group_spread"]

        # 每步的状态缓存
        self.person_states = {}  # {person_id: 结伴状态}

    def update_all(self, all_persons, current_step: int) -> Dict[int, dict]:
        """
        更新所有行人的结伴状态

        兼容两种输入：
            - List[Person]：行人列表
            - Dict[int, Person]：{person_id: person}（main.py 的 ped_dict 形式）
        社会图中存在、但不在 all_persons 里的行人会被跳过，
        避免 C 生成人数与 A/B 实际行人数不一致时崩溃。
        """
        # 统一转为 id -> person 查询表
        if isinstance(all_persons, dict):
            all_persons = list(all_persons.values())
        persons_by_id = {p.id: p for p in all_persons}

        results = {}
        groups = self._group_by_group_id()

        for group_id, member_ids in groups.items():
            if len(member_ids) < 2:
                for pid in member_ids:
                    if pid in persons_by_id:
                        results[pid] = self._no_behavior()
                continue

            active_members = []
            for pid in member_ids:
                person = persons_by_id.get(pid)
                if person is not None and not person.evacuated:
                    active_members.append(person)

            if len(active_members) < 2:
                for pid in member_ids:
                    if pid in persons_by_id:
                        results[pid] = self._no_behavior()
                continue

            center_x = np.mean([p.x for p in active_members])
            center_y = np.mean([p.y for p in active_members])

            for person in active_members:
                result = self._calc_person_behavior(
                    person, active_members, center_x, center_y, current_step
                )
                results[person.id] = result

        # 补充未分组或已撤离的行人
        for person in all_persons:
            pid = person.id
            if pid not in results:
                results[pid] = self._no_behavior()

        self.person_states = results
        return results

    def _group_by_group_id(self) -> Dict[str, List[int]]:
        """按 group_id 分组"""
        groups = {}
        for pid, person in self.persons.items():
            gid = person.group_id
            if gid is None:
                gid = f"single_{pid}"
            if gid not in groups:
                groups[gid] = []
            groups[gid].append(pid)
        return groups

    def _calc_person_behavior(self, person, active_members, center_x, center_y, current_step):
        pid = person.id

        result = {
            "is_following": False,
            "follow_target": None,
            "follow_strength": 0.0,
            "is_waiting": False,
            "waiting_for": None,
            "wait_start_step": None,
            "approach_target": None,
            "exit_preference": {},
            "group_center_x": center_x,
            "group_center_y": center_y,
        }

        # 检查此人是否属于强关系组
        if not self._has_strong_relation(pid):
            return result

        other_members = [p for p in active_members if p.id != pid]

        if not other_members:
            return result

        # 计算到组中心的距离
        dist_to_center = self._distance(person, (center_x, center_y))

        # ============================================================
        # 优先级 1：靠近行为（自己偏离组中心太远 → 主动归队）
        # ============================================================
        if dist_to_center > self.approach_params["approach_trigger_dist"]:
            nearest = min(other_members, key=lambda p: self._distance(person, p))
            result["approach_target"] = nearest.id
            result["is_waiting"] = False
            result["is_following"] = False
            return result

        # ============================================================
        # 优先级 2：跟随行为（有强关系同伴在附近 → 跟着走）
        # ============================================================
        # 找附近的所有强关系同伴
        strong_nearby = []
        for other in other_members:
            rel = self.graph.get_relation(pid, other.id)
            if rel["relation_type"] in self.strong_relations:
                dist = self._distance(person, other)
                follow_prob = rel.get("follow_probability", 0.5)
                if dist <= self.follow_params["follow_range"]:
                    strong_nearby.append((other, dist, follow_prob, rel))

        if strong_nearby:
            # 按距离排序，选最近的同伴
            strong_nearby.sort(key=lambda x: x[1])
            nearest, dist, follow_prob, rel = strong_nearby[0]

            if follow_prob >= self.follow_params["min_follow_prob"]:
                result["is_following"] = True
                result["follow_target"] = nearest.id
                # 跟随强度 = 跟随概率 × 距离衰减 × 缩放系数
                dist_decay = 1.0 - (dist / (self.follow_params["follow_range"] + 1))
                result["follow_strength"] = min(1.0, (
                    follow_prob *
                    dist_decay *
                    self.follow_params["follow_strength_scale"]
                ))
                # 跟随状态下，不触发等待和靠近
                result["is_waiting"] = False
                result["approach_target"] = None
                # 共同出口偏好也会被跟随目标影响
                if hasattr(nearest, 'target_exit') and nearest.target_exit:
                    result["exit_preference"][nearest.target_exit] = (
                        self.co_exit_params["preference_bonus"] * 0.5
                    )
                return result

        # ============================================================
        # 优先级 3：等待行为（检查是否有同伴掉队）
        # ============================================================
        # 计算每个成员到组中心的距离
        all_dists = [self._distance(p, (center_x, center_y)) for p in active_members]
        avg_dist = np.mean(all_dists) if all_dists else 0

        for other in other_members:
            other_dist = self._distance(other, (center_x, center_y))
            # 如果同伴距离组中心超过平均值2倍且超过触发阈值
            if other_dist > avg_dist * 2.0 and other_dist > self.wait_params["wait_trigger_dist"]:
                # 检查是否已经等待太久
                prev_state = self.person_states.get(pid, {})
                if prev_state.get("is_waiting") and prev_state.get("waiting_for") == other.id:
                    wait_steps = current_step - prev_state.get("wait_start_step", current_step)
                    if wait_steps > self.wait_params["max_wait_steps"]:
                        # 等待超时，放弃等待
                        continue
                    # 仍在等待：保留原始开始步数，确保超时判断有效
                    wait_start_step = prev_state.get("wait_start_step", current_step)
                else:
                    # 新开始等待
                    wait_start_step = current_step
                result["is_waiting"] = True
                result["waiting_for"] = other.id
                result["wait_start_step"] = wait_start_step
                result["approach_target"] = None
                result["is_following"] = False
                break

        # ============================================================
        # 优先级 4：共同出口偏好（组内多数人选同一出口）
        # ============================================================
        exit_counts = Counter()
        for p in active_members:
            if hasattr(p, "target_exit") and p.target_exit:
                exit_counts[p.target_exit] += 1

        if exit_counts:
            most_common_exit, count = exit_counts.most_common(1)[0]
            majority_ratio = self.co_exit_params["majority_ratio"]
            group_size = max(1, len(active_members))
            if (count >= self.co_exit_params["min_group_size"]
                    and count / group_size >= majority_ratio):
                bonus = self.co_exit_params["preference_bonus"]
                result["exit_preference"][most_common_exit] = bonus

        return result

    def _has_strong_relation(self, person_id: int) -> bool:
        """检查此人是否有强关系"""
        for neighbor_id in self.graph.graph.neighbors(person_id):
            rel = self.graph.get_relation(person_id, neighbor_id)
            if rel["relation_type"] in self.strong_relations:
                return True
        return False

    @staticmethod
    def _distance(p1, p2) -> float:
        """计算两点距离"""
        if hasattr(p1, 'x'):
            x1, y1 = p1.x, p1.y
        else:
            x1, y1 = p1[0], p1[1]
        if hasattr(p2, 'x'):
            x2, y2 = p2.x, p2.y
        else:
            x2, y2 = p2[0], p2[1]
        return ((x1 - x2) ** 2 + (y1 - y2) ** 2) ** 0.5

    @staticmethod
    def _no_behavior() -> dict:
        return {
            "is_following": False,
            "follow_target": None,
            "follow_strength": 0.0,
            "is_waiting": False,
            "waiting_for": None,
            "wait_start_step": None,
            "approach_target": None,
            "exit_preference": {},
            "group_center_x": None,
            "group_center_y": None,
        }

    # ============================================================
    # 给B组的查询接口
    # ============================================================
    def get_follow_status(self, person_id: int) -> Tuple[bool, Optional[int], float]:
        """查询某人是否在跟随，跟随谁，跟随强度"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["is_following"], state["follow_target"], state["follow_strength"]

    def get_waiting_status(self, person_id: int) -> Tuple[bool, Optional[int]]:
        """查询某人是否在等待，等待谁"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["is_waiting"], state["waiting_for"]

    def get_approach_target(self, person_id: int) -> Optional[int]:
        """查询某人想要靠近的目标"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["approach_target"]

    def get_exit_preference(self, person_id: int) -> Dict[str, float]:
        """查询某人的出口偏好修正"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["exit_preference"]

    def get_group_center(self, person_id: int) -> Tuple[Optional[float], Optional[float]]:
        """查询某人所在组的中心"""
        state = self.person_states.get(person_id, self._no_behavior())
        return state["group_center_x"], state["group_center_y"]

    # ============================================================
    # 给D组的统计接口
    # ============================================================
    def get_statistics(self) -> dict:
        """获取结伴行为统计数据"""
        total = len(self.person_states)
        following = sum(1 for s in self.person_states.values() if s["is_following"])
        waiting = sum(1 for s in self.person_states.values() if s["is_waiting"])
        approaching = sum(1 for s in self.person_states.values() if s["approach_target"] is not None)

        return {
            "total_persons": total,
            "following_persons": following,
            "waiting_persons": waiting,
            "approaching_persons": approaching,
            "following_ratio": following / total if total > 0 else 0,
            "waiting_ratio": waiting / total if total > 0 else 0,
            "approaching_ratio": approaching / total if total > 0 else 0,
        }


# ============================================================
# 便捷函数
# ============================================================
def create_group_behavior_engine(social_graph):
    """创建结伴行为引擎"""
    return GroupBehaviorEngine(social_graph)


# ============================================================
# 演示
# ============================================================
if __name__ == "__main__":
    print("C04: 结伴行为模块 (group_behavior.py)")
    print("\n参数配置:")
    for key, value in GROUP_PARAMS.items():
        print(f"  {key}: {value}")
    print("\n四种结伴行为:")
    print("  1. 跟随 (following) - 主动跟特定同伴走")
    print("  2. 等待 (waiting) - 同伴掉队时原地等待")
    print("  3. 靠近 (approaching) - 偏离组中心时主动归队")
    print("  4. 共同出口 (co-exit) - 组内选择同一出口")
    print("\n使用方式:")
    print("  from group_behavior import create_group_behavior_engine")
    print("  engine = create_group_behavior_engine(social_graph)")
    print("  states = engine.update_all(all_persons, current_step)")
    print("  is_following, target, strength = engine.get_follow_status(person_id)")
