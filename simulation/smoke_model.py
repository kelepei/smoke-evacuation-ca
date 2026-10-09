""" B06 烟雾扩散模型 smoke_model.py

方案四：高斯烟云作源项 + 有限差分扩散方程
    S_{t+1} = S_t + dt·(α∇²S_t + Q − δS_t)

- 高斯烟云：提供每步源项 Q(x,y)
- 扩散方程：让烟雾从门口渗透到相邻区域
- 墙体：零通量边界（Neumann）+ 强制置零，防止穿墙

对外 API 完全不变，兼容上层风险感知、可视化、警报模块。
不依赖 Grid.get_cell，改用内部坐标索引字典。
"""

import numpy as np
from core.schema import Grid, CellType, SmokeSource


class SmokeDiffusionModel:
    def __init__(
        self,
        grid: Grid,
        diffuse_coeff: float = 1.0,       # 高斯初始 sigma σ0
        decay_coeff: float = 0.03,        # δ 全局消散系数
        wall_block_factor: float = 0.0,   # 保留参数兼容旧配置
        sigma_grow_rate: float = 0.07,    # σ 每步增长量
        # ---- 新增扩散方程参数 ----
        alpha: float = 0.1,               # α 扩散系数，建议 ≤ 0.1 满足 CFL
        dt: float = 0.5,                  # 时间步长，与 CA 一致
        normalize: bool = True,           # 是否归一化到 [0,1]
    ):
        self.grid = grid
        self.height = grid.height
        self.width = grid.width
        self.cell_size = getattr(grid, "cell_size", 0.5)

        self.sigma0 = diffuse_coeff
        self.sigma_grow = sigma_grow_rate
        self.delta = decay_coeff
        self.wall_block = wall_block_factor

        # 扩散方程参数
        self.alpha = alpha
        self.dt = dt
        self.normalize = normalize

        self.smoke_matrix = np.zeros((self.height, self.width), dtype=np.float32)
        self.smoke_sources: list[SmokeSource] = []
        self.time_step_count = 0

        # 坐标 → Cell 索引字典（不依赖 Grid.get_cell）
        self.cell_index = {(c.x, c.y): c for c in grid.cells}

        # 预计算可通行掩膜与墙体掩膜
        self.passable = self._build_passable_mask()
        self.wall_mask = ~self.passable

    # ---------- 掩膜 ----------

    def _build_passable_mask(self) -> np.ndarray:
        """
        从 Grid.cells 构建 (H,W) 可通行布尔矩阵。
        WALL / OBSTACLE 不可通行，其余（FREE/EXIT/SIGN/GUIDE_ZONE/SMOKE_SOURCE）可通行。
        未知元胞（cells 里没有的坐标）按不可通行处理，防止越界。
        """
        mask = np.zeros((self.height, self.width), dtype=bool)
        for (x, y), cell in self.cell_index.items():
            if not (0 <= x < self.width and 0 <= y < self.height):
                continue
            if cell.cell_type not in (CellType.WALL, CellType.OBSTACLE):
                mask[y, x] = True
        return mask

    # ---------- 对外接口（不变） ----------

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

    # ---------- 高斯源项 ----------

    def _gaussian_source_term(self, sigma_t: float) -> np.ndarray:
        """多源高斯叠加，作为扩散方程的源项 Q(x,y)"""
        Q = np.zeros((self.height, self.width), dtype=np.float32)
        if not self.smoke_sources:
            return Q
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        for src in self.smoke_sources:
            if src.intensity <= 1e-6:
                continue
            x0, y0 = src.x, src.y
            gauss = src.intensity / (2 * np.pi * sigma_t ** 2) * np.exp(
                -((xx - x0) ** 2 + (yy - y0) ** 2) / (2 * sigma_t ** 2)
            )
            Q += gauss.astype(np.float32)
        return Q

    # ---------- 拉普拉斯 ----------

    def _laplacian(self, S: np.ndarray) -> np.ndarray:
        """
        ∇²S 有限差分，仅可通行区域。
        墙体处零通量（Neumann）：墙体元胞拉普拉斯置 0。
        """
        S_pad = np.pad(S, 1, mode='edge')
        dx = self.cell_size
        lap = (S_pad[2:, 1:-1] + S_pad[:-2, 1:-1] +
               S_pad[1:-1, 2:] + S_pad[1:-1, :-2] - 4 * S) / (dx * dx)
        lap[self.wall_mask] = 0.0
        return lap.astype(np.float32)

    # ---------- 主更新 ----------

    def update_smoke(self):
        """
        单步更新：
            S_{t+1} = S_t + dt·(α∇²S_t + Q − δS_t)
        然后墙体置零、归一化。
        """
        t = self.time_step_count
        sigma_t = self.sigma0 + self.sigma_grow * t
        sigma_t = min(sigma_t, max(self.width, self.height) * 0.4)

        # 1. 高斯源项
        Q = self._gaussian_source_term(sigma_t)

        # 2. 扩散
        lap = self._laplacian(self.smoke_matrix)

        # 3. 扩散方程更新
        S = self.smoke_matrix + self.dt * (
            self.alpha * lap + Q - self.delta * self.smoke_matrix
        )
        np.clip(S, 0.0, None, out=S)

        # 4. 墙体强制置零（防穿墙）
        S[self.wall_mask] = 0.0

        # 5. 归一化到 [0,1]，与旧模型取值对齐
        if self.normalize:
            smax = float(S.max())
            if smax > 1e-12:
                S = S / smax
        S = np.clip(S, 0.0, 1.0)

        self.smoke_matrix = S.astype(np.float32)
        self.time_step_count += 1

    # ---------- 查询接口（不变） ----------

    def get_cell_smoke(self, x: int, y: int) -> float:
        """获取单个网格烟雾浓度【接口完全不变】"""
        if 0 <= x < self.width and 0 <= y < self.height:
            return float(self.smoke_matrix[y, x])
        return 0.0

    def get_max_smoke(self) -> float:
        """获取全场最大烟雾浓度【接口不变】"""
        return float(np.max(self.smoke_matrix))