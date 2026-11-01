from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from experiments.lambda_experiment import (
    LambdaExperimentError,
    lambda_capability,
    lambda_movement_settings,
    lambda_request_config,
)
from experiments.web_runtime_server import WebRuntimeError, _control_runtime_config
from experiments.week6_analysis import _exit_balance, _q95


class LambdaExperimentContractTests(unittest.TestCase):
    def test_supported_lambda_values_and_formal_pressure_gain(self) -> None:
        capability = lambda_capability()
        self.assertEqual(
            [0.0, 0.2, 0.4, 0.6, 0.8],
            capability["allowed_lambda_values"],
        )
        self.assertEqual(3.0, capability["risk_feedback_cap"])
        self.assertEqual(0.5, capability["lambda_exit_pressure_gain"])

    def test_current_b_runtime_reports_verified_feedback_support(self) -> None:
        capability = lambda_capability()
        self.assertTrue(capability["supported"])
        self.assertEqual("supported", capability["status"])
        self.assertTrue(capability["implementation_verified"])
        self.assertEqual([], capability["missing_fields"])

    def test_missing_runtime_method_is_not_reported_as_supported(self) -> None:
        from simulation.evac_simulation import EvacEngine

        with patch.object(EvacEngine, "_apply_panic_risk_feedback", None):
            capability = lambda_capability()
        self.assertFalse(capability["supported"])
        self.assertFalse(capability["implementation_verified"])

    def test_normal_movement_override_is_preserved_without_lambda_mode(self) -> None:
        config = _control_runtime_config(
            {
                "movement": {
                    "panic": {
                        "threshold": 0.33,
                        "panic_to_risk_gain": 0.4,
                    },
                    "exit_choice": {"exit_pressure_gain": 0.7},
                }
            }
        )
        self.assertEqual(0.33, config["movement"]["panic"]["threshold"])
        self.assertEqual(0.4, config["movement"]["panic"]["panic_to_risk_gain"])
        self.assertEqual(0.7, config["movement"]["exit_choice"]["exit_pressure_gain"])

    def test_contract_builds_documented_mapping_without_applying_formula(self) -> None:
        settings = lambda_movement_settings(0.4, require_supported=False)
        self.assertEqual(0.4, settings["panic"]["panic_to_risk_gain"])
        self.assertEqual(3.0, settings["panic"]["risk_feedback_cap"])
        self.assertEqual(0.5, settings["exit_choice"]["exit_pressure_gain"])

    def test_rejects_invalid_lambda_request(self) -> None:
        with self.assertRaises(LambdaExperimentError):
            lambda_movement_settings(0.3, require_supported=False)
        config = _control_runtime_config(
            {"lambda_experiment": True, "panic_to_risk_gain": 0.4}
        )
        self.assertEqual(0.4, config["movement"]["panic"]["panic_to_risk_gain"])
        self.assertEqual(3.0, config["movement"]["panic"]["risk_feedback_cap"])
        self.assertEqual(0.5, config["movement"]["exit_choice"]["exit_pressure_gain"])
        self.assertIsNone(lambda_request_config({"lambda_experiment": False, "panic_to_risk_gain": 0.4}))

    def test_b_feedback_formula_zero_gain_and_cap(self) -> None:
        from simulation.evac_simulation import EvacEngine

        engine = EvacEngine.__new__(EvacEngine)
        engine.person_map = {1: SimpleNamespace(panic=0.5)}
        engine.movement = SimpleNamespace(
            panic=SimpleNamespace(panic_to_risk_gain=0.4, risk_feedback_cap=3.0)
        )
        self.assertAlmostEqual(1.2, engine._apply_panic_risk_feedback({1: 1.0})[1])
        self.assertEqual(3.0, engine._apply_panic_risk_feedback({1: 10.0})[1])
        original = {1: 1.0}
        engine.movement.panic.panic_to_risk_gain = 0.0
        self.assertIs(original, engine._apply_panic_risk_feedback(original))

    def test_real_b_step_applies_feedback_once_to_fresh_risk(self) -> None:
        from scenarios.mock_data import build_base_scene
        from simulation.evac_simulation import EvacEngine

        observed = {}
        for gain in (0.0, 0.4):
            engine = EvacEngine(build_base_scene())
            engine.movement.panic.panic_to_risk_gain = gain
            engine.movement.panic.risk_feedback_cap = 3.0
            for person in engine.person_map.values():
                person.panic = 0.5
            fresh = {pid: 1.0 for pid in engine.person_map}
            with patch.object(engine.risk_engine, "batch_calc_all_risk", return_value=fresh):
                engine.run_one_step({})
            observed[gain] = engine.person_map[1].risk
        self.assertEqual(1.0, observed[0.0])
        self.assertAlmostEqual(1.2, observed[0.4])

    def test_exit_balance_includes_declared_zero_use_exit(self) -> None:
        entropy, gini = _exit_balance([36, 2, 0])
        self.assertAlmostEqual(0.187684, entropy, places=6)
        self.assertAlmostEqual(0.631579, gini, places=6)
        self.assertEqual(("NA", "NA"), _exit_balance([38]))
        self.assertEqual(("NA", "NA"), _exit_balance([0, 0]))

    def test_q95_uses_final_person_values(self) -> None:
        self.assertEqual(3.85, _q95([1.0, 2.0, 3.0, 4.0]))
        self.assertEqual("NA", _q95([]))


if __name__ == "__main__":
    unittest.main()
