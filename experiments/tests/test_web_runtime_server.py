from __future__ import annotations

import base64
import json
import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

from PIL import Image, ImageDraw

from experiments.web_runtime_server import DWebRuntimeServer, RuntimeRequestHandler


class WebRuntimeServerTests(unittest.TestCase):
    def _write_inputs(self, root: Path) -> tuple[Path, Path]:
        width, height = 8, 5
        cells = []
        for y in range(height):
            for x in range(width):
                cell_type = "free"
                if x in (0, width - 1) or y in (0, height - 1):
                    cell_type = "wall"
                if (x, y) == (width - 1, 2):
                    cell_type = "exit"
                if (x, y) == (2, 2):
                    cell_type = "smoke_source"
                cells.append({"x": x, "y": y, "type": cell_type})
        map_path = root / "map.json"
        map_path.write_text(
            json.dumps({"name": "d_test_map", "width": width, "height": height, "cell_size": 0.5, "cells": cells}),
            encoding="utf-8",
        )
        population_path = root / "output_people.json"
        population_path.write_text(
            json.dumps(
                {
                    "persons": [
                        {"id": 0, "x": 2, "y": 3, "profile": "student"},
                        {"id": 1, "x": 3, "y": 3, "profile": "student"},
                    ],
                    "relations": [
                        {
                            "from": 0,
                            "to": 1,
                            "relation_type": "classmate",
                            "strength": 0.6,
                            "trust": 0.7,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return map_path, population_path

    def test_local_session_returns_real_b_snapshot(self) -> None:
        server = DWebRuntimeServer(("127.0.0.1", 0), RuntimeRequestHandler, root=Path.cwd())
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        def post(route: str, body: dict[str, object]) -> dict[str, object]:
            request = Request(
                base_url + route,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            with tempfile.TemporaryDirectory() as raw:
                map_path, population_path = self._write_inputs(Path(raw))
                initial = post(
                    "/api/session",
                    {
                        "map_file": {"name": map_path.name, "text": map_path.read_text(encoding="utf-8")},
                        "population_file": {
                            "name": population_path.name,
                            "text": population_path.read_text(encoding="utf-8"),
                        },
                        "max_steps": 8,
                    },
                )["snapshot"]
                self.assertEqual(2, len(initial["people"]))
                self.assertEqual("A map + C population + B EvacEngine", initial["adapter_meta"]["input_mode"])
                stepped_response = post("/api/session/step", {})
                stepped = stepped_response["snapshot"]
                self.assertEqual(1, stepped["step"])
                self.assertEqual(0.5, stepped["time_s"])
                self.assertTrue(Path(stepped_response["output_dir"], "final_frame.png").is_file())
                with urlopen(base_url + "/api/session/export", timeout=10) as response:
                    self.assertEqual("application/zip", response.headers.get_content_type())
                    package = zipfile.ZipFile(io.BytesIO(response.read()))
                names = set(package.namelist())
                prefix = initial["run_id"] + "/"
                self.assertIn(prefix + "people_log.csv", names)
                self.assertIn(prefix + "event_log.csv", names)
                self.assertIn(prefix + "metrics.csv", names)
                self.assertIn(prefix + "evacuation_curve.svg", names)
                self.assertIn(prefix + "occupancy_heatmap.svg", names)
                self.assertIn(prefix + "inputs/map_file.json", names)
                self.assertIn(prefix + "inputs/population_file.json", names)
        finally:
            try:
                post("/api/session/close", {})
            finally:
                server.shutdown()
                server.close_session()
                server.server_close()
                worker.join(timeout=5)

    def test_map_preview_uses_a_grid_before_session_start(self) -> None:
        server = DWebRuntimeServer(("127.0.0.1", 0), RuntimeRequestHandler, root=Path.cwd())
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        def post(route: str, body: dict[str, object]) -> dict[str, object]:
            request = Request(
                base_url + route,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            with tempfile.TemporaryDirectory() as raw:
                map_path, _ = self._write_inputs(Path(raw))
                preview = post(
                    "/api/map/preview",
                    {
                        "map_file": {
                            "name": map_path.name,
                            "text": map_path.read_text(encoding="utf-8"),
                        }
                    },
                )
                grid = preview["snapshot"]["grid"]
                self.assertEqual(8, grid["width"])
                self.assertEqual(5, grid["height"])
                self.assertEqual("wall", grid["cell_type"][0][0])
                self.assertEqual("exit", grid["cell_type"][2][7])
                self.assertEqual("A JSON/CSV/PNG loader -> Grid", preview["map_meta"]["loader"])
        finally:
            try:
                post("/api/session/close", {})
            finally:
                server.shutdown()
                server.close_session()
                server.server_close()
                worker.join(timeout=5)

    def test_png_preview_uses_ascii_runtime_temp_path(self) -> None:
        server = DWebRuntimeServer(("127.0.0.1", 0), RuntimeRequestHandler, root=Path.cwd())
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f"http://127.0.0.1:{server.server_port}"

        def post(route: str, body: dict[str, object]) -> dict[str, object]:
            request = Request(
                base_url + route,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            with tempfile.TemporaryDirectory() as raw:
                image_path = Path(raw) / "map.png"
                image = Image.new("L", (200, 200), color=0)
                ImageDraw.Draw(image).rectangle((0, 0, 199, 199), outline=255, width=10)
                image.save(image_path)
                preview = post(
                    "/api/map/preview",
                    {
                        "map_file": {
                            "name": image_path.name,
                            "base64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
                        }
                    },
                )
                grid = preview["snapshot"]["grid"]
                self.assertEqual(20, grid["width"])
                self.assertEqual(20, grid["height"])
                self.assertEqual(76, preview["map_meta"]["cell_counts"]["wall"])
                self.assertEqual(324, preview["map_meta"]["cell_counts"]["free"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
