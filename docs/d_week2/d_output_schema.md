# D 端标准输出格式草案 v0.1

## 1. 文档定位

本文档是 D 模块用于接收 A、B、C 模块输出的接口草案，目的是为可视化、日志记录和后续统计提供一个稳定的最小数据模板。

- 当前内容属于项目前两周阶段的草案，不是最终冻结版本。
- 正式联调前，A、B、C、D 应共同确认字段、类型、单位、枚举和存储方式。
- 每次仿真开始前，应依据 `schema_version` 固定本次运行使用的字段集合；仿真运行后不再临时增加字段。
- 本草案优先描述 D 端需要接收的标准化数据，不要求上游模块采用相同的内部数据结构。
- 与第一周文档不一致时，本草案采用当前团队统一规则；仍有歧义的内容统一列入“待团队确认项”。

## 2. 命名与编码规范

1. 所有字段名统一使用小写 `snake_case`。
2. `person_id` 使用全局唯一正整数，并在所有模块和相关输出文件中保持一致。
3. 无值或尚未产生的值统一使用 JSON 的 `null`，不使用空字符串、`"none"` 或特殊数字代替。
4. 枚举值的大小写按各字段的明确约定使用，不对所有枚举作统一小写规定。
5. JSON 文件统一使用 UTF-8 编码。
6. JSON 布尔值使用 `true` 和 `false`。
7. 比例或权重建议使用 `[0, 1]` 范围内的数值；最终范围仍以团队确认结果为准。
8. 字段含义或计算口径发生不兼容变化时，必须更新 `schema_version`。

## 3. 时间规范

1. `step` 为整数，从 `0` 开始。
2. 仿真时间按以下关系计算：

   ```text
   time_s = step * time_step
   ```

3. `time_step` 从 YAML 配置文件读取，单位为秒。
4. `receive_time` 保存整数仿真步数，不直接保存秒。
5. D 展示信息接收时间时，使用以下关系换算：

   ```text
   receive_time_s = receive_time * time_step
   ```

6. 当行人尚未接收到信息时，`receive_time` 使用 `null`。
7. `time_s` 是便于读取和展示的派生字段，应与 `step` 和 `time_step` 的计算结果一致。

## 4. 初始化数据

初始化数据在仿真开始前提供，用于建立场景、网格、出口和烟雾源等静态内容。建议最小结构如下：

```json
{
  "run_id": "run_001",
  "scenario_id": "scenario_001",
  "schema_version": "0.1-draft",
  "random_seed": 42,
  "time_step": 0.5,
  "grid_width": 3,
  "grid_height": 3,
  "cell_size": 0.5,
  "cell_type": [
    ["wall", "wall", "wall"],
    ["wall", "free", "exit"],
    ["wall", "free", "exit"]
  ],
  "exits": [
    {
      "exit_id": "exit_01",
      "cells": [
        {"x": 2, "y": 1},
        {"x": 2, "y": 2}
      ]
    }
  ],
  "smoke_sources": [
    {
      "source_id": "smoke_source_01",
      "x": 1,
      "y": 1,
      "start_step": 0
    }
  ]
}
```

> 上述 `cell_type` 仅展示二维数组结构，不代表数组第一维和第二维的最终含义。二维数组索引顺序必须由团队确认。

### 4.1 初始化字段说明

| 字段 | 类型 | 必填 | 含义 | 主要来源 |
|---|---|---:|---|---|
| `run_id` | string | 是 | 单次仿真运行编号 | 运行配置/团队约定 |
| `scenario_id` | string | 是 | 场景唯一编号 | A |
| `schema_version` | string | 是 | 本次运行采用的输出格式版本 | 团队约定 |
| `random_seed` | integer | 是 | 本次运行的随机种子 | 运行配置 |
| `time_step` | number | 是 | 单步对应的秒数，从 YAML 读取 | 运行配置 |
| `grid_width` | integer | 是 | 网格宽度，单位为元胞 | A |
| `grid_height` | integer | 是 | 网格高度，单位为元胞 | A |
| `cell_size` | number | 是 | 元胞物理边长，单位为米 | A |
| `cell_type` | array | 是 | 全场元胞类型二维数组 | A |
| `exits` | array | 是 | 出口编号及其初始空间信息 | A |
| `smoke_sources` | array | 是 | 烟雾源编号、位置和启动步等初始化信息 | A/B |

`cell_type` 只允许使用以下枚举：

- `free`
- `wall`
- `obstacle`
- `exit`
- `smoke_source`
- `sign`
- `guide_zone`

## 5. 每步仿真快照

每个时间步向 D 提供一份 `simulation_snapshot`。建议最小结构如下：

```json
{
  "schema_version": "0.1-draft",
  "run_id": "run_001",
  "scenario_id": "scenario_001",
  "step": 20,
  "time_step": 0.5,
  "time_s": 10.0,
  "people": [],
  "exits": [],
  "fields": {},
  "relations": [],
  "events": [],
  "strategy_state": {}
}
```

| 字段 | 类型 | 必填 | 含义 |
|---|---|---:|---|
| `schema_version` | string | 是 | 快照使用的输出格式版本 |
| `run_id` | string | 是 | 与初始化数据一致的运行编号 |
| `scenario_id` | string | 是 | 与初始化数据一致的场景编号 |
| `step` | integer | 是 | 当前仿真步，从 0 开始 |
| `time_step` | number | 是 | 单步对应秒数 |
| `time_s` | number | 是 | 当前仿真时间，等于 `step * time_step` |
| `people` | array | 是 | 当前步全部行人状态，包括已撤离行人 |
| `exits` | array | 是 | 当前步各出口状态 |
| `fields` | object | 是 | 当前步的烟雾、风险和拥堵场 |
| `relations` | array | 是 | 当前步需要展示或记录的社会关系边 |
| `events` | array | 是 | 当前步发生的离散事件 |
| `strategy_state` | object | 是 | 当前步的策略、引导和控制状态 |

## 6. `people` 字段

`people` 中每个对象至少包含以下字段：

| 字段 | 类型 | 是否可为 `null` | 含义 | 主要来源 |
|---|---|---:|---|---|
| `person_id` | integer | 否 | 全局唯一正整数行人编号 | B/统一编号 |
| `x` | integer | 否 | 当前横坐标 | B |
| `y` | integer | 否 | 当前纵坐标 | B |
| `heading` | string | 否 | 当前朝向，使用第 7 节枚举 | B |
| `target_exit` | string | 是 | 当前目标出口编号 | B |
| `evacuated` | boolean | 否 | 是否已经完成撤离 | B |
| `risk` | number | 是 | 当前个体风险值 | B |
| `dose` | number | 是 | 当前累计烟雾暴露剂量 | B |
| `info_state` | string | 是 | 当前信息状态，使用本节规定的枚举 | C |
| `info_source` | string 或 integer | 是 | 广播等来源使用字符串，人员来源使用正整数 `person_id` | C |
| `info_source_history` | array | 否 | 信息来源变化历史；无历史时使用空数组 | C |
| `receive_time` | integer | 是 | 首次接收有效信息的仿真步数 | C |
| `follow_target` | integer | 是 | 当前跟随的正整数 `person_id`；无跟随对象时为 `null` | C |

`info_state` 只允许使用以下枚举：

- `UNKNOWN`
- `ALERTED`
- `CONFIRMED`
- `MISINFORMED`
- `GUIDED`

`info_source_history` 建议采用对象数组。每条记录至少包含 `info_source` 和以仿真步表示的 `receive_time`：

```json
[
  {
    "info_source": "broadcast",
    "receive_time": 5
  },
  {
    "info_source": 2,
    "receive_time": 14
  }
]
```

## 7. `heading` 枚举

`heading` 只允许使用以下小写英文枚举：

- `up`
- `down`
- `left`
- `right`
- `up-left`
- `up-right`
- `down-left`
- `down-right`

`heading` 由 B 模块直接输出，D 不根据坐标变化进行二次推算，也不覆盖 B 的结果。

## 8. 已撤离行人规则

1. 行人完成撤离后，`evacuated` 必须为 `true`。
2. 已撤离行人的 `x`、`y` 保持为撤离完成时的坐标，不移出网格，不改写为特殊坐标。
3. 已撤离行人继续出现在每一步的 `people` 中，直到仿真结束。
4. 其他不再变化的字段保持最后有效值；没有有效值的字段使用 `null`。
5. D 只依据 `evacuated` 展示撤离状态，不自行删除行人或修改上游状态。

## 9. `exits` 每步状态

每个出口对象至少包含：

| 字段 | 类型 | 含义 | 主要来源 |
|---|---|---|---|
| `exit_id` | string | 与初始化数据一致的出口编号 | A/B |
| `queue_length` | integer | 当前排队人数，最小值为 0 | B |

示例：

```json
[
  {"exit_id": "exit_01", "queue_length": 2},
  {"exit_id": "exit_02", "queue_length": 0}
]
```

## 10. `fields`

`fields` 至少包含：

| 字段 | 类型 | 含义 | 主要来源 |
|---|---|---|---|
| `smoke_field` | array | 二维浮点数组；当前统一为无量纲，数值范围为 0～1 | B |
| `risk_field` | array | 风险场二维浮点数组；由 B 计算并输出，单位、阈值和最终范围待确认 | B |
| `congestion_field` | array | 元胞邻域行人密度二维浮点数组；数值范围为 0～1，由 B 计算并输出 | B |

当前团队文档中同时出现了“`[x][y]`”和“`y` 行 `x` 列”的描述，二维数组索引顺序存在表述歧义。本草案不自行决定第一维和第二维分别对应 `x` 还是 `y`，也不据此规定转置规则。

在团队确认前：

- 示例 JSON 只展示二维数组结构，不宣称最终索引顺序。
- A、B、C、D 不应仅凭数组位置推断坐标语义。
- 一旦团队确认索引顺序，应将结论写入冻结版 schema，并同步更新所有生产者、适配器和消费者。

## 11. `relations`

每条社会关系至少包含：

| 字段 | 类型 | 含义 | 主要来源 |
|---|---|---|---|
| `person_a_id` | integer | 关系一端的全局唯一正整数行人编号 | C |
| `person_b_id` | integer | 关系另一端的全局唯一正整数行人编号 | C |
| `relation_type` | string | 关系类型枚举 | C |
| `strength` | number | 关系强度 | C |
| `trust` | number | 信任度 | C |

关系是否有向、是否每步完整输出，以及 `strength`、`trust` 的最终范围，仍需团队确认。D 不根据可视化效果修改关系值。

## 12. `events`

事件类型至少支持：

- `exit_switch`
- `evac_success`
- `conflict`

每条事件至少包含：

| 字段 | 类型 | 是否可为 `null` | 含义 |
|---|---|---:|---|
| `type` | string | 否 | 事件类型枚举 |
| `step` | integer | 否 | 事件发生的仿真步 |
| `person_id` | integer | 是 | 相关行人的全局唯一正整数编号 |
| `x` | integer | 是 | 事件发生位置横坐标 |
| `y` | integer | 是 | 事件发生位置纵坐标 |
| `details` | object | 否 | 事件特有的补充信息；无补充信息时使用空对象 |

`exit_switch` 的 `details` 可使用如下草案结构：

```json
{
  "from_exit": "exit_01",
  "to_exit": "exit_02",
  "reason": "congestion"
}
```

事件的产生、判定和原因由上游模型负责；D 只适配、记录和展示。

## 13. `strategy_state`

`strategy_state` 用于承载 C 模块或运行控制层提供的当前策略状态。第一阶段可使用以下最小草案：

```json
{
  "strategy_name": "baseline",
  "control_command": null
}
```

`control_command` 第一阶段是否实现尚未确认。在团队确认前，D 不主动生成控制命令，也不通过展示层反向修改上游模型状态。

## 14. 数据来源与模块边界

### 14.1 A 模块提供

- `scenario_id`；
- `grid_width`、`grid_height`、`cell_size`；
- `cell_type`；
- 墙体、障碍物等初始化地图数据；
- 出口编号和出口初始空间信息；
- 烟雾源的初始化位置或场景定义；
- YAML 配置文件的基础结构。

### 14.2 B 模块提供

- 行人位置 `x`、`y`；
- 行人朝向 `heading`；
- `target_exit` 和 `evacuated`；
- 个体 `risk` 和 `dose`；
- 出口 `queue_length`；
- `smoke_field`、`risk_field`、`congestion_field`；
- `exit_switch`、`evac_success`、`conflict` 等运动、撤离和冲突事件；
- 烟雾、风险、拥堵相关的模型输出。

### 14.3 C 模块提供

- `relations` 中的关系端点、类型、强度和信任度；
- `info_state`、`info_source`、`info_source_history`；
- `receive_time`、`follow_target`；
- 引导、指示牌、出口控制、区域封锁等 `strategy_state`；
- 与社会关系和信息传播有关的事件。

### 14.4 D 模块负责

- 将 A、B、C 的已确认输出适配为统一读取格式；
- 检查必填字段、数据类型和版本；
- 展示地图、行人、烟雾、风险、拥堵、关系和事件；
- 记录快照、日志和实验结果；
- 按团队冻结的口径计算统计指标。

D 不修改 A、B、C 的模型逻辑，不二次推算 `heading`，不自行改变关系、风险、策略或事件结果，也不因展示需要向上游数据临时增加字段。

## 15. 待团队确认项

1. `run_id` 的编号规则、生成方和跨文件唯一性范围。
2. 快照采用单步独立 JSON，还是在一个文件中进行时序存储。
3. `smoke_field`、`risk_field`、`congestion_field` 和 `cell_type` 的二维数组索引顺序。
4. 是否保存全部 `simulation_snapshot`，或按固定间隔、关键帧、增量方式保存。
5. 最终报告导出格式，例如 CSV、JSON、PDF、图像、视频或结果压缩包的组合。
6. `control_command` 第一阶段是否实现，以及其生产者、消费者和权限边界。
7. 指标的最终计算口径、阈值、单位、异常值与未完成疏散时的处理规则。
8. 网格坐标原点、`x`/`y` 方向及其与展示坐标的映射方式。
9. `risk_field` 的最终数值范围、单位和阈值来源。
10. 社会关系是否有向、是否每步变化，以及快照中采用全量还是增量输出。
11. `strategy_state` 的最终字段集合及其合并输出责任方。

## 16. 示例文件

配套的少量模拟数据位于：

```text
docs/d_week2/examples/d_snapshot_example.json
```

该文件只用于展示结构和 JSON 可解析性，不接入真实模型，不代表二维数组索引顺序或所有字段已经冻结。
