# D 方向第三周进度

## 1. 本周完成内容

1. 新增 D 端 CA 快照适配层，在不修改 B 代码的前提下读取当前人员、地图、烟雾和撤离状态；
2. 使用 B 当前真实 `step()` 完成 Matplotlib 逐步动画；
3. 实现开始、暂停、单步、重置和播放速度控制；
4. 实现 `people_log.csv`，记录每名行人的每一步状态；
5. 实现 `event_log.csv`，记录且去重 `evac_success` 事件；
6. 实现无窗口运行方式和每次重置独立保存日志；
7. 增加适配器、日志、运行器和可视化自动测试；
8. 新增 D-only 场景输入适配器，接入 A 的 JSON/CSV Grid、C 的 YAML SceneConfig 参数和 C 的人员关系输出；
9. 明确 A、B、C 后续接入点和当前待确认事项；
10. 新增 C 人群输出的 D 侧只读适配：读取 `output_people.json`，将 C 的 0-based 编号映射为 D 的 1-based 编号，并保留原始编号用于追溯。

## 2. 验证结果

- B 当前 3 人 mock 场景可从第 0 步运行到全员撤离；
- 实测第 10 步全员完成撤离；
- `people_log.csv` 共记录 11 个时间点，每步 3 人，共 33 行；
- `event_log.csv` 共记录 3 条 `evac_success`，每名行人一次；
- 满足 `time_s = step × time_step_s`；
- 烟雾场尺寸与地图一致；
- 重置后重新建立仿真实例和独立日志目录；
- 原 D 可视化和实验测试共 19 项，全部通过。
- 新增场景输入适配后，完整测试共 23 项，全部通过；
- 两张 A JSON 示例均成功绘制为 D 地图预览；
- C `config_template.yaml` 成功读取为 40 人、0.9/0.1 角色比例、关系强度 0.6 的 `SceneConfig`；
- C 已提供 `output_people.json`，D 通过显式编号映射接入 40 名人员和 160 条关系；该数据仍属于人群输入适配，不代表已经驱动 B 的完整运行场景。
- C 的 `output_people.json` 已通过 JSON 解析、人员数量、关系数量和关系端点校验；D 侧编号映射结果为 1～40。
- Python 编译检查通过；无窗口运行检查通过。

## 3. 成果位置

- `visualization/ca_snapshot_adapter.py`
- `visualization/visualizer.py`
- `experiments/csv_logger.py`
- `experiments/runner.py`
- `visualization/tests/`
- `experiments/tests/`
- `docs/d_week3/README.md`
- `docs/d_week3/c_population_mapping.md`
- `docs/d_week3/examples/c_output_people.json`
- `visualization/scene_input_adapter.py`
- `visualization/tests/test_scene_input_adapter.py`
- `docs/d_week3/assets/real_step_animation.png`

## 4. 当前最大问题

1. B 已确认正式仿真入口为 `simulation.ca_model.CaEvacSimulation`，`step` 从 0 开始，且 `time_s = step × 0.5`；
2. B 暂未提供公开的运行后快照、`people_log.csv`、`heading`、`risk`、`dose`、`conflict` 和 `exit_switch`；D 对这些字段统一保留为空，不编造演示值；
3. A 的 JSON/CSV 已能进入 D 地图预览，但地图烟源和出口元数据尚未组装为 B 的 `ScenarioConfig`；
4. C 的 YAML 配置和 `output_people.json` 均可读取；
5. C 的人员编号已经在 D 适配层兼容，当前仍需团队确认最终统一编号口径；
6. A 的 CSV 元胞顺序与 B 的行优先读取假设需要统一；
7. 坐标方向、二维数组索引和部分指标口径仍需团队冻结。

## 5. 下一步计划

1. 收到 A 的 CSV 顺序、烟源和出口元数据确认后，将地图预览接入可运行场景；
2. 将 C 的 YAML 参数和人员关系输出接入统一场景工厂；
3. 等待 B 提供运行后快照和 `people_log.csv`，与 D 的日志字段进行正式对照；
4. 接入后续公开的 `heading`、`risk`、`dose`、拥堵和事件字段；
5. 使用更大场景检查动画性能、日志规模和异常处理；
6. 开始计算第一批真实疏散指标。

## 6. 对其他成员接口的影响

D 没有修改 A、B、C 的代码或模型逻辑；本次新增的 `c_output_people.json` 是 C 输出的 D 侧示例副本，不是对 C 文件的改写。

后续联调需要：

- A 确认 CSV 行优先规则、上传函数命名及地图元数据传递方式；
- B 后续提供运行后快照、`people_log.csv` 与未实现字段的正式输出；
- C 提供 YAML 配置到人员、关系及信息状态的稳定输出；
- 团队共同确认坐标、场数组和事件格式。

## 7. 当前说明

本周动画和日志由 B 当前 CA mock 的实际运行状态驱动，不再是第二周网页中的前端演示运动。

但 A 地图、C 人群关系和 B 正式快照仍未完整联调，因此当前成果是 D 第三周的可运行最小闭环，不代表整个仿真平台已经完成。
