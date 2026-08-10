"""
疏散仿真引擎
管理行人状态、烟雾更新、移动计算、可视化动画
"""

import sys
from pathlib import Path
BASE_PATH = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_PATH))

import random
import numpy as np
import matplotlib.pyplot as plt
from core.schema import ScenarioConfig, Grid, Person, CellType
from .ca_model import calc_next_position
from .smoke_model import update_smoke
from .floor_field import FloorField


class EvacEngine:
    """
    疏散仿真引擎
    适配A模块输出人员坐标，不再内部随机生成位置
    使用方式：
        loader = CASimulationLoader()
        loader.init_ca_model()
        scene.persons = loader.agent_list
        engine = EvacEngine(scenario_config)
        engine.run_one_step(c_step_data, signage_model)
    """

    def __init__(self, scene: ScenarioConfig):
        """
        初始化仿真引擎
        Args:
            scene: ScenarioConfig 对象，scene.persons 是ca_loader加载完成、带x/y坐标的行人列表
        """
        self.scene: ScenarioConfig = scene
        self.grid: Grid = scene.grid
        self.width = self.grid.width
        self.height = self.grid.height
        self.current_step = 0

        # 1. 初始化距离场
        self.floor_field = FloorField(self.grid, scene.exits)
        self.floor_field.compute_distance_field()

        if self.floor_field.dist_field is not None:
            print(f"[OK] 距离场计算完成，出口数量: {len(scene.exits)}")
        else:
            print("[WARN] 距离场计算失败，行人将无法找到出口！")

        # 2. 直接使用外部传入行人，不随机修改坐标
        self.person_map: dict[int, Person] = {}
        self.load_external_persons(scene.persons)
        print(f"[OK] 载入外部行人 {len(self.person_map)} 个，使用A模块分配坐标")

        # 3. 初始化烟雾矩阵
        self.smoke_matrix = [[0.0 for _ in range(self.width)] for _ in range(self.height)]
        for smoke_source in scene.smoke_sources:
            if 0 <= smoke_source.x < self.width and 0 <= smoke_source.y < self.height:
                self.smoke_matrix[smoke_source.y][smoke_source.x] += smoke_source.intensity

        # 4. 统计信息
        self.total_persons = len(self.person_map)
        self.evacuated_count = 0
        self.step_log = []

        # 动画画布初始化
        self.fig, self.ax = plt.subplots(figsize=(10, 7))
        plt.ion()

    def load_external_persons(self, persons):
        """
        加载外部已分配好坐标的行人（来自ca_loader/A模块输出）
        不再覆盖person.x、person.y
        """
        self.person_map.clear()
        for person in persons:
            self.person_map[person.id] = person

    # 【删除原有随机分配坐标的 _assign_person_positions 整个函数】

    def draw_animation(self, signage_model=None, guide_model=None):
        """实时绘制仿真动画，屏蔽引导员绘图规避dict报错，修复鼠标溢出"""
        self.ax.clear()
        # 地形图层
        bg_map = np.zeros((self.height, self.width))
        for cell in self.grid.cells:
            if cell.cell_type == CellType.WALL:
                bg_map[cell.y, cell.x] = 1
            elif cell.cell_type == CellType.EXIT:
                bg_map[cell.y, cell.x] = 2

        # 烟雾叠加
        smoke_np = np.array(self.smoke_matrix)
        render_img = bg_map + smoke_np * 0.6
        im = self.ax.imshow(render_img, cmap="gray", origin="upper", vmin=0, vmax=3)
        # 修复matplotlib鼠标悬浮int溢出报错
        im.format_cursor_data = lambda x: ""

        # 绘制行人
        evac_x, evac_y = [], []
        alive_x, alive_y = [], []
        for p in self.person_map.values():
            if p.evacuated:
                evac_x.append(p.x)
                evac_y.append(p.y)
            else:
                alive_x.append(p.x)
                alive_y.append(p.y)
        self.ax.scatter(alive_x, alive_y, c="red", s=25, zorder=5, label="待疏散人员")
        self.ax.scatter(evac_x, evac_y, c="green", s=15, zorder=5, label="已撤离人员")

        # 绘制指示牌
        if signage_model is not None:
            all_sign = signage_model.get_all_signages()
            for idx, sig in enumerate(all_sign):
                sx, sy = sig["x"], sig["y"]
                if sig["type"] == "static":
                    self.ax.scatter(sx, sy, marker="^", c="blue", s=45, zorder=6, label="静态指示牌" if idx == 0 else "")
                else:
                    self.ax.scatter(sx, sy, marker="^", c="gold", s=45, zorder=6, label="动态指示牌" if idx == 0 else "")

        # 注释引导员绘制，解决dict无get_guide_positions报错
        # if guide_model is not None:
        #     guides = guide_model.get_guide_positions()
        #     g_x = [g[0] for g in guides]
        #     g_y = [g[1] for g in guides]
        #     self.ax.scatter(g_x, g_y, c="orange", marker="s", s=60, zorder=6, label="引导员")

        self.ax.set_title(f"疏散仿真 | 步数:{self.current_step} | 时间:{self.current_step*0.5}s")
        self.ax.legend(loc="upper right")
        plt.tight_layout()
        plt.pause(0.03)

    def is_all_evacuated(self) -> bool:
        """全部撤离判定"""
        return all(p.evacuated for p in self.person_map.values())

    def get_evacuated_count(self) -> int:
        return sum(1 for p in self.person_map.values() if p.evacuated)

    def run_one_step(self, c_step_data: dict = None, signage_model=None):
        """单步仿真更新 + 动画渲染"""
        if c_step_data is None:
            c_step_data = {}

        # 1. 更新烟雾场
        self.smoke_matrix = update_smoke(
            smoke_mat=self.smoke_matrix,
            width=self.width,
            height=self.height,
            smoke_sources=self.scene.smoke_sources
        )

        # 同步烟雾值到每个格子
        for cell in self.grid.cells:
            if 0 <= cell.y < self.height and 0 <= cell.x < self.width:
                cell.smoke = self.smoke_matrix[cell.y][cell.x]

        # 2. 收集当前已占用坐标，防止行人重叠
        occupied_positions = set()
        for pid, person in self.person_map.items():
            if not person.evacuated:
                occupied_positions.add((int(person.x), int(person.y)))

        # 3. 预计算所有人下一步坐标
        next_positions = {}
        for pid, person in self.person_map.items():
            if person.evacuated:
                continue
            single_behavior = c_step_data.get(pid, {})
            if "target_exit" in single_behavior:
                person.target_exit_id = single_behavior["target_exit"]

            # 完整传参匹配ca_model.calc_next_position
            nx, ny = calc_next_position(
                person,
                self.grid,
                self.smoke_matrix,
                single_behavior=single_behavior,
                floor_field=self.floor_field,
                signage_model=signage_model,
                occupied_positions=occupied_positions
            )
            next_positions[pid] = (nx, ny)

        # 4. 批量更新位置、剂量、撤离状态
        for pid, (nx, ny) in next_positions.items():
            person = self.person_map[pid]
            if person.evacuated:
                continue
            person.prev_x = person.x
            person.prev_y = person.y
            person.x = nx
            person.y = ny
            # 烟雾剂量累积
            if 0 <= ny < self.height and 0 <= nx < self.width:
                person.dose += self.smoke_matrix[ny][nx] * 0.5
            # 判断到达出口
            cell = self.grid.get_cell(nx, ny)
            if cell and cell.cell_type == CellType.EXIT:
                person.evacuated = True

        # 5. 统计更新
        self.evacuated_count = self.get_evacuated_count()
        self.current_step += 1

        # 6. 每10步记录日志
        if self.current_step % 10 == 0:
            self.step_log.append({
                "step": self.current_step,
                "evacuated": self.evacuated_count,
                "total": self.total_persons,
            })

        # 绘制动画窗口
        self.draw_animation(signage_model)

        return {
            "step": self.current_step,
            "evacuated": self.evacuated_count,
            "total": self.total_persons,
            "remaining": self.total_persons - self.evacuated_count,
        }

    def get_person_positions(self) -> dict:
        """获取未撤离行人坐标"""
        return {
            pid: (int(p.x), int(p.y))
            for pid, p in self.person_map.items()
            if not p.evacuated
        }

    def get_evacuation_time(self, person_id: int = None) -> int:
        """获取疏散完成步数"""
        if person_id is None:
            max_step = 0
            for p in self.person_map.values():
                if p.evacuated:
                    max_step = max(max_step, self.current_step)
            return max_step
        else:
            p = self.person_map.get(person_id)
            if p and p.evacuated:
                return self.current_step
            return -1

    def close_anim(self):
        """关闭动画绘图窗口"""
        plt.ioff()
        plt.show()
