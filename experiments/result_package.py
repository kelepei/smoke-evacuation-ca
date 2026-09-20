"""Build a downloadable, reproducible result package for a D runtime run.

The package is deliberately derived from the CSV logs and the latest
normalized snapshot.  It does not infer upstream values that B or C have not
provided.  This keeps the web UI useful for real A+B+C runs without turning
the browser into a second simulation implementation.
"""

from __future__ import annotations

import csv
import io
import json
import math
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.academic_crowd import ACADEMIC_CROWD_FIELDS, academic_crowd_fields
from experiments.congestion_level import CONGESTION_LEVEL_FIELDS, congestion_level_field
from experiments.crowd_metrics import DEFAULT_SAMPLING_WINDOW_S, KINEMATICS_FIELDS, VELOCITY_FIELD_FIELDS, trajectory_kinematics, velocity_vector_field
from experiments.metrics_registry import metric_rows
from experiments.week6_analysis import analysis_summary_csv, analyze_run


class ResultPackageError(ValueError):
    """Raised when a D result package cannot be built safely."""


@dataclass(frozen=True)
class ResultPackage:
    """An in-memory archive that can be returned by the local web service."""

    filename: str
    content: bytes
    summary: dict[str, Any]


def _parse_float(value: Any, *, default: float | None = None) -> float | None:
    try:
        if value in (None, ""):
            return default
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (TypeError, ValueError):
        return default


def _parse_int(value: Any, *, default: int | None = None) -> int | None:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _is_true(value: Any) -> bool:
    return str(value).strip().lower() == "true"


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ResultPackageError(f"missing runtime log: {path.name}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    rows = list(rows)
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(
        stream,
        fieldnames=["metric_name", "label", "value", "unit", "source", "note"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return stream.getvalue()


_PERSON_ID_MAPPING_FIELDS = ("source_person_id", "runtime_person_id")


def _person_id_mapping_csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=_PERSON_ID_MAPPING_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _person_id_mapping(
    people_rows: Iterable[Mapping[str, Any]], input_files: Mapping[str, Path]
) -> tuple[list[dict[str, int]], dict[str, Any]]:
    """Join source and runtime IDs using their real initial positions.

    D does not infer an offset. A mapping is emitted only when the positioned
    population and the initial runtime frame form an exact, one-to-one join.
    """
    artifact = "person_id_mapping.csv"
    base_metadata: dict[str, Any] = {
        "artifact": artifact,
        "source_id_convention": "unavailable",
        "runtime_id_convention": "people_log.csv person_id",
        "derivation": "unique initial x/y position join; no numeric ID offset is inferred",
    }
    population_path = input_files.get("population")
    if population_path is None or not population_path.is_file():
        return [], {**base_metadata, "status": "unavailable", "reason": "positioned population input is unavailable"}
    try:
        payload = json.loads(population_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return [], {**base_metadata, "status": "unavailable", "reason": "positioned population input is not valid JSON"}
    raw_people = payload.get("persons") if isinstance(payload, Mapping) else None
    if not isinstance(raw_people, list) or not raw_people:
        return [], {**base_metadata, "status": "unavailable", "reason": "positioned population input has no persons[]"}

    source_by_position: dict[tuple[int, int], int] = {}
    source_ids: list[int] = []
    source_field: str | None = None
    for raw in raw_people:
        if not isinstance(raw, Mapping):
            return [], {**base_metadata, "status": "unavailable", "reason": "source population contains a non-object person"}
        field = next((name for name in ("source_person_id", "person_id", "id") if name in raw), None)
        source_id = _parse_int(raw.get(field)) if field else None
        x, y = _parse_int(raw.get("x")), _parse_int(raw.get("y"))
        if source_id is None or x is None or y is None:
            return [], {**base_metadata, "status": "unavailable", "reason": "source population needs integer ID and x/y"}
        if source_field is None:
            source_field = field
        elif source_field != field:
            return [], {**base_metadata, "status": "unavailable", "reason": "source population uses inconsistent ID fields"}
        if (x, y) in source_by_position or source_id in source_ids:
            return [], {**base_metadata, "status": "unavailable", "reason": "source population IDs or initial positions are not unique"}
        source_by_position[(x, y)] = source_id
        source_ids.append(source_id)

    parsed_runtime = [
        (_parse_int(row.get("step")), _parse_int(row.get("person_id")), _parse_int(row.get("x")), _parse_int(row.get("y")))
        for row in people_rows
    ]
    steps = [step for step, person_id, x, y in parsed_runtime if step is not None and person_id is not None and x is not None and y is not None]
    if not steps:
        return [], {**base_metadata, "status": "unavailable", "reason": "people_log.csv has no valid runtime frame"}
    initial_step = min(steps)
    runtime_by_position: dict[tuple[int, int], int] = {}
    for step, person_id, x, y in parsed_runtime:
        if step != initial_step or person_id is None or x is None or y is None:
            continue
        if (x, y) in runtime_by_position or person_id in runtime_by_position.values():
            return [], {**base_metadata, "status": "unavailable", "reason": "runtime initial IDs or positions are not unique"}
        runtime_by_position[(x, y)] = person_id
    if set(source_by_position) != set(runtime_by_position):
        return [], {**base_metadata, "status": "unavailable", "reason": "source and runtime initial positions do not form an exact join"}

    rows = [
        {"source_person_id": source_id, "runtime_person_id": runtime_by_position[position]}
        for position, source_id in sorted(source_by_position.items(), key=lambda item: item[1])
    ]
    source_convention = "zero_based" if min(source_ids) == 0 else "one_based" if min(source_ids) == 1 else "explicit_source_ids"
    return rows, {
        **base_metadata,
        "status": "verified",
        "source_id_convention": f"{source_convention} {source_field}",
        "runtime_id_convention": "people_log.csv person_id at initial runtime step",
        "initial_runtime_step": initial_step,
        "mapped_person_count": len(rows),
    }


def _trajectory_csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=KINEMATICS_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _velocity_field_csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=VELOCITY_FIELD_FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def _congestion_level_csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CONGESTION_LEVEL_FIELDS)
    writer.writeheader(); writer.writerows(rows)
    return stream.getvalue()

def _academic_crowd_csv_text(rows: Iterable[Mapping[str, Any]]) -> str:
    stream=io.StringIO(newline=""); writer=csv.DictWriter(stream, fieldnames=ACADEMIC_CROWD_FIELDS); writer.writeheader(); writer.writerows(rows); return stream.getvalue()


def _svg_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _curve_svg(points: list[tuple[float, int]], population: int) -> str:
    width, height, left, right, top, bottom = 760, 380, 72, 28, 35, 48
    chart_w, chart_h = width - left - right, height - top - bottom
    max_time = max((point[0] for point in points), default=1.0)
    max_time = max(1.0, max_time)
    maximum = max(1, population)
    max_count = max((point[1] for point in points), default=0)
    if points:
        polyline = " ".join(
            f"{left + chart_w * time_s / max_time:.2f},{top + chart_h * (1 - count / maximum):.2f}"
            for time_s, count in points
        )
    else:
        polyline = ""
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{left}" y="22" font-family="Arial, Microsoft YaHei" font-size="17" fill="#18243a">疏散人数—时间曲线（真实日志）</text>
  <rect x="{left}" y="{top}" width="{chart_w}" height="{chart_h}" fill="#fbfdff" stroke="#cbd5e1"/>
  <line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#556070"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#556070"/>
  <line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#93c5fd" stroke-dasharray="4 4"/>
  <text x="{left - 20}" y="{top + chart_h + 4}" font-family="Arial" font-size="12" fill="#556070">0</text>
  <text x="{left - 36}" y="{top + 4}" font-family="Arial" font-size="12" fill="#556070">{population}</text>
  <text x="{left + chart_w - 58}" y="{top + chart_h + 28}" font-family="Arial" font-size="12" fill="#556070">时间 / s</text>
  <text x="17" y="{top + chart_h / 2}" transform="rotate(-90 17 {top + chart_h / 2})" text-anchor="middle" font-family="Arial, Microsoft YaHei" font-size="12" fill="#556070">已疏散人数</text>
  <polyline points="{polyline}" fill="none" stroke="#2764e7" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
  <text x="{left + chart_w - 98}" y="{top + 18}" font-family="Arial" font-size="12" fill="#2764e7">{max_time:g}s</text>
  {f'<text x="{left + chart_w / 2}" y="{top + chart_h / 2}" text-anchor="middle" font-family="Arial, Microsoft YaHei" font-size="13" fill="#64748b">尚无撤离事件</text>' if max_count == 0 else ''}
</svg>'''


_OCCUPANCY_COLOR_BANDS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (255, 255, 255)),
    (2.0, (254, 202, 202)),
    (5.0, (252, 165, 165)),
    (10.0, (239, 68, 68)),
    (25.0, (220, 38, 38)),
    (50.0, (185, 28, 28)),
    (99.999999, (127, 29, 29)),
    (float("inf"), (69, 10, 10)),
)


def _occupancy_display_color(value: int | float) -> str:
    """Return the fixed, display-only colour for a raw occupancy count.

    The scale is deliberately independent of a run's observed maximum so that
    the same count has the same visual meaning across result packages.
    """
    count = max(0.0, float(value))
    for upper_value, color in _OCCUPANCY_COLOR_BANDS:
        if count <= upper_value:
            return f"rgb({color[0]},{color[1]},{color[2]})"
    raise AssertionError("fixed occupancy colour scale must cover every count")


def _heatmap_svg(occupancy: list[list[int]]) -> str:
    height = len(occupancy)
    width = len(occupancy[0]) if occupancy else 0
    if width <= 0 or height <= 0:
        return "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"120\"><text x=\"12\" y=\"30\">无可绘制网格数据</text></svg>"
    cell = max(5, min(24, int(620 / max(width, height))))
    margin, title_h = 44, 76
    svg_w, svg_h = margin * 2 + width * cell, title_h + margin + height * cell
    maximum = max((value for row in occupancy for value in row), default=0)
    cells: list[str] = []
    for y, row in enumerate(occupancy):
        for x, value in enumerate(row):
            # This affects only SVG colour; ``occupancy`` remains raw counts.
            cells.append(
                f'<rect x="{margin + x * cell}" y="{title_h + y * cell}" width="{cell}" height="{cell}" fill="{_occupancy_display_color(value)}" stroke="#e5e7eb" stroke-width="0.4"/>'
            )
    legend_values = (0, 1, 5, 10, 25, 50, 100)
    legend = "".join(
        f'<rect x="{margin + index * 52}" y="50" width="13" height="10" fill="{_occupancy_display_color(value)}" stroke="#cbd5e1" stroke-width="0.4"/>'
        f'<text x="{margin + index * 52 + 17}" y="59" font-family="Arial" font-size="10" fill="#556070">{"100+" if value == 100 else value}</text>'
        for index, value in enumerate(legend_values)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{margin}" y="24" font-family="Arial, Microsoft YaHei" font-size="17" fill="#18243a">累计占用热力图（真实日志）</text>
  <text x="{margin}" y="40" font-family="Arial, Microsoft YaHei" font-size="11" fill="#556070">颜色采用固定累计次数色标，便于不同实验直接比较；原始累计次数不变；最大值 {maximum}</text>
  {legend}
  {''.join(cells)}
</svg>'''


def _log_visual_data(
    people_rows: list[dict[str, str]],
    *,
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    observed_positions: list[tuple[int, int]] = []
    for row in people_rows:
        step = _parse_int(row.get("step"))
        if step is not None:
            grouped[step].append(row)
        x, y = _parse_int(row.get("x")), _parse_int(row.get("y"))
        if x is not None and y is not None and x >= 0 and y >= 0:
            observed_positions.append((x, y))
    if not grouped:
        raise ResultPackageError("people_log.csv contains no step rows")
    if width is None:
        width = max((x for x, _ in observed_positions), default=-1) + 1
    if height is None:
        height = max((y for _, y in observed_positions), default=-1) + 1
    if width <= 0 or height <= 0:
        raise ResultPackageError("people_log.csv contains no valid grid coordinates")

    initial_population = len(grouped[min(grouped)])
    curve: list[tuple[float, int]] = []
    occupancy = [[0 for _ in range(width)] for _ in range(height)]
    trajectories: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for step in sorted(grouped):
        rows = grouped[step]
        evacuated_count = 0
        time_s = _parse_float(rows[0].get("time_s"), default=0.0) or 0.0
        for row in rows:
            evacuated = _is_true(row.get("evacuated"))
            if evacuated:
                evacuated_count += 1
                continue
            x, y = _parse_int(row.get("x")), _parse_int(row.get("y"))
            if x is not None and y is not None and 0 <= x < width and 0 <= y < height:
                occupancy[y][x] += 1
                person_id = str(row.get("person_id", ""))
                trajectories[person_id].append(
                    {"x": x, "y": y, "step": step, "time_s": time_s}
                )
        curve.append((time_s, evacuated_count))
    return {
        "curve": curve,
        "occupancy": occupancy,
        "trajectories": dict(trajectories),
        "grid": {"width": width, "height": height},
        "initial_population": initial_population,
    }


def _snapshot_grid_dimensions(
    final_snapshot: Mapping[str, Any] | None,
) -> tuple[int | None, int | None]:
    grid = final_snapshot.get("grid") if isinstance(final_snapshot, Mapping) else None
    if not isinstance(grid, Mapping):
        return None, None
    width = _parse_int(grid.get("width"))
    height = _parse_int(grid.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None, None
    return width, height


def build_runtime_analysis(
    *,
    output_dir: str | Path,
    final_snapshot: Mapping[str, Any] | None = None,
    include_figures: bool = False,
    include_layers: bool = False,
) -> dict[str, Any]:
    """Read one completed D log stream into metrics and optional SVG figures.

    This is shared by the browser and ZIP exporter so both show the same
    CSV-derived results instead of maintaining separate analysis logic.
    """

    base = Path(output_dir)
    people_rows = _read_csv(base / "people_log.csv")
    analysis_contract = final_snapshot.get("analysis_contract", {}) if isinstance(final_snapshot, Mapping) else {}
    snapshot_grid = final_snapshot.get("grid", {}) if isinstance(final_snapshot, Mapping) else {}
    if not isinstance(analysis_contract, Mapping): analysis_contract = {}
    if not isinstance(snapshot_grid, Mapping): snapshot_grid = {}
    width, height = _snapshot_grid_dimensions(final_snapshot)
    visual = _log_visual_data(
        people_rows,
        width=width,
        height=height,
    )
    week6_metrics = analyze_run(base)
    exit_entities = final_snapshot.get("exit_entities", []) if isinstance(final_snapshot, Mapping) else []
    entity_level = isinstance(exit_entities, list) and len(exit_entities) > 0
    summary = {
        "initial_population": week6_metrics["total_persons"],
        "evacuated_count": week6_metrics["evacuated_count"],
        "remaining_count": week6_metrics["remaining_count"],
        "evacuation_rate": week6_metrics["evacuation_rate"],
        "last_successful_exit_time": (
            None
            if week6_metrics["last_evac_time_s"] == "NA"
            else week6_metrics["last_evac_time_s"]
        ),
        "completed": week6_metrics["status"] == "complete",
        "last_recorded_time_s": week6_metrics["simulation_time_s"],
        "total_steps": week6_metrics["simulation_steps"],
    }
    result: dict[str, Any] = {
        "metrics": metric_rows(week6_metrics),
        "summary": summary,
        "week6_metrics": week6_metrics,
        "academic_crowd_fields": academic_crowd_fields(people_rows, grid=snapshot_grid, analysis_contract=analysis_contract),
        # A live run decides this once from its normalized topology, rather
        # than switching display representation as partial snapshots arrive.
        "exit_utilization_contract": {
            "representation": "entity" if entity_level else "legacy_cell",
            "source": "actual_exit_entity" if entity_level else "actual_exit",
        },
    }
    if include_figures:
        result["evacuation_curve_svg"] = _curve_svg(
            visual["curve"], summary["initial_population"]
        )
        result["occupancy_heatmap_svg"] = _heatmap_svg(visual["occupancy"])
    if include_layers:
        result["layers"] = {
            "grid": visual["grid"],
            "cumulative_occupancy": visual["occupancy"],
            "trajectories": visual["trajectories"],
            "source": "people_log.csv",
        }
    return result


def build_result_package(
    *,
    output_dir: str | Path,
    final_snapshot: Mapping[str, Any],
    input_files: Mapping[str, Path],
    max_steps: int,
) -> ResultPackage:
    """Create a ZIP result package from one active D runtime session."""

    run_id = str(final_snapshot.get("run_id") or "d_runtime")
    scenario_id = str(final_snapshot.get("scenario_id") or "unnamed_scenario")
    base = Path(output_dir)
    people_path = base / "people_log.csv"
    event_path = base / "event_log.csv"
    _read_csv(event_path)
    people_rows = _read_csv(people_path)
    person_id_mapping, person_id_mapping_metadata = _person_id_mapping(people_rows, input_files)
    analysis = build_runtime_analysis(
        output_dir=base,
        final_snapshot=final_snapshot,
        include_figures=True,
    )
    metrics = analysis["metrics"]
    summary = analysis["summary"]
    analysis_contract = final_snapshot.get("analysis_contract")
    if not isinstance(analysis_contract, Mapping):
        analysis_contract = {}
    snapshot_grid = final_snapshot.get("grid")
    kinematics = trajectory_kinematics(
        people_rows,
        physical_scale=analysis_contract.get("physical_scale"),
        grid=snapshot_grid if isinstance(snapshot_grid, Mapping) else None,
    )
    sampling = analysis_contract.get("sampling_window_s")
    velocity_field = velocity_vector_field(
        kinematics,
        sampling_window_s=sampling.get("value") if isinstance(sampling, Mapping) else DEFAULT_SAMPLING_WINDOW_S,
        physical_scale=analysis_contract.get("physical_scale"),
    )
    congestion_level = congestion_level_field(kinematics, grid=snapshot_grid if isinstance(snapshot_grid, Mapping) else {}, analysis_contract=analysis_contract)
    academic_crowd = academic_crowd_fields(people_rows, grid=snapshot_grid if isinstance(snapshot_grid, Mapping) else {}, analysis_contract=analysis_contract, kinematic_rows=kinematics)

    metadata = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "schema_version": final_snapshot.get("schema_version"),
        "random_seed": final_snapshot.get("random_seed"),
        "time_step_s": final_snapshot.get("time_step"),
        "analysis_contract": analysis_contract,
        "last_step": final_snapshot.get("step"),
        "max_steps": max_steps,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_source": "A map + C population + B CA via D integration boundary",
        "person_id_mapping": person_id_mapping_metadata,
        "limitations": {
            "missing_upstream_fields_remain_empty": ["heading", "risk", "dose", "conflict", "exit_switch"],
            "exit_utilization": "calculated only when B logs actual_exit",
            "strategy_controls": "not connected to B movement decisions in this package",
        },
        "summary": summary,
    }
    configuration = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "random_seed": final_snapshot.get("random_seed"),
        "time_step_s": final_snapshot.get("time_step"),
        "analysis_contract": analysis_contract,
        "max_steps": max_steps,
        "input_files": {key: path.name for key, path in input_files.items() if path.is_file()},
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        prefix = f"{run_id}/"
        bundle.writestr(prefix + "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "config.json", json.dumps(configuration, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "metrics.csv", _csv_text(metrics))
        bundle.writestr(prefix + "evacuation_curve.svg", analysis["evacuation_curve_svg"])
        bundle.writestr(prefix + "occupancy_heatmap.svg", analysis["occupancy_heatmap_svg"])
        bundle.writestr(prefix + "week6_metrics.json", json.dumps(analysis["week6_metrics"], ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "week6_metrics_summary.csv", analysis_summary_csv(analysis["week6_metrics"]))
        bundle.writestr(prefix + "trajectory_kinematics.csv", _trajectory_csv_text(kinematics))
        bundle.writestr(prefix + "velocity_vector_field.json", json.dumps(velocity_field, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "velocity_vector_field.csv", _velocity_field_csv_text(velocity_field["records"]))
        bundle.writestr(prefix + "congestion_level_field.json", json.dumps(congestion_level, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "congestion_level_field.csv", _congestion_level_csv_text(congestion_level["records"]))
        bundle.writestr(prefix + "academic_crowd_fields.json", json.dumps(academic_crowd, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "academic_crowd_fields.csv", _academic_crowd_csv_text(academic_crowd["records"]))
        bundle.writestr(prefix + "person_id_mapping.csv", _person_id_mapping_csv_text(person_id_mapping))
        bundle.write(people_path, prefix + "people_log.csv")
        bundle.write(event_path, prefix + "event_log.csv")
        for key, source in input_files.items():
            if source.is_file():
                bundle.write(source, prefix + "inputs" + "/" + source.name)

    safe_name = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in run_id)
    return ResultPackage(
        filename=f"{safe_name}_result_package.zip",
        content=archive.getvalue(),
        summary=summary,
    )
