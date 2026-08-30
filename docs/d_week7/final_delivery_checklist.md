# 交稿前真实检查表

本表只记录已用当前仓库 A、B、C 文件验证过的内容，不把未完成的模型结果写成完成。

## 已证实可运行

- A 的 `maps/edited_map.json` 能经 A loader 生成 `Grid`，并在 D 页面显示墙体、障碍、出口和自由区域。
- C/A 的 `control/output_people_position.json` 与 `control/config_template.yaml` 能进入 D 初始化；当前样例为 40 人、随机种子 42。
- D 通过 B 的 `EvacEngine.run_one_step(c_step_data)` 完成真实 E0。2026-08-30 两轮均为 40/40、158 step、79.0 s，且同 seed 日志完全一致。
- D 会输出 `config_used.json`、`people_log.csv`、`event_log.csv`、`metrics.json`、`metrics_summary.csv` 和 `final_frame.png`，网页可导出结果包。

## 当前交稿阻塞项

| 项目 | 当前事实 | 负责人 |
| --- | --- | --- |
| 人员冲突 | 第 1 步有 3 个活动人员重叠元胞 | B |
| 性能 | E0 后端平均单步约 104 ms | 已满足当前 D 基线 |
| 有烟场景 | 当前 E0 地图无 `smoke_source`；正式有烟地图接口已确认 | 实验输入 |
| 出口利用率 | B 尚未提供 `actual_exit`，D 显示 NA | B |
| 社会关系对比 | C 每步行为仍为空映射 | C |
| 任务书的四组对比、图表和视频 | 尚未有真实完整运行结果，不能伪造 | ABCD 联调后执行 |

## 最短手工验证

1. 在仓库根目录运行 `python -m experiments.web_runtime_server`。
2. 打开 `http://127.0.0.1:8765/visualization/prototype/integrated_runtime.html`。
3. 上传 `maps/edited_map.json`、`control/output_people_position.json`、`control/config_template.yaml`，点击“启动 A+B+C 联调”。
4. 确认状态栏写有 `EvacEngine.run_one_step(c_step_data)`、地图尺寸 `135 x 116`、人数 `40`。
5. 点击一次“单步”，确认步数变为 1、人员位置变化、右侧记录了真实耗时。
6. 点击“导出结果包”，解压后确认六个输出文件都存在。
7. 运行以下命令保存公平的单步验收报告：

```powershell
python -m experiments.acceptance_audit `
  --map maps/edited_map.json `
  --population control/output_people_position.json `
  --yaml control/config_template.yaml `
  --run-id final_manual_check_01 `
  --max-steps 1
```

报告的 `map_and_population_loaded`、`all_people_moved`、`no_wall_or_obstacle_occupancy`、日志存在和事件一致性应为 `true`。`no_active_person_overlap` 与 `finished_before_max_steps` 当前会是 `false`，这是待上游修复项。

## 正式交稿判定

当前版本可作为 D 端 E0 联调、记录、分析和结果浏览基线提交，但不能宣称 E2 或项目全部任务书指标完成。科学对比仍需 B 冲突/actual_exit 与 C→B 每步行为契约到齐后再执行。
