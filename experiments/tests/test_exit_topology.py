from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from core.schema import Cell, CellType, Grid, Person
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from experiments.exit_topology import entity_id_by_cell, extract_exit_entities
from experiments.week6_analysis import analyze_run
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


def make_grid(width: int, height: int, exits: set[tuple[int, int]], walls: set[tuple[int, int]] | None = None) -> Grid:
    walls = walls or set()
    cells = []
    for y in range(height):
        for x in range(width):
            cell_type = CellType.EXIT if (x, y) in exits else CellType.WALL if (x, y) in walls else CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))
    return Grid(width=width, height=height, cell_size=0.5, cells=cells)


class _ExitRecordingEngine:
    def __init__(self, grid: Grid) -> None:
        self.grid = grid
        self.scene = SimpleNamespace(scenario_id="exit_topology_test", exits=[], relations=[], smoke_sources=[])
        self.person_map = {
            1: Person(id=1, x=1, y=1),
            2: Person(id=2, x=2, y=1),
        }
        self.smoke_matrix = np.zeros((grid.height, grid.width), dtype=float)
        self.exits = [(1, 1, "exit_1"), (2, 1, "exit_2")]
        self.current_step = 0

    def run_one_step(self, _behavior: dict) -> None:
        for person, exit_id in zip(self.person_map.values(), ("exit_1", "exit_2"), strict=True):
            person.actual_exit = exit_id
            person.evacuated = True
        self.current_step += 1

    def is_all_evacuated(self) -> bool:
        return all(person.evacuated for person in self.person_map.values())


class ExitTopologyTests(unittest.TestCase):
    def test_horizontal_contiguous_cells_form_one_entity(self) -> None:
        entities = extract_exit_entities(make_grid(6, 4, {(1, 2), (2, 2), (3, 2)}), cell_size_m=0.5)
        self.assertEqual(1, len(entities))
        self.assertEqual(((1, 2), (2, 2), (3, 2)), entities[0].member_cells)
        self.assertEqual((2.0, 2.0), entities[0].centroid)
        self.assertEqual(3, entities[0].cell_count)
        self.assertEqual(1.5, entities[0].width_m)

    def test_vertical_contiguous_cells_form_one_entity(self) -> None:
        entities = extract_exit_entities(make_grid(5, 6, {(3, 1), (3, 2), (3, 3)}))
        self.assertEqual(1, len(entities))
        self.assertEqual("exit_entity_01", entities[0].exit_entity_id)
        self.assertIsNone(entities[0].width_m)
        self.assertIn("no validated cell_size_m", entities[0].width_note or "")

    def test_wall_separated_exit_groups_remain_distinct(self) -> None:
        entities = extract_exit_entities(make_grid(6, 4, {(1, 1), (3, 1)}, {(2, 1)}))
        self.assertEqual(2, len(entities))
        self.assertEqual(["exit_entity_01", "exit_entity_02"], [entity.exit_entity_id for entity in entities])

    def test_diagonal_exit_cells_remain_distinct(self) -> None:
        entities = extract_exit_entities(make_grid(5, 5, {(1, 1), (2, 2)}))
        self.assertEqual(2, len(entities))

    def test_single_exit_cell_forms_one_entity(self) -> None:
        entities = extract_exit_entities(make_grid(4, 4, {(2, 1)}), cell_size_m=0.5)
        self.assertEqual(1, len(entities))
        self.assertEqual(0.5, entities[0].width_m)

    def test_no_exit_map_returns_no_entities(self) -> None:
        self.assertEqual((), extract_exit_entities(make_grid(4, 4, set())))

    def test_d_mapping_preserves_cell_exit_and_adds_shared_entity_exit(self) -> None:
        grid = make_grid(5, 4, {(1, 1), (2, 1)})
        entities = extract_exit_entities(grid)
        engine = _ExitRecordingEngine(grid)
        adapter = EvacEngineRuntimeAdapter(
            engine,
            exit_entities=entities,
            exit_entity_by_cell_id={"exit_1": "exit_entity_01", "exit_2": "exit_entity_01"},
        )
        adapter.step()

        self.assertEqual("exit_1", engine.person_map[1].actual_exit)
        self.assertEqual("exit_1", engine.person_map[1].actual_exit_cell)
        self.assertEqual("exit_entity_01", engine.person_map[1].actual_exit_entity)
        self.assertEqual("exit_entity_01", engine.person_map[2].actual_exit_entity)

        snapshot = CaSnapshotAdapter(run_id="topology_test", time_step_s=0.5).capture(adapter)
        self.assertEqual(1, len(snapshot["exit_entities"]))
        self.assertEqual("exit_1", snapshot["people"][0]["actual_exit"])
        self.assertEqual("exit_1", snapshot["people"][0]["actual_exit_cell"])
        self.assertEqual("exit_entity_01", snapshot["people"][0]["actual_exit_entity"])
        self.assertEqual("unavailable", snapshot["analysis_contract"]["physical_scale"]["source"])
        self.assertIn("guidance", snapshot)

    def test_entity_level_exit_utilization_is_one_bucket_and_sums_to_one(self) -> None:
        fields = ["step", "time_s", "person_id", "x", "y", "evacuated", "actual_exit", "actual_exit_cell", "actual_exit_entity"]
        rows = [
            {"step": 0, "time_s": 0, "person_id": 1, "x": 1, "y": 1, "evacuated": False, "actual_exit": "", "actual_exit_cell": "", "actual_exit_entity": ""},
            {"step": 0, "time_s": 0, "person_id": 2, "x": 2, "y": 1, "evacuated": False, "actual_exit": "", "actual_exit_cell": "", "actual_exit_entity": ""},
            {"step": 1, "time_s": 0.5, "person_id": 1, "x": 1, "y": 1, "evacuated": True, "actual_exit": "exit_1", "actual_exit_cell": "exit_1", "actual_exit_entity": "exit_entity_01"},
            {"step": 1, "time_s": 0.5, "person_id": 2, "x": 2, "y": 1, "evacuated": True, "actual_exit": "exit_2", "actual_exit_cell": "exit_2", "actual_exit_entity": "exit_entity_01"},
        ]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "people_log.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            metrics = analyze_run(path.parent)
        self.assertEqual({"exit_entity_01": 1.0}, metrics["exit_utilization"])
        self.assertEqual(1.0, sum(metrics["exit_utilization"].values()))

    def test_cell_to_entity_mapping_covers_every_member_cell_once(self) -> None:
        entities = extract_exit_entities(make_grid(5, 4, {(1, 1), (2, 1), (4, 3)}))
        self.assertEqual(
            {(1, 1): "exit_entity_01", (2, 1): "exit_entity_01", (4, 3): "exit_entity_02"},
            entity_id_by_cell(entities),
        )


if __name__ == "__main__":
    unittest.main()
