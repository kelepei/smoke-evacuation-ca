"""Thin D-side bridge for C's existing per-step behavior engines.

The Web runner already consumes real A positions and B's ``EvacEngine``.  C's
information, group, herding and guide engines were previously exercised only
by ``main.py``.  This module wires those public C engines to the same runtime
objects without changing their algorithms or manufacturing any behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import networkx as nx

from social.group_behavior import GroupBehaviorEngine
from social.guide_agent import GUIDE_COLORS, GuideAgentModel, GuideMoveStrategy
from social.herding_model import HerdingModel
from social.information_diffusion import InformationDiffusionEngine
from social.information_state import InformationStateEngine


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
        self._alarm_seen = False
        self._move_credit: dict[int, float] = {}
        self._move_allowed: dict[int, bool] = {}
        self._speed_stats = {"blocked_total": 0, "congested_total": 0}
        self._deploy_guides()
        self._seed_initial_information()
        self._publish_state()

    def _people(self) -> list[Any]:
        return [
            person for person in self.engine.person_map.values()
            if not getattr(person, "evacuated", False) and not getattr(person, "is_dead", False)
        ]

    def _exits(self) -> list[tuple[str, int, int]]:
        return [(str(exit_.id), int(exit_.x), int(exit_.y)) for exit_ in self.engine.exits]

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

    def _seed_initial_information(self) -> None:
        people = self._people()
        if people and self.initial_informed_ratio > 0:
            self.info_diffusion.initialize_initial_informed(
                people, current_step=int(self.engine.current_step), ratio=self.initial_informed_ratio
            )
        self._sync_person_information()

    def _far_exit(self) -> str | None:
        exits = self._exits()
        if not exits:
            return None
        center_x, center_y = self.engine.grid.width / 2.0, self.engine.grid.height / 2.0
        return max(exits, key=lambda item: (item[1] - center_x) ** 2 + (item[2] - center_y) ** 2)[0]

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
        mean_speed = sum(speeds) / len(speeds) if speeds else 1.0
        if mean_speed <= 0:
            mean_speed = 1.0
        for person in people:
            density = sum(
                1
                for other in people
                if abs(int(other.x) - int(person.x)) <= 1 and abs(int(other.y) - int(person.y)) <= 1
            )
            congestion_factor = 1.0
            if density >= 4:
                congestion_factor = max(0.3, 4.0 / float(density))
                self._speed_stats["congested_total"] += 1
            credit = self._move_credit.get(int(person.id), 0.0)
            credit += float(getattr(person, "speed", 1.0) or 1.0) / mean_speed * congestion_factor
            self._move_allowed[int(person.id)] = credit >= 1.0
            if credit >= 1.0:
                credit -= 1.0
            else:
                self._speed_stats["blocked_total"] += 1
            self._move_credit[int(person.id)] = credit

    def __call__(self, engine: Any) -> dict[int, dict[str, Any]]:
        if engine is not self.engine:
            raise ValueError("C runtime bridge received an unexpected B runtime")
        if bool(getattr(engine, "is_alarm_triggered", False)):
            self._activate_alarm()
        people = self._people()
        step = int(engine.current_step)
        smoke = getattr(engine, "smoke_matrix", None)
        self.info_diffusion.update_all(people, current_step=step, smoke_grid=smoke)
        group_result = self.group.update_all(people, step)
        herd_result = self.herd.update_all(people, int(engine.grid.width), int(engine.grid.height), step)
        self.guides_model.update_guides(people, self._exits(), step)
        guide_result = self.guides_model.update_all(people, step)
        self._speed_gate(people)
        result: dict[int, dict[str, Any]] = {}
        target_exit = self.guides_model.target_exit if self.guides_model.guidance_active else None
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
            result[person_id] = {
                "target_exit": target_exit or getattr(person, "target_exit", "") or "",
                "exit_preference": exit_preference,
                "herding_influence": herd.get("herding_influence", 0.0),
                "dominant_direction": herd.get("dominant_direction", (0, 0)),
                "guide_influence": guide.get("guide_influence", 0.0),
                "info_state": info_state,
                "is_following": group.get("is_following", False),
                "follow_target": group.get("follow_target"),
                "follow_strength": group.get("follow_strength", 0.0),
                "is_waiting": person.is_waiting,
                "waiting_for": group.get("waiting_for"),
                "group_id": getattr(person, "group_id", ""),
            }
        self._sync_person_information()
        self._publish_state()
        return result

    def after_b_step(self, engine: Any) -> None:
        """Use B's native alarm result; do not reimplement detector semantics."""
        if engine is not self.engine:
            return
        if bool(getattr(engine, "is_alarm_triggered", False)):
            self._activate_alarm()
        for person in self.engine.person_map.values():
            if (
                not getattr(person, "evacuated", False)
                and not getattr(person, "is_dead", False)
                and not self._move_allowed.get(int(person.id), True)
            ):
                person.x = getattr(person, "prev_x", person.x)
                person.y = getattr(person, "prev_y", person.y)
        self._sync_person_information()
        self._publish_state()

    @property
    def guides(self) -> list[dict[str, Any]]:
        return [guide.to_dict() for guide in self.guides_model.guides]

    def _publish_state(self) -> None:
        active = self._people()
        informed = sum(1 for person in active if self.info_state.get_state_value(int(person.id)) != "UNKNOWN")
        self.engine.c_runtime_state = {
            "informed_count": informed,
            "active_person_count": len(active),
            "initial_informed_ratio": self.initial_informed_ratio,
            "alarm_broadcast_triggered": self._alarm_seen,
            "guide_state": "guiding" if self.guides_model.guidance_active else "patrol",
            "guide_target_exit": self.guides_model.target_exit,
            "guides": self.guides,
            "speed_model": {
                "enabled": True,
                "blocked_total": self._speed_stats["blocked_total"],
                "congested_total": self._speed_stats["congested_total"],
            },
            "guide_colors": dict(GUIDE_COLORS),
        }
