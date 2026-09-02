from __future__ import annotations

import unittest
from pathlib import Path


class FinalPlatformMapEditorPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stable = Path("visualization/prototype/final_platform.html").read_text(encoding="utf-8")
        cls.page = Path("visualization/prototype/final_platform_map_editor.html").read_text(encoding="utf-8")
        cls.editor = Path("visualization/prototype/map_editor_integrated.html").read_text(encoding="utf-8")

    def test_stable_page_has_no_editor_integration_markup(self) -> None:
        self.assertNotIn("mapEditorFrame", self.stable)
        self.assertNotIn("currentMapData", self.stable)

    def test_experimental_page_keeps_one_confirmed_map_state(self) -> None:
        for marker in (
            'id="mapEditorFrame"',
            "let currentMapData = null",
            "structuredClone(currentMapData)",
            "currentMapData = edited",
            "function cancelMapEdit()",
            "/api/map/preview-data",
            "/api/session/auto-position",
        ):
            self.assertIn(marker, self.page)
        self.assertIn("PNG / CSV 保留原导入运行方式", self.page)

    def test_integrated_editor_explicitly_supports_smoke_source(self) -> None:
        self.assertIn('data-type="smoke_source"', self.editor)
        self.assertIn('smoke_source:{label:"烟源"', self.editor)
        self.assertIn('"5":"smoke_source"', self.editor)

    def test_integrated_editor_keeps_the_a_data_round_trip_contract(self) -> None:
        self.assertIn("function loadMapData(data)", self.editor)
        self.assertIn("function getMapData()", self.editor)
        self.assertIn("JSON.parse(JSON.stringify(gridData))", self.editor)


if __name__ == "__main__":
    unittest.main()
