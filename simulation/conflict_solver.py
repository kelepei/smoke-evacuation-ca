"""
resolve_conflict.py
B02 CA冲突消解模块
处理多行人候选移动到同一个目标格子的竞争冲突
规则：多个行人争夺同一位置，随机选出赢家；输家留在原地。
已撤离evacuated行人不参与冲突竞争；
死亡is_dead行人在calc_next_position已经返回原地候选，本文件无需额外判断。
"""
import random


def resolve_conflict(candidate_moves: dict[int, tuple[int, int]],
                     person_map: dict[int, object],
                     rng: random.Random) -> dict[int, tuple[int, int]]:
    """
    CA元胞自动机冲突消解
    :param candidate_moves: dict[行人id, (目标x,y)] 每一个行人计算出的下一步候选位置
    :param person_map: 行人对象字典 {pid:Person实例}，用于读取行人当前真实坐标
    :param rng: 外部传入Random实例，绑定全局seed，保证仿真结果可复现
    :return: dict[pid, (final_x, final_y)] 冲突处理完成之后每个人最终坐标；冲突失败行人留在原地
    """
    result = dict(candidate_moves)
    pos_to_pids = {}

    for pid, pos in candidate_moves.items():
        p = person_map[pid]
        # 已撤离行人，直接维持当前坐标，不参与冲突竞争
        if getattr(p, "evacuated", False):
            result[pid] = (int(p.x), int(p.y))
            continue
        if pos not in pos_to_pids:
            pos_to_pids[pos] = []
        pos_to_pids[pos].append(pid)

    # 遍历每一个目标位置，如果多个人争夺，随机选赢家
    for target_pos, pid_list in pos_to_pids.items():
        if len(pid_list) > 1:
            winner = rng.choice(pid_list)
            for pid in pid_list:
                if pid != winner:
                    # 冲突争夺失败：保留行人原本的坐标，不移动
                    person_obj = person_map[pid]
                    result[pid] = (int(person_obj.x), int(person_obj.y))

    return result
