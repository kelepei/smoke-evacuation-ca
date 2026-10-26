""" B06 烟雾扩散模型 smoke_model.py

方案四：高斯烟羽作源项 + 有限差分扩散方程
    S_{t+1} = S_t + dt·(α∇²S_t + Q − δS_t)

- 高斯烟羽：每个烟源按“可通行路径”上的距离衰减，提供每步源项 Q(x,y)
- 扩散方程：烟雾只沿可通行元胞扩散，可以从门洞绕行，不能穿墙
- 墙体：严格零通量边界（Neumann）+ 墙面强制置零

墙体严格阻断（本次改动）：
1. 源项 Q 使用测地距离（4 邻接最短路，绕墙计算）。墙体另一侧不可达，
   距离为 +inf，Q 恒为 0；因此即使烟源紧贴墙壁，墙后也不会凭空出现烟雾。
2. 拉普拉斯只耦合可通行邻居。墙 / 障碍 / 网格边界的邻居按“自身值”处理，
   即墙面法向梯度为 0（零通量）：烟雾不会被墙体吸收，也不会越过墙体。
3. 每步更新后再次把墙体 / 障碍物置零，保证 get_cell_smoke(墙) 恒为 0。

对外 API 完全不变，兼容上层风险感知、可视化、警报模块。
不依赖 Grid.get_cell，改用内部坐标索引字典。
"""

from collections import deque

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
        # ---- 扩散方程参数 ----
        alpha: float = 0.1,               # α 扩散系数，建议 ≤ 0.1 满足 CFL
        dt: float = 0.5,                  # 时间步长，与 CA 一致
        normalize: bool = True,           # 是否归一化到 [0,1]
    ):
        self.grid = grid
        self.height = grid.height
        self.width = grid.width
        self.cell_size = max(1e-9, float(getattr(grid, "cell_size", 0.5) or 0.5))

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

        # 烟源 → 测地距离场缓存（墙体静态，距离场只需算一次）
        self._geodesic_cache: dict[tuple[int, int], np.ndarray | None] = {}

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

    # ---------- 测地距离（绕墙最短可通行路径） ----------

    def _nearest_passable(self, x: int, y: int) -> tuple[int, int] | None:
        """烟源落在墙里时，就近落到一个可通行元胞；没有可通行元胞则返回 None。"""
        if 0 <= x < self.width and 0 <= y < self.height and self.passable[y, x]:
            return x, y
        ys, xs = np.nonzero(self.passable)
        if xs.size == 0:
            return None
        # 平方距离最小；np.argmin 对并列情况按行优先取第一个，结果稳定
        idx = int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))
        return int(xs[idx]), int(ys[idx])

    def _source_geodesic_distance(self, src: SmokeSource) -> np.ndarray | None:
        """
        从烟源出发，只经过可通行元胞做 4 邻接 BFS，返回 (H,W) 距离场。
        墙体 / 障碍物 / 无法到达的区域为 +inf；烟源本身距离为 0。
        距离单位为“元胞”，与 σ 的单位保持一致。
        """
        key = (int(src.x), int(src.y))
        if key in self._geodesic_cache:
            return self._geodesic_cache[key]

        start = self._nearest_passable(*key)
        if start is None:
            self._geodesic_cache[key] = None
            return None

        dist = np.full((self.height, self.width), np.inf, dtype=np.float32)
        sx, sy = start
        dist[sy, sx] = 0.0
        queue = deque([(sx, sy)])
        passable = self.passable

        while queue:
            cx, cy = queue.popleft()
            nd = float(dist[cy, cx]) + 1.0
            for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                if not (0 <= nx < self.width and 0 <= ny < self.height):
                    continue
                if not passable[ny, nx] or np.isfinite(dist[ny, nx]):
                    continue
                dist[ny, nx] = nd
                queue.append((nx, ny))

        self._geodesic_cache[key] = dist
        return dist

    # ---------- 高斯源项 ----------

    def _gaussian_source_term(self, sigma_t: float) -> np.ndarray:
        """
        多源高斯叠加，作为扩散方程的源项 Q(x,y)。

        与普通高斯的唯一区别：指数里的距离用“绕墙测地距离”而不是欧氏距离。
        开放区域两者一致；有墙时烟雾只能沿可通行路径绕行，
        墙后不可达元胞的距离为 +inf，exp(-inf)=0，因此不会穿墙。
        """
        Q = np.zeros((self.height, self.width), dtype=np.float32)
        if not self.smoke_sources:
            return Q

        sigma_t = max(float(sigma_t), 1e-6)
        inv_two_sigma_sq = 1.0 / (2.0 * sigma_t * sigma_t)

        for src in self.smoke_sources:
            if src.intensity <= 1e-6:
                continue
            dist = self._source_geodesic_distance(src)
            if dist is None:
                continue
            # 1/(2πσ²) = inv_two_sigma_sq / π
            gauss = src.intensity * inv_two_sigma_sq / np.pi * np.exp(
                -(dist * dist) * inv_two_sigma_sq
            )
            Q += gauss.astype(np.float32)
        return Q

    # ---------- 拉普拉斯 ----------

    def _laplacian(self, S: np.ndarray) -> np.ndarray:
        """
        ∇²S 有限差分，只耦合可通行元胞。

        对每个可通行元胞：
        - 邻居可通行  → 用邻居浓度；
        - 邻居是墙/障碍/网格外 → 用自身浓度（零法向梯度，零通量）。
        这样墙面既不吸烟也不透烟，烟雾不会被“抽”进墙里。
        """
        P = self.passable
        S_up = np.empty_like(S)
        S_up[1:, :] = S[:-1, :]
        S_up[0, :] = S[0, :]
        P_up = np.zeros_like(P)
        P_up[1:, :] = P[:-1, :]
        S_up = np.where(P_up, S_up, S)

        S_down = np.empty_like(S)
        S_down[:-1, :] = S[1:, :]
        S_down[-1, :] = S[-1, :]
        P_down = np.zeros_like(P)
        P_down[:-1, :] = P[1:, :]
        S_down = np.where(P_down, S_down, S)

        S_left = np.empty_like(S)
        S_left[:, 1:] = S[:, :-1]
        S_left[:, 0] = S[:, 0]
        P_left = np.zeros_like(P)
        P_left[:, 1:] = P[:, :-1]
        S_left = np.where(P_left, S_left, S)

        S_right = np.empty_like(S)
        S_right[:, :-1] = S[:, 1:]
        S_right[:, -1] = S[:, -1]
        P_right = np.zeros_like(P)
        P_right[:, :-1] = P[:, 1:]
        S_right = np.where(P_right, S_right, S)

        dx = self.cell_size
        lap = (S_up + S_down + S_left + S_right - 4.0 * S) / (dx * dx)
        lap[~P] = 0.0
        return lap.astype(np.float32, copy=False)

    # ---------- 主更新 ----------

    def update_smoke(self):
        """
        单步更新：
            S_{t+1} = S_t + dt·(α∇²S_t + Q − δS_t)
        然后墙体置零、归一化。

        墙体严格性由三处共同保证：
        - Q 只在烟源可达的可通行元胞上非零；
        - ∇² 不跨墙耦合；
        - 更新后再强制墙体为 0。
        """
        t = self.time_step_count
        sigma_t = self.sigma0 + self.sigma_grow * t
        sigma_t = min(sigma_t, max(self.width, self.height) * 0.4)

        # 0. 兜底：外部若写入过墙体浓度，先清掉
        S = self.smoke_matrix
        if not S.flags.writeable:
            S = S.copy()
        S[self.wall_mask] = 0.0

        # 1. 高斯源项（测地距离，墙后为 0）
        Q = self._gaussian_source_term(sigma_t)

        # 2. 扩散（墙面零通量）
        lap = self._laplacian(S)

        # 3. 扩散方程更新
        S_new = S + self.dt * (
            self.alpha * lap + Q - self.delta * S
        )
        np.clip(S_new, 0.0, None, out=S_new)

        # 4. 墙体强制置零（防穿墙的不变量）
        S_new[self.wall_mask] = 0.0

        # 5. 归一化到 [0,1]，与旧模型取值对齐
        if self.normalize:
            smax = float(S_new.max())
            if smax > 1e-12:
                S_new = S_new / smax
        np.clip(S_new, 0.0, 1.0, out=S_new)

        self.smoke_matrix = S_new.astype(np.float32, copy=False)
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
