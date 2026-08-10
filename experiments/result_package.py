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
        fieldnames=["metric_name", "value", "unit", "note"],
    )
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return stream.getvalue()


def _svg_escape(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _curve_svg(points: list[tuple[float, int]], population: int) -> str:
    width, height, left, right, top, bottom = 760, 380, 62, 28, 35, 48
    chart_w, chart_h = width - left - right, height - top - bottom
    max_time = max((point[0] for point in points), default=1.0)
    max_time = max(1.0, max_time)
    maximum = max(1, population)
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
  <line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" y2="{top + chart_h}" stroke="#556070"/>
  <line x1="{left}" y1="{top}" x2="{left}" y2="{top + chart_h}" stroke="#556070"/>
  <text x="{left - 20}" y="{top + chart_h + 4}" font-family="Arial" font-size="12" fill="#556070">0</text>
  <text x="{left - 36}" y="{top + 4}" font-family="Arial" font-size="12" fill="#556070">{population}</text>
  <text x="{left + chart_w - 58}" y="{top + chart_h + 28}" font-family="Arial" font-size="12" fill="#556070">时间 / s</text>
  <text x="10" y="{top + 12}" font-family="Arial, Microsoft YaHei" font-size="12" fill="#556070">已疏散人数</text>
  <polyline points="{polyline}" fill="none" stroke="#2764e7" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
  <text x="{left + chart_w - 98}" y="{top + 18}" font-family="Arial" font-size="12" fill="#2764e7">{max_time:g}s</text>
</svg>'''


def _heat_color(value: float) -> str:
    # White → orange → dark red, based solely on observed occupancy counts.
    t = max(0.0, min(1.0, value))
    red = int(255)
    green = int(248 - 175 * t)
    blue = int(242 - 210 * t)
    return f"rgb({red},{green},{blue})"


def _heatmap_svg(occupancy: list[list[int]]) -> str:
    height = len(occupancy)
    width = len(occupancy[0]) if occupancy else 0
    if width <= 0 or height <= 0:
        return "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"320\" height=\"120\"><text x=\"12\" y=\"30\">无可绘制网格数据</text></svg>"
    cell = max(5, min(24, int(620 / max(width, height))))
    margin, title_h = 44, 48
    svg_w, svg_h = margin * 2 + width * cell, title_h + margin + height * cell
    maximum = max((value for row in occupancy for value in row), default=0)
    cells: list[str] = []
    for y, row in enumerate(occupancy):
        for x, value in enumerate(row):
            ratio = 0.0 if maximum == 0 else value / maximum
            cells.append(
                f'<rect x="{margin + x * cell}" y="{title_h + y * cell}" width="{cell}" height="{cell}" fill="{_heat_color(ratio)}" stroke="#e5e7eb" stroke-width="0.4"/>'
            )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}">
  <rect width="100%" height="100%" fill="#ffffff"/>
  <text x="{margin}" y="24" font-family="Arial, Microsoft YaHei" font-size="17" fill="#18243a">人员占用热力图（真实日志）</text>
  <text x="{margin}" y="40" font-family="Arial, Microsoft YaHei" font-size="11" fill="#556070">颜色越深表示未撤离人员在该元胞累计出现次数越多；最大值 {maximum}</text>
  {''.join(cells)}
</svg>'''


def _metric_rows(
    people_rows: list[dict[str, str]],
    *,
    final_snapshot: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[tuple[float, int]], list[list[int]], dict[str, Any]]:
    grid = final_snapshot.get("grid")
    if not isinstance(grid, Mapping):
        raise ResultPackageError("latest snapshot lacks grid")
    width = _parse_int(grid.get("width"))
    height = _parse_int(grid.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        raise ResultPackageError("latest snapshot has invalid grid dimensions")

    grouped: dict[int, list[dict[str, str]]] = defaultdict(list)
    for row in people_rows:
        step = _parse_int(row.get("step"))
        if step is not None:
            grouped[step].append(row)
    if not grouped:
        raise ResultPackageError("people_log.csv contains no step rows")

    initial_population = len(grouped[min(grouped)])
    first_evacuation_time: dict[int, float] = {}
    curve: list[tuple[float, int]] = []
    occupancy = [[0 for _ in range(width)] for _ in range(height)]
    final_evacuation_count = 0

    for step in sorted(grouped):
        rows = grouped[step]
        evacuated_count = 0
        time_s = _parse_float(rows[0].get("time_s"), default=0.0) or 0.0
        for row in rows:
            person_id = _parse_int(row.get("person_id"))
            evacuated = _is_true(row.get("evacuated"))
            if evacuated:
                evacuated_count += 1
                if person_id is not None and person_id not in first_evacuation_time:
                    first_evacuation_time[person_id] = time_s
                continue
            x, y = _parse_int(row.get("x")), _parse_int(row.get("y"))
            if x is not None and y is not None and 0 <= x < width and 0 <= y < height:
                occupancy[y][x] += 1
        curve.append((time_s, evacuated_count))
        final_evacuation_count = evacuated_count

    remaining = max(0, initial_population - final_evacuation_count)
    total_time = curve[-1][0]
    success_times = list(first_evacuation_time.values())
    last_success = max(success_times) if success_times else None
    t90_target = math.ceil(initial_population * 0.9)
    t90 = next((time_s for time_s, count in curve if count >= t90_target), None)
    complete = final_evacuation_count == initial_population
    metrics = [
        {"metric_name": "initial_population", "value": initial_population, "unit": "person", "note": "from step 0 people_log"},
        {"metric_name": "evacuated_count", "value": final_evacuation_count, "unit": "person", "note": "latest recorded step"},
        {"metric_name": "evacuation_rate", "value": round(final_evacuation_count / max(1, initial_population), 6), "unit": "ratio", "note": "evacuated_count / initial_population"},
        {"metric_name": "remaining_count", "value": remaining, "unit": "person", "note": "latest recorded step"},
        {"metric_name": "last_successful_exit_time", "value": "" if last_success is None else last_success, "unit": "s", "note": "first observed evacuated=true transition"},
        {"metric_name": "total_evacuation_time", "value": last_success if complete else "", "unit": "s", "note": "empty unless all persons have evacuated"},
        {"metric_name": "mean_evacuation_time", "value": "" if not success_times else round(sum(success_times) / len(success_times), 6), "unit": "s", "note": "successful evacuees only"},
        {"metric_name": "t90", "value": "" if t90 is None else t90, "unit": "s", "note": "empty when 90% evacuation was not reached"},
        {"metric_name": "exit_utilization", "value": "", "unit": "ratio", "note": "not calculated: current B output has no actual_exit field"},
    ]
    summary = {
        "initial_population": initial_population,
        "evacuated_count": final_evacuation_count,
        "remaining_count": remaining,
        "evacuation_rate": round(final_evacuation_count / max(1, initial_population), 6),
        "last_successful_exit_time": last_success,
        "completed": complete,
        "last_recorded_time_s": total_time,
    }
    return metrics, curve, occupancy, summary


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
    people_rows = _read_csv(people_path)
    event_rows = _read_csv(event_path)
    metrics, curve, occupancy, summary = _metric_rows(
        people_rows, final_snapshot=final_snapshot
    )

    metadata = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "schema_version": final_snapshot.get("schema_version"),
        "time_step_s": final_snapshot.get("time_step"),
        "last_step": final_snapshot.get("step"),
        "max_steps": max_steps,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "data_source": "A map + C population + B CA via D integration boundary",
        "limitations": {
            "missing_upstream_fields_remain_empty": ["heading", "risk", "dose", "conflict", "exit_switch"],
            "exit_utilization": "not available until B provides actual_exit",
            "strategy_controls": "not connected to B movement decisions in this package",
        },
        "summary": summary,
    }
    configuration = {
        "run_id": run_id,
        "scenario_id": scenario_id,
        "time_step_s": final_snapshot.get("time_step"),
        "max_steps": max_steps,
        "input_files": {key: path.name for key, path in input_files.items() if path.is_file()},
    }

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        prefix = f"{run_id}/"
        bundle.writestr(prefix + "metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "config.json", json.dumps(configuration, ensure_ascii=False, indent=2))
        bundle.writestr(prefix + "metrics.csv", _csv_text(metrics))
        bundle.writestr(prefix + "evacuation_curve.svg", _curve_svg(curve, summary["initial_population"]))
        bundle.writestr(prefix + "occupancy_heatmap.svg", _heatmap_svg(occupancy))
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
