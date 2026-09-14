"""D-side physical-scale contracts and trajectory kinematics.

This module deliberately stops before congestion metrics.  It turns the
normalized D people log into auditable cell-space velocities and exposes
metric units only when a map or explicit runtime configuration provides a
validated physical cell size.  The legacy ``grid.cell_size`` is never used as
a physical length because current maps use it with incompatible meanings.
"""

from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_SAMPLING_WINDOW_S = 2.5
KINEMATICS_FIELDS = [
    "person_id", "step", "time_s", "x", "y", "dx_cells", "dy_cells",
    "dt_s", "vx_cells_s", "vy_cells_s", "speed_cells_s", "vx_m_s",
    "vy_m_s", "speed_m_s", "validity",
]


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
    """Resolve explicit-only physical and sampling configuration.

    Runtime configuration may be used for a controlled run.  If a map also
    declares a different physical scale, the run is rejected instead of
    silently selecting one of two incompatible unit systems.
    """

    map_analysis = map_analysis or {}
    if not isinstance(map_analysis, Mapping):
        raise ValueError("map analysis metadata must be an object")

    map_scale_raw = map_analysis.get("physical_cell_size_m")
    map_scale = (
        None if map_scale_raw in (None, "")
        else _positive_finite(map_scale_raw, field="analysis.physical_cell_size_m")
    )
    runtime_scale = (
        None if runtime_physical_cell_size_m in (None, "")
        else _positive_finite(
            runtime_physical_cell_size_m,
            field="runtime physical_cell_size_m",
        )
    )
    if map_scale is not None and runtime_scale is not None and not math.isclose(
        map_scale, runtime_scale, rel_tol=0.0, abs_tol=1e-12
    ):
        raise ValueError(
            "explicit map and runtime physical_cell_size_m disagree; "
            "choose one calibrated scale before calculating physical metrics"
        )
    if runtime_scale is not None:
        physical_scale = {
            "source": "runtime_config",
            "value": runtime_scale,
            "unit": "m",
            "warning": None,
        }
    elif map_scale is not None:
        physical_scale = {
            "source": "explicit_map",
            "value": map_scale,
            "unit": "m",
            "warning": None,
        }
    else:
        physical_scale = {
            "source": "unavailable",
            "value": None,
            "unit": "m",
            "warning": (
                "physical_cell_size_m was not explicitly supplied; "
                "legacy grid.cell_size is not interpreted as metres"
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
    }


def with_runtime_dt(
    contract: Mapping[str, Any] | None, *, dt_s: Any
) -> dict[str, Any]:
    """Attach the runtime's declared dt without inventing a fixed value."""

    dt = _positive_finite(dt_s, field="runtime dt_s")
    result = dict(contract or resolve_analysis_contract())
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
