"""Fire-aware adaptive evacuation control.

This module implements the control layer described by the three reference
papers supplied by the user:

* Jiang Hualong & Jiang Huixian (2026), "Optimization model based on the ant
  colony optimization algorithm for emergency evacuation path planning in
  library fires".  The paper defines temperature / CO / visibility speed
  factors, the fire-scene index W, passage difficulty epsilon = v0 / vt and
  equivalent length L = d * epsilon.  It also reports the critical values
  60 degC, 0.5e-3 mol/mol CO and 10 m visibility.
* Liu Binwen (2025), "Modeling and Characterization of Multi-Speed
  Heterogeneous Crowd Evacuation under Differentiated Competitiveness".  The
  paper reports the density watershed 0.375 person/m^2, the recommended
  pheromone diffusion radius 2-4 m (3.2 m in chapters 4-5), avoidance
  probability 0.5, avoidance threshold 0.4-0.8 and the recommendation to use
  the elderly competitiveness as the threshold.  It also defines the
  pheromone deposit / diffusion / decay / self-correction rules and the
  differentiated competitiveness formula.
* Zhang Peihong, Chen Baozhi & Liu Lizhen (2001), "Study on evacuation
  behaviour in large public buildings during fire".  The paper stresses
  spatial classification of evacuation spaces, planned evacuation routes,
  exit/stair congestion and available safe evacuation time as the key
  evaluation indices.

The current platform does not yet simulate FDS temperature or CO fields.  The
router therefore accepts optional temperature/CO/visibility grids and applies
Jiang's formulas exactly when they are available.  When only the platform's
normalized smoke field is available, it uses a monotonic smoke-penalty proxy
(clearly marked as a proxy, not as an FDS result) so that the control layer can
still respond to the smoke field.  No temperature/CO values are fabricated.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from core.schema import CellType
except Exception:  # pragma: no cover - allows standalone import in tests
    CellType = None


# ---------------------------------------------------------------------------
# Paper parameters
# ---------------------------------------------------------------------------

# Jiang et al. (2026), section 3.2.1, equations (6)-(13)
TEMP_NORMAL_C = 20.0
TEMP_COMFORT_C = 30.0
TEMP_INJURY_C = 60.0
TEMP_DEATH_C = 120.0
V_NORMAL = 1.2
V_MAX = 4.0
CO_LOW = 0.1
CO_HIGH = 0.25
VISIBILITY_CONSTANT = 3.0       # light-attenuating objects in a library
KC_LOW = 0.35
KC_HIGH = 1.2

# Liu (2025), chapters 3-5
DENSITY_WATERSHED = 0.375       # person/m^2
PHEROMONE_RADIUS_M = 3.2        # m, selected value in chapters 4-5
AVOID_PROBABILITY = 0.5         # minimum evacuation time in chapter 5
AVOID_THRESHOLD_DEFAULT = 0.6   # elderly competitiveness 0.54-0.63; range 0.4-0.8
AVOID_THRESHOLD_RANGE = (0.4, 0.8)

# Zhang et al. (2001): exit/stair congestion and planned routes are key
# evaluation indices.  This is a routing penalty, not a physical closure.
EXIT_CONGESTION_WEIGHT_M = 0.5


@dataclass
class FireAwareConfig:
    """Configuration of the paper-grounded control layer."""

    # Liu (2025) density watershed and pheromone radius.
    density_threshold: float = DENSITY_WATERSHED
    density_radius_m: float = 2.0
    pheromone_radius_m: float = PHEROMONE_RADIUS_M
    pheromone_diffusion: float = 0.30
    pheromone_decay: float = 0.10

    # Liu (2025) avoidance behaviour.
    avoid_probability: float = AVOID_PROBABILITY
    avoid_threshold: float = AVOID_THRESHOLD_DEFAULT

    # Jiang (2026) dynamic re-planning: the paper divides the 600 s fire into
    # 0-200 s, 200-400 s and 400-600 s stages.
    replan_interval_s: float = 200.0
    hazard_change_threshold: float = 0.12

    # Zhang (2001) congestion-aware exit choice.
    exit_congestion_weight_m: float = EXIT_CONGESTION_WEIGHT_M
    vulnerable_congestion_weight_m: float = 1.0
    # In the integrated strategy, keep the zoned-evacuation assignment as a
    # soft preference; a fire-aware route may override it only when the
    # equivalent-distance advantage exceeds this margin.
    zone_bias_m: float = 4.0
    # Locked cells are normally impassable through the control API; the
    # router uses a finite penalty so people already inside can still leave.
    blocked_cost_multiplier: float = 5.0
    # Jiang (2026): prefer exits whose hazard indicators rise later.
    # This is a routing penalty (m per unit drop in passage factor), not a
    # physical exit closure.
    exit_trend_weight_m: float = 10.0
    hysteresis_ratio: float = 0.10

    # Optional FDS fields.  When only smoke is available, this is a proxy.
    smoke_proxy_weight: float = 3.0
    exit_unsafe_factor: float = 0.30
    exit_unsafe_smoke: float = 0.90

    # Real smoke-source proximity is a routing constraint, not a synthetic
    # concentration field. It makes people detour around the source before
    # the smoke matrix has spread.
    source_avoid_radius_m: float = 4.0
    source_avoid_penalty: float = 12.0

    # Competitiveness: Liu (2025) equation (4.1) and Table 4.2.  The paper
    # uses a power function (Stevens' law); exponent 0.5 is the square-root
    # form used here.  Coefficients are taken from the paper's table.
    competitiveness_exponent: float = 0.5
    speed_reference: float = 1.4

    def validate(self) -> None:
        self.density_threshold = float(max(0.0, self.density_threshold))
        self.density_radius_m = float(max(0.1, self.density_radius_m))
        self.pheromone_radius_m = float(max(0.0, self.pheromone_radius_m))
        self.pheromone_diffusion = float(np.clip(self.pheromone_diffusion, 0.0, 1.0))
        self.pheromone_decay = float(np.clip(self.pheromone_decay, 0.0, 1.0))
        self.avoid_probability = float(np.clip(self.avoid_probability, 0.0, 1.0))
        self.avoid_threshold = float(np.clip(self.avoid_threshold, 0.0, 1.0))
        self.replan_interval_s = float(max(0.0, self.replan_interval_s))
        self.hysteresis_ratio = float(max(0.0, self.hysteresis_ratio))
        self.zone_bias_m = float(max(0.0, self.zone_bias_m))
        self.blocked_cost_multiplier = float(max(1.0, self.blocked_cost_multiplier))
        self.smoke_proxy_weight = float(max(0.0, self.smoke_proxy_weight))
        self.source_avoid_radius_m = float(max(0.0, self.source_avoid_radius_m))
        self.source_avoid_penalty = float(max(0.0, self.source_avoid_penalty))


def _as_2d(value: Any, shape: Tuple[int, int], name: str) -> Optional[np.ndarray]:
    if value is None:
        return None
    arr = np.asarray(value, dtype=float)
    if arr.ndim == 1 and arr.size == shape[0] * shape[1]:
        arr = arr.reshape(shape)
    if arr.shape != shape:
        raise ValueError(f"{name} shape {arr.shape} does not match grid {shape}")
    return np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)


def temperature_speed_factor(temperature_c: Any, config: FireAwareConfig = None) -> np.ndarray:
    """Jiang et al. (2026), equation (6): f1(T)."""
    t = np.asarray(temperature_c, dtype=float)
    out = np.ones_like(t, dtype=float)
    low = (t > TEMP_COMFORT_C) & (t <= TEMP_INJURY_C)
    if np.any(low):
        out[low] = 1.0 + (V_MAX / V_NORMAL - 1.0) * (
            (t[low] - TEMP_COMFORT_C) / (TEMP_INJURY_C - TEMP_COMFORT_C)
        ) ** 2
    high = (t > TEMP_INJURY_C) & (t <= TEMP_DEATH_C)
    if np.any(high):
        out[high] = (V_MAX / V_NORMAL) * (
            1.0 - ((t[high] - TEMP_INJURY_C) / (TEMP_DEATH_C - TEMP_INJURY_C)) ** 2
        )
    out[t > TEMP_DEATH_C] = 0.0
    out[t < TEMP_NORMAL_C] = 1.0
    return np.clip(out, 0.0, V_MAX / V_NORMAL)


def co_speed_factor(co: Any, exposure_s: float) -> np.ndarray:
    """Jiang et al. (2026), equation (7): f2(rho_CO).

    The paper writes the middle branch as 1 - (0.2123 + 1.788 rho) t.  The
    exposure time is converted to minutes so the factor is not an instant
    zero at the first simulation step.
    """
    rho = np.asarray(co, dtype=float)
    out = np.ones_like(rho, dtype=float)
    t_min = max(0.0, float(exposure_s)) / 60.0
    mid = (rho >= CO_LOW) & (rho < CO_HIGH)
    if np.any(mid):
        out[mid] = 1.0 - (0.2123 + 1.788 * rho[mid]) * t_min
    out[rho >= CO_HIGH] = 0.0
    return np.clip(out, 0.0, 1.0)


def visibility_speed_factor(visibility_m: Any) -> np.ndarray:
    """Jiang et al. (2026), equation (8): f3(Kc), Kc = C / S."""
    visibility = np.asarray(visibility_m, dtype=float)
    kc = VISIBILITY_CONSTANT / np.maximum(visibility, 1e-6)
    out = np.ones_like(kc, dtype=float)
    mid = (kc >= KC_LOW) & (kc < KC_HIGH)
    if np.any(mid):
        out[mid] = 1.0 - 0.3025 * kc[mid]
    out[kc >= KC_HIGH] = 0.25
    return np.clip(out, 0.0, 1.0)


@dataclass
class FireHazardField:
    """Fire hazard field with optional FDS fields and smoke fallback."""

    grid: Any
    config: FireAwareConfig = field(default_factory=FireAwareConfig)
    width: int = 0
    height: int = 0
    cell_size: float = 0.5
    passage_factor: np.ndarray = field(init=False)
    smoke: Optional[np.ndarray] = field(init=False, default=None)
    temperature: Optional[np.ndarray] = field(init=False, default=None)
    co: Optional[np.ndarray] = field(init=False, default=None)
    visibility: Optional[np.ndarray] = field(init=False, default=None)

    def __post_init__(self):
        self.config.validate()
        if self.grid is not None:
            self.width = int(getattr(self.grid, "width", self.width or 0))
            self.height = int(getattr(self.grid, "height", self.height or 0))
            self.cell_size = float(getattr(self.grid, "cell_size", self.cell_size) or self.cell_size)
        if self.width <= 0 or self.height <= 0:
            raise ValueError("FireHazardField requires positive grid dimensions")
        self.passage_factor = np.ones((self.height, self.width), dtype=float)

    def update(
        self,
        smoke: Any = None,
        temperature: Any = None,
        co: Any = None,
        visibility: Any = None,
        exposure_s: float = 0.0,
    ) -> None:
        shape = (self.height, self.width)
        self.smoke = _as_2d(smoke, shape, "smoke")
        self.temperature = _as_2d(temperature, shape, "temperature")
        self.co = _as_2d(co, shape, "co")
        self.visibility = _as_2d(visibility, shape, "visibility")

        exact_factor = None
        if self.temperature is not None:
            exact_factor = temperature_speed_factor(self.temperature, self.config)
        if self.co is not None:
            f2 = co_speed_factor(self.co, exposure_s)
            exact_factor = f2 if exact_factor is None else exact_factor * f2
        if self.visibility is not None:
            f3 = visibility_speed_factor(self.visibility)
            exact_factor = f3 if exact_factor is None else exact_factor * f3

        if exact_factor is None:
            exact_factor = np.ones(shape, dtype=float)

        # The platform currently exposes only a normalized smoke field.
        # This branch is a transparent monotonic proxy, not an FDS result.
        if self.smoke is not None:
            smoke_proxy = 1.0 / (1.0 + self.config.smoke_proxy_weight * np.clip(self.smoke, 0.0, 1.0))
            if self.visibility is None:
                exact_factor = exact_factor * smoke_proxy

        self.passage_factor = np.clip(exact_factor, 0.05, V_MAX / V_NORMAL)

    def factor_at(self, x: int, y: int) -> float:
        if 0 <= x < self.width and 0 <= y < self.height:
            return float(self.passage_factor[y, x])
        return 1.0

    def edge_cost_multiplier(self, x: int, y: int) -> float:
        # Jiang et al. equation (11)-(12): epsilon = v0 / vt = 1 / W.
        return 1.0 / max(self.factor_at(x, y), 0.05)

    def smoke_at(self, x: int, y: int) -> float:
        if self.smoke is not None and 0 <= x < self.width and 0 <= y < self.height:
            return float(self.smoke[y, x])
        return 0.0

    def exit_factor(self, x: int, y: int, radius: int = 1) -> float:
        x0, x1 = max(0, x - radius), min(self.width, x + radius + 1)
        y0, y1 = max(0, y - radius), min(self.height, y + radius + 1)
        return float(np.mean(self.passage_factor[y0:y1, x0:x1]))

    def exit_smoke(self, x: int, y: int, radius: int = 1) -> float:
        if self.smoke is None:
            return 0.0
        x0, x1 = max(0, x - radius), min(self.width, x + radius + 1)
        y0, y1 = max(0, y - radius), min(self.height, y + radius + 1)
        return float(np.mean(self.smoke[y0:y1, x0:x1]))


class PheromoneField:
    """Liu (2025), equations (3.1)-(3.4).

    The implementation stores a virtual dynamic field, deposits one unit when
    an agent visits a cell, diffuses it over 8-neighbours, decays it, and
    subtracts the agent's own previous deposit before the update.  The latter
    is the one-step self-correction discussed in section 3.2.
    """

    def __init__(self, width: int, height: int, config: FireAwareConfig):
        self.width = width
        self.height = height
        self.config = config
        self.field = np.zeros((height, width), dtype=float)
        self._last_deposit: Dict[int, Tuple[int, int]] = {}

    def update(self, persons: Sequence[Any]) -> None:
        # Remove each person's own previous deposit (self-correction).
        for pid, (x, y) in list(self._last_deposit.items()):
            if 0 <= x < self.width and 0 <= y < self.height:
                self.field[y, x] = max(0.0, self.field[y, x] - 1.0)
        self._last_deposit.clear()

        for person in persons:
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            x, y = int(person.x), int(person.y)
            if 0 <= x < self.width and 0 <= y < self.height:
                self.field[y, x] += 1.0
                self._last_deposit[int(person.id)] = (x, y)

        # Diffusion and decay (Liu 2025, equation 3.2).
        pad = np.pad(self.field, 1, mode="edge")
        neighbours = (
            pad[:-2, 1:-1] + pad[2:, 1:-1] + pad[1:-1, :-2] + pad[1:-1, 2:]
        ) / 4.0
        sigma = self.config.pheromone_diffusion
        tau = self.config.pheromone_decay
        self.field = (1.0 - tau) * ((1.0 - sigma) * self.field + sigma * neighbours)
        np.clip(self.field, 0.0, None, out=self.field)

    def at(self, x: int, y: int) -> float:
        if 0 <= x < self.width and 0 <= y < self.height:
            return float(self.field[y, x])
        return 0.0

    def mean(self) -> float:
        return float(np.mean(self.field)) if self.field.size else 0.0


class FireAwareRouter:
    """Equivalent-distance router + density-adaptive assignment.

    The paper uses an improved ant colony algorithm.  This implementation uses
    the same paper-defined equivalent edge length in a deterministic Dijkstra
    search.  This keeps the control layer lightweight and reproducible while
    preserving the paper's hazard-avoidance and dynamic re-planning logic.
    """

    DIRS = (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    )

    def __init__(
        self,
        grid: Any,
        exit_positions: Dict[str, Tuple[int, int]],
        config: Optional[FireAwareConfig] = None,
        width: int = 0,
        height: int = 0,
    ):
        self.grid = grid
        self.exit_positions = dict(exit_positions or {})
        self.config = config or FireAwareConfig()
        self.config.validate()

        self.width = int(getattr(grid, "width", width) if grid is not None else width)
        self.height = int(getattr(grid, "height", height) if grid is not None else height)
        if self.width <= 0 or self.height <= 0:
            max_x = max((p[0] for p in self.exit_positions.values()), default=0)
            max_y = max((p[1] for p in self.exit_positions.values()), default=0)
            self.width = max_x + 1
            self.height = max_y + 1
        self.cell_size = float(getattr(grid, "cell_size", 0.5) if grid is not None else 0.5)

        self.walkable = np.ones((self.height, self.width), dtype=bool)
        self.blocked = np.zeros((self.height, self.width), dtype=bool)
        if grid is not None:
            for cell in getattr(grid, "cells", []):
                x, y = int(getattr(cell, "x", -1)), int(getattr(cell, "y", -1))
                if not (0 <= x < self.width and 0 <= y < self.height):
                    continue
                ctype = getattr(cell, "cell_type", None)
                if CellType is not None and ctype in (CellType.WALL, CellType.OBSTACLE):
                    self.walkable[y, x] = False
                elif str(getattr(ctype, "value", ctype)).lower() in ("wall", "obstacle"):
                    self.walkable[y, x] = False

        self.hazard = FireHazardField(
            grid=grid, config=self.config, width=self.width, height=self.height,
            cell_size=self.cell_size,
        )
        self.pheromone = PheromoneField(self.width, self.height, self.config)
        self.smoke_sources: List[Any] = []
        self.source_multiplier = np.ones((self.height, self.width), dtype=float)
        self.planned_paths: Dict[int, List[Tuple[int, int]]] = {}
        self.next_cells: Dict[int, Tuple[int, int]] = {}
        self.fields: Dict[str, np.ndarray] = {}
        self.last_plan_step: Optional[int] = None
        self.last_hazard_signature: Optional[float] = None
        self.route_target: Dict[int, str] = {}
        self.assigned_exit_counts: Dict[str, int] = {}
        self.exit_factor_prev: Dict[str, float] = {}
        self.exit_factor_trend: Dict[str, float] = {}
        self.last_density: float = 0.0
        self._last_high_density: bool = False
        self._congestion_active: bool = False
        self.replan_count = 0
        self.assignment_count = 0
        self.vulnerable_assignment_count = 0
        self._rng = np.random.default_rng(20251018)

    def set_smoke_sources(self, smoke_sources: Optional[Sequence[Any]] = None) -> None:
        """Apply a deterministic detour cost around real smoke-source cells."""
        self.smoke_sources = list(smoke_sources or [])
        self.source_multiplier = np.ones((self.height, self.width), dtype=float)
        radius = float(self.config.source_avoid_radius_m)
        if radius > 0.0 and self.config.source_avoid_penalty > 0.0:
            radius_cells = int(math.ceil(radius / max(self.cell_size, 1e-6)))
            for source in self.smoke_sources:
                sx = int(getattr(source, "x", -1))
                sy = int(getattr(source, "y", -1))
                if not (0 <= sx < self.width and 0 <= sy < self.height):
                    continue
                intensity = float(np.clip(abs(float(getattr(source, "intensity", 1.0) or 1.0)), 0.25, 1.0))
                for y in range(max(0, sy - radius_cells), min(self.height, sy + radius_cells + 1)):
                    for x in range(max(0, sx - radius_cells), min(self.width, sx + radius_cells + 1)):
                        distance_m = math.hypot(x - sx, y - sy) * self.cell_size
                        if distance_m > radius:
                            continue
                        closeness = 1.0 - distance_m / radius
                        factor = 1.0 + self.config.source_avoid_penalty * intensity * closeness
                        self.source_multiplier[y, x] = max(self.source_multiplier[y, x], factor)
        self.fields.clear()
        self.last_plan_step = None
        self.last_hazard_signature = None

    # -- hazard / routing -------------------------------------------------
    def update_hazard(
        self,
        smoke: Any = None,
        temperature: Any = None,
        co: Any = None,
        visibility: Any = None,
        exposure_s: float = 0.0,
    ) -> None:
        self.hazard.update(smoke, temperature, co, visibility, exposure_s=exposure_s)

    def _hazard_signature(self) -> float:
        return float(np.mean(self.hazard.passage_factor) + 0.5 * np.min(self.hazard.passage_factor))

    def _should_replan(self, step: int, dt: float, force: bool) -> bool:
        if force or not self.fields:
            return True
        interval_steps = max(1, int(round(self.config.replan_interval_s / max(dt, 1e-6))))
        if self.last_plan_step is None or step - self.last_plan_step >= interval_steps:
            return True
        sig = self._hazard_signature()
        if self.last_hazard_signature is not None:
            if abs(sig - self.last_hazard_signature) >= self.config.hazard_change_threshold:
                return True
        return False

    def recompute_fields(self, step: int, dt: float = 0.5, force: bool = False) -> None:
        if not self._should_replan(step, dt, force):
            return
        self.fields = {}
        for eid, (ex, ey) in self.exit_positions.items():
            if not (0 <= ex < self.width and 0 <= ey < self.height):
                continue
            self.fields[eid] = self._dijkstra(int(ex), int(ey))
        self.last_plan_step = int(step)
        self.last_hazard_signature = self._hazard_signature()
        self._refresh_exit_trends()
        self.replan_count += 1

    def _dijkstra(self, ex: int, ey: int) -> np.ndarray:
        dist = np.full((self.height, self.width), np.inf, dtype=float)
        if not (0 <= ex < self.width and 0 <= ey < self.height):
            return dist
        dist[ey, ex] = 0.0
        heap: List[Tuple[float, int, int]] = [(0.0, ex, ey)]
        while heap:
            d, x, y = heapq.heappop(heap)
            if d > dist[y, x] + 1e-12:
                continue
            for dx, dy in self.DIRS:
                nx, ny = x + dx, y + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                if not self.walkable[ny, nx]:
                    continue
                step_dist = self.cell_size * (math.sqrt(2.0) if dx and dy else 1.0)
                nd = d + step_dist * self.hazard.edge_cost_multiplier(nx, ny)
                nd *= max(1.0, float(self.source_multiplier[ny, nx]))
                if self.blocked[ny, nx]:
                    nd *= self.config.blocked_cost_multiplier
                if nd + 1e-12 < dist[ny, nx]:
                    dist[ny, nx] = nd
                    heapq.heappush(heap, (nd, nx, ny))
        return dist

    # -- exit safety ------------------------------------------------------
    def is_exit_safe(self, exit_id: str) -> bool:
        if exit_id not in self.exit_positions:
            return False
        ex, ey = self.exit_positions[exit_id]
        if self.hazard.exit_factor(ex, ey) < self.config.exit_unsafe_factor:
            return False
        if self.hazard.exit_smoke(ex, ey) > self.config.exit_unsafe_smoke:
            return False
        return True

    def open_safe_exits(self, allowed: Optional[Sequence[str]] = None) -> List[str]:
        candidates = [e for e in (allowed or list(self.exit_positions.keys())) if e in self.fields]
        safe = [e for e in candidates if self.is_exit_safe(e)]
        # Do not deadlock if all exits are currently unsafe; use the allowed
        # set as a fallback and let the hazard penalty still steer choices.
        return safe if safe else candidates

    def set_blocked_cells(self, blocked_cells: Any) -> None:
        """Refresh the lockdown mask used by the equivalent-distance router.

        Blocked cells are penalised rather than removed completely, because
        people already inside a zone must be able to leave.
        """
        self.blocked.fill(False)
        if blocked_cells is None:
            return
        items = blocked_cells.items() if hasattr(blocked_cells, "items") else ((c, True) for c in blocked_cells)
        for cell, is_blocked in items:
            if not is_blocked:
                continue
            try:
                x, y = int(cell[0]), int(cell[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 0 <= x < self.width and 0 <= y < self.height:
                self.blocked[y, x] = True

    def _refresh_exit_trends(self) -> None:
        current = {
            eid: self.hazard.exit_factor(ex, ey)
            for eid, (ex, ey) in self.exit_positions.items()
        }
        self.exit_factor_trend = {
            eid: max(0.0, self.exit_factor_prev.get(eid, val) - val)
            for eid, val in current.items()
        }
        self.exit_factor_prev = current

    # -- density and assignment ------------------------------------------
    def _infer_age_group(self, profile: Any) -> str:
        p = str(profile or "").lower()
        if any(k in p for k in ("elder", "senior", "old", "aged")):
            return "elderly"
        if any(k in p for k in ("child", "kid", "infant")):
            return "child"
        if "patient" in p:
            return "patient"
        if "student" in p or "youth" in p or "teen" in p:
            return "youth"
        if "teacher" in p or "staff" in p or "security" in p or "doctor" in p:
            return "middle"
        return "adult"

    def _age_coefficient(self, age_group: str, pid: int) -> float:
        # Liu (2025), Table 4.2: youth 3-4, middle 1-2, elderly 0-1.5.
        rng = np.random.default_rng(20251018 + int(pid))
        if age_group == "youth":
            return float(rng.uniform(3.0, 4.0))
        if age_group == "middle":
            return float(rng.uniform(1.0, 2.0))
        if age_group == "elderly":
            return float(rng.uniform(0.0, 1.5))
        if age_group in ("child", "patient"):
            return 0.5
        return 1.5

    def assign_person_attributes(self, persons: Sequence[Any]) -> None:
        for person in persons:
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            age_group = getattr(person, "age_group", None) or self._infer_age_group(getattr(person, "profile", ""))
            gender = str(getattr(person, "gender", "") or "").lower()
            gender_coeff = {"male": 0.5, "m": 0.5, "female": 0.4, "f": 0.4}.get(gender, 0.45)
            age_coeff = float(getattr(person, "age_coefficient", 0.0) or 0.0)
            if age_coeff <= 0.0:
                age_coeff = self._age_coefficient(age_group, int(person.id))
            speed = max(0.1, float(getattr(person, "speed", 1.0) or 1.0))
            speed_norm = speed / max(self.config.speed_reference, 0.1)
            competitiveness = (gender_coeff * age_coeff * speed_norm) ** max(
                0.05, self.config.competitiveness_exponent
            )
            person.age_group = age_group
            person.gender = gender or "unspecified"
            person.age_coefficient = age_coeff
            person.competitiveness = float(np.clip(competitiveness, 0.0, 1.0))
            person.vulnerable = age_group in ("elderly", "child", "patient")
            person.avoidance_probability = float(self.config.avoid_probability)
            person.avoidance_threshold = float(self.config.avoid_threshold)
            person.assistance_priority = 1.0 if person.vulnerable else 0.0

    def local_density(self, persons: Sequence[Any], x: float, y: float) -> float:
        radius = max(self.config.density_radius_m, 1e-6)
        radius_cells = radius / max(self.cell_size, 1e-6)
        count = 0
        for p in persons:
            if getattr(p, "evacuated", False) or getattr(p, "is_dead", False):
                continue
            if abs(float(p.x) - x) <= radius_cells and abs(float(p.y) - y) <= radius_cells:
                count += 1
        return count / (math.pi * radius * radius)

    def _score_exit(self, person: Any, eid: str, loads: Dict[str, int]) -> float:
        px, py = int(person.x), int(person.y)
        if not (0 <= px < self.width and 0 <= py < self.height):
            return float("inf")
        dist = float(self.fields[eid][py, px])
        if not np.isfinite(dist):
            return float("inf")
        weight = self.config.exit_congestion_weight_m
        if getattr(person, "vulnerable", False):
            weight = self.config.vulnerable_congestion_weight_m
        if not self._congestion_active:
            weight = 0.0
        trend_penalty = self.config.exit_trend_weight_m * self.exit_factor_trend.get(eid, 0.0)
        return dist + weight * float(loads.get(eid, 0)) + trend_penalty

    def choose_exit(
        self,
        person: Any,
        loads: Dict[str, int],
        allowed: Optional[Sequence[str]] = None,
        zone_target: Optional[str] = None,
    ) -> Optional[str]:
        exits = self.open_safe_exits(allowed)
        if not exits:
            return None
        scored = [(self._score_exit(person, eid, loads), eid) for eid in exits]
        scored = [(s, e) for s, e in scored if np.isfinite(s)]
        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], item[1]))
        best_score, best_exit = scored[0]
        if zone_target in exits:
            # Soft zoning: the assigned zone receives a margin, not a hard
            # constraint, so fire hazard/closures can still override it.
            zone_score = self._score_exit(person, zone_target, loads)
            if np.isfinite(zone_score) and zone_score <= best_score + self.config.zone_bias_m:
                best_score, best_exit = zone_score, zone_target

        # Hysteresis: avoid oscillation unless another exit is clearly better.
        current = getattr(person, "target_exit", None)
        if current in exits:
            current_score = self._score_exit(person, current, loads)
            if np.isfinite(current_score) and current_score <= best_score * (1.0 + self.config.hysteresis_ratio):
                return current
        return best_exit

    def _field_path(
        self,
        person_id: int,
        eid: str,
        start: Tuple[int, int],
        usage: Dict[Tuple[int, int], int],
    ) -> Optional[List[Tuple[int, int]]]:
        """Build a near-optimal path that spreads people across equal-cost lanes."""
        field = self.fields.get(eid)
        if field is None:
            return None
        sx, sy = int(start[0]), int(start[1])
        if not (0 <= sx < self.width and 0 <= sy < self.height):
            return None
        if not np.isfinite(field[sy, sx]):
            return None
        exit_xy = self.exit_positions.get(eid)
        if exit_xy is None:
            return None
        current = (sx, sy)
        path = [current]
        limit = self.width * self.height
        for _ in range(limit):
            if current == exit_xy:
                return path
            cx, cy = current
            current_distance = float(field[cy, cx])
            candidates: List[Tuple[float, float, int, int]] = []
            for dx, dy in self.DIRS:
                nx, ny = cx + dx, cy + dy
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                if not self.walkable[ny, nx] or not np.isfinite(field[ny, nx]):
                    continue
                next_distance = float(field[ny, nx])
                # Only move downhill in the fire-aware field. The usage term
                # is a small tie-breaker, so safety is never sacrificed for
                # lane spreading.
                if next_distance + 1e-12 >= current_distance:
                    continue
                tie = ((int(person_id) * 73856093) ^ (nx * 19349663) ^ (ny * 83492791)) & 1023
                score = next_distance + 0.35 * self.cell_size * usage.get((nx, ny), 0) + tie * 1e-10
                candidates.append((score, next_distance, nx, ny))
            if not candidates:
                return None
            candidates.sort()
            _, _, nx, ny = candidates[0]
            current = (nx, ny)
            path.append(current)
        return None

    def _build_planned_paths(self, alive: Sequence[Any]) -> None:
        self.planned_paths = {}
        self.next_cells = {}
        usage: Dict[Tuple[int, int], int] = defaultdict(int)
        for person in alive:
            pid = int(person.id)
            eid = self.route_target.get(pid)
            if not eid:
                continue
            path = self._field_path(pid, eid, (int(person.x), int(person.y)), usage)
            if not path:
                continue
            self.planned_paths[pid] = path
            next_cell = path[1] if len(path) > 1 else path[0]
            self.next_cells[pid] = next_cell
            person.planned_path = path
            person.route_next_cell = next_cell
            for cell in path:
                usage[cell] += 1

    def next_cell(self, person_id: int, current: Tuple[int, int]):
        path = self.planned_paths.get(int(person_id))
        if not path:
            return None
        cur = (int(current[0]), int(current[1]))
        if cur in path:
            idx = path.index(cur)
            return path[min(idx + 1, len(path) - 1)]
        best_i, best_d = 0, float("inf")
        for i, cell in enumerate(path):
            distance = (cell[0] - cur[0]) ** 2 + (cell[1] - cur[1]) ** 2
            if distance < best_d:
                best_i, best_d = i, distance
        target_i = min(best_i + 1, len(path) - 1)
        adjacent = []
        for i, cell in enumerate(path):
            if max(abs(cell[0] - cur[0]), abs(cell[1] - cur[1])) == 1:
                adjacent.append((abs(i - target_i), i, cell))
        if adjacent:
            adjacent.sort()
            return adjacent[0][2]
        return path[best_i]

    def plan(
        self,
        persons: Sequence[Any],
        step: int,
        dt: float = 0.5,
        allowed_exits: Optional[Sequence[str]] = None,
        zone_targets: Optional[Dict[int, str]] = None,
        force: bool = False,
    ) -> Dict[int, str]:
        alive = [p for p in persons if not getattr(p, "evacuated", False) and not getattr(p, "is_dead", False)]
        alive_ids = {int(p.id) for p in alive}
        self.route_target = {pid: eid for pid, eid in self.route_target.items() if pid in alive_ids}
        self.assign_person_attributes(alive)
        self.pheromone.update(alive)
        self.recompute_fields(step, dt=dt, force=force)

        # The paper's density watershed determines whether congestion-aware
        # load spreading is active.  Below the watershed, the shortest
        # equivalent route is preferred; above it, exit congestion is added.
        densities = [self.local_density(alive, p.x, p.y) for p in alive] if alive else [0.0]
        self.last_density = float(np.mean(densities))
        high_density = self.last_density >= self.config.density_threshold
        self._congestion_active = bool(high_density)

        loads: Dict[str, int] = defaultdict(int)
        # Vulnerable people are assigned first so they are not left with the
        # most congested exit.  This follows Liu (2025)'s recommendation to
        # provide dedicated/assisted evacuation for the elderly.
        order = sorted(alive, key=lambda p: (0 if getattr(p, "vulnerable", False) else 1, int(p.id)))
        for person in order:
            zone_target = zone_targets.get(int(person.id)) if zone_targets else None
            eid = self.choose_exit(
                person, loads, allowed=allowed_exits, zone_target=zone_target,
            )
            if eid is None:
                continue
            person.target_exit = eid
            person.route_target = eid
            self.route_target[int(person.id)] = eid
            loads[eid] += 1
            self.assignment_count += 1
            if getattr(person, "vulnerable", False):
                self.vulnerable_assignment_count += 1
        self.assigned_exit_counts = dict(loads)
        self._last_high_density = bool(high_density)
        self._build_planned_paths(order)
        return dict(self.route_target)

    def statistics(self) -> Dict[str, Any]:
        return {
            "replans": self.replan_count,
            "assigned": self.assignment_count,
            "assigned_current": len(self.route_target),
            "vulnerable_assigned": self.vulnerable_assignment_count,
            "assigned_exit_counts": dict(self.assigned_exit_counts),
            "mean_density": self.last_density,
            "density_threshold": self.config.density_threshold,
            "pheromone_mean": self.pheromone.mean(),
            "pheromone_radius_m": self.config.pheromone_radius_m,
            "avoid_probability": self.config.avoid_probability,
            "avoid_threshold": self.config.avoid_threshold,
            "planned_paths": len(self.planned_paths),
            "source_avoid_radius_m": self.config.source_avoid_radius_m,
            "smoke_sources": len(self.smoke_sources),
        }
