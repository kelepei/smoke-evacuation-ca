from __future__ import annotations

import unittest
from pathlib import Path


class FinalPlatformAutoFlowPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = Path("visualization/prototype")
        cls.stable_page = (root / "final_platform.html").read_text(encoding="utf-8")
        cls.auto_page = (root / "final_platform_auto_flow.html").read_text(encoding="utf-8")

    def test_stable_page_has_no_experimental_auto_position_entry(self) -> None:
        self.assertNotIn("autoPositionBtn", self.stable_page)
        self.assertNotIn("/api/session/auto-position", self.stable_page)

    def test_experimental_page_uses_only_real_json_auto_position_route(self) -> None:
        self.assertIn('id="autoPositionBtn"', self.auto_page)
        self.assertIn("/api/session/auto-position", self.auto_page)
        self.assertIn("allocate_positions", self.auto_page)
        self.assertIn("仅支持 A JSON 编辑结果", self.auto_page)
