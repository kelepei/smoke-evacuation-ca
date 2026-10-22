"""
C10: 管控策略模块 (control_strategy.py)

对外提供一个综合管控策略（fire_aware / integrated），内部统一编排：
    1. 出口关闭（Exit Closure）：按触发步关闭指定出口
    2. 区域封锁（Zone Lockdown）：按手动/烟雾阈值封锁区域，只禁入、允许离开
    3. 分区疏散（Zoned Evacuation）：按位置给每个人分配主出口（计划路线）
    4. 路线管控（Route Control）：火灾感知等效距离动态修正目标出口，
       分区出口作为软偏好；同时包含密度分水岭、信息素、弱势优先与避让

旧的四个单独枚举仅保留用于向后兼容和单因素对比实验，不再作为平台主入口。

职责：
    1. 管理综合策略的四个子管控参数
    2. 提供查询接口给B组（出口可用性、区域可通行性、路径权重）
    3. 提供统计接口给D组（各子管控状态与策略效果）
    4. 通过配置文件驱动综合策略

依赖:
    - C03: SocialGraphBuilder (获取行人、出口信息)
    - C04-C09: 所有C组模块 (协同工作)
"""

import numpy as np
from typing import Dict, List, Optional, Tuple, Set, Any
from collections import defaultdict
from enum import Enum
import json

try:
    from .fire_aware_control import FireAwareConfig, FireAwareRouter
except ImportError:  # allows running this file directly as a script
    from fire_aware_control import FireAwareConfig, FireAwareRouter

try:
    from .vision_mapped_astar import VisionAStarConfig, VisionMappedAStarPlanner
except ImportError:
    from vision_mapped_astar import VisionAStarConfig, VisionMappedAStarPlanner

try:
    from .dual_objective_aco import DualObjectiveConfig, DualObjectivePlanner
except ImportError:
    from dual_objective_aco import DualObjectiveConfig, DualObjectivePlanner


# ============================================================
# 管控策略类型
# ============================================================
class ControlStrategyType(Enum):
    EXIT_CLOSURE = "exit_closure"
    ZONE_LOCKDOWN = "zone_lockdown"
    ZONED_EVACUATION = "zoned_evacuation"
    ROUTE_CONTROL = "route_control"
    # Paper-grounded combined strategy: fire hazard field + equivalent
    # distance + density-adaptive pheromone + vulnerable/avoidance priority.
    FIRE_AWARE_ADAPTIVE = "fire_aware_adaptive"
    # Tian et al. (2026): vision/YOLO-mapped grid + time-based improved A*.
    VISION_MAPPED_ASTAR = "vision_mapped_astar"
    # Yang et al. (2026): time-risk dual-objective evacuation routing.
    DUAL_OBJECTIVE_ACO = "dual_objective_aco"


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
    "fire_aware_adaptive": {
        "enabled": False,
        "density_threshold": 0.375,
        "pheromone_radius_m": 3.2,
        "replan_interval_s": 200.0,
        "avoid_probability": 0.5,
        "avoid_threshold": 0.6,
        "smoke_proxy_weight": 3.0,
    },
    "vision_mapped_astar": {
        "enabled": False,
        "replan_interval_s": 30.0,
    },
    "dual_objective_aco": {
        "enabled": False,
        "risk_weight": 0.5,
        "replan_interval_s": 30.0,
    },
}


class ControlStrategyEngine:
    """管控策略引擎"""

    def __init__(self, social_graph, info_engine,
                 grid_width: int, grid_height: int,
                 exit_pos_dict: Dict[str, Tuple[int, int]] = None,
                 grid=None):
        self.graph = social_graph
        self.persons = social_graph.persons
        self.info_engine = info_engine
        self.grid_width = grid_width
        self.grid_height = grid_height
        self.grid = grid
        self.exit_positions = exit_pos_dict or {}  # 内部属性保持原名

        self.exit_closure_params = CONTROL_PARAMS["exit_closure"]
        self.zone_lockdown_params = CONTROL_PARAMS["zone_lockdown"]
        self.zoned_evacuation_params = CONTROL_PARAMS["zoned_evacuation"]
        self.route_control_params = CONTROL_PARAMS["route_control"]
        self.fire_aware_params = dict(CONTROL_PARAMS["fire_aware_adaptive"])

        self.active_strategies: Set[ControlStrategyType] = set()
        self.closure_status: Dict[str, bool] = {}
        self.lockdown_status: Dict[Tuple[int, int], bool] = {}
        self.zone_assignment: Dict[int, str] = {}
        self.route_weight_map: Dict[Tuple[int, int, int, int], float] = {}
        self.route_weight_base: Dict[Tuple[int, int, int, int], float] = {}
        self.zone_distribution: Dict[str, int] = {}
        self.route_distribution: Dict[str, int] = {}
        # 路线管控：按出口负载均衡后给每个人指派的出口
        self.route_target: Dict[int, str] = {}
        # 论文驱动的新管控路由器（火灾等效距离 + 密度自适应 + 避让）
        self.fire_router: Optional[FireAwareRouter] = None
        self.fire_config: Optional[FireAwareConfig] = None
        # Vision-mapped dynamic A* (Tian et al., 2026).
        self.vision_planner: Optional[VisionMappedAStarPlanner] = None
        self.vision_results: Dict[int, Any] = {}
        self.vision_map_name: str = ""
        self.dual_planner: Optional[DualObjectivePlanner] = None
        self.dual_results: Dict[int, Any] = {}
        self.dual_map_name: str = ""
        self.dual_smoke_sources: List[Any] = []

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
            if str(config.get("mode", "")).lower() in ("fire_aware", "fire_aware_adaptive"):
                self._ensure_fire_router(config)
        elif ctrl_strategy == ControlStrategyType.FIRE_AWARE_ADAPTIVE:
            self.fire_aware_params.update(config)
            self._ensure_fire_router(self.fire_aware_params)

    def _ensure_fire_router(self, config: Dict[str, Any] = None) -> FireAwareRouter:
        """Create/reuse the paper-grounded router."""
        cfg_values = dict(self.fire_aware_params)
        if config:
            cfg_values.update(config)
        allowed = {
            "density_threshold", "density_radius_m", "pheromone_radius_m",
            "pheromone_diffusion", "pheromone_decay", "avoid_probability",
            "avoid_threshold", "replan_interval_s", "hazard_change_threshold",
            "exit_congestion_weight_m", "vulnerable_congestion_weight_m",
            "exit_trend_weight_m", "hysteresis_ratio", "smoke_proxy_weight",
            "exit_unsafe_factor", "zone_bias_m", "blocked_cost_multiplier",
            "exit_unsafe_smoke", "competitiveness_exponent", "speed_reference",
        }
        kwargs = {k: v for k, v in cfg_values.items() if k in allowed}
        self.fire_config = FireAwareConfig(**kwargs)
        self.fire_router = FireAwareRouter(
            self.grid,
            self.exit_positions,
            self.fire_config,
            width=self.grid_width,
            height=self.grid_height,
        )
        self._log_event("火灾感知自适应路由器已初始化", {
            "density_threshold": self.fire_config.density_threshold,
            "pheromone_radius_m": self.fire_config.pheromone_radius_m,
            "avoid_probability": self.fire_config.avoid_probability,
            "avoid_threshold": self.fire_config.avoid_threshold,
        })
        return self.fire_router

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
            self.route_weight_map[key] = float(weight)
            self.route_weight_base[key] = float(weight)

    # ============================================================
    # 每步更新
    # ============================================================

    def update_all(self, all_persons: List, current_step: int,
                   smoke_grid: Optional[np.ndarray] = None,
                   dt: Optional[float] = None,
                   temperature_grid: Optional[np.ndarray] = None,
                   co_grid: Optional[np.ndarray] = None,
                   visibility_grid: Optional[np.ndarray] = None) -> Dict[str, Any]:
        self.step_stats = defaultdict(int)

        if ControlStrategyType.DUAL_OBJECTIVE_ACO in self.active_strategies:
            self.plan_dual_routes(
                all_persons, current_step, dt=dt or 0.5, force=False,
                smoke_grid=smoke_grid, smoke_sources=self.dual_smoke_sources,
            )
        elif ControlStrategyType.VISION_MAPPED_ASTAR in self.active_strategies:
            self.plan_vision_routes(
                all_persons, current_step, dt=dt or 0.5, force=False, smoke_grid=smoke_grid,
            )
        elif ControlStrategyType.FIRE_AWARE_ADAPTIVE in self.active_strategies:
            self._update_integrated_control(
                all_persons, current_step, smoke_grid, dt,
                temperature_grid, co_grid, visibility_grid,
            )
        else:
            if ControlStrategyType.EXIT_CLOSURE in self.active_strategies:
                self._update_exit_closure(all_persons, current_step)

            if ControlStrategyType.ZONE_LOCKDOWN in self.active_strategies:
                self._update_zone_lockdown(all_persons, smoke_grid, current_step)

            if ControlStrategyType.ZONED_EVACUATION in self.active_strategies:
                self._update_zoned_evacuation(all_persons, current_step)

            if ControlStrategyType.ROUTE_CONTROL in self.active_strategies:
                if self.fire_router is not None:
                    self._update_fire_aware(
                        all_persons, current_step, smoke_grid, dt,
                        temperature_grid, co_grid, visibility_grid,
                    )
                else:
                    self._update_route_control(all_persons, current_step)

        return {
            "active_strategies": [s.value for s in self.active_strategies],
            "closed_exits": self._get_closed_exits(),
            "locked_cells": sum(1 for v in self.lockdown_status.values() if v),
            "assigned_persons": len(self.zone_assignment),
            "route_weights": len(self.route_weight_map),
            "fire_aware": self.fire_router.statistics() if self.fire_router is not None else None,
            "vision_astar": self.vision_statistics(),
            "dual_objective": self.dual_statistics(),
            "integrated_components": self._integrated_status(),
            "stats": dict(self.step_stats),
        }

    def configure_integrated_control(self, all_persons: List = None,
                                     exit_ids: Optional[List[str]] = None,
                                     closed_exits: Optional[List[str]] = None,
                                     exit_trigger_time: Optional[int] = 10,
                                     reopen_time: Optional[int] = None,
                                     lockdown_zones: Optional[List[List[int]]] = None,
                                     lockdown_trigger: str = "smoke_threshold",
                                     lockdown_smoke_threshold: float = 0.5,
                                     auto_unlock: bool = True,
                                     smoke_sources: Optional[List[Any]] = None,
                                     **fire_config) -> None:
        """Configure the single integrated strategy (four components)."""
        valid_exits = set(self.exit_positions.keys())
        close_list = [e for e in (closed_exits or []) if e in valid_exits]
        self.exit_closure_params.update({
            "enabled": bool(close_list),
            "closed_exits": close_list,
            "trigger_time": exit_trigger_time,
            "reopen_time": reopen_time,
            "reopen_after_evacuation": False,
        })
        zones = [list(map(int, z)) for z in (lockdown_zones or []) if len(z) == 4]
        self.zone_lockdown_params.update({
            "enabled": bool(zones),
            "locked_zones": zones,
            "lockdown_trigger": str(lockdown_trigger),
            "smoke_threshold": float(lockdown_smoke_threshold),
            "auto_unlock": bool(auto_unlock),
        })
        self.lockdown_status.clear()
        self._init_lockdown_status()
        self.zone_assignment.clear()
        if all_persons:
            open_now = [e for e in (exit_ids or list(self.exit_positions.keys()))
                        if e in valid_exits and not self.closure_status.get(e, False)]
            self.assign_zones_by_positions(list(all_persons), open_now)
        self.enable_strategy(ControlStrategyType.FIRE_AWARE_ADAPTIVE, fire_config)
        if self.fire_router is not None:
            self.fire_router.set_smoke_sources(smoke_sources)
        self._log_event("综合管控已配置（四合一）", {
            "closed_exits": close_list,
            "lockdown_zones": zones,
            "lockdown_trigger": lockdown_trigger,
            "zoned_persons": len(self.zone_assignment),
        })

    def _ensure_zone_assignment(self, all_persons: List) -> None:
        alive = [
            p for p in all_persons
            if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)
        ]
        open_exits = [e for e in self.exit_positions if not self.closure_status.get(e, False)]
        if not alive or not open_exits:
            return
        alive_ids = {int(p.id) for p in alive}
        self.zone_assignment = {
            pid: eid for pid, eid in self.zone_assignment.items() if pid in alive_ids
        }
        if not self.zone_assignment:
            self.assign_zones_by_positions(alive, open_exits)
            return
        counts = defaultdict(int)
        for eid in self.zone_assignment.values():
            if eid in open_exits:
                counts[eid] += 1
        for person in alive:
            pid = int(person.id)
            eid = self.zone_assignment.get(pid)
            if eid in open_exits:
                continue
            best = min(open_exits, key=lambda e: counts.get(e, 0))
            self.zone_assignment[pid] = best
            counts[best] += 1

    def _update_integrated_control(self, all_persons: List, current_step: int,
                                   smoke_grid: Optional[np.ndarray], dt: Optional[float],
                                   temperature_grid: Optional[np.ndarray],
                                   co_grid: Optional[np.ndarray],
                                   visibility_grid: Optional[np.ndarray]):
        """Run the four sub-policies as one composite strategy."""
        # 1) exit closure
        self._update_exit_closure(all_persons, current_step)
        # 2) zone lockdown (manual or smoke-threshold)
        self._update_zone_lockdown(all_persons, smoke_grid, current_step)
        # 3) planned zoned evacuation: one primary exit per person
        self._ensure_zone_assignment(all_persons)
        self._update_zoned_evacuation(all_persons, current_step)
        # 4) fire-aware route control overrides the zone preference only when
        #    the equivalent-distance advantage exceeds the soft margin.
        self._update_fire_aware(
            all_persons, current_step, smoke_grid, dt,
            temperature_grid, co_grid, visibility_grid,
            zone_targets=self.zone_assignment,
        )
        self.step_stats["integrated_exit_closures"] = len(self._get_closed_exits())
        self.step_stats["integrated_lockdown_cells"] = sum(
            1 for v in self.lockdown_status.values() if v
        )
        self.step_stats["integrated_zone_assigned"] = len(self.zone_assignment)
        self.step_stats["integrated_route_assigned"] = len(self.route_target)

    def _integrated_status(self) -> Dict[str, Any]:
        return {
            "active": ControlStrategyType.FIRE_AWARE_ADAPTIVE in self.active_strategies,
            "exit_closure": {
                "enabled": bool(self.exit_closure_params.get("closed_exits")),
                "closed_exits": self._get_closed_exits(),
                "trigger_time": self.exit_closure_params.get("trigger_time"),
            },
            "zone_lockdown": {
                "enabled": bool(self.zone_lockdown_params.get("locked_zones")),
                "trigger": self.zone_lockdown_params.get("lockdown_trigger"),
                "locked_cells": sum(1 for v in self.lockdown_status.values() if v),
                "smoke_threshold": self.zone_lockdown_params.get("smoke_threshold"),
            },
            "zoned_evacuation": {
                "enabled": bool(self.zone_assignment),
                "assigned": len(self.zone_assignment),
                "distribution": dict(self.zone_distribution),
            },
            "route_control": {
                "enabled": self.fire_router is not None,
                "mode": "fire_aware_equivalent_distance",
                "assigned": len(self.route_target),
                "distribution": dict(self.route_distribution),
            },
        }

    # ============================================================
    # Vision-mapped dynamic A* (Tian et al., 2026)
    # ============================================================
    def configure_vision_astar(self, all_persons: List, map_path: str,
                               exit_ids: Optional[List[str]] = None,
                               replan_interval_s: float = 30.0,
                               smoke_sources: Optional[List[Any]] = None) -> None:
        """Configure the monitor-to-grid + improved A* strategy."""
        exit_map = {eid: self.exit_positions[eid] for eid in (exit_ids or list(self.exit_positions))
                    if eid in self.exit_positions}
        cfg = VisionAStarConfig(replan_interval_s=float(replan_interval_s))
        self.vision_planner = VisionMappedAStarPlanner(
            self.grid, map_path, exit_map,
            config=cfg,
        )
        self.vision_planner.set_smoke_sources(smoke_sources)
        self.vision_map_name = self.vision_planner.map_name
        self.vision_results = {}
        self.enable_strategy(ControlStrategyType.VISION_MAPPED_ASTAR, {
            "enabled": True,
            "replan_interval_s": float(replan_interval_s),
        })
        self.plan_vision_routes(all_persons, 0, force=True)

    def plan_vision_routes(self, all_persons: List, current_step: int,
                           dt: float = 0.5, force: bool = False,
                           smoke_grid: Optional[np.ndarray] = None) -> Dict[int, Any]:
        if self.vision_planner is None:
            return {}
        results = self.vision_planner.plan_all(
            all_persons, current_step, dt=dt, force=force, smoke_grid=smoke_grid,
        )
        self.vision_results = results
        self.route_target = {int(pid): r.exit_id for pid, r in results.items()}
        by_id = {int(p.id): p for p in all_persons}
        for pid, result in results.items():
            person = by_id.get(int(pid))
            if person is None:
                continue
            person.target_exit = result.exit_id
            person.planned_path = result.path
            person.route_time_s = result.time_s
            person.route_distance_m = result.distance_m
        self.step_stats["vision_routes_planned"] = len(results)
        self.step_stats["vision_replans"] = int(self.vision_planner.replan_count)
        return results

    def get_route_next_cell(self, person_id: int, current: Tuple[int, int]):
        # The active paper-grounded planner owns movement. Fire-aware paths are
        # soft guidance; dual/vision paths are strict guidance in main.py.
        if self.fire_router is not None and ControlStrategyType.FIRE_AWARE_ADAPTIVE in self.active_strategies:
            return self.fire_router.next_cell(person_id, current)
        if self.dual_planner is not None:
            return self.dual_planner.next_cell(person_id, current)
        if self.vision_planner is None:
            return None
        return self.vision_planner.next_cell(person_id, current)

    @property
    def has_fire_routes(self) -> bool:
        return self.fire_router is not None

    @property
    def has_vision_routes(self) -> bool:
        # A route map should still be emitted when the selected map has no
        # reachable path (e.g. all exits blocked), so the user can see why.
        return self.vision_planner is not None

    def export_vision_route_map(self, path: str, smoke_sources=None) -> str:
        if self.vision_planner is None:
            raise RuntimeError("vision strategy is not configured")
        return self.vision_planner.render(path, smoke_sources=smoke_sources)

    def export_vision_routes_json(self, path: str) -> str:
        if self.vision_planner is None:
            raise RuntimeError("vision strategy is not configured")
        return self.vision_planner.export_json(path)

    def vision_statistics(self) -> Dict[str, Any]:
        if self.vision_planner is None:
            return {}
        return self.vision_planner.statistics()

    def vision_routes_payload(self, max_routes: int = 200) -> List[Dict[str, Any]]:
        """Compact route payload consumed by the web canvas."""
        routes = []
        for result in list(self.vision_results.values())[:max_routes]:
            routes.append({
                "person_id": int(result.person_id),
                "exit_id": str(result.exit_id),
                "path": [[int(x), int(y)] for x, y in result.path],
                "time_s": float(result.time_s),
                "distance_m": float(result.distance_m),
            })
        return routes

    # ============================================================
    # Dual-objective time-risk routing (Yang et al., 2026)
    # ============================================================
    def configure_dual_objective(self, all_persons: List, map_path: str,
                                 exit_ids: Optional[List[str]] = None,
                                 risk_weight: float = 0.5,
                                 replan_interval_s: float = 30.0,
                                 smoke_sources: Optional[List[Any]] = None) -> None:
        """Configure the time-risk dual-objective route controller.

        The planner reads only the A map selected in the web page. Its
        per-cell speed field is the same smoke-concentration rule used by
        ``vision_astar``; no camera or monitoring file is introduced.
        """
        exit_map = {eid: self.exit_positions[eid]
                    for eid in (exit_ids or list(self.exit_positions))
                    if eid in self.exit_positions}
        cfg = DualObjectiveConfig(
            risk_weight=float(risk_weight),
            replan_interval_s=float(replan_interval_s),
        )
        self.dual_planner = DualObjectivePlanner(
            self.grid, map_path, exit_map, config=cfg,
        )
        self.dual_planner.base.set_smoke_sources(smoke_sources)
        self.dual_map_name = self.dual_planner.map_name
        self.dual_results = {}
        self.dual_smoke_sources = list(smoke_sources or [])
        self.enable_strategy(ControlStrategyType.DUAL_OBJECTIVE_ACO, {
            "enabled": True,
            "risk_weight": float(risk_weight),
            "replan_interval_s": float(replan_interval_s),
        })
        self.plan_dual_routes(all_persons, 0, force=True)

    def plan_dual_routes(self, all_persons: List, current_step: int,
                         dt: float = 0.5, force: bool = False,
                         smoke_grid: Optional[np.ndarray] = None,
                         smoke_sources: Optional[List[Any]] = None) -> Dict[int, Any]:
        if self.dual_planner is None:
            return {}
        if smoke_sources is not None:
            self.dual_smoke_sources = list(smoke_sources)
        results = self.dual_planner.plan_all(
            all_persons, current_step, dt=dt, force=force,
            smoke_grid=smoke_grid, smoke_sources=self.dual_smoke_sources,
        )
        self.dual_results = results
        self.route_target = {int(pid): r.exit_id for pid, r in results.items()}
        by_id = {int(p.id): p for p in all_persons}
        for pid, result in results.items():
            person = by_id.get(int(pid))
            if person is None:
                continue
            person.target_exit = result.exit_id
            person.planned_path = result.path
            person.route_time_s = result.time_s
            person.route_distance_m = result.distance_m
            person.route_risk = result.risk
            person.route_exit_probability = result.exit_probability
        self.step_stats["dual_routes_planned"] = len(results)
        self.step_stats["dual_replans"] = int(self.dual_planner.replan_count)
        return results

    @property
    def has_dual_routes(self) -> bool:
        return self.dual_planner is not None

    def export_dual_route_map(self, path: str, smoke_sources=None) -> str:
        if self.dual_planner is None:
            raise RuntimeError("dual-objective strategy is not configured")
        return self.dual_planner.render(
            path,
            smoke_sources=(smoke_sources if smoke_sources is not None else self.dual_smoke_sources),
        )

    def export_dual_routes_json(self, path: str) -> str:
        if self.dual_planner is None:
            raise RuntimeError("dual-objective strategy is not configured")
        return self.dual_planner.export_json(path)

    def dual_statistics(self) -> Dict[str, Any]:
        if self.dual_planner is None:
            return {}
        return self.dual_planner.statistics()

    def dual_routes_payload(self, max_routes: int = 200) -> List[Dict[str, Any]]:
        """Compact time-risk route payload consumed by the web canvas."""
        routes = []
        for result in list(self.dual_results.values())[:max_routes]:
            routes.append({
                "person_id": int(result.person_id),
                "exit_id": str(result.exit_id),
                "path": [[int(x), int(y)] for x, y in result.path],
                "time_s": float(result.time_s),
                "risk": float(result.risk),
                "distance_m": float(result.distance_m),
                "risk_weight": float(result.risk_weight),
            })
        return routes

    def _update_fire_aware(self, all_persons: List, current_step: int,
                           smoke_grid: Optional[np.ndarray], dt: Optional[float],
                           temperature_grid: Optional[np.ndarray],
                           co_grid: Optional[np.ndarray],
                           visibility_grid: Optional[np.ndarray],
                           zone_targets: Optional[Dict[int, str]] = None):
        """Run the paper-grounded dynamic control layer."""
        if self.fire_router is None:
            self._ensure_fire_router(self.fire_aware_params)
        allowed = [eid for eid in self.exit_positions
                   if not self.closure_status.get(eid, False)]
        exposure_s = float(current_step) * float(dt or 0.5)
        self.fire_router.update_hazard(
            smoke=smoke_grid,
            temperature=temperature_grid,
            co=co_grid,
            visibility=visibility_grid,
            exposure_s=exposure_s,
        )
        self.fire_router.set_blocked_cells(self.lockdown_status)
        self.fire_router.plan(
            all_persons, current_step,
            dt=dt or 0.5,
            allowed_exits=allowed,
            zone_targets=zone_targets,
        )
        self.route_target = dict(self.fire_router.route_target)
        self.route_distribution = dict(self.fire_router.assigned_exit_counts)
        if not self.zone_assignment:
            self.zone_distribution = dict(self.route_distribution)
        stats = self.fire_router.statistics()
        self.step_stats["fire_aware_assigned"] = int(stats.get("assigned_current", stats["assigned"]))
        self.step_stats["fire_aware_replans"] = int(stats["replans"])
        self.step_stats["vulnerable_priority"] = sum(
            1 for p in all_persons if getattr(p, "vulnerable", False)
        )

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
            # 死亡/已撤离人员不再改派出口
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            if hasattr(person, 'target_exit'):
                if self.closure_status.get(person.target_exit, False):
                    self._reassign_exit(person)

    def _reassign_exit(self, person):
        available_exits = [
            eid for eid in self.exit_positions
            if not self.closure_status.get(eid, False)
        ]
        if not available_exits:
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
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            if self.lockdown_status.get((int(person.x), int(person.y)), False):
                locked_in += 1
                if not getattr(person, "_locked_alert", False):
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
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
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
            base = self.route_weight_base.get((fx, fy, tx, ty), weight)
            if self.lockdown_status.get((tx, ty), False):
                self.route_weight_map[(fx, fy, tx, ty)] = base * 1.5
            else:
                self.route_weight_map[(fx, fy, tx, ty)] = base

        self.step_stats["route_controlled"] = len(self.route_weight_map)

    # ============================================================
    # 平台接入口（main.py 调用）
    # ============================================================

    def assign_zones_by_positions(self, all_persons: List,
                                  exit_ids: Optional[List[str]] = None) -> Dict[int, str]:
        """按行人当前位置把人员均衡分配到各出口分区（分区疏散）。

        与"就近出口"不同：这里按位置排序后平均切段，主动把一部分人
        引向较远/空闲的出口，用于验证分区疏导效果。
        """
        alive = [
            p for p in all_persons
            if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)
        ]
        exits = list(exit_ids) if exit_ids else list(self.exit_positions.keys())
        if not alive or not exits:
            return {}
        alive.sort(key=lambda p: (int(p.x), int(p.y)))
        k = len(exits)
        n = len(alive)
        self.zone_assignment = {}
        for index, person in enumerate(alive):
            self.zone_assignment[int(person.id)] = exits[min(k - 1, index * k // max(1, n))]
        counts: Dict[str, int] = defaultdict(int)
        for exit_id in self.zone_assignment.values():
            counts[exit_id] += 1
        self.zone_distribution = dict(counts)
        self.step_stats["zones_assigned"] = n
        return dict(self.zone_assignment)

    def update_route_balance(self, all_persons: List,
                             exit_ids: Optional[List[str]] = None,
                             shift_ratio: float = 0.2) -> Dict[int, str]:
        """路线管控：按各出口当前负载均衡改派目标出口，缓解拥堵。"""
        alive = [
            p for p in all_persons
            if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)
        ]
        exits = [e for e in (exit_ids or list(self.exit_positions.keys())) if self.is_exit_open(e)]
        if not alive or len(exits) < 2:
            return dict(self.route_target)

        load: Dict[str, List[Any]] = {e: [] for e in exits}
        for person in alive:
            current = getattr(person, "target_exit", None) or exits[0]
            if current not in load:
                current = min(exits, key=lambda e: len(load[e]))
            load[current].append(person)

        busiest = max(load, key=lambda e: len(load[e]))
        idlest = min(load, key=lambda e: len(load[e]))
        movable = load[busiest]
        shift = int(len(movable) * max(0.0, min(1.0, shift_ratio)))
        for person in movable[:shift]:
            self.route_target[int(person.id)] = idlest
        for exit_id, persons in load.items():
            for person in persons:
                self.route_target.setdefault(int(person.id), exit_id)
        self.step_stats["route_balanced"] = shift
        return dict(self.route_target)

    def get_route_target(self, person_id: int) -> Optional[str]:
        return self.route_target.get(person_id)

    # ============================================================
    # 给B组的查询接口
    # ============================================================

    def is_exit_open(self, exit_id: str) -> bool:
        return not self.closure_status.get(exit_id, False)

    @property
    def is_fire_aware(self) -> bool:
        return self.fire_router is not None

    def is_exit_safe(self, exit_id: str) -> bool:
        """Hazard-aware safety check; closure remains a separate hard state."""
        if not self.is_exit_open(exit_id):
            return False
        if self.fire_router is None:
            return True
        return self.fire_router.is_exit_safe(exit_id)

    def get_open_exits(self) -> List[str]:
        if self.exit_positions:
            return [eid for eid in self.exit_positions
                    if not self.closure_status.get(eid, False)]
        return [eid for eid, closed in self.closure_status.items() if not closed]

    def is_cell_accessible(self, x: int, y: int) -> bool:
        return not self.lockdown_status.get((x, y), False)

    def get_assigned_exit(self, person_id: int) -> Optional[str]:
        return self.zone_assignment.get(person_id) or self.route_target.get(person_id)

    def get_route_weight(self, from_cell: Tuple[int, int],
                         to_cell: Tuple[int, int]) -> float:
        key = (from_cell[0], from_cell[1], to_cell[0], to_cell[1])
        if key in self.route_weight_map:
            return self.route_weight_map[key]
        if self.fire_router is not None:
            return self.fire_router.hazard.edge_cost_multiplier(int(to_cell[0]), int(to_cell[1]))
        return 1.0

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
            "locked_zones": sum(1 for v in self.lockdown_status.values() if v),
            "assigned_zones": len(set(self.zone_assignment.values())),
            "controlled_routes": len(self.route_weight_map),
            "fire_aware": self.fire_router is not None,
            "fire_aware_stats": self.fire_router.statistics() if self.fire_router is not None else None,
            "vision_astar": self.vision_statistics(),
            "dual_objective": self.dual_statistics(),
            "integrated_components": self._integrated_status(),
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
            "fire_aware_assigned": self.step_stats.get("fire_aware_assigned", 0),
            "fire_aware_replans": self.step_stats.get("fire_aware_replans", 0),
            "vulnerable_priority": self.step_stats.get("vulnerable_priority", 0),
            "fire_aware_stats": self.fire_router.statistics() if self.fire_router is not None else None,
            "vision_astar": self.vision_statistics(),
            "dual_objective": self.dual_statistics(),
            "integrated_components": self._integrated_status(),
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
                          exit_pos_dict: Dict[str, Tuple[int, int]] = None,
                          grid=None) -> ControlStrategyEngine:
    return ControlStrategyEngine(
        social_graph, info_engine, grid_width, grid_height, exit_pos_dict, grid=grid,
    )


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
