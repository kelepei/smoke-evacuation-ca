import random
from typing import Optional
from core.schema import Person, ScenarioConfig, Exit


class ExitChooser:
    """
    B03 出口选择模块
    mode: "nearest" | "familiar" | "random" | "guided"
    """
    def __init__(self, scene: ScenarioConfig, rng: random.Random):
        self.scene = scene
        self.exits: list[Exit] = scene.exits
        self.rng = rng

    def select_exit(self, person: Person, mode: str, guided_exit_id: Optional[int] = None) -> Optional[Exit]:
        """
        :param person: 行人对象
        :param mode: 选择模式
        :param guided_exit_id: guided模式下外部传入引导出口id
        :return: Exit对象，没有合法出口返回None
        """
        valid_exits = [e for e in self.exits if e.is_open]
        if not valid_exits:
            return None

        if mode == "guided":
            # 外部引导出口
            for e in valid_exits:
                if e.exit_id == guided_exit_id:
                    return e
            # 引导出口不存在，降级到最近出口
            mode = "nearest"

        if mode == "nearest":
            # 最近出口：使用person内部存储的距离场信息（ca_model使用）
            min_dist = float("inf")
            target = None
            px, py = int(person.x), int(person.y)
            for e in valid_exits:
                dx = px - e.x
                dy = py - e.y
                dist = dx*dx + dy*dy
                if dist < min_dist:
                    min_dist = dist
                    target = e
            return target

        elif mode == "familiar":
            # 熟悉出口：优先选familiar高的出口；familiar为0等价最近出口
            max_fam = -1.0
            target = None
            for e in valid_exits:
                fam_val = person.familiarity
                dist_weight = 1.0 / ((int(person.x)-e.x)**2 + (int(person.y)-e.y)**2 + 1)
                score = fam_val * 0.6 + dist_weight * 0.4
                if score > max_fam:
                    max_fam = score
                    target = e
            return target

        elif mode == "random":
            return self.rng.choice(valid_exits)

        return valid_exits[0]