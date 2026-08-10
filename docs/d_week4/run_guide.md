# D 第 4 周集成 demo 运行说明

## 一键演示

```powershell
python visualization/integrated_runtime.py
```

默认使用 `maps/examples/simple_room.json`，没有 C population 时由 D 生成演示人员，输出到：

```text
outputs/experiments/<run_id>/
```

## 真实 A+B+C+D 联调

```powershell
python visualization/integrated_runtime.py `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --config control/config_template.yaml `
  --max-steps 80
```

这一路径会接入：

- A：`maps/edited_map.json`
- C：`control/output_people_position.json`、`control/config_template.yaml`
- B：`simulation.evac_simulation.EvacEngine`
- D：snapshot、动画、CSV、指标与最终帧导出

## GUI 控制

```powershell
python visualization/integrated_runtime.py --gui
```

支持 Start、Pause、Single step、Reset。

## 输出文件

每次运行目录中至少包含：

- `config_used.json`
- `people_log.csv`
- `event_log.csv`
- `metrics.json`
- `metrics_summary.csv`
- `final_frame.png`

