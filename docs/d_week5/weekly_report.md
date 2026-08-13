# D 端第五周周小结

## 1. 本周目标

完成 A 地图、C 人员配置、B CA/烟雾状态与 D 可视化页面的统一联调，形成可运行、可演示、可记录结果的版本。

## 2. 已完成工作

- 地图上传后调用 A 的 JSON/CSV/PNG loader，统一生成 `Grid`，D 按 `cell.x`、`cell.y`、`cell_type` 绘制墙体、障碍、出口和自由区域。
- 统一网页入口支持地图预览、人员配置、开始、暂停、单步、重置和结果包导出。
- C 配置面板支持 `total_persons`、`profile_ratios`、`group_config`、`relation_intensity` 和 `random_seed`。
- B 正式单步接口确认为 `run_one_step(c_step_data)`；D 读取 B 的 `smoke_matrix[y][x]`。
- 烟雾显示和记录统一采用无量纲 `0~10`，0 表示无烟，10 表示烟源最大浓度。
- 每一步生成 `people_log.csv`，首次撤离写入 `event_log.csv`，同步生成 `metrics.json`、`metrics_summary.csv` 和 `final_frame.png`。
- 新增给 B 的 Python 接入说明：`docs/d_week5/b_visualization_api.md`。

## 3. 验收结果

在当前仓库运行全量测试：

```text
Ran 36 tests ... OK
```

网页 API 已验证：地图预览、真实会话初始化、B 单步、结果包导出均返回成功；输出目录包含配置、CSV、指标和最终帧图片。

## 4. 接口责任

- A：提供地图 loader 和统一 `Grid`；PNG 的颜色语义仍需 A 最终确认。
- C：提供人员属性、群组关系和 YAML 配置；`total_persons` 必须与人员 JSON 数量一致。
- B：提供 `run_one_step(c_step_data)`、人员状态、`smoke_matrix[y][x]` 和撤离状态。
- D：负责快照适配、地图/烟雾/人员显示、控制逻辑、CSV 日志、指标和结果包。

## 5. 当前限制与下周建议

- B 尚未提供的字段（如风险、实际出口、部分信息传播字段）保持为空，D 不伪造。
- B 的烟雾单位和坐标方向已确认；仍建议在 B 最终代码中固定公开属性名称，避免后续字段改名。
- 建议下周用 B 的真实烟雾扩散场跑一次完整场景，保存一张最终截图并由 A/B/C 共同确认视觉与日志字段。

## 6. 运行命令

```powershell
python -m experiments.web_runtime_server
```

浏览器打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```
