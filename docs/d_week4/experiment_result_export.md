# D 第四周：真实实验结果包导出

## 本周目标

在已接通的 A 地图、C 人群/关系和 B 元胞自动机基础上，将一次真实运行保存为可下载、可复查、可复现的实验结果包。

## 使用方式

在仓库根目录启动本地服务：

```powershell
python -m experiments.web_runtime_server
```

浏览器打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

在页面中导入 A 的 JSON/CSV 地图、C 的 `output_people.json`，可选导入 C 的 YAML；或点击“使用仓库真实示例（A+C）”。运行至少一个真实时间步后，点击“导出结果”。

## 结果包内容

页面在真实联调模式下下载一个 ZIP 文件，内容包括：

```text
<run_id>/
├── metadata.json
├── config.json
├── inputs/
│   ├── <A 地图文件>
│   ├── <C output_people.json>
│   └── <C YAML，可选>
├── people_log.csv
├── event_log.csv
├── metrics.csv
├── evacuation_curve.svg
└── occupancy_heatmap.svg
```

`people_log.csv` 与 `event_log.csv` 由 D 读取 B 的真实逐步快照写入。`metrics.csv` 和两张 SVG 图均从真实日志计算，未使用前端演示数据。

## 当前可确认指标

- 初始人数；
- 已撤离人数；
- 疏散完成率；
- 滞留人数；
- 最后成功撤离时间；
- 全员撤离时间（只有全员实际撤离时才填写）；
- 成功撤离人员平均时间；
- 90% 疏散时间（达到时才填写）；
- 人员占用热力图；
- 疏散人数—时间曲线。

## 已验证的真实示例

使用仓库中的 A `scenarios/classroom_corridor.json`、C `social/output_people.json` 和 C `control/config_template.yaml`，经 B 当前 CA 运行 12 步后验证：

- 实际读取 40 名人群和 160 条关系；
- `time_s = step × 0.5`，第 12 步为 6.0 秒；
- 已撤离 10 人；
- `people_log.csv` 共 521 行（含表头）；
- ZIP 中包含日志、指标、两张 SVG 图和三份输入副本。

## 当前边界

- B 尚未提供 `actual_exit`，因此结果包不会伪造出口利用率；
- B 尚未提供 `heading`、`risk`、`dose`、`conflict`、`exit_switch` 时，对应日志字段保持为空；
- 当前仓库示例地图没有烟源，因此真实示例不会产生烟雾扩散图层；
- C 的人员和关系已接入并保存，但 C 的信息传播、从众和引导策略尚未作为 B 移动决策输入；
- 右侧策略面板仍是后续 B/控制模块接入入口，不把界面开关误写成已影响 CA。
