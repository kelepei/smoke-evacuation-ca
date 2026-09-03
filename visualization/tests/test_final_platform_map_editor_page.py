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

    def test_ui_polish_keeps_dynamic_controls_stable_and_figures_readable(self) -> None:
        self.assertIn('id="autoPositionBtn" disabled>自动分配人员</button>', self.page)
        self.assertNotIn("自动分配并初始化</button>", self.page)
        self.assertNotIn('busy ? "计算中..."', self.page)
        self.assertIn('busy ? "计算中"', self.page)
        self.assertIn('class="primary run-control">开始</button>', self.page)
        self.assertLess(self.page.index('id="speedSelect"'), self.page.index('id="resetBtn"'))
        self.assertLess(self.page.index('id="resetBtn"'), self.page.index('id="startBtn"'))
        self.assertIn('class="figure-meta" id="curveNote"', self.page)
        self.assertIn('class="info-tooltip"', self.page)
        self.assertIn('class="info-tooltip-content" id="dataProvenance"', self.page)
        self.assertIn('.toolbar{height:50px;min-height:50px;flex:0 0 50px', self.page)
        self.assertIn('grid-template-columns:320px minmax(560px,1fr) 368px', self.page)
        self.assertIn('.init-control{flex:0 0 88px;min-width:88px;white-space:nowrap}', self.page)
        self.assertIn('.toolbar .spacer{flex:0 0 0;min-width:0}', self.page)
        self.assertIn('.run-control{flex:0 0 56px;min-width:56px;white-space:nowrap}', self.page)
        self.assertIn('#timeText{flex:0 0 86px;width:86px', self.page)
        self.assertIn('flex-wrap:nowrap;white-space:nowrap', self.page)
        self.assertIn('left:12px;right:auto;top:55px;width:auto;max-width:180px;height:32px', self.page)
        self.assertIn('$("layerNotice").title = noticeDetail', self.page)
        self.assertIn('canvas{width:100%;height:auto;min-height:0;flex:1 1 auto', self.page)
        self.assertIn('.run-summary{height:62px;min-height:62px;max-height:62px', self.page)
        self.assertIn('$("runSummary").title = summary', self.page)

    def test_integrated_editor_explicitly_supports_smoke_source(self) -> None:
        self.assertIn('data-type="smoke_source"', self.editor)
        self.assertIn('smoke_source:{label:"烟源"', self.editor)
        self.assertIn('"5":"smoke_source"', self.editor)

    def test_imported_smoke_sources_require_an_explicit_user_choice(self) -> None:
        for marker in (
            'id="smokeImportModal"',
            'id="keepImportedSmoke"',
            'id="clearImportedSmoke"',
            'id="cancelSmokeImport"',
            'await chooseImportedSmokeSources(importedSmokeSources)',
            'cell.type = "free"',
        ):
            self.assertIn(marker, self.page)
        self.assertIn("const cleaned = structuredClone(data)", self.page)
        self.assertNotIn("Math.random", self.page)

    def test_integrated_editor_keeps_the_a_data_round_trip_contract(self) -> None:
        self.assertIn("function loadMapData(data)", self.editor)
        self.assertIn("function getMapData()", self.editor)
        self.assertIn("JSON.parse(JSON.stringify(gridData))", self.editor)


if __name__ == "__main__":
    unittest.main()
