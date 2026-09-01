from typing import Dict, Set
from core.schema import Person, Grid


class CongestionModel:
    """
    B09 拥堵模型：局部密度计算、拥堵等待、出口瓶颈
    注意：本模块只输出【是否应该原地等待】，不做元胞冲突抢占；冲突抢占交给 conflict_solver
    """
    def __init__(self, grid: Grid, neighbor_radius: int = 2, density_threshold: float = 0.35):
        self.grid = grid
        self.neighbor_radius = neighbor_radius
        self.density_threshold = density_threshold

    def calc_local_density(self, person: Person, alive_positions: Set[tuple[int, int]]) -> float:
        """计算行人周边局部密度：有效行人数量 / 邻域总格子数"""
        px, py = int(person.x), int(person.y)
        count = 0
        total_cell = 0
        for dy in range(-self.neighbor_radius, self.neighbor_radius+1):
            for dx in range(-self.neighbor_radius, self.neighbor_radius+1):
                nx = px + dx
                ny = py + dy
                total_cell += 1
                if (nx, ny) in alive_positions:
                    count += 1
        return count / total_cell if total_cell>0 else 0.0

    def need_congestion_wait(self, person: Person, alive_positions: Set[tuple[int, int]], rng) -> bool:
        """
        根据局部密度判断是否因为拥堵原地等待
        :return True:拥堵，原地等待；False:正常移动
        """
        dens = self.calc_local_density(person, alive_positions)
        if dens < self.density_threshold:
            return False
        # 密度超过阈值，按概率等待
        wait_prob = min(0.9, dens * 1.2)
        return rng.random() < wait_prob

    def get_exit_bottleneck_wait(self, exit_pos: tuple[int,int], alive_positions: Set[tuple[int,int]], max_capacity:int=3) -> bool:
        """出口瓶颈：出口元胞周边同时存在超过max_capacity活人，产生排队等待"""
        ex, ey = exit_pos
        cnt = 0
        for dy in (-1,0,1):
            for dx in (-1,0,1):
                if (ex+dx, ey+dy) in alive_positions:
                    cnt +=1
        return cnt >= max_capacity