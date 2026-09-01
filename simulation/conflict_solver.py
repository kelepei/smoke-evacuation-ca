import random


def resolve_conflict(candidate_moves: dict[int, tuple[int, int]],
                     person_map: dict[int, object],
                     rng: random.Random) -> dict[int, tuple[int, int]]:
    """
    CA元胞自动机冲突消解
    candidate_moves: dict[行人id, (目标x,y)]
    person_map: 行人对象字典，用于读取冲突失败行人的当前坐标
    rng: 外部传入随机实例，绑定场景seed，保证仿真可复现
    return: dict[pid, (final_x,y)]，冲突失败返回行人原地坐标
    """
    result = dict(candidate_moves)
    pos_to_pids = {}
    for pid, pos in candidate_moves.items():
        if pos not in pos_to_pids:
            pos_to_pids[pos] = []
        pos_to_pids[pos].append(pid)

    for target_pos, pid_list in pos_to_pids.items():
        if len(pid_list) > 1:
            winner = rng.choice(pid_list)
            for pid in pid_list:
                if pid != winner:
                    # 冲突失败：留在行人当前所在元胞
                    p = person_map[pid]
                    result[pid] = (int(p.x), int(p.y))
    return result