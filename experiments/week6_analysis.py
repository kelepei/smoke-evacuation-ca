"""Week-6 D-side analysis for one run or a baseline/strategy pair.

The analyzer reads D CSV output only. It computes metrics when the required
columns exist and writes ``NA`` when B/C did not provide the corresponding
field. It never infers missing risk, waiting, exit, information, or group
behavior values from coordinates alone.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.metrics_registry import NA, metric_rows

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


def _q95(values: Iterable[float]) -> float | str:
    ordered = sorted(values)
    if not ordered:
        return NA
    if len(ordered) == 1:
        return round(ordered[0], 6)
    position = 0.95 * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 6)


def _exit_balance(counts: list[int]) -> tuple[float | str, float | str]:
    """Return normalized entropy H and Gini G for the declared exit universe."""

    total = sum(counts)
    exit_count = len(counts)
    if exit_count < 2 or total <= 0:
        return NA, NA
    probabilities = [count / total for count in counts if count > 0]
    entropy = -sum(value * math.log(value) for value in probabilities) / math.log(exit_count)
    difference_sum = sum(abs(left - right) for left in counts for right in counts)
    gini = difference_sum / (2 * exit_count * total)
    return round(entropy, 6), round(gini, 6)


def _metric_rows(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    return metric_rows(metrics)


def analyze_run(
    run_dir: str | Path,
    *,
    valid_exit_ids: Iterable[str] | None = None,
    snapshot_metrics: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
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
    final_dose_by_person: dict[str, float] = {}
    all_panic: list[float] = []
    urgent_by_step: dict[int, int] = defaultdict(int)
    urgent_field_present = False
    latest_exit_by_person: dict[str, str] = {}

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
            final_dose_by_person[person_id] = dose
        panic = _float(row.get("panic_level"))
        if panic is not None:
            all_panic.append(panic)
        urgent_value = _bool(row.get("danger_urgent"))
        if urgent_value is not None:
            urgent_field_present = True
        if urgent_value is True:
            row_step = _int(row.get("step"))
            if row_step is not None:
                urgent_by_step[row_step] += 1
        # A planned target is not evidence of the exit actually used. Prefer
        # D's entity alias only when it was mapped from B's actual cell exit;
        # otherwise preserve backward-compatible cell-level actual_exit.
        exit_id = row.get("actual_exit_entity") or row.get("actual_exit")
        if exit_id not in (None, ""):
            latest_exit_by_person[person_id] = str(exit_id)

    final_evacuated = sum(1 for row in final_rows if _bool(row.get("evacuated")) is True)
    evacuation_times = list(evacuated_by_person.values())
    total_time = max((_float(row.get("time_s")) or 0.0 for row in final_rows), default=0.0)
    runtime_metrics = snapshot_metrics if isinstance(snapshot_metrics, Mapping) else {}
    raw_metric_usage = runtime_metrics.get("exit_usage")
    metric_usage = (
        {
            str(exit_id): int(count)
            for exit_id, count in raw_metric_usage.items()
            if exit_id not in (None, "") and _int(count) is not None
        }
        if isinstance(raw_metric_usage, Mapping)
        else None
    )
    exit_distribution = Counter(
        metric_usage if metric_usage is not None else latest_exit_by_person.values()
    )
    declared_exits = tuple(dict.fromkeys(str(item) for item in (valid_exit_ids or ()) if str(item)))
    exit_usage = (
        {exit_id: int(exit_distribution.get(exit_id, 0)) for exit_id in declared_exits}
        if declared_exits
        else NA
    )
    entropy_h, gini_g = (
        _exit_balance(list(exit_usage.values()))
        if isinstance(exit_usage, dict)
        else (NA, NA)
    )
    final_runtime_row = final_rows[0] if final_rows else {}
    switch_total = _int(runtime_metrics.get("switch_total"))
    if switch_total is None:
        switch_total = _int(final_runtime_row.get("movement_switch_total"))
    raw_switch_counts = runtime_metrics.get("switch_counts")
    switch_counts = (
        {
            str(person_id): int(count)
            for person_id, count in raw_switch_counts.items()
            if _int(count) is not None
        }
        if isinstance(raw_switch_counts, Mapping)
        else None
    )
    conflict_total = _int(runtime_metrics.get("conflict_total"))
    if conflict_total is None:
        conflict_total = _int(final_runtime_row.get("movement_conflict_total"))
    conflict_lost_total = _int(runtime_metrics.get("conflict_lost_total"))
    if conflict_lost_total is None:
        conflict_lost_total = _int(final_runtime_row.get("movement_conflict_lost_total"))
    active_positions = Counter(
        (x, y)
        for row in final_rows
        if _bool(row.get("evacuated")) is not True
        for x, y in [(_int(row.get("x")), _int(row.get("y")))]
        if x is not None and y is not None
    )
    overlap_cells = sum(1 for count in active_positions.values() if count > 1)
    overlap_by_step = [
        Counter(
            (x, y)
            for row in step_rows
            if _bool(row.get("evacuated")) is not True
            for x, y in [(_int(row.get("x")), _int(row.get("y")))]
            if x is not None and y is not None
        )
        for step_rows in by_step.values()
    ]
    overlap_steps = sum(1 for positions in overlap_by_step if any(count > 1 for count in positions.values()))
    max_overlap_cells = max((sum(count > 1 for count in positions.values()) for positions in overlap_by_step), default=0)
    max_persons_per_cell = max((max(positions.values(), default=0) for positions in overlap_by_step), default=0)
    t90_target = math.ceil(population * 0.9)
    t90 = NA
    for step in steps:
        count = sum(1 for row in by_step[step] if _bool(row.get("evacuated")) is True)
        if count >= t90_target:
            t90 = _float(by_step[step][0].get("time_s")) or 0.0
            break

    mean_evacuation_time = _mean(evacuation_times)
    complete = population > 0 and final_evacuated == population
    total_evacuation_time = max(evacuation_times) if complete else NA
    random_seed = _int(rows[0].get("random_seed"))
    metrics: dict[str, Any] = {
        "run_id": rows[0].get("run_id", output_dir.name),
        "scenario_id": rows[0].get("scenario_id", NA),
        "random_seed": random_seed if random_seed is not None else NA,
        "status": "complete" if complete else "incomplete",
        "total_persons": population,
        "simulation_steps": steps[-1],
        "simulation_time_s": total_time,
        "total_evacuation_time_s": total_evacuation_time,
        "mean_evacuation_time_s": mean_evacuation_time,
        "first_evacuation_time_s": min(evacuation_times) if evacuation_times else NA,
        "overlap_cells": overlap_cells,
        "overlap_steps": overlap_steps,
        "max_overlap_cells": max_overlap_cells,
        "max_persons_per_cell": max_persons_per_cell,
        "exit_utilization": (
            {exit_id: round(count / len(latest_exit_by_person), 6) for exit_id, count in sorted(exit_distribution.items())}
            if latest_exit_by_person
            else NA
        ),
        "exit_utilization_note": (
            "shares calculated from actual_exit_entity when available; otherwise actual_exit"
            if latest_exit_by_person
            else "NA: B did not provide actual_exit"
        ),
        "switch_total": switch_total if switch_total is not None else NA,
        "switch_per_person": (
            round(switch_total / population, 6)
            if switch_total is not None and population > 0
            else NA
        ),
        "switch_counts": switch_counts if switch_counts is not None else NA,
        "switch_counts_note": (
            "B snapshot.metrics switch_counts"
            if switch_counts is not None
            else "NA: snapshot.metrics did not publish per-person exit switch counts"
        ),
        "conflict_total": conflict_total if conflict_total is not None else NA,
        "conflict_lost_total": conflict_lost_total if conflict_lost_total is not None else NA,
        "exit_usage": exit_usage,
        "exit_usage_note": (
            "counts all declared valid exit entities, including zero-use exits"
            if declared_exits
            else "NA: valid exit universe is unavailable"
        ),
        "exit_entropy_h": entropy_h,
        "exit_gini_g": gini_g,
        "exit_balance_note": (
            "K includes all declared valid exit entities, including zero-use exits"
            if declared_exits
            else "NA: valid exit universe is unavailable"
        ),
        "panic_peak": max(all_panic) if all_panic else NA,
        "urgent_peak": max(urgent_by_step.values(), default=0) if urgent_field_present else NA,
        "q95_dose": _q95(final_dose_by_person.values()),
        # Compatibility aliases are retained for Round-1 consumers. They are
        # intentionally absent from the formal registry and batch contract.
        "total_population": population,
        "total_steps": steps[-1],
        "elapsed_time_s": total_time,
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
        "exit_distribution": dict(exit_distribution) if exit_distribution else NA,
        "exit_distribution_note": (
            "NA: B did not provide actual_exit"
            if not exit_distribution
            else "counts actual_exit_entity when available; otherwise actual_exit"
        ),
        "total_evacuation_time_s_note": (
            "all people evacuated"
            if complete
            else "NA: run is not fully evacuated"
        ),
        "mean_evacuation_time_s_note": (
            "mean first evacuated transition time"
            if evacuation_times
            else "NA: no person has evacuated"
        ),
        "avg_risk_note": "NA: B did not provide risk" if not all_risk else "mean logged risk",
        "avg_dose_note": "NA: B did not provide dose" if not all_dose else "mean logged dose",
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
        writer = csv.DictWriter(
            stream,
            fieldnames=["metric_name", "label", "value", "unit", "source", "note"],
        )
        writer.writeheader()
        writer.writerows(_metric_rows(metrics))


def analysis_summary_csv(metrics: Mapping[str, Any]) -> str:
    """Return the standard Week-6 summary without creating another artifact."""

    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=["metric_name", "label", "value", "unit", "source", "note"],
    )
    writer.writeheader()
    writer.writerows(_metric_rows(metrics))
    return stream.getvalue()


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
