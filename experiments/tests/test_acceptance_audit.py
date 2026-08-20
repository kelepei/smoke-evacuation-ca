from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from experiments.acceptance_audit import run_acceptance_audit


class AcceptanceAuditTests(unittest.TestCase):
    def test_small_real_runtime_passes_audit(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cells = []
            for y in range(5):
                for x in range(5):
                    cell_type = "free"
                    if x in (0, 4) or y in (0, 4):
                        cell_type = "wall"
                    if (x, y) == (4, 2):
                        cell_type = "exit"
                    cells.append({"x": x, "y": y, "type": cell_type})
            map_path = root / "map.json"
            map_path.write_text(
                json.dumps(
                    {"name": "audit", "width": 5, "height": 5, "cell_size": 0.5, "cells": cells}
                ),
                encoding="utf-8",
            )
            people_path = root / "people.json"
            people_path.write_text(
                json.dumps({"persons": [{"id": 0, "x": 2, "y": 2}], "relations": []}),
                encoding="utf-8",
            )
            report, output_dir = run_acceptance_audit(
                map_path=map_path,
                population_path=people_path,
                yaml_path=None,
                output_root=root / "outputs",
                run_id="audit_run",
                max_steps=10,
            )
            self.assertEqual("PASS", report["status"])
            self.assertFalse(report["observations"]["smoke_field_exercised"])
            self.assertTrue((output_dir / "acceptance_report.json").is_file())
            self.assertTrue((output_dir / "people_log.csv").is_file())
            self.assertTrue((output_dir / "final_frame.png").is_file())


if __name__ == "__main__":
    unittest.main()
