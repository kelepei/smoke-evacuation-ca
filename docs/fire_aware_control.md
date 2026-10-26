# 火灾感知自适应管控策略（C10 重构说明）

## 1. 改造目标

原 C10 的 `route_control` 只做“最拥挤出口 → 最空闲出口”的按比例改派，`zoned_evacuation` 只按位置切段分配。该逻辑没有使用火灾场，也没有体现异质人群的密度、弱势群体和避让行为。

本次将原来分开的 **出口关闭、区域封锁、分区疏散、路线管控** 合并为一个用户可见策略 **`fire_aware` / `integrated`（内部枚举 `fire_aware_adaptive`）**。对外只有一个策略入口，内部按固定顺序编排四个子管控，并把三篇文献的结论落到可执行规则中：

| 文献结论 | 代码中的落地方式 |
|---|---|
| 江华龙等（2026）：用温度、CO、能见度影响系数构造火灾现场指数；以等效路径长度替代几何距离；随火灾阶段动态重规划 | `control/fire_aware_control.py` 的 `FireHazardField`、`FireAwareRouter._dijkstra`、`plan` |
| 江华龙等（2026）：温度 60 ℃、CO 0.5×10⁻³ mol/mol、能见度 10 m 为危险阈值；优先引导至危险指标上升滞后的出口 | `temperature_speed_factor`、`co_speed_factor`、`visibility_speed_factor`、`is_exit_safe` |
| 刘彬文（2025）：0.375 人/m² 是疏散时间增长速度分水岭 | `FireAwareConfig.density_threshold=0.375`，`local_density` |
| 刘彬文（2025）：信息素扩散半径 2~4 m 最佳，第 4~5 章取 3.2 m；需消除个体上一步自身信息素影响 | `PheromoneField`（沉积、扩散、衰减、自身一步修正），`pheromone_radius_m=3.2` |
| 刘彬文（2025）：避让概率 0.5 时疏散时间最小；避让阈值 0.4~0.8 时减小速率最大；建议以老年人竞争力作为阈值 | `avoid_probability=0.5`、`avoid_threshold=0.6`（老年人竞争力区间 0.54~0.63；可在 0.4~0.8 调整），`simulation/conflict_solver.py` |
| 刘彬文（2025）：老年人是弱势群体，建议专门通道、增加标识和协助 | `FireAwareRouter.assign_person_attributes` 中 `vulnerable` 优先分配；拥挤惩罚对弱势群体更大 |
| 张培红等（2001）：建筑空间分类、预先计划疏散路线、出口/楼梯滞留人数与持续时间、可用安全疏散时间是评价关键 | 按空间/出口计算等效距离；出口拥堵惩罚；策略统计输出出口分配与重规划次数 |

## 2. 新策略的决策流程

综合管控的执行顺序固定为：

1. **出口关闭**：按触发步关闭指定出口，并改派受影响人员；
2. **区域封锁**：按手动或烟雾阈值封锁区域，只禁止进入、允许已在内人员离开；
3. **分区疏散**：按位置给每个人分配一个主出口（计划疏散路线）；
4. **路线管控**：用火灾感知等效距离动态修正目标出口；分区出口作为软偏好，只有火灾/拥堵优势超过 `zone_bias_m` 时才改派。

因此不需要再分别选择四种策略；选择 `fire_aware` 即同时包含四类管控。旧的单独策略枚举仅保留用于向后兼容和单因素对比实验。


1. **火灾场更新**  
   每步把平台烟雾矩阵传给 `FireHazardField.update`。若调用方提供温度、CO、能见度矩阵，则严格按江华龙等（2026）式 (6)~(8) 计算：
   - 温度影响系数 `f1(T)`：30 ℃ 以下为 1；30~60 ℃ 随温度升高而增大；60~120 ℃ 随温度升高而减小；120 ℃ 以上为 0。
   - CO 影响系数 `f2(ρ)`：按论文式 (7) 的分段形式。
   - 能见度影响系数 `f3(Kc)`：`Kc = 3/S`，按论文式 (8) 分段。
   - 火灾现场指数 `W = f1·f2·f3`；通行难度 `ε = 1/W`；等效边长 `L = d·ε`。

2. **动态重规划**  
   `replan_interval_s=200`，与江华龙等（2026）把 600 s 火灾过程分为 0~200 s、200~400 s、400~600 s 三个阶段一致；同时当火灾场平均/最小通行系数变化超过 `hazard_change_threshold` 时提前重规划。

3. **出口选择**  
   对每个出口做 Dijkstra，边权为等效长度；每个行人选择“等效距离 + 出口拥堵惩罚”最小的安全出口。若当前出口仍接近最优，则保持目标出口（滞回），避免目标出口来回切换。

4. **密度自适应**  
   用 2 m 半径内人数/面积计算局部密度。低于 0.375 人/m² 时按论文“低密度下长距离信息素/最短路径更有利”的思想，不额外施加拥堵分散；高于 0.375 人/m² 时启用出口拥堵惩罚和负载分散。

5. **弱势优先与避让**  
   - 老年人、儿童、患者标记为 `vulnerable`，优先分配出口，且拥堵惩罚权重更高。
   - 冲突消解时按竞争力排序；强势者竞争力比弱势者高出 `avoid_threshold` 时，以 `avoid_probability` 概率让行（刘彬文 2025 第 5 章）。
   - 竞争力采用论文式 (4.1) 的幂函数思想：`φ = (δ·λ·u_norm)^0.5`，其中性别系数 `δ` 男 0.5/女 0.4、年龄系数 `λ` 青年 3~4/中年 1~2/老年 0~1.5（表 4.2），速度按参考速度 1.4 m/s 归一化。

## 3. 新增/修改的代码

| 文件 | 修改内容 |
|---|---|
| `control/fire_aware_control.py` | 新增：火灾场、等效距离 Dijkstra、信息素场、密度自适应、弱势优先、避让属性与统计 |
| `control/control_strategy.py` | 新增 `ControlStrategyType.FIRE_AWARE_ADAPTIVE`；`ControlStrategyEngine` 接入新路由器；`route_control` 可选 `mode=fire_aware`；统计接口扩展 |
| `main.py` | 新增 `--strategy fire_aware`；新增密度、信息素半径、重规划间隔、避让概率/阈值、烟雾代理权重参数；把真实 `grid` 和 `dt` 传入管控引擎；修复 `panic_cache` 在 `c_step_data` 之前未定义的问题 |
| `simulation/conflict_solver.py` | 冲突消解由随机胜者改为竞争力排序；增加刘彬文（2025）的强势者按概率避让规则 |
| `control/config_template.yaml` | 增加 `fire_aware_control` 参数段 |
| `control/tests/test_fire_aware_control.py` | 新增 7 个测试：温度/CO/能见度系数、烟雾侧规避、密度分水岭、弱势优先、避让、信息素自修正 |

## 4. 运行方式

```bash
python main.py --strategy fire_aware --social on --visual off --report off
```

可调参数：

```bash
python main.py --strategy fire_aware \
  --density-threshold 0.375 \
  --pheromone-radius-m 3.2 \
  --replan-interval-s 200 \
  --avoid-probability 0.5 \
  --avoid-threshold 0.6 \
  --smoke-proxy-weight 3.0 \
  --visual off --report off
```

若希望沿用 `route_control` 名称但启用新路由：

```bash
python main.py --strategy route_control --route-mode fire_aware --social on --visual off
```

## 5. 重要限制（避免把代理量当成 FDS 结果）

当前项目只输出归一化烟雾浓度场，尚未模拟 FDS 的温度、CO 体积分数和能见度。因此：

- 当调用方提供 `temperature_grid`、`co_grid`、`visibility_grid` 时，代码严格使用江华龙等（2026）公式；
- 当前主程序只传烟雾场时，等效距离中的火灾项使用 `1/(1 + smoke_proxy_weight·smoke)` 作为**单调烟雾代理**，并在代码和文档中明确标注为代理，不伪造温度/CO 数值，也不把烟雾浓度直接当作能见度或 CO 体积分数；
- 后续若接入 FDS/PyroSim 切片数据，可直接把温度、CO、能见度矩阵传入 `ControlStrategyEngine.update_all`，无需改路由主逻辑。

## 6. 参考文献

[1] 江华龙, 江辉仙. 图书馆火灾应急疏散路径规划的蚁群算法优化模型[J]. 福建师范大学学报(自然科学版), 2026, 42(5): 25-35. DOI: 10.12046/j.issn.1000-5277.2025090033.

[2] 刘彬文. 差异化竞争力下多速度异质人群疏散建模与特性研究[D]. 赣州: 江西理工大学, 2025.

[3] 张培红, 陈宝智, 刘丽珍. 大型公共建筑物火灾时人员疏散行为规律研究[J]. 中国安全科学学报, 2001, 11(2): 22-26.

若已由 FDS/PyroSim 导出温度、CO、能见度切片，可直接输入论文公式所需的真实场：

```bash
python main.py --strategy fire_aware \
  --temperature-grid outputs/fds/temperature.npy \
  --co-grid outputs/fds/co.npy \
  --visibility-grid outputs/fds/visibility.npy \
  --social on --visual off --report off
```

三个数组可为二维 `(grid_height, grid_width)` 或三维 `(T, grid_height, grid_width)`（按帧取对应切片）。不提供时，主程序只传烟雾场，
并按本文档第 5 节使用烟雾代理；不会伪造温度/CO 数值。


## 烟源位置与个体路径

`fire_aware` 现在会读取 A/B 提供的真实烟源坐标，在烟源周围建立确定性的路线绕行代价（默认半径 4 m、最大代价倍率 13），因此烟场尚未扩散时人员也会避开烟源。每个人员还会从同一火灾等效距离场重建一条个体路径，并通过 `route_next_cell` 作为软引导传给 CA；路径重建使用轻微的分散代价，使同向人群在宽通道中优先选择不同近似最优车道，而不是全部压成一条线。窄通道内仍会自然排队，这是物理通行能力限制。
