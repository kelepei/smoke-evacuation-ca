"""
resolve_conflict.py
B02 CA冲突消解模块

处理多行人候选移动到同一个目标格子的竞争冲突。

调研结论（白鹏 2026；Xiong 2025；张琦 2022）：
- 恐慌状态人员在抢格时有更大概率获得通行权；
- 冲突时移动速度更大的人获胜；
- 不耐烦/危险感知更高的人有"抢占优先权"。

因此本模块不再"纯随机选赢家"，而是按"速度 + 恐慌值（+ 危险感知）"叠加评分，
得分最高者获胜；未获胜者留在原地。实现上按评分从高到低依次占格，避免同时移动叠格。
"""

from .movement_config import ConflictConfig


def _panic_of(person) -> float:
    try:
        return float(getattr(person, "panic", getattr(person, "panic_level", 0.0)) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _speed_of(person) -> float:
    try:
        current = float(getattr(person, "speed_current", 0.0) or 0.0)
        base = float(getattr(person, "speed", 1.0) or 1.0)
    except (TypeError, ValueError):
        return 1.0
    # 尚未进入移动规则的行人 speed_current 为 0，回退到基础速度
    return current if current > 1e-6 else base


def conflict_priority(person, config: ConflictConfig | None = None) -> float:
    """抢格竞争力：速度 + 恐慌值 叠加。"""
    cfg = config or ConflictConfig()
    speed_term = min(1.6, max(0.0, _speed_of(person) / max(1e-6, cfg.speed_ref)))
    panic_term = min(1.0, max(0.0, _panic_of(person)))
    danger_term = float(getattr(person, "danger_pressure", 0.0) or 0.0)
    return cfg.w_speed * speed_term + cfg.w_panic * panic_term + 0.5 * danger_term


def resolve_conflict(candidate_moves: dict[int, tuple[int, int]],
                     person_map: dict[int, object],
                     rng,
                     priorities: dict[int, float] | None = None,
                     config: ConflictConfig | None = None) -> dict[int, tuple[int, int]]:
    """
    CA元胞自动机冲突消解

    :param candidate_moves: dict[行人id, (目标x,y)]
    :param person_map: 行人对象字典 {pid:Person实例}
    :param rng: 外部传入Random实例，绑定全局seed，保证仿真结果可复现
    :param priorities: 可选的竞争力评分（引擎统一计算后传入）
    :param config: 抢格权重配置
    :return: dict[pid, (final_x, final_y)]
    """
    cfg = config or ConflictConfig()

    # 先用"当前占用"建立初始占有关系，再按竞争力从高到低依次处理想移动的人：
    #   - 目标格仍被占用 → 留在原地；
    #   - 目标格已空出 → 离开自己的格子并占据目标格。
    # 这样天然满足三件事：不允许移动的人作为静止占用者、抢格输家留在原地、
    # 不会出现"A 想进 B 的格子、B 抢格失败留在原地"导致两人叠格。
    result: dict[int, tuple[int, int]] = {}
    claimed: dict[tuple[int, int], int] = {}
    movers: list[int] = []
    for pid, pos in candidate_moves.items():
        p = person_map[pid]
        current = (int(p.x), int(p.y))
        result[pid] = current
        if getattr(p, "evacuated", False):
            continue
        claimed[current] = pid
        if (int(pos[0]), int(pos[1])) != current:
            movers.append(pid)

    def _score(pid: int) -> float:
        if priorities is not None and pid in priorities:
            return float(priorities[pid])
        return conflict_priority(person_map[pid], cfg)

    movers.sort(key=lambda pid: (-_score(pid), pid))

    # Xiong 2025 的摩擦模型：同一目标格的多人竞争先整体判定
    #   μ1 = (⟨v⟩/v_max)^m            —— 所有人本次都进不去
    #   μ2 = v_i^k / Σ v_j^k          —— 摩擦未发生时，速度高者更可能获胜
    # 恐慌/危险压力以乘积形式叠加到 μ2 的权重上（项目扩展；都为 0 时即纯原文）。
    friction_blocked: set[int] = set()
    designated: dict[int, int] = {}  # target -> winner pid
    if cfg.friction_enabled:
        groups: dict[tuple[int, int], list[int]] = {}
        for pid in movers:
            target = (int(candidate_moves[pid][0]), int(candidate_moves[pid][1]))
            groups.setdefault(target, []).append(pid)
        for target, pids in groups.items():
            if len(pids) == 1:
                designated[target] = pids[0]
                continue
            speeds = [max(0.05, _speed_of(person_map[pid])) for pid in pids]
            mean_speed = sum(speeds) / len(speeds)
            vmax = max(1e-6, float(cfg.friction_vmax))
            mu1 = min(1.0, max(0.0, (mean_speed / vmax) ** max(0.0, float(cfg.friction_m))))
            if rng.random() < mu1:
                friction_blocked.update(pids)
                continue
            exponent = max(0.0, float(cfg.speed_exponent_k))
            weights = []
            for pid, speed in zip(pids, speeds):
                panic = min(1.0, max(0.0, _panic_of(person_map[pid])))
                danger = min(1.0, max(0.0, float(getattr(person_map[pid], "danger_pressure", 0.0) or 0.0)))
                boost = 1.0 + cfg.w_panic * panic + 0.5 * danger
                weights.append((speed ** exponent) * boost)
            if sum(weights) <= 0:
                designated[target] = pids[0]
            else:
                designated[target] = rng.choices(pids, weights=weights, k=1)[0]
    else:
        # 关闭摩擦模型时退回"按竞争力顺序依次占格"的确定性行为
        friction_blocked = set()
        designated = {}

    for pid in movers:
        person_obj = person_map[pid]
        current = (int(person_obj.x), int(person_obj.y))
        target = (int(candidate_moves[pid][0]), int(candidate_moves[pid][1]))
        if cfg.friction_enabled:
            if pid in friction_blocked:
                continue
            if designated.get(target) != pid:
                continue
        if target in claimed and claimed[target] != pid:
            continue
        claimed.pop(current, None)
        claimed[target] = pid
        result[pid] = target

    return result
