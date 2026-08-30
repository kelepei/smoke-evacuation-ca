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


def _metric_rows(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    return metric_rows(metrics)


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
        # A planned target is not evidence of the exit actually used.  Keep
        # this analysis truthful until B emits ``actual_exit``.
        exit_id = row.get("actual_exit")
        if exit_id not in (None, ""):
            latest_exit_by_person[person_id] = str(exit_id)

    final_evacuated = sum(1 for row in final_rows if _bool(row.get("evacuated")) is True)
    evacuation_times = list(evacuated_by_person.values())
    total_time = max((_float(row.get("time_s")) or 0.0 for row in final_rows), default=0.0)
    exit_distribution = Counter(latest_exit_by_person.values())
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
            "shares calculated from logged actual_exit"
            if latest_exit_by_person
            else "NA: B did not provide actual_exit"
        ),
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
            else "counts actual_exit only"
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
