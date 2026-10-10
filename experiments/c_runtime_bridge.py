"""Thin D-side bridge for C's existing per-step behavior engines.

The Web runner already consumes real A positions and B's ``EvacEngine``.  C's
information, group, herding and guide engines were previously exercised only
by ``main.py``.  This module wires those public C engines to the same runtime
objects without changing their algorithms or manufacturing any behavior.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import networkx as nx

from social.group_behavior import GroupBehaviorEngine
from social.guide_agent import GUIDE_COLORS, GuideAgentModel, GuideMoveStrategy
from social.herding_model import HerdingModel
from social.information_diffusion import InformationDiffusionEngine
from social.information_state import InfoState, InformationStateEngine
from control.control_strategy import ControlStrategyEngine, ControlStrategyType


@dataclass
class _RuntimeSocialGraph:
    """Expose the small SocialGraphBuilder contract C engines consume.

    Edges and attributes come from the already-loaded C population relation
    output held in ``engine.scene``; D does not regenerate relations.
    """

    persons: dict[int, Any]
    graph: nx.DiGraph

    @classmethod
    def from_engine(cls, engine: Any) -> "_RuntimeSocialGraph":
        people = {int(person.id): person for person in engine.person_map.values()}
        graph = nx.DiGraph()
        graph.add_nodes_from(people)
        for relation in getattr(engine.scene, "relations", []):
            left = getattr(relation, "person_a_id", None)
            right = getattr(relation, "person_b_id", None)
            if left not in people or right not in people:
                continue
            payload = {
                key: getattr(relation, key)
                for key in ("relation_type", "strength", "trust", "wait_probability", "follow_probability")
                if hasattr(relation, key)
            }
            graph.add_edge(int(left), int(right), **payload)
        return cls(persons=people, graph=graph)

    def get_relation(self, left: int, right: int) -> dict[str, Any]:
        if self.graph.has_edge(left, right):
            return dict(self.graph[left][right])
        return {
            "relation_type": "stranger",
            "strength": 0.0,
            "trust": 0.1,
            "wait_probability": 0.0,
            "follow_probability": 0.05,
        }


class CWebRuntimeBridge:
    """Run C's established step engines around a real B runtime step."""

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        self.social_graph = _RuntimeSocialGraph.from_engine(engine)
        self.info_state = InformationStateEngine(self.social_graph)
        self.info_diffusion = InformationDiffusionEngine(
            self.social_graph, self.info_state, int(engine.grid.width), int(engine.grid.height)
        )
        # The old unconditional step-20 broadcast is intentionally disabled.
        self.info_diffusion.broadcast_params["enabled"] = False
        self.info_diffusion.wom_params["enabled"] = True
        self.info_diffusion.rel_params["enabled"] = True
        self.info_diffusion.misinfo_params["enabled"] = False
        self.group = GroupBehaviorEngine(self.social_graph)
        self.herd = HerdingModel(self.social_graph, self.info_state, self._exits())
        self.guides_model = GuideAgentModel(
            self.social_graph, self.info_state, int(engine.grid.width), int(engine.grid.height), self._exits()
        )
        settings = getattr(engine.scene, "parameters", {}).get("d_c_runtime", {})
        self.initial_informed_ratio = float(settings.get("initial_informed_ratio", 0.15))
        self.alarm_enabled = bool(settings.get("alarm_enabled", True))
        self.guide_share = float(settings.get("guide_share", 0.30))
        # 网页运行时采用真实烟源语义：烟源附近先知情，浓度达阈值后广播。
        self.web_mode = bool(settings.get("web_mode", "source" in settings))
        self.smoke_local_radius = max(0.0, float(settings.get("smoke_local_radius", 5.0)))
        self.smoke_exposure_threshold = max(
            0.0, float(settings.get("smoke_exposure_threshold", 0.02))
        )
        self.info_diffusion.smoke_confirmation_threshold = self.smoke_exposure_threshold
        self.smoke_ramp_steps = max(
            0, int(settings.get("smoke_ramp_steps", 1000 if self.web_mode else 0))
        )
        self.broadcast_smoke_threshold = max(
            0.0,
            float(
                settings.get(
                    "broadcast_smoke_threshold",
                    getattr(engine, "alarm_threshold", 0.45),
                )
                or 0.45
            ),
        )
        self._smoke_ramp_targets: dict[int, float] = {}
        self._smoke_ramp_values: dict[int, float] = {}
        self._smoke_configured_intensity: dict[int, float] = {}
        # 无烟源时只允许日常行走/巡逻；显式配置该开关才允许做无火疏散演练。
        self.force_evacuation_without_hazard = bool(
            settings.get("force_evacuation_without_hazard", False)
        )
        self._alarm_seen = False
        self._move_credit: dict[int, float] = {}
        self._move_allowed: dict[int, bool] = {}
        self._speed_stats = {"blocked_total": 0, "congested_total": 0, "same_cell_congested_total": 0}
        # 未获知险情人员行为：walk=正常速度随机行走（默认）/ freeze=原地 / evacuate=直接疏散
        self.unknown_behavior = str(settings.get("unknown_behavior", "walk") or "walk").strip().lower()
        self.unknown_speed_factor = max(0.0, float(settings.get("unknown_speed_factor", 0.35)))
        self.informed_speed_factor = max(0.0, float(settings.get("informed_speed_factor", 1.15)))
        self.person_speed_variation = max(
            0.0, min(0.45, float(settings.get("person_speed_variation", 0.30)))
        )
        self.panic_smoke_peak = max(0.0, min(1.0, float(settings.get("panic_smoke_peak", 0.5))))
        self.panic_sigma = max(0.01, float(settings.get("panic_sigma", 0.25)))
        self.panic_threshold = max(0.0, min(1.0, float(settings.get("panic_threshold", 0.35))))
        self.panic_speed_gain = max(0.0, float(settings.get("panic_speed_gain", 0.45)))
        self.toxic_smoke_threshold = max(0.0, min(1.0, float(settings.get("toxic_smoke_threshold", 0.65))))
        self.dose_slowdown_scale = max(0.1, float(settings.get("dose_slowdown_scale", 6.0)))
        self._panic_cache: dict[int, dict[str, float | bool]] = {}
        if self.unknown_behavior not in ("walk", "freeze", "evacuate"):
            self.unknown_behavior = "walk"
        self._walk_credit: dict[int, float] = {}
        self._walk_dir: dict[int, tuple[int, int]] = {}
        self._walk_stats = {"walk_total": 0, "frozen_total": 0}
        self.control = ControlStrategyEngine(
            self.social_graph,
            self.info_state,
            int(engine.grid.width),
            int(engine.grid.height),
            {eid: (x, y) for eid, x, y in self._exits()},
        )
        self.control_route_shift_ratio = 0.2
        self._configure_control_strategy(settings)
        self._apply_individual_speed_variation()
        self._configure_web_smoke_semantics(settings)
        self._deploy_guides()
        self.has_hazard = self._detect_hazard()
        self._seed_initial_information()
        self._publish_state()

    def _apply_individual_speed_variation(self) -> None:
        if self.person_speed_variation <= 0.0:
            return
        raw_seed = getattr(self.engine.scene, "parameters", {}).get("random_seed", 0)
        base_seed = int(raw_seed) if raw_seed is not None else 0
        for person in self.engine.person_map.values():
            rng = random.Random(base_seed + int(person.id))
            factor = 1.0 + rng.uniform(-self.person_speed_variation, self.person_speed_variation)
            person.speed = max(0.1, float(getattr(person, "speed", 1.0) or 1.0) * factor)

    def _people(self) -> list[Any]:
        return [
            person for person in self.engine.person_map.values()
            if not getattr(person, "evacuated", False) and not getattr(person, "is_dead", False)
        ]

    def _exits(self) -> list[tuple[str, int, int]]:
        return [(str(exit_.id), int(exit_.x), int(exit_.y)) for exit_ in self.engine.exits]

    def _smoke_sources(self) -> list[Any]:
        sources = list(getattr(self.engine, "smoke_sources", None) or [])
        if sources:
            return sources
        scene = getattr(self.engine, "scene", None)
        return list(getattr(scene, "smoke_sources", None) or [])

    def _panic_state(self, person: Any) -> dict[str, float | bool]:
        smoke = 0.0
        smoke_matrix = getattr(self.engine, "smoke_matrix", None)
        if smoke_matrix is not None:
            x, y = int(person.x), int(person.y)
            if 0 <= y < smoke_matrix.shape[0] and 0 <= x < smoke_matrix.shape[1]:
                smoke = float(smoke_matrix[y, x])
        dose = 0.0
        recorder = getattr(self.engine, "dose_recorder", None)
        if recorder is not None and callable(getattr(recorder, "get_dose", None)):
            dose = float(recorder.get_dose(int(person.id)))
        else:
            dose = float(getattr(person, "dose", 0.0) or 0.0)
        panic_curve = math.exp(
            -((smoke - self.panic_smoke_peak) ** 2)
            / (2.0 * (self.panic_sigma ** 2))
        )
        toxic_factor = 1.0
        if smoke > self.toxic_smoke_threshold:
            toxic_factor = max(
                0.35,
                1.0 - (smoke - self.toxic_smoke_threshold) * 1.7,
            )
        dose_factor = max(0.35, 1.0 / (1.0 + dose / self.dose_slowdown_scale))
        panic_level = max(panic_curve, min(1.0, smoke * 1.2))
        speed_multiplier = max(
            0.20,
            (1.0 + self.panic_speed_gain * panic_curve) * toxic_factor * dose_factor,
        )
        return {
            "is_panicked": bool(smoke >= 0.05 or panic_level >= self.panic_threshold),
            "panic_level": max(0.0, min(1.0, panic_level)),
            "speed_multiplier": speed_multiplier,
        }

    def _smoke_peak(self) -> float:
        smoke = getattr(self.engine, "smoke_matrix", None)
        if smoke is None:
            return 0.0
        peak = 0.0
        try:
            for row in smoke:
                for value in row:
                    peak = max(peak, float(value))
        except (TypeError, ValueError):
            return 0.0
        return peak

    def _configure_web_smoke_semantics(self, settings: Mapping[str, Any]) -> None:
        if not self.web_mode:
            return
        smoke_engine = getattr(self.engine, "smoke_engine", None)
        if smoke_engine is not None:
            smoke_engine.sigma0 = max(0.05, float(settings.get("smoke_sigma0", 0.8)))
            smoke_engine.sigma_grow = max(0.0, float(settings.get("smoke_sigma_grow", 0.18)))
        target_floor = max(0.05, float(settings.get("smoke_target_intensity", 120.0)))
        for source in self._smoke_sources():
            target = target_floor
            current = max(0.0, float(getattr(source, "intensity", 1.0) or 0.0))
            self._smoke_ramp_targets[id(source)] = target
            self._smoke_configured_intensity[id(source)] = current
            self._smoke_ramp_values[id(source)] = (
                target * (1.0 / self.smoke_ramp_steps)
                if self.smoke_ramp_steps > 0
                else target
            )

    def _advance_smoke_ramp(self) -> None:
        if not self.web_mode or self.smoke_ramp_steps <= 0 or not self._smoke_ramp_targets:
            return
        progress = min(1.0, max(0.0, float(self.engine.current_step) / float(self.smoke_ramp_steps)))
        for source in self._smoke_sources():
            target = self._smoke_ramp_targets.get(id(source))
            if target is not None:
                self._smoke_ramp_values[id(source)] = target * progress

    def _apply_smoke_ramp_to_engine(self) -> None:
        for source in self._smoke_sources():
            value = self._smoke_ramp_values.get(id(source))
            if value is not None:
                source.intensity = value

    def _restore_smoke_intensity(self) -> None:
        for source in self._smoke_sources():
            original = self._smoke_configured_intensity.get(id(source))
            if original is not None:
                source.intensity = original

    def _source_smoke_level(self) -> float:
        smoke = getattr(self.engine, "smoke_matrix", None)
        if smoke is None:
            return 0.0
        level = 0.0
        for source in self._smoke_sources():
            x, y = int(source.x), int(source.y)
            if 0 <= y < smoke.shape[0] and 0 <= x < smoke.shape[1]:
                level = max(level, float(smoke[y, x]))
        return level

    def _detect_hazard(self) -> bool:
        """Return True only when the runtime has fire/smoke evidence.

        An alarm point alone is not a fire source. This matters for the web
        runtime: loading a map without smoke_source cells must not seed
        informed people or start an evacuation.
        """
        if self.force_evacuation_without_hazard:
            return True
        return bool(self._smoke_sources()) or self._smoke_peak() > 1e-9

    def _deploy_guides(self) -> None:
        exits = self._exits()
        if not exits:
            return
        patrol_points = [
            (int(cell.x), int(cell.y))
            for cell in self.engine.grid.cells
            if str(getattr(getattr(cell, "cell_type", None), "value", getattr(cell, "cell_type", ""))).lower() == "free"
        ]
        # This is C09's existing patrol mode: one automatically placed guide
        # per available exit, with a route built from real free cells only.
        for index, (_exit_id, x, y) in enumerate(exits):
            route = patrol_points[index::max(1, len(exits))]
            if not route:
                route = [(x, y)]
            profile = "security" if index == 0 else "staff"
            self.guides_model.add_guide_with_patrol(x, y, route, profile=profile)

    def _seed_smoke_adjacent_information(self, people: list[Any]) -> int:
        sources = self._smoke_sources()
        if not sources or self.smoke_local_radius <= 0.0:
            return 0
        radius_sq = self.smoke_local_radius ** 2
        seeded = 0
        for person in people:
            person_id = int(person.id)
            if self.info_state.get_state_value(person_id) != "UNKNOWN":
                continue
            near_source = any(
                (float(person.x) - float(source.x)) ** 2
                + (float(person.y) - float(source.y)) ** 2
                <= radius_sq
                for source in sources
            )
            if not near_source:
                continue
            if self.info_state.transition_state(
                person_id,
                InfoState.ALERTED,
                int(self.engine.current_step),
                source=-3,
                method="smoke_adjacent",
            ):
                seeded += 1
        return seeded

    def _seed_initial_information(self) -> None:
        people = self._people()
        if not self.has_hazard:
            self._sync_person_information()
            return
        if self.web_mode:
            self._seed_smoke_adjacent_information(people)
        elif people and self.initial_informed_ratio > 0:
            self.info_diffusion.initialize_initial_informed(
                people, current_step=int(self.engine.current_step), ratio=self.initial_informed_ratio
            )
        self._sync_person_information()

    def _configure_control_strategy(self, settings: Mapping[str, Any]) -> None:
        raw_value = str(settings.get("control_strategy", "none") or "none").strip().lower()
        strategy_names = [item.strip() for item in raw_value.split(",") if item.strip()]
        if "all" in strategy_names:
            strategy_names = [item.value for item in ControlStrategyType]
        self.control_route_shift_ratio = max(
            0.0, min(1.0, float(settings.get("route_shift_ratio", 0.2)))
        )
        exits = self._exits()
        exit_ids = [eid for eid, _x, _y in exits]
        if not exits:
            return
        if "exit_closure" in strategy_names:
            closed = list(settings.get("control_closed_exits") or [])
            if not closed:
                closed = [exit_ids[-1]]
            self.control.enable_strategy(
                ControlStrategyType.EXIT_CLOSURE,
                {"closed_exits": closed, "trigger_time": int(settings.get("control_trigger_step", 10))},
            )
        if "zone_lockdown" in strategy_names:
            area = settings.get("control_lockdown_area")
            if not isinstance(area, (list, tuple)) or len(area) != 4:
                area = [
                    int(self.engine.grid.width // 3),
                    int(self.engine.grid.height // 3),
                    int(self.engine.grid.width * 2 // 3),
                    int(self.engine.grid.height * 2 // 3),
                ]
            self.control.enable_strategy(
                ControlStrategyType.ZONE_LOCKDOWN,
                {"locked_zones": [[int(value) for value in area]], "lockdown_trigger": "manual"},
            )
        if "zoned_evacuation" in strategy_names:
            self.control.enable_strategy(ControlStrategyType.ZONED_EVACUATION, {})
            self.control.assign_zones_by_positions(self._people(), exit_ids)
        if "route_control" in strategy_names:
            self.control.enable_strategy(ControlStrategyType.ROUTE_CONTROL, {"controlled_routes": []})

    def _far_exit(self) -> str | None:
        exits = self._exits()
        if not exits:
            return None
        center_x, center_y = self.engine.grid.width / 2.0, self.engine.grid.height / 2.0
        return max(exits, key=lambda item: (item[1] - center_x) ** 2 + (item[2] - center_y) ** 2)[0]

    def _maybe_activate_alarm(self) -> None:
        if self._alarm_seen or not self.alarm_enabled or not self.has_hazard:
            return
        b_alarm = bool(getattr(self.engine, "is_alarm_triggered", False))
        should_activate = (
            self._source_smoke_level() >= self.broadcast_smoke_threshold
            if self.web_mode
            else b_alarm
        )
        if should_activate:
            self._activate_alarm()

    def _activate_alarm(self) -> None:
        if self._alarm_seen or not self.alarm_enabled:
            return
        people = self._people()
        self.info_diffusion.trigger_alarm(people, int(self.engine.current_step))
        target_exit = self._far_exit()
        if target_exit is not None:
            self.guides_model.activate_guidance(target_exit)
        self._alarm_seen = True
        self._sync_person_information()

    def _sync_person_information(self) -> None:
        for person in self.engine.person_map.values():
            person_id = int(person.id)
            person.info_state = self.info_state.get_state_value(person_id)
            person.info_source = self.info_state.get_info_source(person_id)
            received = self.info_state.get_receive_step(person_id)
            person.receive_time = received if received >= 0 else None
            person.info_source_history = self.info_state.get_info_source_history(person_id)

    def _speed_gate(self, people: list[Any]) -> None:
        self._move_allowed = {}
        if not people:
            return
        speeds = [float(getattr(person, "speed", 1.0) or 1.0) for person in people]
        cell_counts: dict[tuple[int, int], int] = {}
        for person in people:
            cell_counts[(int(person.x), int(person.y))] = cell_counts.get((int(person.x), int(person.y)), 0) + 1
        mean_speed = sum(speeds) / len(speeds) if speeds else 1.0
        if mean_speed <= 0:
            mean_speed = 1.0
        for person in people:
            person_id = int(person.id)

            # 无火源时绝不走 B 的疏散路径；有火但未获知险情时也不疏散。
            # walk 稍后改为日常行走，freeze 则原地，evacuate 只作用于有火场景。
            info_state = self.info_state.get_state_value(person_id)
            if (not self.has_hazard) or (
                self.unknown_behavior != "evacuate" and info_state == "UNKNOWN"
            ):
                self._move_allowed[person_id] = False
                if self.unknown_behavior == "freeze":
                    self._walk_stats["frozen_total"] += 1
                continue

            density = sum(
                1
                for other in people
                if abs(int(other.x) - int(person.x)) <= 1 and abs(int(other.y) - int(person.y)) <= 1
            )
            congestion_factor = 1.0
            same_cell = cell_counts.get((int(person.x), int(person.y)), 1)
            if same_cell > 1:
                congestion_factor = min(congestion_factor, max(0.15, 1.0 / float(same_cell)))
                self._speed_stats["same_cell_congested_total"] += 1
            if density >= 4:
                congestion_factor = min(congestion_factor, max(0.3, 4.0 / float(density)))
                self._speed_stats["congested_total"] += 1
            panic_state = self._panic_cache.get(person_id, {})
            panic_speed = float(panic_state.get("speed_multiplier", 1.0) or 1.0)
            credit = self._move_credit.get(int(person.id), 0.0)
            informed_speed = (
                float(getattr(person, "speed", 1.0) or 1.0)
                * self.informed_speed_factor
                * panic_speed
            )
            credit += informed_speed / mean_speed * congestion_factor
            self._move_allowed[int(person.id)] = credit >= 1.0
            if credit >= 1.0:
                credit -= 1.0
            else:
                self._speed_stats["blocked_total"] += 1
            self._move_credit[int(person.id)] = credit

    def __call__(self, engine: Any) -> dict[int, dict[str, Any]]:
        if engine is not self.engine:
            raise ValueError("C runtime bridge received an unexpected B runtime")
        self.has_hazard = self.has_hazard or self._detect_hazard()
        self._maybe_activate_alarm()
        self._apply_smoke_ramp_to_engine()
        people = self._people()
        step = int(engine.current_step)

        if self.has_hazard:
            # 只判断人员当前所在格的烟雾，不把远处扩散烟雾作为全图知情源。
            # 站在烟雾中的人员必须立即转为知情并开始疏散。
            smoke = getattr(engine, "smoke_matrix", None)
            self.info_diffusion.update_all(people, current_step=step, smoke_grid=smoke)
            group_result = self.group.update_all(people, step)
            herd_result = self.herd.update_all(
                people, int(engine.grid.width), int(engine.grid.height), step
            )
        else:
            # 无火源：不传播险情，不产生等待/跟随/从众行为。
            group_result = {}
            herd_result = {}

        # 引导员无论有无火灾都移动；但只有报警后才允许影响行人状态。
        if self.control is not None:
            self.control.update_all(people, step, getattr(engine, "smoke_matrix", None))
            if ControlStrategyType.ROUTE_CONTROL in self.control.active_strategies:
                self.control.update_route_balance(
                    people,
                    [eid for eid, _x, _y in self._exits()],
                    self.control_route_shift_ratio,
                )

        self._panic_cache = {int(person.id): self._panic_state(person) for person in people}
        self.guides_model.update_guides(people, self._exits(), step)
        if self.has_hazard and self.guides_model.guidance_active:
            guide_result = self.guides_model.update_all(people, step)
        else:
            guide_result = {}
        self._speed_gate(people)
        result: dict[int, dict[str, Any]] = {}
        target_exit = (
            self.guides_model.target_exit
            if self.has_hazard and self.guides_model.guidance_active
            else None
        )
        for person in people:
            person_id = int(person.id)
            group = group_result.get(person_id, {})
            herd = herd_result.get(person_id, {})
            guide = guide_result.get(person_id, {})
            info_state = self.info_state.get_state_value(person_id)
            exit_preference = dict(group.get("exit_preference", {}) or {})
            for exit_id, value in (herd.get("exit_preference", {}) or {}).items():
                exit_preference[exit_id] = exit_preference.get(exit_id, 0.0) + value
            person.is_waiting = bool(group.get("is_waiting", False))
            person.follow_target = group.get("follow_target")
            person_target_exit = target_exit or getattr(person, "target_exit", "") or ""
            if self.control is not None:
                assigned_exit = (
                    self.control.get_assigned_exit(person_id)
                    or self.control.get_route_target(person_id)
                )
                if assigned_exit and self.control.is_exit_open(assigned_exit):
                    person_target_exit = assigned_exit
                    exit_preference[assigned_exit] = max(
                        exit_preference.get(assigned_exit, 0.0), 1.2
                    )
            panic_state = self._panic_cache.get(person_id, {})
            person.is_panicked = bool(panic_state.get("is_panicked", False))
            person.panic_level = float(panic_state.get("panic_level", 0.0) or 0.0)
            person.speed_multiplier = float(panic_state.get("speed_multiplier", 1.0) or 1.0)
            result[person_id] = {
                "target_exit": person_target_exit,
                "exit_preference": exit_preference,
                "herding_influence": herd.get("herding_influence", 0.0),
                "dominant_direction": herd.get("dominant_direction", (0, 0)),
                "guide_influence": guide.get("guide_influence", 0.0),
                "info_state": info_state,
                "is_following": group.get("is_following", False),
                "follow_target": group.get("follow_target"),
                "follow_strength": group.get("follow_strength", 0.0),
                "is_waiting": person.is_waiting,
                "is_panicked": person.is_panicked,
                "panic_level": person.panic_level,
                "speed_multiplier": person.speed_multiplier,
                "waiting_for": group.get("waiting_for"),
                "group_id": getattr(person, "group_id", ""),
            }
        self._sync_person_information()
        self._publish_state()
        return result

    def _walkable(self, x: int, y: int) -> bool:
        grid = self.engine.grid
        if not (0 <= x < int(grid.width) and 0 <= y < int(grid.height)):
            return False
        cell = grid.get_cell(x, y)
        if cell is None:
            return False
        cell_type = str(getattr(getattr(cell, "cell_type", None), "value", getattr(cell, "cell_type", ""))).lower()
        if self.control is not None and not self.control.is_cell_accessible(x, y):
            return False
        return cell_type in ("free", "sign", "guide_zone")

    def _daily_walk(self) -> None:
        """未获知险情者以自身正常速度随机行走（不参与疏散）。"""
        people = self._people()
        if not people:
            return
        occupied = {(int(person.x), int(person.y)) for person in people}
        cell_counts: dict[tuple[int, int], int] = {}
        for person in people:
            key = (int(person.x), int(person.y))
            cell_counts[key] = cell_counts.get(key, 0) + 1
        ordered_people = sorted(
            people,
            key=lambda item: -(
                float(getattr(item, "speed", 1.0) or 1.0) * self.unknown_speed_factor
            ),
        )
        for person in ordered_people:
            person_id = int(person.id)
            if self.has_hazard and self.info_state.get_state_value(person_id) != "UNKNOWN":
                continue
            speed = float(getattr(person, "speed", 1.0) or 1.0) * self.unknown_speed_factor
            same_cell = cell_counts.get((int(person.x), int(person.y)), 1)
            if same_cell > 1:
                speed *= max(0.15, 1.0 / float(same_cell))
                self._speed_stats["same_cell_congested_total"] += 1
            credit = self._walk_credit.get(person_id, 0.0) + speed
            if credit < 1.0:
                self._walk_credit[person_id] = credit
                continue
            self._walk_credit[person_id] = credit - 1.0

            cx, cy = int(person.x), int(person.y)
            last = self._walk_dir.get(person_id)
            candidates = []
            for dx, dy in (
                (-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)
            ):
                nx, ny = cx + dx, cy + dy
                if (nx, ny) in occupied or not self._walkable(nx, ny):
                    continue
                candidates.append((dx, dy, nx, ny))
            if not candidates:
                continue
            chosen = None
            if last is not None:
                for cand in candidates:
                    if (cand[0], cand[1]) == last:
                        chosen = cand
                        break
            if chosen is None:
                chosen = random.choice(candidates)
            dx, dy, nx, ny = chosen
            occupied.discard((cx, cy))
            person.x = nx
            person.y = ny
            occupied.add((nx, ny))
            self._walk_dir[person_id] = (dx, dy)
            self._walk_stats["walk_total"] += 1

    def after_b_step(self, engine: Any) -> None:
        """Use B's native alarm result; do not reimplement detector semantics."""
        if engine is not self.engine:
            return
        alarm_was_seen = self._alarm_seen
        self.has_hazard = self.has_hazard or self._detect_hazard()
        self._maybe_activate_alarm()
        if self._alarm_seen and not alarm_was_seen:
            self._speed_gate(self._people())
        self._restore_smoke_intensity()
        self._advance_smoke_ramp()
        for person in self.engine.person_map.values():
            if getattr(person, "is_dead", False):
                continue
            if self._move_allowed.get(int(person.id), True):
                continue
            prev_x = getattr(person, "prev_x", person.x)
            prev_y = getattr(person, "prev_y", person.y)
            # 未获知人员被 B 判定“走到出口撤离”时撤销该状态
            if getattr(person, "evacuated", False):
                person.evacuated = False
                if hasattr(person, "evac_step"):
                    person.evac_step = -1
                if hasattr(person, "actual_exit"):
                    person.actual_exit = None
            person.x = prev_x
            person.y = prev_y
        if hasattr(self.engine, "get_evacuated_count"):
            self.engine.evacuated_count = self.engine.get_evacuated_count()
        if self.unknown_behavior == "walk":
            self._daily_walk()
        if self.control is not None:
            for person in self.engine.person_map.values():
                if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                    continue
                if not self.control.is_cell_accessible(int(person.x), int(person.y)):
                    prev_x = getattr(person, "prev_x", person.x)
                    prev_y = getattr(person, "prev_y", person.y)
                    if self.control.is_cell_accessible(int(prev_x), int(prev_y)):
                        person.x = prev_x
                        person.y = prev_y
        smoke = getattr(self.engine, "smoke_matrix", None)
        if smoke is not None:
            self.info_diffusion.apply_smoke_exposure(
                self._people(), smoke, int(self.engine.current_step)
            )
        self._sync_person_information()
        self._publish_state()

    @property
    def guides(self) -> list[dict[str, Any]]:
        return [guide.to_dict() for guide in self.guides_model.guides]

    def _publish_state(self) -> None:
        active = self._people()
        informed = sum(1 for person in active if self.info_state.get_state_value(int(person.id)) != "UNKNOWN")
        panic_levels = [
            float(self._panic_cache.get(int(person.id), {}).get("panic_level", 0.0) or 0.0)
            for person in active
        ]
        self.engine.c_runtime_state = {
            "informed_count": informed,
            "panic_count": sum(1 for value in panic_levels if value >= self.panic_threshold),
            "mean_panic_level": (sum(panic_levels) / len(panic_levels)) if panic_levels else 0.0,
            "max_panic_level": max(panic_levels) if panic_levels else 0.0,
            "active_person_count": len(active),
            "has_hazard": self.has_hazard,
            "evacuation_authorized": self.has_hazard,
            "smoke_peak": self._smoke_peak(),
            "source_smoke_concentration": self._source_smoke_level(),
            "broadcast_smoke_threshold": self.broadcast_smoke_threshold,
            "smoke_local_radius": self.smoke_local_radius,
            "smoke_exposure_threshold": self.smoke_exposure_threshold,
            "smoke_ramp_progress": (
                min(1.0, float(self.engine.current_step) / float(self.smoke_ramp_steps))
                if self.smoke_ramp_steps > 0
                else 1.0
            ),
            "web_smoke_semantics": self.web_mode,
            "initial_informed_ratio": self.initial_informed_ratio if self.has_hazard else 0.0,
            "alarm_broadcast_triggered": self._alarm_seen,
            "guide_state": "guiding" if self.guides_model.guidance_active else "patrol",
            "guide_target_exit": self.guides_model.target_exit,
            "guides": self.guides,
            "speed_model": {
                "enabled": True,
                "unknown_speed_factor": self.unknown_speed_factor,
                "informed_speed_factor": self.informed_speed_factor,
                "person_speed_variation": self.person_speed_variation,
                "blocked_total": self._speed_stats["blocked_total"],
                "congested_total": self._speed_stats["congested_total"],
                "same_cell_congested_total": self._speed_stats["same_cell_congested_total"],
            },
            "unknown_behavior": self.unknown_behavior,
            "daily_walk": {
                "walk_total": self._walk_stats["walk_total"],
                "frozen_total": self._walk_stats["frozen_total"],
            },
            "guide_colors": dict(GUIDE_COLORS),
            "control_strategy": self.control.get_strategy_status(),
        }
