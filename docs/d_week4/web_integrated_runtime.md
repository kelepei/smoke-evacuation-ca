# D 第四周：统一网页初步联调版

## 当前入口

`visualization/prototype/integrated_runtime.html` 是 D 的统一网页入口。

页面保留 Week 2 的地图编辑、2.5D 预览、图层、控制、曲线、日志和参数面板；真实联调模式通过本地 D 服务读取 A/C 文件，并调用 B 当前的元胞自动机。

旧版示范页面 `visualization/prototype/week2_ui_prototype.html` 保留不改，作为 Week 2 备份。

## 启动

在仓库根目录运行：

```powershell
python -m experiments.web_runtime_server
```

再在浏览器打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

可选择任意符合当前接口的 A JSON/CSV 地图和 C `output_people.json`；C YAML 为可选复现配置。

也可点击“使用仓库真实示例（A+C）”，它使用：

- A：`scenarios/classroom_corridor.json`
- C：`social/output_people.json`
- 可选 C 配置：`control/config_template.yaml`
- B：`simulation.evac_simulation.EvacEngine`

## 已实现链路

```text
A 地图 → A loader → D 输入校验/场景组装
C 人群与关系 JSON → D 输入校验/ID 适配
B 当前 CA step() → D 快照、CSV 日志与本地网页 API
网页 → 2.5D 地图、人员、真实撤离曲线和日志
```

真实模式下，页面不会用前端演示值替代 B 未输出的字段；例如 `heading`、`risk`、`dose`、`conflict` 和 `exit_switch` 暂保持空值。

## 当前边界

- 右侧策略面板保留为后续 B 策略参数接口，当前不会伪造为已影响 CA。
- 当前仓库的课堂地图没有 `smoke_source`，默认真实示例没有烟雾扩散图层。
- C 的关系数据已读入快照并可显示；B 当前 CA 尚未使用关系或信息传播来改变移动决策。
- D 的 B 冲突兼容层只在内存中将 B 当前的 `None` 冲突结果解释为原地等待，不修改 B 源文件；B 正式修复后应移除该兼容层。
