"""
B08 烟雾暴露剂量统计模块 risk_metrics.py
累计每个行人全程烟雾吸入剂量Dose，写入CSV日志
"""
from core.schema import Person
import numpy as np

class SmokeDoseRecorder:
    def __init__(self, delta_t: float = 0.5):
        self.dt = delta_t  # 单步仿真时长Δt
        # 存储每个人累计剂量 key:person_id, value:总剂量
        self.person_dose: dict[int, float] = {}

    def init_person_dose(self, person_list: list[Person]):
        """仿真初始化，给所有行人初始化剂量0"""
        for p in person_list:
            if p.id not in self.person_dose:
                self.person_dose[p.id] = 0.0

    def update_all_dose(self, person_list: list[Person], smoke_matrix: np.ndarray):
        """单步累加所有人烟雾暴露剂量"""
        h, w = smoke_matrix.shape
        for p in person_list:
            px, py = int(p.x), int(p.y)
            if 0 <= px < w and 0 <= py < h:
                s_val = smoke_matrix[py, px]
                # Dose += S * Δt
                self.person_dose[p.id] += s_val * self.dt
            # 把剂量写入行人对象，用于日志导出
            p.dose = self.person_dose[p.id]

    def get_person_total_dose(self, pid: int) -> float:
        """获取指定行人总暴露剂量"""
        return self.person_dose.get(pid, 0.0)