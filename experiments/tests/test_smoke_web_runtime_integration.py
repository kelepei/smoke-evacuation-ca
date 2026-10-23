"""D end-to-end checks for B smoke output and the normalized Web snapshot."""

from __future__ import annotations

import unittest

from core.schema import Cell, CellType, Exit, Grid, Person, ScenarioConfig, SmokeSource
from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from simulation.evac_simulation import EvacEngine
from simulation.smoke_model import SmokeDiffusionModel
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


def _grid(*, width: int = 7, height: int = 5, barrier: bool = False) -> Grid:
    cells = []
    for y in range(height):
        for x in range(width):
            cell_type = CellType.WALL if barrier and x == width // 2 else CellType.FREE
            cells.append(Cell(x=x, y=y, cell_type=cell_type))
    return Grid(width=width, height=height, cell_size=0.5, cells=cells)


class SmokeWebRuntimeIntegrationTests(unittest.TestCase):
    def test_wall_barrier_blocks_practical_smoke_transfer_to_other_side(self) -> None:
        """Check the field beyond a full barrier, not merely wall-cell values."""

        def field_with(barrier: bool):
            # Match EvacEngine's actual B Runtime constructor rather than the
            # smoke model's standalone default.
            model = SmokeDiffusionModel(
                _grid(width=9, height=7, barrier=barrier), diffuse_coeff=0.24
            )
            model.add_smoke_source(SmokeSource(x=2, y=3, intensity=1.0))
            for _ in range(5):
                model.update_smoke()
            return model.smoke_matrix

        blocked = field_with(True)
        open_field = field_with(False)
        barrier_x = 4
        self.assertEqual(float(blocked[:, barrier_x].max()), 0.0)
        # The current Gaussian source retains an extremely small numerical tail
        # beyond the barrier; it is over six orders below the open-path field
        # and below C/D's real smoke exposure threshold (0.02).
        self.assertLess(float(blocked[:, barrier_x + 1 :].max()), 1e-6)
        self.assertGreater(float(open_field[:, barrier_x + 1 :].max()), 1e-3)

    def test_smoke_changes_real_b_movement_and_snapshot_reports_same_state(self) -> None:
        def run(with_smoke: bool):
            grid = _grid()
            grid.cells[2 * grid.width + 6].cell_type = CellType.EXIT
            scene = ScenarioConfig(
                scenario_id="smoke-avoidance",
                grid=grid,
                exits=[Exit(id="exit_1", x=6, y=2)],
                persons=[Person(id=1, x=1, y=2, info_state="INFORMED")],
                smoke_sources=[SmokeSource(x=2, y=2, intensity=1.0)] if with_smoke else [],
                parameters={"time_step_s": 0.5, "random_seed": 42},
            )
            engine = EvacEngine(scene)
            adapter = EvacEngineRuntimeAdapter(engine)
            adapter.step()
            snapshot = CaSnapshotAdapter(
                run_id="smoke-web-test", time_step_s=0.5, random_seed=42
            ).capture(adapter)
            return (engine.person_map[1].x, engine.person_map[1].y), snapshot

        baseline_position, baseline = run(False)
        smoke_position, smoke = run(True)

        self.assertEqual(baseline_position, (2, 2))
        # 新规则下"躲烟"的具体格子由出口距离/烟雾/软最大化共同决定，
        # 只要不进入 1.0 浓度的烟源格 (2,2)，就说明烟雾确实改变了移动。
        self.assertNotEqual(smoke_position, (2, 2))
        self.assertNotEqual(smoke_position, (1, 2))
        self.assertEqual(smoke["people"][0]["x"], smoke_position[0])
        self.assertEqual(smoke["people"][0]["y"], smoke_position[1])
        self.assertEqual(smoke["fields"]["smoke_field"][2][2], 1.0)
        self.assertEqual(baseline["fields"]["smoke_field"][2][2], 0.0)


if __name__ == "__main__":
    unittest.main()
