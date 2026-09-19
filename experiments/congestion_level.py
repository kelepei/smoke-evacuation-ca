"""D-side analysis mesh, velocity curl, and Congestion Level foundation.

This module is deliberately downstream of the B CA runtime.  It consumes
auditable trajectory kinematics, never changes movement, and produces formal
CL only when a calibrated physical scale *and* explicit analysis-mesh/ROI
settings are present.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.crowd_metrics import (
    IMPASSABLE_CELL_TYPES,
    VALID_VELOCITY_SAMPLES,
)


CONGESTION_LEVEL_FIELDS = [
    "time_window", "window_start_s", "window_end_s", "analysis_x", "analysis_y",
    "vx_m_s", "vy_m_s", "speed_m_s", "curl_z_s_inv", "cl_m_inv",
    "sample_count", "validity", "analysis_mesh_size_m", "roi_definition",
]
DEFAULT_MIN_VALID_ROI_CELLS = 3
DEFAULT_MIN_VELOCITY_SAMPLES = 1
DEFAULT_NEAR_ZERO_SPEED_M_S = 1e-9


def _positive(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive finite number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return number


def _finite(value: Any, *, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _integer(value: Any, *, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if isinstance(value, float) and value != number:
        raise ValueError(f"{name} must be an integer")
    return number


def _positive_int(value: Any, *, name: str) -> int:
    number = _integer(value, name=name)
    if number <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return number


def _physical_scale(analysis_contract: Mapping[str, Any]) -> float | None:
    physical = analysis_contract.get("physical_scale")
    if not isinstance(physical, Mapping):
        return None
    if physical.get("source") not in {"explicit_map", "runtime_config"}:
        return None
    value = physical.get("value")
    return None if value in (None, "") else _positive(value, name="physical_cell_size_m")


def _option(value: Any, *, source: str, unit: str, name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {"value": None, "unit": unit, "source": "unavailable", "warning": f"{name} was not explicitly supplied"}
    return {"value": _positive(value, name=name), "unit": unit, "source": source, "warning": None}


def resolve_congestion_level_contract(
    *,
    physical_scale: Mapping[str, Any] | None,
    map_analysis: Mapping[str, Any] | None = None,
    runtime_analysis_mesh_size_m: Any = None,
    runtime_roi_radius_m: Any = None,
) -> dict[str, Any]:
    """Resolve explicit analysis mesh and ROI settings without paper defaults.

    ``parameter_source: literature_reference`` is accepted only as labelled
    synthetic/test provenance.  It never materializes a value by itself.
    """

    root = map_analysis or {}
    if not isinstance(root, Mapping):
        raise ValueError("map analysis metadata must be an object")
    settings = root.get("congestion_level", {})
    if settings in (None, ""):
        settings = {}
    if not isinstance(settings, Mapping):
        raise ValueError("analysis.congestion_level must be an object")
    declared_source = settings.get("parameter_source", "explicit_map")
    if declared_source not in {"explicit_map", "literature_reference"}:
        raise ValueError("congestion_level.parameter_source is not supported")

    map_mesh = settings.get("analysis_mesh_size_m", root.get("analysis_mesh_size_m"))
    map_radius = settings.get("roi_radius_m", root.get("roi_radius_m"))
    map_diameter = settings.get("roi_diameter_m", root.get("roi_diameter_m"))
    if map_radius not in (None, "") and map_diameter not in (None, ""):
        raise ValueError("declare either roi_radius_m or roi_diameter_m, not both")
    if map_diameter not in (None, ""):
        map_radius = _positive(map_diameter, name="roi_diameter_m") / 2.0

    mesh = _option(
        runtime_analysis_mesh_size_m if runtime_analysis_mesh_size_m not in (None, "") else map_mesh,
        source="runtime_config" if runtime_analysis_mesh_size_m not in (None, "") else str(declared_source),
        unit="m", name="analysis_mesh_size_m",
    )
    radius = _option(
        runtime_roi_radius_m if runtime_roi_radius_m not in (None, "") else map_radius,
        source="runtime_config" if runtime_roi_radius_m not in (None, "") else str(declared_source),
        unit="m", name="roi_radius_m",
    )
    min_cells_raw = settings.get("minimum_valid_roi_cells", DEFAULT_MIN_VALID_ROI_CELLS)
    min_samples_raw = settings.get("minimum_velocity_samples", DEFAULT_MIN_VELOCITY_SAMPLES)
    near_zero_raw = settings.get("near_zero_mean_speed_m_s", DEFAULT_NEAR_ZERO_SPEED_M_S)
    if _physical_scale({"physical_scale": physical_scale}) is None:
        status = "unavailable_physical_scale"
    elif mesh["value"] is None or radius["value"] is None:
        status = "unconfigured_analysis_mesh_or_roi"
    else:
        status = "configured"
    return {
        "status": status,
        "analysis_mesh_size_m": mesh,
        "sampling_window_s": None,  # inherited from the enclosing analysis contract
        "roi": {
            "definition": "circular_radius_m",
            "radius_m": radius,
            "diameter_m": None if radius["value"] is None else 2.0 * radius["value"],
        },
        "minimum_valid_roi_cells": {
            "value": _positive_int(min_cells_raw, name="minimum_valid_roi_cells"),
            "unit": "analysis_cells",
            "source": "explicit_map" if "minimum_valid_roi_cells" in settings else "algorithm_default",
        },
        "minimum_velocity_samples": {
            "value": _positive_int(min_samples_raw, name="minimum_velocity_samples"),
            "unit": "samples_per_analysis_cell",
            "source": "explicit_map" if "minimum_velocity_samples" in settings else "algorithm_default",
        },
        "near_zero_mean_speed_m_s": {
            "value": _positive(near_zero_raw, name="near_zero_mean_speed_m_s"),
            "unit": "m/s",
            "source": "explicit_map" if "near_zero_mean_speed_m_s" in settings else "algorithm_default",
        },
        "notes": [
            "No paper mesh, ROI, or threshold is injected as a project default.",
            "literature_reference is provenance only and is appropriate for synthetic validation, not calibration.",
        ],
    }


def _walkable_mesh_cells(
    grid: Mapping[str, Any], *, physical_cell_size_m: float, mesh_size_m: float
) -> set[tuple[int, int]]:
    width, height = grid.get("width"), grid.get("height")
    cell_type = grid.get("cell_type")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("grid width and height must be positive integers")
    if not isinstance(cell_type, list) or len(cell_type) != height:
        raise ValueError("grid.cell_type shape is invalid")
    result: set[tuple[int, int]] = set()
    for y, row in enumerate(cell_type):
        if not isinstance(row, list) or len(row) != width:
            raise ValueError("grid.cell_type row width is invalid")
        for x, raw_type in enumerate(row):
            if str(raw_type).strip().lower() in IMPASSABLE_CELL_TYPES:
                continue
            left, top = x * physical_cell_size_m, y * physical_cell_size_m
            right, bottom = left + physical_cell_size_m, top + physical_cell_size_m
            min_x, min_y = math.floor(left / mesh_size_m), math.floor(top / mesh_size_m)
            max_x = math.ceil(right / mesh_size_m) - 1
            max_y = math.ceil(bottom / mesh_size_m) - 1
            for analysis_y in range(min_y, max_y + 1):
                for analysis_x in range(min_x, max_x + 1):
                    result.add((analysis_x, analysis_y))
    return result


def _congestion_settings(analysis_contract: Mapping[str, Any]) -> Mapping[str, Any]:
    settings = analysis_contract.get("congestion_level")
    if isinstance(settings, Mapping):
        return settings
    return {"status": "unconfigured_analysis_mesh_or_roi"}


def _setting_value(settings: Mapping[str, Any], name: str) -> Any:
    value = settings.get(name)
    return value.get("value") if isinstance(value, Mapping) else None


def analysis_mesh_velocity_field(
    kinematic_rows: Iterable[Mapping[str, Any]],
    *,
    grid: Mapping[str, Any],
    analysis_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Project observed velocities onto a physical analysis mesh.

    The mesh is independent from CA cells.  Samples are binned by their
    physical CA-cell centres; no value is interpolated into a missing mesh
    cell, including cells occupied by walls or obstacles.
    """

    physical_scale_m = _physical_scale(analysis_contract)
    settings = _congestion_settings(analysis_contract)
    mesh_size_m = _setting_value(settings, "analysis_mesh_size_m")
    sampling = analysis_contract.get("sampling_window_s")
    sampling_window_s = sampling.get("value") if isinstance(sampling, Mapping) else None
    if physical_scale_m is None:
        return {"status": "unavailable_physical_scale", "reason": "formal analysis mesh requires explicit physical_cell_size_m", "records": []}
    if mesh_size_m in (None, "") or sampling_window_s in (None, ""):
        return {"status": "unconfigured_analysis_mesh_or_roi", "reason": "analysis_mesh_size_m and sampling_window_s are required", "records": []}
    mesh_size_m = _positive(mesh_size_m, name="analysis_mesh_size_m")
    sampling_window_s = _positive(sampling_window_s, name="sampling_window_s")
    legal_mesh = _walkable_mesh_cells(
        grid, physical_cell_size_m=physical_scale_m, mesh_size_m=mesh_size_m
    )
    groups: dict[tuple[int, int, int], list[tuple[float, float]]] = defaultdict(list)
    input_counts: Counter[str] = Counter()
    excluded_outside_domain = 0
    for row in kinematic_rows:
        validity = str(row.get("validity", ""))
        input_counts[validity] += 1
        if validity not in VALID_VELOCITY_SAMPLES:
            continue
        try:
            time_s = _finite(row.get("time_s"), name="time_s")
            x, y = _integer(row.get("x"), name="x"), _integer(row.get("y"), name="y")
            vx_cells = _finite(row.get("vx_cells_s"), name="vx_cells_s")
            vy_cells = _finite(row.get("vy_cells_s"), name="vy_cells_s")
        except ValueError:
            continue
        analysis_x = math.floor(((x + 0.5) * physical_scale_m) / mesh_size_m)
        analysis_y = math.floor(((y + 0.5) * physical_scale_m) / mesh_size_m)
        if (analysis_x, analysis_y) not in legal_mesh:
            excluded_outside_domain += 1
            continue
        time_window = math.floor(time_s / sampling_window_s)
        groups[(time_window, analysis_x, analysis_y)].append((
            vx_cells * physical_scale_m, vy_cells * physical_scale_m
        ))
    records: list[dict[str, Any]] = []
    for (time_window, analysis_x, analysis_y), vectors in sorted(groups.items()):
        vx = sum(vector[0] for vector in vectors) / len(vectors)
        vy = sum(vector[1] for vector in vectors) / len(vectors)
        records.append({
            "time_window": time_window,
            "window_start_s": time_window * sampling_window_s,
            "window_end_s": (time_window + 1) * sampling_window_s,
            "analysis_x": analysis_x,
            "analysis_y": analysis_y,
            "vx_m_s": vx,
            "vy_m_s": vy,
            "speed_m_s": math.hypot(vx, vy),
            "sample_count": len(vectors),
            "validity": "valid_observed_samples",
            "analysis_mesh_size_m": mesh_size_m,
        })
    return {
        "status": "available",
        "method": "observed_velocity_component_mean_by_independent_analysis_mesh_and_time_window",
        "physical_cell_size_m": physical_scale_m,
        "analysis_mesh_size_m": mesh_size_m,
        "sampling_window_s": sampling_window_s,
        "legal_analysis_cells": len(legal_mesh),
        "records": records,
        "input_validity_counts": dict(sorted(input_counts.items())),
        "excluded_outside_analysis_domain": excluded_outside_domain,
        "missing_cells": "not emitted and never imputed as zero velocity",
    }


def curl_z_field(velocity_field: Mapping[str, Any]) -> dict[str, Any]:
    """Use centred finite differences: d(vy)/dx - d(vx)/dy.

    Every one of the four cardinal velocity neighbours must be observed in the
    same window.  This intentionally returns NA at boundaries, walls, and
    data gaps rather than using a one-sided or zero-filled estimate.
    """

    if velocity_field.get("status") != "available":
        return {"status": velocity_field.get("status", "unavailable"), "reason": velocity_field.get("reason"), "records": []}
    mesh_size_m = _positive(velocity_field.get("analysis_mesh_size_m"), name="analysis_mesh_size_m")
    originals = velocity_field.get("records")
    if not isinstance(originals, list):
        raise ValueError("velocity field records must be a list")
    indexed = {
        (record["time_window"], record["analysis_x"], record["analysis_y"]): record
        for record in originals
        if isinstance(record, Mapping) and record.get("validity") == "valid_observed_samples"
    }
    records: list[dict[str, Any]] = []
    for key, record in sorted(indexed.items()):
        window, x, y = key
        east, west = indexed.get((window, x + 1, y)), indexed.get((window, x - 1, y))
        south, north = indexed.get((window, x, y + 1)), indexed.get((window, x, y - 1))
        output = dict(record)
        if not all((east, west, south, north)):
            output.update({"curl_z_s_inv": "NA", "validity": "insufficient_velocity_support"})
        else:
            curl = (
                (_finite(east["vy_m_s"], name="east.vy_m_s") - _finite(west["vy_m_s"], name="west.vy_m_s")) / (2.0 * mesh_size_m)
                - (_finite(south["vx_m_s"], name="south.vx_m_s") - _finite(north["vx_m_s"], name="north.vx_m_s")) / (2.0 * mesh_size_m)
            )
            output.update({"curl_z_s_inv": curl, "validity": "valid"})
        records.append(output)
    return {
        "status": "available",
        "method": "centred_difference_curl_z=(vy_east-vy_west)/(2h)-(vx_south-vx_north)/(2h)",
        "analysis_mesh_size_m": mesh_size_m,
        "records": records,
    }


def congestion_level_field(
    kinematic_rows: Iterable[Mapping[str, Any]],
    *,
    grid: Mapping[str, Any],
    analysis_contract: Mapping[str, Any],
) -> dict[str, Any]:
    """Calculate CL only from a same-window, same-ROI valid curl population."""

    velocity = analysis_mesh_velocity_field(
        kinematic_rows, grid=grid, analysis_contract=analysis_contract
    )
    settings = _congestion_settings(analysis_contract)
    if velocity.get("status") != "available":
        return {
            "status": velocity.get("status"), "reason": velocity.get("reason"),
            "analysis_contract": settings, "velocity_field": velocity, "records": [],
        }
    radius_m = _setting_value(settings.get("roi", {}) if isinstance(settings.get("roi"), Mapping) else {}, "radius_m")
    if radius_m in (None, ""):
        return {
            "status": "unconfigured_analysis_mesh_or_roi", "reason": "roi_radius_m is required",
            "analysis_contract": settings, "velocity_field": velocity, "records": [],
        }
    radius_m = _positive(radius_m, name="roi_radius_m")
    minimum_cells = _positive_int(_setting_value(settings, "minimum_valid_roi_cells"), name="minimum_valid_roi_cells")
    minimum_samples = _positive_int(_setting_value(settings, "minimum_velocity_samples"), name="minimum_velocity_samples")
    near_zero_speed = _positive(_setting_value(settings, "near_zero_mean_speed_m_s"), name="near_zero_mean_speed_m_s")
    curl = curl_z_field(velocity)
    curl_records = curl["records"]
    valid_by_window: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for record in curl_records:
        if record["validity"] == "valid" and int(record["sample_count"]) >= minimum_samples:
            valid_by_window[int(record["time_window"])].append(record)

    records: list[dict[str, Any]] = []
    for record in curl_records:
        output = dict(record)
        output["cl_m_inv"] = "NA"
        output["roi_definition"] = f"circular_radius_m={radius_m:g}"
        if record["validity"] != "valid":
            records.append(output)
            continue
        candidates = [
            other for other in valid_by_window[int(record["time_window"])]
            if math.hypot(
                (int(other["analysis_x"]) - int(record["analysis_x"])) * velocity["analysis_mesh_size_m"],
                (int(other["analysis_y"]) - int(record["analysis_y"])) * velocity["analysis_mesh_size_m"],
            ) <= radius_m + 1e-12
        ]
        if len(candidates) < minimum_cells:
            output["validity"] = "insufficient_roi_support"
            records.append(output)
            continue
        mean_speed = sum(_finite(other["speed_m_s"], name="speed_m_s") for other in candidates) / len(candidates)
        if mean_speed <= near_zero_speed:
            output["validity"] = "near_zero_mean_speed"
            records.append(output)
            continue
        curls = [_finite(other["curl_z_s_inv"], name="curl_z_s_inv") for other in candidates]
        output["cl_m_inv"] = (max(curls) - min(curls)) / mean_speed
        output["validity"] = "valid"
        records.append(output)
    return {
        "status": "available",
        "formula": "CL=(max_ROI(curl_z)-min_ROI(curl_z))/mean_ROI(speed)",
        "analysis_contract": settings,
        "velocity_field": {
            key: value for key, value in velocity.items() if key != "records"
        },
        "curl_method": curl["method"],
        "records": records,
    }


def write_congestion_level_field(
    *,
    kinematics_path: str | Path,
    grid: Mapping[str, Any],
    analysis_contract: Mapping[str, Any],
    json_path: str | Path,
    csv_path: str | Path,
) -> dict[str, Any]:
    """Persist a transparent CL artifact; unavailable states get header-only CSV."""

    with Path(kinematics_path).open("r", encoding="utf-8", newline="") as stream:
        field = congestion_level_field(
            csv.DictReader(stream), grid=grid, analysis_contract=analysis_contract
        )
    json_destination, csv_destination = Path(json_path), Path(csv_path)
    json_destination.write_text(json.dumps(field, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CONGESTION_LEVEL_FIELDS)
        writer.writeheader()
        writer.writerows(field["records"])
    return {
        "json_path": json_destination.name,
        "csv_path": csv_destination.name,
        "status": field["status"],
        "record_count": len(field["records"]),
        "reason": field.get("reason"),
    }
