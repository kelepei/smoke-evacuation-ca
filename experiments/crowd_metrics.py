"""D-side physical-scale contracts and trajectory kinematics.

This module deliberately stops before congestion metrics.  It turns the
normalized D people log into auditable cell-space velocities using the
platform-wide 0.5 m/cell simulation assumption.  The legacy ``grid.cell_size``
is never interpreted as metres.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.platform_physics import (
    PLATFORM_PHYSICAL_CELL_SIZE_M,
    PLATFORM_PHYSICAL_SCALE_NOTE,
    PLATFORM_PHYSICAL_SCALE_SOURCE,
    platform_physical_cell_size,
)


DEFAULT_SAMPLING_WINDOW_S = 2.5
KINEMATICS_FIELDS = [
    "person_id", "step", "time_s", "x", "y", "dx_cells", "dy_cells",
    "dt_s", "vx_cells_s", "vy_cells_s", "speed_cells_s", "vx_m_s",
    "vy_m_s", "speed_m_s", "validity",
]
VALID_VELOCITY_SAMPLES = {"valid", "valid_step_gap", "valid_to_exit"}
VELOCITY_FIELD_FIELDS = [
    "window_index", "window_start_s", "window_end_s", "x", "y",
    "vx_cells_s", "vy_cells_s", "speed_cells_s", "vx_m_s", "vy_m_s",
    "speed_m_s", "sample_count", "validity", "physical_status",
]
# This is intentionally derived from B's movement rule: only walls and
# obstacles block a person.  Exit and smoke-source cells remain part of the
# legal pedestrian domain; treating them as empty exterior space would bias a
# boundary Voronoi area.
IMPASSABLE_CELL_TYPES = {"wall", "obstacle"}
VORONOI_EPSILON = 1e-12


def _positive_finite(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive finite number")
    try:
        normalized = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return normalized


def resolve_analysis_contract(
    *,
    map_analysis: Mapping[str, Any] | None = None,
    runtime_physical_cell_size_m: Any = None,
    runtime_sampling_window_s: Any = None,
) -> dict[str, Any]:
    """Resolve the fixed platform scale and explicit analysis sampling settings."""

    map_analysis = map_analysis or {}
    if not isinstance(map_analysis, Mapping):
        raise ValueError("map analysis metadata must be an object")

    map_scale_raw = map_analysis.get("physical_cell_size_m")
    map_scale = (
        None if map_scale_raw in (None, "")
        else _positive_finite(map_scale_raw, field="analysis.physical_cell_size_m")
    )
    platform_physical_cell_size(
        runtime_physical_cell_size_m,
        field="runtime physical_cell_size_m",
    )
    map_scale_differs = map_scale is not None and not math.isclose(
        map_scale,
        PLATFORM_PHYSICAL_CELL_SIZE_M,
        rel_tol=0.0,
        abs_tol=1e-12,
    )
    physical_scale = {
        "source": PLATFORM_PHYSICAL_SCALE_SOURCE,
        "value": PLATFORM_PHYSICAL_CELL_SIZE_M,
        "unit": "m",
        "assumption": "uniform_simulation",
        "warning": PLATFORM_PHYSICAL_SCALE_NOTE,
        "map_declared": (
            {
                "physical_cell_size_m": map_scale,
                "unit": "m",
                "source": "explicit_map",
                "differs_from_platform_assumption": map_scale_differs,
            }
            if map_scale is not None
            else None
        ),
    }

    map_window_raw = map_analysis.get("sampling_window_s")
    map_window = (
        None if map_window_raw in (None, "")
        else _positive_finite(map_window_raw, field="analysis.sampling_window_s")
    )
    runtime_window = (
        None if runtime_sampling_window_s in (None, "")
        else _positive_finite(
            runtime_sampling_window_s, field="runtime sampling_window_s"
        )
    )
    if runtime_window is not None:
        sampling_window = {
            "source": "runtime_config",
            "value": runtime_window,
            "unit": "s",
            "note": "explicit run setting; retain for sensitivity analysis",
        }
    elif map_window is not None:
        sampling_window = {
            "source": "explicit_map",
            "value": map_window,
            "unit": "s",
            "note": "explicit map-analysis setting; retain for sensitivity analysis",
        }
    else:
        sampling_window = {
            "source": "literature_default",
            "value": DEFAULT_SAMPLING_WINDOW_S,
            "unit": "s",
            "note": (
                "2018 literature reference only; not calibrated for this "
                "project and not used to resample this artifact"
            ),
        }
    velocity_method = map_analysis.get("velocity_method")
    if velocity_method in (None, ""):
        velocity_method = "consecutive_valid_log_samples"
    if velocity_method != "consecutive_valid_log_samples":
        raise ValueError(
            "analysis.velocity_method must be consecutive_valid_log_samples "
            "for the current kinematics foundation"
        )
    return {
        "physical_scale": physical_scale,
        "sampling_window_s": sampling_window,
        "velocity_method": velocity_method,
        "map_declared_analysis": dict(map_analysis),
    }


def normalize_analysis_contract(
    contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Upgrade legacy contracts to the fixed platform physical scale."""

    result = resolve_analysis_contract()
    if not isinstance(contract, Mapping):
        return result
    for key, value in contract.items():
        if key == "physical_scale":
            continue
        result[key] = dict(value) if isinstance(value, Mapping) else value
    sampling = contract.get("sampling_window_s")
    if not (
        isinstance(sampling, Mapping)
        and isinstance(sampling.get("value"), (int, float))
        and not isinstance(sampling.get("value"), bool)
        and sampling.get("value") > 0
    ):
        result["sampling_window_s"] = resolve_analysis_contract()["sampling_window_s"]
    raw_physical = contract.get("physical_scale")
    if isinstance(raw_physical, Mapping) and "map_declared" in raw_physical:
        result["physical_scale"]["map_declared"] = raw_physical["map_declared"]
    return result


def with_runtime_dt(
    contract: Mapping[str, Any] | None, *, dt_s: Any
) -> dict[str, Any]:
    """Attach the runtime's declared dt without inventing a fixed value."""

    dt = _positive_finite(dt_s, field="runtime dt_s")
    result = normalize_analysis_contract(contract)
    result["dt_s"] = {
        "source": "runtime_snapshot.time_step",
        "value": dt,
        "unit": "s",
    }
    return result


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if str(value).strip() not in {str(parsed), f"{parsed}.0"} and not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return parsed


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _is_evacuated(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _grid_position_validity(
    grid: Mapping[str, Any] | None, x: int, y: int
) -> str | None:
    if not isinstance(grid, Mapping):
        return None
    width, height = grid.get("width"), grid.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        return None
    if not (0 <= x < width and 0 <= y < height):
        return "invalid_position_out_of_bounds"
    cell_type = grid.get("cell_type")
    if not isinstance(cell_type, list) or y >= len(cell_type):
        return None
    row = cell_type[y]
    if not isinstance(row, list) or x >= len(row):
        return None
    value = str(row[x]).strip().lower()
    if value in {"wall", "obstacle"}:
        return "invalid_impassable_position"
    return None


def _na(value: float | None) -> float | str:
    return "NA" if value is None else value


def _physical_scale_value(physical_scale: Mapping[str, Any] | None) -> float | None:
    """Return only an explicit, positive physical scale.

    The source check prevents a caller from attaching a numeric legacy grid
    size to an otherwise unavailable physical-scale contract.
    """

    if not isinstance(physical_scale, Mapping):
        return None
    if physical_scale.get("source") not in {
        "explicit_map",
        "runtime_config",
        PLATFORM_PHYSICAL_SCALE_SOURCE,
    }:
        return None
    value = physical_scale.get("value")
    return None if value in (None, "") else _positive_finite(
        value, field="physical_scale.value"
    )


def _walkable_row_runs(
    grid: Mapping[str, Any], *, roi_cells: Iterable[tuple[int, int]] | None = None
) -> list[tuple[int, int, int]]:
    """Return legal row intervals ``(y, x_start, x_end_exclusive)``."""

    width, height = grid.get("width"), grid.get("height")
    cell_type = grid.get("cell_type")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("grid width and height must be positive integers")
    if not isinstance(cell_type, list) or len(cell_type) != height:
        raise ValueError("grid.cell_type must contain one row per grid row")
    requested = None if roi_cells is None else set(roi_cells)
    if requested is not None:
        for x, y in requested:
            if not (isinstance(x, int) and isinstance(y, int) and 0 <= x < width and 0 <= y < height):
                raise ValueError("ROI cell is outside the grid")

    result: list[tuple[int, int, int]] = []
    for y, row in enumerate(cell_type):
        if not isinstance(row, list) or len(row) != width:
            raise ValueError("grid.cell_type row width does not match grid.width")
        run_start: int | None = None
        for x in range(width + 1):
            legal = (
                x < width
                and (requested is None or (x, y) in requested)
                and str(row[x]).strip().lower() not in IMPASSABLE_CELL_TYPES
            )
            if legal and run_start is None:
                run_start = x
            elif not legal and run_start is not None:
                result.append((y, run_start, x))
                run_start = None
    return result


def _walkable_cells(
    grid: Mapping[str, Any], *, roi_cells: Iterable[tuple[int, int]] | None = None
) -> list[tuple[int, int]]:
    """Return legal CA cells, optionally limited to an explicit grid ROI."""

    return [
        (x, y)
        for y, x_start, x_end in _walkable_row_runs(grid, roi_cells=roi_cells)
        for x in range(x_start, x_end)
    ]


def _polygon_area(vertices: list[tuple[float, float]]) -> float:
    if len(vertices) < 3:
        return 0.0
    return abs(sum(
        vertices[index][0] * vertices[(index + 1) % len(vertices)][1]
        - vertices[(index + 1) % len(vertices)][0] * vertices[index][1]
        for index in range(len(vertices))
    )) * 0.5


def _clip_half_plane(
    polygon: list[tuple[float, float]], *, nx: float, ny: float, c: float
) -> list[tuple[float, float]]:
    """Clip a convex polygon to nx*x + ny*y <= c (Sutherland-Hodgman)."""

    if not polygon:
        return []
    result: list[tuple[float, float]] = []
    previous = polygon[-1]
    previous_value = nx * previous[0] + ny * previous[1] - c
    previous_inside = previous_value <= VORONOI_EPSILON
    for current in polygon:
        current_value = nx * current[0] + ny * current[1] - c
        current_inside = current_value <= VORONOI_EPSILON
        if current_inside != previous_inside:
            denominator = previous_value - current_value
            if abs(denominator) > VORONOI_EPSILON:
                fraction = previous_value / denominator
                result.append((
                    previous[0] + fraction * (current[0] - previous[0]),
                    previous[1] + fraction * (current[1] - previous[1]),
                ))
        if current_inside:
            result.append(current)
        previous, previous_value, previous_inside = current, current_value, current_inside
    return result


def _cell_polygon(x: int, y: int, scale_m: float) -> list[tuple[float, float]]:
    left, top = x * scale_m, y * scale_m
    right, bottom = left + scale_m, top + scale_m
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def _clip_to_rectangle(
    polygon: list[tuple[float, float]],
    *,
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> list[tuple[float, float]]:
    for nx, ny, c in (
        (1.0, 0.0, right),
        (-1.0, 0.0, -left),
        (0.0, 1.0, bottom),
        (0.0, -1.0, -top),
    ):
        polygon = _clip_half_plane(polygon, nx=nx, ny=ny, c=c)
        if not polygon:
            break
    return polygon


def voronoi_density_for_frame(
    positions: Iterable[Mapping[str, Any]],
    *,
    grid: Mapping[str, Any],
    physical_scale: Mapping[str, Any] | None,
    roi_cells: Iterable[tuple[int, int]] | None = None,
    time_s: float | None = None,
    legal_row_runs: Iterable[tuple[int, int, int]] | None = None,
    use_row_runs: bool = True,
) -> dict[str, Any]:
    """Compute clipped Voronoi local density for one observed CA frame.

    A person's Voronoi cell is intersected with every legal CA-cell square and
    areas are summed.  This represents the exact intersection with the union
    of walkable squares, so walls, obstacles, map edges, and an optional ROI
    cannot leak into a person's available area.  It deliberately requires an
    explicit metres-per-cell contract; when unavailable it emits no density.
    """

    scale_m = _physical_scale_value(physical_scale)
    raw_positions = list(positions)
    if scale_m is None:
        return {
            "status": "unavailable_physical_scale",
            "method": "clipped_voronoi_legal_ca_domain",
            "unit": None,
            "time_s": time_s,
            "samples": [],
            "warning": "formal persons/m² requires explicit physical_cell_size_m",
        }
    width, height = int(grid["width"]), int(grid["height"])
    row_runs = (
        list(legal_row_runs)
        if legal_row_runs is not None
        else _walkable_row_runs(grid, roi_cells=roi_cells)
    )
    runs_by_row: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for y, x_start, x_end in row_runs:
        if not (
            isinstance(y, int)
            and isinstance(x_start, int)
            and isinstance(x_end, int)
            and 0 <= y < height
            and 0 <= x_start < x_end <= width
        ):
            raise ValueError("legal row run is outside the grid")
        runs_by_row[y].append((x_start, x_end))
    for runs in runs_by_row.values():
        runs.sort()
        for previous, current in zip(runs, runs[1:]):
            if previous[1] > current[0]:
                raise ValueError("legal row runs overlap")
    legal_cell_count = sum(x_end - x_start for _, x_start, x_end in row_runs)
    if legal_cell_count <= 0:
        raise ValueError("Voronoi density ROI contains no legal pedestrian cells")

    def legal_position(x: int, y: int) -> bool:
        return any(x_start <= x < x_end for x_start, x_end in runs_by_row.get(y, ()))

    sites: list[tuple[int, int, int]] = []
    seen_ids: set[int] = set()
    seen_positions: set[tuple[int, int]] = set()
    for raw in raw_positions:
        person_id = _integer(raw.get("person_id"), field="person_id")
        x, y = _integer(raw.get("x"), field="x"), _integer(raw.get("y"), field="y")
        if person_id in seen_ids:
            raise ValueError("Voronoi frame contains duplicate person_id")
        if (x, y) in seen_positions:
            raise ValueError("Voronoi frame contains overlapping person positions")
        if not legal_position(x, y):
            raise ValueError("Voronoi person position is outside the legal pedestrian domain")
        seen_ids.add(person_id)
        seen_positions.add((x, y))
        sites.append((person_id, x, y))

    samples: list[dict[str, Any]] = []
    physical_sites = [
        (person_id, (x + 0.5) * scale_m, (y + 0.5) * scale_m)
        for person_id, x, y in sites
    ]
    site_by_id = {person_id: (x, y) for person_id, x, y in sites}
    for person_id, px, py in physical_sites:
        # Clip the global rectangular extent once before considering legal
        # cells.  This is algebraically identical to clipping every CA square
        # independently, but limits the exact union-of-squares intersection
        # to cells that can still touch this person's Voronoi polygon.
        voronoi_polygon = [
            (0.0, 0.0), (width * scale_m, 0.0),
            (width * scale_m, height * scale_m), (0.0, height * scale_m),
        ]
        for other_id, ox, oy in physical_sites:
            if other_id == person_id:
                continue
            nx, ny = ox - px, oy - py
            c = (ox * ox + oy * oy - px * px - py * py) / 2.0
            voronoi_polygon = _clip_half_plane(voronoi_polygon, nx=nx, ny=ny, c=c)
            if not voronoi_polygon:
                break
        if not voronoi_polygon:
            raise ValueError("Voronoi clipping produced a non-positive legal area")
        min_x = max(0, math.floor(min(point[0] for point in voronoi_polygon) / scale_m))
        max_x = min(width - 1, math.ceil(max(point[0] for point in voronoi_polygon) / scale_m) - 1)
        min_y = max(0, math.floor(min(point[1] for point in voronoi_polygon) / scale_m))
        max_y = min(height - 1, math.ceil(max(point[1] for point in voronoi_polygon) / scale_m) - 1)
        area_m2 = 0.0
        if use_row_runs:
            for cell_y in range(min_y, max_y + 1):
                for run_start, run_end in runs_by_row.get(cell_y, ()):
                    clipped_start = max(run_start, min_x)
                    clipped_end = min(run_end, max_x + 1)
                    if clipped_start >= clipped_end:
                        continue
                    left, top = clipped_start * scale_m, cell_y * scale_m
                    right = clipped_end * scale_m
                    bottom = top + scale_m
                    polygon = _clip_to_rectangle(
                        voronoi_polygon,
                        left=left,
                        top=top,
                        right=right,
                        bottom=bottom,
                    )
                    area_m2 += _polygon_area(polygon)
        else:
            # Original cell-by-cell exact path retained as a reference for
            # small-scene equivalence tests and profiling.
            for cell_y in range(min_y, max_y + 1):
                for cell_x in range(min_x, max_x + 1):
                    if not legal_position(cell_x, cell_y):
                        continue
                    left, top = cell_x * scale_m, cell_y * scale_m
                    right = left + scale_m
                    bottom = top + scale_m
                    polygon = _clip_to_rectangle(
                        voronoi_polygon,
                        left=left,
                        top=top,
                        right=right,
                        bottom=bottom,
                    )
                    area_m2 += _polygon_area(polygon)
        if area_m2 <= VORONOI_EPSILON:
            raise ValueError("Voronoi clipping produced a non-positive legal area")
        samples.append({
            "person_id": person_id,
            "x": site_by_id[person_id][0],
            "y": site_by_id[person_id][1],
            "voronoi_area_m2": area_m2,
            "density_persons_m2": 1.0 / area_m2,
            "validity": "valid",
        })
    return {
        "status": "available",
        "method": "clipped_voronoi_legal_ca_domain",
        "unit": "persons/m²",
        "time_s": time_s,
        "physical_cell_size_m": scale_m,
        "legal_cell_count": legal_cell_count,
        "legal_row_run_count": len(row_runs),
        "roi": "explicit_grid_cells" if roi_cells is not None else "all_legal_grid_cells",
        "samples": samples,
    }


def velocity_vector_field(
    kinematic_rows: Iterable[Mapping[str, Any]],
    *,
    sampling_window_s: Any,
    physical_scale: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate valid observed velocities by CA cell and true-time window.

    No missing windows are filled with zero.  A field cell represents the
    arithmetic mean of observed components ending in that cell during the
    window; the result remains a labelled grid-space diagnostic until an
    explicit physical scale is present.
    """

    window_s = _positive_finite(sampling_window_s, field="sampling_window_s")
    groups: dict[tuple[int, int, int], list[tuple[float, float]]] = defaultdict(list)
    validity_counts: Counter[str] = Counter()
    for row in kinematic_rows:
        validity = str(row.get("validity", ""))
        validity_counts[validity] += 1
        if validity not in VALID_VELOCITY_SAMPLES:
            continue
        try:
            time_s = _finite_number(row.get("time_s"), field="time_s")
            x, y = _integer(row.get("x"), field="x"), _integer(row.get("y"), field="y")
            vx = _finite_number(row.get("vx_cells_s"), field="vx_cells_s")
            vy = _finite_number(row.get("vy_cells_s"), field="vy_cells_s")
        except ValueError:
            continue
        window_index = math.floor(time_s / window_s)
        groups[(window_index, x, y)].append((vx, vy))

    scale_m = _physical_scale_value(physical_scale)
    records: list[dict[str, Any]] = []
    for (window_index, x, y), values in sorted(groups.items()):
        vx = sum(value[0] for value in values) / len(values)
        vy = sum(value[1] for value in values) / len(values)
        record: dict[str, Any] = {
            "window_index": window_index,
            "window_start_s": window_index * window_s,
            "window_end_s": (window_index + 1) * window_s,
            "x": x,
            "y": y,
            "vx_cells_s": vx,
            "vy_cells_s": vy,
            "speed_cells_s": math.hypot(vx, vy),
            "sample_count": len(values),
            "validity": "valid_observed_samples",
        }
        if scale_m is None:
            record.update({
                "vx_m_s": "NA", "vy_m_s": "NA", "speed_m_s": "NA",
                "physical_status": "unavailable_physical_scale",
            })
        else:
            record.update({
                "vx_m_s": vx * scale_m,
                "vy_m_s": vy * scale_m,
                "speed_m_s": math.hypot(vx, vy) * scale_m,
                "physical_status": "available",
            })
        records.append(record)
    return {
        "status": "available" if scale_m is not None else "diagnostic_grid_space_only",
        "method": "observed_velocity_component_mean_by_ca_cell_and_time_window",
        "sampling_window_s": window_s,
        "physical_cell_size_m": scale_m,
        "records": records,
        "input_validity_counts": dict(sorted(validity_counts.items())),
        "missing_windows": "not emitted; missing data is never represented as zero velocity",
    }


def write_velocity_vector_field(
    *,
    kinematics_path: str | Path,
    output_path: str | Path,
    csv_output_path: str | Path | None = None,
    analysis_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Write no-fabrication velocity-field JSON and CSV from a real artifact."""

    contract = dict(analysis_contract or resolve_analysis_contract())
    with Path(kinematics_path).open("r", encoding="utf-8", newline="") as stream:
        result = velocity_vector_field(
            csv.DictReader(stream),
            sampling_window_s=(contract.get("sampling_window_s") or {}).get("value"),
            physical_scale=contract.get("physical_scale"),
        )
    destination = Path(output_path)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    csv_destination = (
        Path(csv_output_path)
        if csv_output_path is not None
        else destination.with_suffix(".csv")
    )
    with csv_destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=VELOCITY_FIELD_FIELDS)
        writer.writeheader()
        writer.writerows(result["records"])
    return {
        "json_path": destination.name,
        "csv_path": csv_destination.name,
        "status": result["status"],
        "record_count": len(result["records"]),
        "sampling_window_s": result["sampling_window_s"],
        "physical_cell_size_m": result["physical_cell_size_m"],
    }


def trajectory_kinematics(
    trajectory_rows: Iterable[Mapping[str, Any]],
    *,
    physical_scale: Mapping[str, Any] | None = None,
    grid: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Calculate auditable pairwise kinematics from real D trajectory rows.

    Rows are grouped by person and evaluated by logged step order.  Sorting by
    raw time would hide repeated/backward timestamps, so those are retained as
    invalid samples instead.  A step gap uses the observed time difference;
    no missing frames are fabricated.
    """

    scale_value: float | None = None
    if isinstance(physical_scale, Mapping) and physical_scale.get("value") not in (None, ""):
        scale_value = _positive_finite(
            physical_scale.get("value"), field="physical_scale.value"
        )

    grouped: dict[int, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(trajectory_rows):
        try:
            person_id = _integer(row.get("person_id"), field="person_id")
        except ValueError:
            # A row without a stable person id cannot be joined to a trajectory.
            continue
        grouped[person_id].append((index, row))

    result: list[dict[str, Any]] = []
    for person_id in sorted(grouped):
        def record_sort_key(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int, int]:
            index, row = item
            try:
                return (0, _integer(row.get("step"), field="step"), index)
            except ValueError:
                return (1, index, index)

        records = sorted(
            grouped[person_id],
            key=record_sort_key,
        )
        previous: dict[str, Any] | None = None
        for _, raw in records:
            try:
                step = _integer(raw.get("step"), field="step")
                time_s = _finite_number(raw.get("time_s"), field="time_s")
                x = _integer(raw.get("x"), field="x")
                y = _integer(raw.get("y"), field="y")
            except ValueError:
                result.append({
                    "person_id": person_id, "step": raw.get("step"),
                    "time_s": raw.get("time_s"), "x": raw.get("x"),
                    "y": raw.get("y"), "dx_cells": "NA", "dy_cells": "NA",
                    "dt_s": "NA", "vx_cells_s": "NA", "vy_cells_s": "NA",
                    "speed_cells_s": "NA", "vx_m_s": "NA", "vy_m_s": "NA",
                    "speed_m_s": "NA", "validity": "invalid_required_field",
                })
                continue

            evacuated = _is_evacuated(raw.get("evacuated"))
            position_error = _grid_position_validity(grid, x, y)
            base = {"person_id": person_id, "step": step, "time_s": time_s, "x": x, "y": y}
            if previous is not None and previous["evacuated"]:
                result.append({
                    **base, "dx_cells": "NA", "dy_cells": "NA", "dt_s": "NA",
                    "vx_cells_s": "NA", "vy_cells_s": "NA", "speed_cells_s": "NA",
                    "vx_m_s": "NA", "vy_m_s": "NA", "speed_m_s": "NA",
                    "validity": "excluded_post_evacuation",
                })
                continue
            if position_error is not None:
                result.append({
                    **base, "dx_cells": "NA", "dy_cells": "NA", "dt_s": "NA",
                    "vx_cells_s": "NA", "vy_cells_s": "NA", "speed_cells_s": "NA",
                    "vx_m_s": "NA", "vy_m_s": "NA", "speed_m_s": "NA",
                    "validity": position_error,
                })
                continue
            if previous is None:
                result.append({
                    **base, "dx_cells": "NA", "dy_cells": "NA", "dt_s": "NA",
                    "vx_cells_s": "NA", "vy_cells_s": "NA", "speed_cells_s": "NA",
                    "vx_m_s": "NA", "vy_m_s": "NA", "speed_m_s": "NA",
                    "validity": "excluded_initial_evacuated" if evacuated else "initial_sample",
                })
                previous = {**base, "evacuated": evacuated}
                continue

            dx, dy = x - previous["x"], y - previous["y"]
            dt_s = time_s - previous["time_s"]
            step_gap = step - previous["step"]
            if step_gap <= 0:
                validity = "invalid_nonincreasing_step"
            elif dt_s <= 0:
                validity = "invalid_nonpositive_dt"
            else:
                allowed_chebyshev = step_gap
                moved_chebyshev = max(abs(dx), abs(dy))
                if moved_chebyshev > allowed_chebyshev:
                    validity = "flagged_teleport"
                elif evacuated:
                    validity = "valid_to_exit"
                elif step_gap > 1:
                    validity = "valid_step_gap"
                else:
                    validity = "valid"

            if validity.startswith("invalid"):
                result.append({
                    **base, "dx_cells": dx, "dy_cells": dy, "dt_s": _na(dt_s),
                    "vx_cells_s": "NA", "vy_cells_s": "NA", "speed_cells_s": "NA",
                    "vx_m_s": "NA", "vy_m_s": "NA", "speed_m_s": "NA",
                    "validity": validity,
                })
                continue

            vx_cells_s, vy_cells_s = dx / dt_s, dy / dt_s
            speed_cells_s = math.hypot(vx_cells_s, vy_cells_s)
            vx_m_s = None if scale_value is None else vx_cells_s * scale_value
            vy_m_s = None if scale_value is None else vy_cells_s * scale_value
            speed_m_s = None if scale_value is None else speed_cells_s * scale_value
            result.append({
                **base, "dx_cells": dx, "dy_cells": dy, "dt_s": dt_s,
                "vx_cells_s": vx_cells_s, "vy_cells_s": vy_cells_s,
                "speed_cells_s": speed_cells_s, "vx_m_s": _na(vx_m_s),
                "vy_m_s": _na(vy_m_s), "speed_m_s": _na(speed_m_s),
                "validity": validity,
            })
            previous = {**base, "evacuated": evacuated}
    return result


def read_people_log(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def write_trajectory_kinematics(
    *,
    people_log_path: str | Path,
    output_path: str | Path,
    analysis_contract: Mapping[str, Any] | None = None,
    grid: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the validation CSV and return its truthful provenance summary."""

    contract = dict(analysis_contract or resolve_analysis_contract())
    rows = trajectory_kinematics(
        read_people_log(people_log_path),
        physical_scale=contract.get("physical_scale"),
        grid=grid,
    )
    destination = Path(output_path)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=KINEMATICS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return {
        "path": destination.name,
        "row_count": len(rows),
        "validity_counts": dict(sorted(Counter(row["validity"] for row in rows).items())),
        "analysis_contract": contract,
    }
