"""Dual-objective (time + fire risk) evacuation route control.

Based on Yang Tianpeng et al. (2026), "Research on dual-objective evacuation
path planning in complex building fire environments", Journal of Disaster
Prevention and Reduction, 42(3): 40-50, DOI 10.13693/j.cnki.cn21-1573.2026.03.005.

Paper model
-----------
The paper combines the Critical Cluster Model (CCM) with a fire risk index:

    BLD = P / C                                  (1)
    R_f = f_t + f_co + f_vi                      (2)
    R_r = R_f * BLD / l                          (3)

and solves a two-objective problem:

    min R(x) = sum R_ij * x_ij                   (5)
    min T(x) = sum d_ij / v_ij(t) * x_ij          (6)

The improved ACO uses equivalent distance D_ij = L_ij / M_ij and pheromone
updates.  This implementation uses a deterministic Pareto-weighted A* on the
same grid for reproducibility and interactive runtime performance.  The
weight ``risk_weight`` is the user-controlled trade-off between time (0) and
risk (1); intermediate values correspond to a Pareto compromise.  The current
platform supplies a real smoke matrix but not temperature, CO or visibility
fields to this controller, so ``R_f`` is represented by normalized smoke and
the CCM/BLD term by local occupancy and exit count.  No temperature/CO values
are fabricated; when those FDS fields are added they should be mapped to the
paper's ``f_t + f_co + f_vi`` term.
"""

from __future__ import annotations

import heapq
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from .vision_mapped_astar import (
        ZONE_COLORS,
        ZONE_FIRE,
        ZONE_NORMAL,
        ZONE_REBAR,
        ZONE_STEEL,
        ZONE_WASTE,
        ZONE_OBSTACLE,
        VisionAStarConfig,
        VisionMappedAStarPlanner,
        render_route_map,
    )
except ImportError:  # direct script execution
    from vision_mapped_astar import (  # type: ignore
        ZONE_COLORS, ZONE_FIRE, ZONE_NORMAL, ZONE_REBAR, ZONE_STEEL,
        ZONE_WASTE, ZONE_OBSTACLE, VisionAStarConfig,
        VisionMappedAStarPlanner, render_route_map,
    )


@dataclass
class DualObjectiveConfig:
    risk_weight: float = 0.5          # 0=shortest time, 1=minimum risk
    replan_interval_s: float = 30.0
    density_radius_m: float = 2.0
    risk_scale: float = 12.0          # converts normalized risk to time-like cost
    risk_min: float = 0.0
    max_expansions: int = 220000

    def validate(self) -> None:
        self.risk_weight = float(np.clip(self.risk_weight, 0.0, 1.0))
        self.replan_interval_s = float(max(0.0, self.replan_interval_s))
        self.density_radius_m = float(max(0.5, self.density_radius_m))
        self.risk_scale = float(max(0.1, self.risk_scale))
        self.max_expansions = int(max(1000, self.max_expansions))


@dataclass
class DualRouteResult:
    person_id: int
    start: Tuple[int, int]
    exit_id: str
    path: List[Tuple[int, int]]
    time_s: float
    risk: float
    distance_m: float
    exit_probability: float
    probabilities: Dict[str, float] = field(default_factory=dict)
    risk_weight: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_id": int(self.person_id),
            "start": list(self.start),
            "exit_id": self.exit_id,
            "path": [list(cell) for cell in self.path],
            "time_s": float(self.time_s),
            "risk": float(self.risk),
            "distance_m": float(self.distance_m),
            "exit_probability": float(self.exit_probability),
            "probabilities": {k: float(v) for k, v in self.probabilities.items()},
            "risk_weight": float(self.risk_weight),
        }


class DualObjectivePlanner:
    """Risk-aware dual-objective route planner over the selected A map."""

    def __init__(self, grid: Any, map_path: str | Path,
                 exit_positions: Dict[str, Tuple[int, int]],
                 config: Optional[DualObjectiveConfig] = None,
                 vision_config: Optional[VisionAStarConfig] = None):
        self.grid = grid
        self.map_path = Path(map_path)
        self.map_name = self.map_path.stem
        self.exit_positions = {str(k): (int(v[0]), int(v[1])) for k, v in (exit_positions or {}).items()}
        self.dual_config = config or DualObjectiveConfig()
        self.dual_config.validate()
        self.base = VisionMappedAStarPlanner(
            grid, map_path, self.exit_positions,
            config=vision_config or VisionAStarConfig(replan_interval_s=self.dual_config.replan_interval_s),
        )
        # The shared route-map renderer reads planner.config.normal_speed.
        self.config = self.base.config
        self.monitoring = self.base.monitoring
        self.monitoring_source = self.base.monitoring_source
        self.smoke_grid = None
        self.risk_field = np.zeros((self.monitoring.height, self.monitoring.width), dtype=float)
        self.results: Dict[int, DualRouteResult] = {}
        self.last_plan_step = None
        self.replan_count = 0
        self._last_smoke_signature = None

    def update_smoke(self, smoke_grid=None) -> bool:
        changed = self.base.refresh_smoke(smoke_grid)
        self.smoke_grid = self.base.smoke_grid
        return changed

    def build_risk_field(self, persons: Sequence[Any],
                         smoke_sources: Optional[Sequence[Any]] = None) -> np.ndarray:
        """Grid approximation of the paper's R_r = R_f * BLD / l."""
        h, w = self.monitoring.height, self.monitoring.width
        smoke = self.smoke_grid
        if smoke is None:
            smoke = np.zeros((h, w), dtype=float)
        risk_fire = np.clip(np.asarray(smoke, dtype=float), 0.0, 1.0)

        # CCM/BLD approximation: local demand / number of exits.
        counts = np.zeros((h, w), dtype=float)
        radius_cells = max(1, int(round(self.dual_config.density_radius_m / max(self.monitoring.cell_size, 1e-6))))
        for person in persons:
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            px, py = int(person.x), int(person.y)
            for y in range(max(0, py - radius_cells), min(h, py + radius_cells + 1)):
                for x in range(max(0, px - radius_cells), min(w, px + radius_cells + 1)):
                    if (x - px) ** 2 + (y - py) ** 2 <= radius_cells ** 2:
                        counts[y, x] += 1.0
        exit_count = max(1, len(self.exit_positions))
        bld = counts / float(exit_count)
        bld_norm = bld / (1.0 + bld)

        # Distance to the nearest smoke source (metres).
        dist = np.full((h, w), 1.0e6, dtype=float)
        valid_sources = []
        for source in smoke_sources or []:
            sx = int(getattr(source, "x", -1))
            sy = int(getattr(source, "y", -1))
            if 0 <= sx < w and 0 <= sy < h:
                valid_sources.append((sx, sy))
        if valid_sources:
            yy, xx = np.mgrid[0:h, 0:w]
            for sx, sy in valid_sources:
                d = np.hypot(xx - sx, yy - sy) * self.monitoring.cell_size
                dist = np.minimum(dist, d)
        elif float(np.max(risk_fire)) > 1e-9:
            # No explicit source object: derive the distance to the smoke
            # region from the real smoke matrix itself (multi-source BFS).
            from collections import deque
            queue = deque()
            visited = np.zeros((h, w), dtype=bool)
            for sy, sx in np.argwhere(risk_fire > 0.01):
                sy, sx = int(sy), int(sx)
                dist[sy, sx] = 0.0
                visited[sy, sx] = True
                queue.append((sx, sy))
            while queue:
                cx, cy = queue.popleft()
                for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1, -1), (-1, 1), (1, -1), (1, 1)):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < w and 0 <= ny < h and not visited[ny, nx]:
                        visited[ny, nx] = True
                        dist[ny, nx] = dist[cy, cx] + self.monitoring.cell_size * math.hypot(dx, dy)
                        queue.append((nx, ny))
        else:
            dist[:] = 1.0e6

        # Avoid division by zero; R_f is zero if the platform has no smoke.
        risk = risk_fire * (1.0 + bld_norm) / (1.0 + dist / 10.0)
        risk = np.nan_to_num(risk, nan=0.0, posinf=0.0, neginf=0.0)
        if risk.max() > 1e-12:
            risk = risk / risk.max()
        self.risk_field = risk
        return risk

    def _edge_risk(self, x: int, y: int) -> float:
        if 0 <= x < self.monitoring.width and 0 <= y < self.monitoring.height:
            return float(self.risk_field[y, x])
        return 0.0

    def _weighted_edge_cost(self, x0: int, y0: int, x1: int, y1: int) -> float:
        """Pareto-weighted cost of entering cell (x1, y1)."""
        step_time = self.base._move_cost(x0, y0, x1, y1)
        if not math.isfinite(step_time):
            return float("inf")
        step_m = self.monitoring.cell_size * math.hypot(x1 - x0, y1 - y0)
        w = self.dual_config.risk_weight
        return ((1.0 - w) * step_time
                + w * self.dual_config.risk_scale * self._edge_risk(x1, y1) * step_m)

    def _cost_field_to_targets(
        self, goal: Tuple[int, int], targets: Sequence[Tuple[int, int]],
    ) -> Tuple[Dict[Tuple[int, int], float], np.ndarray]:
        """Dijkstra from one exit to all person starts in a single search.

        The previous implementation ran one A* per person and per exit (40 x 12
        searches on a large map). This reverse Dijkstra computes every person's
        cost to the same exit once. Parent pointers are retained so each route
        can be reconstructed immediately, then the field is discarded.
        """
        gx, gy = int(goal[0]), int(goal[1])
        if not self.base._passable(gx, gy):
            return {}, np.empty(0, dtype=np.int64)
        width, height = self.monitoring.width, self.monitoring.height
        size = width * height
        target_set = {(int(x), int(y)) for x, y in targets}
        goal_idx = gy * width + gx
        best = np.full(size, np.inf, dtype=np.float64)
        parent = np.full(size, -1, dtype=np.int64)
        closed = np.zeros(size, dtype=bool)
        best[goal_idx] = 0.0
        heap = [(0.0, goal_idx)]
        remaining = set(target_set)
        expansions = 0
        while heap and remaining:
            current_cost, idx = heapq.heappop(heap)
            if closed[idx]:
                continue
            closed[idx] = True
            expansions += 1
            if expansions > self.dual_config.max_expansions:
                break
            x, y = idx % width, idx // width
            remaining.discard((x, y))
            for nx, ny in self.base._neighbors(x, y):
                nidx = ny * width + nx
                if closed[nidx]:
                    continue
                edge = self._weighted_edge_cost(nx, ny, x, y)
                if not math.isfinite(edge):
                    continue
                new_cost = current_cost + edge
                if new_cost + 1e-12 < best[nidx]:
                    best[nidx] = new_cost
                    parent[nidx] = idx
                    heapq.heappush(heap, (new_cost, nidx))
        costs: Dict[Tuple[int, int], float] = {}
        for sx, sy in target_set:
            idx = sy * width + sx
            if 0 <= idx < size and math.isfinite(float(best[idx])):
                costs[(sx, sy)] = float(best[idx])
        return costs, parent

    def _path_from_parent(
        self, start: Tuple[int, int], goal: Tuple[int, int], parent: np.ndarray,
    ) -> Optional[List[Tuple[int, int]]]:
        width = self.monitoring.width
        sx, sy = int(start[0]), int(start[1])
        gx, gy = int(goal[0]), int(goal[1])
        if (sx, sy) == (gx, gy):
            return [(sx, sy)]
        idx = sy * width + sx
        goal_idx = gy * width + gx
        path = [(sx, sy)]
        seen = {idx}
        while idx != goal_idx:
            nxt = int(parent[idx]) if 0 <= idx < parent.size else -1
            if nxt < 0 or nxt in seen:
                return None
            idx = nxt
            seen.add(idx)
            path.append((idx % width, idx // width))
        return path

    def _plan_routes(
        self, starts: Dict[int, Tuple[int, int]],
        probabilities: Dict[int, Dict[str, float]],
    ) -> Dict[int, DualRouteResult]:
        best: Dict[int, Tuple[float, float, float, str, List[Tuple[int, int]], float, float]] = {}
        for eid, goal in self.exit_positions.items():
            costs, parent = self._cost_field_to_targets(goal, list(starts.values()))
            if not costs:
                continue
            for person_id, start in starts.items():
                if start not in costs:
                    continue
                path = self._path_from_parent(start, goal, parent)
                if not path:
                    continue
                time_s, risk, distance_m = self._metrics(path)
                probs = probabilities.get(person_id, {})
                candidate = (
                    float(costs[start]),
                    -float(probs.get(eid, 0.0)),
                    float(time_s),
                    str(eid),
                    path,
                    float(risk),
                    float(distance_m),
                )
                current = best.get(person_id)
                if current is None or candidate[:4] < current[:4]:
                    best[person_id] = candidate
        results: Dict[int, DualRouteResult] = {}
        for person_id, candidate in best.items():
            _, neg_prob, time_s, eid, path, risk, distance_m = candidate
            results[person_id] = DualRouteResult(
                person_id=int(person_id), start=starts[person_id], exit_id=eid,
                path=path, time_s=time_s, risk=risk, distance_m=distance_m,
                exit_probability=float(-neg_prob),
                probabilities=probabilities.get(person_id, {}),
                risk_weight=self.dual_config.risk_weight,
            )
        return results

    def _astar(self, start: Tuple[int, int], goal: Tuple[int, int]) -> Optional[List[Tuple[int, int]]]:
        sx, sy = int(start[0]), int(start[1])
        gx, gy = int(goal[0]), int(goal[1])
        if not (0 <= sx < self.monitoring.width and 0 <= sy < self.monitoring.height):
            return None
        if (sx, sy) == (gx, gy):
            return [(sx, sy)]
        if not self.base._passable(gx, gy):
            return None

        w = self.dual_config.risk_weight
        time_weight = 1.0 - w
        heap = [(time_weight * self.base._heuristic(sx, sy, gx, gy), 0.0, sx, sy)]
        best = {(sx, sy): 0.0}
        parent = {}
        closed = set()
        expansions = 0
        while heap:
            _, gcur, x, y = heapq.heappop(heap)
            if (x, y) in closed:
                continue
            closed.add((x, y))
            expansions += 1
            if expansions > self.dual_config.max_expansions:
                return None
            if (x, y) == (gx, gy):
                path = [(x, y)]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                path.reverse()
                return path
            for nx, ny in self.base._neighbors(x, y):
                step_time = self.base._move_cost(x, y, nx, ny)
                if not math.isfinite(step_time):
                    continue
                step_m = self.monitoring.cell_size * (math.sqrt(2.0) if nx != x and ny != y else 1.0)
                edge_cost = time_weight * step_time + w * self.dual_config.risk_scale * self._edge_risk(nx, ny) * step_m
                ng = gcur + edge_cost
                if ng + 1e-12 < best.get((nx, ny), float("inf")):
                    best[(nx, ny)] = ng
                    parent[(nx, ny)] = (x, y)
                    h = time_weight * self.base._heuristic(nx, ny, gx, gy)
                    heapq.heappush(heap, (ng + h, ng, nx, ny))
        return None

    def _metrics(self, path: Sequence[Tuple[int, int]]) -> Tuple[float, float, float]:
        time_s = 0.0
        risk = 0.0
        distance_m = 0.0
        for (x0, y0), (x1, y1) in zip(path[:-1], path[1:]):
            time_s += self.base._raw_move_cost(x0, y0, x1, y1)
            step_m = self.monitoring.cell_size * math.hypot(x1 - x0, y1 - y0)
            distance_m += step_m
            risk += self._edge_risk(x1, y1) * step_m
        return time_s, risk, distance_m

    def _route_objective(self, path: Sequence[Tuple[int, int]]) -> float:
        """Pareto-weighted objective used to compare complete exit routes."""
        w = self.dual_config.risk_weight
        value = 0.0
        for (x0, y0), (x1, y1) in zip(path[:-1], path[1:]):
            step_time = self.base._move_cost(x0, y0, x1, y1)
            if not math.isfinite(step_time):
                return float("inf")
            step_m = self.monitoring.cell_size * math.hypot(x1 - x0, y1 - y0)
            value += ((1.0 - w) * step_time
                      + w * self.dual_config.risk_scale * self._edge_risk(x1, y1) * step_m)
        return value

    def choose_route(self, person_id: int, start: Tuple[int, int]) -> Optional[DualRouteResult]:
        """Choose the best complete route after evaluating every exit."""
        probs = self.base.exit_probabilities(start)
        results = self._plan_routes({int(person_id): (int(start[0]), int(start[1]))},
                                    {int(person_id): probs})
        return results.get(int(person_id))

    def plan_all(self, persons: Sequence[Any], step: int, dt: float = 0.5,
                 force: bool = False, smoke_grid=None,
                 smoke_sources: Optional[Sequence[Any]] = None) -> Dict[int, DualRouteResult]:
        changed = self.update_smoke(smoke_grid)
        interval_steps = max(1, int(round(self.dual_config.replan_interval_s / max(dt, 1e-6))))
        if not force and not changed and self.last_plan_step is not None and step - self.last_plan_step < interval_steps:
            return dict(self.results)
        self.build_risk_field(persons, smoke_sources)
        starts: Dict[int, Tuple[int, int]] = {}
        probabilities: Dict[int, Dict[str, float]] = {}
        for person in persons:
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            start = (int(person.x), int(person.y))
            starts[int(person.id)] = start
            probabilities[int(person.id)] = self.base.exit_probabilities(start)
        results = self._plan_routes(starts, probabilities)
        self.results = results
        self.last_plan_step = int(step)
        self.replan_count += 1
        return dict(results)

    def next_cell(self, person_id: int, current: Tuple[int, int]):
        result = self.results.get(int(person_id))
        if result is None or not result.path:
            return None
        path = result.path
        cur = (int(current[0]), int(current[1]))
        if cur in path:
            idx = path.index(cur)
            return path[min(idx + 1, len(path) - 1)]
        best_i, best_d = 0, float("inf")
        for i, cell in enumerate(path):
            d = (cell[0] - cur[0]) ** 2 + (cell[1] - cur[1]) ** 2
            if d < best_d:
                best_i, best_d = i, d
        target_i = min(best_i + 1, len(path) - 1)
        adjacent = []
        for i, cell in enumerate(path):
            if max(abs(cell[0] - cur[0]), abs(cell[1] - cur[1])) == 1:
                adjacent.append((abs(i - target_i), (cell[0] - cur[0]) ** 2 + (cell[1] - cur[1]) ** 2, i, cell))
        if adjacent:
            adjacent.sort()
            return adjacent[0][3]
        return path[best_i]

    def statistics(self) -> Dict[str, Any]:
        times = [r.time_s for r in self.results.values()]
        risks = [r.risk for r in self.results.values()]
        return {
            "map_name": self.map_name,
            "routes": len(self.results),
            "replans": self.replan_count,
            "risk_weight": self.dual_config.risk_weight,
            "mean_time_s": float(np.mean(times)) if times else 0.0,
            "max_time_s": float(np.max(times)) if times else 0.0,
            "mean_route_risk": float(np.mean(risks)) if risks else 0.0,
            "max_risk_field": float(np.max(self.risk_field)) if self.risk_field.size else 0.0,
            "blocked_cells": int(np.sum(self.monitoring.blocked)),
            "mean_region_speed": float(np.mean(self.monitoring.speed)),
        }

    def export_json(self, path: str | Path) -> str:
        payload = {
            "map": str(self.map_path), "map_name": self.map_name,
            "strategy": "dual_objective_aco",
            "paper": "Yang et al. (2026), DOI 10.13693/j.cnki.cn21-1573.2026.03.005",
            "risk_weight": self.dual_config.risk_weight,
            "risk_model": "R_r = R_f * BLD / l (smoke proxy for absent FDS fields)",
            "routes": [r.to_dict() for r in self.results.values()],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def render(self, path: str | Path, smoke_sources=None) -> str:
        # Reuse the common route-map renderer; it consumes the same result fields.
        return render_route_map(self, path, title=f"{self.map_name} | dual-objective (time + risk) routes", smoke_sources=smoke_sources)
