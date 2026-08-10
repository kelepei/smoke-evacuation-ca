"""Create a reproducible D result package from actual runner logs.

Every metric and SVG in the package is computed from the CSV that D recorded
from B snapshots.  Missing B fields remain absent; this module never creates
positions, exit choices, smoke/risk values, or evacuation events.
"""

from __future__ import annotations

import csv
import json
import math
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape


class ResultPackageError(ValueError):
    """Raised when actual D logs cannot produce a trustworthy package."""


def _read_people_log(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ResultPackageError(f"people log does not exist: {path}")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = {"step", "time_s", "person_id", "x", "y", "evacuated"}
            missing = required - set(reader.fieldnames or [])
            if missing:
                raise ResultPackageError(
                    "people log is missing columns: " + ", ".join(sorted(missing))
                )
            rows = list(reader)
    except OSError as exc:
        raise ResultPackageError(f"cannot read people log: {path}") from exc
    if not rows:
        raise ResultPackageError("people log has no recorded people")
    return rows


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    raise ResultPackageError(f"evacuated value is invalid: {value!r}")


def _int(value: str, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ResultPackageError(f"{field} must be an integer") from exc
    if result < 0:
        raise ResultPackageError(f"{field} must be non-negative")
    return result


def _float(value: str, field: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ResultPackageError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ResultPackageError(f"{field} must be finite")
    return result


def calculate_metrics(rows: Iterable[Mapping[str, str]]) -> tuple[dict[str, Any], list[dict[str, Any]], Counter[tuple[int, int]]]:
    """Calculate only evacuation metrics inferable from actual people logs."""

    ordered = sorted(
        list(rows), key=lambda row: (_int(row["step"], "step"), _int(row["person_id"], "person_id"))
    )
    by_step: dict[int, list[Mapping[str, str]]] = defaultdict(list)
    first_evacuation_time: dict[int, float] = {}
    occupancy: Counter[tuple[int, int]] = Counter()
    for row in ordered:
        step = _int(row["step"], "step")
        person_id = _int(row["person_id"], "person_id")
        time_s = _float(row["time_s"], "time_s")
        x, y = _int(row["x"], "x"), _int(row["y"], "y")
        evacuated = _bool(row["evacuated"])
        by_step[step].append(row)
        occupancy[(x, y)] += 1
        if evacuated and person_id not in first_evacuation_time:
            first_evacuation_time[person_id] = time_s

    first_step = min(by_step)
    last_step = max(by_step)
    initial_ids = {_int(row["person_id"], "person_id") for row in by_step[first_step]}
    final_rows = by_step[last_step]
    final_evacuated = {
        _int(row["person_id"], "person_id")
        for row in final_rows
        if _bool(row["evacuated"])
    }
    if {_int(row["person_id"], "person_id") for row in final_rows} != initial_ids:
        raise ResultPackageError("people IDs changed within the recorded run")

    curve: list[dict[str, Any]] = []
    for step in sorted(by_step):
        step_rows = by_step[step]
        ids = {_int(row["person_id"], "person_id") for row in step_rows}
        if ids != initial_ids:
            raise ResultPackageError("people IDs changed within the recorded run")
        time_values = {_float(row["time_s"], "time_s") for row in step_rows}
        if len(time_values) != 1:
            raise ResultPackageError(f"step {step} has inconsistent time_s")
        evacuated_count = sum(_bool(row["evacuated"]) for row in step_rows)
        curve.append({"step": step, "time_s": time_values.pop(), "evacuated": evacuated_count})

    total = len(initial_ids)
    final_time_s = curve[-1]["time_s"]
    total_evacuated = len(final_evacuated)
    t90 = next((point["time_s"] for point in curve if point["evacuated"] / total >= 0.9), None)
    metrics = {
        "initial_people": total,
        "evacuated_people": total_evacuated,
        "evacuation_rate": total_evacuated / total,
        "remaining_people": total - total_evacuated,
        "last_recorded_time_s": final_time_s,
        "all_evacuated_time_s": final_time_s if total_evacuated == total else None,
        "mean_successful_evacuation_time_s": (
            sum(first_evacuation_time.values()) / len(first_evacuation_time)
            if first_evacuation_time
            else None
        ),
        "t90_s": t90,
        "source": "D people_log.csv from B snapshots",
    }
    return metrics, curve, occupancy


def _curve_svg(curve: list[dict[str, Any]]) -> str:
    width, height, padding = 700, 260, 38
    max_time = max(float(point["time_s"]) for point in curve) or 1.0
    max_people = max(int(point["evacuated"]) for point in curve) or 1
    points = " ".join(
        f"{padding + (float(point['time_s']) / max_time) * (width - 2 * padding):.1f},"
        f"{height - padding - (int(point['evacuated']) / max_people) * (height - 2 * padding):.1f}"
        for point in curve
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/><path d="M {padding} {padding} V {height-padding} H {width-padding}" stroke="#334155" fill="none"/>
<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="3"/>
<text x="{padding}" y="22" fill="#17243a" font-family="sans-serif" font-size="15">?????? - ??</text>
<text x="{width-padding-95}" y="{height-12}" fill="#475569" font-family="sans-serif" font-size="12">time (s)</text>
</svg>'''


def _occupancy_svg(occupancy: Counter[tuple[int, int]]) -> str:
    cell, padding = 22, 36
    max_x = max(x for x, _y in occupancy)
    max_y = max(y for _x, y in occupancy)
    max_count = max(occupancy.values())
    width, height = padding * 2 + (max_x + 1) * cell, padding * 2 + (max_y + 1) * cell
    cells = []
    for (x, y), count in sorted(occupancy.items(), key=lambda item: (item[0][1], item[0][0])):
        opacity = 0.15 + 0.85 * count / max_count
        cells.append(f'<rect x="{padding+x*cell}" y="{padding+y*cell}" width="{cell-1}" height="{cell-1}" fill="#dc2626" fill-opacity="{opacity:.3f}"/><title>({x}, {y}): {count}</title>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="white"/><text x="{padding}" y="20" fill="#17243a" font-family="sans-serif" font-size="14">????????</text>{''.join(cells)}</svg>'''


def _write_metrics_csv(path: Path, metrics: Mapping[str, Any]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("metric", "value"))
        writer.writeheader()
        for key, value in metrics.items():
            writer.writerow({"metric": key, "value": value})


def create_result_package(
    *,
    run_directory: str | Path,
    destination_directory: str | Path,
    map_path: str | Path,
    population_path: str | Path,
    config_path: str | Path | None = None,
) -> Path:
    """Zip actual logs and reproducibility inputs; never substitute demo data."""

    run_dir = Path(run_directory)
    destination = Path(destination_directory)
    people_log = run_dir / "people_log.csv"
    event_log = run_dir / "event_log.csv"
    rows = _read_people_log(people_log)
    metrics, curve, occupancy = calculate_metrics(rows)
    package_name = f"{run_dir.name}_result.zip"
    destination.mkdir(parents=True, exist_ok=True)
    package_path = destination / package_name
    if package_path.exists():
        raise FileExistsError(f"refusing to overwrite result package: {package_path}")

    inputs = [(Path(map_path), "inputs/map" + Path(map_path).suffix), (Path(population_path), "inputs/population.json")]
    if config_path is not None:
        inputs.append((Path(config_path), "inputs/config" + Path(config_path).suffix))
    for source, _target in inputs:
        if not source.is_file():
            raise ResultPackageError(f"reproducibility input does not exist: {source}")

    metadata = {
        "run_id": run_dir.name,
        "metrics": metrics,
        "input_policy": "A positions and C people/relations are copied as provided",
        "missing_values_are_not_inferred": True,
    }
    with tempfile.TemporaryDirectory(prefix="d_result_package_") as temporary:
        scratch = Path(temporary)
        _write_metrics_csv(scratch / "metrics.csv", metrics)
        (scratch / "evacuation_curve.svg").write_text(_curve_svg(curve), encoding="utf-8")
        (scratch / "occupancy_heatmap.svg").write_text(_occupancy_svg(occupancy), encoding="utf-8")
        (scratch / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(package_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(people_log, "people_log.csv")
            if event_log.is_file():
                archive.write(event_log, "event_log.csv")
            for generated in scratch.iterdir():
                archive.write(generated, generated.name)
            for source, target in inputs:
                archive.write(source, target)
    return package_path
