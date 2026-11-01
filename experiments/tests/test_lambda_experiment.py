from __future__ import annotations

import unittest

from experiments.lambda_experiment import (
    LambdaExperimentError,
    lambda_capability,
    lambda_movement_settings,
    lambda_request_config,
)
from experiments.web_runtime_server import WebRuntimeError, _control_runtime_config
from experiments.week6_analysis import _exit_balance, _q95


class LambdaExperimentContractTests(unittest.TestCase):
    def test_current_b_runtime_is_explicitly_unavailable(self) -> None:
        capability = lambda_capability()
        self.assertFalse(capability["supported"])
        self.assertEqual("unavailable", capability["status"])
        self.assertFalse(capability["implementation_verified"])
        self.assertIn("panic_to_risk_gain", capability["missing_fields"])
        self.assertIn("risk_feedback_cap", capability["missing_fields"])
        self.assertIn(
            "B implementation marker PANIC_RISK_FEEDBACK_IMPLEMENTED",
            capability["missing_fields"],
        )

    def test_contract_builds_documented_mapping_without_applying_formula(self) -> None:
        settings = lambda_movement_settings(0.4, require_supported=False)
        self.assertEqual(0.4, settings["panic"]["panic_to_risk_gain"])
        self.assertEqual(3.0, settings["panic"]["risk_feedback_cap"])
        self.assertEqual(0.5, settings["exit_choice"]["exit_pressure_gain"])

    def test_rejects_unsupported_or_invalid_lambda_request(self) -> None:
        with self.assertRaises(LambdaExperimentError):
            lambda_movement_settings(0.3, require_supported=False)
        with self.assertRaises(WebRuntimeError):
            _control_runtime_config({"lambda_experiment": True, "panic_to_risk_gain": 0.4})
        self.assertIsNone(lambda_request_config({"lambda_experiment": False, "panic_to_risk_gain": 0.4}))

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
