# 给 B 的 D 可视化接入接口

D 已提供一个轻量的 Python 接口。B 继续负责 CA、烟雾扩散和每一步状态更新，D 只读取 B 更新后的状态并完成快照、动画、CSV 日志和结果包。

## 最小接入方式

在 B 创建 `EvacEngine` 后、第一次 `step()` 或 `run_one_step()` 之前接入：

```python
from visualization.runtime_entry import DVisualizationEntry

d_view = DVisualizationEntry(
    engine,
    output_root="outputs/experiments",
    run_id="b_smoke_demo_001",
    time_step_s=0.5,
)

try:
    d_view.start()  # 记录 step=0
    for _ in range(500):
        engine.run_one_step(c_step_data)  # 或 engine.step()
        snapshot = d_view.capture()       # 记录这一步并返回 D 标准快照
        if snapshot["people"] and all(p["evacuated"] for p in snapshot["people"]):
            break
finally:
    d_view.close()
```

如果 B 的入口是 `engine.step()`，D 适配层会自动调用该入口；如果是当前仓库版本的 `engine.run_one_step(c_step_data)`，则保留 B 的行为参数并传入空字典或 B 提供的字典。D 不会主动替 B 调用运动规则。

## B 需要提供的状态

接入时请保证引擎提供以下公开成员：

| 成员 | 用途 |
| --- | --- |
| `scene` | 场景配置，含 `scenario_id`、`exits`、`relations` |
| `grid` | A 的统一 `Grid`，含 `width`、`height`、`cells` |
| `person_map` | 人员字典，人员有 `id`、`x`、`y` |
| `smoke_matrix` | B 烟雾扩散矩阵，形状为 `[height][width]` |
| `current_step` | 当前时间步，每次 B 更新后加 1 |
| `run_one_step()` 或 `step()` | B 的真实一步推进入口 |
| `is_all_evacuated()` 或 `all_done()` | 是否结束 |

人员对象上的 `evacuated`、`dose`、`risk`、`group_id`、`info_state`、`target_exit_id` 等字段会被 D 读取；没有提供的字段保持空值，不由 D 伪造。烟雾数值按 B 原值记录和显示，单位及是否归一化请 B 确认。

## 输出和页面

每次 `capture()` 会追加：

`outputs/experiments/<run_id>/people_log.csv` 和 `event_log.csv`

当前结果目录还会保存 `config_used.json`、`metrics.json` 和 `metrics_summary.csv`（网页联调入口会自动写入）。网页演示入口是：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

启动服务：

```powershell
python -m experiments.web_runtime_server
```

如果 B 想保留自己的主循环，直接使用上面的 `DVisualizationEntry`；如果想让 D 网页服务代为驱动当前仓库样例，使用统一网页的“加载仓库样例 / 开始 / 单步”。

## 待 B 确认

- `smoke_matrix` 的单位、范围和坐标方向是否为 `smoke_matrix[y][x]`。
- `step()` 与 `run_one_step(c_step_data)` 的最终稳定入口。
- `evacuated` 的判定时刻，以及 `target_exit_id` / 实际出口字段名称。
- B 的烟雾矩阵是否已经与 `maps/edited_map.json` 使用同一宽高和坐标原点。
