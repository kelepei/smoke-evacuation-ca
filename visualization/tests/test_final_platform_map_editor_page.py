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
        self.assertIn(".jpg,.jpeg,.bmp", self.page)

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
        self.assertIn('.toolbar .spacer{flex:1 1 auto;min-width:0}', self.page)
        self.assertIn('@media(max-width:1640px){.layer-controls{display:none}}', self.page)
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

    def test_enhanced_map_has_view_only_zoom_controls_without_touching_stable_page(self) -> None:
        for marker in (
            'id="mapExpandBtn"',
            'id="mapExpandedView"',
            "function zoomMapAt",
            "function attachMapInteractions",
            'target.addEventListener("wheel"',
            'if(event.button !== 1) return',
            "function resetAllMapViews",
            "function mapTransform",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn("mapExpandedView", self.stable)

    def test_enhanced_page_explains_spawn_area_capacity_without_touching_stable_page(self) -> None:
        self.assertIn("function showSpawnCapacityError", self.page)
        self.assertIn("可生成人员容量不足", self.page)
        self.assertIn("spawn-area 规则", self.page)
        self.assertIn("人员重叠或生成到墙体、出口、烟源及地图外区域", self.page)
        self.assertIn("showSpawnCapacityError(error, \"人员生成\")", self.page)
        self.assertIn("showSpawnCapacityError(error, \"自动位置分配\")", self.page)
        self.assertNotIn("showSpawnCapacityError", self.stable)

    def test_enhanced_page_uses_b06_normalized_smoke_scale(self) -> None:
        self.assertIn("B06 publishes a normalized smoke concentration in [0, 1].", self.page)
        self.assertIn("const value = Math.max(0, Math.min(1, rawValue));", self.page)
        self.assertNotIn("Math.max(0, Math.min(1, rawValue / 10))", self.page)
        self.assertNotIn("B06 publishes a normalized smoke concentration", self.stable)

    def test_large_map_extrema_do_not_spread_full_fields_into_math_functions(self) -> None:
        for page in (self.stable, self.page, self.editor):
            self.assertNotIn("Math.max(...values)", page)
        self.assertNotIn("Math.max(...points.map", self.page)
        self.assertIn("large map can contain 100k+ cells", self.page)
        self.assertIn("Avoid spreading 100k+ large-map cells", self.stable)

    def test_enhanced_page_exposes_real_information_modes_and_state_colors(self) -> None:
        for marker in (
            'id="informationMode"',
            'value="broadcast"',
            'value="local"',
            "function informationModePayload()",
            "information_mode:mode",
            'id="alarmState"',
            "已报警 · 局部传播",
            "const knowsFire = [\"ALERTED\", \"CONFIRMED\", \"GUIDED\", \"INFORMED\"].includes(infoState);",
            "烟雾暴露外环",
        ):
            self.assertIn(marker, self.page)
        self.assertNotIn('id="informationMode"', self.stable)

    def test_enhanced_page_exposes_optional_demo_physical_scale(self) -> None:
        self.assertIn('id="physicalCellSizeM"', self.page)
        self.assertIn("假设尺度／演示用途", self.page)
        self.assertIn("physical_cell_size_m:physicalCellSizeM", self.page)
        self.assertIn("不会覆盖地图原有 cell_size", self.page)
        for marker in ('id="analysisMeshSizeM"', 'id="samplingWindowS"', 'id="roiRadiusM"'):
            self.assertIn(marker, self.page)
        self.assertIn("analysis_mesh_size_m:analysisMeshSizeM", self.page)
        self.assertIn("sampling_window_s:samplingWindowS", self.page)
        self.assertIn("roi_radius_m:roiRadiusM", self.page)
        self.assertIn("不等同于地图元胞尺寸", self.page)

    def test_academic_heatmaps_fit_real_analysis_extent_inside_svg_viewport(self) -> None:
        for label in ("累计占用", "Density", "CL", "CN", "Crowd Danger"):
            self.assertIn(label, self.page)
        self.assertIn("viewBox=\"0 0 520 360\"", self.page)
        self.assertIn("preserveAspectRatio=\"xMidYMid meet\"", self.page)
        self.assertIn("const columns=Math.max(1,maxX-minX+1), rows=Math.max(1,maxY-minY+1)", self.page)
        self.assertIn("const cell=Math.max(2,Math.min(plotWidth/columns,plotHeight/rows))", self.page)


if __name__ == "__main__":
    unittest.main()
