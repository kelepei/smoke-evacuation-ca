# 地图时间型 A* 管控策略（vision_astar，参考监控转化地图方法）

## 1. 论文方法

本策略借鉴田卫等（2026）《既有建筑改造施工火灾逃生路径动态规划方法研究》的方法，但不引入任何监控文件：

- 用网页选择的 A 地图（网格、墙体、障碍、出口）构建逃生网格；
- 用时间型 A* 计算每名人员到出口的最短耗时路径；
- 区域基础速度沿用论文表 2 的施工区域速度；
- 区域实际速度按平台既有的烟雾浓度速度规则实时折减；
- A* 下一路点传给 CA 移动模型，路线真实影响人员行动；
- 路线同时输出 PNG/JSON，并通过 Web 快照传给网页画布显示。

论文原文的 YOLO 检测和透视矩阵只在方法层面参考；当前实现不读取、也不要求任何 `*_monitor.json`。

## 2. 烟雾浓度如何影响区域速度

`control/vision_mapped_astar.py` 的 `refresh_smoke()` 使用平台原有的人员烟雾速度规则：

1. 烟雾接近 0.5 时出现恐慌加速项；
2. 烟雾超过 0.65 后进入毒性减速；
3. 速度下限为正常速度的 0.20；
4. 烟雾超过 0.90 的格子视为不可通行。

区域速度按如下方式计算：

```text
panic = exp(-(smoke - 0.5)^2 / (2 * 0.25^2))
toxic = 1.0                                      smoke <= 0.65
        max(0.35, 1 - (smoke - 0.65) * 1.7)       smoke > 0.65
factor = max(0.20, (1 + 0.45 * panic) * toxic)
region_speed = base_region_speed * factor
```

因此，同一个 A 地图在不同烟雾场下会得到不同的 A* 路径和预计耗时。

## 3. 基础区域速度

| 区域 | 速度 |
|---|---:|
| 普通区域 | 1.95 m/s |
| 施工垃圾/材料堆砌 | 1.38 m/s |
| 钢筋绑扎 | 1.42 m/s |
| 钢管支撑 | 1.70 m/s |

地图 cell 的 `type`、`zone_type`、`construction_type`、`semantic` 字段可用于标记这些区域；只有墙体/障碍物会直接阻断路径。没有这些字段时，全图使用普通区域速度，再叠加烟雾折减。

## 4. A* 与人员行动

- 边代价：`距离 / 当前烟雾折减后的区域速度`；
- 启发函数：沿当前点到出口连线分段计算 `长度 / 区域速度`；
- 出口概率：`P_j = exp(-D_j) / Σ exp(-D_k)`；
- 每名人员保存完整路径 `planned_path`；
- 当前格的下一个路点通过 `route_next_cell` 传给 `simulation/ca_model.py`；
- `vision_astar` 设置 `strict_route=True`：下一路点可通行且未被占用时，CA 直接选择该路点；只有该路点被占用/阻断或人员偏离路线时，才回退到 CA 效用决策；
- 人员偏离路线后，会通过相邻路径格重新接回 A* 路线，避免路点不可达时原地绕圈；
- 烟雾场变化超过阈值或达到重规划间隔时重新计算。

## 5. 网页显示

`CWebRuntimeBridge` 会把路线写入：

```text
snapshot.c_runtime.vision_routes
```

每个路线包含：

```json
{
  "person_id": 1,
  "exit_id": "exit_01",
  "path": [[3, 3], [4, 4], [5, 5]],
  "time_s": 5.4,
  "distance_m": 9.45
}
```

网页的 `drawVisionRoutes()` 会在主画布和放大画布上绘制这些路径；图层栏新增“A*路线”开关，默认开启。

## 6. 运行方式

```bash
python main.py --strategy vision_astar   --map maps/examples/simple_room.json   --social on --visual off --report off
```

更换网页/命令行选择的 A 地图即可得到该地图的路径图：

```bash
python main.py --strategy vision_astar --map maps/templates/mall.json --social on --visual off
python main.py --strategy vision_astar --map maps/templates/canteen.json --social on --visual off
```

可调参数：

```bash
--vision-replan-interval-s 30
```

## 7. 输出文件

```text
outputs/experiments/<run_id>/evacuation_routes_<地图名>_initial.png
outputs/experiments/<run_id>/evacuation_routes_<地图名>_final.png
outputs/experiments/<run_id>/evacuation_routes_<地图名>_initial.json
outputs/experiments/<run_id>/evacuation_routes_<地图名>_final.json
```

PNG 中显示：墙体/障碍、区域速度分区、烟雾热区、出口、人员起点、A* 路径和预计耗时。

## 8. 参考文献

[1] 田卫, 李欣阳, 郑福康, 周菻鈜, 胡家乐, 高明哲. 既有建筑改造施工火灾逃生路径动态规划方法研究[J/OL]. 安全与环境学报, 2026. DOI: 10.13637/j.issn.1009-6094.2025.1925.


## 烟源位置约束

`vision_astar` 在烟场尚未扩散时也会读取 A/B 提供的真实烟源坐标，在烟源周围建立确定性的路线代价（默认半径 4 m、最大代价倍率 13）。这是路线避让约束，不是伪造烟雾浓度；真实烟雾场仍负责区域减速和阻塞。烟源位置会同时标注在路径图上。
