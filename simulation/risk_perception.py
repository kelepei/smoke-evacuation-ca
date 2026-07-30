class RiskPerception:
    """风险感知计算，预留扩展，当前直接使用烟雾浓度作为风险"""
    def calc_risk(self, smoke_value: float) -> float:
        return smoke_value