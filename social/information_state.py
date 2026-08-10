"""
C06: 信息状态模块 (information_state.py)
管理行人的信息状态及状态转换。

信息状态（任务书第5.5节）:
    UNKNOWN     - 未知：未收到任何疏散信息
    ALERTED     - 已警觉：收到警报但未确认
    CONFIRMED   - 已确认：确认了疏散信息（看到烟雾或收到多次确认）
    MISINFORMED - 被误导：收到了错误信息
    GUIDED      - 被引导：受到引导员或指示牌影响

职责：
    1. 定义信息状态枚举
    2. 存储每个行人的当前状态
    3. 管理状态转换的合法性
    4. 记录信息来源历史（最近来源 + 完整来源链）
    5. 提供查询接口（给 C07 和 B 组使用）
    6. 提供统计接口（给 D 组使用）

注意：信息传播的具体实现（广播、口头传播等）在 C07 中实现。
"""

from enum import Enum
from typing import List, Optional, Tuple


# ============================================================
# 信息状态枚举
# ============================================================
class InfoState(Enum):
    UNKNOWN = "UNKNOWN"
    ALERTED = "ALERTED"
    CONFIRMED = "CONFIRMED"
    MISINFORMED = "MISINFORMED"
    GUIDED = "GUIDED"


# 状态转换图：允许的状态转换
STATE_TRANSITIONS = {
    InfoState.UNKNOWN: {InfoState.ALERTED, InfoState.GUIDED},
    InfoState.ALERTED: {InfoState.CONFIRMED, InfoState.MISINFORMED, InfoState.GUIDED},
    InfoState.CONFIRMED: {InfoState.GUIDED, InfoState.MISINFORMED},
    InfoState.MISINFORMED: {InfoState.CONFIRMED, InfoState.GUIDED, InfoState.ALERTED},
    InfoState.GUIDED: {InfoState.CONFIRMED, InfoState.ALERTED},
}


class InformationStateEngine:
    """
    信息状态管理引擎（纯状态容器）
    只负责状态的存储、查询和转换管理，不包含任何传播逻辑
    """

    def __init__(self, social_graph):
        """
        :param social_graph: C03 构建的 SocialGraphBuilder 实例
        """
        self.graph = social_graph
        self.persons = social_graph.persons

        self.person_states = {}
        self.state_history = []

    def initialize_person(self, person_id: int):
        """初始化行人的信息状态"""
        if person_id not in self.person_states:
            self.person_states[person_id] = {
                "state": InfoState.UNKNOWN,
                "confidence": 0.0,
                "receive_step": -1,
                "info_source": None,
                "info_source_history": [],
                "info_age": 0,
            }

    def transition_state(self, person_id: int, new_state: InfoState,
                         current_step: int, source: Optional[int] = None,
                         method: str = "unknown") -> bool:
        """
        执行状态转换（由C07调用）
        """
        if person_id not in self.person_states:
            self.initialize_person(person_id)

        old_state = self.person_states[person_id]["state"]

        if old_state == new_state:
            return True

        if new_state not in STATE_TRANSITIONS.get(old_state, set()):
            return False

        self.person_states[person_id]["state"] = new_state
        self.person_states[person_id]["confidence"] = 1.0 if new_state in [
            InfoState.CONFIRMED, InfoState.GUIDED
        ] else 0.5

        if source is not None:
            self.person_states[person_id]["info_source"] = source
            self.person_states[person_id]["info_source_history"].append(
                (current_step, source, method, new_state.value)
            )

        if self.person_states[person_id]["receive_step"] == -1:
            self.person_states[person_id]["receive_step"] = current_step

        self.person_states[person_id]["info_age"] = current_step - self.person_states[person_id]["receive_step"]

        self.state_history.append({
            "step": current_step,
            "person_id": person_id,
            "from_state": old_state.value,
            "to_state": new_state.value,
            "source": source,
            "method": method,
        })

        return True

    def update_info_age(self, person_id: int, current_step: int):
        if person_id in self.person_states:
            receive_step = self.person_states[person_id]["receive_step"]
            if receive_step != -1:
                self.person_states[person_id]["info_age"] = current_step - receive_step

    def update_all_ages(self, all_persons: List, current_step: int):
        for person in all_persons:
            self.update_info_age(person.id, current_step)

    # ============================================================
    # 状态优先级（供C07使用）
    # ============================================================
    @staticmethod
    def state_priority(info_state: InfoState) -> int:  
        priorities = {
            InfoState.UNKNOWN: 0,
            InfoState.ALERTED: 1,
            InfoState.MISINFORMED: 2,
            InfoState.CONFIRMED: 3,
            InfoState.GUIDED: 4,
        }
        return priorities.get(info_state, 0)

    @staticmethod
    def state_priority_str(state_str: str) -> int:
        priorities = {
            "UNKNOWN": 0,
            "ALERTED": 1,
            "MISINFORMED": 2,
            "CONFIRMED": 3,
            "GUIDED": 4,
        }
        return priorities.get(state_str, 0)

    @staticmethod
    def str_to_enum(state_str: str) -> InfoState:
        mapping = {
            "UNKNOWN": InfoState.UNKNOWN,
            "ALERTED": InfoState.ALERTED,
            "CONFIRMED": InfoState.CONFIRMED,
            "MISINFORMED": InfoState.MISINFORMED,
            "GUIDED": InfoState.GUIDED,
        }
        return mapping.get(state_str, InfoState.UNKNOWN)

    # ============================================================
    # 给B组和C07的查询接口
    # ============================================================
    def get_state(self, person_id: int) -> InfoState:
        return self.person_states.get(person_id, {}).get("state", InfoState.UNKNOWN)

    def get_state_value(self, person_id: int) -> str:
        return self.get_state(person_id).value

    def get_confidence(self, person_id: int) -> float:
        return self.person_states.get(person_id, {}).get("confidence", 0.0)

    def get_receive_step(self, person_id: int) -> int:
        return self.person_states.get(person_id, {}).get("receive_step", -1)

    def get_info_age(self, person_id: int) -> int:
        return self.person_states.get(person_id, {}).get("info_age", 0)

    def get_info_source(self, person_id: int) -> Optional[int]:
        return self.person_states.get(person_id, {}).get("info_source", None)

    def get_info_source_history(self, person_id: int) -> List[Tuple[int, Optional[int], str, str]]:
        return self.person_states.get(person_id, {}).get("info_source_history", [])

    def is_informed(self, person_id: int) -> bool:
        return self.get_state(person_id) != InfoState.UNKNOWN

    def is_misinformed(self, person_id: int) -> bool:
        return self.get_state(person_id) == InfoState.MISINFORMED

    def is_confirmed(self, person_id: int) -> bool:
        return self.get_state(person_id) == InfoState.CONFIRMED

    def is_guided(self, person_id: int) -> bool:
        return self.get_state(person_id) == InfoState.GUIDED

    # ============================================================
    # 给D组的统计接口
    # ============================================================
    def get_statistics(self) -> dict:
        state_counts = {s.value: 0 for s in InfoState}
        total_confidence = 0
        informed_count = 0
        total_people = len(self.person_states)

        for data in self.person_states.values():
            current_state = data["state"]  
            state_counts[current_state.value] += 1
            total_confidence += data["confidence"]
            if current_state != InfoState.UNKNOWN:
                informed_count += 1

        history_lengths = [len(data.get("info_source_history", [])) for data in self.person_states.values()]
        avg_history_len = sum(history_lengths) / len(history_lengths) if history_lengths else 0

        return {
            "total_persons": total_people,
            "state_counts": state_counts,
            "informed_count": informed_count,
            "informed_ratio": informed_count / total_people if total_people > 0 else 0,
            "avg_confidence": total_confidence / total_people if total_people > 0 else 0,
            "total_transitions": len(self.state_history),
            "avg_source_history_len": avg_history_len,
        }

    def get_state_history(self) -> List[dict]:
        return self.state_history

    def get_state_for_person(self, person_id: int) -> dict:
        if person_id not in self.person_states:
            self.initialize_person(person_id)
        data = self.person_states[person_id]
        return {
            "state": data["state"].value,
            "confidence": data["confidence"],
            "receive_step": data["receive_step"],
            "info_age": data["info_age"],
            "info_source": data["info_source"],
            "info_source_history": data["info_source_history"],
        }


# ============================================================
# 便捷函数
# ============================================================
def create_info_state_engine(social_graph) -> InformationStateEngine:
    return InformationStateEngine(social_graph)


if __name__ == "__main__":
    print("C06: 信息状态模块 (information_state.py)")
    print("\n信息状态:")
    for state in InfoState:
        print(f"  {state.value}")
    print("\n状态转换图:")
    for state, targets in STATE_TRANSITIONS.items():
        print(f"  {state.value} → {[t.value for t in targets]}")
    print("\n新增功能: info_source_history 记录完整来源链")
    print("使用方式:")
    print("  from information_state import create_info_state_engine")
    print("  info_engine = create_info_state_engine(social_graph)")
    print("  info_engine.transition_state(pid, InfoState.ALERTED, step, source=5, method='word_of_mouth')")
    print("  source = info_engine.get_info_source(pid)               # 最近来源")
    print("  history = info_engine.get_info_source_history(pid)      # 完整来源链")
