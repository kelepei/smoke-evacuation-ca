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
import json
import mimetypes
import shutil
import tempfile
import threading
from datetime import datetime
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping
from urllib.parse import urlparse

from experiments.integrated_runner import create_integrated_runner
from experiments.result_package import ResultPackageError, build_result_package
from visualization.scene_input_adapter import grid_to_static_snapshot, load_map_grid


MAX_REQUEST_BYTES = 24 * 1024 * 1024
ALLOWED_MAP_SUFFIXES = {".json", ".csv", ".png"}
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


class DWebRuntimeServer(ThreadingHTTPServer):
    """Local server state; only one active run is needed for the MVP UI."""

    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handler: type[SimpleHTTPRequestHandler], *, root: Path) -> None:
        super().__init__(address, handler)
        self.root = root.resolve()
        self.session: RuntimeSession | None = None
        # A B step can be much slower than browser timer events.  Serialize
        # all mutations so reset/close cannot replace a live runner mid-step.
        self.session_lock = threading.RLock()

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


def _new_run_id(prefix: str = "d_web_runtime") -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"


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


def _snapshot_metrics(snapshot: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    people = snapshot.get("people", [])
    people_list = people if isinstance(people, list) else []
    total = len(people_list)
    evacuated = sum(1 for person in people_list if isinstance(person, Mapping) and person.get("evacuated") is True)
    smoke_values = _flatten_numeric_field((snapshot.get("fields") or {}).get("smoke_field") if isinstance(snapshot.get("fields"), Mapping) else [])

    evac_times: list[float] = []
    event_path = output_dir / "event_log.csv"
    if event_path.is_file():
        with event_path.open("r", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row.get("event_type") == "evac_success":
                    try:
                        evac_times.append(float(row.get("time_s", "")))
                    except ValueError:
                        pass

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
        path = urlparse(self.path).path
        if path == "/api/health":
            self._send_json({"ok": True, "service": "d_web_runtime", "version": "0.1"})
            return
        if path == "/api/session/export":
            try:
                with self.server.session_lock:
                    self._export_session()
            except WebRuntimeError as exc:
                self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"export error: {exc}")
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802 - required HTTP handler name
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
            if path == "/api/map/preview":
                response = self._preview_map(payload)
            elif path == "/api/template/preview":
                response = self._preview_template(payload)
            else:
                with self.server.session_lock:
                    if path == "/api/session":
                        response = self._create_session(payload)
                    elif path == "/api/session/template":
                        response = self._create_template_session(payload)
                    elif path == "/api/session/sample":
                        response = self._create_repository_sample()
                    elif path == "/api/session/step":
                        response = self._step_session()
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
            map_path = _uploaded_file(payload, "map_file", ALLOWED_MAP_SUFFIXES, root)
            people_path = _uploaded_file(payload, "population_file", {".json"}, root)
            yaml_path = _uploaded_file(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
            if map_path is None or people_path is None:
                raise WebRuntimeError("map_file and population_file are required")
            return self._start_runner(
                temporary_directory,
                map_path=map_path,
                people_path=people_path,
                yaml_path=yaml_path,
                max_steps=_positive_steps(payload.get("max_steps")),
            )
        except Exception:
            temporary_directory.cleanup()
            raise

    def _preview_map(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        temporary_directory = _runtime_temp_directory(
            self.server.root, "d_map_preview_"
        )
        root = Path(temporary_directory.name)
        try:
            map_path = _uploaded_file(payload, "map_file", ALLOWED_MAP_SUFFIXES, root)
            if map_path is None:
                raise WebRuntimeError("map_file is required")
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

    def _create_repository_sample(self) -> dict[str, Any]:
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
                max_steps=500,
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
                raise WebRuntimeError("C population_file is required for a standard template")
            try:
                return self._start_runner(
                    temporary_directory,
                    map_path=map_path,
                    people_path=people_path,
                    yaml_path=yaml_path,
                    max_steps=_positive_steps(payload.get("max_steps")),
                )
            except Exception as exc:
                message = str(exc)
                if "A-assigned position" in message and "outside the map" in message:
                    template_id = str(payload.get("template_id") or "").strip().lower()
                    raise WebRuntimeError(
                        f"C 人员坐标与 A 模板 {template_id} 的尺寸不匹配；"
                        "请让 C 按该模板重新生成 output_people_position.json。"
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
    ) -> dict[str, Any]:
        run_id = _new_run_id()
        output_root = self.server.root / "outputs" / "experiments"
        _force_headless_matplotlib()
        runner = create_integrated_runner(
            map_path=map_path,
            population_path=people_path,
            yaml_path=yaml_path,
            c_module_path=self.server.root / "control" / "scene_config.py",
            output_root=output_root,
            run_id=run_id,
            max_steps=max_steps,
        )
        try:
            snapshot = runner.initialize()
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
        }

    def _require_session(self) -> RuntimeSession:
        if self.server.session is None:
            raise WebRuntimeError("no active session; import A map and C population first")
        return self.server.session

    def _step_session(self) -> dict[str, Any]:
        session = self._require_session()
        request_started = perf_counter()
        snapshot = session.runner.step()
        step_compute_ms = (perf_counter() - request_started) * 1000.0
        self._write_run_metadata(
            session.runner,
            snapshot,
            session.input_files,
            save_frame=session.runner.finished,
        )
        request_processing_ms = (perf_counter() - request_started) * 1000.0
        return {
            "ok": True,
            "finished": session.runner.finished,
            "snapshot": snapshot,
            "output_dir": str(session.runner.output_root / snapshot["run_id"]),
            "diagnostics": {
                "step_compute_ms": round(step_compute_ms, 3),
                "request_processing_ms": round(request_processing_ms, 3),
            },
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
        output_dir.mkdir(parents=True, exist_ok=True)
        config_used = {
            "run_id": run_id,
            "scenario_id": snapshot.get("scenario_id"),
            "schema_version": snapshot.get("schema_version"),
            "random_seed": snapshot.get("random_seed"),
            "input_files": {key: str(path) for key, path in input_files.items()},
            "runtime_contract": "A Grid + C population/config + B EvacEngine through D adapters",
            "missing_upstream_fields": "CSV logger leaves unprovided upstream fields empty; D does not fabricate values.",
        }
        _write_json(output_dir / "config_used.json", config_used)
        metrics = _snapshot_metrics(snapshot, output_dir)
        _write_json(output_dir / "metrics.json", metrics)
        _write_summary_csv(output_dir / "metrics_summary.csv", metrics)
        if save_frame:
            # Browser canvas owns live frames; write PNG only at durable checkpoints.
            from visualization.integrated_runtime import save_snapshot_png

            save_snapshot_png(dict(snapshot), output_dir / "final_frame.png")

    def _export_session(self) -> None:
        """Return a ZIP built from the active runner's actual CSV output."""

        session = self._require_session()
        snapshot = session.runner.current_snapshot
        run_id = session.runner.current_run_id
        if snapshot is None or not run_id:
            raise WebRuntimeError("active session has no current snapshot to export")
        self._write_run_metadata(
            session.runner, snapshot, session.input_files, save_frame=True
        )
        try:
            package = build_result_package(
                output_dir=session.runner.output_root / run_id,
                final_snapshot=snapshot,
                input_files=session.input_files,
                max_steps=session.max_steps,
            )
        except ResultPackageError as exc:
            raise WebRuntimeError(str(exc)) from exc
        self._send_binary(
            package.content,
            content_type="application/zip",
            filename=package.filename,
        )

    def _send_json(self, data: Mapping[str, Any]) -> None:
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
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
    print(f"D local web runtime: http://{args.host}:{args.port}/visualization/prototype/integrated_runtime.html")
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
