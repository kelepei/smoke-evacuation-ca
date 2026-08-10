def update_smoke(smoke_mat, width, height, smoke_sources):
    """简易烟雾扩散"""
    new_smoke = [[0.0 for _ in range(width)] for _ in range(height)]
    # 基础扩散衰减
    for y in range(height):
        for x in range(width):
            if y-1 >= 0:
                new_smoke[y][x] += smoke_mat[y-1][x] * 0.7
            if y+1 < height:
                new_smoke[y][x] += smoke_mat[y+1][x] * 0.7
            if x-1 >= 0:
                new_smoke[y][x] += smoke_mat[y][x-1] * 0.7
            if x+1 < width:
                new_smoke[y][x] += smoke_mat[y][x+1] * 0.7

    # 新增烟源持续释放烟雾
    for src in smoke_sources:
        if 0 <= src.x < width and 0 <= src.y < height:
            new_smoke[src.y][src.x] += src.intensity

    return new_smoke