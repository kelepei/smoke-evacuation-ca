import sys
from pathlib import Path
BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

import numpy as np
from core.schema import Grid, SmokeSource, CellType


def get_cell(grid: Grid, x: int, y: int):
    idx = y * grid.width + x
    return grid.cells[idx]


class SmokeSim:
    def __init__(self, grid: Grid, smoke_sources):
        self.grid = grid
        self.smoke_sources = smoke_sources
        self.smoke_matrix = None
        self.diff_rate = 0.12
        self.source_strength = 0.08

    def init_smoke_matrix(self):
        w = self.grid.width
        h = self.grid.height
        self.smoke_matrix = np.zeros((h, w), dtype=float)

    def step(self):
        w = self.grid.width
        h = self.grid.height
        new_smoke = self.smoke_matrix.copy()

        # 烟源持续生成烟雾
        for src in self.smoke_sources:
            sx, sy = src.x, src.y
            if 0 <= sx < w and 0 <= sy < h:
                new_smoke[sy][sx] += self.source_strength

        # 扩散
        for y in range(h):
            for x in range(w):
                cell = get_cell(self.grid, x, y)
                if cell.cell_type == CellType.WALL:
                    new_smoke[y][x] = 0.0
                    continue
                val = self.smoke_matrix[y][x]
                for dx, dy in ((1,0),(-1,0),(0,1),(0,-1)):
                    nx = x + dx
                    ny = y + dy
                    if 0 <= nx < w and 0 <= ny < h:
                        n_cell = get_cell(self.grid, nx, ny)
                        if n_cell.cell_type != CellType.WALL:
                            new_smoke[y][x] += self.diff_rate * (self.smoke_matrix[ny][nx] - val)
        # 截断0‑1
        new_smoke = np.clip(new_smoke, 0.0, 1.0)
        self.smoke_matrix = new_smoke