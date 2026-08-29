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
        "total_steps": snapshot.get("step", "NA"),
        "total_time_s": snapshot.get("time_s", "NA"),
        "evacuated_count": evacuated,
        "remaining_count": max(0, total - evacuated),
        "evacuation_rate": (evacuated / total) if total else "NA",
        "first_evac_time_s": min(evac_times) if evac_times else "NA",
        "last_evac_time_s": max(evac_times) if evac_times else "NA",
        "max_smoke": max(smoke_values) if smoke_values else "NA",
        "avg_smoke": (sum(smoke_values) / len(smoke_values)) if smoke_values else "NA",
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
        "input_files": {key: str(path) for key, path in input_files.items()},
        "runtime_contract": "A Grid + C population/config + B EvacEngine through D adapters",
        "missing_upstream_fields": "CSV logger leaves unprovided upstream fields empty; D does not fabricate values.",
    }
    _write_json(destination / "config_used.json", config_used)
    metrics = snapshot_metrics(snapshot, destination)
    _write_json(destination / "metrics.json", metrics)
    _write_summary_csv(destination / "metrics_summary.csv", metrics)
    if save_frame:
        # Import lazily so non-rendering callers do not require Matplotlib.
        from visualization.integrated_runtime import save_snapshot_png

        save_snapshot_png(dict(snapshot), destination / "final_frame.png")
    return metrics
