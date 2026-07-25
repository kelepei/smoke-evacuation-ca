from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


# 1. 元胞类型定义
class CellType(Enum):
    FREE = "free"  # 可通行区域
    WALL = "wall"  # 墙壁，不可进入
    OBSTACLE = "obstacle"  # 障碍物
    EXIT = "exit"  # 出口位置
    SMOKE_SOURCE = "smoke_source"  # 烟源位置
    SIGN = "sign"  # 指示牌
    GUIDE_ZONE = "guide_zone"  # 引导区域





# 2. 建筑语义类型
class SemanticType(Enum):
    CLASSROOM = "classroom"  # 教室
    CORRIDOR = "corridor"  # 走廊
    STAIR = "stair"  # 楼梯
    SHOP = "shop"  # 商店
    HALL = "hall"  # 大厅
    CANTEEN = "canteen"  # 食堂
    DORM = "dorm"  # 宿舍





# 3. 信息状态
class InfoState(Enum):
    UNKNOWN = "unknown"  # 未获取危险信息
    ALERTED = "alerted"  # 已收到警报
    CONFIRMED = "confirmed"  # 已确认危险
    MISINFORMED = "misinformed"  # 获取错误信息
    GUIDED = "guided"  # 接收到引导信息



# 4. 社会关系类型
class RelationType(Enum):
    FRIEND = "friend"
    CLASSMATE = "classmate"
    FAMILY = "family"
    COLLEAGUE = "colleague"
    STRANGER = "stranger"


STAFF_TO_CUSTOMER = "staff_to_customer"




# 5. Cell 元胞对象
@dataclass
class Cell:
    x: int  # 元胞坐标
    y: int  # 元胞坐标
    cell_type: CellType = CellType.FREE  # 元胞类型
    room_id: str = ""  # 房间编号
    semantic: Optional[SemanticType] = None  # 建筑语义类型
    smoke: float = 0.0  # 烟雾浓度
    risk: float = 0.0  # 风险值
    guidance: float = 0.0  # 引导信息





# 6. Exit 出口对象

@dataclass
class Exit:
    id: str  # 出口编号


x: int  # 出口位置坐标
y: int  # 出口位置坐标
width: float = 1.0  # 出口实际宽度(m)
label: str = "EXIT"  # 出口名称



# 7. Person 行人对象
@dataclass
class Person:
    id: int  # 行人编号
    x: int  # 当前坐标
    y: int  # 当前坐标


speed: float = 1.0  # 基础移动速度
risk_sensitivity: float = 0.5  # 风险敏感程度
familiarity: float = 0.5  # 对环境熟悉程度
herding_tendency: float = 0.5  # 从众倾向
group_id: Optional[str] = None  # 所属群体
profile: str = "default"  # 人员类型
info_state: InfoState = InfoState.UNKNOWN  # 当前信息状态
target_exit_id: Optional[str] = None  # 当前选择的出口
evacuated: bool = False  # 是否已经完成疏散
dose: float = 0.0  # 烟雾暴露剂量， B模块计算



# 8. Relation 社会关系对象
@dataclass
class Relation:
    person_a_id: int  # 两个人员编号
    person_b_id: int


relation_type: RelationType = RelationType.STRANGER  # 关系类型
strength: float = 0.5  # 关系强度
trust: float = 0.5
wait_probability: float = 0.3  # 等待概率
follow_probability: float = 0.3  # 跟随概率




# 9. Grid 网格对象
@dataclass
class Grid:
    width: int  # 网格尺寸,x方向网格数量（列数），范围[ 0, width-1]
    height: int  # y方向网格数量（行数），y范围[ 0, height-1]
    cell_size: float = 0.5  # 单个元胞实际尺寸(m)
    cells: List[Cell] = field(default_factory=list)  # 所有元胞




# 10. 烟源对象
@dataclass
class SmokeSource:
    x: int  # 烟源位置
    y: int
    intensity: float = 1.0  # 初始强度




# 11. 实验场景配置
@dataclass
class ScenarioConfig:
    # 实验编号
    scenario_id: str

    # 地图信息
    grid: Grid

    # 出口列表
    exits: List[Exit] = field(default_factory=list)

    # 初始人员
    persons: List[Person] = field(default_factory=list)

    # 社会关系
    # C模块提供
    relations: List[Relation] = field(default_factory=list)

    # 烟源
    smoke_sources: List[SmokeSource] = field(default_factory=list)

    # 其他实验参数


parameters: Dict[str, Any] = field(default_factory=dict)
