# D 第三周接口对齐决议（草案）

本文档只记录 D 侧适配采用的工程默认值，不修改 A、B、C 的模型逻辑。若团队后续冻结的 schema 与本文不同，应以团队冻结版本为准，并同步更新 D 适配器和测试。

## 已采用的默认规则

- 坐标原点为左上角 `(0, 0)`；
- `x` 向右增加，`y` 向下增加；
- 二维场统一按 `field[y][x]` 解释；
- 网格使用稠密行优先顺序：`cells[y * width + x]`；
- `cell_size` 默认 `0.5 m`；
- 元胞类型使用 `free`、`wall`、`obstacle`、`exit`、`smoke_source`、`sign`、`guide_zone`；
- A 提供静态出口和烟雾源信息，B 提供动态出口队列和烟雾场；
- C 的人员编号在正式输出中使用 `1...N` 的全局唯一正整数；
- 关系按有向边记录。对称关系输出两个方向，具有方向性的关系保留方向差异；
- D 不重新编号人员，不补造人员、关系、风险或策略数据。

## D 侧兼容行为

如果 A 的旧版地图只提供 `cells`：

- D 可从 `cell_type=exit` 的连通区域派生静态 `exit_id` 和出口元胞列表；
- D 可从 `cell_type=smoke_source` 派生静态烟雾源编号；
- D 不会虚构动态 `queue_length`、烟雾浓度或风险值；
- D 同时保留旧的嵌套 `grid` 预览结构，并补充统一表要求的顶层 `grid_width`、`grid_height`、`cell_size` 和 `cell_type`。

如果 C 的原型输出使用 `id`、`from`、`to`，D 可以做字段名兼容转换；但 ID 必须已经是正整数，D 不会把 `0...N-1` 私自改成 `1...N`。

## 仍需团队冻结的内容

- `relation_intensity` 对关系数量还是关系强度的具体含义；
- `risk_field` 的单位、阈值和数值范围；
- `info_source_history` 的最终结构；
- `strategy_state` 的生产者；
- A 是否在正式 JSON 中提供显式 `exits` 和 `smoke_sources`；
- 二维场索引顺序和坐标原点是否与本草案一致。
