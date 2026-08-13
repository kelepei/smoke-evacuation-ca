# D 第五周统一前端运行说明

## 启动

在仓库根目录运行：

```bash
python -m experiments.web_runtime_server
```

然后打开：

```text
http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html
```

## 推荐演示路径

1. 点击“加载仓库样例”，会使用当前仓库中匹配的 A 地图、C 人群输出和 C YAML 配置初始化真实 B 运行时。
2. 点击“单步”确认人员位置随 step 更新。
3. 点击“开始”自动播放，点击“暂停”停止。
4. 运行后右侧会显示输出目录，形如：

```text
outputs/experiments/d_web_runtime_YYYYMMDD_HHMMSS_xxxxxx/
```

该目录包含：

- `config_used.json`
- `people_log.csv`
- `event_log.csv`
- `metrics.json`
- `metrics_summary.csv`

也可以点击“导出结果包”下载 zip。

## 自定义输入

- A 地图：选择 `.json` / `.csv` / `.png` 后，前端会立即请求 `/api/map/preview`，由 A loader 生成 Grid，再由 D 端按 `cell.x / cell.y / cell.cell_type` 绘制。
- C 配置：可在同一页面编辑 `total_persons`、`profile_ratios`、`group_config`、`relation_intensity`、`random_seed`，点击“应用配置”后会作为下一次联调初始化的 YAML 传入。
- C 人群：启动真实 B 联调时需要选择 `output_people_position.json` 或等价 persons/relations JSON。

