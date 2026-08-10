# D 第四周：B 运行时与 people_log.csv 适配说明

## 已直接接入

B 当前提交的正式入口为：

```python
engine = EvacEngine(scene)
engine.run_one_step(c_step_data)
engine.is_all_evacuated()
```

D 通过 `experiments/b_runtime_adapter.py` 读取公开的 `grid`、`person_map`、
`smoke_matrix` 和 `current_step`，供快照、网页、CSV 日志与结果包使用。

## D 运行时适配

B 当前 `EvacEngine` 会读取 `person.evacuated`、`person.dose` 和
`grid.get_cell()`，而现有共享实例并不总创建这些成员。D 仅在当前运行的内存
对象上补上 B 代码已假定的中性默认值：`evacuated=False`、`dose=0.0`，以及基于
已有 `cells` 的 `get_cell(x, y)` 查找。

这不修改 A/B/C 源文件、不改变 B 的移动规则，也不宣称为完整联调；所有此类
默认值都会写入 `snapshot.adapter_meta.runtime_instance_defaults`。

## B people_log.csv

D 的 `visualization.people_log_adapter` 可只读解析 B 发来的列：

```text
step,time_s,person_id,x,y,evacuated,heading,risk,dose,conflict,exit_switch
```

`step,time_s,person_id,x,y,evacuated` 为必填列。空的可选列仍显示为缺失，D 不会
用演示数据补齐。若多人在同一步拥有相同坐标，解析器会保留该事实用于回放和
核查，不重新布点或去重。

## 仍需 B / 团队确认

- `c_step_data` 的字段、生产者与语义；当前 C 未提供时 D 只传空字典；
- 烟雾浓度单位与归一化规则。D 当前只要求非负并原样记录，不假定范围是 0–1；
- `conflict`、`exit_switch` 的事件语义，以及实际出口编号的输出格式；
- B 的当前移动策略是否应允许所有人员重叠、或在同一格之间往返。
