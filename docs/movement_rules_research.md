# 行人疏散移动规则改造说明（文献调研版）

本文档对应这次对 B 模块移动规则的改造：把调研文档中选定的四类规则
（恐慌阈值、加速、多出口、危险感知）以及"速度 + 恐慌抢格"真正落到
`simulation/` 的 CA 引擎里。项目只有一个仿真引擎 `simulation.evac_simulation.EvacEngine`，
网页 runtime 是唯一运行入口；`main.py` 是个人测试文件，不属于平台，保持原样未改。
`experiments/c_runtime_bridge.py` 改为只镜像 B 的恐慌/速度结果，不再重复计算。

- 目标：行人移动体现"烟雾风险感知 + 社会信息传播"，而不是只按最近出口走直线。
- 原则：**B 是移动决策的唯一实现**；C 只提供信息/行为输入；同一时刻只允许一套拥堵/速度门控。

---

## 1. 文献与规则的对应关系

| 规则 | 依据（调研文档） | 实现位置 | 关键结论 |
|---|---|---|---|
| 恐慌阈值 + 传染 | 白鹏 2026（情绪阈值 α=0.5、ρ~N(0.5,0.1)、β1=0.10/0.3s、恐慌 1.2 倍）；王莉 2023（恐慌后易做错误决定） | `simulation/panic_model.py` | SIS 情绪感染：恐慌值 0~1，越过 α=0.5 进入恐慌；邻居传染 + 自衰减；恐慌者目标速度 ×1.2 |
| 加速 / 多速度 | Xiong 2025（分速度区间实测 μ/σ、加速/减速两阶段）；高瑾 2021（亏欠时间、0.37~1.85 m/s） | `simulation/speed_model.py` | 按当前速度插值采样加速度；速度差异用"距离亏欠"在元胞网格上补偿 |
| 多出口动态选择 | 霍非舟 2023（P1/P2/E、σ=0.002、Pm=0.9、R=3.2 m、α=0.45） | `simulation/exit_choice.py`、`simulation/floor_field.py` | 距离概率 + 出口区域密度概率加权；σ 与 Pm 控制换出口 |
| 危险感知（替代不耐烦） | 张琦 2025（imp=(1-v/v_e)·exp(-γΔv)·exp(β·t_c)，γ=0.5、β=0.1、λ=0.2）；张琦 2022（三种行为模式） | `simulation/danger_perception.py`、`simulation/ca_model.py` | 低速/停滞累积不耐烦，叠加烟雾风险；超阈值触发横向绕道、抢格优先、换出口 |
| 速度+恐慌抢格 | Xiong 2025（摩擦因子 μ1、速度指数 μ2）；白鹏 2026（恐慌者更易获得通行权）；张琦 2025（不耐烦高者优先） | `simulation/conflict_solver.py` | 先过摩擦概率 μ1，再按 `v^k × 恐慌/危险加成` 选赢家；按竞争力顺序占格 |
| 信息传播对移动的影响 | 陈亮 2024（感知距离/社会信息） | C 的 `info_state` + B 的 `_is_evacuating` | 未获知者不按疏散路径走（walk/freeze），已获知者才进入疏散决策 |

> 说明：文档中"结伴行为"和"有限视野/沿墙"没有作为独立机制实现。前者仍由 C 的
> group/herding 行为通过 `is_waiting`、`follow_*`、`exit_preference` 输入；后者体现在
> 出口距离场（绕墙最短路）与烟雾惩罚上。若后续要加"零视野沿墙"，建议在
> `ca_model.calc_next_position` 里按 `visibility` 分支，而不是再复制一套移动循环。

---

## 2. 数学模型

### 2.1 恐慌值（白鹏 2026 情绪感染）

每个行人维护恐慌值 `P_i ∈ [0,1]`（原文称"情绪值"）：

```
ρ_i ~ N(0.5, 0.1)                     # 人格特质，白鹏 2026
P_i(0) = ρ_i （原文）或 0（项目默认，由烟雾驱动）

exposure_i = min(1, S_i / smoke_ref)
           + delta_weight · min(1, max(0, ΔS_i) / dt)
           + risk_weight  · min(1, Risk_i / risk_ref)
target_i   = 1 - exp(-exposure_i)

P_i(t+dt) = clip( P_i + dt·[ ρ_i·growth·target_i·(1-P_i)
                             + ρ_i·contagion·ratio_i·(1-P_i)
                             - decay·P_i ], 0, 1 )
```

- `ratio_i`：邻域（默认 3×3）内已恐慌者比例，实现情绪感染。
- 阈值：原文 `α = 0.5`，高于 α 为感染者（恐慌）、低于 α 恢复为易感者；
  这里 `threshold = exit_threshold = 0.5`，不再额外加滞回。
- 自衰减：原文 `β1 = 0.10`（步长 0.3 s），换算为 `decay ≈ 0.333/s`。
- 进入恐慌：`speed_multiplier = 1 + speed_gain = 1.2`（白鹏 2026）。
- 项目扩展：烟雾/风险作为外部刺激项驱动 `target_i`（原文没有烟雾）；
  `initial_from_susceptibility=True` 可复现原文 `E(0)=ρ(m)`。

### 2.2 加速与多速度

每个行人维护当前速度 `v_i`（m/s）与距离亏欠 `credit_i`（单位：元胞）：

```
v_target = v_max · (1 + speed_gain·[is_panicked]) · smoke_factor · dose_factor · density_factor
a        ~ N(μ(v), σ(v))       # Xiong 2025 实测分布，按当前速度插值
v        = v + a·dt            (v < v_target)
v        = v - a·dt            (v > v_target)
v        = 0                   (上一步遭遇真实阻挡)
credit  += v·dt / cell_size
前进格数 = min(floor(credit), max_cells_per_step)   # 默认最多 2 格
```

- **加速度/减速度按 Xiong 2025 的实测分布采样**：把速度分成 1 m/s 区间，
  每个区间一个高斯分布 `N(μ_A, σ_A)` / `N(μ_D, σ_D)`，按当前速度线性插值。
  原文 Table 1 的取值（m/s²）：

  | v | 0.5 | 1.0 | 1.5 | 2.0 | 2.5 | 3.0 | 3.5 | 4.0 | 4.5 | 5.0 |
  |---|---|---|---|---|---|---|---|---|---|---|
  | μA | 1.30 | 1.43 | 1.64 | 1.76 | 1.62 | 1.39 | 0.99 | 0.86 | 0.73 | 0.40 |
  | σA | 0.60 | 0.73 | 0.65 | 0.73 | 1.16 | 1.07 | 0.85 | 0.62 | 0.45 | 0.53 |
  | μD | 1.90 | 1.86 | 1.88 | 1.79 | 1.66 | 1.39 | 0.89 | 0.78 | 0.61 | 0.43 |
  | σD | 1.90 | 0.56 | 0.72 | 0.83 | 1.31 | 1.11 | 0.74 | 0.60 | 0.51 | 0.49 |

- `accel_model="xiong2025"`（默认）用上面的分布；`"constant"` 时回到固定 `accel`，
  便于对照实验或旧场景复现。
- **提前减速（Xiong 2025 Rule 2）**：先算相对减速距离

  ```
  L(Δv) = 10 − sqrt((5 − Δv) / 0.05)      # Δv = v_i − v_前方
  ```

  沿行进方向探测最近障碍/慢行者：距离 < L(Δv) 时不再加速，而是按减速度分布减速到
  对方速度（障碍物按 0 m/s）。方向优先用上一步位移，静止时用目标出口距离场的下坡方向；
  探测最远 `lookahead_m=10 m`（覆盖 L 的最大值）。
- `cell_size=0.5 m`、`dt=0.5 s` 时，1 格/步 = 1 m/s。0.37~1.85 m/s 的差异通过"有时走 2 格、
  有时攒一步再走"体现。
- 初始 `v=0.8·v_max`、`credit=0.5`：行人进入场景时已在行走，避免第 1 步全部静止。
- 关键区分：**距离亏欠导致的停留不算阻挡**，否则慢的人会被永久清零（开发中踩过这个坑）。
- 元胞尺度：地图 JSON 的 `cell_size` 可能是像素尺度（例如 `edited_map.json` 是 10），
  不是米。`EvacEngine` 只在 `0.2~2.0 m` 之间采信 `grid.cell_size`，否则移动换算按 0.5 m
  参考元胞处理；否则会出现"一步只走 1/20 格"、网页端看起来完全不动的问题。
- 性能：距离场 BFS 使用一维 `bytearray` + 一维距离表，避免在大地图上反复做
  `getattr/str/lower`。135×116 的地图、40 人时，B 引擎 + C 桥约 20 ms/步，
  网页 runner（含快照与 CSV 日志）约 100 ms/步。警报后 D 端会生成引导推荐，
  当前实现是"每个行人 × 每个出口"各跑一次 Dijkstra，约 1.2 s/步；
  建议 D 端改成"每个出口实体一次多源 Dijkstra"（约 150 ms/步），
  具体见随附的《D 板块待改说明》（未放入项目）。

### 2.3 多出口选择（按霍非舟 2023 原文公式）

对行人 i、出口 k：

```
d_k    = 行人到出口 k 各元胞距离的最小值
ρ_k    = N_k / C_k                  # 出口区域人数 / 最大可容纳人数
P1k    = exp(-d_k / Σd) / Σ_j exp(-d_j / Σd)      # 距离概率
P2k    = exp(-ρ_k)      / Σ_j exp(-ρ_j)           # 拥挤概率
E_k    = (1 - α)·P1k + α·P2k                      # 综合概率
μ      = argmax_k E_k
ΔE     = E_t(μ) - E_{t-1}(μ)                       # 最大概率的步间变化
若 ΔE ≥ σ：以 Pm 保持原出口，以 1-Pm 换到 μ；否则保持原出口
```

- `α`：出口区域密度权重（原文最佳区间 0.3~0.6，取 0.45）。
- `σ`：选择模糊度（原文 0.2×10⁻²，即 0.002）。
- `Pm`：出口保持率（原文 σ=0.2×10⁻² 时最佳 0.9）。
- 出口区域：以出口为中心、半径 `R=3.2 m` 的圆（出口在墙上，室内部分即原文的半圆）；
  `C_k` 按区域面积 / 元胞面积换算，`capacity_mode` 可选 `half_disc` / `rect`。
- 距离 `d_k` 默认用可绕墙的 BFS 静态场；`distance_mode="euclidean"` 时与原文一致
  （到出口元胞的欧氏最短距离）。
- 原文没有烟雾项；本项目用 `smoke_weight` 对 E_k 做指数惩罚：
  `E_k ← E_k·exp(-smoke_weight·S_k/smoke_ref)`，默认 0.15，设为 0 即完全按原文。
- 原文没有冷却；`switch_cooldown=0`。`reevaluate_interval=1` 表示每个时间步都判断，
  与原文一致。

> 论文结论：动态出口选择在低密度阶段基本无效、在高密度阶段才有效；σ 越大越"犹豫"，
> Pm 越大越"懒得换"。因此稀疏场景里换出口次数为 0 是正常现象，不是实现错误。

高密度验证（150 人、8 个出口元胞、`smoke_weight=0`、`distance_mode=euclidean`）：

| 配置 | 换出口次数 | 疏散步数 |
|---|---|---|
| σ=0.002，Pm=0 | 117 | 38 |
| σ=0.002，Pm=0.9 | 4 | 46 |
| σ=0.01，Pm=0.9 | 0 | 46 |

趋势与原文一致：Pm 降低会增加换出口次数并在高密度下缩短疏散时间；σ 增大则趋于
"不换出口"。


### 2.4 危险感知（替代"不耐烦"，张琦 2025）

按张琦 2025 的不耐烦公式计算：

```
v_i(t)   = 最近 10 个时间步的位移 / 时间
t_c      = 低速或停滞持续时间（v < λ·v^e，λ=0.2）

imp_i(t) = (1 - v_i/v_i^e) · exp(-γ·(v_i(t)-v_i(t-1))) · exp(β·t_c)

imp_total = imp_i + smoke_gain · Risk_norm          # 项目扩展：烟雾附加压力
D_i       = imp_total / (1 + imp_total)             # 归一化到 0~1
```

- 原文参数：`γ = 0.5`（速度变化趋势敏感性）、`β = 0.1`（停滞时间敏感性）、`λ = 0.2`；
  原文实验取 γ=0.5、β=0.1。
- `smoke_gain=0` 时完全退化为原文的不耐烦模型。
- 超过 `danger.threshold=0.5` 后：

1. **横向绕道**（张琦 2022 模式 1）：在 `calc_next_position` 中给垂直于"下坡方向"的候选格
   加 `w_lateral·(0.75 + D_i)`；
2. **抢格优先**（模式 2）：冲突竞争力加 `priority_gain·D_i`；
3. **换出口**（模式 3）：降低出口保持率 `Pm`（默认 `exit_pressure_gain=0`，即不削弱）。

> 多速度表达：原文用"随机数 p_i < η_i=v_i/v_max 才移动"的间接算法；
> 本项目用高瑾 2021 的"距离亏欠 + Xiong 2025 加速"实现同样的速度差异，
> 并保留加速度过程。两者在平均速度上等价，但本项目多了起步/受阻后的加速段。

### 2.5 抢格（Xiong 2025 摩擦模型 + 恐慌/危险压力）

同一目标格多人竞争时，先按 Xiong 2025 的摩擦模型判定：

```
μ1 = (⟨v⟩ / v_max)^m                 # 本次所有竞争者都进不去的概率
μ2,i = v_i^k / Σ_j v_j^k             # 摩擦未发生时，i 获胜的概率
```

- `m`：竞争强度；`m=0` 表示"完全竞争"（双方都过不去），`m` 越大越合作；
- `k`：速度优势强度；`k=0` 时所有速度等概率，`k` 越大高速度者越占优；
- 论文正文没有给出 m、k 的具体取值（敏感性分析在 Ref [30]），默认取 `m=1.0`、`k=1.0`、
  `v_max=5.0 m/s`，可在 `movement.conflict` 里调；
- 项目扩展：把恐慌/危险压力乘到 μ2 的权重上
  `weight_i = v_i^k · (1 + w_panic·P_i + 0.5·D_i)`，
  当 `P_i=D_i=0` 时即纯原文的速度竞争；`friction_enabled=False` 可退回原来的
  "按竞争力顺序占格"。

**原地不动者视为静止占用者**：其格子不参与竞争，想走进去的人直接失败，避免两人叠格。
实现上仍按竞争力从高到低依次占格，目标格被占就留下、已空出才占住；
摩擦判定发生在这一步之前（同一目标格的竞争者先整体过 μ1/μ2）。

---

## 3. 执行顺序与职责边界

`EvacEngine.run_one_step()` 的顺序（这次改造的重点）：

```
烟雾更新 → 风险/剂量 → 恐慌更新 → 危险感知更新 → 出口选择
   → 速度/credit 判定（谁允许移动）
   → 只让允许移动的人 calc_next_position
   → 不允许移动的人保持原位（作为静止占用者）
   → resolve_conflict（速度+恐慌）
   → 更新坐标/撤离判定 → 未获知者日常行走 → 统计
```

这样解决了评审文档 `c.docx` 指出的三个问题：

1. **门控顺序**：不再"B 先移动、C 再拉回"，不会出现被门控者赢了格子又退回；
2. **拥堵重复**：B 的确定性 credit 门控生效时，`CongestionModel.need_congestion_wait()`
   不再被调用（`congestion_model=None`），C 的 `_speed_gate()` 只做状态镜像，不再计算；
3. **信息状态归属**：C 提供 `info_state` 时（网页桥），B 只读取、不再改写；
   只有完全没有 C 信息输入时才启用 B 的自动知情 fallback。

`experiments/c_runtime_bridge.py` 的改动：

- `_panic_state()` 在 B 启用时直接返回 B 的恐慌值；
- `_speed_gate()` 只镜像 `person.can_move` 与 B 的统计；
- `after_b_step()` 不再回滚 B 的移动，也不再自己跑 `_daily_walk()`；
- `_publish_state()` 转发 B 的统计，保留网页依赖的兼容字段。

`main.py` 未改动：它是个人测试文件，不属于这个项目，也没有接入平台的运行链。

---

## 4. 参数表（默认值）

全部在 `simulation/movement_config.py`，运行时可用：

```python
scene.parameters["movement"] = {
    "panic": {"threshold": 0.5, "speed_gain": 0.2},
    "speed": {"accel": 1.5},
    "exit_choice": {"retention": 0.3, "sigma": 0.1},
    "danger": {"threshold": 0.6},
    "conflict": {"w_panic": 0.8},
}
```

D 端完成 `movement` 透传后（见随附的《D 板块待改说明》第 2 节），
网页 API 也可以直接传同一份配置：

```json
{
  "map": "...", "population": "...",
  "movement": {"panic": {"threshold": 0.45}, "exit_choice": {"retention": 0.2}}
}
```

恐慌阈值以 B 的 `movement.panic.threshold` 为唯一标准；`c_runtime.panic_threshold`
只是把它转发给界面显示，C 不再用自己的 `panic_threshold` 统计恐慌人数。

| 组 | 参数 | 默认 | 含义 |
|---|---|---|---|
| 总开关 | `enabled` | True | False 时回到旧 CA（1 格/步、无新规则） |
| 未获知 | `unknown_behavior` | walk | walk / freeze / evacuate |
| 未获知 | `unknown_speed_factor` | 0.35 | 日常行走速度系数 |
| panic | `smoke_ref` | 0.75 | 烟雾归一化参考 |
| panic | `delta_weight` / `risk_weight` | 0.8 / 0.6 | ΔS、综合风险的权重 |
| panic | `growth` / `decay` | 1.2 / 0.333 | 恐慌增长速率、自衰减（原文 β1=0.10/0.3s） |
| panic | `contagion` / `contagion_radius` | 0.30 / 1 | 情绪感染强度/半径 |
| panic | `susceptibility_mean` / `susceptibility_sd` | 0.5 / 0.1 | 人格特质 ρ~N(0.5,0.1) |
| panic | `initial_from_susceptibility` | False | True 时按原文 E(0)=ρ 初始化 |
| panic | `threshold` / `exit_threshold` | 0.50 / 0.50 | 情绪阈值 α（原文 0.5，无滞回） |
| panic | `speed_gain` | 0.20 | 恐慌加速 = 1.2 倍 |
| speed | `accel_model` | xiong2025 | xiong2025=实测分布；constant=固定加速度 |
| speed | `accel` / `accel_sd` / `accel_min` / `accel_max` | 2.0 / 0.6 / 0.5 / 5.5 | 仅 constant 模式使用（m/s²） |
| speed | `decel` | 2.5 | 仅 constant 模式的减速度（m/s²） |
| speed | `use_deceleration_distance` / `lookahead_m` | True / 10.0 | Xiong Rule 2 提前减速与探测距离（m） |
| speed | `initial_velocity_ratio` / `initial_credit` | 0.8 / 0.5 | 初始速度比例/初始距离亏欠 |
| speed | `max_cells_per_step` | 2 | 单步最多前进格数 |
| speed | `smoke_slow` / `min_speed_factor` | 0.55 / 0.30 | 烟雾减速系数/下限 |
| speed | `dose_ref` | 6.0 | 剂量减速参考 |
| danger | `gamma` / `beta` / `low_speed_ratio` | 0.5 / 0.1 / 0.2 | 张琦 2025 原文参数 |
| danger | `window_steps` | 10 | 实时速度滚动窗口 |
| danger | `smoke_gain` / `risk_ref` | 0.5 / 1.0 | 烟雾附加压力（0=纯原文） |
| danger | `threshold` | 0.5 | 归一化危险压力阈值 |
| danger | `lateral_bonus` / `priority_gain` | 2.0 / 0.5 | 横向绕道/抢格加成 |
| exit | `alpha` | 0.45 | 出口区域密度权重 α（原文 0.45） |
| exit | `sigma` / `retention` | 0.002 / 0.90 | 选择模糊度 σ / 出口保持率 Pm（原文） |
| exit | `region_radius_m` | 3.2 | 出口区域半径 R（m，原文 3.2） |
| exit | `capacity_mode` / `capacity_scale` | half_disc / 1.0 | 容量按半圆或矩形面积换算 |
| exit | `distance_mode` | bfs | bfs=可绕墙；euclidean=原文欧氏距离 |
| exit | `reevaluate_interval` / `switch_cooldown` | 1 / 0 | 每步判断；原文无冷却 |
| exit | `smoke_weight` / `smoke_ref` | 0.15 / 1.0 | 出口烟雾惩罚（项目扩展，0=原文） |
| exit | `panic_noise` | 0.0 | 恐慌判断噪声（项目扩展） |
| exit | `exit_pressure_gain` | 0.0 | 危险感知削弱 Pm（项目扩展） |
| conflict | `w_speed` / `w_panic` / `speed_ref` | 1.0 / 0.6 / 1.2 | 抢格权重与速度参考 |
| conflict | `friction_enabled` / `friction_m` / `friction_vmax` | True / 1.0 / 5.0 | Xiong μ1 摩擦模型（m、v_max） |
| conflict | `speed_exponent_k` | 1.0 | Xiong μ2 的速度指数 k |
| conflict | `tie_noise` | 0.02 | 平票扰动 |

按原文复现（霍非舟 2023 的参数）：

```python
scene.parameters["movement"]["exit_choice"] = {
    "alpha": 0.45,            # 原文
    "sigma": 0.002,           # 0.2×10⁻²
    "retention": 0.9,         # Pm
    "region_radius_m": 3.2,   # R
    "capacity_mode": "half_disc",
    "distance_mode": "euclidean",  # 原文用欧氏距离
    "reevaluate_interval": 1,
    "switch_cooldown": 0,
    "smoke_weight": 0.0,      # 原文没有烟雾项
}
```

默认配置与原文的差异只有两处：`distance_mode="bfs"`（项目地图有墙，需要绕墙静态场）
和 `smoke_weight=0.15`（让烟雾参与出口选择）。想完全复现论文时把这两项改成
`euclidean` / `0.0` 即可。元胞尺寸沿用项目实际的 0.5 m（论文为 0.4 m），
R、α、σ、Pm 均按原文的物理量/数值设置。

按原文复现（白鹏 2026 的情绪感染参数）：

```python
scene.parameters["movement"]["panic"] = {
    "threshold": 0.5,                  # 情绪阈值 α
    "exit_threshold": 0.5,             # 原文同一阈值
    "decay": 0.333,                    # β1=0.10 / 0.3 s
    "susceptibility_mean": 0.5,        # ρ ~ N(0.5, 0.1)
    "susceptibility_sd": 0.1,
    "initial_from_susceptibility": True,  # E(0)=ρ(m)
    "speed_gain": 0.2,                 # 恐慌 1.2 倍
}
```

按原文复现（张琦 2025 的不耐烦参数）：

```python
scene.parameters["movement"]["danger"] = {
    "gamma": 0.5,           # 原文实验取值
    "beta": 0.1,            # 原文实验取值
    "low_speed_ratio": 0.2, # λ
    "window_steps": 10,
    "smoke_gain": 0.0,      # 原文没有烟雾项
    "threshold": 0.5,
}
```

两者的项目扩展都是"烟雾/风险作为附加刺激"：白鹏用烟雾驱动 `target_i`
（默认初始情绪为 0），张琦用 `smoke_gain` 叠加到不耐烦量。设 `smoke_gain=0`、
`initial_from_susceptibility=True` 即回到论文原始设定。

按原文复现（Xiong 2025 的冲突摩擦参数）：

```python
scene.parameters["movement"]["conflict"] = {
    "friction_enabled": True,
    "friction_m": 1.0,       # 论文未给具体值（敏感性见 Ref [30]）
    "friction_vmax": 5.0,    # 论文最大速度 5 m/s
    "speed_exponent_k": 1.0, # 论文未给具体值
}
```

论文正文只给了 μ1、μ2 的形式，m、k 的取值在参考文献 [30] 里做敏感性分析，
所以这里把它们做成可调参数，默认 1.0；想让高速度者更占优就增大 k，
想让冲突更容易"双方都过不去"就减小 m（m→0 时 μ1→1）。

调参建议：

- 想更"恐慌"：降低 `panic.threshold`（如 0.45）或提高 `panic.growth`；
- 想更愿意换出口：降低 `exit_choice.retention`（Pm）或提高 `exit_choice.alpha`；
- 想更"不耐烦"：提高 `danger.beta`（停滞累积）或降低 `danger.threshold`；
- 想更"挤"：降低 `speed.smoke_slow` 或提高 `speed.min_speed_factor` 的影响；
- 想完全回到旧结果：`movement.enabled = False` 走 legacy 分支（仍是 1 格/步、无加速/恐慌/多出口，
  但抢格仍按"速度优先"，不再纯随机），并且 C 桥会恢复自己的旧 `_speed_gate()`。

---

## 5. 兼容字段

网页/日志依赖的字段保持发布（由 B 计算、C 只转发）：

`is_panicked`、`panic_level`、`speed_multiplier`、`speed_model.enabled`、
`blocked_total`、`congested_total`、`same_cell_congested_total`、
`unknown_behavior`、`daily_walk.walk_total`、`daily_walk.frozen_total`，
以及新增的 `speed_model.source = "B.movement"`、`speed_model.switch_total`、
`speed_model.mean_speed / max_speed`。

**D 端还需要补的字段**（本次不改 D，需求见随附的《D 板块待改说明》）：

- 快照人员：`speed_current`、`danger_pressure`、`danger_urgent`、`impatience`；
- 界面展示：`c_runtime.mean_impatience / max_impatience / urgent_count`；
- 参数入口：把网页 payload 的 `movement` 透传给 `scene.parameters["movement"]`。

`engine.movement_stats` 是唯一统计来源：

```python
{
  "enabled", "blocked_total", "congested_total", "same_cell_congested_total",
  "conflict_total", "conflict_lost_total", "switch_total",
  "panic_count", "urgent_count", "mean_panic", "max_panic",
  "mean_impatience", "max_impatience", "mean_speed", "max_speed",
  "walk_total", "frozen_total", "unknown_behavior", "info_state_owner",
}
```

---

## 6. 验证

1. 单元测试（新增）：
   - `experiments/tests/test_movement_rules.py`：恐慌阈值 α=0.5 与 1.2 倍、加速、
     张琦不耐烦（被堵上升/正常行走不上升）、抢格优先级、静止占用者、
     每出口距离场、拥挤触发换出口；
   - `experiments/tests/test_engine_movement_policy.py`：信息归属、walk/freeze、
     门控先于冲突。
2. 对比实验：`python -m experiments.compare_movement_rules`
   （同图同人同种子，输出 `outputs/compare_movement_rules.json`）。
3. 回归：`python -m unittest discover -s experiments/tests -t .` 与
   `python -m unittest discover -s visualization/tests -t .`。
   本次改动的结果是：与改造前基线相同的 4 个既有失败，没有新增失败。
4. 验收测试：`experiments/tests/test_movement_acceptance.py` 覆盖烟雾阈值
   （0.05/0.5/0.65/0.9）、剂量减速、3×3 密集不叠格、窄出口仍能疏散、
   `unknown_behavior=evacuate` 无烟场景。

标定后的量级检查（单步 0.5 s）：

| 场景 | 结果 |
|---|---|
| 烟雾 0.20 持续 10 s | 恐慌值约 0.42，未越过 α=0.5 |
| 烟雾 0.35 持续 10 s | 恐慌值约 0.53，进入恐慌（1.2 倍速） |
| 烟雾 0.90 持续 10 s | 恐慌值约 0.64（高浓度下饱和） |
| 被堵 10 s + 风险 0.2 | 不耐烦压力约 0.73，触发"危险不耐"行为 |

说明：白鹏原文的初始情绪是 `ρ~N(0.5,0.1)`，本项目的默认值是 0（由烟雾驱动），
所以"烟雾 0.2 不恐慌、0.35 以上恐慌"是项目扩展下的阈值表现；设
`initial_from_susceptibility=True` 可回到论文的初始状态。

**数值结果一定会变**（评审文档也指出这是正常且更合理的变化）：

- 门控从"先移动、后回滚"改为"先门控、再移动"，冲突赢家会变；
- 旧 C 桥的恐慌曲线与 B 的阈值模型不是同一套公式；
- 多出口会让部分人改走不同出口。

如果论文需要"基准结果"，建议以网页 runtime 作为唯一基准入口（项目只有一个引擎、
一个运行入口），在同一 seed/地图/人群下记录指标。

---

## 7. 文件清单

新增：

- `simulation/movement_config.py`（参数）
- `simulation/panic_model.py`（恐慌阈值 + 传染）
- `simulation/speed_model.py`（加速 + 多速度）
- `simulation/danger_perception.py`（危险感知）
- `experiments/compare_movement_rules.py`（对比实验）
- `experiments/tests/test_movement_rules.py`
- `experiments/tests/test_engine_movement_policy.py`
- `experiments/tests/test_movement_acceptance.py`
- `docs/movement_rules_research.md`（本文档）

修改：

- `simulation/evac_simulation.py`（统一编排、统计、信息归属、日常行走）
- `simulation/ca_model.py`（目标出口距离场、危险绕道、多格前进）
- `simulation/exit_choice.py`（多出口效用 + σ/Pm）
- `simulation/conflict_solver.py`（速度+恐慌+危险压力、按竞争力顺序占格）
- `simulation/floor_field.py`（每个出口的距离场）
- `core/schema.py`（Person 增加 panic / speed_current / danger_pressure 等字段）
- `experiments/c_runtime_bridge.py`（只镜像 B 的状态，不再重复计算）
- `experiments/tests/test_smoke_web_runtime_integration.py`、
  `experiments/tests/test_c_runtime_bridge.py`（按新语义更新断言）

未改动：`main.py`（个人测试文件）、`experiments/integrated_runner.py`、
`experiments/web_runtime_server.py`、`experiments/guidance_interface.py`、
`visualization/ca_snapshot_adapter.py`——这些 D 端配套改动见随附的
《D 板块待改说明》（未放入项目）。

未改动：

- `main.py`（个人测试文件，不属于平台运行链）
