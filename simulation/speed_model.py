"""渐进加速与多速度模型（Xiong 2025 + 高瑾 2021）

调研结论：
- Xiong 等（2025）：行人不是瞬间达到最大速度，而是先加速、遇阻再减速；
  加速度/减速度随当前速度变化，且服从高斯分布；速度更大的人在抢格冲突中获胜。
- 高瑾等（2021）：用"亏欠时间/距离"处理元胞离散性与速度多样性的矛盾，
  慢的人累积不足一格的距离，后续步再补偿。

Xiong 2025 的实测参数（Table 1，按 1 m/s 速度区间拟合）：
    速度区间    μA     σA     μD     σD
    0.5        1.30   0.60   1.90   1.90
    1.0        1.43   0.73   1.86   0.56
    1.5        1.64   0.65   1.88   0.72
    2.0        1.76   0.73   1.79   0.83
    2.5        1.62   1.16   1.66   1.31
    3.0        1.39   1.07   1.39   1.11
    3.5        0.99   0.85   0.89   0.74
    4.0        0.86   0.62   0.78   0.60
    4.5        0.73   0.45   0.61   0.51
    5.0        0.40   0.53   0.43   0.49

实现方式：
每个行人维护当前速度 v (m/s) 与"已积累距离" credit（单位：元胞）。
每步 credit += v * dt / cell_size；credit >= 1 时允许前进一步（最多
max_cells_per_step 格）。加速/减速时按当前速度插值取 μ、σ，再从高斯分布采样
本步加速度，这样 0.37~1.85 m/s 的速度差异、恐慌 1.2 倍加速、受阻后重新起步
都能在元胞网格上体现。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from .movement_config import MovementConfig, SpeedConfig

# Xiong 2025, Table 1：速度区间中心 -> 加速度/减速度的均值与标准差
_SPEED_POINTS = (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0)
_MU_ACCEL = (1.30, 1.43, 1.64, 1.76, 1.62, 1.39, 0.99, 0.86, 0.73, 0.40)
_SIGMA_ACCEL = (0.60, 0.73, 0.65, 0.73, 1.16, 1.07, 0.85, 0.62, 0.45, 0.53)
_MU_DECEL = (1.90, 1.86, 1.88, 1.79, 1.66, 1.39, 0.89, 0.78, 0.61, 0.43)
_SIGMA_DECEL = (1.90, 0.56, 0.72, 0.83, 1.31, 1.11, 0.74, 0.60, 0.51, 0.49)


def _interp(value: float, points: tuple[float, ...], table: tuple[float, ...]) -> float:
    if value <= points[0]:
        return table[0]
    if value >= points[-1]:
        return table[-1]
    for index in range(len(points) - 1):
        left, right = points[index], points[index + 1]
        if left <= value <= right:
            ratio = (value - left) / (right - left)
            return table[index] + ratio * (table[index + 1] - table[index])
    return table[-1]



class SpeedModel:
    def __init__(
        self,
        config: SpeedConfig | None = None,
        movement: MovementConfig | None = None,
        time_step_s: float = 0.5,
        cell_size: float = 0.5,
        rng=None,
    ):
        self.cfg = config or SpeedConfig()
        self.movement = movement
        self.dt = max(1e-6, float(time_step_s))
        self.cell_size = max(1e-6, float(cell_size))
        self.rng = rng
        self._velocity: dict[int, float] = {}
        self._credit: dict[int, float] = {}

    # ------------------------------------------------------------------ 初始化
    def reset(self, persons: list[Any]) -> None:
        self._velocity.clear()
        self._credit.clear()
        cfg = self.cfg
        for person in persons:
            pid = int(person.id)
            if str(cfg.accel_model).lower() == "xiong2025":
                # 每步按当前速度从实测分布采样，初始值留 0 等第一步再填
                setattr(person, "accel_current", 0.0)
            else:
                if self.rng is not None and cfg.accel_sd > 0:
                    accel = self.rng.gauss(cfg.accel, cfg.accel_sd)
                else:
                    accel = cfg.accel
                setattr(person, "accel_current", min(cfg.accel_max, max(cfg.accel_min, float(accel))))
            self._velocity[pid] = self._max_velocity(person) * cfg.initial_velocity_ratio
            self._credit[pid] = float(cfg.initial_credit)

    # ------------------------------------------------------------------ 主更新
    def update(
        self,
        person: Any,
        smoke_concentration: float,
        dose: float = 0.0,
        local_density: float = 1.0,
        blocked_last_step: bool = False,
        dt: float | None = None,
        free_distance_m: float | None = None,
        leader_speed: float | None = None,
    ) -> dict[str, float | int | bool]:
        """推进一个行人的速度状态，返回本步可前进的元胞数。

        :param free_distance_m: 沿行进方向到最近障碍/慢行者的距离（米）
        :param leader_speed: 该慢行者当前速度；障碍物传 0
        """
        cfg = self.cfg
        dt = self.dt if dt is None else max(1e-6, float(dt))
        pid = int(person.id)
        if not cfg.enabled:
            return {
                "velocity": self._max_velocity(person),
                "target_velocity": self._max_velocity(person),
                "credit": 1.0,
                "cells": 1,
                "allow_move": True,
                "blocked": False,
            }

        velocity = self._velocity.get(pid)
        if velocity is None:
            # 允许 C 模块在引擎初始化后调整个体速度
            velocity = self._max_velocity(person) * cfg.initial_velocity_ratio
            self._credit.setdefault(pid, float(cfg.initial_credit))
        target = self._target_velocity(person, smoke_concentration, dose, local_density)
        blocked = bool(blocked_last_step or getattr(person, "is_dead", False))

        # Xiong 2025 Rule 2：到前方慢行者/障碍物的距离小于相对减速距离 L(Δv) 时提前减速，
        # 而不是等到撞上才把速度清零。
        leader = 0.0 if leader_speed is None else max(0.0, float(leader_speed))
        too_close = False
        if cfg.use_deceleration_distance and free_distance_m is not None:
            relative_speed = max(0.0, velocity - leader)
            if float(free_distance_m) < self.deceleration_distance(relative_speed):
                too_close = True
        effective_target = min(target, leader) if too_close else target

        empirical = str(cfg.accel_model).lower() == "xiong2025" and self.rng is not None
        if empirical:
            speed_level = min(5.5, max(0.0, velocity))
            if blocked or too_close or velocity > effective_target:
                mu = _interp(speed_level, _SPEED_POINTS, _MU_DECEL)
                sigma = _interp(speed_level, _SPEED_POINTS, _SIGMA_DECEL)
                magnitude = max(0.1, self.rng.gauss(mu, sigma))
                if blocked:
                    velocity = 0.0 if cfg.stop_on_block else max(0.0, velocity - magnitude * dt)
                else:
                    velocity = max(effective_target, velocity - magnitude * dt)
                accel_value = -magnitude
            else:
                mu = _interp(speed_level, _SPEED_POINTS, _MU_ACCEL)
                sigma = _interp(speed_level, _SPEED_POINTS, _SIGMA_ACCEL)
                magnitude = max(0.1, self.rng.gauss(mu, sigma))
                velocity = min(effective_target, velocity + magnitude * dt)
                accel_value = magnitude
            setattr(person, "accel_current", accel_value)
        else:
            # constant 模式：个体固定加速度（兼容旧配置/对照实验）
            accel = float(getattr(person, "accel_current", cfg.accel) or cfg.accel)
            if blocked:
                velocity = 0.0 if cfg.stop_on_block else max(0.0, velocity - cfg.decel * dt)
            elif too_close or velocity > effective_target:
                velocity = max(effective_target, velocity - cfg.decel * dt)
            elif velocity < effective_target:
                velocity = min(effective_target, velocity + accel * dt)

        velocity = min(cfg.max_speed, max(0.0, velocity))
        credit = self._credit.get(pid, 0.0) + velocity * dt / self.cell_size
        # 最多攒 max_cells_per_step 格，避免长期受阻后"瞬移"
        credit = min(float(cfg.max_cells_per_step), credit)
        cells = int(credit // 1.0) if credit >= 1.0 else 0
        cells = min(cells, cfg.max_cells_per_step)

        self._velocity[pid] = velocity
        self._credit[pid] = credit
        return {
            "velocity": velocity,
            "target_velocity": effective_target,
            "credit": credit,
            "cells": cells,
            "allow_move": cells >= 1,
            "blocked": cells < 1,
        }

    @staticmethod
    def deceleration_distance(relative_speed: float) -> float:
        """Xiong 2025 Eq.(3)：从相对速度 Δv 减速到 0 所需距离 L（米）。"""
        dv = min(5.0, max(0.0, float(relative_speed)))
        return max(0.0, 10.0 - math.sqrt((5.0 - dv) / 0.05))

    # ------------------------------------------------------------------ 状态回写
    def consume(self, person: Any, cells: int) -> None:
        """成功移动 cells 格后扣除距离。"""
        pid = int(person.id)
        credit = self._credit.get(pid, 0.0) - float(cells)
        self._credit[pid] = min(float(self.cfg.max_cells_per_step), max(0.0, credit))

    def on_conflict_lost(self, person: Any) -> None:
        """抢格失败：停下并清空积累，下一步重新加速。"""
        pid = int(person.id)
        self._velocity[pid] = 0.0
        self._credit[pid] = 0.0

    def freeze(self, person: Any) -> None:
        """行人本步不参与疏散移动（未知情/原地等待）时不积累距离，避免"攒步瞬移"。"""
        pid = int(person.id)
        self._velocity[pid] = 0.0
        self._credit[pid] = 0.0

    # ------------------------------------------------------------------ 速度计算
    def _max_velocity(self, person: Any) -> float:
        try:
            base = float(getattr(person, "speed", 1.0) or 1.0)
        except (TypeError, ValueError):
            base = 1.0
        return min(self.cfg.max_speed, max(self.cfg.min_speed, base))

    def _target_velocity(
        self, person: Any, smoke: float, dose: float, local_density: float
    ) -> float:
        cfg = self.cfg
        target = self._max_velocity(person)

        # 恐慌加速：1 + speed_gain（默认 1.2 倍）
        if bool(getattr(person, "is_panicked", False)):
            target *= 1.0 + (self.movement.panic.speed_gain if self.movement else 0.20)

        # 烟雾、剂量导致的减速
        smoke = min(1.0, max(0.0, float(smoke)))
        target *= max(cfg.min_speed_factor, 1.0 - cfg.smoke_slow * smoke)
        dose = max(0.0, float(dose))
        target *= max(0.4, 1.0 / (1.0 + dose / max(1e-6, cfg.dose_ref)))

        # 局部拥挤减速（拥挤时走不快，但不是完全停住）
        density = max(1.0, float(local_density))
        target *= max(0.55, 1.0 - 0.06 * (density - 1.0))

        return min(cfg.max_speed, max(cfg.min_speed, target))
