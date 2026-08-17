"""Week-6 D-side analysis for one run or a baseline/strategy pair.

The analyzer reads D CSV output only. It computes metrics when the required
columns exist and writes ``NA`` when B/C did not provide the corresponding
field. It never infers missing risk, waiting, exit, information, or group
behavior values from coordinates alone.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


NA = "NA"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    return None


def _mean(values: Iterable[float]) -> float | str:
    values = list(values)
    return round(sum(values) / len(values), 6) if values else NA


def _metric_rows(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    units = {
        "total_steps": "step",
        "total_time_s": "s",
        "evacuated_count": "person",
        "remaining_count": "person",
        "evacuation_rate": "ratio",
        "first_evac_time_s": "s",
        "last_evac_time_s": "s",
        "t90_time_s": "s",
        "max_smoke": "concentration",
        "avg_smoke": "concentration",
        "avg_risk": "risk",
        "avg_dose": "dose",
        "avg_congestion": "ratio",
        "mean_waiting_time_s": "s",
        "informed_rate": "ratio",
        "group_cohesion": "ratio",
        "exit_distribution": "json",
        "improvement_rate": "ratio",
    }
    return [
        {
            "metric_name": name,
            "value": metrics.get(name, NA),
            "unit": units.get(name, ""),
            "note": metrics.get(f"{name}_note", ""),
        }
        for name in units
    ]


def _group_cohesion_values(values: Iterable[tuple[str, str]]) -> list[float]:
    by_group: dict[str, Counter[str]] = defaultdict(Counter)
    for group_id, exit_id in values:
        by_group[group_id][exit_id] += 1
    cohesion: list[float] = []
    for exits in by_group.values():
        group_size = sum(exits.values())
        if group_size > 1:
            cohesion.append(max(exits.values()) / group_size)
    return cohesion


def analyze_run(run_dir: str | Path) -> dict[str, Any]:
    """Analyze D's ``people_log.csv`` and optional event/config files."""

    output_dir = Path(run_dir)
    rows = _read_csv(output_dir / "people_log.csv")
    if not rows:
        raise ValueError("people_log.csv contains no rows")

    by_step: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        step = _int(row.get("step"))
        if step is not None:
            by_step[step].append(row)
    if not by_step:
        raise ValueError("people_log.csv contains no valid step values")

    steps = sorted(by_step)
    initial_rows = by_step[steps[0]]
    final_rows = by_step[steps[-1]]
    population = len({row.get("person_id") for row in initial_rows})
    evacuated_by_person: dict[str, float] = {}
    all_smoke: list[float] = []
    all_risk: list[float] = []
    all_dose: list[float] = []
    all_congestion: list[float] = []
    informed_values: list[bool] = []
    waiting_values: list[float] = []
    latest_exit_by_person: dict[str, str] = {}
    group_exit_by_person: dict[str, tuple[str, str]] = {}

    for row in rows:
        time_s = _float(row.get("time_s"))
        evacuated = _bool(row.get("evacuated"))
        person_id = row.get("person_id", "")
        if evacuated and time_s is not None:
            evacuated_by_person.setdefault(person_id, time_s)
        smoke = _float(row.get("smoke_concentration") or row.get("smoke"))
        if smoke is not None:
            all_smoke.append(smoke)
        risk = _float(row.get("risk"))
        if risk is not None:
            all_risk.append(risk)
        dose = _float(row.get("dose"))
        if dose is not None:
            all_dose.append(dose)
        congestion = _float(row.get("congestion") or row.get("congestion_level"))
        if congestion is not None:
            all_congestion.append(congestion)
        exit_id = row.get("actual_exit") or row.get("target_exit")
        if exit_id not in (None, ""):
            latest_exit_by_person[person_id] = str(exit_id)
            group_id = row.get("group_id")
            if group_id not in (None, ""):
                group_exit_by_person[person_id] = (str(group_id), str(exit_id))
        info_state = row.get("info_state")
        if info_state not in (None, ""):
            informed_values.append(str(info_state).upper() != "UNKNOWN")
        waiting = _float(row.get("waiting_time_s") or row.get("wait_time_s"))
        if waiting is not None:
            waiting_values.append(waiting)

    final_evacuated = sum(1 for row in final_rows if _bool(row.get("evacuated")) is True)
    evacuation_times = list(evacuated_by_person.values())
    total_time = max((_float(row.get("time_s")) or 0.0 for row in final_rows), default=0.0)
    exit_distribution = Counter(latest_exit_by_person.values())
    group_values = _group_cohesion_values(group_exit_by_person.values())
    t90_target = math.ceil(population * 0.9)
    t90 = NA
    for step in steps:
        count = sum(1 for row in by_step[step] if _bool(row.get("evacuated")) is True)
        if count >= t90_target:
            t90 = _float(by_step[step][0].get("time_s")) or 0.0
            break

    metrics: dict[str, Any] = {
        "run_id": rows[0].get("run_id", output_dir.name),
        "scenario_id": rows[0].get("scenario_id", NA),
        "total_steps": steps[-1],
        "total_time_s": total_time,
        "evacuated_count": final_evacuated,
        "remaining_count": max(0, population - final_evacuated),
        "evacuation_rate": round(final_evacuated / population, 6) if population else NA,
        "first_evac_time_s": min(evacuation_times) if evacuation_times else NA,
        "last_evac_time_s": max(evacuation_times) if evacuation_times else NA,
        "t90_time_s": t90,
        "max_smoke": max(all_smoke) if all_smoke else NA,
        "avg_smoke": _mean(all_smoke),
        "avg_risk": _mean(all_risk),
        "avg_dose": _mean(all_dose),
        "avg_congestion": _mean(all_congestion),
        "mean_waiting_time_s": _mean(waiting_values),
        "informed_rate": _mean(float(value) for value in informed_values),
        "group_cohesion": _mean(group_values),
        "exit_distribution": dict(exit_distribution) if exit_distribution else NA,
        "group_cohesion_note": (
            "NA: B/C did not provide group_id with exit choice"
            if not group_values
            else "mean majority-exit share per group"
        ),
        "avg_congestion_note": (
            "NA: B did not provide congestion"
            if not all_congestion
            else "mean of B congestion rows"
        ),
        "mean_waiting_time_s_note": (
            "NA: B did not provide waiting_time_s"
            if not waiting_values
            else "mean of B waiting_time_s rows"
        ),
    }
    return metrics


def compare_runs(baseline: Mapping[str, Any], strategy: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate simple improvement rates for lower-is-better metrics."""

    result = dict(strategy)
    for name in (
        "total_time_s",
        "last_evac_time_s",
        "t90_time_s",
        "avg_smoke",
        "avg_risk",
        "avg_dose",
        "avg_congestion",
    ):
        base_value = _float(baseline.get(name))
        strategy_value = _float(strategy.get(name))
        if base_value is None or strategy_value is None or abs(base_value) < 1e-12:
            result[f"{name}_improvement_rate"] = NA
        else:
            result[f"{name}_improvement_rate"] = round((base_value - strategy_value) / abs(base_value), 6)
    return result


def write_analysis(metrics: Mapping[str, Any], output_dir: str | Path) -> None:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    (output_path / "week6_metrics.json").write_text(
        json.dumps(dict(metrics), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_path / "week6_metrics_summary.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["metric_name", "value", "unit", "note"])
        writer.writeheader()
        writer.writerows(_metric_rows(metrics))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze D week-6 evacuation experiment output.")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--baseline-dir", type=Path)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    metrics = analyze_run(args.run_dir)
    if args.baseline_dir is not None:
        metrics = compare_runs(analyze_run(args.baseline_dir), metrics)
    write_analysis(metrics, args.output_dir or args.run_dir)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
