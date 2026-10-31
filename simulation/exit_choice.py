"""多出口动态选择：按霍非舟等（2023）原文公式实现

原文（中国安全科学学报，2023，33(1)：191-197）：
- 到出口距离 d_k：行人到出口 k 各元胞距离的最小值；
- 出口区域密度 ρ_k = N_k / C_k：区域半径 R 内实际人数 / 最大可容纳人数；
- 距离选择概率 P1k = softmax(-d_k / Σd)；
- 拥挤选择概率 P2k = softmax(-N_k/C_k)；
- 综合概率 E_k = (1-α)P1k + αP2k，α 为密度权重（原文最佳 0.3~0.6，取 0.45）；
- 选择模糊度 σ：E_t(μ) - E_{t-1}(μ) ≥ σ 时才考虑换出口；
- 出口保持率 Pm：满足条件后仍有 Pm 概率保持原出口、1-Pm 概率换到 μ。

原文参数：元胞 0.4 m，场景 20×28 m，4 个出口（宽 0.8 m），R=3.2 m，
α=0.45，σ=0.2×10⁻²，Pm=0.9。

本项目扩展（默认不改变原文行为）：
- `smoke_weight`：出口区域平均烟雾浓度对 E_k 的指数惩罚，0 即完全按原文；
- `exit_pressure_gain` / `panic_noise`：危险感知削弱 Pm、恐慌判断噪声，默认 0。
"""

from __future__ import annotations

import math
from typing import Any, Mapping, Optional

import numpy as np

from .movement_config import ExitChoiceConfig
from .floor_field import FloorField


class ExitChooser:
    """兼容旧接口的出口选择器，同时提供原文动态多出口决策。"""

    def __init__(
        self,
        scene,
        rng,
        floor_field: FloorField | None = None,
        config: ExitChoiceConfig | None = None,
        time_step_s: float = 0.5,
        cell_size: float = 0.5,
    ):
        self.scene = scene
        self.rng = rng
        self.cfg = config or ExitChoiceConfig()
        self.floor_field = floor_field
        self.dt = max(1e-6, float(time_step_s))
        self.cell_size = max(1e-6, float(cell_size))

        exit_cells = []
        if scene is not None and getattr(scene, "grid", None) is not None:
            exit_cells = [
                (int(c.x), int(c.y))
                for c in scene.grid.cells
                if str(getattr(getattr(c, "cell_type", None), "value", getattr(c, "cell_type", ""))).lower()
                == "exit"
            ]
        self.exit_cells = exit_cells
        self.exits: list[tuple[str, int, int, float]] = []
        self._cells_by_exit: dict[str, list[tuple[int, int]]] = {}
        for index, exit_obj in enumerate(getattr(scene, "exits", []) or []):
            eid = str(getattr(exit_obj, "id", f"exit_{index + 1}"))
            ex, ey = int(getattr(exit_obj, "x", 0)), int(getattr(exit_obj, "y", 0))
            if (ex, ey) not in exit_cells:
                if exit_cells:
                    ex, ey = min(
                        exit_cells, key=lambda p: (p[0] - ex) ** 2 + (p[1] - ey) ** 2
                    )
                else:
                    continue
            width = float(getattr(exit_obj, "width", 1.0) or 1.0)
            self.exits.append((eid, ex, ey, width))
            # 欧氏距离模式用：同一出口的所有元胞
            cells = [p for p in exit_cells if p == (ex, ey)] or [(ex, ey)]
            self._cells_by_exit[eid] = cells
        if not self.exits:
            for index, (ex, ey) in enumerate(exit_cells):
                eid = f"exit_{index + 1:02d}"
                self.exits.append((eid, ex, ey, 1.0))
                self._cells_by_exit[eid] = [(ex, ey)]

        self._target: dict[int, str] = {}
        self._prev_probs: dict[int, dict[str, float]] = {}
        self._prev_best: dict[int, float] = {}
        self._last_switch: dict[int, int] = {}
        self.switch_total = 0

        # 出口区域半径（元胞）与容量（元胞数）
        self._radius_cells = max(1.0, float(self.cfg.region_radius_m) / self.cell_size)
        area = (
            math.pi * float(self.cfg.region_radius_m) ** 2 / 2.0
            if str(self.cfg.capacity_mode).lower() != "rect"
            else float(self.cfg.region_radius_m) * 2.0 * float(self.cfg.region_radius_m)
        )
        self._capacity = max(
            1.0,
            area / (self.cell_size ** 2) * max(1e-6, float(self.cfg.capacity_scale)),
        )

    # ------------------------------------------------------------------ 旧接口
    def select_exit(self, person, mode: str, guided_exit_id: Optional[str] = None):
        """兼容旧调用：返回 (x, y, exit_id)。"""
        if not self.exits:
            return None
        if mode == "guided" and guided_exit_id is not None:
            for eid, ex, ey, _w in self.exits:
                if eid == str(guided_exit_id):
                    return (ex, ey, eid)
            mode = "nearest"
        if mode == "random":
            eid, ex, ey, _w = self.rng.choice(self.exits)
            return (ex, ey, eid)
        if mode == "familiar":
            best = None
            best_score = -1e18
            fam = float(getattr(person, "familiarity", 0.5) or 0.5)
            for eid, ex, ey, _w in self.exits:
                dist = math.hypot(int(person.x) - ex, int(person.y) - ey)
                score = 0.6 * fam + 0.4 / (dist + 1.0)
                if score > best_score:
                    best_score = score
                    best = (ex, ey, eid)
            return best
        return self._nearest(person)

    def _nearest(self, person):
        px, py = int(person.x), int(person.y)
        best = None
        best_d = float("inf")
        for eid, ex, ey, _w in self.exits:
            d = self._distance(eid, px, py)
            if d < best_d:
                best_d = d
                best = (ex, ey, eid)
        return best

    # ------------------------------------------------------------------ 动态选择
    def target_of(self, person) -> Optional[str]:
        return self._target.get(int(person.id))

    def update_all(
        self,
        persons: list[Any],
        step: int,
        smoke_matrix: np.ndarray | None,
        forced_targets: Mapping[int, str] | None = None,
    ) -> dict[int, str]:
        """按原文公式为每个行人决定目标出口。"""
        if not self.cfg.enabled or not self.exits:
            return {}
        forced_targets = forced_targets or {}
        counts = self._region_counts(persons)
        smoke_at_exit = self._region_smoke(smoke_matrix)

        result: dict[int, str] = {}
        for person in persons:
            pid = int(person.id)
            current = self._target.get(pid)
            forced = forced_targets.get(pid)
            probs = self._probabilities(person, counts, smoke_at_exit)
            if not probs:
                continue
            if forced and str(forced) in probs:
                self._target[pid] = str(forced)
                self._prev_probs[pid] = probs
                self._prev_best[pid] = max(probs.values())
                result[pid] = str(forced)
                continue

            best_exit = max(probs, key=probs.get)
            best_prob = probs[best_exit]
            if current is None:
                # Start from the shortest reachable route.  Density and smoke
                # may justify a later switch, but the initial choice must not
                # send a person past a nearer exit merely because softmax
                # probabilities are nearly flat.
                nearest = self._nearest(person)
                initial_exit = nearest[2] if nearest is not None else best_exit
                self._target[pid] = initial_exit
                self._prev_probs[pid] = probs
                self._prev_best[pid] = best_prob
                result[pid] = initial_exit
                continue

            # 原文：当前时间步"出口最大概率"与上一时间步相比，差值 ≥ σ 才考虑换出口
            previous_best = self._prev_best.get(pid, best_prob)
            delta = best_prob - previous_best
            self._prev_best[pid] = best_prob
            cooldown = int(self.cfg.switch_cooldown)
            can_switch = cooldown <= 0 or (step - self._last_switch.get(pid, -10**9)) >= cooldown
            # 距当前目标出口足够近时锁定出口，避免"到门口又掉头跟别人跑"
            commit_radius = max(0.0, float(getattr(self.cfg, "commit_radius_cells", 0.0)))
            if can_switch and commit_radius > 0.0 and current is not None:
                current_distance = self._distance(str(current), int(person.x), int(person.y))
                if math.isfinite(current_distance) and current_distance <= commit_radius:
                    can_switch = False
            if can_switch and best_exit != current and delta >= float(self.cfg.sigma):
                danger = min(1.0, max(0.0, float(getattr(person, "danger_pressure", 0.0) or 0.0)))
                # 原文：以 Pm 概率保持原出口，以 1-Pm 概率换出口
                retention = float(self.cfg.retention) * max(
                    0.0, 1.0 - float(self.cfg.exit_pressure_gain) * danger
                )
                if self.rng.random() >= retention:
                    self._target[pid] = best_exit
                    self._last_switch[pid] = int(step)
                    self.switch_total += 1
            self._prev_probs[pid] = probs
            result[pid] = self._target[pid]
        return result

    # ------------------------------------------------------------------ 概率
    def _probabilities(self, person, counts, smoke_at_exit) -> dict[str, float]:
        cfg = self.cfg
        px, py = int(person.x), int(person.y)
        distances = {
            eid: self._distance(eid, px, py)
            for eid, _ex, _ey, _w in self.exits
        }
        valid = {eid: d for eid, d in distances.items() if math.isfinite(d) and d < 9998}
        if not valid:
            return {}

        # P1：距离概率 softmax(-d / Σd)
        total_d = sum(valid.values()) or 1.0
        weights = {eid: math.exp(-d / total_d) for eid, d in valid.items()}
        weight_sum = sum(weights.values()) or 1.0
        p1 = {eid: weights[eid] / weight_sum for eid in weights}

        # P2：出口区域密度概率 softmax(-N/C)
        density = {eid: counts.get(eid, 0.0) / self._capacity for eid in valid}
        density_weights = {eid: math.exp(-rho) for eid, rho in density.items()}
        density_sum = sum(density_weights.values()) or 1.0
        p2 = {eid: density_weights[eid] / density_sum for eid in density_weights}

        # E = (1-α)P1 + αP2
        alpha = min(1.0, max(0.0, float(cfg.alpha)))
        probs = {eid: (1.0 - alpha) * p1[eid] + alpha * p2[eid] for eid in valid}

        # 项目扩展：出口烟雾惩罚（smoke_weight=0 即完全按原文）
        smoke_weight = max(0.0, float(cfg.smoke_weight))
        if smoke_weight > 0 and smoke_at_exit:
            smoke_ref = max(1e-6, float(cfg.smoke_ref))
            probs = {
                eid: probs[eid] * math.exp(-smoke_weight * smoke_at_exit.get(eid, 0.0) / smoke_ref)
                for eid in probs
            }
            norm = sum(probs.values()) or 1.0
            probs = {eid: value / norm for eid, value in probs.items()}

        # 项目扩展：恐慌判断噪声（默认 0）
        panic = float(getattr(person, "panic", 0.0) or 0.0)
        if cfg.panic_noise > 0 and panic > 0:
            probs = {
                eid: probs[eid] * math.exp(self.rng.gauss(0.0, cfg.panic_noise * panic * 0.5))
                for eid in probs
            }
            norm = sum(probs.values()) or 1.0
            probs = {eid: value / norm for eid, value in probs.items()}
        return probs

    # ------------------------------------------------------------------ 距离/密度/烟雾
    def _distance(self, exit_id: str, x: int, y: int) -> float:
        if str(self.cfg.distance_mode).lower() == "euclidean":
            cells = self._cells_by_exit.get(exit_id) or []
            if not cells:
                return float("inf")
            return min(math.hypot(x - cx, y - cy) for cx, cy in cells)
        if self.floor_field is not None:
            return self.floor_field.distance_to(exit_id, x, y)
        ex, ey = next(
            ((ex, ey) for eid, ex, ey, _w in self.exits if eid == exit_id), (0, 0)
        )
        return math.hypot(x - ex, y - ey)

    def _region_counts(self, persons: list[Any]) -> dict[str, float]:
        radius = self._radius_cells
        counts = {eid: 0.0 for eid, _x, _y, _w in self.exits}
        for person in persons:
            px, py = int(person.x), int(person.y)
            for eid, ex, ey, _w in self.exits:
                if math.hypot(px - ex, py - ey) <= radius:
                    counts[eid] += 1.0
        return counts

    def _region_smoke(self, smoke_matrix) -> dict[str, float]:
        if smoke_matrix is None:
            return {eid: 0.0 for eid, _x, _y, _w in self.exits}
        height, width = smoke_matrix.shape
        radius = int(math.ceil(self._radius_cells))
        result: dict[str, float] = {}
        for eid, ex, ey, _w in self.exits:
            total = 0.0
            count = 0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if math.hypot(dx, dy) > self._radius_cells:
                        continue
                    x, y = ex + dx, ey + dy
                    if 0 <= x < width and 0 <= y < height:
                        total += float(smoke_matrix[y, x])
                        count += 1
            result[eid] = (total / count) if count else 0.0
        return result
