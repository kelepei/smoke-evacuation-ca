# D 第一周：实验日志与结果文件格式设计

## 1. 文档目的

本文件用于统一仿真平台的数据记录格式，使地图、基础疏散、烟雾风险、社会关系、信息传播、疏散策略、可视化和实验分析模块能够交换数据。

日志设计应满足：

- 仿真过程可回放；
- 指标结果可追溯；
- 不同实验可比较；
- 其他成员可复现；
- 后续界面可直接读取；
- 批量实验可统一管理。

当前为第一版接口草案，正式开发时由团队共同确认字段名称和数据类型。

---

## 2. 单次实验目录结构

建议每次实验使用独立目录：

```text
outputs/
└── experiments/
    └── classroom_smoke_guidance_001/
        ├── config.yaml
        ├── metadata.json
        ├── people_log.csv
        ├── event_log.csv
        ├── field_log.csv
        ├── social_edges.csv
        ├── metrics.csv
        ├── evacuation_curve.png
        ├── congestion_heatmap.png
        ├── smoke_heatmap.png
        ├── information_timeline.png
        └── video.mp4
```

其中：

- `config.yaml`：保存实验输入配置；
- `metadata.json`：保存版本、运行时间和运行环境；
- `people_log.csv`：保存逐人逐步状态；
- `event_log.csv`：保存关键事件；
- `field_log.csv`：保存全场网格场数据；
- `social_edges.csv`：保存社会关系网络；
- `metrics.csv`：保存实验汇总指标；
- PNG 和 MP4 文件：保存图表和演示结果。

第一版暂时不要求生成全部图片和视频，但目录命名和数据文件应尽量保持统一。

---

## 3. config.yaml

### 3.1 功能

`config.yaml` 保存本次实验的全部输入条件，用于复现实验。

### 3.2 建议字段

| 字段                   | 含义             | 示例                    |
| ---------------------- | ---------------- | ----------------------- |
| `experiment_id`        | 实验编号         | `exp_001`               |
| `scenario_name`        | 场景名称         | `classroom_double_exit` |
| `map_file`             | 地图文件路径     | `maps/classroom_01.csv` |
| `population`           | 初始人数         | `100`                   |
| `random_seed`          | 随机种子         | `42`                    |
| `time_step`            | 单步时间         | `0.2`                   |
| `max_steps`            | 最大仿真步数     | `3000`                  |
| `cell_size`            | 元胞物理边长     | `0.4`                   |
| `smoke_enabled`        | 是否开启烟雾     | `true`                  |
| `social_enabled`       | 是否开启社会关系 | `true`                  |
| `information_enabled`  | 是否开启信息传播 | `true`                  |
| `guidance_enabled`     | 是否开启引导策略 | `false`                 |
| `exit_states`          | 各出口初始状态   | `exit_A: open`          |
| `risk_thresholds`      | 风险阈值         | `high: 0.7`             |
| `visibility_threshold` | 低能见度阈值     | `5.0`                   |
| `congestion_threshold` | 拥堵密度阈值     | `4.0`                   |
| `model_version`        | 模型版本         | `v0.1`                  |
| `schema_version`       | 日志格式版本     | `v0.1`                  |

烟雾、社会关系、信息传播和引导策略的详细参数，可在对应的子节点中继续补充。

---

## 4. metadata.json

### 4.1 功能

`metadata.json` 保存本次实验的运行环境和文件信息。

### 4.2 建议字段

| 字段             | 含义                      |
| ---------------- | ------------------------- |
| `experiment_id`  | 实验编号                  |
| `created_at`     | 实验开始时间              |
| `finished_at`    | 实验结束时间              |
| `git_commit`     | 本次实验对应的 Git commit |
| `branch_name`    | 分支名称                  |
| `model_version`  | 模型版本                  |
| `schema_version` | 日志格式版本              |
| `python_version` | Python 版本               |
| `platform`       | 操作系统                  |
| `status`         | 实验是否正常完成          |
| `notes`          | 补充说明                  |

---

## 5. people_log.csv

### 5.1 功能

`people_log.csv` 用于记录每名行人在每个时间步的位置、行为、烟雾暴露、风险状态和信息状态。

一名行人在一个时间步对应一行。

### 5.2 字段设计

| 字段名                | 类型   | 必填 | 含义                   | 示例            | 提供模块 |
| --------------------- | ------ | ---: | ---------------------- | --------------- | -------- |
| `experiment_id`       | string |   是 | 实验编号               | `exp_001`       | 实验控制 |
| `random_seed`         | int    |   是 | 随机种子               | `42`            | 实验控制 |
| `time_step`           | int    |   是 | 当前仿真步             | `18`            | 核心仿真 |
| `time`                | float  |   是 | 当前仿真时间           | `3.6`           | 核心仿真 |
| `person_id`           | int    |   是 | 行人唯一编号           | `15`            | B        |
| `x`                   | int    |   是 | 当前元胞横坐标         | `26`            | B        |
| `y`                   | int    |   是 | 当前元胞纵坐标         | `41`            | B        |
| `previous_x`          | int    |   否 | 上一时间步横坐标       | `25`            | B        |
| `previous_y`          | int    |   否 | 上一时间步纵坐标       | `41`            | B        |
| `direction`           | string |   否 | 当前移动方向           | `EAST`          | B 或 D   |
| `status`              | string |   是 | 当前行人状态           | `EVACUATING`    | B        |
| `target_exit`         | string |   否 | 当前计划前往的出口     | `exit_A`        | B        |
| `actual_exit`         | string |   否 | 最终实际离开的出口     | `exit_B`        | B        |
| `evacuated`           | bool   |   是 | 是否完成疏散           | `false`         | B        |
| `evacuation_time`     | float  |   否 | 完成疏散的时间         | `35.8`          | B        |
| `is_waiting`          | bool   |   是 | 是否处于等待状态       | `true`          | B        |
| `wait_reason`         | string |   否 | 等待原因               | `CELL_CONFLICT` | B/C      |
| `queue_state`         | string |   否 | 当前排队状态           | `QUEUING`       | B        |
| `smoke_concentration` | float  |   否 | 所在元胞烟雾浓度       | `0.36`          | B        |
| `visibility`          | float  |   否 | 所在位置能见度         | `6.4`           | B        |
| `risk_value`          | float  |   否 | 当前个体风险值         | `0.52`          | B        |
| `smoke_dose`          | float  |   否 | 累计烟雾暴露剂量       | `1.84`          | B        |
| `information_state`   | string |   否 | 当前信息状态           | `ALERTED`       | C        |
| `information_source`  | string |   否 | 当前信息来源           | `BROADCAST`     | C        |
| `first_alert_time`    | float  |   否 | 首次收到有效信息时间   | `2.8`           | C        |
| `group_id`            | string |   否 | 社会关系组编号         | `group_03`      | C        |
| `misinformed`         | bool   |   否 | 是否正在受错误信息影响 | `false`         | C        |
| `decision_reason`     | string |   否 | 当前决策主要原因       | `HERDING`       | B/C      |

### 5.3 状态枚举建议

`status`：

- `EVACUATING`
- `WAITING`
- `QUEUING`
- `EVACUATED`
- `TRAPPED`

`direction`：

- `NORTH`
- `SOUTH`
- `EAST`
- `WEST`
- `NORTHEAST`
- `NORTHWEST`
- `SOUTHEAST`
- `SOUTHWEST`
- `STAY`

`information_state`：

- `UNKNOWN`
- `ALERTED`
- `CONFIRMED`
- `MISINFORMED`
- `CORRECTED`
- `GUIDED`

`information_source`：

- `NONE`
- `BROADCAST`
- `LOCAL_PERSON`
- `FRIEND`
- `FAMILY`
- `GUIDE`
- `STATIC_SIGN`
- `DYNAMIC_SIGN`
- `FALSE_MESSAGE`

`decision_reason`：

- `SHORTEST_PATH`
- `AVOID_SMOKE`
- `AVOID_CONGESTION`
- `FOLLOW_GROUP`
- `HERDING`
- `GUIDANCE`
- `FALSE_INFORMATION`
- `EXIT_CLOSED`

### 5.4 示例

```csv
experiment_id,random_seed,time_step,time,person_id,x,y,previous_x,previous_y,direction,status,target_exit,actual_exit,evacuated,evacuation_time,is_waiting,wait_reason,queue_state,smoke_concentration,visibility,risk_value,smoke_dose,information_state,information_source,first_alert_time,group_id,misinformed,decision_reason
exp_001,42,18,3.6,15,26,41,25,41,EAST,EVACUATING,exit_A,,false,,false,,NONE,0.36,6.4,0.52,1.84,ALERTED,BROADCAST,2.8,group_03,false,SHORTEST_PATH
```

---

## 6. event_log.csv

### 6.1 功能

`event_log.csv` 用于记录仿真过程中发生的关键离散事件。

仅在事件发生时记录，不要求每个时间步都写入。

### 6.2 字段设计

| 字段名          | 类型   | 必填 | 含义             | 示例            |
| --------------- | ------ | ---: | ---------------- | --------------- |
| `experiment_id` | string |   是 | 实验编号         | `exp_001`       |
| `random_seed`   | int    |   是 | 随机种子         | `42`            |
| `time_step`     | int    |   是 | 事件发生的仿真步 | `18`            |
| `time`          | float  |   是 | 事件发生时间     | `3.6`           |
| `event_type`    | string |   是 | 事件类型         | `EXIT_CHANGED`  |
| `person_id`     | int    |   否 | 相关行人编号     | `15`            |
| `x`             | int    |   否 | 事件位置横坐标   | `26`            |
| `y`             | int    |   否 | 事件位置纵坐标   | `41`            |
| `source`        | string |   否 | 事件来源或原状态 | `exit_A`        |
| `target`        | string |   否 | 事件目标或新状态 | `exit_B`        |
| `details`       | string |   否 | 补充说明         | `exit_A closed` |

### 6.3 事件类型建议

仿真事件：

- `SIMULATION_START`
- `SIMULATION_PAUSE`
- `SIMULATION_RESUME`
- `SIMULATION_END`

烟雾和风险事件：

- `SMOKE_SOURCE_STARTED`
- `HIGH_RISK_EXPOSURE`
- `LOW_VISIBILITY_EXPOSURE`

信息传播事件：

- `ALERT_RECEIVED`
- `FALSE_INFORMATION_RECEIVED`
- `INFORMATION_CORRECTED`
- `GUIDANCE_RECEIVED`

疏散行为事件：

- `EXIT_SELECTED`
- `EXIT_CHANGED`
- `QUEUE_JOINED`
- `QUEUE_LEFT`
- `CELL_CONFLICT`
- `PERSON_EVACUATED`
- `PERSON_TRAPPED`

策略事件：

- `EXIT_CLOSED`
- `EXIT_REOPENED`
- `AREA_BLOCKED`
- `AREA_REOPENED`
- `GUIDE_ACTIVATED`
- `DYNAMIC_SIGN_UPDATED`

### 6.4 示例

```csv
experiment_id,random_seed,time_step,time,event_type,person_id,x,y,source,target,details
exp_001,42,18,3.6,ALERT_RECEIVED,15,26,41,BROADCAST,,global_warning
exp_001,42,35,7.0,EXIT_CHANGED,15,34,42,exit_A,exit_B,exit_A_closed
exp_001,42,82,16.4,PERSON_EVACUATED,15,50,20,,exit_B,
```

---

## 7. field_log.csv

### 7.1 功能

`field_log.csv` 用于记录每个时间步的全场网格数据，支持烟雾热力图、风险热力图、能见度图和拥堵热力图回放。

如果只在 `people_log.csv` 中记录行人所在位置的烟雾浓度，将无法恢复没有行人的区域，因此需要单独记录全场数据。

### 7.2 字段设计

| 字段名                | 类型   | 必填 | 含义           | 示例      | 提供模块 |
| --------------------- | ------ | ---: | -------------- | --------- | -------- |
| `experiment_id`       | string |   是 | 实验编号       | `exp_001` | 实验控制 |
| `time_step`           | int    |   是 | 当前仿真步     | `18`      | 核心仿真 |
| `time`                | float  |   是 | 当前仿真时间   | `3.6`     | 核心仿真 |
| `x`                   | int    |   是 | 元胞横坐标     | `26`      | A/B      |
| `y`                   | int    |   是 | 元胞纵坐标     | `41`      | A/B      |
| `cell_type`           | string |   是 | 元胞类型       | `FLOOR`   | A        |
| `occupied`            | bool   |   否 | 是否被行人占用 | `true`    | B        |
| `occupancy_count`     | int    |   否 | 当前元胞行人数 | `1`       | B/D      |
| `local_density`       | float  |   否 | 当前局部密度   | `2.5`     | B/D      |
| `smoke_concentration` | float  |   否 | 元胞烟雾浓度   | `0.36`    | B        |
| `visibility`          | float  |   否 | 元胞能见度     | `6.4`     | B        |
| `risk_field`          | float  |   否 | 环境风险场数值 | `0.52`    | B        |
| `exit_state`          | string |   否 | 出口状态       | `OPEN`    | A/C      |

`cell_type` 建议使用：

- `FLOOR`
- `WALL`
- `OBSTACLE`
- `EXIT`
- `SMOKE_SOURCE`
- `BLOCKED`

### 7.3 数据量说明

逐元胞逐时间步的 CSV 数据量可能较大。

第一版可采用以下方法降低数据量：

- 每隔若干时间步记录一次；
- 只记录可通行区域；
- 只记录发生变化的元胞；
- 后续改用 NumPy、Parquet 或压缩文件。

但在格式尚未稳定前，优先使用便于检查的 CSV。

---

## 8. social_edges.csv

### 8.1 功能

`social_edges.csv` 用于保存行人之间的社会关系边，以支持：

- 社会关系网络展示；
- 朋友和家庭关系分析；
- 信息传播路径分析；
- 同伴分离率计算。

仅使用 `group_id` 无法准确表达“谁与谁相连”，因此需要保存关系边。

### 8.2 字段设计

| 字段名               | 类型   | 必填 | 含义           | 示例      |
| -------------------- | ------ | ---: | -------------- | --------- |
| `experiment_id`      | string |   是 | 实验编号       | `exp_001` |
| `source_person_id`   | int    |   是 | 起点行人编号   | `15`      |
| `target_person_id`   | int    |   是 | 终点行人编号   | `28`      |
| `relation_type`      | string |   是 | 关系类型       | `FRIEND`  |
| `trust_weight`       | float  |   否 | 信任权重       | `0.70`    |
| `information_weight` | float  |   否 | 信息传播权重   | `0.65`    |
| `bidirectional`      | bool   |   是 | 是否为双向关系 | `true`    |

`relation_type` 建议使用：

- `FAMILY`
- `FRIEND`
- `CLASSMATE`
- `COLLEAGUE`
- `STRANGER`
- `GUIDE_RELATION`

### 8.3 示例

```csv
experiment_id,source_person_id,target_person_id,relation_type,trust_weight,information_weight,bidirectional
exp_001,15,28,FRIEND,0.70,0.65,true
exp_001,15,31,FAMILY,0.90,0.85,true
```

---

## 9. metrics.csv

### 9.1 功能

`metrics.csv` 用于保存一组实验结束后的汇总指标。

每项指标对应一行，便于不同实验合并和统计。

### 9.2 字段设计

| 字段名                | 类型   | 必填 | 含义           | 示例                    |
| --------------------- | ------ | ---: | -------------- | ----------------------- |
| `experiment_id`       | string |   是 | 实验编号       | `exp_001`               |
| `random_seed`         | int    |   是 | 随机种子       | `42`                    |
| `scenario_name`       | string |   是 | 场景名称       | `classroom_double_exit` |
| `strategy_name`       | string |   是 | 策略名称       | `baseline`              |
| `metric_name`         | string |   是 | 指标变量名     | `evacuation_rate`       |
| `value`               | float  |   否 | 指标值         | `0.92`                  |
| `unit`                | string |   是 | 指标单位       | `ratio`                 |
| `threshold_name`      | string |   否 | 使用的阈值名称 | `high_risk_threshold`   |
| `threshold_value`     | float  |   否 | 使用的阈值数值 | `0.70`                  |
| `calculation_version` | string |   是 | 指标计算版本   | `v0.1`                  |

当某个指标无法计算，例如全员未完成疏散时的总疏散时间，`value` 可留空。

### 9.3 示例

```csv
experiment_id,random_seed,scenario_name,strategy_name,metric_name,value,unit,threshold_name,threshold_value,calculation_version
exp_001,42,classroom_double_exit,baseline,total_evacuation_time,,s,,,v0.1
exp_001,42,classroom_double_exit,baseline,evacuation_rate,0.92,ratio,,,v0.1
exp_001,42,classroom_double_exit,baseline,remaining_count,8,person,,,v0.1
exp_001,42,classroom_double_exit,baseline,risk_exposed_count,16,person,high_risk_threshold,0.70,v0.1
```

---

## 10. 字段一致性规则

1. 所有文件必须使用相同的 `experiment_id`；
2. 随机实验必须记录 `random_seed`；
3. 时间统一使用秒；
4. 元胞位置统一使用整数坐标；
5. 比例统一保存为 `[0, 1]`；
6. 布尔值统一使用 `true` 和 `false`；
7. 枚举值统一使用大写英文；
8. 不适用或尚未产生的字段允许留空；
9. `status` 是行人状态的权威字段；
10. `evacuated` 是便于筛选的冗余字段，必须与 `status` 一致；
11. `actual_exit` 只在成功完成疏散后填写；
12. `direction` 默认可由当前坐标和上一时刻坐标计算；
13. 如果模型直接输出 `direction`，必须与坐标变化一致；
14. 目标出口 `target_exit` 不等于实际出口 `actual_exit`；
15. 日志格式修改时必须更新 `schema_version`；
16. 指标算法修改时必须更新 `calculation_version`；
17. 可视化模块不得自行猜测缺失的关键状态；
18. 汇总指标必须能够从原始日志重新计算。

---

## 11. 模块责任划分

### A：地图与系统架构

负责提供：

- 地图文件；
- 网格尺寸；
- 元胞类型；
- 墙体和障碍物；
- 出口位置；
- 场景语义；
- 配置文件基础结构。

### B：基础疏散、烟雾与风险

负责提供：

- 行人位置和移动状态；
- 出口选择；
- 排队和冲突状态；
- 疏散完成状态；
- 烟雾浓度；
- 能见度；
- 风险值；
- 烟雾暴露剂量；
- 全场网格场数据。

### C：社会关系、信息传播与策略

负责提供：

- 社会关系边；
- 关系类型和权重；
- 信息状态；
- 信息来源；
- 错误信息；
- 从众行为；
- 引导员；
- 指示牌；
- 出口关闭和区域封锁事件。

### D：可视化、数据分析与实验

负责：

- 统一读取日志；
- 检查字段完整性；
- 保存实验结果；
- 绘制动画和热力图；
- 计算汇总指标；
- 管理批量实验；
- 输出图表、视频和实验结果包。

---

## 12. 第一版最小日志集合

第 3—4 周基础动画和日志阶段，至少需要：

- `config.yaml`
- `people_log.csv`
- `event_log.csv`
- `metrics.csv`

烟雾和社会关系模块接入后增加：

- `field_log.csv`
- `social_edges.csv`

---

## 13. 当前待团队确认的问题

1. 网格坐标采用 `(row, column)` 还是 `(x, y)`；
2. 坐标原点位置；
3. `x` 和 `y` 分别对应行还是列；
4. 烟雾浓度的物理单位；
5. 能见度的计算方式；
6. 风险值的取值范围；
7. 排队状态由 B 直接输出还是由 D 计算；
8. 局部密度由 B 输出还是 D 根据位置计算；
9. 社会关系是否允许一名行人属于多个关系组；
10. 信息传播关系是否为有向边；
11. 全场场数据的记录间隔；
12. 批量实验的目录命名规则。

---

## 14. 当前结论

第一版日志设计优先保证：

- 字段清晰；
- 文件可读取；
- 实验可复现；
- 指标可追溯；
- 模块之间能够对接。

后续模型功能增加时允许新增字段，但应避免频繁修改已确定字段名称。
