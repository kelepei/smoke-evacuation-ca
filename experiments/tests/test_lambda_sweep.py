from __future__ import annotations

import unittest

from experiments.lambda_sweep import DEFAULT_LAMBDA_LEVELS, _verified_exit_results


class LambdaSweepContractTests(unittest.TestCase):
    def test_default_levels_match_formal_experiment(self) -> None:
        self.assertEqual("0,0.2,0.4,0.6,0.8", DEFAULT_LAMBDA_LEVELS)

    def test_actual_usage_includes_zero_use_open_exit(self) -> None:
        usage, entropy_h, gini_g = _verified_exit_results(
            {
                "exit_universe": {
                    "status": "available",
                    "valid_exit_ids": ["exit_entity_01", "exit_entity_02"],
                },
                "metrics": {"exit_usage": {"exit_entity_01": 3}},
                "people": [{"target_exit": "exit_entity_02", "evacuated": False}],
            }
        )
        self.assertEqual({"exit_entity_01": 3, "exit_entity_02": 0}, usage)
        self.assertEqual(0.0, entropy_h)
        self.assertEqual(0.5, gini_g)

    def test_unverified_exit_universe_is_na(self) -> None:
        self.assertEqual(
            ("NA", "NA", "NA"),
            _verified_exit_results(
                {
                    "exit_universe": {"status": "unavailable"},
                    "metrics": {"exit_usage": {"exit_entity_01": 3}},
                }
            ),
        )


if __name__ == "__main__":
    unittest.main()
