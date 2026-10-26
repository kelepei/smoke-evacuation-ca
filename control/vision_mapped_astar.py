"""Vision-mapped dynamic A* evacuation control.

This module follows Tian Wei et al. (2026), "Research on dynamic evacuation
route planning methods for fire scenarios during renovation construction of
existing buildings", Journal of Safety and Environment, DOI
10.13637/j.issn.1009-6094.2025.1925.

Paper-to-code mapping
---------------------
* Fixed cameras + YOLOv8 detect fire, construction waste/material stacks,
  rebar tying, steel-pipe supports, machinery and unremoved obstacles.
* OpenCV/perspective transformation maps image bounding boxes to the 2-D
  evacuation grid.  Multiple camera views are merged by taking the union of
  their mapped rectangles.
* The improved A* heuristic uses time instead of distance:
      f(n) = g(n) + sum_i h(n)_i / v_i
  where v_i are the construction-zone speeds in Table 2 of the paper.
* Exit target selection uses the paper's distance probability:
      P_j = exp(-D_j) / sum_k exp(-D_k)
* The monitoring grid is refreshed during the run so the planned routes follow
  changes in the construction site.

The platform does not ship a YOLOv8 model.  Monitoring detections are therefore
read from the selected A map only: map-cell ``type``, ``zone_type``,
``construction_type`` and ``semantic`` fields.  No external monitoring file is
read and no detections or fire locations are fabricated.
"""

from __future__ import annotations

import json
import heapq
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    from core.schema import CellType
except Exception:  # pragma: no cover
    CellType = None


# Tian et al. (2026), Table 2: evacuation speeds in different areas.
SPEED_TABLE = {
    "normal": 1.95,
    "waste": 1.38,
    "rebar": 1.42,
    "steel": 1.70,
}

ZONE_NORMAL = 0
ZONE_WASTE = 1
ZONE_REBAR = 2
ZONE_STEEL = 3
ZONE_FIRE = 4
ZONE_OBSTACLE = 5

ZONE_LABELS = {
    ZONE_NORMAL: "normal",
    ZONE_WASTE: "waste/material stack",
    ZONE_REBAR: "rebar tying",
    ZONE_STEEL: "steel-pipe support",
    ZONE_FIRE: "fire",
    ZONE_OBSTACLE: "obstacle/machinery",
}

ZONE_COLORS = {
    ZONE_NORMAL: (1.00, 1.00, 1.00),
    ZONE_WASTE: (0.95, 0.78, 0.45),
    ZONE_REBAR: (0.72, 0.80, 0.95),
    ZONE_STEEL: (0.78, 0.90, 0.72),
    ZONE_FIRE: (0.95, 0.35, 0.25),
    ZONE_OBSTACLE: (0.35, 0.35, 0.35),
}


@dataclass
class VisionAStarConfig:
    normal_speed: float = SPEED_TABLE["normal"]
    waste_speed: float = SPEED_TABLE["waste"]
    rebar_speed: float = SPEED_TABLE["rebar"]
    steel_speed: float = SPEED_TABLE["steel"]
    fire_speed: float = 0.25
    replan_interval_s: float = 30.0
    heuristic_samples: int = 48
    max_expansions: int = 200000
    blocked_penalty: float = 10000.0
    diagonal: bool = True
    smoke_panic_peak: float = 0.5
    smoke_panic_sigma: float = 0.25
    smoke_toxic_threshold: float = 0.65
    smoke_min_factor: float = 0.20
    smoke_block_threshold: float = 0.90
    # Source-proximity avoidance is a routing constraint based on the real
    # smoke-source coordinates returned by A/B. It is not a fabricated smoke
    # concentration field; the real smoke field still controls speed/blocking.
    source_avoid_radius_m: float = 4.0
    source_avoid_penalty: float = 12.0

    def validate(self) -> None:
        for name in ("normal_speed", "waste_speed", "rebar_speed", "steel_speed", "fire_speed"):
            setattr(self, name, max(0.05, float(getattr(self, name))))
        self.replan_interval_s = max(0.0, float(self.replan_interval_s))
        self.heuristic_samples = max(4, int(self.heuristic_samples))
        self.max_expansions = max(1000, int(self.max_expansions))
        self.blocked_penalty = max(1.0, float(self.blocked_penalty))
        self.smoke_panic_peak = float(np.clip(self.smoke_panic_peak, 0.0, 1.0))
        self.smoke_panic_sigma = max(0.01, float(self.smoke_panic_sigma))
        self.smoke_toxic_threshold = float(np.clip(self.smoke_toxic_threshold, 0.0, 1.0))
        self.smoke_min_factor = float(np.clip(self.smoke_min_factor, 0.05, 1.0))
        self.smoke_block_threshold = float(np.clip(self.smoke_block_threshold, 0.0, 1.0))
        self.source_avoid_radius_m = float(max(0.0, self.source_avoid_radius_m))
        self.source_avoid_penalty = float(max(0.0, self.source_avoid_penalty))


@dataclass
class Detection:
    cls: str
    bbox: Tuple[float, float, float, float]
    camera: str = ""
    confidence: float = 1.0


@dataclass
class MonitoringMap:
    width: int
    height: int
    cell_size: float
    speed: np.ndarray
    blocked: np.ndarray
    zone_code: np.ndarray
    detections: List[Detection] = field(default_factory=list)
    cameras: Dict[str, np.ndarray] = field(default_factory=dict)
    source: str = ""

    def speed_at(self, x: int, y: int) -> float:
        if 0 <= x < self.width and 0 <= y < self.height:
            return float(self.speed[y, x])
        return 0.0

    def is_blocked(self, x: int, y: int) -> bool:
        if 0 <= x < self.width and 0 <= y < self.height:
            return bool(self.blocked[y, x])
        return True


@dataclass
class RouteResult:
    person_id: int
    start: Tuple[int, int]
    exit_id: str
    path: List[Tuple[int, int]]
    time_s: float
    distance_m: float
    exit_probability: float
    probabilities: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "person_id": int(self.person_id),
            "start": list(self.start),
            "exit_id": self.exit_id,
            "path": [list(cell) for cell in self.path],
            "time_s": float(self.time_s),
            "distance_m": float(self.distance_m),
            "exit_probability": float(self.exit_probability),
            "probabilities": {k: float(v) for k, v in self.probabilities.items()},
        }


def solve_homography(src_pts: Sequence[Sequence[float]],
                     dst_pts: Sequence[Sequence[float]]) -> np.ndarray:
    """Solve the 8-DOF perspective transform used by the paper (Eq. 1-4)."""
    if len(src_pts) != 4 or len(dst_pts) != 4:
        raise ValueError("perspective calibration needs exactly four source and four target points")
    rows = []
    rhs = []
    for (x, y), (X, Y) in zip(src_pts, dst_pts):
        rows.append([x, y, 1, 0, 0, 0, -x * X, -y * X])
        rows.append([0, 0, 0, x, y, 1, -x * Y, -y * Y])
        rhs.extend([X, Y])
    h, *_ = np.linalg.lstsq(np.asarray(rows, dtype=float), np.asarray(rhs, dtype=float), rcond=None)
    return np.array([[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]], dtype=float)


def parse_homography(value: Any) -> Optional[np.ndarray]:
    """Accept a 3x3/9-value matrix, an 8-value a11..a32 vector, or src/dst points."""
    if value is None:
        return None
    if isinstance(value, dict):
        src = value.get("src") or value.get("source")
        dst = value.get("dst") or value.get("target")
        if src is not None and dst is not None:
            return solve_homography(src, dst)
        for key in ("matrix", "homography", "perspective", "H"):
            if key in value:
                return parse_homography(value[key])
        return None
    arr = np.asarray(value, dtype=float).reshape(-1)
    if arr.size == 9:
        return arr.reshape(3, 3)
    if arr.size == 8:
        return np.array([[arr[0], arr[1], arr[2]], [arr[3], arr[4], arr[5]], [arr[6], arr[7], 1.0]])
    return None


def transform_point(H: np.ndarray, x: float, y: float) -> Tuple[float, float]:
    p = H @ np.array([x, y, 1.0], dtype=float)
    if abs(p[2]) < 1e-12:
        return float("nan"), float("nan")
    return float(p[0] / p[2]), float(p[1] / p[2])


def transform_bbox(H: np.ndarray,
                   bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    x1, y1, x2, y2 = [float(v) for v in bbox]
    pts = [
        transform_point(H, x1, y1), transform_point(H, x2, y1),
        transform_point(H, x1, y2), transform_point(H, x2, y2),
    ]
    xs = [p[0] for p in pts if math.isfinite(p[0])]
    ys = [p[1] for p in pts if math.isfinite(p[1])]
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def load_monitoring_data(map_path: str | Path,
                         map_data: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], str]:
    """Use only the selected A map; no external monitoring file is required."""
    if map_data is None:
        map_data = json.loads(Path(map_path).read_text(encoding="utf-8"))
    if isinstance(map_data.get("monitoring"), dict):
        return map_data["monitoring"], f"{map_path}:monitoring"
    return {}, ""


def _normalize_class(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def class_to_zone(value: Any, config: VisionAStarConfig) -> Tuple[int, float, bool]:
    """Map a YOLO class to (zone code, speed, blocked) using the paper's classes."""
    s = _normalize_class(value)
    if any(k in s for k in ("fire", "flame", "smoke", "burn")):
        return ZONE_FIRE, config.fire_speed, True
    if any(k in s for k in ("steel", "pipe", "support")):
        return ZONE_STEEL, config.steel_speed, False
    if any(k in s for k in ("rebar", "reinforce", "reinforcing")):
        return ZONE_REBAR, config.rebar_speed, False
    if any(k in s for k in ("waste", "heap", "garbage", "debris", "material", "masonry", "stack")):
        return ZONE_WASTE, config.waste_speed, False
    if any(k in s for k in (
        "chair", "desk", "bookcase", "cabinet", "table", "obstacle",
        "dolly", "cart", "machine", "mechanical", "truck", "equipment",
    )):
        return ZONE_OBSTACLE, 0.05, True
    return ZONE_NORMAL, config.normal_speed, False


def _cell_zone_value(cell: Dict[str, Any]) -> Any:
    for key in ("zone_type", "construction_type", "work_zone", "semantic", "type"):
        value = cell.get(key)
        if value not in (None, "", "free", "exit", "sign", "guide_zone", "wall", "obstacle"):
            return value
    return None


@dataclass
class MonitoringMapBuilder:
    config: VisionAStarConfig = field(default_factory=VisionAStarConfig)

    def build(self, grid: Any, map_data: Optional[Dict[str, Any]],
              monitoring_data: Optional[Dict[str, Any]],
              source: str = "") -> MonitoringMap:
        self.config.validate()
        width = int(getattr(grid, "width", 0) or (map_data or {}).get("width", 0))
        height = int(getattr(grid, "height", 0) or (map_data or {}).get("height", 0))
        if width <= 0 or height <= 0:
            raise ValueError("monitoring map needs positive grid dimensions")
        cell_size = float(getattr(grid, "cell_size", 0.5) or 0.5)
        speed = np.full((height, width), float(self.config.normal_speed), dtype=float)
        blocked = np.zeros((height, width), dtype=bool)
        zone = np.zeros((height, width), dtype=np.int8)

        raw_cells = (map_data or {}).get("cells", [])
        if not raw_cells and grid is not None:
            raw_cells = [
                {
                    "x": getattr(c, "x", 0), "y": getattr(c, "y", 0),
                    "type": getattr(getattr(c, "cell_type", None), "value", getattr(c, "cell_type", "")),
                    "semantic": getattr(c, "semantic", None),
                }
                for c in getattr(grid, "cells", [])
            ]
        for cell in raw_cells:
            x, y = int(cell.get("x", -1)), int(cell.get("y", -1))
            if not (0 <= x < width and 0 <= y < height):
                continue
            ctype = _normalize_class(cell.get("type", ""))
            if ctype in ("wall", "obstacle"):
                blocked[y, x] = True
                zone[y, x] = ZONE_OBSTACLE
                speed[y, x] = 0.05
                continue
            zval = _cell_zone_value(cell)
            if zval is not None:
                code, spd, is_blocked = class_to_zone(zval, self.config)
                if code != ZONE_NORMAL:
                    zone[y, x] = max(int(zone[y, x]), code)
                    speed[y, x] = min(speed[y, x], spd)
                    blocked[y, x] = blocked[y, x] or is_blocked

        cameras: Dict[str, np.ndarray] = {}
        for i, cam in enumerate((monitoring_data or {}).get("cameras", []) or []):
            if not isinstance(cam, dict):
                continue
            name = str(cam.get("name") or cam.get("id") or f"cam{i}")
            H = parse_homography(cam.get("perspective") or cam.get("homography") or cam.get("matrix") or cam)
            if H is not None:
                cameras[name] = H

        detections: List[Detection] = []
        raw_detections = (monitoring_data or {}).get("detections")
        if raw_detections is None:
            raw_detections = (monitoring_data or {}).get("objects", [])
        units = str((monitoring_data or {}).get("units", "grid")).lower()
        for raw in raw_detections or []:
            if not isinstance(raw, dict):
                continue
            bbox = raw.get("grid_bbox") or raw.get("cell_bbox") or raw.get("bbox")
            if bbox is None or len(bbox) != 4:
                continue
            camera = str(raw.get("camera") or raw.get("camera_id") or "")
            if raw.get("grid_bbox") or raw.get("cell_bbox"):
                mapped = tuple(float(v) for v in bbox)
            else:
                H = cameras.get(camera)
                if H is None and len(cameras) == 1:
                    H = next(iter(cameras.values()))
                if H is None:
                    continue
                mapped = transform_bbox(H, bbox)
                if units == "m":
                    mapped = tuple(v / max(cell_size, 1e-9) for v in mapped)
            cls_name = str(raw.get("class") or raw.get("label") or raw.get("name") or "")
            code, spd, is_blocked = class_to_zone(cls_name, self.config)
            x0, y0, x1, y1 = mapped
            ix0, ix1 = math.floor(min(x0, x1)), math.ceil(max(x0, x1))
            iy0, iy1 = math.floor(min(y0, y1)), math.ceil(max(y0, y1))
            ix0, iy0 = max(0, ix0), max(0, iy0)
            ix1, iy1 = min(width - 1, ix1), min(height - 1, iy1)
            if ix1 < ix0 or iy1 < iy0:
                continue
            detections.append(Detection(cls_name, (x0, y0, x1, y1), camera, float(raw.get("confidence", 1.0) or 1.0)))
            if code == ZONE_NORMAL:
                continue
            for yy in range(iy0, iy1 + 1):
                for xx in range(ix0, ix1 + 1):
                    zone[yy, xx] = max(int(zone[yy, xx]), code)
                    speed[yy, xx] = min(speed[yy, xx], spd)
                    blocked[yy, xx] = blocked[yy, xx] or is_blocked

        return MonitoringMap(
            width=width, height=height, cell_size=cell_size,
            speed=speed, blocked=blocked, zone_code=zone,
            detections=detections, cameras=cameras, source=source,
        )


class VisionMappedAStarPlanner:
    """Time-based improved A* over a monitoring-transformed grid."""

    DIRS_4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    DIRS_8 = DIRS_4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))

    def __init__(self, grid: Any, map_path: str | Path,
                 exit_positions: Dict[str, Tuple[int, int]],
                 config: Optional[VisionAStarConfig] = None):
        self.grid = grid
        self.map_path = Path(map_path)
        self.map_data = json.loads(self.map_path.read_text(encoding="utf-8"))
        self.map_name = self.map_path.stem
        self.exit_positions = {str(k): (int(v[0]), int(v[1])) for k, v in (exit_positions or {}).items()}
        self.config = config or VisionAStarConfig()
        self.config.validate()
        self.monitoring_data, self.monitoring_source = load_monitoring_data(
            self.map_path, self.map_data,
        )
        self.builder = MonitoringMapBuilder(self.config)
        self.monitoring = self.builder.build(
            self.grid, self.map_data, self.monitoring_data, self.monitoring_source,
        )
        self.base_speed = self.monitoring.speed.copy()
        self.base_blocked = self.monitoring.blocked.copy()
        self.smoke_grid = None
        self.smoke_sources: List[Any] = []
        self.source_multiplier = np.ones((self.monitoring.height, self.monitoring.width), dtype=float)
        self._last_monitor_mtime = None
        self.results: Dict[int, RouteResult] = {}
        self.last_plan_step = None
        self.replan_count = 0
        self._walkable = self._build_walkable()

    def _build_walkable(self) -> np.ndarray:
        w, h = self.monitoring.width, self.monitoring.height
        walkable = np.ones((h, w), dtype=bool)
        for cell in getattr(self.grid, "cells", []) or []:
            x, y = int(getattr(cell, "x", -1)), int(getattr(cell, "y", -1))
            if not (0 <= x < w and 0 <= y < h):
                continue
            ctype = getattr(cell, "cell_type", None)
            if CellType is not None and ctype in (CellType.WALL, CellType.OBSTACLE):
                walkable[y, x] = False
            elif str(getattr(ctype, "value", ctype)).lower() in ("wall", "obstacle"):
                walkable[y, x] = False
        return walkable

    def set_smoke_sources(self, smoke_sources: Optional[Sequence[Any]] = None) -> None:
        """Apply a proximity cost around real smoke-source coordinates.

        This ensures route generation considers where the fire source is even
        before B has produced a non-zero smoke matrix. The penalty is a
        deterministic routing constraint, not a synthetic smoke concentration.
        """
        self.smoke_sources = list(smoke_sources or [])
        self.source_multiplier = np.ones(
            (self.monitoring.height, self.monitoring.width), dtype=float
        )
        radius = float(self.config.source_avoid_radius_m)
        if radius <= 0.0 or self.config.source_avoid_penalty <= 0.0:
            return
        radius_cells = int(math.ceil(radius / max(self.monitoring.cell_size, 1e-6)))
        height, width = self.monitoring.height, self.monitoring.width
        for source in self.smoke_sources:
            sx = int(getattr(source, "x", -1))
            sy = int(getattr(source, "y", -1))
            if not (0 <= sx < width and 0 <= sy < height):
                continue
            intensity = float(getattr(source, "intensity", 1.0) or 1.0)
            intensity = float(np.clip(abs(intensity), 0.25, 1.0))
            for y in range(max(0, sy - radius_cells), min(height, sy + radius_cells + 1)):
                for x in range(max(0, sx - radius_cells), min(width, sx + radius_cells + 1)):
                    distance_m = math.hypot(x - sx, y - sy) * self.monitoring.cell_size
                    if distance_m > radius:
                        continue
                    closeness = 1.0 - distance_m / radius
                    factor = 1.0 + self.config.source_avoid_penalty * intensity * closeness
                    self.source_multiplier[y, x] = max(self.source_multiplier[y, x], factor)

    def refresh_monitoring(self, force: bool = False) -> bool:
        # The selected A map is the only monitoring source in this platform.
        return False

    def refresh_smoke(self, smoke_grid=None) -> bool:
        """Apply the existing smoke-concentration speed rule to every cell.

        This reuses the platform speed rule already used for individual
        movement: a panic response around smoke 0.5, a toxic slowdown after
        0.65, and a lower bound of 0.20 of the base speed.  The returned value
        tells the caller whether the speed field changed materially.
        """
        shape = (self.monitoring.height, self.monitoring.width)
        if smoke_grid is None:
            new_smoke = np.zeros(shape, dtype=float)
        else:
            new_smoke = np.asarray(smoke_grid, dtype=float)
            if new_smoke.shape != shape:
                return False
        new_smoke = np.nan_to_num(np.clip(new_smoke, 0.0, 1.0), nan=0.0)
        changed = self.smoke_grid is None or float(np.mean(np.abs(new_smoke - self.smoke_grid))) >= 0.01
        self.smoke_grid = new_smoke
        panic = np.exp(-((new_smoke - self.config.smoke_panic_peak) ** 2) /
                       (2.0 * self.config.smoke_panic_sigma ** 2))
        toxic = np.where(
            new_smoke <= self.config.smoke_toxic_threshold, 1.0,
            np.maximum(0.35, 1.0 - (new_smoke - self.config.smoke_toxic_threshold) * 1.7),
        )
        factor = np.maximum(self.config.smoke_min_factor, (1.0 + 0.45 * panic) * toxic)
        self.monitoring.speed = self.base_speed * factor
        self.monitoring.blocked = self.base_blocked | (new_smoke > self.config.smoke_block_threshold)
        return changed

    def _passable(self, x: int, y: int) -> bool:
        if not (0 <= x < self.monitoring.width and 0 <= y < self.monitoring.height):
            return False
        return bool(self._walkable[y, x]) and not bool(self.monitoring.blocked[y, x])

    def _raw_move_cost(self, x0: int, y0: int, x1: int, y1: int) -> float:
        if not self._passable(x1, y1):
            return float("inf")
        dist = self.monitoring.cell_size * (math.sqrt(2.0) if x1 != x0 and y1 != y0 else 1.0)
        speed = max(0.05, float(self.monitoring.speed[y1, x1]))
        return dist / speed

    def _move_cost(self, x0: int, y0: int, x1: int, y1: int) -> float:
        """Routing cost including real smoke speed and source proximity."""
        raw = self._raw_move_cost(x0, y0, x1, y1)
        if not math.isfinite(raw):
            return float("inf")
        multiplier = float(self.source_multiplier[y1, x1])
        return raw * max(1.0, multiplier)

    def _heuristic(self, x: int, y: int, gx: int, gy: int) -> float:
        dist = math.hypot(gx - x, gy - y)
        if dist <= 1e-9:
            return 0.0
        n = max(2, min(int(self.config.heuristic_samples), max(2, int(dist) * 4)))
        total = 0.0
        prev_x, prev_y = float(x), float(y)
        for i in range(1, n + 1):
            t = i / float(n)
            nx = x + (gx - x) * t
            ny = y + (gy - y) * t
            mx = int(round((prev_x + nx) * 0.5))
            my = int(round((prev_y + ny) * 0.5))
            speed = 0.05
            if 0 <= mx < self.monitoring.width and 0 <= my < self.monitoring.height:
                speed = max(0.05, float(self.monitoring.speed[my, mx]))
            total += math.hypot(nx - prev_x, ny - prev_y) * self.monitoring.cell_size / speed
            prev_x, prev_y = nx, ny
        return total

    def _neighbors(self, x: int, y: int) -> Iterable[Tuple[int, int]]:
        dirs = self.DIRS_8 if self.config.diagonal else self.DIRS_4
        for dx, dy in dirs:
            nx, ny = x + dx, y + dy
            if not self._passable(nx, ny):
                continue
            if dx and dy and (not self._passable(x + dx, y) or not self._passable(x, y + dy)):
                continue
            yield nx, ny

    def astar(self, start: Tuple[int, int], goal: Tuple[int, int]) -> Optional[List[Tuple[int, int]]]:
        sx, sy = int(start[0]), int(start[1])
        gx, gy = int(goal[0]), int(goal[1])
        if not (0 <= sx < self.monitoring.width and 0 <= sy < self.monitoring.height):
            return None
        if (sx, sy) == (gx, gy):
            return [(sx, sy)]
        if not self._passable(gx, gy):
            return None
        open_heap = [(self._heuristic(sx, sy, gx, gy), 0.0, sx, sy)]
        g_score = {(sx, sy): 0.0}
        parent = {}
        closed = set()
        expansions = 0
        while open_heap:
            _, gcur, x, y = heapq.heappop(open_heap)
            if (x, y) in closed:
                continue
            closed.add((x, y))
            expansions += 1
            if expansions > self.config.max_expansions:
                return None
            if (x, y) == (gx, gy):
                path = [(x, y)]
                while path[-1] in parent:
                    path.append(parent[path[-1]])
                path.reverse()
                return path
            for nx, ny in self._neighbors(x, y):
                step = self._move_cost(x, y, nx, ny)
                if not math.isfinite(step):
                    continue
                ng = gcur + step
                if ng + 1e-12 < g_score.get((nx, ny), float("inf")):
                    g_score[(nx, ny)] = ng
                    parent[(nx, ny)] = (x, y)
                    heapq.heappush(open_heap, (ng + self._heuristic(nx, ny, gx, gy), ng, nx, ny))
        return None

    def _path_metrics(self, path):
        time_s = 0.0
        dist_m = 0.0
        for (x0, y0), (x1, y1) in zip(path[:-1], path[1:]):
            dist_m += math.hypot(x1 - x0, y1 - y0) * self.monitoring.cell_size
            time_s += self._raw_move_cost(x0, y0, x1, y1)
        return time_s, dist_m

    def exit_probabilities(self, start):
        sx, sy = start
        raw = {}
        for eid, (ex, ey) in self.exit_positions.items():
            raw[eid] = math.hypot((sx - ex) * self.monitoring.cell_size,
                                  (sy - ey) * self.monitoring.cell_size)
        if not raw:
            return {}
        dmin = min(raw.values())
        weights = {eid: math.exp(-(d - dmin)) for eid, d in raw.items()}
        total = sum(weights.values()) or 1.0
        return {eid: w / total for eid, w in weights.items()}

    def choose_route(self, person_id: int, start: Tuple[int, int]) -> Optional[RouteResult]:
        probs = self.exit_probabilities(start)
        order = sorted(probs, key=lambda eid: probs[eid], reverse=True)
        for eid in order:
            path = self.astar(start, self.exit_positions[eid])
            if path:
                time_s, dist_m = self._path_metrics(path)
                return RouteResult(int(person_id), (int(start[0]), int(start[1])), eid,
                                   path, time_s, dist_m, float(probs[eid]), probs)
        for eid, goal in self.exit_positions.items():
            path = self.astar(start, goal)
            if path:
                time_s, dist_m = self._path_metrics(path)
                return RouteResult(int(person_id), (int(start[0]), int(start[1])), eid,
                                   path, time_s, dist_m, float(probs.get(eid, 0.0)), probs)
        return None

    def plan_all(self, persons, step: int, dt: float = 0.5, force: bool = False, smoke_grid=None):
        self.refresh_monitoring()
        smoke_changed = self.refresh_smoke(smoke_grid)
        interval_steps = max(1, int(round(self.config.replan_interval_s / max(dt, 1e-6))))
        if not force and not smoke_changed and self.last_plan_step is not None and step - self.last_plan_step < interval_steps:
            return dict(self.results)
        new_results = {}
        for person in persons:
            if getattr(person, "evacuated", False) or getattr(person, "is_dead", False):
                continue
            result = self.choose_route(int(person.id), (int(person.x), int(person.y)))
            if result is not None:
                new_results[int(person.id)] = result
        self.results = new_results
        self.last_plan_step = int(step)
        self.replan_count += 1
        return dict(self.results)

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
        # Rejoin the route through an adjacent path cell when the person has
        # been pushed off the planned line by congestion or conflicts.
        target_i = min(best_i + 1, len(path) - 1)
        adjacent = []
        for i, cell in enumerate(path):
            if max(abs(cell[0] - cur[0]), abs(cell[1] - cur[1])) == 1:
                adjacent.append((abs(i - target_i), (cell[0] - cur[0]) ** 2 + (cell[1] - cur[1]) ** 2, i, cell))
        if adjacent:
            adjacent.sort()
            return adjacent[0][3]
        return path[best_i]

    def statistics(self):
        times = [r.time_s for r in self.results.values()]
        return {
            "map_name": self.map_name,
            "monitoring_source": self.monitoring_source,
            "routes": len(self.results),
            "replans": self.replan_count,
            "mean_time_s": float(np.mean(times)) if times else 0.0,
            "max_time_s": float(np.max(times)) if times else 0.0,
            "blocked_cells": int(np.sum(self.monitoring.blocked)),
            "waste_cells": int(np.sum(self.monitoring.zone_code == ZONE_WASTE)),
            "rebar_cells": int(np.sum(self.monitoring.zone_code == ZONE_REBAR)),
            "steel_cells": int(np.sum(self.monitoring.zone_code == ZONE_STEEL)),
            "fire_cells": int(np.sum(self.monitoring.zone_code == ZONE_FIRE)),
            "detections": len(self.monitoring.detections),
            "smoke_mean": float(np.mean(self.smoke_grid)) if self.smoke_grid is not None else 0.0,
            "mean_region_speed": float(np.mean(self.monitoring.speed)),
            "speeds": {"normal": self.config.normal_speed, "waste": self.config.waste_speed,
                       "rebar": self.config.rebar_speed, "steel": self.config.steel_speed},
        }

    def export_json(self, path):
        payload = {
            "map": str(self.map_path), "map_name": self.map_name,
            "monitoring_source": self.monitoring_source,
            "strategy": "vision_mapped_astar",
            "paper": "Tian et al. (2026), DOI 10.13637/j.issn.1009-6094.2025.1925",
            "speeds": {"normal": self.config.normal_speed, "waste": self.config.waste_speed,
                       "rebar": self.config.rebar_speed, "steel": self.config.steel_speed},
            "routes": [r.to_dict() for r in self.results.values()],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def render(self, path, title=None, smoke_sources=None):
        return render_route_map(self, path, title=title, smoke_sources=smoke_sources)


def render_route_map(planner, output_path, title=None, smoke_sources=None):
    """Render the monitoring-transformed map and every computed A* route."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    mon = planner.monitoring
    w, h = mon.width, mon.height
    img = np.ones((h, w, 3), dtype=float)
    for code, color in ZONE_COLORS.items():
        if code == ZONE_NORMAL:
            continue
        img[mon.zone_code == code] = color
    for cell in getattr(planner.grid, "cells", []) or []:
        x, y = int(getattr(cell, "x", -1)), int(getattr(cell, "y", -1))
        if not (0 <= x < w and 0 <= y < h):
            continue
        ctype = getattr(cell, "cell_type", None)
        cval = str(getattr(ctype, "value", ctype)).lower()
        if cval in ("wall", "obstacle"):
            img[y, x] = (0.18, 0.18, 0.18)

    fig, ax = plt.subplots(figsize=(max(7.0, w / 14.0), max(5.0, h / 14.0)), dpi=120)
    ax.imshow(img, origin="upper", interpolation="nearest", extent=(0, w, h, 0))
    if planner.smoke_grid is not None and float(np.max(planner.smoke_grid)) > 0.0:
        smoke = np.clip(planner.smoke_grid, 0.0, 1.0)
        overlay = np.zeros((h, w, 4), dtype=float)
        overlay[..., 0] = 0.98
        overlay[..., 1] = 0.25
        overlay[..., 2] = 0.05
        overlay[..., 3] = 0.04 + 0.42 * smoke
        ax.imshow(overlay, origin="upper", interpolation="nearest", extent=(0, w, h, 0))
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_aspect("equal")
    ax.set_xlabel("grid x")
    ax.set_ylabel("grid y")
    ax.grid(color="0.85", linewidth=0.35, alpha=0.6)

    if planner.results:
        starts = [(r.start[0] + 0.5, r.start[1] + 0.5) for r in planner.results.values()]
        ax.scatter([p[0] for p in starts], [p[1] for p in starts],
                   c="#1f77b4", s=18, alpha=0.8, zorder=4, label="evacuee start")
    for eid, (ex, ey) in planner.exit_positions.items():
        ax.scatter([ex + 0.5], [ey + 0.5], marker="s", s=90,
                   c="#2ca02c", edgecolors="black", linewidths=0.6, zorder=6)
        ax.annotate(str(eid), (ex + 0.8, ey + 0.8), fontsize=7, color="black", zorder=7)
    for src in smoke_sources or []:
        sx, sy = int(getattr(src, "x", -1)), int(getattr(src, "y", -1))
        if 0 <= sx < w and 0 <= sy < h:
            ax.scatter([sx + 0.5], [sy + 0.5], marker="*", s=130,
                       c="#d62728", edgecolors="black", linewidths=0.4, zorder=6)

    palette = ["#d62728", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22"]
    route_handles = []
    for idx, result in enumerate(sorted(planner.results.values(), key=lambda r: r.person_id)):
        color = palette[idx % len(palette)]
        xs = [p[0] + 0.5 for p in result.path]
        ys = [p[1] + 0.5 for p in result.path]
        ax.plot(xs, ys, color=color, linewidth=1.35, alpha=0.9, zorder=5)
        if len(xs) >= 2:
            ax.annotate("", xy=(xs[-1], ys[-1]), xytext=(xs[-2], ys[-2]),
                        arrowprops=dict(arrowstyle="->", color=color, lw=1.2), zorder=5)
        route_handles.append(Line2D([0], [0], color=color, lw=1.5,
                                    label=f"#{result.person_id} -> {result.exit_id} ({result.time_s:.1f}s)"))

    zone_handles = [
        Patch(facecolor=ZONE_COLORS[ZONE_WASTE], edgecolor="0.5", label="waste/material 1.38 m/s"),
        Patch(facecolor=ZONE_COLORS[ZONE_REBAR], edgecolor="0.5", label="rebar 1.42 m/s"),
        Patch(facecolor=ZONE_COLORS[ZONE_STEEL], edgecolor="0.5", label="steel support 1.70 m/s"),
        Patch(facecolor=ZONE_COLORS[ZONE_FIRE], edgecolor="0.5", label="fire (avoid)"),
        Patch(facecolor=ZONE_COLORS[ZONE_OBSTACLE], edgecolor="0.5", label="obstacle/machinery"),
        Patch(facecolor=(0.98, 0.25, 0.05), alpha=0.35, edgecolor="0.5", label="smoke speed loss"),
    ]
    handles = zone_handles + route_handles[:12]
    if len(route_handles) > 12:
        handles.append(Line2D([0], [0], color="0.4", lw=1,
                              label=f"... {len(route_handles) - 12} more routes"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              fontsize=6.5, frameon=True, borderaxespad=0.0)
    if title is None:
        title = f"{planner.map_name} | vision-mapped A* routes"
    ax.set_title(title, fontsize=11)
    footer = (f"monitoring: {planner.monitoring_source or 'map cell metadata'} | "
              f"routes={len(planner.results)} | replans={planner.replan_count} | "
              f"normal={planner.config.normal_speed:.2f} m/s")
    fig.text(0.01, 0.01, footer, fontsize=7, color="0.35")
    fig.tight_layout()
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=180, bbox_inches="tight")
    plt.close(fig)
    return str(out)
