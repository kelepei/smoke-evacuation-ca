"""危险感知（替代"不耐烦"）：按张琦 2025 的不耐烦公式实现

原文（中国安全生产科学技术，2025，21(2)：37-43）：

    imp_i(t) = (1 - v_i(t)/v_i^e) · exp(-γ(v_i(t) - v_i(t-1))) · exp(β·t_c)

- v_i(t)：最近 10 个时间步的实际速度（位移 / 时间）；
- v_i^e：期望速度；t_c：低速或停滞持续时间（v < λ·v^e，λ=0.2）；
- γ=0.5（对速度变化趋势的敏感性），β=0.1（对停滞时间的敏感性）。

本项目用"危险感知"替代"不耐烦"：在上式基础上加一个烟雾/风险附加项，
并把结果归一化到 0~1，供横向绕道、抢格优先、换出口使用：

    imp_total = imp + smoke_gain · risk_norm
    danger_pressure = imp_total / (1 + imp_total)

`smoke_gain=0` 时退化为纯原文的不耐烦模型。
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Mapping

from .movement_config import DangerConfig


class DangerPerceptionModel:
    def __init__(
        self,
        config: DangerConfig | None = None,
        time_step_s: float = 0.5,
        cell_size: float = 0.5,
    ):
        self.cfg = config or DangerConfig()
        self.dt = max(1e-6, float(time_step_s))
        self.cell_size = max(1e-6, float(cell_size))
        self._history: dict[int, deque[tuple[int, int]]] = {}
        self._prev_speed: dict[int, float] = {}
        self._stagnation: dict[int, float] = {}

    def reset(self, persons: list[Any]) -> None:
        self._history.clear()
        self._prev_speed.clear()
        self._stagnation.clear()
        for person in persons:
            self._history[int(person.id)] = deque(maxlen=max(2, int(self.cfg.window_steps) + 1))
            self._prev_speed[int(person.id)] = float(getattr(person, "speed", 1.0) or 1.0)
            self._stagnation[int(person.id)] = 0.0
            self._write(person, 0.0, 0.0)

    def update(
        self,
        persons: list[Any],
        risk_map: Mapping[int, float] | None,
        blocked_flags: Mapping[int, bool] | None = None,
        dt: float | None = None,
    ) -> dict[int, dict[str, float | bool]]:
        cfg = self.cfg
        dt = self.dt if dt is None else max(1e-6, float(dt))
        risk_map = risk_map or {}
        result: dict[int, dict[str, float | bool]] = {}
        if not cfg.enabled:
            return result

        for person in persons:
            pid = int(person.id)
            expected = max(0.1, float(getattr(person, "speed", 1.0) or 1.0))
            history = self._history.setdefault(pid, deque(maxlen=max(2, int(cfg.window_steps) + 1)))
            history.append((int(person.x), int(person.y)))

            speed = self._window_speed(history, expected)
            previous_speed = self._prev_speed.get(pid, speed)
            self._prev_speed[pid] = speed

            # 低速/停滞持续时间 t_c
            stagnation = self._stagnation.get(pid, 0.0)
            if speed < cfg.low_speed_ratio * expected:
                stagnation += dt
            else:
                stagnation = 0.0
            self._stagnation[pid] = stagnation

            base = max(0.0, 1.0 - speed / expected)
            trend = math.exp(-float(cfg.gamma) * (speed - previous_speed))
            impatience = base * trend * math.exp(float(cfg.beta) * stagnation)
            impatience = min(50.0, max(0.0, impatience))

            risk_norm = min(1.0, max(0.0, float(risk_map.get(pid, 0.0) or 0.0) / max(1e-6, cfg.risk_ref)))
            total = impatience + max(0.0, float(cfg.smoke_gain)) * risk_norm
            pressure = total / (1.0 + total) if total > 0 else 0.0

            self._write(person, pressure, impatience, stagnation)
            result[pid] = {
                "danger_pressure": pressure,
                "danger_urgent": pressure >= cfg.threshold,
                "impatience": impatience,
                "stagnation_s": stagnation,
            }
        return result

    # ------------------------------------------------------------------ 辅助
    def _window_speed(self, history: deque[tuple[int, int]], expected: float) -> float:
        """最近 window 步的位移 / 时间；历史不足 2 个点时按正常行走处理。"""
        if len(history) < 2:
            return expected
        points = list(history)
        distance = 0.0
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            distance += math.hypot(x2 - x1, y2 - y1)
        elapsed = (len(points) - 1) * self.dt
        if elapsed <= 0:
            return expected
        return (distance * self.cell_size) / elapsed

    def _write(
        self,
        person: Any,
        pressure: float,
        impatience: float = 0.0,
        stagnation: float = 0.0,
    ) -> None:
        setattr(person, "danger_pressure", pressure)
        setattr(person, "danger_urgent", pressure >= self.cfg.threshold)
        setattr(person, "impatience", impatience)
        setattr(person, "stagnation_s", stagnation)
