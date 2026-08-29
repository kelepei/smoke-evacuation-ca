"""
C07: 信息传播模块 (information_diffusion.py)
实现四种信息传播方式：
    1. 广播（Broadcast）
    2. 局部口头传播（Word of Mouth）
    3. 关系传播（Relation Spread）
    4. 错误信息传播（Misinformation）

职责：
    1. 实现四种传播方式的逻辑
    2. 通过 C06 的 transition_state 接口更新行人状态
    3. 记录传播日志

依赖:
    - C03: SocialGraphBuilder
    - C06: InformationStateEngine
"""

import numpy as np
from typing import Dict, List, Optional, Any
from collections import Counter, defaultdict
from .information_state import InfoState, InformationStateEngine


# ============================================================
# 信息传播参数
# ============================================================
DIFFUSION_PARAMS = {
    "broadcast": {
        "enabled": True,
        "trigger_time": 20,
        "target_state": "ALERTED",
        "source": -1,
        "message": "Fire alarm! Evacuate immediately!",
    },
    "word_of_mouth": {
        "enabled": True,
        "radius": 3,
        "base_prob": 0.3,
        "trust_boost": 0.5,
        "state_required": "ALERTED",
        "max_spread_per_step": 5,
    },
    "relation_spread": {
        "enabled": True,
        "base_prob": 0.2,
        "trust_threshold": 0.3,
        "relation_boost": {
            "family": 0.8,
            "friend": 0.6,
            "classmate": 0.4,
            "colleague": 0.3,
            "stranger": 0.05,
            "staff_to_customer": 0.35,
            "doctor_patient": 0.5,
        },
        "max_spread_per_step": 3,
    },
    "misinformation": {
        "enabled": True,
        "inject": {
            "trigger_time": 25,
            "source": -2,
            "message": "Exit 01 is blocked! Use Exit 02!",
        },
        "spread": {
            "base_prob": 0.4,
        },
        "correction": {
            "enabled": True,
            "decay_steps": 30,
            "correction_prob": 0.05,
            "smoke_confirm_threshold": 0.15,
        },
    },
}


class InformationDiffusionEngine:
    """信息传播引擎"""

    def __init__(self, social_graph, info_engine: InformationStateEngine,
                 grid_width: int, grid_height: int):
        self.graph = social_graph
        self.persons = social_graph.persons
        self.info_engine = info_engine
        self.grid_width = grid_width
        self.grid_height = grid_height

        self.broadcast_params = DIFFUSION_PARAMS["broadcast"]
        self.wom_params = DIFFUSION_PARAMS["word_of_mouth"]
        self.rel_params = DIFFUSION_PARAMS["relation_spread"]
        self.misinfo_params = DIFFUSION_PARAMS["misinformation"]

        self.misinfo_active = False
        inject_data = self.misinfo_params.get("inject", {})
        self.misinfo_inject_time = inject_data.get("trigger_time", 25) if isinstance(inject_data, dict) else 25

        self.propagation_log: List[Dict[str, Any]] = []
        self.step_stats = defaultdict(int)

    def update_all(self, all_persons: List, current_step: int,
                   smoke_grid: Optional[np.ndarray] = None) -> Dict[str, int]:
        """执行所有信息传播方式"""
        self.step_stats = defaultdict(int)

        # 更新信息年龄
        self.info_engine.update_all_ages(all_persons, current_step)

        # 1. 广播
        if self.broadcast_params["enabled"]:
            if current_step == self.broadcast_params["trigger_time"]:
                self._apply_broadcast(all_persons, current_step)

        # 2. 错误信息
        if self.misinfo_params.get("enabled", False):
            inject_data = self.misinfo_params.get("inject", {})
            if isinstance(inject_data, dict) and current_step == inject_data.get("trigger_time"):
                self._inject_misinformation(all_persons, current_step)

            if self.misinfo_active:
                self._spread_misinformation(all_persons, current_step)

            correction_data = self.misinfo_params.get("correction", {})
            if isinstance(correction_data, dict) and correction_data.get("enabled", False):
                self._apply_correction(all_persons, current_step, smoke_grid)

        # 3. 烟雾触发确认
        if smoke_grid is not None:
            self._apply_smoke_confirmation(all_persons, smoke_grid, current_step)

        # 4. 局部口头传播
        if self.wom_params["enabled"]:
            self._apply_word_of_mouth(all_persons, current_step)

        # 5. 关系传播
        if self.rel_params["enabled"]:
            self._apply_relation_spread(all_persons, current_step)

        return dict(self.step_stats)

    # ============================================================
    # 1. 广播
    # ============================================================
    def _apply_broadcast(self, all_persons: List, current_step: int):
        target_state = InfoState.ALERTED
        source = self.broadcast_params["source"]
        message = self.broadcast_params["message"]

        broadcast_count = 0
        for person in all_persons:
            pid = int(person.id)
            if self.info_engine.get_state_value(pid) == "UNKNOWN":
                if self.info_engine.transition_state(
                    pid, target_state, current_step,
                    source=source, method="broadcast"
                ):
                    broadcast_count += 1

        self.step_stats["broadcast"] = broadcast_count
        self._log_propagation(-1, None, current_step, "broadcast", message)

    # ============================================================
    # 2. 局部口头传播
    # ============================================================
    def _apply_word_of_mouth(self, all_persons: List, current_step: int):
        radius = self.wom_params["radius"]
        base_prob = self.wom_params["base_prob"]
        trust_boost = self.wom_params["trust_boost"]
        state_required = self.wom_params["state_required"]
        max_spread = self.wom_params["max_spread_per_step"]

        required_priority = self.info_engine.state_priority_str(state_required)

        # 找出所有可传播信息的人
        spreaders = []
        for person in all_persons:
            pid = int(person.id)
            state = self.info_engine.get_state_value(pid)
            if self.info_engine.state_priority_str(state) >= required_priority:
                if state != "MISINFORMED":
                    spreaders.append(person)

        if not spreaders:
            return

        for spreader in spreaders:
            spreader_id = int(spreader.id)
            spreader_state = self.info_engine.get_state_value(spreader_id)

            targets = []
            for person in all_persons:
                person_id = int(person.id)
                if person_id == spreader_id:
                    continue
                if getattr(person, "evacuated", False):
                    continue

                dist = self._distance(spreader, person)
                if dist <= radius:
                    target_state = self.info_engine.get_state_value(person_id)
                    if self.info_engine.state_priority_str(target_state) < self.info_engine.state_priority_str(spreader_state):
                        targets.append(person)

            if targets:
                np.random.shuffle(targets)
                targets = targets[:max_spread]

                for target in targets:
                    target_id = int(target.id)
                    rel = self.graph.get_relation(spreader_id, target_id)

                    if isinstance(rel, dict):
                        trust = rel.get("trust", 0.1)
                    else:
                        trust = 0.1

                    prob = base_prob + trust * trust_boost
                    prob = min(0.95, prob)

                    if np.random.random() < prob:
                        if self.info_engine.transition_state(
                            target_id,
                            self.info_engine.str_to_enum(spreader_state),
                            current_step,
                            source=spreader_id,
                            method="word_of_mouth"
                        ):
                            self.step_stats["word_of_mouth"] += 1
                            self._log_propagation(spreader_id, target_id, current_step,
                                                  "word_of_mouth", f"口头传播: {spreader_state}")

    # ============================================================
    # 3. 关系传播
    # ============================================================
    def _apply_relation_spread(self, all_persons: List, current_step: int):
        base_prob = self.rel_params["base_prob"]
        trust_threshold = self.rel_params["trust_threshold"]
        relation_boost = self.rel_params.get("relation_boost", {})
        if not isinstance(relation_boost, dict):
            return
        max_spread = self.rel_params["max_spread_per_step"]

        # 找出所有可传播的人（CONFIRMED 或 GUIDED）
        spreaders = []
        for person in all_persons:
            pid = int(person.id)
            state = self.info_engine.get_state_value(pid)
            if state in ["CONFIRMED", "GUIDED"]:
                spreaders.append(person)

        if not spreaders:
            return

        for spreader in spreaders:
            spreader_id = int(spreader.id)
            spreader_state = self.info_engine.get_state_value(spreader_id)

            neighbors = list(self.graph.graph.neighbors(spreader_id))
            if not neighbors:
                continue

            # 只向实际参与仿真的行人传播
            sim_ids = {int(p.id) for p in all_persons}
            neighbors = [n for n in neighbors if n in sim_ids]

            np.random.shuffle(neighbors)
            neighbors = neighbors[:max_spread]

            for neighbor_id in neighbors:
                neighbor_id_int = int(neighbor_id)
                rel = self.graph.get_relation(spreader_id, neighbor_id_int)

                if isinstance(rel, dict):
                    trust = rel.get("trust", 0.1)
                    rel_type = rel.get("relation_type", "stranger")
                else:
                    trust = 0.1
                    rel_type = "stranger"

                if trust < trust_threshold:
                    continue

                boost = relation_boost.get(rel_type, 0.1)
                prob = base_prob + trust * 0.3 + boost * 0.2
                prob = min(0.95, prob)

                target_state = self.info_engine.get_state_value(neighbor_id_int)
                if self.info_engine.state_priority_str(target_state) < self.info_engine.state_priority_str(spreader_state):
                    if np.random.random() < prob:
                        if self.info_engine.transition_state(
                            neighbor_id_int,
                            self.info_engine.str_to_enum(spreader_state),
                            current_step,
                            source=spreader_id,
                            method="relation"
                        ):
                            self.step_stats["relation_spread"] += 1
                            self._log_propagation(spreader_id, neighbor_id_int, current_step,
                                                  "relation", f"关系传播: {rel_type}")

    # ============================================================
    # 4. 错误信息注入
    # ============================================================
    def _inject_misinformation(self, all_persons: List, current_step: int):
        self.misinfo_active = True
        inject_params = self.misinfo_params.get("inject", {})
        if not isinstance(inject_params, dict):
            return

        source = int(inject_params.get("source", -2))
        message = str(inject_params.get("message", ""))

        candidates = [p for p in all_persons if not getattr(p, "evacuated", False)]
        np.random.shuffle(candidates)
        inject_count = max(1, int(len(candidates) * 0.2))

        injected = 0
        for person in candidates[:inject_count]:
            pid = int(person.id)
            state = self.info_engine.get_state_value(pid)
            if state in ["UNKNOWN", "ALERTED"]:
                if self.info_engine.transition_state(
                    pid,
                    InfoState.MISINFORMED,
                    current_step,
                    source=source,
                    method="misinformation_inject"
                ):
                    injected += 1
                    self._log_propagation(source, pid, current_step,
                                          "misinformation_inject", message)

        self.step_stats["misinfo_injected"] = injected

    # ============================================================
    # 错误信息传播
    # ============================================================
    def _spread_misinformation(self, all_persons: List, current_step: int):
        spread_params = self.misinfo_params.get("spread", {})
        if not isinstance(spread_params, dict):
            return

        base_prob = float(spread_params.get("base_prob", 0.4))

        misinformed = [p for p in all_persons if self.info_engine.get_state_value(int(p.id)) == "MISINFORMED"]

        if not misinformed:
            return

        for spreader in misinformed:
            spreader_id = int(spreader.id)

            for person in all_persons:
                person_id = int(person.id)
                if person_id == spreader_id:
                    continue
                if getattr(person, "evacuated", False):
                    continue

                dist = self._distance(spreader, person)
                if dist <= self.wom_params["radius"]:
                    target_state = self.info_engine.get_state_value(person_id)
                    if target_state in ["UNKNOWN", "ALERTED"]:
                        rel = self.graph.get_relation(spreader_id, person_id)
                        if isinstance(rel, dict):
                            trust = rel.get("trust", 0.1)
                        else:
                            trust = 0.1
                        prob = base_prob + trust * 0.2
                        prob = min(0.85, prob)

                        if np.random.random() < prob:
                            if self.info_engine.transition_state(
                                person_id,
                                InfoState.MISINFORMED,
                                current_step,
                                source=spreader_id,
                                method="misinformation_spread"
                            ):
                                self.step_stats["misinfo_spread"] += 1
                                self._log_propagation(spreader_id, person_id, current_step,
                                                      "misinformation_spread", "错误信息传播")

    # ============================================================
    # 错误信息纠正
    # ============================================================
    def _apply_correction(self, all_persons: List, current_step: int,
                          smoke_grid: Optional[np.ndarray] = None):
        correction_params = self.misinfo_params.get("correction", {})
        if not isinstance(correction_params, dict):
            return

        decay_steps = int(correction_params.get("decay_steps", 30))
        correction_prob = float(correction_params.get("correction_prob", 0.05))
        smoke_confirm_threshold = float(correction_params.get("smoke_confirm_threshold", 0.15))

        for person in all_persons:
            pid = int(person.id)
            state = self.info_engine.get_state_value(pid)

            if state != "MISINFORMED":
                continue

            state_data = self.info_engine.person_states.get(pid, {})
            receive_step = state_data.get("receive_step", current_step)
            info_age = current_step - receive_step

            # 方式1: 时间衰减纠正
            if info_age > decay_steps:
                if np.random.random() < correction_prob:
                    if self.info_engine.transition_state(
                        pid,
                        InfoState.ALERTED,
                        current_step,
                        source=None,
                        method="correction_decay"
                    ):
                        self.step_stats["correction_decay"] += 1
                        self._log_propagation(None, pid, current_step,
                                              "correction_decay", "时间衰减纠正")
                        continue

            # 方式2: 看到烟雾纠正
            if smoke_grid is not None:
                x = int(person.x)
                y = int(person.y)
                if 0 <= x < smoke_grid.shape[1] and 0 <= y < smoke_grid.shape[0]:
                    smoke_level = float(smoke_grid[y, x])
                    if smoke_level > smoke_confirm_threshold:
                        if np.random.random() < 0.3:
                            if self.info_engine.transition_state(
                                pid,
                                InfoState.CONFIRMED,
                                current_step,
                                source=-3,
                                method="correction_smoke"
                            ):
                                self.step_stats["correction_smoke"] += 1
                                self._log_propagation(-3, pid, current_step,
                                                      "correction_smoke", "看到烟雾纠正")
                                continue

            # 方式3: 被同伴纠正
            confirmed_friends = []
            for other in all_persons:
                other_id = int(other.id)
                if other_id == pid:
                    continue
                if self.info_engine.get_state_value(other_id) == "CONFIRMED":
                    dist = self._distance(person, other)
                    if dist <= self.wom_params["radius"]:
                        confirmed_friends.append(other)

            if confirmed_friends:
                for friend in confirmed_friends[:3]:
                    friend_id = int(friend.id)
                    rel = self.graph.get_relation(pid, friend_id)
                    if isinstance(rel, dict):
                        trust = rel.get("trust", 0.1)
                    else:
                        trust = 0.1
                    if trust > 0.5 and np.random.random() < 0.2:
                        if self.info_engine.transition_state(
                            pid,
                            InfoState.CONFIRMED,
                            current_step,
                            source=friend_id,
                            method="correction_peer"
                        ):
                            self.step_stats["correction_peer"] += 1
                            self._log_propagation(friend_id, pid, current_step,
                                                  "correction_peer", "同伴纠正")
                            break

    # ============================================================
    # 5. 烟雾触发确认
    # ============================================================
    def _apply_smoke_confirmation(self, all_persons: List,
                                  smoke_grid: np.ndarray, current_step: int):
        smoke_threshold = 0.1
        confirmed = 0

        for person in all_persons:
            pid = int(person.id)
            state = self.info_engine.get_state_value(pid)

            if state in ["CONFIRMED", "GUIDED"]:
                continue

            x = int(person.x)
            y = int(person.y)
            if 0 <= x < smoke_grid.shape[1] and 0 <= y < smoke_grid.shape[0]:
                smoke_level = float(smoke_grid[y, x])
                if smoke_level > smoke_threshold:
                    if state == "UNKNOWN":
                        if self.info_engine.transition_state(
                            pid,
                            InfoState.ALERTED,
                            current_step,
                            source=-3,
                            method="smoke"
                        ):
                            confirmed += 1
                    elif state == "ALERTED":
                        if self.info_engine.transition_state(
                            pid,
                            InfoState.CONFIRMED,
                            current_step,
                            source=-3,
                            method="smoke"
                        ):
                            confirmed += 1

        if confirmed > 0:
            self.step_stats["smoke_confirmed"] = confirmed

    # ============================================================
    # 工具方法
    # ============================================================
    @staticmethod
    def _distance(p1, p2) -> float:
        return ((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2) ** 0.5

    def _log_propagation(self, from_id: Optional[int], to_id: Optional[int],
                         step: int, method: str, message: str = ""):
        self.propagation_log.append({
            "step": step,
            "from_id": from_id,
            "to_id": to_id,
            "method": method,
            "message": message,
        })

    # ============================================================
    # 给D组的查询接口
    # ============================================================
    def get_propagation_log(self) -> List[dict]:
        return self.propagation_log

    def get_propagation_summary(self) -> dict:
        method_counts = Counter()
        for log in self.propagation_log:
            method_counts[log["method"]] += 1

        return {
            "total_propagations": len(self.propagation_log),
            "method_counts": dict(method_counts),
            "misinfo_active": self.misinfo_active,
            "step_stats": dict(self.step_stats),
        }

    def get_misinformation_status(self) -> dict:
        misinformed_count = sum(
            1 for p in self.persons.values()
            if self.info_engine.get_state_value(int(p.id)) == "MISINFORMED"
        )
        return {
            "active": self.misinfo_active,
            "inject_time": self.misinfo_inject_time,
            "misinformed_count": misinformed_count,
        }


# ============================================================
# 便捷函数
# ============================================================
def create_diffusion_engine(social_graph, info_engine,
                            grid_width: int, grid_height: int):
    return InformationDiffusionEngine(
        social_graph, info_engine, grid_width, grid_height
    )
