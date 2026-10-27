"""恐慌阈值与恐慌传染模型（矩阵化版本）

- 白鹏 2026：情绪阈值 α；恐慌者 1.2 倍速；
- 关系加权：r_i = Σ w_ij·[P_j≥α] / Σ w_ij；
- 矩阵化：把邻域传染、暴露、衰减写成 numpy 数组运算，减少逐步 Python 循环；
- 烟源附加：source_proximity 表示到烟源的高斯邻近度，直接加到暴露项，
  避免风险值在 risk_ref 处被截断后"烟源不够危险"。

对外接口与旧版一致：reset / update / set_relations。
"""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np

from .movement_config import PanicConfig

_OFFSETS = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))
_DENSE_LIMIT = 2000


class PanicModel:
    """逐步更新每个行人的恐慌值。"""

    def __init__(
        self,
        config: PanicConfig | None = None,
        time_step_s: float = 0.5,
        rng=None,
        relations: Iterable[Any] | None = None,
    ):
        self.cfg = config or PanicConfig()
        self.dt = max(1e-6, float(time_step_s))
        self.rng = rng
        self._last_smoke: dict[int, float] = {}
        self._relations_raw: list[Any] = list(relations or [])
        self._relation_index: dict[tuple[int, int], float] = {}
        self._type_weights: dict[str, float] = {}
        self._default_relation_weight = 0.0
        self._ids = np.zeros(0, dtype=np.int64)
        self._row_map = np.zeros(0, dtype=np.int64)
        self._weight_matrix: np.ndarray | None = None
        self._rebuild_relation_index()

    # ------------------------------------------------------------------ 初始化
    def reset(self, persons: list[Any]) -> None:
        self._last_smoke.clear()
        self._rebuild_relation_index()
        cfg = self.cfg
        for person in persons:
            if self.rng is not None:
                susceptibility = self.rng.gauss(cfg.susceptibility_mean, cfg.susceptibility_sd)
            else:
                susceptibility = cfg.susceptibility_mean
            susceptibility = min(0.99, max(0.01, float(susceptibility)))
            setattr(person, "panic_susceptibility", susceptibility)
            initial = susceptibility if cfg.initial_from_susceptibility else 0.0
            self._write_state(person, initial, initial >= cfg.threshold)
        self._sync_ids([int(person.id) for person in persons])
        return None

    def set_relations(self, relations: Iterable[Any] | None) -> None:
        """关系图变化时由 C 调用一次；只重建权重表，不改 P_i。"""
        self._relations_raw = list(relations or [])
        self._rebuild_relation_index()
        if self._ids.size:
            self._weight_matrix = self._build_weight_matrix()

    # ------------------------------------------------------------------ 主更新
    def update(
        self,
        persons: list[Any],
        smoke_matrix: np.ndarray,
        risk_map: Mapping[int, float] | None = None,
        positions: Mapping[tuple[int, int], int] | None = None,
        dt: float | None = None,
        source_proximity: Mapping[int, float] | None = None,
    ) -> dict[int, dict[str, float | bool]]:
        cfg = self.cfg
        dt = self.dt if dt is None else max(1e-6, float(dt))
        risk_map = risk_map or {}
        persons = list(persons)
        if not cfg.enabled:
            for person in persons:
                self._write_state(person, 0.0, False)
            return {}
        if not persons:
            return {}

        ids = np.fromiter((int(person.id) for person in persons), dtype=np.int64, count=len(persons))
        if ids.size != self._ids.size or not np.array_equal(ids, self._ids):
            self._sync_ids(ids.tolist())
        n = len(persons)
        xs = np.fromiter((int(person.x) for person in persons), dtype=np.int64, count=n)
        ys = np.fromiter((int(person.y) for person in persons), dtype=np.int64, count=n)
        panic = np.fromiter((self._panic_of(person) for person in persons), dtype=np.float64, count=n)
        height, width = smoke_matrix.shape
        xs_clipped = np.clip(xs, 0, width - 1)
        ys_clipped = np.clip(ys, 0, height - 1)
        smoke = np.asarray(smoke_matrix[ys_clipped, xs_clipped], dtype=np.float64)

        last = np.fromiter(
            (self._last_smoke.get(int(person.id), smoke[index]) for index, person in enumerate(persons)),
            dtype=np.float64,
            count=n,
        )
        delta_s = smoke - last
        risk = np.fromiter(
            (float(risk_map.get(int(person.id), 0.0) or 0.0) for person in persons),
            dtype=np.float64,
            count=n,
        )

        exposure = (
            np.minimum(1.0, np.maximum(0.0, smoke) / max(1e-6, cfg.smoke_ref))
            + cfg.delta_weight * np.minimum(1.0, np.maximum(0.0, delta_s) / max(1e-6, self.dt))
            + cfg.risk_weight * np.minimum(1.0, np.maximum(0.0, risk) / max(1e-6, cfg.risk_ref))
        )
        if source_proximity is not None:
            proximity = np.fromiter(
                (float(source_proximity.get(int(person.id), 0.0) or 0.0) for person in persons),
                dtype=np.float64,
                count=n,
            )
            exposure = exposure + max(0.0, float(cfg.source_weight)) * proximity
        target = 1.0 - np.exp(-exposure)

        ratio = (
            self._weighted_ratio(xs, ys, panic, width, height)
            if self._weight_matrix is not None
            else self._scalar_ratio(persons)
        )
        susceptibility = np.fromiter(
            (float(getattr(person, "panic_susceptibility", 1.0) or 1.0) for person in persons),
            dtype=np.float64,
            count=n,
        )
        was_panicked = np.fromiter(
            (bool(getattr(person, "is_panicked", False)) for person in persons),
            dtype=bool,
            count=n,
        )
        growth = susceptibility * cfg.growth * target * (1.0 - panic)
        contagion = susceptibility * cfg.contagion * ratio * (1.0 - panic)
        decay = cfg.decay * panic
        panic_new = np.clip(panic + dt * (growth + contagion - decay), 0.0, 1.0)
        thresholds = np.where(was_panicked, cfg.exit_threshold, cfg.threshold)
        is_panicked = panic_new >= thresholds

        result: dict[int, dict[str, float | bool]] = {}
        for index, person in enumerate(persons):
            pid = int(person.id)
            value = float(panic_new[index])
            self._write_state(person, value, bool(is_panicked[index]))
            self._last_smoke[pid] = float(smoke[index])
            result[pid] = {
                "panic": value,
                "is_panicked": bool(is_panicked[index]),
                "neighbor_panic_ratio": float(ratio[index]),
            }
        return result

    # ------------------------------------------------------------------ 矩阵化传染
    def _weighted_ratio(
        self,
        xs: np.ndarray,
        ys: np.ndarray,
        panic: np.ndarray,
        width: int,
        height: int,
    ) -> np.ndarray:
        n = len(xs)
        pid_grid = np.full((height, width), -1, dtype=np.int64)
        pid_grid[ys, xs] = self._ids
        panic_grid = np.full((height, width), -1.0, dtype=np.float64)
        panic_grid[ys, xs] = panic
        panicked_grid = panic_grid >= self.cfg.threshold
        numerator = np.zeros(n, dtype=np.float64)
        denominator = np.zeros(n, dtype=np.float64)
        rows = np.arange(n)
        sigma = max(0.1, float(self.cfg.relation_sigma_cells))
        for dx, dy in _OFFSETS:
            nx = xs + dx
            ny = ys + dy
            inside = (nx >= 0) & (nx < width) & (ny >= 0) & (ny < height)
            if not np.any(inside):
                continue
            nx_safe = np.clip(nx, 0, width - 1)
            ny_safe = np.clip(ny, 0, height - 1)
            neighbours = pid_grid[ny_safe, nx_safe]
            valid = inside & (neighbours >= 0) & (neighbours != self._ids)
            if not np.any(valid):
                continue
            safe_neighbours = np.where(valid, neighbours, self._ids[0])
            neighbour_rows = self._row_map[safe_neighbours]
            weight = self._weight_matrix[rows, neighbour_rows]
            if self.cfg.relation_distance_decay:
                weight = weight * math.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma))
            neighbour_panicked = panicked_grid[ny_safe, nx_safe]
            numerator += np.where(valid, weight * neighbour_panicked, 0.0)
            denominator += np.where(valid, weight, 0.0)
        return np.divide(numerator, denominator, out=np.zeros(n), where=denominator > 0)

    def _scalar_ratio(self, persons: list[Any]) -> np.ndarray:
        """关系加权矩阵过大时的回退路径（>2000 人）。"""
        radius = max(1, int(self.cfg.contagion_radius))
        panic_by_id = {int(p.id): self._panic_of(p) for p in persons}
        positions = {(int(p.x), int(p.y)): int(p.id) for p in persons}
        ratios: list[float] = []
        for person in persons:
            pid = int(person.id)
            px, py = int(person.x), int(person.y)
            total = 0.0
            panicked = 0.0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx == 0 and dy == 0:
                        continue
                    other = positions.get((px + dx, py + dy))
                    if other is None or other == pid:
                        continue
                    weight = (
                        self._pair_weight(pid, int(other), dx, dy)
                        if self.cfg.relation_contagion
                        else 1.0
                    )
                    total += weight
                    if panic_by_id.get(other, 0.0) >= self.cfg.threshold:
                        panicked += weight
            ratios.append((panicked / total) if total > 0 else 0.0)
        return np.asarray(ratios, dtype=np.float64)

    # ------------------------------------------------------------------ 关系权重
    def _sync_ids(self, ids: list[int]) -> None:
        self._ids = np.asarray(ids, dtype=np.int64)
        if self._ids.size:
            max_id = int(self._ids.max())
            self._row_map = np.full(max_id + 1, -1, dtype=np.int64)
            for row, pid in enumerate(self._ids.tolist()):
                self._row_map[int(pid)] = row
        else:
            self._row_map = np.zeros(0, dtype=np.int64)
        self._weight_matrix = self._build_weight_matrix()

    def _build_weight_matrix(self) -> np.ndarray | None:
        cfg = self.cfg
        if not cfg.relation_contagion or self._ids.size == 0 or self._ids.size > _DENSE_LIMIT:
            return None
        size = self._ids.size
        matrix = np.full((size, size), self._default_relation_weight, dtype=np.float32)
        np.fill_diagonal(matrix, 0.0)
        for (left, right), weight in self._relation_index.items():
            if left >= self._row_map.size or right >= self._row_map.size:
                continue
            left_row = int(self._row_map[left])
            right_row = int(self._row_map[right])
            if left_row < 0 or right_row < 0:
                continue
            if weight > matrix[left_row, right_row]:
                matrix[left_row, right_row] = weight
                matrix[right_row, left_row] = weight
        return matrix

    def _rebuild_relation_index(self) -> None:
        cfg = self.cfg
        self._type_weights = {
            str(key).strip().lower(): float(value)
            for key, value in dict(cfg.relation_type_weights).items()
        }
        self._default_relation_weight = self._relation_weight("stranger", 0.0, 0.1)
        index: dict[tuple[int, int], float] = {}
        for relation in self._relations_raw:
            left = getattr(relation, "person_a_id", None)
            right = getattr(relation, "person_b_id", None)
            if left is None or right is None or int(left) == int(right):
                continue
            raw_type = getattr(relation, "relation_type", "stranger")
            type_name = str(getattr(raw_type, "value", raw_type)).strip().lower()
            strength = min(1.0, max(0.0, float(getattr(relation, "strength", 0.0) or 0.0)))
            trust = min(1.0, max(0.0, float(getattr(relation, "trust", 0.1) or 0.0)))
            weight = self._relation_weight(type_name, strength, trust)
            a, b = int(left), int(right)
            if weight > index.get((a, b), 0.0):
                index[(a, b)] = weight
                index[(b, a)] = weight
        self._relation_index = index

    def _relation_weight(self, type_name: str, strength: float, trust: float) -> float:
        cfg = self.cfg
        weights = self._type_weights or {
            "family": 1.00, "friend": 0.80, "classmate": 0.65, "colleague": 0.60,
            "doctor_patient": 0.50, "staff_to_customer": 0.35, "stranger": 0.20,
        }
        psi = float(weights.get(type_name, weights.get("stranger", 0.20)))
        floor = min(1.0, max(0.0, float(cfg.relation_floor)))
        strength_weight = max(0.0, float(cfg.relation_strength_weight))
        trust_weight = max(0.0, float(cfg.relation_trust_weight))
        denominator = strength_weight + trust_weight
        if denominator <= 1e-9:
            modulation = 1.0
        else:
            modulation = (strength_weight * strength + trust_weight * trust) / denominator
        return max(0.0, psi * (floor + (1.0 - floor) * modulation))

    def _pair_weight(self, left: int, right: int, dx: int, dy: int) -> float:
        weight = self._relation_index.get((left, right), self._default_relation_weight)
        if self.cfg.relation_distance_decay:
            sigma = max(0.1, float(self.cfg.relation_sigma_cells))
            weight *= math.exp(-(dx * dx + dy * dy) / (2.0 * sigma * sigma))
        return weight

    # ------------------------------------------------------------------ 状态回写
    @staticmethod
    def _panic_of(person: Any) -> float:
        try:
            return float(getattr(person, "panic", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _write_state(self, person: Any, panic: float, is_panicked: bool) -> None:
        setattr(person, "panic", panic)
        setattr(person, "panic_level", panic)
        setattr(person, "is_panicked", bool(is_panicked))
        setattr(
            person,
            "speed_multiplier",
            1.0 + (self.cfg.speed_gain if is_panicked else 0.0),
        )
        return None
