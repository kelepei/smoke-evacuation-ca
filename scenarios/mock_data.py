from core.schema import (
    ScenarioConfig, Grid, Cell, CellType,
    Exit, Person, SmokeSource
)

def build_base_scene() -> ScenarioConfig:
    """构建简单测试场景，适配当前仓库schema"""
    W = 20
    H = 12
    cells = []
    # 构建网格
    for y in range(H):
        for x in range(W):
            ct = CellType.FREE
            # 上下边界墙体
            if y == 0 or y == H - 1:
                ct = CellType.WALL
            # 左右边界墙体
            elif x == 0 or x == W - 1:
                ct = CellType.WALL
            # (15,6)设置为出口格子
            if x == 15 and y == 6:
                ct = CellType.EXIT
            # 烟源位置(3,3)
            if x == 3 and y == 3:
                ct = CellType.SMOKE_SOURCE

            cell = Cell(x=x, y=y, cell_type=ct)
            cells.append(cell)

    grid = Grid(width=W, height=H, cell_size=0.5, cells=cells)

    exits = [Exit(id="exit_01", x=15, y=6)]

    # 创建几个行人
    persons = [
        Person(id=1, x=5, y=5),
        Person(id=2, x=6, y=5),
        Person(id=3, x=7, y=4),
    ]

    smoke_sources = [SmokeSource(x=3, y=3, intensity=1.0)]

    scene = ScenarioConfig(
        scenario_id="mock_01",
        grid=grid,
        exits=exits,
        persons=persons,
        smoke_sources=smoke_sources
    )
    return scene
