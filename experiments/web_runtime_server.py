"""Local D-side web bridge for the integrated A + B + C runtime.

The browser cannot import Python modules directly.  This intentionally small
standard-library HTTP service lets the D HTML page upload contract-compliant
A map and C population files, run B's existing CA through
``integrated_runner``, and receive normalized snapshots one step at a time.

It owns no A/B/C logic and does not modify their files.  It is a local-only
development bridge (127.0.0.1), not a production deployment server.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import csv
import gzip
import json
import mimetypes
import shutil
import tempfile
import threading
import math
from datetime import datetime
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from experiments.auto_positioning import AutoPositioningError, allocate_map_data_positions
from experiments.academic_crowd import academic_crowd_fields
from experiments.scene_config_pipeline import (
    SceneConfigPipelineError,
    canonical_from_c_scene_config,
    generate_positioned_population,
    validate_canonical_scene_config,
)
from experiments.experiment_history import ExperimentHistoryError, discover_experiments, load_experiment_detail
from experiments.guidance_interface import warm_guidance_route_cache
from experiments.integrated_runner import create_integrated_runner
from experiments.lambda_experiment import lambda_capability
from experiments.platform_physics import (
    PLATFORM_PHYSICAL_CELL_SIZE_M,
    PLATFORM_PHYSICAL_SCALE_SOURCE,
    platform_physical_cell_size,
)
from experiments.result_package import (
    ResultPackageError,
    build_lambda_experiment_csv,
    build_result_package,
    build_runtime_analysis,
)
from experiments.run_artifacts import write_run_artifacts
from visualization.scene_input_adapter import grid_to_static_snapshot, load_map_grid


# A maps are sent through the local browser bridge as JSON. Keep a bounded
# limit while accepting the supplied 25.7 MB large-map JSON.
MAX_REQUEST_BYTES = 64 * 1024 * 1024
# A's public image loader accepts these raster formats.  D transports them
# unchanged to that loader rather than interpreting image content itself.
ALLOWED_MAP_SUFFIXES = {".json", ".csv", ".png", ".jpg", ".jpeg", ".bmp"}
ALLOWED_YAML_SUFFIXES = {".yaml", ".yml"}
STANDARD_TEMPLATE_IDS = ("classroom", "mall", "canteen", "dormitory")


def _runtime_temp_directory(root: Path, prefix: str) -> tempfile.TemporaryDirectory[str]:
    """Keep uploaded inputs in an ASCII path for A's Windows OpenCV loader."""

    temp_root = root / "outputs" / ".d_runtime_tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=str(temp_root))


class WebRuntimeError(ValueError):
    """A user-facing validation error from the local HTML bridge."""


@dataclass
class RuntimeSession:
    """One local run and the temporary input files that support it."""

    temporary_directory: tempfile.TemporaryDirectory[str]
    runner: Any
    input_files: dict[str, Path]
    max_steps: int

    def close(self) -> None:
        self.runner.close()
        self.temporary_directory.cleanup()


def _termination_state(
    snapshot: Mapping[str, Any], *, finished: bool, max_steps: int
) -> dict[str, Any]:
    """Describe why a run stopped without changing B's completion rules."""

    raw_people = snapshot.get("people")
    people = raw_people if isinstance(raw_people, list) else []
    evacuated_count = sum(
        1
        for person in people
        if isinstance(person, Mapping) and person.get("evacuated") is True
    )
    dead_count = sum(
        1
        for person in people
        if isinstance(person, Mapping)
        and person.get("evacuated") is not True
        and person.get("is_dead") is True
    )
    total_count = len(people)
    unfinished_count = max(0, total_count - evacuated_count - dead_count)
    step = int(snapshot.get("step") or 0)

    reason = "running"
    if finished:
        if total_count > 0 and evacuated_count == total_count:
            reason = "all_evacuated"
        elif total_count > 0 and unfinished_count == 0:
            reason = "all_evacuated_or_dead"
        elif step >= max_steps:
            reason = "max_steps_reached"
        else:
            reason = "upstream_finished"

    return {
        "finished": bool(finished),
        "reason": reason,
        "step": step,
        "max_steps": int(max_steps),
        "total_count": total_count,
        "evacuated_count": evacuated_count,
        "dead_count": dead_count,
        "unfinished_count": unfinished_count,
    }


class DWebRuntimeServer(ThreadingHTTPServer):
    """Local server state; only one active run is needed for the MVP UI."""

    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[SimpleHTTPRequestHandler], *, root: Path) -> None:
        super().__init__(address, handler)
        self.root = root.resolve()
        self.session: RuntimeSession | None = None
        self.academic_cache: dict[tuple[str, int, str], dict[str, Any]] = {}
        # A B step can be much slower than browser timer events.  Serialize
        # all mutations so reset/close cannot replace a live runner mid-step.
        self.session_lock = threading.RLock()
        self.academic_cache_lock = threading.Lock()

    def close_session(self) -> None:
        with self.session_lock:
            if self.session is not None:
                self.session.close()
            self.session = None


def _safe_suffix(filename: Any, allowed: set[str], field: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in allowed:
        choices = ", ".join(sorted(allowed))
        raise WebRuntimeError(f"{field} must use one of: {choices}")
    return suffix


def _control_runtime_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    # This endpoint is the Enhanced Web Runtime, so opt into C's documented
    # source-driven Web semantics.  CWebRuntimeBridge owns the algorithm;
    # D only makes the runtime mode explicit instead of relying on an absent
    # optional payload key.
    config: dict[str, Any] = {"web_mode": True}
    movement = payload.get("movement")
    if movement is not None:
        if not isinstance(movement, Mapping):
            raise WebRuntimeError("movement must be an object")
        config["movement"] = {
            str(section): dict(values) if isinstance(values, Mapping) else values
            for section, values in movement.items()
        }
    information_mode = str(payload.get("information_mode", "broadcast") or "broadcast").strip().lower()
    if information_mode not in {"broadcast", "local"}:
        raise WebRuntimeError("information_mode must be 'broadcast' or 'local'")
    # Both modes retain C's smoke perception, word-of-mouth and relation
    # engines. This choice only controls C's real global alarm broadcast.
    config["information_mode"] = information_mode
    config["alarm_enabled"] = information_mode == "broadcast"
    for key in ("smoke_exposure_threshold", "preference_bonus", "broadcast_smoke_threshold"):
        if key not in payload:
            continue
        try:
            value = float(payload.get(key))
        except (TypeError, ValueError) as exc:
            raise WebRuntimeError(f"{key} must be numeric") from exc
        if not math.isfinite(value) or value < 0.0 or value > 1.0:
            raise WebRuntimeError(f"{key} must be between 0 and 1")
        config[key] = value
    unknown_behavior = str(
        payload.get("unknown_behavior", "evacuate") or "evacuate"
    ).strip().lower()
    if unknown_behavior not in {"evacuate", "walk", "freeze"}:
        raise WebRuntimeError("unknown_behavior must be evacuate, walk, or freeze")
    config["unknown_behavior"] = unknown_behavior
    strategy = str(payload.get("control_strategy", "none") or "none").strip().lower()
    if strategy:
        config["control_strategy"] = strategy
    if "control_trigger_step" in payload:
        config["control_trigger_step"] = int(payload.get("control_trigger_step") or 10)
    closed = payload.get("control_closed_exits")
    if isinstance(closed, str):
        closed = [item.strip() for item in closed.split(",") if item.strip()]
    if isinstance(closed, (list, tuple)):
        config["control_closed_exits"] = [str(item) for item in closed]
    area = payload.get("control_lockdown_area")
    if isinstance(area, str):
        area = [item.strip() for item in area.split(",") if item.strip()]
    if isinstance(area, (list, tuple)) and len(area) == 4:
        try:
            config["control_lockdown_area"] = [int(item) for item in area]
        except (TypeError, ValueError):
            pass
    if "route_shift_ratio" in payload:
        config["route_shift_ratio"] = float(payload.get("route_shift_ratio") or 0.2)
    from experiments.lambda_experiment import LambdaExperimentError, lambda_request_config

    try:
        lambda_movement = lambda_request_config(payload)
    except LambdaExperimentError as exc:
        raise WebRuntimeError(str(exc)) from exc
    if lambda_movement is not None:
        merged = dict(config.get("movement") or {})
        for section, values in lambda_movement.items():
            section_values = dict(merged.get(section) or {})
            section_values.update(values)
            merged[section] = section_values
        config["movement"] = merged
    return config


def _uploaded_file(payload: Mapping[str, Any], field: str, allowed: set[str], root: Path) -> Path | None:
    raw = payload.get(field)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WebRuntimeError(f"{field} must be a file object")
    filename = raw.get("name")
    suffix = _safe_suffix(filename, allowed, field)
    target = root / f"{field}{suffix}"
    text = raw.get("text")
    if isinstance(text, str):
        target.write_text(text, encoding="utf-8")
        return target
    encoded = raw.get("data_url", raw.get("base64"))
    if isinstance(encoded, str):
        if "," in encoded:
            encoded = encoded.split(",", 1)[1]
        try:
            target.write_bytes(base64.b64decode(encoded, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise WebRuntimeError(f"{field} must contain valid base64 data") from exc
        return target
    raise WebRuntimeError(f"{field} must contain text or base64/data_url")


def _map_input_file(payload: Mapping[str, Any], root: Path) -> Path | None:
    """Materialize one canonical edited JSON map for a preview/session."""

    map_data = payload.get("map_data")
    if map_data is None:
        return _uploaded_file(payload, "map_file", ALLOWED_MAP_SUFFIXES, root)
    if not isinstance(map_data, Mapping):
        raise WebRuntimeError("map_data must be a JSON object")
    target = root / "current_map.json"
    target.write_text(json.dumps(map_data, ensure_ascii=False), encoding="utf-8")
    return target


def _json_object(path: Path, field: str) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebRuntimeError(f"{field} must be valid UTF-8 JSON") from exc
    if not isinstance(parsed, Mapping):
        raise WebRuntimeError(f"{field} must be a JSON object")
    return dict(parsed)


def _new_run_id(prefix: str = "d_web_runtime") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


def _force_headless_matplotlib() -> None:
    """Keep B's Matplotlib-backed runtime usable inside the local web server."""

    try:
        import matplotlib

        matplotlib.use("Agg", force=True)
    except Exception:
        return


def _positive_steps(value: Any) -> int:
    if value is None:
        return 500
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5000:
        raise WebRuntimeError("max_steps must be an integer between 1 and 5000")
    return value


def _optional_seed(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise WebRuntimeError("random_seed must be an integer or empty")
    return value


def _positive_time_step(value: Any) -> float:
    if value is None:
        return 0.5
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WebRuntimeError("time_step_s must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or not 0 < normalized <= 60:
        raise WebRuntimeError("time_step_s must be greater than 0 and at most 60")
    return normalized


def _platform_physical_cell_size(value: Any) -> float:
    try:
        return platform_physical_cell_size(value)
    except ValueError as exc:
        raise WebRuntimeError(str(exc)) from exc


# Compatibility name used by existing callers; the value is now always fixed.
_optional_physical_cell_size = _platform_physical_cell_size


def _optional_positive_analysis_value(value: Any, field: str) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WebRuntimeError(f"{field} must be numeric or empty")
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise WebRuntimeError(f"{field} must be greater than 0")
    return normalized


class RuntimeRequestHandler(SimpleHTTPRequestHandler):
    """Serve the D HTML and a minimal JSON API from one localhost origin."""

    server: DWebRuntimeServer

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # ``BaseHTTPRequestHandler`` assigns ``self.server`` during its own
        # constructor, so use the server argument directly at this point.
        server = args[2]
        super().__init__(*args, directory=str(server.root), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Keep the user-facing terminal readable while still showing requests.
        print("[D web runtime] " + format % args)

    def do_OPTIONS(self) -> None:  # noqa: N802 - required HTTP handler name
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - required HTTP handler name
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/api/health":
            self._send_json({"ok": True, "service": "d_web_runtime", "version": "0.1"})
            return
        if path == "/api/capabilities":
            self._send_json({"ok": True, "lambda_experiment": lambda_capability()})
            return
        if path == "/api/experiments":
            self._send_json({"ok": True, "experiments": discover_experiments(self._history_roots())})
            return
        if path.startswith("/api/experiments/"):
            try:
                experiment_id = unquote(path.removeprefix("/api/experiments/"))
                self._send_json({"ok": True, "experiment": load_experiment_detail(experiment_id, self._history_roots())})
            except ExperimentHistoryError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if path == "/api/session/export":
            try:
                self._export_session()
            except WebRuntimeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"export error: {exc}")
            return
        if path == "/api/session/lambda.csv":
            try:
                self._export_lambda_csv()
            except WebRuntimeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"lambda CSV error: {exc}")
            return
        if path == "/api/session/academic-analysis":
            try:
                self._send_json(self._academic_analysis())
            except WebRuntimeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"academic analysis error: {exc}")
            return
        if path in {
            "/api/session/analysis-base",
            "/api/session/metrics",
            "/api/session/analysis",
            "/api/session/layers",
        }:
            try:
                self._send_json(
                    self._runtime_analysis(
                        include_figures=path.endswith("/analysis")
                        or path.endswith("/analysis-base"),
                        include_layers=path.endswith("/analysis")
                        or path.endswith("/analysis-base")
                        or path.endswith("/layers"),
                        include_academic=path.endswith("/analysis"),
                    )
                )
            except WebRuntimeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"analysis error: {exc}")
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - required HTTP handler name
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path in {"/api/map/preview", "/api/map/preview-data"}:
                response = self._preview_map(payload)
            elif path == "/api/template/preview":
                response = self._preview_template(payload)
            else:
                with self.server.session_lock:
                    if path == "/api/session":
                        response = self._create_session(payload)
                    elif path == "/api/session/generate-people":
                        response = self._create_generated_session(payload)
                    elif path == "/api/session/auto-position":
                        response = self._create_auto_position_session(payload)
                    elif path == "/api/session/template":
                        response = self._create_template_session(payload)
                    elif path == "/api/session/sample":
                        response = self._create_repository_sample(payload)
                    elif path == "/api/session/step":
                        response = self._step_session()
                    elif path == "/api/session/broadcast":
                        response = self._set_broadcast_enabled(payload)
                    elif path == "/api/session/reset":
                        response = self._reset_session()
                    elif path == "/api/session/close":
                        self.server.close_session()
                        response = {"ok": True}
                    else:
                        self._send_error_json(HTTPStatus.NOT_FOUND, "unknown API route")
                        return
            self._send_json(response)
        except WebRuntimeError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # keep browser errors understandable without hiding developer traceback
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"runtime error: {exc}")

    def _read_json(self) -> Mapping[str, Any]:
        length_header = self.headers.get("Content-Length")
        try:
            length = int(length_header or "0")
        except ValueError as exc:
            raise WebRuntimeError("invalid Content-Length") from exc
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise WebRuntimeError(f"request must be between 1 byte and {MAX_REQUEST_BYTES} bytes")
        try:
            parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WebRuntimeError("request must contain UTF-8 JSON") from exc
        if not isinstance(parsed, Mapping):
            raise WebRuntimeError("request JSON must be an object")
        return parsed

    def _create_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        self.server.close_session()
        temporary_directory = _runtime_temp_directory(
            self.server.root, "d_web_runtime_"
        )
        root = Path(temporary_directory.name)
        try:
            map_path = _map_input_file(payload, root)
            people_path = _uploaded_file(payload, "population_file", {".json"}, root)
            yaml_path = _uploaded_file(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
            if map_path is None or people_path is None:
                raise WebRuntimeError("map_data or map_file, and population_file, are required")
            control_config = _control_runtime_config(payload)
            return self._start_runner(
                temporary_directory,
                map_path=map_path,
                people_path=people_path,
                yaml_path=yaml_path,
                max_steps=_positive_steps(payload.get("max_steps")),
                random_seed=_optional_seed(payload.get("random_seed")),
                time_step_s=_positive_time_step(payload.get("time_step_s")),
                physical_cell_size_m=_optional_physical_cell_size(payload.get("physical_cell_size_m")),
                sampling_window_s=_optional_positive_analysis_value(payload.get("sampling_window_s"), "sampling_window_s"),
                analysis_mesh_size_m=_optional_positive_analysis_value(payload.get("analysis_mesh_size_m"), "analysis_mesh_size_m"),
                roi_radius_m=_optional_positive_analysis_value(payload.get("roi_radius_m"), "roi_radius_m"),
                c_runtime_config=control_config,
            )
        except Exception:
            temporary_directory.cleanup()
            raise

    def _create_generated_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Generate C people/relations and A positions from one canonical UI config."""
        self.server.close_session()
        temporary_directory = _runtime_temp_directory(self.server.root, "d_web_generated_")
        root = Path(temporary_directory.name)
        try:
            map_path = _map_input_file(payload, root)
            if map_path is None or map_path.suffix.lower() != ".json":
                raise WebRuntimeError("直接生成人员需要当前 JSON 地图")
            yaml_path = _uploaded_file(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
            if yaml_path is not None:
                from control.scene_config import SceneConfigGenerator
                canonical = canonical_from_c_scene_config(SceneConfigGenerator.load_config_from_yaml(str(yaml_path)))
                config_source = "yaml"
            else:
                raw_config = payload.get("scene_config")
                try:
                    canonical = validate_canonical_scene_config(raw_config if isinstance(raw_config, Mapping) else {})
                except SceneConfigPipelineError as exc:
                    raise WebRuntimeError(str(exc)) from exc
                config_source = "ui"
            generated = generate_positioned_population(
                scene_config=canonical, map_path=map_path, destination=root / "generated"
            )
            runtime_config = {**canonical, **_control_runtime_config(payload)}
            config_path = root / "scene_config.json"
            config_path.write_text(json.dumps({"config_source": config_source, **canonical}, ensure_ascii=False, indent=2), encoding="utf-8")
            result = self._start_runner(
                temporary_directory,
                map_path=map_path,
                people_path=generated["population_path"],
                yaml_path=None,
                max_steps=_positive_steps(payload.get("max_steps")),
                random_seed=canonical["random_seed"],
                time_step_s=_positive_time_step(payload.get("time_step_s")),
                physical_cell_size_m=_optional_physical_cell_size(payload.get("physical_cell_size_m")),
                sampling_window_s=_optional_positive_analysis_value(payload.get("sampling_window_s"), "sampling_window_s"),
                analysis_mesh_size_m=_optional_positive_analysis_value(payload.get("analysis_mesh_size_m"), "analysis_mesh_size_m"),
                roi_radius_m=_optional_positive_analysis_value(payload.get("roi_radius_m"), "roi_radius_m"),
                c_runtime_config=runtime_config,
            )
            result["initialization"] = {
                "config_source": config_source,
                "person_count": generated["person_count"],
                "profile_counts": generated["profile_counts"],
                "canonical_config": canonical,
                "spawn_area": generated["spawn_area"],
            }
            if self.server.session is not None:
                self.server.session.input_files["scene_config"] = config_path
                self._write_run_metadata(self.server.session.runner, self.server.session.runner.current_snapshot, self.server.session.input_files, save_frame=True)
            return result
        except SceneConfigPipelineError as exc:
            temporary_directory.cleanup()
            raise WebRuntimeError(str(exc)) from exc
        except Exception:
            temporary_directory.cleanup()
            raise

    def _create_auto_position_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Allocate on the same edited JSON map that B will subsequently run."""

        self.server.close_session()
        temporary_directory = _runtime_temp_directory(self.server.root, "d_web_auto_position_")
        root = Path(temporary_directory.name)
        try:
            map_path = _map_input_file(payload, root)
            people_path = _uploaded_file(payload, "population_file", {".json"}, root)
            yaml_path = _uploaded_file(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
            if map_path is None or people_path is None:
                raise WebRuntimeError("automatic position allocation requires map_data and population_file")
            if map_path.suffix.lower() != ".json":
                raise WebRuntimeError("automatic position allocation currently supports JSON maps only")
            random_seed = _optional_seed(payload.get("random_seed"))
            people_data = _json_object(people_path, "population_file")
            map_data = _json_object(map_path, "map_data")
            try:
                allocation = allocate_map_data_positions(
                    map_data=map_data, people_data=people_data, random_seed=random_seed
                )
            except AutoPositioningError as exc:
                raise WebRuntimeError(str(exc)) from exc
            people_path.write_text(json.dumps(people_data, ensure_ascii=False), encoding="utf-8")
            control_config = _control_runtime_config(payload)
            result = self._start_runner(
                temporary_directory,
                map_path=map_path,
                people_path=people_path,
                yaml_path=yaml_path,
                max_steps=_positive_steps(payload.get("max_steps")),
                random_seed=random_seed,
                time_step_s=_positive_time_step(payload.get("time_step_s")),
                physical_cell_size_m=_optional_physical_cell_size(payload.get("physical_cell_size_m")),
                sampling_window_s=_optional_positive_analysis_value(payload.get("sampling_window_s"), "sampling_window_s"),
                analysis_mesh_size_m=_optional_positive_analysis_value(payload.get("analysis_mesh_size_m"), "analysis_mesh_size_m"),
                roi_radius_m=_optional_positive_analysis_value(payload.get("roi_radius_m"), "roi_radius_m"),
                c_runtime_config=control_config,
            )
            result["auto_positioning"] = allocation
            return result
        except Exception:
            temporary_directory.cleanup()
            raise

    def _preview_map(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        temporary_directory = _runtime_temp_directory(
            self.server.root, "d_map_preview_"
        )
        root = Path(temporary_directory.name)
        try:
            map_path = _map_input_file(payload, root)
            if map_path is None:
                raise WebRuntimeError("map_data or map_file is required")
            grid = load_map_grid(map_path)
            snapshot = grid_to_static_snapshot(
                grid,
                run_id="d_map_preview",
                scenario_id=Path(str(payload.get("source_name") or map_path.stem)).stem,
            )
            cells = snapshot["grid"]["cell_type"]
            counts: dict[str, int] = {}
            for row in cells:
                for cell_type in row:
                    key = str(cell_type)
                    counts[key] = counts.get(key, 0) + 1
            return {
                "ok": True,
                "snapshot": snapshot,
                "map_meta": {
                    "source": str(payload.get("source_name") or map_path.name),
                    "width": snapshot["grid"]["width"],
                    "height": snapshot["grid"]["height"],
                    "cell_counts": counts,
                    "loader": "A JSON/CSV/PNG loader -> Grid",
                },
            }
        finally:
            temporary_directory.cleanup()

    def _standard_template_path(self, template_id: Any) -> Path:
        """Return an A-provided standard template without a new A interface."""

        normalized = str(template_id or "").strip().lower()
        if normalized not in STANDARD_TEMPLATE_IDS:
            choices = ", ".join(STANDARD_TEMPLATE_IDS)
            raise WebRuntimeError(f"template_id must be one of: {choices}")
        path = self.server.root / "maps" / "templates" / f"{normalized}.json"
        if not path.is_file():
            raise WebRuntimeError(f"A standard template is unavailable: {normalized}")
        return path

    def _preview_template(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        template_id = str(payload.get("template_id") or "").strip().lower()
        map_path = self._standard_template_path(template_id)
        grid = load_map_grid(map_path)
        snapshot = grid_to_static_snapshot(
            grid,
            run_id="d_template_preview",
            scenario_id=template_id,
        )
        counts: dict[str, int] = {}
        for row in snapshot["grid"]["cell_type"]:
            for cell_type in row:
                key = str(cell_type)
                counts[key] = counts.get(key, 0) + 1
        return {
            "ok": True,
            "snapshot": snapshot,
            "map_meta": {
                "source": str(map_path.relative_to(self.server.root)),
                "template_id": template_id,
                "width": snapshot["grid"]["width"],
                "height": snapshot["grid"]["height"],
                "cell_counts": counts,
                "loader": "A standard template JSON -> Grid",
            },
        }

    def _create_repository_sample(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Start the one confirmed A/C repository sample without browser uploads."""

        self.server.close_session()
        map_candidates = [
            self.server.root / "maps" / "edited_map.json",
            self.server.root / "maps" / "examples" / "classroom_corridor.json",
            self.server.root / "scenarios" / "classroom_corridor.json",
        ]
        people_candidates = [
            self.server.root / "control" / "output_people_position.json",
            self.server.root / "social" / "output_people.json",
            self.server.root / "docs" / "d_week3" / "examples" / "c_output_people.json",
        ]
        map_path = next((path for path in map_candidates if path.is_file()), None)
        people_path = next((path for path in people_candidates if path.is_file()), None)
        yaml_path = self.server.root / "control" / "config_template.yaml"
        if map_path is None or people_path is None:
            raise WebRuntimeError("repository A/C sample files are unavailable")
        temporary_directory = _runtime_temp_directory(
            self.server.root, "d_web_runtime_sample_"
        )
        try:
            return self._start_runner(
                temporary_directory,
                map_path=map_path,
                people_path=people_path,
                yaml_path=yaml_path if yaml_path.is_file() else None,
                max_steps=_positive_steps(payload.get("max_steps")),
                random_seed=_optional_seed(payload.get("random_seed")),
                time_step_s=_positive_time_step(payload.get("time_step_s")),
                physical_cell_size_m=_optional_physical_cell_size(payload.get("physical_cell_size_m")),
                sampling_window_s=_optional_positive_analysis_value(payload.get("sampling_window_s"), "sampling_window_s"),
                analysis_mesh_size_m=_optional_positive_analysis_value(payload.get("analysis_mesh_size_m"), "analysis_mesh_size_m"),
                roi_radius_m=_optional_positive_analysis_value(payload.get("roi_radius_m"), "roi_radius_m"),
                c_runtime_config=_control_runtime_config(payload),
            )
        except Exception:
            temporary_directory.cleanup()
            raise

    def _create_template_session(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Run a selected A template with a C file, preserving C coordinates."""

        self.server.close_session()
        temporary_directory = _runtime_temp_directory(
            self.server.root, "d_web_template_"
        )
        root = Path(temporary_directory.name)
        try:
            map_path = self._standard_template_path(payload.get("template_id"))
            people_path = _uploaded_file(payload, "population_file", {".json"}, root)
            yaml_path = _uploaded_file(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
            if people_path is None:
                raise WebRuntimeError("population_file 是标准模板仿真所必需的人员数据")
            try:
                return self._start_runner(
                    temporary_directory,
                    map_path=map_path,
                    people_path=people_path,
                    yaml_path=yaml_path,
                    max_steps=_positive_steps(payload.get("max_steps")),
                    random_seed=_optional_seed(payload.get("random_seed")),
                    time_step_s=_positive_time_step(payload.get("time_step_s")),
                    physical_cell_size_m=_optional_physical_cell_size(payload.get("physical_cell_size_m")),
                    sampling_window_s=_optional_positive_analysis_value(payload.get("sampling_window_s"), "sampling_window_s"),
                    analysis_mesh_size_m=_optional_positive_analysis_value(payload.get("analysis_mesh_size_m"), "analysis_mesh_size_m"),
                    roi_radius_m=_optional_positive_analysis_value(payload.get("roi_radius_m"), "roi_radius_m"),
                    c_runtime_config=_control_runtime_config(payload),
                )
            except Exception as exc:
                message = str(exc)
                if "A-assigned position" in message and "outside the map" in message:
                    template_id = str(payload.get("template_id") or "").strip().lower()
                    raise WebRuntimeError(
                        f"人员文件与 A 模板 {template_id} 的尺寸不匹配；"
                        "请先由 C 生成人员/关系，再由 A 按该模板分配坐标并导出 "
                        "output_people_position.json。"
                    ) from exc
                raise
        except Exception:
            temporary_directory.cleanup()
            raise

    def _start_runner(
        self,
        temporary_directory: tempfile.TemporaryDirectory[str],
        *,
        map_path: Path,
        people_path: Path,
        yaml_path: Path | None,
        max_steps: int,
        random_seed: int | None = None,
        time_step_s: float = 0.5,
        physical_cell_size_m: float | None = None,
        sampling_window_s: float | None = None,
        analysis_mesh_size_m: float | None = None,
        roi_radius_m: float | None = None,
        c_runtime_config: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = _new_run_id()
        output_root = self.server.root / "outputs" / "experiments"
        physical_cell_size_m = _platform_physical_cell_size(physical_cell_size_m)
        _force_headless_matplotlib()
        runner = create_integrated_runner(
            map_path=map_path,
            population_path=people_path,
            yaml_path=yaml_path,
            c_module_path=self.server.root / "control" / "scene_config.py",
            output_root=output_root,
            run_id=run_id,
            random_seed=random_seed,
            time_step_s=time_step_s,
            max_steps=max_steps,
            physical_cell_size_m=physical_cell_size_m,
            sampling_window_s=sampling_window_s,
            analysis_mesh_size_m=analysis_mesh_size_m,
            roi_radius_m=roi_radius_m,
            c_runtime_config=c_runtime_config,
        )
        try:
            snapshot = runner.initialize()
            # Precompute the static route cache outside the live step loop.
            warm_guidance_route_cache(snapshot)
        except Exception:
            runner.close()
            raise
        input_files = {
            "map": map_path,
            "population": people_path,
        }
        if yaml_path is not None:
            input_files["yaml"] = yaml_path
        self._write_run_metadata(runner, snapshot, input_files, save_frame=True)
        self.server.session = RuntimeSession(
            temporary_directory=temporary_directory,
            runner=runner,
            input_files=input_files,
            max_steps=max_steps,
        )
        return {
            "ok": True,
            "snapshot": snapshot,
            "output_dir": str(runner.output_root / run_id),
            "runtime_parameters": {
                "random_seed": snapshot.get("random_seed"),
                "time_step_s": snapshot.get("time_step"),
                "max_steps": max_steps,
                **({
                    "physical_cell_size_m": physical_cell_size_m,
                    "physical_scale_kind": PLATFORM_PHYSICAL_SCALE_SOURCE,
                } if physical_cell_size_m is not None else {}),
            },
        }

    def _require_session(self) -> RuntimeSession:
        if self.server.session is None:
            raise WebRuntimeError("当前没有活动仿真，请先导入场景地图和人员数据")
        return self.server.session

    def _step_session(self) -> dict[str, Any]:
        session = self._require_session()
        request_started = perf_counter()
        snapshot = session.runner.step()
        runtime_step_ms = (perf_counter() - request_started) * 1000.0
        artifact_write_ms = 0.0
        # The CSV logger already persists every step.  Rebuilding every
        # derived artifact here rereads the whole log and makes one click cost
        # O(steps-so-far); defer it to completion or the explicit analysis API.
        if session.runner.finished:
            artifacts_started = perf_counter()
            self._write_run_metadata(
                session.runner,
                snapshot,
                session.input_files,
                save_frame=False,
            )
            artifact_write_ms = (perf_counter() - artifacts_started) * 1000.0
        # 增量返回：grid / relations 是静态数据，初始化时已经下发过，
        # 每步不再重复发送（可减少约 57% 的响应体积，明显改善动画流畅度）。
        slim_snapshot = dict(snapshot)
        slim_snapshot.pop("grid", None)
        slim_snapshot.pop("relations", None)
        payload_prepare_ms = (perf_counter() - request_started) * 1000.0 - runtime_step_ms - artifact_write_ms
        # Avoid serializing the multi-megabyte snapshot a second time merely
        # to report its size.  _send_json performs the one authoritative encode.
        payload_bytes = 0
        request_processing_ms = (perf_counter() - request_started) * 1000.0
        finished = session.runner.finished
        return {
            "ok": True,
            "finished": finished,
            "termination": _termination_state(
                snapshot, finished=finished, max_steps=session.max_steps
            ),
            "snapshot": slim_snapshot,
            "static_fields_omitted": ["grid", "relations"],
            "output_dir": str(session.runner.output_root / snapshot["run_id"]),
            "diagnostics": {
                "runtime_step_ms": round(runtime_step_ms, 3),
                "artifact_write_ms": round(artifact_write_ms, 3),
                "payload_prepare_ms": round(payload_prepare_ms, 3),
                "payload_bytes": payload_bytes,
                "request_processing_ms": round(request_processing_ms, 3),
            },
        }

    def _set_broadcast_enabled(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Toggle future alarm broadcast for the active C runtime bridge."""

        enabled = payload.get("enabled")
        if not isinstance(enabled, bool):
            raise WebRuntimeError("enabled must be a boolean")
        session = self._require_session()
        provider = getattr(session.runner.simulation, "behavior_provider", None)
        if provider is None or not callable(getattr(provider, "set_broadcast_enabled", None)):
            raise WebRuntimeError("active runtime does not expose a broadcast controller")
        provider.set_broadcast_enabled(enabled)
        return {
            "ok": True,
            "broadcast_enabled": enabled,
            "information_mode": "broadcast" if enabled else "local",
        }

    def _reset_session(self) -> dict[str, Any]:
        session = self._require_session()
        snapshot = session.runner.reset()
        self._write_run_metadata(
            session.runner, snapshot, session.input_files, save_frame=True
        )
        return {
            "ok": True,
            "snapshot": snapshot,
            "output_dir": str(session.runner.output_root / snapshot["run_id"]),
            "runtime_parameters": {
                "random_seed": snapshot.get("random_seed"),
                "time_step_s": snapshot.get("time_step"),
                "max_steps": session.max_steps,
            },
        }

    def _write_run_metadata(
        self,
        runner: Any,
        snapshot: Mapping[str, Any],
        input_files: Mapping[str, Path],
        *,
        save_frame: bool = False,
    ) -> None:
        run_id = str(snapshot.get("run_id") or runner.current_run_id)
        output_dir = runner.output_root / run_id
        write_run_artifacts(
            snapshot,
            output_dir,
            input_files=input_files,
            save_frame=save_frame,
        )

    def _export_session(self) -> None:
        """Return a ZIP built from the active runner's actual CSV output."""

        # Capture immutable request inputs under the session lock, then release
        # it before expensive log analysis.  Export must not block a fast CSV
        # request or a step behind the full academic analysis.
        with self.server.session_lock:
            session = self._require_session()
            runner = session.runner
            snapshot = runner.current_snapshot
            run_id = runner.current_run_id
            if snapshot is None or not run_id:
                raise WebRuntimeError("active session has no current snapshot to export")
            output_dir = runner.output_root / run_id
            input_files = dict(session.input_files)
            max_steps = session.max_steps
            cache_key = self._academic_cache_key(snapshot)
            with self.server.academic_cache_lock:
                cached_academic = self.server.academic_cache.get(cache_key)
        self._write_run_metadata(
            runner, snapshot, input_files, save_frame=False
        )
        try:
            package = build_result_package(
                output_dir=output_dir,
                final_snapshot=snapshot,
                input_files=input_files,
                max_steps=max_steps,
                precomputed_academic=(
                    cached_academic.get("academic_crowd_fields")
                    if cached_academic is not None
                    else None
                ),
            )
        except ResultPackageError as exc:
            raise WebRuntimeError(str(exc)) from exc
        self._send_binary(
            package.content,
            content_type="application/zip",
            filename=package.filename,
        )

    @staticmethod
    def _academic_cache_key(snapshot: Mapping[str, Any]) -> tuple[str, int, str]:
        contract = snapshot.get("analysis_contract")
        return (
            str(snapshot.get("run_id") or ""),
            int(snapshot.get("step") or 0),
            json.dumps(
                contract if isinstance(contract, Mapping) else {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def _academic_analysis(self) -> dict[str, Any]:
        with self.server.session_lock:
            session = self._require_session()
            snapshot = session.runner.current_snapshot
            run_id = session.runner.current_run_id
            if snapshot is None or not run_id:
                raise WebRuntimeError(
                    "active session has no current snapshot for academic analysis"
                )
            output_dir = session.runner.output_root / run_id
            cache_key = self._academic_cache_key(snapshot)

        with self.server.academic_cache_lock:
            cached = self.server.academic_cache.get(cache_key)
        if cached is not None:
            return {
                **cached,
                "ok": True,
                "cache_hit": True,
            }

        grid = snapshot.get("grid")
        contract = snapshot.get("analysis_contract")
        if not isinstance(grid, Mapping) or not isinstance(contract, Mapping):
            raise WebRuntimeError(
                "active snapshot has no grid or analysis contract for academic analysis"
            )
        started = perf_counter()
        with (output_dir / "people_log.csv").open(
            "r", encoding="utf-8-sig", newline=""
        ) as stream:
            field = academic_crowd_fields(
                csv.DictReader(stream),
                grid=grid,
                analysis_contract=contract,
            )
        result = {
            "run_id": run_id,
            "academic_crowd_fields": field,
            "diagnostics": {
                "academic_compute_ms": round(
                    (perf_counter() - started) * 1000.0, 3
                )
            },
            "cache_hit": False,
        }
        with self.server.academic_cache_lock:
            self.server.academic_cache = {
                cache_key: result,
            }
        return {**result, "ok": True}

    def _export_lambda_csv(self) -> None:
        with self.server.session_lock:
            session = self._require_session()
            runner = session.runner
            snapshot = runner.current_snapshot
            run_id = runner.current_run_id
            if snapshot is None or not run_id:
                raise WebRuntimeError("active session has no current snapshot for lambda CSV")
            output_dir = runner.output_root / run_id
        csv_text = build_lambda_experiment_csv(
            output_dir=output_dir,
            final_snapshot=snapshot,
        )
        self._send_binary(
            csv_text.encode("utf-8-sig"),
            content_type="text/csv; charset=utf-8",
            filename="lambda_experiment.csv",
        )

    def _runtime_analysis(
        self,
        *,
        include_figures: bool,
        include_layers: bool,
        include_academic: bool,
    ) -> dict[str, Any]:
        """Return only CSV-derived analysis for the active D run."""

        with self.server.session_lock:
            session = self._require_session()
            runner = session.runner
            snapshot = runner.current_snapshot
            run_id = runner.current_run_id
            if snapshot is None or not run_id:
                raise WebRuntimeError("active session has no current snapshot to analyze")
            output_dir = runner.output_root / run_id
        analysis_started = perf_counter()
        try:
            result = build_runtime_analysis(
                output_dir=output_dir,
                final_snapshot=snapshot,
                include_figures=include_figures,
                include_layers=include_layers,
                include_academic=include_academic,
            )
        except ResultPackageError as exc:
            raise WebRuntimeError(str(exc)) from exc
        result["ok"] = True
        result["diagnostics"] = {
            "analysis_compute_ms": round((perf_counter() - analysis_started) * 1000.0, 3),
            "analysis_response_bytes": len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
        }
        return result

    def _history_roots(self) -> tuple[Path, ...]:
        """Return only D's real formal output roots for the history view."""

        return (
            self.server.root / "outputs" / "experiments",
            self.server.root / "outputs" / "integrated",
        )

    def _send_json(self, data: Mapping[str, Any]) -> None:
        encoded = json.dumps(
            data, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        use_gzip = (
            len(encoded) >= 64 * 1024
            and "gzip" in self.headers.get("Accept-Encoding", "").lower()
        )
        if use_gzip:
            encoded = gzip.compress(encoded, compresslevel=1)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        if use_gzip:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_binary(self, content: bytes, *, content_type: str, filename: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def _send_error_json(self, status: HTTPStatus, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        encoded = json.dumps({"ok": False, "error": message}, ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve D's local A+B+C HTML runtime bridge.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = args.root.resolve()
    if not (root / "visualization" / "prototype" / "integrated_runtime.html").is_file():
        raise SystemExit("--root must be the smoke-evacuation-ca repository root")
    server = DWebRuntimeServer((args.host, args.port), RuntimeRequestHandler, root=root)
    print(f"D local web runtime: http://{args.host}:{args.port}/visualization/prototype/final_platform.html")
    print("Press Ctrl+C to stop the local server.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nD local web runtime stopped.")
    finally:
        server.close_session()
        server.server_close()


if __name__ == "__main__":
    main()
