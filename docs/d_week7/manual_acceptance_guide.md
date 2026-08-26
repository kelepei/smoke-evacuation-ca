# D 端第 7 周手工验收指南

## 1. 启动统一前端

在 PowerShell 中进入仓库并启动本地服务：

```powershell
cd D:\projects\smoke-evacuation-ca
python -m experiments.web_runtime_server
```

浏览器打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

不要直接双击 HTML。页面必须经本地服务打开，才能调用真实 Python 接口。

## 2. 逐项手工导入

使用以下仓库文件：

- A 地图：`maps/edited_map.json`
- 人员文件：C 先生成 `out_people.json`（人员/关系），A 再按地图分配可通行坐标后导出 `control/output_people_position.json`
- C 配置：`control/config_template.yaml`

验收步骤：

1. 在“地图文件”选择 A 地图。页面应立即显示楼层结构，尺寸为 `135 x 116`，墙体和出口可见。
2. 在“人员文件”选择经 A 分配坐标后的 `control/output_people_position.json`。不要直接上传尚未分配坐标的 C 原始 `out_people.json`。
3. 在“YAML 配置”选择 C 配置文件，或在配置面板填写参数后点击“应用到当前仿真”。
4. 点击“启动 A+B+C 联调”。状态栏应明确显示 B 使用 `run_one_step(c_step_data)`；C 配置和人员已接入，但 C 每步行为目前为 `empty mapping`。
5. 连续点击三次“单步”。步数应逐次增加，人员位置应变化，耗时面板应刷新。
6. 点击“开始”，观察人员持续移动；点击“暂停”后步数应停止；点击“重置”后回到第 0 步并创建新的运行目录。
7. 点击“导出结果包”，确认能够下载 ZIP。

配置面板和外部 YAML 要分两次短测。测试面板时不要选择 YAML；点击“应用配置”后，按钮应变成“配置已应用”，下方出现绿色提示。若修改了任一配置项，页面会要求重新应用。测试外部 YAML 时，状态栏应明确显示配置来源为外部 YAML。未点击应用且未选择 YAML 时，页面会如实显示“后端默认/fallback”。

## 3. 画面与数据核对

当前仓库样例的合理预期：

- 地图尺寸 `135 x 116`，人员数 `40`。
- 人员不能进入墙体或障碍。
- 使用场景配置和随机种子 42 时，当前样例通常在约第 `158` 步完成；未应用随机种子时步数可能变化，验收重点是最终 40 人全部撤离且日志行数与状态数一致。
- 无烟样例的最大烟雾为 `0`；这只能证明无烟基线可运行，不能证明烟雾扩散正确。
- 若“重叠元胞”大于 0，前端会报警。这是 B 冲突处理的真实结果，不是 D 绘图重影。
- 若最大烟雾超过 10，前端会报警并保留原值，不会静默截断。
- 烟雾第一次超过 10 时，连续动画会自动暂停；页面应停在约第 4 步并显示“烟雾越界，动画已自动暂停”。需要继续研究原始 B 输出时仍可手工单步，但演示验收不应继续放大异常值。
- 右侧状态、输出目录和事件日志应在面板内自动换行，不应出现横向滚动条。若浏览器曾经缩放，先按 `Ctrl+0` 恢复 100%，再按 `Ctrl+F5` 强制刷新页面。

每次运行检查：

```text
outputs/experiments/<run_id>/
```

至少应有：

- `config_used.json`
- `people_log.csv`
- `event_log.csv`
- `metrics.json`
- `metrics_summary.csv`
- `final_frame.png`

`final_frame.png` 在初始化、重置、运行完成或导出时写入，不再每一步重复保存，以避免动画卡顿。

## 4. 自动公平验收

运行真实仓库样例：

```powershell
python -m experiments.acceptance_audit `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --yaml control/config_template.yaml `
  --run-id d_week7_manual_check_01
```

报告位置：

```text
outputs/experiments/d_week7_manual_check_01/acceptance_report.json
```

报告检查真实移动、墙体占用、人员重叠、烟雾范围、CSV 完整性、撤离事件一致性和是否在最大步数前完成。不要只看总状态，应查看每个 `checks` 和 `observations`。

日志器默认拒绝覆盖已有实验。重复验收时请把 `--run-id` 改成新的名称，例如把 `_01` 改成 `_02`，这样每次结果都可追溯。

## 5. 当前公平结论

- A 地图到 Grid 再到 D Canvas 的链路通过。
- C YAML、人员/关系文件和随机种子已进入初始化；人员坐标由 A 按地图分配后交给 B/D。C 每步行为输出尚未提供，社会关系策略效果不能宣称已完成。
- B 正式调用 `run_one_step(c_step_data)`，人员能移动并全部撤离。
- 当前 B 状态存在活动人员同元胞问题。
- 仓库样例没有烟源；独立烟源测试中 B 烟雾从第 4 步起超过 0 到 10 的接口约定。
- D 的日志、事件、指标和前端告警是真实读取上游状态，没有补造缺失字段。
