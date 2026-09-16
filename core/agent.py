"""
core/agent.py
行人Agent数据结构，元胞自动机个体，包含位置、疏散状态、烟雾剂量、死亡标记、行为相关属性
对应任务书schema定义
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class Agent:
    """
    疏散行人个体
    状态说明：
        evacuated=True : 已经成功撤离出口，释放元胞，不再参与仿真
        is_dead=True   : 烟雾致死，原地停留、占用元胞、不执行移动，未撤离
    """
    # 网格坐标
    x: int
    y: int

    # 疏散状态标记
    evacuated: bool = False        # 是否已经成功从出口撤离
    is_dead: bool = False          # ✅烟雾死亡标记，死亡不代表撤离

    # 烟雾风险相关
    dose: float = 0.0              # 烟雾累积暴露剂量 Dose_i

    # 出口选择
    target_exit: Optional[str] = None   # 目标出口id

    # 个体行为参数
    risk_sensitivity: float = 1.0      # 风险敏感度
    familiarity: float = 0.5           # 对环境熟悉度
    herding_tendency: float = 0.5      # 从众倾向

    # 信息传播状态，对接C模块
    # UNKNOWN / ALERTED / CONFIRMED / MISINFORMED / GUIDED
    info_state: str = "UNKNOWN"

    def reset(self, x: int, y: int):
        """重置行人位置与状态，用于仿真reset"""
        self.x = x
        self.y = y
        self.evacuated = False
        self.is_dead = False
        self.dose = 0.0
        self.target_exit = None
        self.info_state = "UNKNOWN"

    def set_evacuated(self):
        """标记行人成功撤离，撤离之后不再占用格子"""
        self.evacuated = True

    def set_dead(self):
        """标记烟雾死亡，原地停留，仍然占用元胞，不会自动evacuated"""
        self.is_dead = True
