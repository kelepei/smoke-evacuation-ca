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


def _cell_type_value(cell) -> str:
    ct = getattr(cell, "cell_type", None)
    return str(getattr(ct, "value", ct)).lower()


class FloorField:
    """静态场：到出口的距离场。

    多出口场景下，除了全场最近的 dist_field，还为每个出口单独算一张场
    （exit_fields[exit_id]），这样"行人选择了哪个出口"才能真正决定他往哪走。

    性能：BFS 用一维 bytearray + 一维距离表，避免在大地图上每一步都做
    上百万次 getattr/str/lower（网页 runtime 开启封锁策略时会重建距离场）。
    """

    def __init__(self, grid: Grid, exits):
        self.grid = grid
        self.exits = exits
        self.dist_field = None
        self.exit_fields: dict[str, np.ndarray] = {}
        self._width = int(grid.width)
        self._height = int(grid.height)
        self._cell_index = {(c.x, c.y): c for c in grid.cells}
        self._passable = self._build_passable()

    # ------------------------------------------------------------------ 预处理
    def _build_passable(self) -> bytearray:
        passable = bytearray(self._width * self._height)
        for (x, y), cell in self._cell_index.items():
            if 0 <= x < self._width and 0 <= y < self._height and _cell_type_value(cell) not in ("wall", "obstacle"):
                passable[y * self._width + x] = 1
        return passable

    def exit_cells(self) -> list[tuple[int, int]]:
        return [
            (int(c.x), int(c.y))
            for c in self.grid.cells
            if _cell_type_value(c) == "exit"
        ]

    def _exit_cells_for(self, exit_obj, all_exit_cells: list[tuple[int, int]]) -> list[tuple[int, int]]:
        """把一个出口对象映射到它的出口元胞（同坐标优先，否则取最近的出口元胞）。"""
        ex = int(getattr(exit_obj, "x", -1))
        ey = int(getattr(exit_obj, "y", -1))
        if (ex, ey) in all_exit_cells:
            return [(ex, ey)]
        if not all_exit_cells:
            return []
        nearest = min(all_exit_cells, key=lambda p: (p[0] - ex) ** 2 + (p[1] - ey) ** 2)
        return [nearest]

    # ------------------------------------------------------------------ 距离场
    def compute_distance_field(self):
        all_exit_cells = self.exit_cells()
        seeds = [y * self._width + x for (x, y) in all_exit_cells]
        self.dist_field = self._bfs(seeds)

        # 每个出口一张独立距离场
        self.exit_fields = {}
        for exit_obj in self.exits or []:
            eid = str(getattr(exit_obj, "id", ""))
            cells = self._exit_cells_for(exit_obj, all_exit_cells)
            if not cells:
                continue
            field = self._bfs([y * self._width + x for (x, y) in cells])
            self.exit_fields[eid] = field

    def _bfs(self, seed_indices: list[int]) -> np.ndarray:
        width, height = self._width, self._height
        total = width * height
        dist = [9999] * total
        passable = self._passable
        queue: deque[int] = deque()
        for index in seed_indices:
            if 0 <= index < total:
                dist[index] = 0
                queue.append(index)

        while queue:
            index = queue.popleft()
            next_dist = dist[index] + 1
            x = index % width
            if x > 0:
                nxt = index - 1
                if passable[nxt] and dist[nxt] > next_dist:
                    dist[nxt] = next_dist
                    queue.append(nxt)
            if x + 1 < width:
                nxt = index + 1
                if passable[nxt] and dist[nxt] > next_dist:
                    dist[nxt] = next_dist
                    queue.append(nxt)
            nxt = index - width
            if nxt >= 0 and passable[nxt] and dist[nxt] > next_dist:
                dist[nxt] = next_dist
                queue.append(nxt)
            nxt = index + width
            if nxt < total and passable[nxt] and dist[nxt] > next_dist:
                dist[nxt] = next_dist
                queue.append(nxt)

        return np.asarray(dist, dtype=float).reshape(height, width)

    # ------------------------------------------------------------------ 查询
    def distance_to(self, exit_id, x: int, y: int) -> float:
        """指定出口的距离；没有该出口场时退化为全场最近出口距离。"""
        field = self.exit_fields.get(str(exit_id))
        if field is None:
            field = self.dist_field
        if field is None:
            return float("inf")
        if not (0 <= x < self._width and 0 <= y < self._height):
            return float("inf")
        return float(field[y][x])
