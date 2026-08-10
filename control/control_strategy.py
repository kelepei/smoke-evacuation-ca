"""
C10: 管控策略模块 (control_strategy.py)
实现四种管控策略对疏散过程的影响：
    1. 出口关闭（Exit Closure）：动态关闭/开启特定出口
    2. 区域封锁（Zone Lockdown）：封锁特定区域，禁止行人进入
    3. 分区疏散（Zoned Evacuation）：将地图分区，每区分配指定出口
    4. 路线管控（Route Control）：动态调整路径选择权重

核心机制：
    比较不同管控策略下的疏散效率

职责：
    1. 管理四种管控策略的开关和参数
    2. 提供查询接口给B组（出口可用性、区域可通行性、路径权重）
    3. 提供统计接口给D组（策略效果对比）
    4. 通过配置文件驱动策略组合

依赖:
    - C03: SocialGraphBuilder (获取行人、出口信息)
    - C04-C09: 所有C组模块 (协同工作)
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Set, Any
from collections import defaultdict
from enum import Enum
import json


# ============================================================
# 管控策略类型
# ============================================================
class ControlStrategyType(Enum):
    EXIT_CLOSURE = "exit_closure"
    ZONE_LOCKDOWN = "zone_lockdown"
    ZONED_EVACUATION = "zoned_evacuation"
    ROUTE_CONTROL = "route_control"


# ============================================================
# 管控策略参数
# ============================================================
CONTROL_PARAMS = {
    "exit_closure": {
        "enabled": False,
        "closed_exits": [],
        "trigger_time": None,
        "reopen_time": None,
        "reopen_after_evacuation": True,
    },
    "zone_lockdown": {
        "enabled": False,
        "locked_zones": [],
        "lockdown_trigger": "manual",
        "smoke_threshold": 0.5,
    },
    "zoned_evacuation": {
        "enabled": False,
        "zones": [],
        "zone_map": None,
    },
    "route_control": {
        "enabled": False,
        "controlled_routes": [],
        "route_weights": {},
    },
}


class ControlStrategyEngine:
    """管控策略引擎"""

    def __init__(self, social_graph, info_engine,
                 grid_width: int, grid_height: int,
                 exit_pos_dict: Dict[str, Tuple[int, int]] = None): 
        self.graph = social_graph
        self.persons = social_graph.persons
        self.info_engine = info_engine
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.exit_positions = exit_pos_dict or {}  # 内部属性保持原名

        self.exit_closure_params = CONTROL_PARAMS["exit_closure"]
        self.zone_lockdown_params = CONTROL_PARAMS["zone_lockdown"]
        self.zoned_evacuation_params = CONTROL_PARAMS["zoned_evacuation"]
        self.route_control_params = CONTROL_PARAMS["route_control"]

        self.active_strategies: Set[ControlStrategyType] = set()
        self.closure_status: Dict[str, bool] = {}
        self.lockdown_status: Dict[Tuple[int, int], bool] = {}
        self.zone_assignment: Dict[int, str] = {}
        self.route_weight_map: Dict[Tuple[int, int, int, int], float] = {}
        self.zone_distribution: Dict[str, int] = {} 

        self.step_stats = defaultdict(int)
        self.strategy_log = []

    # ============================================================
    # 策略配置
    # ============================================================

    def enable_strategy(self, ctrl_strategy: ControlStrategyType, config: dict = None):  
        self.active_strategies.add(ctrl_strategy)
        if config:
            self._update_strategy_config(ctrl_strategy, config)
        self._log_event(f"策略启用: {ctrl_strategy.value}", config)

    def disable_strategy(self, ctrl_strategy: ControlStrategyType):  
        self.active_strategies.discard(ctrl_strategy)
        self._log_event(f"策略禁用: {ctrl_strategy.value}")

    def _update_strategy_config(self, ctrl_strategy: ControlStrategyType, config: dict):
        if ctrl_strategy == ControlStrategyType.EXIT_CLOSURE:
            self.exit_closure_params.update(config)
            for exit_id in self.exit_closure_params.get("closed_exits", []):
                self.closure_status[exit_id] = True
        elif ctrl_strategy == ControlStrategyType.ZONE_LOCKDOWN:
            self.zone_lockdown_params.update(config)
            self._init_lockdown_status()
        elif ctrl_strategy == ControlStrategyType.ZONED_EVACUATION:
            self.zoned_evacuation_params.update(config)
            self._init_zone_assignment()
        elif ctrl_strategy == ControlStrategyType.ROUTE_CONTROL:
            self.route_control_params.update(config)
            self._init_route_weights()

    # ============================================================
    # 策略初始化
    # ============================================================

    def _init_lockdown_status(self):
        locked_zones = self.zone_lockdown_params.get("locked_zones", [])
        for x1, y1, x2, y2 in locked_zones:
            for x in range(x1, x2 + 1):
                for y in range(y1, y2 + 1):
                    self.lockdown_status[(x, y)] = True

    def _init_zone_assignment(self):
        zones = self.zoned_evacuation_params.get("zones", [])
        for zone in zones:
           
            exit_id = zone["exit_id"]
            for x, y in zone.get("cells", []):
                for pid, person in self.persons.items():
                    if int(person.x) == x and int(person.y) == y:
                        self.zone_assignment[pid] = exit_id
                        break

    def _init_route_weights(self):
        controlled_routes = self.route_control_params.get("controlled_routes", [])
        for from_cell, to_cell, weight in controlled_routes:
            key = (from_cell[0], from_cell[1], to_cell[0], to_cell[1])
            self.route_weight_map[key] = weight

    # ============================================================
    # 每步更新
    # ============================================================

    def update_all(self, all_persons: List, current_step: int,
                   smoke_grid: Optional[np.ndarray] = None) -> Dict[str, Any]:
        self.step_stats = defaultdict(int)

        if ControlStrategyType.EXIT_CLOSURE in self.active_strategies:
            self._update_exit_closure(all_persons, current_step)

        if ControlStrategyType.ZONE_LOCKDOWN in self.active_strategies:
            self._update_zone_lockdown(all_persons, smoke_grid, current_step)

        if ControlStrategyType.ZONED_EVACUATION in self.active_strategies:
            self._update_zoned_evacuation(all_persons, current_step)

        if ControlStrategyType.ROUTE_CONTROL in self.active_strategies:
            self._update_route_control(all_persons, current_step)

        return {
            "active_strategies": [s.value for s in self.active_strategies],
            "closed_exits": self._get_closed_exits(),
            "locked_cells": len(self.lockdown_status),
            "assigned_persons": len(self.zone_assignment),
            "route_weights": len(self.route_weight_map),
            "stats": dict(self.step_stats),
        }

    # ============================================================
    # 1. 出口关闭策略
    # ============================================================

    def _update_exit_closure(self, all_persons: List, current_step: int):
        trigger_time = self.exit_closure_params.get("trigger_time")
        reopen_time = self.exit_closure_params.get("reopen_time")
        reopen_after_evacuation = self.exit_closure_params.get("reopen_after_evacuation", True)

        if trigger_time is not None and current_step >= trigger_time:
            for exit_id in self.exit_closure_params.get("closed_exits", []):
                self.closure_status[exit_id] = True
                self.step_stats["exits_closed"] += 1

        if reopen_time is not None and current_step >= reopen_time:
            for exit_id in self.exit_closure_params.get("closed_exits", []):
                self.closure_status[exit_id] = False
                self.step_stats["exits_reopened"] += 1

        if reopen_after_evacuation:
            remaining = sum(1 for p in all_persons if not p.evacuated)
            if remaining == 0:
                for exit_id in list(self.closure_status.keys()):
                    self.closure_status[exit_id] = False

        for person in all_persons:
            if hasattr(person, 'target_exit'):
                if self.closure_status.get(person.target_exit, False):
                    self._reassign_exit(person)

    def _reassign_exit(self, person):
        available_exits = [
            eid for eid, closed in self.closure_status.items()
            if not closed
        ]
        if not available_exits:
            return

        best_exit = None
        best_dist = float('inf')
        for eid in available_exits:
            exit_pos = self.exit_positions.get(eid)
            if exit_pos:
                dist = ((person.x - exit_pos[0]) ** 2 + (person.y - exit_pos[1]) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_exit = eid

        if best_exit:
            person.target_exit = best_exit

    # ============================================================
    # 2. 区域封锁策略
    # ============================================================

    def _update_zone_lockdown(self, all_persons: List,
                              smoke_grid: Optional[np.ndarray],
                              current_step: int):
        trigger = self.zone_lockdown_params.get("lockdown_trigger", "manual")

        if trigger == "smoke_threshold" and smoke_grid is not None:
            threshold = self.zone_lockdown_params.get("smoke_threshold", 0.5)
            for (x, y), _ in list(self.lockdown_status.items()):
                if 0 <= x < smoke_grid.shape[1] and 0 <= y < smoke_grid.shape[0]:
                    if float(smoke_grid[y, x]) > threshold:
                        self.lockdown_status[(x, y)] = True
                    else:
                        if self.zone_lockdown_params.get("auto_unlock", False):
                            self.lockdown_status[(x, y)] = False

        locked_in = 0
        for person in all_persons:
            if self.lockdown_status.get((int(person.x), int(person.y)), False):
                locked_in += 1
                if hasattr(person, '_locked_alert') and not person._locked_alert:
                    person._locked_alert = True
                    self.info_engine.transition_state(
                        person.id,
                        self.info_engine.str_to_enum("ALERTED"),
                        current_step,
                        source=-4,
                        method="lockdown"
                    )

        self.step_stats["locked_persons"] = locked_in

    # ============================================================
    # 3. 分区疏散策略
    # ============================================================

    def _update_zoned_evacuation(self, all_persons: List, _current_step: int):  
        for person in all_persons:
            pid = person.id
            if pid in self.zone_assignment:
                assigned_exit = self.zone_assignment[pid]
                if not self.closure_status.get(assigned_exit, False):
                    person.target_exit = assigned_exit
                    self.step_stats["zoned_assigned"] += 1

        exit_counts = defaultdict(int)
        for pid, exit_id in self.zone_assignment.items():
            exit_counts[exit_id] += 1
        
        self.zone_distribution = dict(exit_counts)

    # ============================================================
    # 4. 路线管控策略
    # ============================================================

    def _update_route_control(self, _all_persons: List, _current_step: int):  
        for (fx, fy, tx, ty), weight in list(self.route_weight_map.items()):
            if self.lockdown_status.get((tx, ty), False):
                self.route_weight_map[(fx, fy, tx, ty)] = weight * 1.5
            else:
                self.route_weight_map[(fx, fy, tx, ty)] = weight

        self.step_stats["route_controlled"] = len(self.route_weight_map)

    # ============================================================
    # 给B组的查询接口
    # ============================================================

    def is_exit_open(self, exit_id: str) -> bool:
        return not self.closure_status.get(exit_id, False)

    def get_open_exits(self) -> List[str]:
        return [eid for eid, closed in self.closure_status.items() if not closed]

    def is_cell_accessible(self, x: int, y: int) -> bool:
        return not self.lockdown_status.get((x, y), False)

    def get_assigned_exit(self, person_id: int) -> Optional[str]:
        return self.zone_assignment.get(person_id)

    def get_route_weight(self, from_cell: Tuple[int, int],
                         to_cell: Tuple[int, int]) -> float:
        key = (from_cell[0], from_cell[1], to_cell[0], to_cell[1])
        return self.route_weight_map.get(key, 1.0)

    def get_path_modifier(self, from_cell: Tuple[int, int],
                          to_cell: Tuple[int, int]) -> float:
        if not self.is_cell_accessible(to_cell[0], to_cell[1]):
            return 0.0
        return self.get_route_weight(from_cell, to_cell)

    # ============================================================
    # 给D组的查询接口
    # ============================================================

    def _get_closed_exits(self) -> List[str]:
        return [eid for eid, closed in self.closure_status.items() if closed]

    def get_strategy_status(self) -> dict:
        return {
            "active_strategies": [s.value for s in self.active_strategies],
            "closed_exits": self._get_closed_exits(),
            "locked_zones": len(self.lockdown_status),
            "assigned_zones": len(set(self.zone_assignment.values())),
            "controlled_routes": len(self.route_weight_map),
        }

    def get_statistics(self) -> dict:
        return {
            "total_strategies": len(self.active_strategies),
            "strategy_list": [s.value for s in self.active_strategies],
            "exits_closed": self.step_stats.get("exits_closed", 0),
            "exits_reopened": self.step_stats.get("exits_reopened", 0),
            "locked_persons": self.step_stats.get("locked_persons", 0),
            "zoned_assigned": self.step_stats.get("zoned_assigned", 0),
            "zone_distribution": self.zone_distribution,  
            "route_controlled": self.step_stats.get("route_controlled", 0),
            "strategy_log": self.strategy_log[-20:],
        }

    def get_control_map(self, grid_width: int, grid_height: int) -> np.ndarray:
        control_map = np.zeros((grid_height, grid_width))

        for (x, y), locked in self.lockdown_status.items():
            if locked and 0 <= x < grid_width and 0 <= y < grid_height:
                control_map[y, x] = 1

        for (fx, fy, tx, ty), weight in self.route_weight_map.items():
            if weight != 1.0:
                if 0 <= tx < grid_width and 0 <= ty < grid_height:
                    if control_map[ty, tx] == 0:
                        control_map[ty, tx] = 0.5

        return control_map

    def _log_event(self, event: str, details: dict = None):
        self.strategy_log.append({
            "event": event,
            "details": details or {},
            "step": len(self.strategy_log),
        })


# ============================================================
# 便捷函数
# ============================================================
def create_control_engine(social_graph, info_engine,
                          grid_width: int, grid_height: int,
                          exit_pos_dict: Dict[str, Tuple[int, int]] = None) -> ControlStrategyEngine:  
    return ControlStrategyEngine(social_graph, info_engine, grid_width, grid_height, exit_pos_dict)


# ============================================================
# 配置加载工具
# ============================================================
def load_strategy_config(config_path: str) -> dict:
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ============================================================
# 演示
# ============================================================
if __name__ == "__main__":
    print("C10: 管控策略模块 (control_strategy.py)")

    # 模拟依赖对象
    class DummySocialGraph:
        persons = {}

    class DummyInfoEngine:
        @staticmethod
        def transition_state(_pid, _state, _step, _source, _method): 
            return True

        @staticmethod
        def str_to_enum(s):
            return s

    dummy_graph = DummySocialGraph()
    dummy_info = DummyInfoEngine()

    exit_pos_map = {"exit_01": (10, 50), "exit_02": (90, 50)}

    engine = create_control_engine(dummy_graph, dummy_info, 100, 100, exit_pos_map)

    print("\n四种管控策略:")
    for strategy in ControlStrategyType:
        print(f"  {strategy.value}")

    print("\n策略配置示例:")
    print(json.dumps({
        "exit_closure": {"enabled": True, "closed_exits": ["exit_01"], "trigger_time": 30},
        "zone_lockdown": {"enabled": True, "locked_zones": [[10, 10, 20, 20]]},
        "zoned_evacuation": {"enabled": True, "zones": []},
        "route_control": {"enabled": True, "controlled_routes": []}
    }, indent=2))

    print("\n使用方式:")
    print("  from control_strategy import create_control_engine, ControlStrategyType")
    print("  engine = create_control_engine(social_graph, info_engine, grid_w, grid_h, exit_pos_map)")
    print("  engine.enable_strategy(ControlStrategyType.EXIT_CLOSURE, {'closed_exits': ['exit_01']})")
    print("  status = engine.update_all(all_persons, current_step)")
    print("  is_open = engine.is_exit_open('exit_01')")
