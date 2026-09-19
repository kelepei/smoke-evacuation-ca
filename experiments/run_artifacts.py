"""Write D-owned, reproducible artifacts from one normalized runtime snapshot.

The helper is deliberately shared by the web bridge and the command-line
integration runner so a real A+B+C run has the same output contract through
either entry point.  It never fills in missing upstream measurements.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Mapping

from experiments.crowd_metrics import resolve_analysis_contract, write_trajectory_kinematics, write_velocity_vector_field
from experiments.guidance_interface import unavailable_guidance, write_guidance_artifacts
from experiments.week6_analysis import analyze_run


def _flatten_numeric_field(field: Any) -> list[float]:
    if not isinstance(field, list):
        return []
    values: list[float] = []
    for row in field:
        if not isinstance(row, list):
            continue
        for value in row:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return values


def snapshot_metrics(snapshot: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    """Calculate only metrics that are available in the current snapshot/logs."""

    if (output_dir / "people_log.csv").is_file():
        try:
            return analyze_run(output_dir)
        except (OSError, ValueError):
            # A caller may be writing an isolated snapshot without a complete
            # D log stream. Snapshot values below remain real, not fabricated.
            pass

    people = snapshot.get("people", [])
    people_list = people if isinstance(people, list) else []
    total = len(people_list)
    evacuated = sum(
        1
        for person in people_list
        if isinstance(person, Mapping) and person.get("evacuated") is True
    )
    fields = snapshot.get("fields")
    smoke_values = _flatten_numeric_field(
        fields.get("smoke_field") if isinstance(fields, Mapping) else []
    )

    evac_times: list[float] = []
    event_path = output_dir / "event_log.csv"
    if event_path.is_file():
        with event_path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("event_type") != "evac_success":
                    continue
                try:
                    evac_times.append(float(row.get("time_s", "")))
                except ValueError:
                    continue

    return {
        "total_persons": total,
        "simulation_steps": snapshot.get("step", "NA"),
        "simulation_time_s": snapshot.get("time_s", "NA"),
        "evacuated_count": evacuated,
        "remaining_count": max(0, total - evacuated),
        "evacuation_rate": (evacuated / total) if total else "NA",
        "first_evacuation_time_s": min(evac_times) if evac_times else "NA",
        "total_evacuation_time_s": "NA",
        "max_smoke": max(smoke_values) if smoke_values else "NA",
        "avg_smoke": (sum(smoke_values) / len(smoke_values)) if smoke_values else "NA",
        "avg_dose": "NA",
        "avg_risk": "NA",
        "exit_utilization": "NA",
        "overlap_cells": "NA",
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_summary_csv(path: Path, row: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def write_run_artifacts(
    snapshot: Mapping[str, Any],
    output_dir: str | Path,
    *,
    input_files: Mapping[str, str | Path],
    save_frame: bool = True,
) -> dict[str, Any]:
    """Write config, metrics, and optionally the rendered final frame.

    ``people_log.csv`` and ``event_log.csv`` remain owned by ``CsvExperimentLogger``.
    They must already exist when this function is called.
    """

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    config_used = {
        "run_id": snapshot.get("run_id"),
        "scenario_id": snapshot.get("scenario_id"),
        "schema_version": snapshot.get("schema_version"),
        "random_seed": snapshot.get("random_seed"),
        "time_step_s": snapshot.get("time_step"),
        "analysis_contract": snapshot.get("analysis_contract", {}),
        "grid": {
            "width": snapshot.get("grid", {}).get("width")
            if isinstance(snapshot.get("grid"), Mapping)
            else None,
            "height": snapshot.get("grid", {}).get("height")
            if isinstance(snapshot.get("grid"), Mapping)
            else None,
        },
        "exit_entities": snapshot.get("exit_entities", []),
        "input_files": {key: str(path) for key, path in input_files.items()},
        "runtime_contract": "A Grid + C population/config + B EvacEngine through D adapters",
        "missing_upstream_fields": "CSV logger leaves unprovided upstream fields empty; D does not fabricate values.",
    }
    people_log_path = destination / "people_log.csv"
    if people_log_path.is_file():
        grid = snapshot.get("grid")
        kinematics = write_trajectory_kinematics(
            people_log_path=people_log_path,
            output_path=destination / "trajectory_kinematics.csv",
            analysis_contract=(snapshot.get("analysis_contract") if isinstance(snapshot.get("analysis_contract"), Mapping) else None),
            grid=grid if isinstance(grid, Mapping) else None,
        )
        raw_contract = snapshot.get("analysis_contract")
        analysis_contract = raw_contract if isinstance(raw_contract, Mapping) and isinstance(raw_contract.get("sampling_window_s"), Mapping) else resolve_analysis_contract()
        velocity_field = write_velocity_vector_field(
            kinematics_path=destination / "trajectory_kinematics.csv",
            output_path=destination / "velocity_vector_field.json",
            csv_output_path=destination / "velocity_vector_field.csv",
            analysis_contract=analysis_contract,
        )
    else:
        kinematics = {"path": "trajectory_kinematics.csv", "status": "unavailable", "reason": "people_log.csv is not present"}
        velocity_field = {"json_path": "velocity_vector_field.json", "csv_path": "velocity_vector_field.csv", "status": "unavailable", "reason": "trajectory_kinematics.csv is unavailable"}
    config_used["trajectory_kinematics"] = kinematics
    config_used["velocity_vector_field"] = velocity_field
    _write_json(destination / "config_used.json", config_used)
    metrics = snapshot_metrics(snapshot, destination)
    _write_json(destination / "metrics.json", metrics)
    _write_summary_csv(destination / "metrics_summary.csv", metrics)
    # Persist the exact live annotation returned through the Web API.  This
    # must not calculate another recommendation from a later/alternate state.
    guidance = snapshot.get("guidance")
    if not isinstance(guidance, Mapping):
        guidance = unavailable_guidance(
            snapshot, "normalized runtime snapshot did not contain guidance"
        )
    write_guidance_artifacts(guidance, destination)
    if save_frame:
        # Import lazily so non-rendering callers do not require Matplotlib.
        from visualization.integrated_runtime import save_snapshot_png

        save_snapshot_png(dict(snapshot), destination / "final_frame.png")
    return metrics
