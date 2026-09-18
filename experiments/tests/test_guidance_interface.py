from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.schema import Cell, CellType, Grid, Person, SmokeSource
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from experiments.guidance_interface import generate_guidance, write_guidance_artifacts
from experiments.run_artifacts import write_run_artifacts
from simulation.smoke_model import SmokeDiffusionModel
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


def _snapshot(*, alarm: bool = True, smoke: list[list[float]] | None = None) -> dict:
    width, height = 7, 3
    field = smoke if smoke is not None else [[0.0] * width for _ in range(height)]
    return {
        "step": 8,
        "time_s": 4.0,
        "grid": {"width": width, "height": height, "cell_type": [["free"] * width for _ in range(height)]},
        "fields": {"smoke_field": field, "alarm_triggered": alarm, "max_smoke_concentration": max(max(row) for row in field)},
        "exit_entities": [
            {"exit_entity_id": "exit_entity_near", "member_cells": [[2, 1], [2, 2]]},
            {"exit_entity_id": "exit_entity_far", "member_cells": [[6, 1]]},
        ],
        "people": [
            {"person_id": 1, "x": 1, "y": 1, "evacuated": False, "target_exit": "exit_01"},
            {"person_id": 2, "x": 1, "y": 0, "evacuated": True, "target_exit": "exit_02"},
        ],
    }


class _B06Engine:
    def __init__(self, smoke_engine: SmokeDiffusionModel, grid: Grid) -> None:
        self.scene = SimpleNamespace(scenario_id="b06", parameters={})
        self.grid = grid
        self.person_map = {1: Person(id=1, x=0, y=0)}
        self.smoke_engine = smoke_engine
        self.smoke_matrix = smoke_engine.smoke_matrix
        self.current_step = 0

    def run_one_step(self, _behavior: dict) -> None:
        self.current_step += 1

    def is_all_evacuated(self) -> bool:
        return False


class GuidanceInterfaceTests(unittest.TestCase):
    def test_no_smoke_prefers_shorter_entity_path(self) -> None:
        guidance = generate_guidance(_snapshot(), trigger_mode="manual")
        self.assertEqual("active", guidance["status"])
        self.assertEqual("smoke_distance_baseline_v1", guidance["policy_id"])
        self.assertEqual("exit_entity_near", guidance["recommendations"][0]["recommended_exit_entity"])

    def test_smoke_dominant_baseline_can_choose_farther_lower_smoke_route(self) -> None:
        smoke = [[0.0] * 7 for _ in range(3)]
        smoke[1][2] = 1.0
        guidance = generate_guidance(_snapshot(smoke=smoke))
        recommendation = guidance["recommendations"][0]
        self.assertEqual("exit_entity_far", recommendation["recommended_exit_entity"])
        self.assertEqual("LOWER_SMOKE_ROUTE", recommendation["reason_code"])

    def test_similar_smoke_prefers_shorter_path(self) -> None:
        smoke = [[0.2] * 7 for _ in range(3)]
        guidance = generate_guidance(_snapshot(smoke=smoke))
        self.assertEqual("exit_entity_near", guidance["recommendations"][0]["recommended_exit_entity"])

    def test_unreachable_entity_is_not_selected(self) -> None:
        snapshot = _snapshot()
        snapshot["grid"]["cell_type"][1][2] = "wall"
        snapshot["grid"]["cell_type"][2][2] = "wall"
        guidance = generate_guidance(snapshot)
        self.assertEqual("exit_entity_far", guidance["recommendations"][0]["recommended_exit_entity"])

    def test_explicitly_unavailable_entity_is_not_selected(self) -> None:
        snapshot = _snapshot()
        snapshot["exit_entities"][0]["available"] = False
        guidance = generate_guidance(snapshot)
        self.assertEqual("exit_entity_far", guidance["recommendations"][0]["recommended_exit_entity"])

    def test_contiguous_exit_cells_produce_entity_not_cell_recommendation(self) -> None:
        guidance = generate_guidance(_snapshot(), trigger_mode="manual")
        identifier = guidance["recommendations"][0]["recommended_exit_entity"]
        self.assertEqual("exit_entity_near", identifier)
        self.assertNotIn("exit_01", identifier)

    def test_false_alarm_is_inactive_and_manual_mode_is_explicit(self) -> None:
        snapshot = _snapshot(alarm=False)
        inactive = generate_guidance(snapshot)
        self.assertEqual("inactive", inactive["status"])
        self.assertEqual("guidance.v1", inactive["schema_version"])
        self.assertEqual(8, inactive["generated_step"])
        self.assertIsNone(inactive["unavailable_reason"])
        self.assertEqual("active", generate_guidance(snapshot, trigger_mode="manual")["status"])

    def test_missing_runtime_input_is_unavailable_with_stable_schema(self) -> None:
        snapshot = _snapshot()
        del snapshot["fields"]["smoke_field"]
        guidance = generate_guidance(snapshot)
        self.assertEqual("unavailable", guidance["status"])
        self.assertEqual("SMOKE_FIELD_UNAVAILABLE", guidance["reason_code"])
        self.assertIn("smoke_field", guidance["unavailable_reason"])
        self.assertEqual([], guidance["recommendations"])

    def test_true_alarm_generates_deterministic_recommendations_without_mutation(self) -> None:
        snapshot = _snapshot()
        original = copy.deepcopy(snapshot)
        first = generate_guidance(snapshot)
        second = generate_guidance(snapshot)
        self.assertEqual(first, second)
        self.assertEqual(original, snapshot)
        self.assertEqual([1], [row["person_id"] for row in first["recommendations"]])
        self.assertFalse(first["policy"]["academic_fields_used"])

    def test_artifacts_log_route_costs_and_reason(self) -> None:
        guidance = generate_guidance(_snapshot())
        with tempfile.TemporaryDirectory() as raw:
            write_guidance_artifacts(guidance, Path(raw))
            event_text = (Path(raw) / "guidance_events.csv").read_text(encoding="utf-8")
            self.assertIn("route_smoke_cost", event_text)
            self.assertIn("LOWEST_COMBINED_COST", event_text)

    def test_real_b06_smoke_no_source_and_threshold_projection(self) -> None:
        cells = [Cell(x=x, y=y, cell_type=CellType.FREE) for y in range(3) for x in range(3)]
        grid = Grid(width=3, height=3, cells=cells)
        # B06's smoke model relies on the runtime's public grid lookup.
        grid.get_cell = lambda x, y: grid.cells[y * grid.width + x] if 0 <= x < grid.width and 0 <= y < grid.height else None
        no_source = SmokeDiffusionModel(grid, diffuse_coeff=0.24)
        no_source.update_smoke()
        self.assertEqual(0.0, no_source.get_max_smoke())
        self.assertFalse(EvacEngineRuntimeAdapter(_B06Engine(no_source, grid)).alarm_triggered)

        below_threshold = SmokeDiffusionModel(grid, diffuse_coeff=0.24)
        below_threshold.add_smoke_source(SmokeSource(x=1, y=1, intensity=0.01))
        below_threshold.update_smoke()
        self.assertLess(below_threshold.get_max_smoke(), 0.45)
        self.assertFalse(EvacEngineRuntimeAdapter(_B06Engine(below_threshold, grid)).alarm_triggered)

        smoke = SmokeDiffusionModel(grid, diffuse_coeff=0.24)
        smoke.add_smoke_source(SmokeSource(x=1, y=1, intensity=1.0))
        smoke.update_smoke()
        engine = _B06Engine(smoke, grid)
        engine.smoke_matrix = smoke.smoke_matrix
        adapter = EvacEngineRuntimeAdapter(engine)
        self.assertGreater(adapter.max_smoke_concentration or 0.0, 0.45)
        self.assertTrue(adapter.alarm_triggered)
        projected = CaSnapshotAdapter(run_id="b06", time_step_s=0.5).capture(adapter)
        self.assertTrue(projected["fields"]["alarm_triggered"])
        self.assertGreater(projected["fields"]["max_smoke_concentration"], 0.45)
        self.assertEqual("d_projection_from_b06_smoke", projected["fields"]["alarm_source"])
        self.assertEqual("unavailable", projected["guidance"]["status"])

    def test_artifact_persists_the_same_live_snapshot_guidance(self) -> None:
        snapshot = _snapshot()
        snapshot["guidance"] = generate_guidance(snapshot)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_run_artifacts(snapshot, root, input_files={"map": "map.json"}, save_frame=False)
            saved = (root / "guidance_recommendations.json").read_text(encoding="utf-8")
            self.assertIn('"generated_step": 8', saved)
            self.assertIn('"smoke_distance_baseline_v1"', saved)


if __name__ == "__main__":
    unittest.main()
