""" B06 烟雾扩散模型 smoke_model.py
替换为二维高斯烟云模型；保留原有对外API不变，兼容上层风险感知、可视化、警报模块
每个时间步：各烟源生成高斯分布 → 叠加多源 → 墙体位置置零 → 全局衰减
注意：简化工程实现；真正做到墙体阻断烟云传播（近似）
"""
import numpy as np
from core.schema import Grid, CellType, SmokeSource


class SmokeDiffusionModel:
    def __init__(
        self,
        grid: Grid,
        diffuse_coeff: float = 1.0,      # 高斯初始sigma σ0
        decay_coeff: float = 0.03,      # δ 全局消散系数
        wall_block_factor: float = 0.0,  # 本模型不再使用，保留参数兼容旧配置
        sigma_grow_rate: float = 0.07    # σ每一步增长量，控制烟云扩张速度
    ):
        self.grid = grid
        self.height = grid.height
        self.width = grid.width
        self.sigma0 = diffuse_coeff
        self.sigma_grow = sigma_grow_rate
        self.delta = decay_coeff
        self.wall_block = wall_block_factor

        self.smoke_matrix = np.zeros((self.height, self.width), dtype=np.float32)
        self.smoke_sources: list[SmokeSource] = []
        self.time_step_count = 0  # 记录仿真步数，用于扩大高斯σ

    def add_smoke_source(self, source: SmokeSource):
        """添加烟源对象【接口不变】"""
        self.smoke_sources.append(source)

    def _get_source_intensity(self, x: int, y: int) -> float:
        """获取当前格子烟源释放强度 Q(x,y)【接口不变】"""
        q = 0.0
        for src in self.smoke_sources:
            if src.x == x and src.y == y:
                q += src.intensity
        return q

    def update_smoke(self):
        """
        单步更新烟雾场，替换为高斯烟云模型
        S(x,y,t) = Σ 各烟源二维高斯分布叠加；墙体直接置0；全局衰减
        σ(t) = σ0 + sigma_grow * t
        """
        next_smoke = np.zeros((self.height, self.width), dtype=np.float32)
        t = self.time_step_count
        sigma_t = self.sigma0 + self.sigma_grow * t

        # 限制sigma最大上限，防止sigma过大幅值过低
        sigma_t = min(sigma_t, max(self.width, self.height) * 0.4)

        # 1：上一步残留烟雾先衰减
        next_smoke[:] = self.smoke_matrix * (1.0 - self.delta)

        # 2：每个烟源，生成高斯烟云，叠加到本步
        for src in self.smoke_sources:
            x0, y0 = src.x, src.y
            Q = src.intensity
            if Q <= 1e-6:
                continue

            yy, xx = np.mgrid[0:self.height, 0:self.width]
            # 二维高斯分布
            gauss = Q / (2 * np.pi * sigma_t ** 2) * np.exp(
                -((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma_t ** 2)
            )
            next_smoke += gauss

        # 3.墙体阻挡：墙体元胞强制置0
        for y in range(self.height):
            for x in range(self.width):
                cell = self.grid.get_cell(x, y)
                if cell is None or cell.cell_type == CellType.WALL:
                    next_smoke[y, x] = 0.0

        # 浓度钳位 0~1，和旧模型取值对齐
        next_smoke = np.clip(next_smoke, 0.0, 1.0)

        self.smoke_matrix = next_smoke
        self.time_step_count += 1

    def get_cell_smoke(self, x: int, y: int) -> float:
        """获取单个网格烟雾浓度，供风险感知、警报逻辑调用【接口完全不变】"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return float(self.smoke_matrix[y, x])
        return 0.0

    def get_max_smoke(self) -> float:
        """新增工具：获取全场最大烟雾浓度，用于外部警报判断"""
        return float(np.max(self.smoke_matrix))
