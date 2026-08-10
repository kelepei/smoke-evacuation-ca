# C 人群输出接入说明（D 适配层）

## 目的

本文件说明 D 如何读取 C 生成的 `output_people.json`。该文件是 C 的人群与关系输出示例，不修改 C 的生成逻辑。

## 当前输入格式

- 人员编号：C 当前使用 `0..N-1`。
- 关系字段：使用 `from` 和 `to`。
- 人员字段：包含 `speed`、`group_id`、`profile`、`risk_sensitivity`、`familiarity`、`herding_tendency`、`target_exit`、`info_state`、`evacuated`、`dose` 等。
- 本次示例：40 名人员、160 条关系。

## D 侧适配规则

调用 `visualization.scene_input_adapter.load_population_output()` 时，默认按 `source_id_base=0` 读取，并映射为 D 统一使用的正整数编号：

```text
C source_person_id 0  -> D person_id 1
C source_person_id 39 -> D person_id 40
```

关系端点也使用相同映射。适配后的记录同时保留 `source_person_id`、`source_person_a_id` 和 `source_person_b_id`，便于追溯原始 C 数据。

## 验证结果

- 输入 JSON 可正常解析；
- 40 名人员均被读取；
- 160 条关系均被读取；
- 关系端点均能映射到已加载人员；
- D 侧编号为 1..40；
- C 的 `output_people.json` 未被修改。

## 当前边界

该适配只完成 C 输出到 D 的读取、字段规范化和编号映射，暂不代表已经驱动 B 的正式疏散场景。后续若团队统一 C 改为 1-based 编号，应通过参数或版本规则调整，不应在多个模块重复改写编号。
