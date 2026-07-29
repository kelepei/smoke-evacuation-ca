import sys
import random
import math
from pathlib import Path

# 路径配置
BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

from data_model.schema_new import ScenarioConfig, Person, Grid, CellType, Cell


class EvacSimulation:
    def __init__(self, config: ScenarioConfig):
        self.config = config
        self.grid: Grid = config.grid
        self.persons: dict[int, Person] = {}

        # ==========任务书标准仿真参数==========
        self.dt = 0.5  # 时间步长 0.5s
        # 8摩尔邻域，包含原地等待
        self.neighbors_8 = [(-1, -1), (-1, 0), (-1, 1),
                            (0, -1),          (0, 1),
                            (1, -1),  (1, 0), (1, 1),
                            (0, 0)]

        # 效用函数权重
        self.w_d = 0.6    # 出口距离
        self.w_s = 0.5    # 烟雾浓度
        self.w_q = 0.4    # 拥堵程度
        self.w_g = 0.3    # 引导标识(A模块)
        self.w_f = 0.2    # 熟悉度
        self.w_r = 0.2    # 社会关系(C模块)
        self.w_h = 0.2    # 从众效应
        self.lam = 2.0    # Softmax系数

        # 风险感知公式参数
        self.a = 0.7
        self.b = 0.2
        self.c = 0.1

        self.distance_field = None   # D_exit距离场
        self.current_step = 0
        self.max_step = 500

    def init_simulation(self):
        # 加载行人 + 动态注入临时属性【核心，不修改公共schema】
        for person in self.config.persons:
            if not hasattr(person, "wait_step"):
                person.wait_step = 0
            if not hasattr(person, "risk"):
                person.risk = 0.0
            if not hasattr(person, "target_x"):
                person.target_x = None
            if not hasattr(person, "target_y"):
                person.target_y = None
            self.persons[person.id] = person

        # 预计算网格到出口距离场
        self._calc_distance_field()

    def _calc_distance_field(self):
        """计算每个元胞到最近出口的欧氏距离 D_exit(c)"""
        w = self.grid.width
        h = self.grid.height
        self.distance_field = [[float("inf")] * w for _ in range(h)]
        exit_pos_list = [(e.x, e.y) for e in self.config.exits]

        for y in range(h):
            for x in range(w):
                min_dist = float("inf")
                for (ex, ey) in exit_pos_list:
                    dist = math.hypot(x - ex, y - ey)
                    min_dist = min(min_dist, dist)
                self.distance_field[y][x] = min_dist

    def _get_cell(self, x: int, y: int) -> Cell | None:
        """坐标查询元胞"""
        for cell in self.grid.cells:
            if cell.x == x and cell.y == y:
                return cell
        return None

    def _can_pass(self, x: int, y: int) -> bool:
        """判断格子是否可行走"""
        cell = self._get_cell(x, y)
        if cell is None:
            return False
        if cell.cell_type in (CellType.WALL, CellType.OBSTACLE):
            return False
        return True

    def _update_smoke(self):
        """烟雾扩散模型，数据写入Cell.smoke，贴合schema"""
        diffuse_rate = 0.25
        decay_rate = 0.03
        spread_dir = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        new_smoke_cache = {}

        for cell in self.grid.cells:
            x, y = cell.x, cell.y
            cur_smoke = cell.smoke
            if not self._can_pass(x, y):
                new_smoke_cache[(x, y)] = cur_smoke * (1 - decay_rate)
                continue

            # 烟源强度
            source_val = 0.0
            for src in self.config.smoke_sources:
                if src.x == x and src.y == y:
                    source_val = src.intensity

            # 邻域扩散
            neighbor_smoke_sum = 0.0
            for dx, dy in spread_dir:
                nx, ny = x + dx, y + dy
                n_cell = self._get_cell(nx, ny)
                if n_cell and self._can_pass(nx, ny):
                    neighbor_smoke_sum += n_cell.smoke
            spread_value = neighbor_smoke_sum * diffuse_rate / 4

            total_smoke = cur_smoke * (1 - decay_rate) + spread_value + source_val
            new_smoke_cache[(x, y)] = min(total_smoke, 1.0)

        # 回写元胞烟雾
        for cell in self.grid.cells:
            cell.smoke = new_smoke_cache.get((cell.x, cell.y), 0.0)

    def _person_move_update(self):
        """核心：效用函数 + Softmax采样，仅计算目标位置，暂不移动"""
        # 清空本轮目标坐标
        for p in self.persons.values():
            p.target_x = None
            p.target_y = None

        # 统计网格拥堵人数 Congestion
        grid_occupancy = {}
        for p in self.persons.values():
            if not p.evacuated:
                grid_occupancy[(p.x, p.y)] = grid_occupancy.get((p.x, p.y), 0) + 1

        for person in self.persons.values():
            if person.evacuated:
                continue
            px, py = person.x, person.y
            candidate_list = []

            # 遍历8邻域候选位置
            for dx, dy in self.neighbors_8:
                cx = px + dx
                cy = py + dy
                # 边界校验
                if not (0 <= cx < self.grid.width and 0 <= cy < self.grid.height):
                    continue
                if not self._can_pass(cx, cy):
                    continue

                cell = self._get_cell(cx, cy)
                D_exit = self.distance_field[cy][cx]
                smoke = cell.smoke
                congestion = grid_occupancy.get((cx, cy), 0)
                guidance = cell.guidance

                # 读取schema原生自带参数
                familiarity = person.familiarity
                herding = person.herding_tendency
                relation = 0.0  # 【待对接C模块社会关系数据】
                eps = random.uniform(-0.05, 0.05)  # 随机扰动

                # 任务书完整效用公式
                utility = (
                    -self.w_d * D_exit
                    - self.w_s * smoke
                    - self.w_q * congestion
                    + self.w_g * guidance
                    + self.w_f * familiarity
                    + self.w_r * relation
                    + self.w_h * herding
                    + eps
                )
                candidate_list.append((cx, cy, utility))

            # 无合法候选，原地等待
            if not candidate_list:
                person.target_x = px
                person.target_y = py
                continue

            # Softmax概率计算
            u_values = [u for _, _, u in candidate_list]
            exp_u = [math.exp(self.lam * u) for u in u_values]
            sum_exp = sum(exp_u)
            prob = [e / sum_exp for e in exp_u]

            # 随机采样选出目标位置
            rand_val = random.random()
            cumulative = 0.0
            target_x, target_y = px, py
            for idx, (cx, cy, _) in enumerate(candidate_list):
                cumulative += prob[idx]
                if rand_val <= cumulative:
                    target_x, target_y = cx, cy
                    break
            person.target_x = target_x
            person.target_y = target_y

    def _collision_check(self):
        """任务书冲突解决策略：多人抢占同一格子处理"""
        target_pos_map = {}
        for p in self.persons.values():
            if p.evacuated or p.target_x is None:
                continue
            pos = (p.target_x, p.target_y)
            if pos not in target_pos_map:
                target_pos_map[pos] = []
            target_pos_map[pos].append(p)

        for pos, people in target_pos_map.items():
            if len(people) == 1:
                # 无冲突，直接移动
                self._move_person(people[0], pos[0], pos[1])
                people[0].wait_step = 0
            else:
                # 规则：等待步数越高优先级越大
                people.sort(key=lambda x: x.wait_step, reverse=True)
                selected_person = people[0]
                self._move_person(selected_person, pos[0], pos[1])
                # 其余行人原地等待，等待计数+1
                for rest_p in people[1:]:
                    rest_p.wait_step += 1

    def _move_person(self, person: Person, new_x, new_y):
        """执行行人移动，判断是否抵达出口撤离"""
        person.x = new_x
        person.y = new_y
        # 判断到达出口
        for exit_info in self.config.exits:
            if exit_info.x == new_x and exit_info.y == new_y:
                person.evacuated = True
                break

    def _update_person_risk(self):
        """任务书风险感知公式 & 累计烟雾剂量（dose为schema原生字段）"""
        dt = self.dt
        for p in self.persons.values():
            if p.evacuated:
                continue
            cell = self._get_cell(p.x, p.y)
            S = cell.smoke
            delta_S = 0
            visibility_loss = S
            # Risk_i(t) = aS + bΔS + c·VisibilityLoss
            p.risk = self.a * S + self.b * delta_S + self.c * visibility_loss
            # 累计暴露剂量
            p.dose += S * dt

    def _export_timestep_data(self):
        """时序数据输出接口，预留对接D可视化模块"""
        pass

    def render_text_map(self):
        """文本可视化地图 方案2"""
        w = self.grid.width
        h = self.grid.height
        map_grid = [["." for _ in range(w)] for _ in range(h)]

        # 墙体标记 #
        for cell in self.grid.cells:
            x, y = cell.x, cell.y
            if cell.cell_type in (CellType.WALL, CellType.OBSTACLE):
                map_grid[y][x] = "#"

        # 出口 E
        for exit_obj in self.config.exits:
            ex, ey = exit_obj.x, exit_obj.y
            map_grid[ey][ex] = "E"

        # 烟雾区域 *
        for cell in self.grid.cells:
            x, y = cell.x, cell.y
            if 0.3 <= cell.smoke < 1.0 and map_grid[y][x] == ".":
                map_grid[y][x] = "*"

        # 行人 @
        for p in self.persons.values():
            if not p.evacuated:
                px, py = p.x, p.y
                map_grid[py][px] = "@"

        print("-" * (w + 8))
        for line in map_grid:
            print("".join(line))
        print("图例：#墙体  .空地  E出口  @行人  *烟雾区")

    def step(self):
        # 仿真时序
        self._update_smoke()
        self._person_move_update()
        self._collision_check()
        self._update_person_risk()
        self._export_timestep_data()

        print(f"\n==== Step {self.current_step} ====")
        self.render_text_map()

        # 打印行人信息
        for p in self.persons.values():
            status = "已撤离" if p.evacuated else "疏散中"
            print(f"行人{p.id} | 坐标({p.x},{p.y}) |风险:{p.risk:.2f}|{status}")

        self.current_step += 1

    def run(self):
        print("====仿真开始运行====")
        while self.current_step < self.max_step:
            self.step()
            if self.current_step % 100 == 0 and self.current_step > 0:
                print(f"\n====进度：已运行 {self.current_step} 步====")
        print("====仿真运行结束====")


if __name__ == "__main__":
    from data_model.mock_data import build_base_scene
    scene = build_base_scene()
    sim = EvacSimulation(config=scene)
    sim.init_simulation()
    print("场景初始化完成，行人数量：", len(sim.persons))
    sim.run()
