from __future__ import annotations

import unittest

from experiments.metrics_registry import METRIC_REGISTRY, NA, metric_rows


class MetricsRegistryTests(unittest.TestCase):
    def test_registry_excludes_unconnected_information_metrics(self) -> None:
        keys = {definition.key for definition in METRIC_REGISTRY}
        self.assertIn("total_persons", keys)
        self.assertIn("overlap_cells", keys)
        self.assertNotIn("informed_rate", keys)

    def test_missing_risk_and_exit_utilization_remain_na(self) -> None:
        rows = {row["metric_name"]: row for row in metric_rows({})}
        self.assertEqual(NA, rows["avg_risk"]["value"])
        self.assertEqual(NA, rows["exit_utilization"]["value"])


if __name__ == "__main__":
    unittest.main()
