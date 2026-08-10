from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.schema import Cell, CellType, Grid, Person, ScenarioConfig
from visualization.runtime_entry import DVisualizationEntry, DVisualizationEntryError


class _LiveRuntime:
    def __init__(self) -> None:
        cells = [
            Cell(x=0, y=0, cell_type=CellType.FREE),
            Cell(x=1, y=0, cell_type=CellType.EXIT),
            Cell(x=0, y=1, cell_type=CellType.WALL),
            Cell(x=1, y=1, cell_type=CellType.WALL),
        ]
        self.grid = Grid(width=2, height=2, cell_size=0.5, cells=cells)
        self.config = ScenarioConfig(scenario_id="d_entry_test", grid=self.grid)
        person = Person(id=1, x=0, y=0)
        person.evacuated = False
        person.dose = 0.0
        self.persons = {1: person}
        self.current_step = 0
        self.smoke_matrix = [[0.0, 0.0], [0.0, 0.0]]

    def advance(self) -> None:
        person = self.persons[1]
        person.x = 1
        person.evacuated = True
        self.current_step += 1


class _RawBShape:
    def __init__(self) -> None:
        base = _LiveRuntime()
        self.scene = base.config
        self.grid = base.grid
        self.person_map = base.persons
        self.smoke_matrix = base.smoke_matrix
        self.current_step = 0

    def run_one_step(self, _behavior: dict | None = None) -> None:
        self.person_map[1].x = 1
        self.person_map[1].evacuated = True
        self.current_step += 1

    def is_all_evacuated(self) -> bool:
        return bool(self.person_map[1].evacuated)


class DVisualizationEntryTests(unittest.TestCase):
    def test_records_existing_runtime_without_owning_step_logic(self) -> None:
        runtime = _LiveRuntime()
        with tempfile.TemporaryDirectory() as raw:
            entry = DVisualizationEntry(runtime, output_root=raw, run_id="d_entry_test", random_seed=7)
            try:
                self.assertEqual(0, entry.start()["step"])
                runtime.advance()  # Existing A/B/C loop owns this step.
                current = entry.capture()
                self.assertEqual(1, current["step"])
                self.assertTrue(current["people"][0]["evacuated"])
                self.assertTrue((Path(raw) / "d_entry_test" / "people_log.csv").is_file())
                self.assertTrue((Path(raw) / "d_entry_test" / "event_log.csv").is_file())
            finally:
                entry.close()

    def test_rejects_late_attachment(self) -> None:
        runtime = _LiveRuntime()
        runtime.current_step = 1
        with tempfile.TemporaryDirectory() as raw:
            entry = DVisualizationEntry(runtime, output_root=raw, run_id="late")
            with self.assertRaises(DVisualizationEntryError):
                entry.start()

    def test_accepts_current_raw_b_shape(self) -> None:
        runtime = _RawBShape()
        with tempfile.TemporaryDirectory() as raw:
            entry = DVisualizationEntry(runtime, output_root=raw, run_id="raw_b")
            try:
                self.assertEqual(0, entry.start()["step"])
                runtime.run_one_step({})
                self.assertEqual(1, entry.capture()["step"])
            finally:
                entry.close()


if __name__ == "__main__":
    unittest.main()
