# D 第 4 周工作进度

## 本次集成结果

1. 已合并 `smoke-evacuation-ca-main (4).zip` 中的 A/B/C 最新源码与示例输入。
2. D 新增 `visualization/integrated_runtime.py`，支持一键 demo，也支持传入真实 A 地图、C 人员/关系输出和 C YAML 配置后进行 A+B+C+D 联调。
3. A 地图接入：JSON/CSV 继续走 A loader；PNG 走 A 的 `map_loader_image.load_image()` + `binary_to_grid()` 转 Grid。
4. C 配置接入：真实联调时读取 `control/config_template.yaml` 和 `control/output_people_position.json`；无完整输入时 D 使用 fallback 配置保证演示可运行。
5. B 接入：真实联调优先调用新版 `simulation.evac_simulation.EvacEngine`，由 `experiments.b_runtime_adapter.EvacEngineRuntimeAdapter` 暴露给 D 的 snapshot/logging。
6. D 输出：生成 `people_log.csv`、`event_log.csv`、`metrics.json`、`metrics_summary.csv`、`config_used.json`、`final_frame.png`。

## 已验证命令

默认 D demo：

```powershell
python visualization/integrated_runtime.py --max-steps 3 --persons 5
```

真实 A+B+C+D smoke test：

```powershell
python visualization/integrated_runtime.py `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --config control/config_template.yaml `
  --max-steps 2 `
  --run-id d_week4_abc_smoke_test
```

真实 integrated runner：

```powershell
python -m experiments.integrated_runner `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --yaml control/config_template.yaml `
  --max-steps 2 `
  --run-id d_integrated_runner_smoke_2 `
  --headless
```

关键单测：

```powershell
python -m unittest experiments.tests.test_csv_logger visualization.tests.test_ca_snapshot_adapter visualization.tests.test_runtime_entry visualization.tests.test_scene_input_adapter
```

结果：通过。

## 当前结论

- A 最新地图导入成果已接入到 D adapter。
- B 最新 `EvacEngine` 已真实接入。
- C 最新 YAML 和 `output_people_position.json` 已真实接入。
- 默认无输入 demo 仍保留 fallback，方便没有完整 A/C 输入时快速演示。

## 待确认

1. B 当前无烟源示例时，D 会使用 fallback smoke heatmap 做展示；正式烟源/烟雾场语义仍待 B/A 确认。
2. C 的逐步行为字典暂未提供，D 向 B 传空行为映射，不伪造策略数据。
3. B 的 Matplotlib 中文字体 warning 不影响 CSV、snapshot、结果包；Windows 控制台 emoji 编码问题已做最小兼容修复。

