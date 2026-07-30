from data_model.schema import ScenarioConfig, Grid, Cell, Person, CellType, SmokeSource, Exit


def build_base_scene():
    width, height = 16, 12
    cell_size = 0.5
    cells = []

    # 填充全部空地
    for y in range(height):
        for x in range(width):
            cells.append(Cell(x=x, y=y, cell_type=CellType.FREE))

    # 中间墙体障碍物
    for wx in range(3, 12):
        cells.append(Cell(x=wx, y=5, cell_type=CellType.WALL))

    grid = Grid(width=width, height=height, cell_size=cell_size, cells=cells)

    # 定义出口（新版schema独立Exit对象）
    exits = [
        Exit(id="exit_01", x=15, y=6, width=1.0, label="主出口")
    ]

    # 初始化行人，使用schema原生字段
    persons = [
        Person(id=1, x=2, y=2, speed=1.2),
        Person(id=2, x=4, y=3, speed=1.1),
        Person(id=3, x=7, y=2, speed=1.3),
    ]

    # 烟源
    smoke_sources = [
        SmokeSource(x=3, y=3, intensity=0.32)
    ]

    return ScenarioConfig(
        scenario_id="mock_001",
        grid=grid,
        exits=exits,
        persons=persons,
        smoke_sources=smoke_sources
    )
