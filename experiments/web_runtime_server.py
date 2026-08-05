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
import json
import mimetypes
import shutil
import tempfile
from dataclasses import dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from experiments.integrated_runner import create_integrated_runner
from experiments.result_package import ResultPackageError, build_result_package


MAX_REQUEST_BYTES = 8 * 1024 * 1024
ALLOWED_MAP_SUFFIXES = {".json", ".csv"}
ALLOWED_YAML_SUFFIXES = {".yaml", ".yml"}


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

    def close_session(self) -> None:
        if self.session is not None:
            self.session.close()
        self.session = None


def _safe_suffix(filename: Any, allowed: set[str], field: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix not in allowed:
        choices = ", ".join(sorted(allowed))
        raise WebRuntimeError(f"{field} must use one of: {choices}")
    return suffix


def _uploaded_text(payload: Mapping[str, Any], field: str, allowed: set[str], root: Path) -> Path | None:
    raw = payload.get(field)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise WebRuntimeError(f"{field} must be a file object")
    filename = raw.get("name")
    text = raw.get("text")
    if not isinstance(text, str):
        raise WebRuntimeError(f"{field}.text must be UTF-8 text")
    suffix = _safe_suffix(filename, allowed, field)
    target = root / f"{field}{suffix}"
    target.write_text(text, encoding="utf-8")
    return target


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
            if path == "/api/session":
                response = self._create_session(payload)
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
        temporary_directory = tempfile.TemporaryDirectory(prefix="d_web_runtime_")
        root = Path(temporary_directory.name)
        try:
            map_path = _uploaded_text(payload, "map_file", ALLOWED_MAP_SUFFIXES, root)
            people_path = _uploaded_text(payload, "population_file", {".json"}, root)
            yaml_path = _uploaded_text(payload, "yaml_file", ALLOWED_YAML_SUFFIXES, root)
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

    def _create_repository_sample(self) -> dict[str, Any]:
        """Start the one confirmed A/C repository sample without browser uploads."""

        self.server.close_session()
        map_path = self.server.root / "scenarios" / "classroom_corridor.json"
        people_path = self.server.root / "social" / "output_people.json"
        yaml_path = self.server.root / "control" / "config_template.yaml"
        if not map_path.is_file() or not people_path.is_file():
            raise WebRuntimeError("repository A/C sample files are unavailable")
        temporary_directory = tempfile.TemporaryDirectory(prefix="d_web_runtime_sample_")
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

    def _start_runner(
        self,
        temporary_directory: tempfile.TemporaryDirectory[str],
        *,
        map_path: Path,
        people_path: Path,
        yaml_path: Path | None,
        max_steps: int,
    ) -> dict[str, Any]:
        runner = create_integrated_runner(
            map_path=map_path,
            population_path=people_path,
            yaml_path=yaml_path,
            c_module_path=self.server.root / "control" / "scene_config.py",
            output_root=Path(temporary_directory.name) / "outputs",
            run_id="d_web_runtime",
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
        self.server.session = RuntimeSession(
            temporary_directory=temporary_directory,
            runner=runner,
            input_files=input_files,
            max_steps=max_steps,
        )
        return {"ok": True, "snapshot": snapshot}

    def _require_session(self) -> RuntimeSession:
        if self.server.session is None:
            raise WebRuntimeError("no active session; import A map and C population first")
        return self.server.session

    def _step_session(self) -> dict[str, Any]:
        session = self._require_session()
        snapshot = session.runner.step()
        return {"ok": True, "finished": session.runner.finished, "snapshot": snapshot}

    def _reset_session(self) -> dict[str, Any]:
        session = self._require_session()
        snapshot = session.runner.reset()
        return {"ok": True, "snapshot": snapshot}

    def _export_session(self) -> None:
        """Return a ZIP built from the active runner's actual CSV output."""

        session = self._require_session()
        snapshot = session.runner.current_snapshot
        run_id = session.runner.current_run_id
        if snapshot is None or not run_id:
            raise WebRuntimeError("active session has no current snapshot to export")
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
