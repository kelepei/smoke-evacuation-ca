"""
B06 烟雾扩散模型 smoke_model.py
实现简化烟雾扩散偏微分方程、墙体遮挡、烟源生成、烟雾场更新
对应论文公式 S_{t+1}(x,y) = S_t(x,y) + α∇²S_t(x,y) + Q(x,y) − δS_t(x,y).
"""
import numpy as np
from core.schema import Grid, CellType, SmokeSource


class SmokeDiffusionModel:
    def __init__(
        self,
        grid: Grid,
        diffuse_coeff: float = 0.25,   # α 扩散系数
        decay_coeff: float = 0.03,     # δ 消散系数
        wall_block_factor: float = 0.0 # 墙体阻挡烟雾扩散
    ):
        self.grid = grid
        self.height = grid.height
        self.width = grid.width
        self.alpha = diffuse_coeff
        self.delta = decay_coeff
        self.wall_block = wall_block_factor

        # 初始化烟雾浓度矩阵 S_t
        self.smoke_matrix = np.zeros((self.height, self.width), dtype=np.float32)
        # 存储所有烟源
        self.smoke_sources: list[SmokeSource] = []

    def add_smoke_source(self, source: SmokeSource):
        """添加烟源对象"""
        self.smoke_sources.append(source)

    def _get_source_intensity(self, x: int, y: int) -> float:
        """获取当前格子烟源释放强度 Q(x,y)"""
        q = 0.0
        for src in self.smoke_sources:
            if src.x == x and src.y == y:
                q += src.intensity
        return q

    def laplacian_2d(self, x: int, y: int, smoke: np.ndarray) -> float:
        """
        计算二维拉普拉斯算子 ∇²S，5邻域差分
        ∇²S = S(x+1,y)+S(x-1,y)+S(x,y+1)+S(x,y-1) - 4*S(x,y)
        墙体/空格子直接返回0，阻断扩散
        """
        cell = self.grid.get_cell(x, y)
        # 增加空值保护，防止None.cell_type报错
        if cell is None or cell.cell_type == CellType.WALL:
            return 0.0

        total = 0.0
        center_val = smoke[y, x]
        # 上下左右四邻域
        neighbors = [(x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)]
        for nx, ny in neighbors:
            # 边界判断
            if 0 <= nx < self.width and 0 <= ny < self.height:
                n_cell = self.grid.get_cell(nx, ny)
                if n_cell is not None and n_cell.cell_type != CellType.WALL:
                    total += smoke[ny, nx] * (1 - self.wall_block)
        return total - 4 * center_val

    def update_smoke(self):
        """
        单步更新烟雾场，核心公式实现
        S_{t+1} = S_t + α*∇²S + Q − δ*S_t
        """
        # 新建下一时刻烟雾矩阵，防止原地覆盖
        next_smoke = np.zeros_like(self.smoke_matrix)

        for y in range(self.height):
            for x in range(self.width):
                cell = self.grid.get_cell(x, y)
                # 空单元格 / 墙体，烟雾置0
                if cell is None or cell.cell_type == CellType.WALL:
                    next_smoke[y, x] = 0.0
                    continue

                s_t = self.smoke_matrix[y, x]
                lap = self.laplacian_2d(x, y, self.smoke_matrix)
                q = self._get_source_intensity(x, y)

                # 论文标准公式
                s_next = s_t + self.alpha * lap + q - self.delta * s_t
                # 浓度下限不能为负数
                next_smoke[y, x] = max(0.0, s_next)

        # 更新全局烟雾矩阵
        self.smoke_matrix = next_smoke

    def get_cell_smoke(self, x: int, y: int) -> float:
        """获取单个网格烟雾浓度，供风险感知模块调用"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.smoke_matrix[y, x]
        return 0.0