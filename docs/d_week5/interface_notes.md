# D 第五周接口说明

## 已接入

- A 地图：通过 `visualization.scene_input_adapter.load_map_grid()` 调用 A 的 JSON/CSV/PNG loader，并统一转换为 Grid 预览快照。
- D 前端渲染：统一入口 `visualization/prototype/integrated_runtime.html` 遍历 `snapshot.grid.cell_type[y][x]` 绘制 free、wall、obstacle、exit、smoke_source、sign、guide_zone。
- C 配置：统一前端内置人员配置面板，可生成 C YAML；后端仍通过 `control/scene_config.py` 的 SceneConfig loader 校验。
- C 人群：通过 `control/output_people_position.json` 或用户上传 JSON 接入 persons/relations。
- B 仿真：通过 `experiments.integrated_runner` 和 `EvacEngineRuntimeAdapter` 调用 B 正式入口 `EvacEngine.run_one_step(c_step_data)`，读取 `smoke_matrix[y][x]`，烟雾范围为无量纲 `0~10`；D 端不修改 B 算法。
- D 日志：每个 step 写入 `people_log.csv`，首次撤离写入 `event_log.csv`，并同步生成 metrics。

## 本次修复的问题

之前前端“地图已导入”只代表浏览器选中了文件；没有单独请求 A 地图 loader，也没有在只上传地图时生成 Grid 预览，因此画布不会变化。

现在新增 `/api/map/preview`：地图文件上传后立即由 A loader 转成 Grid，D 再按 Grid 重绘画布。PNG/JSON/CSV 最终都走同一条 Grid 渲染链路。

## 待 A/B/C 确认

- A：PNG loader 的颜色/语义映射规则是否已稳定，尤其是墙体、出口、障碍、烟源在 PNG 中的编码。
- B：已确认正式单步入口为 `run_one_step(c_step_data)`；烟雾矩阵坐标为 `[y][x]`，无量纲范围 `0~10`。D 的 web bridge 仍在服务端启用 headless 后端，避免 B 内部 GUI 绘制影响网页服务。
- C：`total_persons` 必须与上传的人群 JSON 人数一致，否则 D 会拒绝初始化，避免伪造人员。
