from __future__ import annotations

import unittest

from experiments.b_runtime_adapter import EvacEngineRuntimeAdapter
from experiments.c_runtime_bridge import CWebRuntimeBridge
from scenarios.mock_data import build_base_scene
from simulation.evac_simulation import EvacEngine
from visualization.ca_snapshot_adapter import CaSnapshotAdapter


class CWebRuntimeBridgeTests(unittest.TestCase):
    def _runtime(self, *, ratio: float = 0.0) -> tuple[EvacEngine, CWebRuntimeBridge, EvacEngineRuntimeAdapter]:
        scene = build_base_scene()
        scene.parameters = {"d_c_runtime": {"initial_informed_ratio": ratio, "alarm_enabled": True}}
        engine = EvacEngine(scene)
        bridge = CWebRuntimeBridge(engine)
        return engine, bridge, EvacEngineRuntimeAdapter(engine, behavior_provider=bridge)

    def test_initial_information_is_partial_and_step_twenty_is_not_a_broadcast(self) -> None:
        engine, bridge, runtime = self._runtime(ratio=1 / 3)
        initial = engine.c_runtime_state
        self.assertEqual(1, initial["informed_count"])
        bridge.info_diffusion.wom_params["enabled"] = False
        bridge.info_diffusion.rel_params["enabled"] = False
        engine.alarm_threshold = 2.0
        engine.current_step = 20
        runtime.step()
        self.assertEqual(1, engine.c_runtime_state["informed_count"])
        self.assertFalse(bridge.info_diffusion.alarm_triggered)

    def test_native_alarm_broadcasts_and_switches_guides(self) -> None:
        engine, _bridge, runtime = self._runtime(ratio=0.0)
        runtime.step()
        self.assertTrue(engine.is_alarm_triggered)
        state = engine.c_runtime_state
        self.assertTrue(state["alarm_broadcast_triggered"])
        self.assertEqual(state["active_person_count"], state["informed_count"])
        self.assertEqual("guiding", state["guide_state"])

    def test_snapshot_contains_real_c_state_guide_metadata_and_death_flag(self) -> None:
        engine, _bridge, runtime = self._runtime(ratio=0.0)
        runtime.step()
        engine.person_map[1].is_dead = True
        location = (engine.person_map[1].x, engine.person_map[1].y)
        runtime.step()
        self.assertEqual(location, (engine.person_map[1].x, engine.person_map[1].y))
        snapshot = CaSnapshotAdapter(run_id="c_bridge", time_step_s=0.5).capture(runtime)
        self.assertIn("c_runtime", snapshot)
        self.assertTrue(snapshot["guides"])
        self.assertEqual("#ff7f0e", snapshot["guides"][0]["color"])
        person = next(item for item in snapshot["people"] if item["person_id"] == 1)
        self.assertTrue(person["is_dead"])
        self.assertIn(person["info_state"], {"UNKNOWN", "ALERTED", "CONFIRMED", "GUIDED"})

    def test_speed_gate_records_cadence_state(self) -> None:
        engine, _bridge, runtime = self._runtime(ratio=0.0)
        engine.person_map[1].speed = 0.4
        engine.person_map[2].speed = 1.6
        starts = {pid: (p.x, p.y) for pid, p in engine.person_map.items()}
        # 第 1 步警报在 B 内部触发、C 在步末才同步知情状态，第 2 步起体现速度门控。
        runtime.step()
        runtime.step()
        speed_state = engine.c_runtime_state["speed_model"]
        self.assertTrue(speed_state["enabled"])
        self.assertGreaterEqual(speed_state["blocked_total"], 1)
        # 慢的人（0.4 m/s）累计位移应少于快的人（1.6 m/s），而不是"一步都不许走"。
        def travelled(pid: int) -> int:
            person = engine.person_map[pid]
            sx, sy = starts[pid]
            return abs(int(person.x) - sx) + abs(int(person.y) - sy)
        self.assertGreater(travelled(2), travelled(1))

    def test_web_mode_is_an_explicit_c_runtime_contract(self) -> None:
        scene = build_base_scene()
        scene.parameters = {
            "d_c_runtime": {
                "initial_informed_ratio": 0.0,
                "alarm_enabled": True,
                "web_mode": True,
            }
        }
        bridge = CWebRuntimeBridge(EvacEngine(scene))
        self.assertTrue(bridge.web_mode)
        self.assertTrue(bridge.engine.c_runtime_state["web_smoke_semantics"])

    def test_local_mode_preserves_b_alarm_without_global_c_broadcast(self) -> None:
        scene = build_base_scene()
        scene.persons[0].x, scene.persons[0].y = 4, 3
        scene.persons[1].x, scene.persons[1].y = 5, 3
        scene.persons[2].x, scene.persons[2].y = 15, 9
        scene.parameters = {"d_c_runtime": {
            "web_mode": True,
            "information_mode": "local",
            "alarm_enabled": False,
            "smoke_local_radius": 1.1,
            "unknown_behavior": "freeze",
        }}
        engine = EvacEngine(scene)
        # Lower only this test engine's detector threshold so B's native alarm
        # occurs during the first step despite the current gradual smoke ramp.
        engine.alarm_threshold = 0.0
        bridge = CWebRuntimeBridge(engine)
        runtime = EvacEngineRuntimeAdapter(engine, behavior_provider=bridge)

        self.assertEqual("local", engine.c_runtime_state["information_mode"])
        self.assertEqual(1, engine.c_runtime_state["informed_count"])
        runtime.step()
        state = engine.c_runtime_state
        self.assertTrue(engine.is_alarm_triggered)
        self.assertEqual("local", state["information_mode"])
        self.assertFalse(state["alarm_broadcast_triggered"])
        # Local mode delegates information changes to C's actual perception
        # and relationship logic.  In this layout the first tick need not
        # reach another person, but it must never become a global broadcast.
        self.assertLess(state["informed_count"], state["active_person_count"])


if __name__ == "__main__":
    unittest.main()
