# -*- coding: utf-8 -*-
"""恐慌→风险反馈（λ）单因子批量实验。

用法（在项目根目录）：
    .venv\\Scripts\\python.exe -m experiments.lambda_sweep ^
        --map scenarios\\classroom_corridor.json ^
        --people docs\\d_week3\\examples\\c_output_people.json ^
        --lambdas 0,0.2,0.4,0.6,0.8 --seeds 30 --smoke-at 15,10

输出：outputs/lambda_sweep.csv（每行一次运行）+ 控制台按 λ 汇总。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from experiments.integrated_runner import create_integrated_runner  # noqa: E402


def pick_free_cell(map_path: Path, near: tuple[int, int]) -> tuple[int, int]:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    free = [(c["x"], c["y"]) for c in data["cells"] if str(c.get("type", "")).lower() == "free"]
    if not free:
        raise SystemExit("地图里没有 free 单元格")
    return min(free, key=lambda p: (p[0] - near[0]) ** 2 + (p[1] - near[1]) ** 2)


def inject_smoke(map_path: Path, out_path: Path, smoke_at: tuple[int, int]) -> Path:
    data = json.loads(map_path.read_text(encoding="utf-8"))
    cell = next((c for c in data["cells"] if (c["x"], c["y"]) == smoke_at), None)
    if cell is None:
        raise SystemExit(f"烟源位置 {smoke_at} 不在地图内")
    cell["type"] = "smoke_source"
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out_path


def entropy(counts):
    total = sum(counts)
    if total <= 0 or len(counts) <= 1:
        return float("nan")
    h = 0.0
    for n in counts:
        if n > 0:
            p = n / total
            h -= p * math.log(p)
    return h / math.log(len(counts))


def gini(counts):
    n = len(counts)
    total = sum(counts)
    if n <= 1 or total <= 0:
        return float("nan")
    diff = sum(abs(a - b) for a in counts for b in counts)
    mean = total / n
    return diff / (2 * n * n * mean)


def repair_positions(map_path: Path, people_path: Path, out_path: Path) -> tuple[Path, int]:
    """把落在墙里/重复的行人位置改到自由格，返回修正后的文件与人数。"""
    map_data = json.loads(map_path.read_text(encoding="utf-8"))
    free = [(c["x"], c["y"]) for c in map_data["cells"] if str(c.get("type", "")).lower() == "free"]
    data = json.loads(people_path.read_text(encoding="utf-8"))
    people = data["persons"] if isinstance(data, dict) and "persons" in data else data
    used = set()
    fixed = 0
    for person in people:
        x, y = int(person.get("x", -1)), int(person.get("y", -1))
        if (x, y) in free and (x, y) not in used:
            used.add((x, y))
            continue
        for cand in free:
            if cand not in used:
                person["x"], person["y"] = cand
                used.add(cand)
                fixed += 1
                break
    out_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return out_path, fixed


def run_once(map_path, people_path, yaml_path, lam, seed, max_steps, pressure_gain):
    runner = create_integrated_runner(
        map_path=map_path,
        population_path=people_path,
        yaml_path=yaml_path,
        c_module_path=ROOT / "control" / "scene_config.py",
        output_root=ROOT / "outputs" / "experiments",
        run_id=f"lambda_{lam}_s{seed}_{int(time.time() * 1000) % 1000000}",
        random_seed=seed,
        time_step_s=0.5,
        max_steps=max_steps,
        c_runtime_config={
            "movement": {
                "panic": {
                    "panic_to_risk_gain": float(lam),
                    "risk_feedback_cap": 3.0,
                },
                "exit_choice": {"exit_pressure_gain": float(pressure_gain)},
            }
        },
    )
    snap = runner.initialize()
    engine = runner.simulation
    panic_peak = 0.0
    risk_sum = 0.0
    risk_n = 0
    steps = 0
    for _ in range(max_steps):
        snap = runner.step()
        steps += 1
        for p in snap.get("people", []):
            if p.get("panic_level") is not None:
                panic_peak = max(panic_peak, float(p["panic_level"]))
            if p.get("risk") is not None and not p.get("evacuated"):
                risk_sum += float(p["risk"])
                risk_n += 1
        if getattr(runner, "finished", False):
            break

    people = snap.get("people", [])
    evacuated = [p for p in people if p.get("evacuated")]
    usage = {}
    for p in people:
        exit_id = p.get("actual_exit") or p.get("target_exit_id")
        if exit_id:
            usage[str(exit_id)] = usage.get(str(exit_id), 0) + 1
    counts = list(usage.values())

    metrics = snap.get("metrics") or {}
    stats = getattr(engine, "movement_stats", {}) or {}
    chooser = getattr(engine, "exit_chooser", None)
    switch_total = int(metrics.get("switch_total", getattr(chooser, "switch_total", 0)) or 0)
    conflict_total = int(metrics.get("conflict_total", stats.get("conflict_total", 0)) or 0)
    conflict_lost_total = int(metrics.get("conflict_lost_total", stats.get("conflict_lost_total", 0)) or 0)
    if metrics.get("exit_usage"):
        usage = {str(k): int(v) for k, v in metrics["exit_usage"].items()}
        counts = list(usage.values())

    doses = [float(p["dose"]) for p in people if p.get("dose") is not None]
    doses.sort()
    q95 = doses[min(len(doses) - 1, int(0.95 * (len(doses) - 1)))] if doses else 0.0

    row = {
        "lambda": lam,
        "seed": seed,
        "people": len(people),
        "steps": steps,
        "finished": bool(getattr(runner, "finished", False)),
        "T_all_s": float(snap.get("time_s", steps * 0.5)) if getattr(runner, "finished", False) else "",
        "evacuated": len(evacuated),
        "switch_total": switch_total,
        "switch_per_person": switch_total / max(1, len(people)),
        "conflict_total": conflict_total,
        "conflict_lost_total": conflict_lost_total,
        "H": entropy(counts) if counts else "",
        "G": gini(counts) if counts else "",
        "panic_peak": panic_peak,
        "risk_mean": (risk_sum / risk_n) if risk_n else "",
        "q95_dose": q95,
    }
    runner.close()
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="scenarios/classroom_corridor.json")
    ap.add_argument("--people", default="docs/d_week3/examples/c_output_people.json")
    ap.add_argument("--yaml", default="control/config_template.yaml")
    ap.add_argument("--lambdas", default="0,0.25,0.5,0.75")
    ap.add_argument("--seeds", type=int, default=30)
    ap.add_argument("--seed-start", type=int, default=1)
    ap.add_argument("--max-steps", type=int, default=600)
    ap.add_argument("--smoke-at", default="", help="把该格改成烟源，如 15,10；为空则不改地图")
    ap.add_argument("--pressure-gain", type=float, default=0.5, help="exit_pressure_gain g_D")
    ap.add_argument("--out", default="outputs/lambda_sweep.csv")
    args = ap.parse_args()

    map_path = ROOT / args.map
    people_path = ROOT / args.people
    yaml_path = ROOT / args.yaml if (ROOT / args.yaml).is_file() else None

    tmp_dir = ROOT / "outputs" / "_lambda_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    if args.smoke_at:
        x, y = (int(v) for v in args.smoke_at.split(","))
        free = pick_free_cell(map_path, (x, y))
        map_path = inject_smoke(map_path, tmp_dir / "map_with_smoke.json", free)
        print(f"[smoke] 烟源放在最近的自由格 {free}（请求 {x},{y}）", flush=True)

    people_path, fixed = repair_positions(map_path, people_path, tmp_dir / "people_fixed.json")
    if fixed:
        print(f"[people] 修正了 {fixed} 个落在墙里或重复的初始位置", flush=True)

    lambdas = [float(v) for v in args.lambdas.split(",") if v.strip()]
    rows = []
    total = len(lambdas) * args.seeds
    done = 0
    for lam in lambdas:
        for k in range(args.seeds):
            seed = args.seed_start + k
            done += 1
            row = run_once(map_path, people_path, yaml_path, lam, seed, args.max_steps, args.pressure_gain)
            rows.append(row)
            print(f"[{done}/{total}] lambda={lam} seed={seed} "
                  f"evac={row['evacuated']}/{row['people']} switch={row['switch_total']} "
                  f"conflict={row['conflict_total']} H={row['H'] if row['H'] != '' else 'NA'}", flush=True)

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print("\n=== 按 λ 汇总（均值） ===")
    for lam in lambdas:
        subset = [r for r in rows if r["lambda"] == lam]
        def mean(key):
            vals = [float(r[key]) for r in subset if r[key] != "" and r[key] is not None]
            return statistics.mean(vals) if vals else float("nan")
        print(f"λ={lam:<4} switch={mean('switch_total'):7.2f} conflict={mean('conflict_total'):7.2f} "
              f"H={mean('H'):.3f} G={mean('G'):.3f} T_all={mean('T_all_s'):7.1f}s panic={mean('panic_peak'):.3f}")
    print(f"\nCSV: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
