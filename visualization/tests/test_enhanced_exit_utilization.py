from __future__ import annotations

import unittest
from pathlib import Path


class EnhancedExitUtilizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.page = Path("visualization/prototype/final_platform_map_editor.html").read_text(encoding="utf-8")

    def test_live_topology_selects_entity_once_and_uses_entity_field(self) -> None:
        self.assertIn("liveExitUtilizationRepresentation", self.page)
        self.assertIn('next.exit_entities) && next.exit_entities.length > 0', self.page)
        self.assertIn('representation === "entity"', self.page)
        self.assertIn("person && person.actual_exit_entity", self.page)

    def test_legacy_fallback_is_explicit_not_per_snapshot(self) -> None:
        self.assertIn('"legacy_cell"', self.page)
        self.assertIn("旧日志未提供实体出口，按 actual_exit 统计", self.page)
        self.assertIn("exit_utilization_contract", self.page)


if __name__ == "__main__":
    unittest.main()
