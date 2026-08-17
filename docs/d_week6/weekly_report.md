# D 端第六周周小结

## 本周依据

项目任务书第 6 章要求进入实验评估阶段，重点关注 `T_all`、`T90`、撤离率、出口选择、等待/拥堵、烟雾暴露、信息传播、群体行为以及策略改进率。D 本周继续负责结果可视化、日志分析和实验报告材料。

## 已完成

- 兼容 A/C 联合文件的原生 0-based 人员 ID：`person_id=0` 可进入快照、关系、跟随目标、事件和 CSV 日志；负数仍拒绝。
- 保留已有 integrated runner 的 source ID 映射规则，直接 B runtime 接入和 A/C 文件适配两条路径不会混淆。
- 新增 `experiments.week6_analysis`，从 `people_log.csv` 计算总步数、总时间、撤离人数、撤离率、首次/最后撤离时间、T90、烟雾、风险、剂量、拥堵、信息状态、群体凝聚度和出口分布。
- 出口分布改为按每个 `person_id` 的最终出口统计一次，避免人员每一步日志被重复计数。
- 支持 baseline 与策略运行的改进率比较，缺少 B/C 字段时输出 `NA`。
- 新增第六周运行说明和分析输出文件说明。

## 验收方式

```powershell
python -m unittest discover -q
python -m experiments.week6_analysis --run-dir outputs/experiments/<run_id>
```

## 待 A/B/C 确认或补充

- B 是否提供 `waiting_time_s`、`actual_exit`、风险和剂量的最终字段名。
- C/B 是否提供每步信息传播和群体行为统计字段，以便 D 计算信息扩散率和群体凝聚指标。
- 需要至少一组 baseline 和一组策略运行结果，才能形成任务书要求的策略改进率表。
- A 仍需最终确认 PNG 颜色到 wall/exit/obstacle/smoke_source 的语义映射。
