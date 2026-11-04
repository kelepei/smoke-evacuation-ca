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
        self.assertLess(self.page.index('id="resetBtn"'), self.page.index('id="startBtn"'))
        self.assertIn('class="figure-meta" id="curveNote"', self.page)
        self.assertIn('class="info-tooltip"', self.page)
        self.assertIn('class="info-tooltip-content" id="dataProvenance"', self.page)
        self.assertIn('grid-template-columns:320px minmax(560px,1fr) 368px', self.page)
        self.assertIn('.init-control{flex:0 0 88px;min-width:88px;white-space:nowrap}', self.page)
        self.assertIn('.toolbar .spacer{flex:1 1 0;min-width:0}', self.page)
        self.assertIn('.run-control{flex:0 0 56px;min-width:56px;white-space:nowrap}', self.page)
        self.assertIn('.toolbar-options{display:flex;align-items:center;gap:5px;flex:0 0 auto}', self.page)
        self.assertIn('flex-wrap:nowrap;white-space:nowrap', self.page)
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

    def test_enhanced_page_uses_fixed_platform_physical_scale(self) -> None:
        self.assertIn("const PHYSICAL_CELL_SIZE_M = 0.5", self.page)
        self.assertIn("physical_cell_size_m:PHYSICAL_CELL_SIZE_M", self.page)
        self.assertIn("物理标定：0.5 m/格", self.page)
        self.assertIn("不等同于地图显示像素或实地测量", self.page)
        self.assertNotIn('id="physicalCellSizeM"', self.page)

    def test_academic_heatmaps_fit_real_analysis_extent_inside_svg_viewport(self) -> None:
        for label in ("累计占用", "Density", "CL", "CN", "Crowd Danger"):
            self.assertIn(label, self.page)
        self.assertIn("viewBox=\"0 0 520 360\"", self.page)
        self.assertIn("preserveAspectRatio=\"xMidYMid meet\"", self.page)
        self.assertIn("const columns=Math.max(1,maxX-minX+1), rows=Math.max(1,maxY-minY+1)", self.page)
        self.assertIn("const cell=Math.max(2,Math.min(plotWidth/columns,plotHeight/rows))", self.page)

    def test_enhanced_page_surfaces_runtime_and_analysis_timing_diagnostics(self) -> None:
        self.assertIn("extra.diagnostics.runtime_step_ms", self.page)
        self.assertIn("extra.diagnostics.artifact_write_ms", self.page)
        self.assertIn("extra.diagnostics.payload_bytes", self.page)
        self.assertIn("data.diagnostics.analysis_compute_ms", self.page)

    def test_client_api_timing_is_measured_without_changing_response_data(self) -> None:
        api_body = self.page[self.page.index("async function api(") : self.page.index("function normalizeCell(")]
        self.assertIn("const apiClientDiagnostics = new WeakMap()", self.page)
        self.assertIn(
            "numeric !== null && (numeric > 0 || responseBodyBytes === 0) ? numeric : null",
            self.page,
        )
        for marker in (
            "const requestStarted = performance.now()",
            "const headersReceived = performance.now()",
            "const bodyRead = performance.now()",
            "const decodeFinished = performance.now()",
            "const parseFinished = performance.now()",
            "const responseBody = await response.arrayBuffer()",
            'const text = new TextDecoder("utf-8").decode(responseBody)',
            "data = JSON.parse(text)",
            "headers_ms: headersReceived - requestStarted",
            "body_read_ms: bodyRead - bodyStarted",
            "text_decode_ms: decodeFinished - decodeStarted",
            "json_parse_ms: parseFinished - parseStarted",
            "http_total_ms: bodyRead - requestStarted",
            "client_total_ms: parseFinished - requestStarted",
            "response_body_bytes: responseBody.byteLength",
            "resourceByteCount(resourceEntry.transferSize, responseBody.byteLength)",
            "resourceByteCount(resourceEntry.encodedBodySize, responseBody.byteLength)",
            "resourceByteCount(resourceEntry.decodedBodySize, responseBody.byteLength)",
            "apiClientDiagnostics.set(data,",
            "if(!response.ok || !data.ok) throw new Error(data.error",
        ):
            self.assertIn(marker, api_body)
        self.assertNotIn("await response.json()", api_body)
        self.assertNotIn("await response.text()", api_body)
        self.assertNotIn("response.clone()", api_body)

    def test_client_timing_uses_na_for_unmeasured_values_and_ignores_stale_sessions(self) -> None:
        step_body = self.page[self.page.index("async function stepOnce()") : self.page.index("function scheduleNextStep()")]
        for marker in (
            "let lastDrawMs = null",
            "let lastGridDrawMs = null",
            "let lastSmokeDrawMs = null",
            "return numeric === null ? \"NA\" : `${numeric.toFixed(1)} ms`",
            "const clientTiming = clientDiagnosticsFor(result) || {}",
            "const requestGeneration = sessionGeneration",
            "if(requestGeneration !== sessionGeneration)",
            "旧会话的仿真响应已忽略",
            "响应头 ${formatDuration(clientTiming.headers_ms)}",
            "响应体读取 ${formatDuration(clientTiming.body_read_ms)}",
            "文本解码 ${formatDuration(clientTiming.text_decode_ms)}",
            "JSON 解析 ${formatDuration(clientTiming.json_parse_ms)}",
            "HTTP ${formatDuration(clientTiming.http_total_ms)}",
            "静态网格 ${formatDuration(lastGridDrawMs)}",
            "读取响应体 ${formatBytes(clientTiming.response_body_bytes)}",
            "编码响应体 ${formatBytes(clientTiming.encoded_body_bytes)}",
            "解码 JSON ${formatBytes(clientTiming.decoded_body_bytes)}",
        ):
            self.assertIn(marker, self.page if marker.startswith("let last") or marker.startswith("return numeric") else step_body)
        self.assertNotIn("result._client_diagnostics", self.page)
        self.assertNotIn("Number(serverTiming.runtime_step_ms || 0)", self.page)
        self.assertNotIn("Number(clientTiming.http_total_ms || 0)", self.page)

    def test_static_map_identity_uses_reference_without_per_step_serialization(self) -> None:
        apply_body = self.page[self.page.index("function applySnapshot(") : self.page.index("function finiteMean(")]
        self.assertIn("let currentStaticGrid = null", self.page)
        self.assertIn("const mapChanged = Boolean(nextGrid && nextGrid !== currentStaticGrid)", apply_body)
        self.assertIn("if(nextGrid) currentStaticGrid = nextGrid", apply_body)
        self.assertIn("else if(extra.resetHistory) currentStaticGrid = null", apply_body)
        self.assertIn("extra.resetMapView || extra.resetHistory || mapChanged", apply_body)
        self.assertNotIn("JSON.stringify(nextGrid.cell_type", apply_body)
        self.assertNotIn("currentMapViewKey", self.page)

    def test_alarm_cells_are_indexed_once_per_grid_draw(self) -> None:
        draw_grid = self.page[self.page.index("function drawGrid(") : self.page.index("function drawExitEntityLabels(")]
        self.assertLess(draw_grid.index("const alarmCells"), draw_grid.index("for(let y=0"))
        self.assertIn("const alarmCellKeys = new Set(alarmCells.map(", draw_grid)
        self.assertIn("if(alarmCellKeys.has(x + y * grid.width))", draw_grid)
        self.assertNotIn("alarms.some(", draw_grid)
        self.assertIn('ctx.strokeStyle = "#dc2626"', draw_grid)
        self.assertIn("ctx.strokeRect(ox + x * cell + 1, oy + y * cell + 1", draw_grid)

    def test_enhanced_page_records_synchronous_render_timing_without_promising_a_long_step(self) -> None:
        self.assertIn("appendFrontendRenderTiming", self.page)
        self.assertIn("前端同步绘制", self.page)
        self.assertNotIn("烟雾模型可能需要十几秒", self.page)

    def test_round2_contract_is_visible_without_claiming_lambda_support(self) -> None:
        for marker in (
            'id="lambdaValue" disabled',
            'id="lambdaExperimentEnabled" type="checkbox"',
            'fetch("/api/capabilities")',
            "physical_cell_size_m:PHYSICAL_CELL_SIZE_M",
            'id="relationFilter"',
            "snapshot.relations",
            'id="actualRunTime"',
            "performance.now()",
            'id="exportLambdaBtn"',
        ):
            self.assertIn(marker, self.page)
        self.assertIn("普通疏散实验不受影响", self.page)
        self.assertIn("if(lambdaEnabled && lambdaCapability.supported !== true)", self.page)

    def test_manual_acceptance_exit_panel_and_clock_contract(self) -> None:
        self.assertIn("暂无人员撤离，出现实际撤离记录后显示。", self.page)
        self.assertIn('value.textContent = `${Number(count)}人 · ${percent.toFixed(1)}%`', self.page)
        self.assertIn('id="topActualRunTime">实际运行 00:00:00.0', self.page)
        self.assertLess(self.page.index('id="topActualRunTime"'), self.page.index('id="timeText"'))
        self.assertIn("formatElapsedRunTime(elapsed)", self.page)
        self.assertIn('`Step ${currentStep} / 模拟 ${timeValue(simulatedTime)}s`', self.page)

    def test_toolbar_uses_two_rows_with_primary_controls_kept_on_first_row(self) -> None:
        self.assertIn(".toolbar-primary{flex-wrap:nowrap;white-space:nowrap}", self.page)
        self.assertIn(".toolbar-time{flex:0 0 118px;width:118px", self.page)
        primary = self.page[self.page.index('<div class="toolbar-row toolbar-primary">') : self.page.index('<div class="toolbar-row toolbar-layers">')]
        for control_id in (
            "initBtn", "guideToggle", "broadcastToggle", "topActualRunTime", "timeText", "resetBtn",
            "pauseBtn", "stepBtn", "startBtn",
        ):
            self.assertIn(f'id="{control_id}"', primary)
        self.assertNotIn('id="speedSelect"', primary)
        self.assertNotIn('id="mapExpandBtn"', primary)
        layers = self.page[self.page.index('<div class="toolbar-row toolbar-layers">') : self.page.index('</div>\n  </div>\n    <canvas id="view"')]
        for control_id in ("relationFilter", "speedSelect", "mapExpandBtn"):
            self.assertIn(f'id="{control_id}"', layers)

    def test_compact_desktop_layout_keeps_primary_controls_reachable(self) -> None:
        self.assertIn(
            "@media(max-width:1400px){main{grid-template-columns:260px minmax(0,1fr) 300px",
            self.page,
        )
        self.assertIn("toolbar-primary .good", self.page)
        self.assertIn("run-controls{gap:2px}", self.page)

    def test_user_facing_copy_avoids_internal_workstream_labels(self) -> None:
        self.assertNotIn("A+B+C", self.page)
        self.assertNotIn("C 人员", self.page)
        self.assertNotIn("A 地图", self.page)
        self.assertIn("物理标定：0.5 m/格", self.page)
        self.assertIn("平台统一仿真假设", self.page)

    def test_exit_entity_names_are_stable_display_only_labels(self) -> None:
        self.assertIn("function exitDisplayName(exitId, sourceSnapshot = snapshot)", self.page)
        self.assertIn('/^exit_entity_(\\d+)$/i.exec(rawId)', self.page)
        self.assertIn('return `出口${chineseExitOrdinal(Number(canonical[1]))}`', self.page)
        self.assertIn('label.textContent = exitDisplayName(name)', self.page)
        self.assertIn('label.title = String(name)', self.page)
        self.assertIn('exitDisplayName(recommendation.recommended_exit_entity)', self.page)
        self.assertNotIn('ctx.fillText("EXIT"', self.page)
        self.assertIn('drawExitEntityLabels(ox, oy, cell)', self.page)

    def test_exit_display_mapping_does_not_mutate_machine_fields(self) -> None:
        display_helper = self.page[
            self.page.index("function exitDisplayName") : self.page.index("function displayedRunMs")
        ]
        self.assertNotIn("actual_exit", display_helper)
        self.assertNotIn("target_exit", display_helper)
        self.assertNotIn("exit_usage", display_helper)

    def test_animation_loop_defers_expensive_analysis_until_pause_or_completion(self) -> None:
        step_body = self.page[self.page.index("async function stepOnce()") : self.page.index("function scheduleNextStep()")]
        self.assertNotIn("refreshLogLayers()", step_body)
        self.assertIn("if(result.finished)", step_body)
        self.assertIn("refreshRealAnalysis()", step_body)
        self.assertIn("exitDistributionSignature !== lastExitDistributionSignature", step_body)
        self.assertIn("refreshCoreMetrics()", step_body)
        self.assertIn('id="clientStepTiming"', self.page)
        self.assertIn("json_parse_ms", self.page)
        self.assertIn("lastSmokeDrawMs", self.page)
        self.assertIn("snapshot.runtime_metrics", self.page)
        self.assertIn("exitDistributionSignature !== lastExitDistributionSignature", self.page)
        self.assertIn("requestedGeneration !== sessionGeneration", self.page)
        self.assertNotIn("result.snapshot.step - lastAnalysisStep >= 25", step_body)
        self.assertIn('termination.reason || ""', step_body)
        self.assertIn("unfinished_count", step_body)

    def test_product_copy_hides_internal_team_labels(self) -> None:
        self.assertIn("关系紧密程度（0–1）", self.page)
        self.assertNotIn("relation_intensity：0=关系最弱", self.page)
        self.assertNotIn("A 地图（JSON / CSV / PNG）", self.page)
        self.assertNotIn("B 正在计算 step", self.page)

    def test_relation_filter_uses_real_edges_and_current_positions(self) -> None:
        self.assertIn("relation.person_a_id", self.page)
        self.assertIn("relation.person_b_id", self.page)
        self.assertIn("relation.relation_type", self.page)
        self.assertIn("positions.set(String(person.person_id", self.page)
        self.assertNotIn("group_id) ===", self.page)

    def test_exports_use_bounded_requests_and_delayed_blob_cleanup(self) -> None:
        self.assertIn('fetchWithTimeout("/api/session/export", 180000', self.page)
        self.assertIn('fetchWithTimeout("/api/session/lambda.csv", 30000', self.page)
        self.assertIn("document.body.appendChild(link)", self.page)
        self.assertIn("triggerBlobDownload(blob, responseFilename", self.page)
        self.assertIn("setTimeout(() => URL.revokeObjectURL(url), 60000)", self.page)
        self.assertNotIn("link.click();\n    URL.revokeObjectURL", self.page)

    def test_analysis_splits_fast_base_from_academic_fields(self) -> None:
        self.assertIn('fetchWithTimeout("/api/session/analysis-base", 30000', self.page)
        self.assertIn('fetchWithTimeout("/api/session/academic-analysis", 180000', self.page)
        self.assertIn("基础撤离曲线、占用热力图和出口指标已刷新", self.page)
        self.assertIn("学术分析计算中", self.page)
        self.assertIn("requestedRunId !== (snapshot && snapshot.run_id)", self.page)


if __name__ == "__main__":
    unittest.main()
