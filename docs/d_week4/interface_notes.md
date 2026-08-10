# D 第 4 周接口说明

## 已接入

- A JSON 地图：`map_import.map_loader_grid.load_grid`
- A CSV 地图：`map_import.csv_loader_grid.load_csv_grid`
- A PNG 地图：`map_import.map_loader_image.load_image` + `map_import.binary_to_grid.binary_to_grid`
- C YAML：`control/scene_config.py`、`control/config_template.yaml`
- C 人员/关系输出：`control/output_people_position.json`
- B 运行入口：`simulation.evac_simulation.EvacEngine`
- D 运行适配：`experiments.b_runtime_adapter.EvacEngineRuntimeAdapter`

## D 端兼容层

- `visualization/integrated_runtime.py`：一键演示和真实联调统一入口。
- `visualization/adapters/map_adapter.py`：选择 A loader，不重写 A 的解析逻辑。
- `visualization/adapters/config_adapter.py`：读取 C 风格 YAML；缺失时 fallback。
- `visualization/adapters/snapshot_adapter.py`：补齐 D 输出所需的 smoke/group_id 兼容字段。
- `experiments/csv_logger.py`：输出本周要求的 `smoke`、`group_id` 字段，同时保留既有字段。

## Fallback 边界

- 没有 `--population` 时，D 会生成演示人员，只用于给组长快速看动画和日志框架。
- 有 `--population` 时，D 保留 C/A 已给出的人员位置，不再自行分配。
- 没有上游烟源或烟雾场时，D fallback smoke heatmap 只用于可视化展示。
- C 逐步行为字典暂未接入，D 当前传空映射给 B。

