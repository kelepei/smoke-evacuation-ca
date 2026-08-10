"""Local D web service for guarded A + C input and B runtime control."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from experiments.integrated_runner import IntegrationInputError, create_integrated_runner
from experiments.result_package import ResultPackageError, create_result_package


class RuntimeWebError(RuntimeError):
    """Raised when a browser asks for an unavailable runtime operation."""


class RuntimeWebService:
    """Own one local D session; it never fills in absent A/C/B data."""

    def __init__(self, *, output_root: str | Path) -> None:
        self.output_root = Path(output_root)
        self._lock = threading.RLock()
        self._runner: Any | None = None
        self._inputs: dict[str, Path | None] = {}
        self._packages: dict[str, Path] = {}

    @staticmethod
    def _required_path(payload: Mapping[str, Any], field: str) -> Path:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise RuntimeWebError(f"{field} is required")
        path = Path(value).expanduser()
        if not path.is_file():
            raise RuntimeWebError(f"{field} does not exist: {path}")
        return path.resolve()

    @staticmethod
    def _optional_int(payload: Mapping[str, Any], field: str) -> int | None:
        value = payload.get(field)
        if value in (None, ""):
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise RuntimeWebError(f"{field} must be an integer or empty")
        return value

    def start(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        map_path = self._required_path(payload, "map_path")
        population_path = self._required_path(payload, "population_path")
        config_path = payload.get("config_path")
        if config_path in (None, ""):
            optional_config = None
        elif isinstance(config_path, str) and Path(config_path).expanduser().is_file():
            optional_config = Path(config_path).expanduser().resolve()
        else:
            raise RuntimeWebError("config_path must be an existing file or empty")
        random_seed = self._optional_int(payload, "random_seed")
        source_id_base = payload.get("source_id_base", 0)
        if source_id_base not in (0, 1):
            raise RuntimeWebError("source_id_base must be 0 or 1")

        try:
            next_runner = create_integrated_runner(
                map_path=map_path,
                population_path=population_path,
                output_root=self.output_root,
                scenario_id=payload.get("scenario_id") or None,
                random_seed=random_seed,
                source_id_base=source_id_base,
            )
            initial = next_runner.initialize()
        except (IntegrationInputError, OSError, TypeError, ValueError) as exc:
            raise RuntimeWebError(str(exc)) from exc

        with self._lock:
            self.close()
            self._runner = next_runner
            self._inputs = {
                "map_path": map_path,
                "population_path": population_path,
                "config_path": optional_config,
            }
            return initial

    def _active_runner(self) -> Any:
        if self._runner is None or self._runner.current_snapshot is None:
            raise RuntimeWebError("no active runtime session")
        return self._runner

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._active_runner().current_snapshot

    def step(self) -> dict[str, Any]:
        with self._lock:
            try:
                return self._active_runner().step()
            except Exception as exc:
                raise RuntimeWebError(f"B runtime step failed: {exc}") from exc

    def export(self) -> Path:
        with self._lock:
            runner = self._active_runner()
            if runner.logger is None or runner.current_run_id is None:
                raise RuntimeWebError("active session has no D log output")
            try:
                package = create_result_package(
                    run_directory=runner.logger.output_dir,
                    destination_directory=self.output_root / "packages",
                    map_path=self._inputs["map_path"],
                    population_path=self._inputs["population_path"],
                    config_path=self._inputs.get("config_path"),
                )
            except (OSError, ResultPackageError, ValueError) as exc:
                raise RuntimeWebError(f"result package failed: {exc}") from exc
            self._packages[package.name] = package
            return package

    def package_path(self, name: str) -> Path:
        with self._lock:
            package = self._packages.get(name)
            if package is None or not package.is_file():
                raise RuntimeWebError("result package does not exist")
            return package

    def close(self) -> None:
        with self._lock:
            if self._runner is not None:
                self._runner.close()
            self._runner = None
            self._inputs = {}


def create_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    repository_root: str | Path | None = None,
) -> ThreadingHTTPServer:
    root = Path(repository_root or Path(__file__).resolve().parents[1]).resolve()
    page = root / "visualization" / "prototype" / "integrated_runtime.html"
    if not page.is_file():
        raise FileNotFoundError(f"D integrated page does not exist: {page}")
    service = RuntimeWebService(output_root=root / "outputs" / "d_week4")

    class Handler(BaseHTTPRequestHandler):
        server_version = "DEvacRuntime/0.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _json(self, status: HTTPStatus, payload: Mapping[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _request_payload(self) -> Mapping[str, Any]:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 1_000_000:
                raise RuntimeWebError("request body must be JSON below 1 MB")
            try:
                payload = json.loads(self.rfile.read(size).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeWebError("request body must be valid UTF-8 JSON") from exc
            if not isinstance(payload, Mapping):
                raise RuntimeWebError("request JSON must be an object")
            return payload

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path in {"/", "/visualization/prototype/integrated_runtime.html"}:
                content = page.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
            if path.startswith("/api/packages/"):
                try:
                    package = service.package_path(unquote(path.rsplit("/", 1)[-1]))
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="{package.name}"')
                    self.send_header("Content-Length", str(package.stat().st_size))
                    self.end_headers()
                    with package.open("rb") as handle:
                        self.wfile.write(handle.read())
                except RuntimeWebError as exc:
                    self._json(HTTPStatus.NOT_FOUND, {"error": str(exc)})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            try:
                if path == "/api/start":
                    self._json(HTTPStatus.OK, {"snapshot": service.start(self._request_payload())})
                elif path == "/api/step":
                    self._json(HTTPStatus.OK, {"snapshot": service.step()})
                elif path == "/api/snapshot":
                    self._json(HTTPStatus.OK, {"snapshot": service.snapshot()})
                elif path == "/api/export":
                    package = service.export()
                    self._json(HTTPStatus.OK, {"download_url": f"/api/packages/{package.name}"})
                elif path == "/api/close":
                    service.close()
                    self._json(HTTPStatus.OK, {"closed": True})
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except RuntimeWebError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    return ThreadingHTTPServer((host, port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start D's local integrated runtime page.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = create_server(host=args.host, port=args.port)
    print(f"D runtime page: http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
