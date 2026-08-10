# D 可视化程序接入入口（第四周）

## 作用

`visualization.runtime_entry.DVisualizationEntry` 是统一主程序使用的 D 侧接入点。它不接管 A 的地图加载、C 的行为计算或 B 的元胞自动机移动；它只在每一步读取已经运行的 B 状态，形成统一快照，保存 `people_log.csv`、`event_log.csv`，并可导出 D 的结果包。

## A 侧接入方式

在 A 完成“加载地图 → 生成人员/关系 → 创建 B 的 `EvacEngine`”之后：

```python
from visualization.runtime_entry import DVisualizationEntry

d_view = DVisualizationEntry(sim, output_root="outputs", run_id="abcd_run_001", time_step_s=0.5)
d_view.start()  # 必须在 B 的第 0 步、第一次 run_one_step 前调用

for _ in range(max_steps):
    sim.run_one_step(c_step_data=behavior_package, signage_model=signage_model)
    snapshot = d_view.capture()  # 网页、图表和 CSV 可使用此快照
    if sim.is_all_evacuated():
        break

d_view.close()
```

`snapshot` 可直接交给 D 的网页或图表层显示。D 不替 A/B/C 编造缺失字段。

## 已验证输入

- A：`maps/edited_map.json`；
- C/A 已分配坐标的人群：`control/output_people_position.json`（40 人）；
- B：`simulation.evac_simulation.EvacEngine`；
- D：第 0 步挂接、B 真实运行一步后成功捕获快照并写出两类 CSV 日志。

## 直接运行方式

```powershell
python -m experiments.integrated_runner --map maps/edited_map.json --population control/output_people_position.json --headless --max-steps 50
```

浏览器版：`python -m experiments.web_runtime_server`，然后访问 `http://127.0.0.1:8765/`。

## 当前边界

- B 当前会自行创建 Matplotlib 动画窗口；D 的网页和日志读取同一个真实运行状态，但不修改 B 的绘图逻辑。
- C 尚未提供逐步行为字典时，D 的独立入口向 B 传递空行为映射；A 已生成 `behavior_package` 时，应由 A 正常传给 B。
- `heading`、`risk`、`dose`、`conflict`、`exit_switch` 等字段仍以 B/C 的正式输出为准，缺失时 D 保持为空值。
