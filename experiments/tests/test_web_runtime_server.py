from __future__ import annotations

import json
import tempfile
import threading
import unittest
from urllib.request import urlopen
from pathlib import Path

from experiments.web_runtime_server import (
    RuntimeWebError,
    RuntimeWebService,
    create_server,
)


def _write_map(path: Path) -> None:
    cells = []
    for y in range(5):
        for x in range(5):
            cell_type = "wall" if x in (0, 4) or y in (0, 4) else "free"
            if (x, y) == (4, 2):
                cell_type = "exit"
            cells.append({"x": x, "y": y, "type": cell_type})
    path.write_text(json.dumps({"width": 5, "height": 5, "cell_size": 0.5, "cells": cells}), encoding="utf-8")


class RuntimeWebServiceTests(unittest.TestCase):
    def test_serves_integrated_runtime_page(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        server = create_server(port=0, repository_root=repository_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f"http://127.0.0.1:{server.server_address[1]}/", timeout=3) as response:
                page = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertIn("D ?????", page)
        self.assertIn("/api/start", page)

    def test_starts_steps_exports_and_closes_real_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            map_path = root / "map.json"; _write_map(map_path)
            population_path = root / "population.json"
            population_path.write_text(json.dumps({"persons": [{"id": 0, "x": 1, "y": 1}], "relations": []}), encoding="utf-8")
            service = RuntimeWebService(output_root=root / "outputs")
            initial = service.start({"map_path": str(map_path), "population_path": str(population_path)})
            after_step = service.step()
            package = service.export()
            self.assertTrue(package.is_file())
            service.close()

        self.assertEqual(initial["step"], 0)
        self.assertEqual(after_step["step"], 1)
        with self.assertRaisesRegex(RuntimeWebError, "no active"):
            service.step()
