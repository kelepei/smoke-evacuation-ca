import random


def resolve_conflict(candidate_moves: dict[int, tuple[int, int]]) -> dict[int, tuple[int, int] | None]:
    """
    CA元胞自动机冲突消解
    candidate_moves: dict[行人id, (目标x,y)]
    return: dict，value=None代表冲突失败，原地不动
    """
    result = dict(candidate_moves)
    pos_to_pids = {}
    for pid, pos in candidate_moves.items():
        if pos not in pos_to_pids:
            pos_to_pids[pos] = []
        pos_to_pids[pos].append(pid)

    for target_pos, pid_list in pos_to_pids.items():
        if len(pid_list) > 1:
            winner = random.choice(pid_list)
            for pid in pid_list:
                if pid != winner:
                    result[pid] = None
    return result