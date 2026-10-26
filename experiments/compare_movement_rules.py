"""小规模对比实验：文献化移动规则 vs 旧 CA（同一地图、同一人群、同一种子）

用途：
1. 论文/答辩里说明"新规则带来了什么变化"；
2. 迁移后做回归：确认新规则没有把仿真跑崩。

运行（在项目根目录）：
    python -m experiments.compare_movement_rules
输出：
    控制台表格 + outputs/compare_movement_rules.json
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_PATH))

from core.schema import Cell, CellType, Exit, Grid, Person, ScenarioConfig, SmokeSource
from simulation.evac_simulation import EvacEngine


def build_scene(*, movement_enabled: bool) -> ScenarioConfig:
    width, height = 24, 14
    exits = ((23, 3), (23, 10))
    cells = []
    for y in range(height):
        for x in range(width):
            if (x, y) in exits:
                cell_type = CellType.EXIT
            elif x in (0, width - 1) or y in (0, height - 1):
                cell_type = CellType.WALL
            else:
                cell_type = CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))

    grid = Grid(width=width, height=height, cell_size=0.5, cells=cells)

    persons = []
    pid = 1
    for x in range(2, 8):
        for y in range(3, 11):
            if pid > 48:
                break
            persons.append(
                Person(
                    id=pid,
                    x=x,
                    y=y,
                    speed=0.8 + 0.4 * ((pid * 37) % 7) / 6.0,
                    risk_sensitivity=0.4 + 0.4 * ((pid * 17) % 5) / 4.0,
                    info_state="INFORMED",
                )
            )
            pid += 1

    return ScenarioConfig(
        scenario_id="compare_two_exits",
        grid=grid,
        exits=[Exit(id="exit_A", x=23, y=3, width=1.5), Exit(id="exit_B", x=23, y=10, width=1.5)],
        persons=persons,
        smoke_sources=[SmokeSource(x=18, y=3, intensity=0.8)],
        parameters={
            "time_step_s": 0.5,
            "random_seed": 42,
            "movement": {"enabled": movement_enabled},
        },
    )


def run(label: str, movement_enabled: bool, max_steps: int = 600) -> dict:
    scene = build_scene(movement_enabled=movement_enabled)
    engine = EvacEngine(scene)
    steps = 0
    for step in range(max_steps):
        engine.run_one_step()
        steps = step + 1
        if engine.is_all_evacuated():
            break

    people = list(engine.person_map.values())
    evac_times = [p.evac_step for p in people if getattr(p, "evacuated", False)]
    exit_usage: dict[str, int] = {}
    for person in people:
        if getattr(person, "evacuated", False):
            exit_id = str(getattr(person, "actual_exit", "unknown"))
            exit_usage[exit_id] = exit_usage.get(exit_id, 0) + 1

    stats = engine.movement_stats
    speeds = [float(getattr(p, "speed_current", 0.0) or 0.0) for p in people]
    result = {
        "label": label,
        "movement_enabled": movement_enabled,
        "steps": steps,
        "sim_time_s": steps * 0.5,
        "evacuated": sum(1 for p in people if getattr(p, "evacuated", False)),
        "dead": sum(1 for p in people if getattr(p, "is_dead", False)),
        "total": len(people),
        "mean_evac_step": statistics.fmean(evac_times) if evac_times else None,
        "exit_usage": exit_usage,
        "exit_switches": stats.get("switch_total", 0),
        "panic_peak": max((float(getattr(p, "panic", 0.0)) for p in people), default=0.0)
        if movement_enabled
        else None,
        "blocked_total": stats.get("blocked_total", 0),
        "mean_speed": round(statistics.fmean(speeds), 3) if speeds else 0.0,
    }
    print(
        f"{label:22s} 撤离 {result['evacuated']:2d}/{result['total']}  "
        f"死亡 {result['dead']:2d}  用时 {result['sim_time_s']:6.1f}s  "
        f"换出口 {result['exit_switches']:3d}  出口使用 {result['exit_usage']}"
    )
    return result


def main() -> int:
    print(f"项目根目录: {BASE_PATH}")
    print("同一地图 / 同一人群 / 同一种子，仅切换移动规则：\n")
    rows = [
        run("旧规则(legacy)", False),
        run("文献化规则", True),
    ]
    output = BASE_PATH / "outputs" / "compare_movement_rules.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=2)
    print(f"\n结果已写入: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
