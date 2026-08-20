"""Run a reproducible D-side audit against the real A+B+C integration path."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping

from experiments.integrated_runner import create_integrated_runner
from experiments.week6_analysis import analyze_run, write_analysis
from visualization.integrated_runtime import save_snapshot_png


BLOCKED_TYPES = {"wall", "obstacle"}


def _cell_value(value: Any) -> str:
    return str(getattr(value, "value", value)).strip().lower()


def _event_count(path: Path, event_type: str) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8", newline="") as handle:
        return sum(
            1 for row in csv.DictReader(handle) if row.get("event_type") == event_type
        )


def run_acceptance_audit(
    *,
    map_path: str | Path,
    population_path: str | Path,
    yaml_path: str | Path | None,
    output_root: str | Path,
    run_id: str,
    max_steps: int = 500,
) -> tuple[dict[str, Any], Path]:
    runner = create_integrated_runner(
        map_path=map_path,
        population_path=population_path,
        yaml_path=yaml_path,
        output_root=output_root,
        run_id=run_id,
        max_steps=max_steps,
    )
    started = perf_counter()
    snapshot = runner.initialize()
    initial_positions = {
        person["person_id"]: (person["x"], person["y"])
        for person in snapshot["people"]
    }
    moved_ids: set[int] = set()
    blocked_violations: list[dict[str, Any]] = []
    overlap_step_count = 0
    max_overlap_cells = 0
    first_overlap: dict[str, Any] | None = None
    smoke_min = 0.0
    smoke_max = 0.0
    smoke_nonzero_states = 0
    smoke_violation_step: int | None = None
    step_durations: list[float] = []

    grid = snapshot["grid"]
    blocked = {
        (x, y)
        for y, row in enumerate(grid["cell_type"])
        for x, cell_type in enumerate(row)
        if _cell_value(cell_type) in BLOCKED_TYPES
    }

    def inspect(current: Mapping[str, Any]) -> None:
        nonlocal overlap_step_count, max_overlap_cells, first_overlap
        nonlocal smoke_min, smoke_max, smoke_nonzero_states, smoke_violation_step
        step = int(current["step"])
        active = [person for person in current["people"] if not person["evacuated"]]
        counts = Counter((int(person["x"]), int(person["y"])) for person in active)
        overlaps = [
            {"x": x, "y": y, "count": count}
            for (x, y), count in counts.items()
            if count > 1
        ]
        if overlaps:
            overlap_step_count += 1
            max_overlap_cells = max(max_overlap_cells, len(overlaps))
            if first_overlap is None:
                first_overlap = {"step": step, "cells": overlaps[:10]}

        for person in active:
            person_id = int(person["person_id"])
            position = (int(person["x"]), int(person["y"]))
            if position != initial_positions[person_id]:
                moved_ids.add(person_id)
            if position in blocked and len(blocked_violations) < 20:
                blocked_violations.append(
                    {"step": step, "person_id": person_id, "x": position[0], "y": position[1]}
                )

        smoke = current.get("fields", {}).get("smoke_field", [])
        values = [float(value) for row in smoke for value in row]
        if values:
            smoke_min = min(smoke_min, min(values))
            smoke_max = max(smoke_max, max(values))
            if max(values) > 0.0:
                smoke_nonzero_states += 1
            if smoke_violation_step is None and (
                min(values) < 0.0 or max(values) > 10.0
            ):
                smoke_violation_step = step

    try:
        inspect(snapshot)
        while not runner.finished:
            step_started = perf_counter()
            snapshot = runner.step()
            step_durations.append(perf_counter() - step_started)
            inspect(snapshot)
    finally:
        runner.close()

    output_dir = Path(output_root) / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    save_snapshot_png(dict(snapshot), output_dir / "final_frame.png")
    metrics = analyze_run(output_dir)
    write_analysis(metrics, output_dir)
    evacuated_count = sum(1 for person in snapshot["people"] if person["evacuated"])
    event_count = _event_count(output_dir / "event_log.csv", "evac_success")
    average_step_ms = (
        sum(step_durations) / len(step_durations) * 1000.0
        if step_durations
        else 0.0
    )
    checks = {
        "map_and_population_loaded": len(snapshot["people"]) > 0,
        "all_people_moved": len(moved_ids) == len(snapshot["people"]),
        "no_wall_or_obstacle_occupancy": not blocked_violations,
        "no_active_person_overlap": overlap_step_count == 0,
        "smoke_values_within_0_10": smoke_violation_step is None,
        "people_log_present": (output_dir / "people_log.csv").is_file(),
        "event_log_present": (output_dir / "event_log.csv").is_file(),
        "evacuation_events_match": event_count == evacuated_count,
        "finished_before_max_steps": evacuated_count == len(snapshot["people"]),
    }
    report = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "run_id": run_id,
        "inputs": {
            "map": str(Path(map_path)),
            "population": str(Path(population_path)),
            "yaml": None if yaml_path is None else str(Path(yaml_path)),
        },
        "runtime": {
            "elapsed_s": round(perf_counter() - started, 4),
            "average_step_ms": round(average_step_ms, 3),
            "steps": int(snapshot["step"]),
            "population": len(snapshot["people"]),
            "evacuated": evacuated_count,
        },
        "checks": checks,
        "observations": {
            "moved_people": len(moved_ids),
            "blocked_violations": blocked_violations,
            "overlap_step_count": overlap_step_count,
            "max_overlap_cells_in_one_step": max_overlap_cells,
            "first_overlap": first_overlap,
            "smoke_min": smoke_min,
            "smoke_max": smoke_max,
            "smoke_field_exercised": smoke_nonzero_states > 0,
            "smoke_nonzero_states": smoke_nonzero_states,
            "smoke_contract_violation_step": smoke_violation_step,
            "evacuation_event_count": event_count,
        },
        "ownership_notes": {
            "D": "CSV/event consistency, rendering, timing, and report generation",
            "B": "movement conflicts, wall avoidance, evacuation, and smoke range",
            "C": "behavior, information, relation, and strategy fields",
        },
    }
    (output_dir / "acceptance_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report, output_dir


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit the real A+B+C runtime fairly.")
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--population", required=True, type=Path)
    parser.add_argument("--yaml", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("outputs") / "experiments")
    parser.add_argument("--run-id", default="d_acceptance_audit")
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report, output_dir = run_acceptance_audit(
        map_path=args.map,
        population_path=args.population,
        yaml_path=args.yaml,
        output_root=args.output_root,
        run_id=args.run_id,
        max_steps=args.max_steps,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"acceptance output: {output_dir}")
    if args.strict and report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
