"""Unified, physical-scale-gated academic crowd-analysis fields for D.

The artifact deliberately keeps cumulative occupancy outside this module:
occupancy is a person-step history, whereas density, CL, CN, and Crowd Danger
are same-window analysis-mesh measurements with separate validity states.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from experiments.congestion_level import congestion_level_field
from experiments.crowd_metrics import trajectory_kinematics, voronoi_density_for_frame


ACADEMIC_CROWD_FIELDS = [
    "window_start_s", "window_end_s", "analysis_x", "analysis_y",
    "density_persons_m2", "vx_m_s", "vy_m_s", "speed_m_s", "curl_z_s_inv",
    "cl_m_inv", "cn", "crowd_danger_persons_m3", "sample_count",
    "density_validity", "cl_validity", "cn_validity", "crowd_danger_validity",
    "analysis_mesh_size_m", "sampling_window_s", "roi_definition",
]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, "", "NA"):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if not isinstance(value, float) or value == parsed else None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _physical_scale(contract: Mapping[str, Any]) -> float | None:
    scale = contract.get("physical_scale")
    if not isinstance(scale, Mapping) or scale.get("source") not in {"explicit_map", "runtime_config"}:
        return None
    value = _finite(scale.get("value"))
    return value if value is not None and value > 0 else None


def _mesh_and_window(contract: Mapping[str, Any]) -> tuple[float | None, float | None, str]:
    congestion = contract.get("congestion_level")
    if not isinstance(congestion, Mapping):
        return None, None, "unconfigured_analysis_mesh_or_roi"
    mesh = congestion.get("analysis_mesh_size_m")
    mesh_value = _finite(mesh.get("value")) if isinstance(mesh, Mapping) else None
    sampling = contract.get("sampling_window_s")
    window_value = _finite(sampling.get("value")) if isinstance(sampling, Mapping) else None
    if mesh_value is None or mesh_value <= 0 or window_value is None or window_value <= 0:
        return None, None, "unconfigured_analysis_mesh_or_roi"
    return mesh_value, window_value, "available"


def _empty(status: str, reason: str, *, contract: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "reference_method": {
            "density": "Voronoi local density on legal pedestrian domain",
            "congestion_level": "2018 velocity curl range / mean ROI speed",
            "congestion_number": "CN≈CL×analysis_mesh_size_m/6",
            "crowd_danger": "Crowd Danger=CL×local_density",
        },
        "analysis_contract": dict(contract),
        "records": [],
    }


def _density_by_analysis_cell(
    people_rows: Iterable[Mapping[str, Any]],
    *,
    grid: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> tuple[dict[tuple[int, int, int], dict[str, Any]], dict[str, Any]]:
    """Aggregate per-frame Voronoi samples into the same mesh/time bins as CL."""

    scale_m = _physical_scale(contract)
    mesh_m, window_s, state = _mesh_and_window(contract)
    if scale_m is None:
        return {}, {"status": "unavailable_physical_scale", "reason": "formal density requires explicit physical_cell_size_m"}
    if state != "available" or mesh_m is None or window_s is None:
        return {}, {"status": state, "reason": "analysis_mesh_size_m and sampling_window_s are required"}
    frames: dict[tuple[int, float], list[dict[str, Any]]] = defaultdict(list)
    for raw in people_rows:
        step, time_s, person_id = _integer(raw.get("step")), _finite(raw.get("time_s")), _integer(raw.get("person_id"))
        x, y = _integer(raw.get("x")), _integer(raw.get("y"))
        if None in {step, time_s, person_id, x, y} or _truthy(raw.get("evacuated")):
            continue
        frames[(step, float(time_s))].append({"person_id": person_id, "x": x, "y": y})
    values: dict[tuple[int, int, int], list[float]] = defaultdict(list)
    skipped_frames = 0
    for (_, time_s), positions in frames.items():
        try:
            frame = voronoi_density_for_frame(
                positions, grid=grid, physical_scale=contract.get("physical_scale"), time_s=time_s
            )
        except ValueError:
            skipped_frames += 1
            continue
        if frame.get("status") != "available":
            continue
        window = math.floor(time_s / window_s)
        for sample in frame.get("samples", []):
            density, x, y = _finite(sample.get("density_persons_m2")), _integer(sample.get("x")), _integer(sample.get("y"))
            if density is None or x is None or y is None:
                continue
            analysis_x = math.floor(((x + 0.5) * scale_m) / mesh_m)
            analysis_y = math.floor(((y + 0.5) * scale_m) / mesh_m)
            values[(window, analysis_x, analysis_y)].append(density)
    records = {
        key: {
            "density_persons_m2": sum(samples) / len(samples),
            "density_sample_count": len(samples),
            "density_validity": "valid",
        }
        for key, samples in values.items()
    }
    return records, {"status": "available", "skipped_invalid_frames": skipped_frames}


def academic_crowd_fields(
    people_rows: Iterable[Mapping[str, Any]],
    *,
    grid: Mapping[str, Any],
    analysis_contract: Mapping[str, Any],
    kinematic_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build one joined analysis frame for Density, CL, CN, and Crowd Danger."""

    people_rows = list(people_rows)
    if _physical_scale(analysis_contract) is None:
        return _empty(
            "unavailable_physical_scale",
            "physical scale not calibrated: Density, CL, CN, and Crowd Danger are unavailable",
            contract=analysis_contract,
        )
    mesh_m, window_s, state = _mesh_and_window(analysis_contract)
    if state != "available" or mesh_m is None or window_s is None:
        return _empty(state, "analysis mesh or sampling window is not configured", contract=analysis_contract)
    kinematics = list(kinematic_rows) if kinematic_rows is not None else trajectory_kinematics(
        people_rows, physical_scale=analysis_contract.get("physical_scale"), grid=grid
    )
    cl_field = congestion_level_field(kinematics, grid=grid, analysis_contract=analysis_contract)
    if cl_field.get("status") != "available":
        return _empty(str(cl_field.get("status")), str(cl_field.get("reason") or "CL unavailable"), contract=analysis_contract)
    density, density_meta = _density_by_analysis_cell(
        people_rows, grid=grid, contract=analysis_contract
    )
    congestion = analysis_contract.get("congestion_level", {})
    roi = congestion.get("roi", {}) if isinstance(congestion, Mapping) else {}
    radius = roi.get("radius_m", {}) if isinstance(roi, Mapping) else {}
    radius_value = _finite(radius.get("value")) if isinstance(radius, Mapping) else None
    roi_definition = f"circular_radius_m={radius_value:g}" if radius_value is not None else "NA"
    records: list[dict[str, Any]] = []
    for cl_record in cl_field.get("records", []):
        window, x, y = _integer(cl_record.get("time_window")), _integer(cl_record.get("analysis_x")), _integer(cl_record.get("analysis_y"))
        if None in {window, x, y}:
            continue
        key = (window, x, y)
        density_record = density.get(key)
        cl_valid = str(cl_record.get("validity", "invalid_cl"))
        cl_value = _finite(cl_record.get("cl_m_inv")) if cl_valid == "valid" else None
        density_value = _finite(density_record.get("density_persons_m2")) if density_record else None
        density_validity = density_record["density_validity"] if density_record else "insufficient_density_support"
        cn_value: float | str = "NA"
        cn_validity = "invalid_cl"
        if cl_value is not None:
            cn_value = cl_value * mesh_m / 6.0
            cn_validity = "valid"
        crowd_danger: float | str = "NA"
        crowd_danger_validity = "invalid_cl" if cl_value is None else "insufficient_density_support"
        if cl_value is not None and density_value is not None and density_validity == "valid":
            crowd_danger = cl_value * density_value
            crowd_danger_validity = "valid"
        records.append({
            "window_start_s": cl_record.get("window_start_s"),
            "window_end_s": cl_record.get("window_end_s"),
            "analysis_x": x, "analysis_y": y,
            "density_persons_m2": density_value if density_value is not None else "NA",
            "vx_m_s": cl_record.get("vx_m_s", "NA"),
            "vy_m_s": cl_record.get("vy_m_s", "NA"),
            "speed_m_s": cl_record.get("speed_m_s", "NA"),
            "curl_z_s_inv": cl_record.get("curl_z_s_inv", "NA"),
            "cl_m_inv": cl_value if cl_value is not None else "NA",
            "cn": cn_value,
            "crowd_danger_persons_m3": crowd_danger,
            "sample_count": cl_record.get("sample_count", 0),
            "density_validity": density_validity,
            "cl_validity": cl_valid,
            "cn_validity": cn_validity,
            "crowd_danger_validity": crowd_danger_validity,
            "analysis_mesh_size_m": mesh_m,
            "sampling_window_s": window_s,
            "roi_definition": roi_definition,
        })
    return {
        "status": "available",
        "formula": {
            "cn": "CN≈CL×analysis_mesh_size_m/6 (dimensionless)",
            "crowd_danger": "Crowd Danger=CL×local_density (persons/m³)",
        },
        "units": {"density": "persons/m²", "cl": "m⁻¹", "cn": "dimensionless", "crowd_danger": "persons/m³"},
        "reference_method": {
            "congestion_number": "2023 operational definition using analysis mesh size R",
            "crowd_danger": "2018 CL × local Voronoi density",
        },
        "analysis_contract": dict(analysis_contract),
        "density_metadata": density_meta,
        "records": records,
    }


def write_academic_crowd_fields(
    *,
    people_log_path: str | Path,
    grid: Mapping[str, Any],
    analysis_contract: Mapping[str, Any],
    json_path: str | Path,
    csv_path: str | Path,
) -> dict[str, Any]:
    with Path(people_log_path).open("r", encoding="utf-8", newline="") as stream:
        field = academic_crowd_fields(
            csv.DictReader(stream), grid=grid, analysis_contract=analysis_contract
        )
    json_destination, csv_destination = Path(json_path), Path(csv_path)
    json_destination.write_text(json.dumps(field, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=ACADEMIC_CROWD_FIELDS)
        writer.writeheader()
        writer.writerows(field["records"])
    return {
        "json_path": json_destination.name,
        "csv_path": csv_destination.name,
        "status": field["status"],
        "record_count": len(field["records"]),
        "reason": field.get("reason"),
    }
