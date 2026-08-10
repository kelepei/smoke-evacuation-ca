import sys
from pathlib import Path
BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

import numpy as np
from collections import deque
from core.schema import Grid, Exit, CellType


def get_cell(grid: Grid, x: int, y: int):
    idx = y * grid.width + x
    return grid.cells[idx]


class FloorField:
    def __init__(self, grid: Grid, exits):
        self.grid = grid
        self.exits = exits
        self.dist_field = None

    def compute_distance_field(self):
        w = self.grid.width
        h = self.grid.height
        dist = np.full((h, w), fill_value=9999, dtype=float)
        q = deque()

        # 寻找所有出口格子
        for y in range(h):
            for x in range(w):
                c = get_cell(self.grid, x, y)
                if c.cell_type == CellType.EXIT:
                    dist[y][x] = 0
                    q.append((x, y))

        dirs = [(1,0),(-1,0),(0,1),(0,-1)]
        while q:
            cx, cy = q.popleft()
            for dx, dy in dirs:
                nx = cx + dx
                ny = cy + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                cell = get_cell(self.grid, nx, ny)
                if cell.cell_type in (CellType.WALL, CellType.OBSTACLE):
                    continue
                if dist[ny][nx] > dist[cy][cx] + 1:
                    dist[ny][nx] = dist[cy][cx] + 1
                    q.append((nx, ny))
        self.dist_field = dist