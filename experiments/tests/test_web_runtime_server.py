from __future__ import annotations

import json
import io
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

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
                stepped = post("/api/session/step", {})["snapshot"]
                self.assertEqual(1, stepped["step"])
                self.assertEqual(0.5, stepped["time_s"])
                with urlopen(base_url + "/api/session/export", timeout=10) as response:
                    self.assertEqual("application/zip", response.headers.get_content_type())
                    package = zipfile.ZipFile(io.BytesIO(response.read()))
                names = set(package.namelist())
                self.assertIn("d_web_runtime/people_log.csv", names)
                self.assertIn("d_web_runtime/event_log.csv", names)
                self.assertIn("d_web_runtime/metrics.csv", names)
                self.assertIn("d_web_runtime/evacuation_curve.svg", names)
                self.assertIn("d_web_runtime/occupancy_heatmap.svg", names)
                self.assertIn("d_web_runtime/inputs/map_file.json", names)
                self.assertIn("d_web_runtime/inputs/population_file.json", names)
        finally:
            try:
                post("/api/session/close", {})
            finally:
                server.shutdown()
                server.close_session()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
