# C 人员关系输出到 D 的编号适配

## 当前 C 输出

C 当前提供的 `output_people.json` 使用：

- `persons[].id`，从 `0` 开始；
- `relations[].from` 和 `relations[].to`，同样从 `0` 开始；
- 关系包含 `relation_type`、`strength`、`trust`、`wait_probability` 和 `follow_probability`。

## D 侧处理

D 的统一日志和快照使用正整数 `person_id`，因此适配器默认执行：

```text
D person_id = C source id + 1
```

同时保留：

- `source_person_id`；
- `source_person_a_id`；
- `source_person_b_id`。

这样既符合 D 的正整数编号规范，又能追溯 C 的原始编号。D 不修改 C 的 JSON 文件，也不修改 C 的关系生成逻辑。

如果 C 后续改为从 `1` 开始，D 调用适配器时传入 `source_id_base=1` 即可。

## 已验证内容

C 提供的 40 人、160 条关系文件可按此规则转换；关系端点在转换后仍能正确指向已转换的人员。
