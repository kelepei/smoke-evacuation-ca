""" B07 行人烟雾风险感知模型 risk_perception.py
实现论文行人风险感知计算公式，输出单个人风险值给CA移动逻辑
参数a/b/c为权重系数，可根据实验调整.
新增：烟雾暴露剂量累加、烟雾致死判定 B08
"""
import numpy as np
from core.schema import Person, Grid


class SmokeRiskPerception:
    def __init__(
        self,
        weight_conc: float = 1.0,    # a 烟雾浓度权重
        weight_delta: float = 0.6,    # b 烟雾浓度变化量权重
        weight_vis: float = 1.2,      # c 能见度损失权重
        vis_base_coeff: float = 0.08,  # 能见度换算系数，浓度越高能见度越低
        death_dose_threshold: float = 12.0  # ✅新增：烟雾致死剂量阈值(无量纲)
    ):
        self.a = weight_conc
        self.b = weight_delta
        self.c = weight_vis
        self.vis_coeff = vis_base_coeff
        self.death_dose_threshold = death_dose_threshold

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

    def _update_dose_and_death(self, person: Person, smoke_matrix: np.ndarray, time_step_s: float):
        """✅新增：更新行人烟雾累积剂量，判断是否烟雾致死
        要求Person对象具备属性：
            person.dose: float       累积暴露剂量
            person.is_dead: bool     是否死亡
            person.evacuated: bool   是否已经撤离
        """
        # 已经撤离或者已经死亡，不再计算剂量
        if person.evacuated or person.is_dead:
            return

        px = int(person.x)
        py = int(person.y)
        h, w = smoke_matrix.shape
        if not (0 <= px < w and 0 <= py < h):
            return

        s_now = smoke_matrix[py, px]
        # Dose += S * Δt
        person.dose += s_now * time_step_s

        # 剂量超过阈值标记死亡，死亡不修改evacuated
        if person.dose >= self.death_dose_threshold:
            person.is_dead = True

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

        # 已经死亡/撤离，风险返回0，不再参与风险驱动移动
        if person.evacuated or person.is_dead:
            return 0.0

        # 1. 当前位置烟雾浓度 S(xi,yi,t)
        s_now = smoke_matrix[py, px]
        # 2. 浓度变化量 ΔS
        delta_s = self.calc_delta_s(px, py, smoke_matrix)
        # 3. 能见度损失
        vis_loss = self.calc_visibility_loss(s_now)

        # 论文标准风险公式
        total_risk = self.a * s_now + self.b * delta_s + self.c * vis_loss

        return round(total_risk, 4)

    def batch_calc_all_risk(self, person_list: list[Person], smoke_matrix: np.ndarray, time_step_s: float) -> dict[int, float]:
        """
        批量计算所有行人风险 + 更新烟雾暴露剂量 + 判定死亡
        :param person_list: 全部行人列表
        :param smoke_matrix: 当前烟雾矩阵
        :param time_step_s: 单步仿真时间 Δt
        :return: {person_id: 风险值}
        """
        risk_map = {}
        for p in person_list:
            # ✅每一步先更新剂量、做死亡判定
            self._update_dose_and_death(p, smoke_matrix, time_step_s)
            # 再计算风险
            risk_map[p.id] = self.get_person_risk(p, smoke_matrix)

        # ✅【修复】整帧所有人全部计算完成后，才更新上一帧烟雾缓存
        self.last_smoke_matrix = smoke_matrix.copy()
        return risk_map
