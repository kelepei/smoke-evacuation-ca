"""
B07 行人烟雾风险感知模型 risk_perception.py
实现论文行人风险感知计算公式，输出单个人风险值给CA移动逻辑
参数a/b/c为权重系数，可根据实验调整
"""
import numpy as np
from core.schema import Person, Grid

class SmokeRiskPerception:
    def __init__(
        self,
        weight_conc: float = 1.0,    # a 烟雾浓度权重
        weight_delta: float = 0.6,    # b 烟雾浓度变化量权重
        weight_vis: float = 1.2,      # c 能见度损失权重
        vis_base_coeff: float = 0.08  # 能见度换算系数，浓度越高能见度越低
    ):
        self.a = weight_conc
        self.b = weight_delta
        self.c = weight_vis
        self.vis_coeff = vis_base_coeff
        # 缓存上一帧烟雾矩阵，计算ΔS = 当前S - 上一帧S
        self.last_smoke_matrix = None

    def calc_visibility_loss(self, smoke_conc: float) -> float:
        """
        根据烟雾浓度计算能见度损失值VisibilityLoss
        浓度越高，能见度下降越严重，损失值越大
        """
        loss = smoke_conc * self.vis_coeff
        return min(loss, 10.0)  # 上限限制，防止风险爆炸

    def calc_delta_s(self, x: int, y: int, current_smoke: np.ndarray) -> float:
        """ΔS = 当前位置烟雾浓度 - 上一帧同位置浓度"""
        h, w = current_smoke.shape
        if self.last_smoke_matrix is None or not (0 <= x < w and 0 <= y < h):
            return 0.0
        delta = current_smoke[y, x] - self.last_smoke_matrix[y, x]
        return delta

    def get_person_risk(self, person: Person, smoke_matrix: np.ndarray) -> float:
        """
        计算单个行人当前综合风险 Risk_i(t)
        :param person: 行人对象，带x/y坐标
        :param smoke_matrix: 当前全局烟雾浓度场
        :return: 行人感知总风险值
        """
        px = int(person.x)
        py = int(person.y)
        h, w = smoke_matrix.shape

        # 坐标越界直接风险为0
        if not (0 <= px < w and 0 <= py < h):
            return 0.0

        # 1. 当前位置烟雾浓度 S(xi,yi,t)
        s_now = smoke_matrix[py, px]
        # 2. 浓度变化量 ΔS
        delta_s = self.calc_delta_s(px, py, smoke_matrix)
        # 3. 能见度损失
        vis_loss = self.calc_visibility_loss(s_now)

        # 论文标准风险公式
        total_risk = self.a * s_now + self.b * delta_s + self.c * vis_loss

        # 更新缓存上一帧烟雾场，供下一帧计算ΔS
        self.last_smoke_matrix = smoke_matrix.copy()
        return round(total_risk, 4)

    def batch_calc_all_risk(self, person_list: list[Person], smoke_matrix: np.ndarray) -> dict[int, float]:
        """批量计算所有行人风险，返回 {person_id: 风险值}"""
        risk_map = {}
        for p in person_list:
            risk_map[p.id] = self.get_person_risk(p, smoke_matrix)
        return risk_map