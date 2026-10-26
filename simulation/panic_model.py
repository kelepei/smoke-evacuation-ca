"""恐慌阈值与恐慌传染模型（文献调研版）

对应调研结论：
- 白鹏等（2026）：情绪阈值 α 区分易感者/感染者；恐慌者速度变为 1.2 倍；
- 王莉等（2023）：情绪阈值 η；恐慌后更容易做出错误决定，人群密集时恐慌反复传播。

本模块只负责"恐慌值"本身：输入烟雾场、综合风险、邻居恐慌比例，输出 0~1 的
恐慌值 P_i 与是否越过阈值。速度增益由 speed_model 使用，决策噪声由 exit_choice 使用。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from .movement_config import PanicConfig


class PanicModel:
    """逐步更新每个行人的恐慌值。"""

    def __init__(self, config: PanicConfig | None = None, time_step_s: float = 0.5, rng=None):
        self.cfg = config or PanicConfig()
        self.dt = max(1e-6, float(time_step_s))
        self.rng = rng
        # person_id -> 上一帧所在格烟雾浓度（用于 ΔS）
        self._last_smoke: dict[int, float] = {}

    # ------------------------------------------------------------------ 初始化
    def reset(self, persons: list[Any]) -> None:
        self._last_smoke.clear()
        cfg = self.cfg
        for person in persons:
            # 白鹏 2026：人格特质 ρ(m) ~ N(0.5, 0.1)，决定情绪感染速度
            if self.rng is not None:
                susceptibility = self.rng.gauss(cfg.susceptibility_mean, cfg.susceptibility_sd)
            else:
                susceptibility = cfg.susceptibility_mean
            susceptibility = min(0.99, max(0.01, float(susceptibility)))
            setattr(person, "panic_susceptibility", susceptibility)
            initial = susceptibility if cfg.initial_from_susceptibility else 0.0
            self._write_state(person, initial, initial >= cfg.threshold)
        return None

    # ------------------------------------------------------------------ 主更新
    def update(
        self,
        persons: list[Any],
        smoke_matrix: np.ndarray,
        risk_map: Mapping[int, float] | None = None,
        positions: Mapping[tuple[int, int], int] | None = None,
        dt: float | None = None,
    ) -> dict[int, dict[str, float | bool]]:
        """更新全部行人的恐慌值。

        :param persons: 存活行人列表
        :param smoke_matrix: 当前烟雾场 (H, W)
        :param risk_map: risk_perception 输出的 {pid: risk}
        :param positions: {(x, y): pid} 用于计算恐慌传染
        :param dt: 本步时长，默认使用构造时的 time_step_s
        :return: {pid: {"panic": P, "is_panicked": bool, "neighbor_panic_ratio": r}}
        """
        cfg = self.cfg
        dt = self.dt if dt is None else max(1e-6, float(dt))
        risk_map = risk_map or {}
        result: dict[int, dict[str, float | bool]] = {}
        if not cfg.enabled:
            for person in persons:
                self._write_state(person, 0.0, False)
            return result

        height, width = smoke_matrix.shape
        # 先算所有邻居恐慌比例，避免"边更新边传染"的顺序偏差
        ratios: dict[int, float] = {}
        if cfg.contagion > 0 and positions:
            ratios = self._neighbor_panic_ratio(persons, positions)

        for person in persons:
            pid = int(person.id)
            x, y = int(person.x), int(person.y)
            smoke = 0.0
            if 0 <= y < height and 0 <= x < width:
                smoke = float(smoke_matrix[y, x])
            last = self._last_smoke.get(pid, smoke)
            delta_s = smoke - last
            self._last_smoke[pid] = smoke

            risk = float(risk_map.get(pid, 0.0) or 0.0)
            # 归一化的危险信号：浓度、浓度变化、综合风险
            exposure = (
                min(1.0, max(0.0, smoke) / max(1e-6, cfg.smoke_ref))
                + cfg.delta_weight * min(1.0, max(0.0, delta_s) / max(1e-6, self.dt))
                + cfg.risk_weight * min(1.0, max(0.0, risk) / max(1e-6, cfg.risk_ref))
            )
            # 饱和映射，避免烟雾无限增大导致 P 立刻顶到 1
            target = 1.0 - math.exp(-exposure)

            ratio = ratios.get(pid, 0.0)
            susceptibility = float(getattr(person, "panic_susceptibility", 1.0) or 1.0)
            # 易感者→感染者：自身危险信号 + 周围恐慌传染，按人格特质 ρ(m) 缩放；同时存在衰减
            growth = susceptibility * cfg.growth * target * (1.0 - self._panic_of(person))
            contagion = susceptibility * cfg.contagion * ratio * (1.0 - self._panic_of(person))
            decay = cfg.decay * self._panic_of(person)
            panic = self._panic_of(person) + dt * (growth + contagion - decay)
            panic = min(1.0, max(0.0, panic))

            # 滞回：已恐慌者要降到 exit_threshold 以下才恢复；未恐慌者要越过 threshold 才进入
            if bool(getattr(person, "is_panicked", False)):
                is_panicked = panic >= cfg.exit_threshold
            else:
                is_panicked = panic >= cfg.threshold

            self._write_state(person, panic, is_panicked)
            result[pid] = {
                "panic": panic,
                "is_panicked": is_panicked,
                "neighbor_panic_ratio": ratio,
            }
        return result

    # ------------------------------------------------------------------ 辅助
    def _neighbor_panic_ratio(
        self, persons: list[Any], positions: Mapping[tuple[int, int], int]
    ) -> dict[int, float]:
        radius = max(1, int(self.cfg.contagion_radius))
        panic_by_id = {int(p.id): self._panic_of(p) for p in persons}
        ratios: dict[int, float] = {}
        for person in persons:
            pid = int(person.id)
            px, py = int(person.x), int(person.y)
            total = 0
            panicked = 0
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if dx == 0 and dy == 0:
                        continue
                    other = positions.get((px + dx, py + dy))
                    if other is None or other == pid:
                        continue
                    total += 1
                    if panic_by_id.get(other, 0.0) >= self.cfg.threshold:
                        panicked += 1
            ratios[pid] = (panicked / total) if total else 0.0
        return ratios

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
        # C 桥 / 可视化沿用 speed_multiplier 字段，这里同步"恐慌导致的速度增益"
        setattr(
            person,
            "speed_multiplier",
            1.0 + (self.cfg.speed_gain if is_panicked else 0.0),
        )
        return None
