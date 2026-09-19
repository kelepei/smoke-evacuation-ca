"""D-side explicit physical-scale contracts and auditable trajectory kinematics.

This foundation deliberately does not calculate density, CL, CN, or Crowd
Danger.  Legacy ``grid.cell_size`` is geometry only and is never interpreted
as a metre value.
"""
from __future__ import annotations

import csv
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

DEFAULT_SAMPLING_WINDOW_S = 2.5
KINEMATICS_FIELDS = ["person_id", "step", "time_s", "x", "y", "dx_cells", "dy_cells", "dt_s", "vx_cells_s", "vy_cells_s", "speed_cells_s", "vx_m_s", "vy_m_s", "speed_m_s", "validity"]

def _positive(value: Any, field: str) -> float:
    if isinstance(value, bool): raise ValueError(f"{field} must be a positive finite number")
    try: result = float(value)
    except (TypeError, ValueError) as exc: raise ValueError(f"{field} must be a positive finite number") from exc
    if not math.isfinite(result) or result <= 0: raise ValueError(f"{field} must be a positive finite number")
    return result

def resolve_analysis_contract(*, map_analysis: Mapping[str, Any] | None = None, runtime_physical_cell_size_m: Any = None, runtime_sampling_window_s: Any = None) -> dict[str, Any]:
    """Resolve only explicit calibrated scale; never infer it from grid.cell_size."""
    metadata = map_analysis or {}
    if not isinstance(metadata, Mapping): raise ValueError("map analysis metadata must be an object")
    raw_map_scale, raw_runtime_scale = metadata.get("physical_cell_size_m"), runtime_physical_cell_size_m
    map_scale = None if raw_map_scale in (None, "") else _positive(raw_map_scale, "analysis.physical_cell_size_m")
    runtime_scale = None if raw_runtime_scale in (None, "") else _positive(raw_runtime_scale, "runtime physical_cell_size_m")
    if map_scale is not None and runtime_scale is not None and not math.isclose(map_scale, runtime_scale, rel_tol=0, abs_tol=1e-12):
        raise ValueError("explicit map and runtime physical_cell_size_m disagree; choose one calibrated scale")
    if runtime_scale is not None: physical = {"source":"runtime_config", "value":runtime_scale, "unit":"m", "warning":None}
    elif map_scale is not None: physical = {"source":"explicit_map", "value":map_scale, "unit":"m", "warning":None}
    else: physical = {"source":"unavailable", "value":None, "unit":"m", "warning":"physical_cell_size_m was not explicitly supplied; legacy grid.cell_size is not interpreted as metres"}
    raw_map_window, raw_runtime_window = metadata.get("sampling_window_s"), runtime_sampling_window_s
    map_window = None if raw_map_window in (None, "") else _positive(raw_map_window, "analysis.sampling_window_s")
    runtime_window = None if raw_runtime_window in (None, "") else _positive(raw_runtime_window, "runtime sampling_window_s")
    if runtime_window is not None: window = {"source":"runtime_config", "value":runtime_window, "unit":"s", "note":"explicit run setting; retain for sensitivity analysis"}
    elif map_window is not None: window = {"source":"explicit_map", "value":map_window, "unit":"s", "note":"explicit map-analysis setting; retain for sensitivity analysis"}
    else: window = {"source":"literature_default", "value":DEFAULT_SAMPLING_WINDOW_S, "unit":"s", "note":"2018 literature reference only; not project-calibrated"}
    method = metadata.get("velocity_method") or "consecutive_valid_log_samples"
    if method != "consecutive_valid_log_samples": raise ValueError("analysis.velocity_method must be consecutive_valid_log_samples")
    return {"physical_scale":physical, "sampling_window_s":window, "velocity_method":method}

def with_runtime_dt(contract: Mapping[str, Any] | None, *, dt_s: Any) -> dict[str, Any]:
    result = dict(contract or resolve_analysis_contract())
    result["dt_s"] = {"source":"runtime_snapshot.time_step", "value":_positive(dt_s, "runtime dt_s"), "unit":"s"}
    return result

def _na(value: float | None) -> float | str: return "NA" if value is None else value
def _is_evacuated(value: Any) -> bool: return value is True or str(value).lower() == "true"
def _integer(value: Any) -> int:
    if isinstance(value, bool): raise ValueError
    result = int(value)
    if float(value) != result: raise ValueError
    return result
def _position_error(grid: Mapping[str, Any] | None, x: int, y: int) -> str | None:
    if not isinstance(grid, Mapping): return None
    width, height = grid.get("width"), grid.get("height")
    if not isinstance(width, int) or not isinstance(height, int): return None
    if not (0 <= x < width and 0 <= y < height): return "invalid_position_out_of_bounds"
    cells = grid.get("cell_type")
    if isinstance(cells, list) and y < len(cells) and isinstance(cells[y], list) and x < len(cells[y]) and str(cells[y][x]).lower() in {"wall", "obstacle"}: return "invalid_impassable_position"
    return None

def trajectory_kinematics(trajectory_rows: Iterable[Mapping[str, Any]], *, physical_scale: Mapping[str, Any] | None = None, grid: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Use observed consecutive records; invalid timestamps are retained, not repaired."""
    scale = None
    if isinstance(physical_scale, Mapping) and physical_scale.get("value") not in (None, ""): scale = _positive(physical_scale["value"], "physical_scale.value")
    grouped: dict[int, list[tuple[int, Mapping[str, Any]]]] = defaultdict(list)
    for index, raw in enumerate(trajectory_rows):
        try: grouped[_integer(raw.get("person_id"))].append((index, raw))
        except (ValueError, TypeError): continue
    results: list[dict[str, Any]] = []
    for person_id, items in sorted(grouped.items()):
        def order(item: tuple[int, Mapping[str, Any]]) -> tuple[int, int]:
            try: return (_integer(item[1].get("step")), item[0])
            except (ValueError, TypeError): return (10**12, item[0])
        previous: dict[str, Any] | None = None
        for _, raw in sorted(items, key=order):
            try:
                step, x, y = _integer(raw.get("step")), _integer(raw.get("x")), _integer(raw.get("y")); time_s = float(raw.get("time_s"))
                if not math.isfinite(time_s): raise ValueError
            except (ValueError, TypeError):
                results.append({"person_id":person_id,"step":raw.get("step"),"time_s":raw.get("time_s"),"x":raw.get("x"),"y":raw.get("y"), **{field:"NA" for field in KINEMATICS_FIELDS[5:-1]}, "validity":"invalid_required_field"}); continue
            base={"person_id":person_id,"step":step,"time_s":time_s,"x":x,"y":y}; evacuated=_is_evacuated(raw.get("evacuated"))
            if previous is not None and previous["evacuated"]:
                results.append({**base, **{field:"NA" for field in KINEMATICS_FIELDS[5:-1]}, "validity":"excluded_post_evacuation"}); continue
            error=_position_error(grid,x,y)
            if error:
                results.append({**base, **{field:"NA" for field in KINEMATICS_FIELDS[5:-1]}, "validity":error}); continue
            if previous is None:
                results.append({**base, **{field:"NA" for field in KINEMATICS_FIELDS[5:-1]}, "validity":"excluded_initial_evacuated" if evacuated else "initial_sample"}); previous={**base,"evacuated":evacuated}; continue
            dx,dy,dt=x-previous["x"],y-previous["y"],time_s-previous["time_s"]; gap=step-previous["step"]
            validity="invalid_nonincreasing_step" if gap <= 0 else "invalid_nonpositive_dt" if dt <= 0 else "flagged_teleport" if max(abs(dx),abs(dy)) > gap else "valid_to_exit" if evacuated else "valid_step_gap" if gap > 1 else "valid"
            if validity.startswith("invalid"):
                results.append({**base,"dx_cells":dx,"dy_cells":dy,"dt_s":_na(dt), **{field:"NA" for field in KINEMATICS_FIELDS[8:-1]},"validity":validity}); continue
            vx,vy=dx/dt,dy/dt; speed=math.hypot(vx,vy)
            results.append({**base,"dx_cells":dx,"dy_cells":dy,"dt_s":dt,"vx_cells_s":vx,"vy_cells_s":vy,"speed_cells_s":speed,"vx_m_s":_na(None if scale is None else vx*scale),"vy_m_s":_na(None if scale is None else vy*scale),"speed_m_s":_na(None if scale is None else speed*scale),"validity":validity})
            previous={**base,"evacuated":evacuated}
    return results

def write_trajectory_kinematics(*, people_log_path: str | Path, output_path: str | Path, analysis_contract: Mapping[str, Any] | None = None, grid: Mapping[str, Any] | None = None) -> dict[str, Any]:
    with Path(people_log_path).open(encoding="utf-8", newline="") as handle: rows = trajectory_kinematics(csv.DictReader(handle), physical_scale=(analysis_contract or resolve_analysis_contract()).get("physical_scale"), grid=grid)
    target=Path(output_path)
    with target.open("w",encoding="utf-8",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=KINEMATICS_FIELDS); writer.writeheader(); writer.writerows(rows)
    return {"path":target.name,"row_count":len(rows),"validity_counts":dict(sorted(Counter(row["validity"] for row in rows).items())),"analysis_contract":dict(analysis_contract or resolve_analysis_contract())}
