# D 第四周工作进度

## 本周完成

1. 将 D 的运行器和网页入口适配到 B 当前公开的 `EvacEngine`：初始化后调用
   `run_one_step(c_step_data)`，读取 `current_step`、`grid`、`person_map` 与
   `smoke_matrix`。
2. 新增 D 侧运行时适配层。它只在当前内存对象补齐 B 已读取但共享对象未创建的
   中性字段，并记录在适配元数据中；未修改 A/B/C 源码或 B 的移动规则。
3. 更新快照与 CSV 记录，使烟雾浓度按 B 当前的非负原始值记录，不擅自假定为 0–1。
4. 新增 `people_log.csv` 只读适配器，支持 B 给出的 11 列格式；空字段、人员重叠和
   坐标会原样保留，不用演示数据补齐。
5. 更新统一网页：可加载 A 地图、C 人员数据与 B 当前运行状态；页面明确显示
   “真实运行”或“演示数据”，不把演示模式说成完整联调。
6. 更新 D 测试和接口说明，并保留第二、三、四周网页版本归档。

## 验证结果

在当前最新仓库上运行：

```text
PYTHONIOENCODING=utf-8 python -B -m unittest discover -q
Ran 34 tests ... OK
```

网页可完成真实加载、单步、导出、关闭会话的自动验证。运行时看到的中文字体缺失警告来自 B 的 Matplotlib 窗口，不影响 D 的快照、CSV 和结果包验证。

### 本次最新仓库复核

使用仓库当前真实输入完成了无界面联调：

```text
A 地图：maps/edited_map.json
C/A 人群位置：control/output_people_position.json（40 人）
B 运行入口：simulation.evac_simulation.EvacEngine
D 入口：experiments.integrated_runner
运行结果：成功运行至第 50 步，并生成 D 的 people_log.csv 与 event_log.csv。
```

新增 `visualization.runtime_entry.DVisualizationEntry`，作为统一主程序的 D 可视化接入点：A 在创建 B 的 `EvacEngine` 后于第 0 步调用 `start()`，每次 B 完成一步后调用 `capture()`，即可得到网页/图表可用快照并写入真实日志；D 不改变 A、B、C 的运行逻辑。

## 当前接口结论

- 直接接入：A 的地图、C 的人员/关系输入、B 当前 `EvacEngine` 的公开状态；
- 需要 D 适配：B 所需的运行时字段及 `people_log.csv` 的只读解析；
- 仍需团队确认：C 的逐步行为字典字段、烟雾浓度单位、出口编号，以及冲突和出口切换
  的事件语义。

当前没有 C 的逐步行为输出时，D 只向 B 传空字典，不生成策略数据，也不把这称为
完整 A+B+C 行为联调。

## 已观察到的问题

B 提供的 `people_log.csv` 在示例步中存在多人同坐标、整体同步往返的情况。D 的网页
按实际坐标显示，所以画面会出现重叠或同步跳动；这不是 D 把坐标画错。D 不会通过
自行打散人员来掩盖该模型/初始位置问题。

## 后续待 B/C 确认

1. B：初始化时为每位人员提供有效且可区分的位置，并确认是否需要同格冲突/占用处理；
2. B：统一 `person.evacuated`、`person.dose`、`grid.get_cell()` 的正式归属；
3. C：提供正式的每步行为参数及其字段说明。
