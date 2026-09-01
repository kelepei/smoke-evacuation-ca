import random
from typing import Optional
from core.schema import CellType, Person, ScenarioConfig


class ExitChooser:
    """
    B03 出口选择模块
    mode: "nearest" | "familiar" | "random" | "guided"
    """
    def __init__(self, scene: ScenarioConfig, rng: random.Random):
        self.scene = scene
        exit_cells = [
            (int(cell.x), int(cell.y))
            for cell in scene.grid.cells
            if cell.cell_type == CellType.EXIT
        ]
        self.exits = [
            (x, y, str(exit_obj.id))
            for exit_obj, (x, y) in zip(scene.exits, exit_cells)
        ]
        self.rng = rng

    def select_exit(self, person: Person, mode: str, guided_exit_id: Optional[str] = None):
        """
        :param person: 行人对象
        :param mode: 选择模式
        :param guided_exit_id: guided模式下外部传入引导出口id
        :return: (x, y, exit_id)，没有合法出口返回None
        """
        # 当前正式运行链未接入出口开闭状态；场景中的出口均为可用出口。
        valid_exits = self.exits
        if not valid_exits:
            return None

        if mode == "guided":
            # 外部引导出口
            for exit_item in valid_exits:
                if exit_item[2] == guided_exit_id:
                    return exit_item
            # 引导出口不存在，降级到最近出口
            mode = "nearest"

        if mode == "nearest":
            # 最近出口：使用person内部存储的距离场信息（ca_model使用）
            min_dist = float("inf")
            target = None
            px, py = int(person.x), int(person.y)
            for exit_item in valid_exits:
                ex, ey, _ = exit_item
                dx = px - ex
                dy = py - ey
                dist = dx*dx + dy*dy
                if dist < min_dist:
                    min_dist = dist
                    target = exit_item
            return target

        elif mode == "familiar":
            # 熟悉出口：优先选familiar高的出口；familiar为0等价最近出口
            max_fam = -1.0
            target = None
            for exit_item in valid_exits:
                ex, ey, _ = exit_item
                fam_val = person.familiarity
                dist_weight = 1.0 / ((int(person.x)-ex)**2 + (int(person.y)-ey)**2 + 1)
                score = fam_val * 0.6 + dist_weight * 0.4
                if score > max_fam:
                    max_fam = score
                    target = exit_item
            return target

        elif mode == "random":
            return self.rng.choice(valid_exits)

        return valid_exits[0]
