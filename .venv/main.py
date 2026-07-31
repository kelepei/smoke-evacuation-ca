from map_import.map_loader_grid import load_grid
from simulation.ca_model import CAModel

def run_with_a_map():
    """模式一：使用A输出的Grid对象，正式联调"""
    # A负责读取文件，输出Grid对象，B完全不碰原始文件
    grid = load_grid("classroom_corridor.json")
    ca = CAModel(grid)
    # 打印地图信息，验证读取成功（文档示例）
    ca.show_map_info()
    # 添加测试行人
    ca.add_person(pid=1, x=2, y=2)
    ca.run(max_step=200)

def run_mock():
    """模式二：本地自测，自己构造mock Grid，不依赖A"""
    # 这里你可以继续用原来build_base_scene拿到grid，用来调试CA逻辑
    from scenarios.mock_data import build_base_scene
    scene_cfg = build_base_scene()
    grid = scene_cfg.grid
    ca = CAModel(grid)
    ca.show_map_info()
    ca.add_person(pid=1, x=2, y=2)
    ca.run(max_step=200)


if __name__ == "__main__":
    # run_mock() # 本地开发自测优先打开
    run_with_a_map() # 和A联调的时候打开