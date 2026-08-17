# D 端第六周运行与分析说明

## 运行一次仿真

```powershell
python -m experiments.web_runtime_server
```

打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

网页运行后，结果会保存到：

```text
outputs/experiments/<run_id>/
```

## 分析一次运行

```powershell
python -m experiments.week6_analysis `
  --run-dir outputs/experiments/<run_id>
```

分析结果会写回该目录：

- `week6_metrics.json`
- `week6_metrics_summary.csv`

缺失的上游字段会记录为 `NA`，例如没有 `waiting_time_s` 时不会根据位置变化猜等待时间。

## 比较 baseline 与策略运行

```powershell
python -m experiments.week6_analysis `
  --run-dir outputs/experiments/<strategy_run_id> `
  --baseline-dir outputs/experiments/<baseline_run_id> `
  --output-dir outputs/experiments/week6_comparison
```

当前支持的比较指标包括总时间、最后撤离时间、T90、平均烟雾、平均风险、平均剂量和平均拥堵。改进率按“baseline - strategy”计算，正数表示策略降低了该指标。

出口选择分布按每个 `person_id` 的最终 `actual_exit` 或 `target_exit` 统计一次，不按每一步重复累计。
