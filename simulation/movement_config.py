"""移动规则参数配置（文献调研版）

把"恐慌阈值 + 多出口 + 加速 + 危险感知（替代不耐烦）"四类规则的参数集中在这里，
全部有默认值，可在 ScenarioConfig.parameters["movement"] 中按需覆盖：

    scene.parameters["movement"] = {
        "panic": {"threshold": 0.5, "speed_gain": 0.2},
        "speed": {"accel": 1.5, "max_cells_per_step": 2},
        "exit_choice": {"retention": 0.3, "sigma": 0.1},
        "danger": {"threshold": 0.6},
        "conflict": {"w_speed": 1.0, "w_panic": 0.6},
    }

单位约定：
- 长度：米（cell_size 默认 0.5 m）
- 时间：秒（time_step_s 默认 0.5 s）
- 速度：m/s；加速度：m/s^2
- 恐慌值/危险感知：0~1 无量纲
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, Mapping


@dataclass
class PanicConfig:
    """恐慌阈值 + 情绪感染（白鹏 2026）

    原文：SIS 情绪感染，情绪阈值 α=0.5；初始情绪 E(0)=ρ(m)，ρ~N(0.5,0.1)；
          情绪自衰减系数 β1=0.10（原文步长 0.3 s，即约 0.333/s）；
          恐慌者速度为非恐慌者的 1.2 倍。
    本项目把烟雾/风险作为外部刺激源，加速情绪上升。
    """

    enabled: bool = True
    # 烟雾浓度参考值：S / smoke_ref 归一化到 0~1
    smoke_ref: float = 0.75
    # 烟雾浓度变化量权重（ΔS，突变烟雾更容易引发恐慌）
    delta_weight: float = 0.8
    # 风险感知值权重（risk_perception 输出的综合风险）
    risk_weight: float = 0.6
    risk_ref: float = 1.0
    # 恐慌增长/衰减速率（1/s）。原文 β1=0.10/0.3s ≈ 0.333/s
    growth: float = 1.2
    decay: float = 0.333
    # 恐慌传染强度：周围已恐慌邻居比例对未恐慌者的影响
    contagion: float = 0.30
    contagion_radius: int = 1
    # 人格特质 ρ(m)：原文 ρ~N(0.5, 0.1)，用于缩放情绪增长速度
    susceptibility_mean: float = 0.5
    susceptibility_sd: float = 0.1
    # 原文 E(0)=ρ(m)：是否用 ρ 作为初始情绪值（项目默认从 0 开始，由烟雾驱动）
    initial_from_susceptibility: bool = False
    # 恐慌阈值 α（原文 0.5；感染者低于 α 即恢复，无滞回）
    threshold: float = 0.50
    exit_threshold: float = 0.50
    # 恐慌后速度增益：1.2 倍 ⇒ 0.2
    speed_gain: float = 0.20
    # 烟源本身的危险感知：在烟雾浓度之外，按到烟源的距离额外增加暴露
    source_weight: float = 1.0
    # ---- 关系加权情绪传染（任务书 5.3 / 项目设计） ----
    relation_contagion: bool = True
    relation_floor: float = 0.30
    relation_strength_weight: float = 0.50
    relation_trust_weight: float = 0.50
    relation_distance_decay: bool = False
    relation_sigma_cells: float = 1.0
    relation_type_weights: dict = field(default_factory=lambda: {
        "family": 1.00,
        "friend": 0.80,
        "classmate": 0.65,
        "colleague": 0.60,
        "doctor_patient": 0.50,
        "staff_to_customer": 0.35,
        "stranger": 0.20,
    })


@dataclass
class SpeedConfig:
    """渐进加速 / 多速度（Xiong 2025；高瑾 2021）"""

    enabled: bool = True
    # 加速度模型：xiong2025=按 Xiong 2025 实测的分速度区间高斯分布；
    # constant=所有人/所有速度用固定 accel（兼容旧配置）
    accel_model: str = "xiong2025"
    # constant 模式下的加速度（m/s^2）与个体差异
    accel: float = 2.0
    accel_sd: float = 0.6
    accel_min: float = 0.5
    accel_max: float = 5.5
    decel: float = 2.5
    # Xiong 2025 Rule 2：到前方慢行者/障碍物的距离小于相对减速距离 L(Δv) 时提前减速
    use_deceleration_distance: bool = True
    # 向前探测的最远距离（m），需覆盖 L(v) 的最大值 10 m
    lookahead_m: float = 10.0
    # 初始速度比例：行人进入仿真时已在行走，默认取 0.8 倍最大速度
    initial_velocity_ratio: float = 0.8
    # 初始"距离亏欠"：相当于行人已经走到半步，避免第一步全部原地不动
    initial_credit: float = 0.5
    # 单步最多前进的元胞数（元胞 0.5 m、步长 0.5 s 时，2 格对应 2 m/s）
    max_cells_per_step: int = 2
    # 烟雾对速度的削弱系数：v *= max(min_factor, 1 - smoke_slow * S)
    smoke_slow: float = 0.55
    min_speed_factor: float = 0.30
    # 剂量对速度的削弱：v *= max(0.4, 1 / (1 + dose / dose_ref))
    dose_ref: float = 6.0
    # 速度上下限（m/s）
    min_speed: float = 0.2
    max_speed: float = 2.4
    # 受阻后是否清空速度（重新起步需要加速过程）
    stop_on_block: bool = True


@dataclass
class DangerConfig:
    """危险感知（替代"不耐烦"）：按张琦 2025 的不耐烦公式实现

    原文 imp_i(t) = (1 - v_i/v_i^e) · exp(-γ(v_i(t)-v_i(t-1))) · exp(β·t_c)
      - v_i(t)：最近 10 个时间步的实际速度（位移/时间）
      - t_c：低速或停滞持续时间（v < λ·v^e，λ=0.2）
      - γ=0.5，β=0.1（原文实验取值）
    本项目把烟雾/风险作为附加压力源，并归一化为 0~1 的 danger_pressure。
    """

    enabled: bool = True
    # ---- 张琦 2025 原文参数 ----
    gamma: float = 0.5          # 对速度变化趋势的敏感性
    beta: float = 0.1           # 对低速/停滞累积时间的敏感性
    low_speed_ratio: float = 0.2  # λ：低于 λ·v^e 视为低速/停滞
    window_steps: int = 10      # 实时速度的滚动窗口
    # ---- 项目扩展：烟雾/风险附加压力 ----
    smoke_gain: float = 0.5     # 每小时风险归一化值附加的不耐烦量；0=纯原文
    source_weight: float = 1.2  # 烟源邻近的附加压力（0=不启用）
    risk_ref: float = 1.0
    # 归一化后超过该阈值触发行为（横向绕道、抢格优先、换出口）
    threshold: float = 0.5
    # 达到阈值后的行为强度
    lateral_bonus: float = 2.0      # 模式1：横向换道/绕行
    priority_gain: float = 0.50     # 模式2：抢占优先权


@dataclass
class ExitChoiceConfig:
    """多出口动态选择（霍非舟 2023，按原文公式与参数）

    原文：P1k = softmax(-d_k / Σd)，P2k = softmax(-N_k/C_k)，
          E_k = (1-α)P1k + αP2k，
          当 ΔE = E_t(μ) - E_{t-1}(μ) ≥ σ 时，以 Pm 概率保持原出口、以 1-Pm 概率换到 μ。
    """

    enabled: bool = True
    # ---- 原文参数（霍非舟 2023） ----
    alpha: float = 0.45          # 出口区域密度权重 α（原文最佳范围 0.3~0.6）
    sigma: float = 0.002         # 选择模糊度 σ（原文 0.2×10⁻²）
    retention: float = 0.90      # 出口保持率 Pm（原文 σ=0.2×10⁻² 时最佳 0.9）
    region_radius_m: float = 3.2  # 出口区域半径 R（原文 3.2 m）
    capacity_mode: str = "half_disc"  # half_disc=半圆区域；rect=R×2R 矩形
    capacity_scale: float = 1.0  # 容量折算系数
    distance_mode: str = "bfs"   # bfs=可绕墙的静态场；euclidean=原文欧氏距离
    # 原文每个时间步都比较一次；项目默认每 3 步重算，减少出口抖动
    reevaluate_interval: int = 3
    # 原文没有冷却；项目默认 5 步冷却，减少来回切换
    switch_cooldown: int = 5
    # 距当前目标出口 commit_radius_cells 格以内不再换出口（防到门口又掉头）
    commit_radius_cells: float = 3.0
    # ---- 项目扩展（默认不改变原文行为） ----
    smoke_weight: float = 0.15   # 出口区域烟雾权重；0=完全按原文（原文无烟雾）
    smoke_ref: float = 1.0
    panic_noise: float = 0.0     # 恐慌判断噪声；0=关闭
    exit_pressure_gain: float = 0.0  # 危险感知削弱 Pm 的强度；0=固定 Pm


@dataclass
class ConflictConfig:
    """抢格规则：Xiong 2025 摩擦模型 + 速度/恐慌/危险压力加成"""

    w_speed: float = 1.0
    w_panic: float = 0.60
    # 速度归一化参考（m/s）
    speed_ref: float = 1.2
    # 平票随机扰动
    tie_noise: float = 0.02
    # ---- Xiong 2025 冲突摩擦模型 ----
    friction_enabled: bool = True
    friction_m: float = 1.0       # μ1 = (⟨v⟩/v_max)^m，m 越大越合作
    friction_vmax: float = 5.0    # 原文最大速度 5 m/s
    speed_exponent_k: float = 1.0  # μ2 ∝ v^k，k 越大速度优势越强


@dataclass
class MovementConfig:
    enabled: bool = True
    # 未获知险情人员的行为：walk=正常速度随机行走 / freeze=原地 / evacuate=直接疏散
    unknown_behavior: str = "walk"
    unknown_speed_factor: float = 0.35
    # 烟源危险感知的影响半径（米）：到烟源距离按 exp(-d^2/(2σ^2)) 衰减
    source_sigma_m: float = 1.5
    # 烟雾浓度超过该值时，该候选格直接不可进入（硬绕行）
    smoke_avoid_threshold: float = 0.8
    panic: PanicConfig = field(default_factory=PanicConfig)
    speed: SpeedConfig = field(default_factory=SpeedConfig)
    danger: DangerConfig = field(default_factory=DangerConfig)
    exit_choice: ExitChoiceConfig = field(default_factory=ExitChoiceConfig)
    conflict: ConflictConfig = field(default_factory=ConflictConfig)

    @classmethod
    def from_scene(cls, scene: Any) -> "MovementConfig":
        """从 scene.parameters["movement"] 读取覆盖项，缺省用默认值。"""

        raw: Mapping[str, Any] = {}
        parameters = getattr(scene, "parameters", None)
        if isinstance(parameters, Mapping):
            candidate = parameters.get("movement")
            if isinstance(candidate, Mapping):
                raw = candidate
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "MovementConfig":
        config = cls()
        if not isinstance(raw, Mapping):
            return config
        if "enabled" in raw:
            config.enabled = bool(raw["enabled"])
        if raw.get("unknown_behavior") is not None:
            value = str(raw["unknown_behavior"]).strip().lower()
            if value in ("walk", "freeze", "evacuate"):
                config.unknown_behavior = value
        if raw.get("unknown_speed_factor") is not None:
            try:
                config.unknown_speed_factor = max(0.0, float(raw["unknown_speed_factor"]))
            except (TypeError, ValueError):
                pass
        for section_name in ("panic", "speed", "danger", "exit_choice", "conflict"):
            section = raw.get(section_name)
            if not isinstance(section, Mapping):
                continue
            target = getattr(config, section_name)
            valid = {item.name for item in fields(target)}
            for key, value in section.items():
                if key in valid and value is not None:
                    try:
                        setattr(target, key, type(getattr(target, key))(value))
                    except (TypeError, ValueError):
                        # 配置写错时保留默认值，避免整场仿真起不来
                        continue
        return config

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "unknown_behavior": self.unknown_behavior,
            "unknown_speed_factor": self.unknown_speed_factor,
            "panic": dict(self.panic.__dict__),
            "speed": dict(self.speed.__dict__),
            "danger": dict(self.danger.__dict__),
            "exit_choice": dict(self.exit_choice.__dict__),
            "conflict": dict(self.conflict.__dict__),
        }
