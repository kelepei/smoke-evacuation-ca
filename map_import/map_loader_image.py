"""
PNG / JPG 房间地图导入模块
============================================================

功能：

1. 读取 PNG / JPG / JPEG / BMP 图片
2. 自动灰度化
3. 支持旋转
4. 支持裁剪
5. 自动判断黑底 / 白底
6. 自动二值化
7. 提取房间结构（墙体 + 家具障碍物）
8. 识别水平 / 垂直 / 斜向墙体
9. 连接轻微断裂的墙体
10. 保留小型障碍物与短墙
11. 过滤文字、数字、标注等非结构内容
12. 自动适应图片像素比例尺
13. PNG / JPG -> Grid
14. 保存二值化结果

============================================================
核心识别目标
============================================================

本模块从图片中提取“物理阻挡物”，包括：

        墙体（水平 / 垂直 / 斜向长直线）
        家具障碍物（桌子、柱子等实心小块）
        短墙（较短的墙体片段）

最终 binary 仍然只有两个值：

    0
        ↓
    FREE

    255
        ↓
    阻挡物（WALL）

注意：

    障碍物在 binary 里同样记为 255，
    与墙体一起转成 Grid 中的 WALL。

    之后在 MapEditor 中可人工把家具等
    区域改为 OBSTACLE。

本模块会自动过滤掉：

    文字
    数字
    房间名称
    箭头
    标注

这些不是物理结构，不应进入 Grid。

============================================================
像素比例
============================================================

这里有两个完全不同的尺寸概念。

一、图片像素比例：

    例如：

        5 × 5 像素
            ↓
        1 个 CA 元胞

    这里的 5 是 image_pixel_per_cell。

二、CA 物理尺寸：

    一个 CA 元胞：

        0.5m × 0.5m

    对应：

        grid.cell_size = 0.5

两者不能混淆。

============================================================
接口
============================================================

原有接口：

    load_image(
        image_path,
        rotate=0,
        crop=None,
        threshold=200
    )

    save_binary(
        binary,
        path
    )

组合接口：

    load_image_grid(
        image_path,
        rotate=0,
        crop=None,
        threshold=200,
        cell_size=None
    )

其中：

    cell_size=None
        自动检测图片像素比例。

    cell_size=5
        手动指定：
        5 × 5 像素 = 1 个 CA 元胞。

============================================================
注意
============================================================

本文件不修改：

    map_import/binary_to_grid.py

而是直接调用现有：

    binary_to_grid()

============================================================
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


# ============================================================
# 项目统一的 CA 物理元胞尺寸
# ============================================================

REAL_CELL_SIZE = 0.5


# ============================================================
# Grid 尺寸上限
# ============================================================
#
# 图片导入时，如果图片很大，不允许生成过大的 Grid。
#
# 这会影响两个地方：
#
#   1. _auto_detect_cell_size 的分辨率下限
#   2. _uniformize_walls 的目标墙厚
#
# 两者必须一致，否则会出现
# “墙太细被采样吞掉”的问题。
# ============================================================

TARGET_MAX_GRID_SIZE = 500


# ============================================================
# ImageMap
# ============================================================

class ImageMap:
    """
    图片地图对象。

    保持原有接口。
    """

    def __init__(
        self,
        image,
        binary,
        scale=1.0,
    ):
        self.image = image
        self.binary = binary
        self.scale = scale

        self.height, self.width = (
            binary.shape[:2]
        )

        # ----------------------------------------------------
        # 图片像素 / CA 元胞
        #
        # 这个属性不会改变原来的接口。
        #
        # load_image() 默认先设为 1。
        # load_image_grid() 自动计算后会更新。
        # ----------------------------------------------------

        self.pixel_per_cell = 1


# ============================================================
# 读取图片
# ============================================================

def _read_image(
    image_path,
):
    """
    读取 PNG / JPG / JPEG / BMP。

    使用 imdecode + fromfile，
    支持 macOS / Windows 中文路径。
    """

    image_path = Path(
        image_path
    )

    if not image_path.is_file():

        raise FileNotFoundError(
            f"图片文件不存在：{image_path}"
        )

    try:

        data = np.fromfile(
            str(image_path),
            dtype=np.uint8,
        )

        image = cv2.imdecode(
            data,
            cv2.IMREAD_COLOR,
        )

    except Exception as exc:

        raise ValueError(
            f"图片读取失败：{image_path}"
        ) from exc

    if image is None:

        raise ValueError(
            f"无法读取图片：{image_path}"
        )

    return image


# ============================================================
# 旋转
# ============================================================

def _rotate_image(
    image,
    rotate,
):
    """
    图片旋转。

    支持：

        0
        90
        180
        270
    """

    rotate = int(rotate) % 360

    if rotate == 0:

        return image

    if rotate == 90:

        return cv2.rotate(
            image,
            cv2.ROTATE_90_CLOCKWISE,
        )

    if rotate == 180:

        return cv2.rotate(
            image,
            cv2.ROTATE_180,
        )

    if rotate == 270:

        return cv2.rotate(
            image,
            cv2.ROTATE_90_COUNTERCLOCKWISE,
        )

    raise ValueError(
        "rotate 只支持 0、90、180、270"
    )


# ============================================================
# 裁剪
# ============================================================

def _crop_image(
    image,
    crop,
):
    """
    crop：

        (x1, y1, x2, y2)
    """

    if crop is None:

        return image

    if not isinstance(
        crop,
        (tuple, list),
    ):

        raise ValueError(
            "crop 必须是 "
            "(x1, y1, x2, y2)"
        )

    if len(crop) != 4:

        raise ValueError(
            "crop 必须包含 4 个参数"
        )

    x1, y1, x2, y2 = [
        int(value)
        for value in crop
    ]

    height, width = image.shape[:2]

    x1 = max(
        0,
        min(x1, width),
    )

    x2 = max(
        0,
        min(x2, width),
    )

    y1 = max(
        0,
        min(y1, height),
    )

    y2 = max(
        0,
        min(y2, height),
    )

    if x2 <= x1:

        raise ValueError(
            "crop 的 x2 必须大于 x1"
        )

    if y2 <= y1:

        raise ValueError(
            "crop 的 y2 必须大于 y1"
        )

    return image[
        y1:y2,
        x1:x2
    ]


# ============================================================
# 判断图片背景
# ============================================================

def _get_background_value(
    gray,
):
    """
    根据图片边缘判断背景。

    例如：

        白底黑线
            -> background ≈ 255

        黑底白线
            -> background ≈ 0
    """

    height, width = gray.shape

    border = max(
        2,
        min(
            height,
            width
        ) // 50,
    )

    top = gray[
        :border,
        :
    ]

    bottom = gray[
        -border:,
        :
    ]

    left = gray[
        :,
        :border
    ]

    right = gray[
        :,
        -border:
    ]

    values = np.concatenate(
        [
            top.reshape(-1),
            bottom.reshape(-1),
            left.reshape(-1),
            right.reshape(-1),
        ]
    )

    if len(values) == 0:

        return 255

    return float(
        np.median(values)
    )


# ============================================================
# 灰度 + 降噪
# ============================================================

def _prepare_gray(
    image,
):
    """
    图片转灰度。

    轻微高斯滤波：

        减少扫描噪声
        减少 JPG 压缩噪声
    """

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    gray = cv2.GaussianBlur(
        gray,
        (3, 3),
        0,
    )

    return gray


# ============================================================
# 获取深色候选
# ============================================================

def _extract_dark_pixels(
    gray,
    threshold,
):
    """
    提取深色结构。

    支持：

        白底黑线

    以及：

        黑底白线

    最终统一：

        白色 = 候选结构
        黑色 = 背景
    """

    background = _get_background_value(
        gray
    )

    # --------------------------------------------------------
    # 白色背景
    # --------------------------------------------------------

    if background >= 128:

        _, binary = cv2.threshold(
            gray,
            threshold,
            255,
            cv2.THRESH_BINARY_INV,
        )

    # --------------------------------------------------------
    # 黑色背景
    # --------------------------------------------------------

    else:

        _, binary = cv2.threshold(
            gray,
            threshold,
            255,
            cv2.THRESH_BINARY,
        )

    # --------------------------------------------------------
    # 轻度去除孤立噪声
    #
    # 使用连通域删除极小散点，
    # 不使用形态学开运算，
    # 避免把 1px 的细墙线磨掉。
    # --------------------------------------------------------

    binary = _remove_speckle_noise(
        binary
    )

    return binary


# ============================================================
# 去除孤立噪点
# ============================================================

def _remove_speckle_noise(
    binary,
):
    """
    删除面积极小的孤立散点。

    与形态学开运算不同：
    这里不会腐蚀细线的端点，
    只删掉真正孤立的 1~2 px 噪点。

    JPG 压缩、扫描噪声通常是这种散点。
    """

    num_labels, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )
    )

    if num_labels <= 1:

        return binary

    cleaned = np.zeros_like(
        binary
    )

    min_area = 3

    for label in range(
        1,
        num_labels,
    ):

        area = stats[
            label,
            cv2.CC_STAT_AREA,
        ]

        if area < min_area:

            continue

        cleaned[
            labels == label
        ] = 255

    return cleaned


# ============================================================
# 提取水平墙体
# ============================================================

def _extract_horizontal_walls(
    dark,
):
    """
    提取长水平结构。

    房间墙体通常：

        长
        直
        连续

    文字和家具通常：

        较短
        不连续

    因此使用长水平结构筛选。
    """

    height, width = dark.shape

    min_length = max(
        15,
        min(
            height,
            width
        ) // 25,
    )

    min_length = min(
        min_length,
        120,
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
            min_length,
            1,
        ),
    )

    horizontal = cv2.morphologyEx(
        dark,
        cv2.MORPH_OPEN,
        kernel,
        iterations=1,
    )

    return horizontal


# ============================================================
# 提取垂直墙体
# ============================================================

def _extract_vertical_walls(
    dark,
):
    """
    提取长垂直结构。
    """

    height, width = dark.shape

    min_length = max(
        15,
        min(
            height,
            width
        ) // 25,
    )

    min_length = min(
        min_length,
        120,
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (
            1,
            min_length,
        ),
    )

    vertical = cv2.morphologyEx(
        dark,
        cv2.MORPH_OPEN,
        kernel,
        iterations=1,
    )

    return vertical


# ============================================================
# 沿线深色连续性
# ============================================================

def _line_dark_contiguity(
    dark,
    x1,
    y1,
    x2,
    y2,
    half_thick=1.0,
):
    """
    估算一段线段上深色像素的“连续性”。

    真正墙体：
        沿线是一条连续的深色线，
        最长连续段几乎覆盖整条线。

    文字：
        即使笔画密集（如中文），
        沿线也是一段段断开的短笔画，
        最长连续段很短。

    用“最长连续段占比”来过滤 Hough
    误检的文字直线，比单纯看密度更稳。

    实现上画一条“带宽度”的线（而不是 1px），
    再沿线方向投影，统计连续覆盖了多少，
    这样能容忍 Hough 线相对墙的轻微错位，
    同时仍能区分文字（字符之间有断口）。

    线带宽度随墙半厚 half_thick 自适应，
    避免对小图墙太宽、对大图墙太窄。
    """

    length = float(
        np.sqrt(
            (x2 - x1) ** 2
            +
            (y2 - y1) ** 2
        )
    )

    if length <= 0:

        return 0.0

    # 线带宽度：随墙厚自适应，
    # 至少 7px，约 5 倍墙半厚。
    band_thickness = max(
        7,
        int(round(half_thick * 5.0)),
    )

    band = np.zeros_like(dark)

    cv2.line(
        band,
        (x1, y1),
        (x2, y2),
        255,
        band_thickness,
    )

    band = band & dark

    ys, xs = np.nonzero(band)

    if len(xs) < 2:

        return 0.0

    # 把带内深色像素投影到线方向，
    # 得到每个“沿线位置”是否有深色。
    dx = x2 - x1
    dy = y2 - y1

    projection = (
        (xs - x1) * dx
        +
        (ys - y1) * dy
    ) / length

    position = np.round(projection).astype(int)

    pmin = int(position.min())
    pmax = int(position.max())

    if pmax <= pmin:

        return 0.0

    covered = np.zeros(pmax - pmin + 1, np.uint8)

    covered[position - pmin] = 1

    longest = 0
    run = 0

    for value in covered:

        if value:

            run += 1

            if run > longest:

                longest = run

        else:

            run = 0

    return longest / float(len(covered))


# ============================================================
# Hough 长直线
# ============================================================

def _extract_hough_walls(
    dark,
):
    """
    使用 HoughLinesP 检测长直线。

    接受任意方向的直线：

        水平
        垂直
        斜线

    不接受：

        很短的线段
        文字边缘形成的假直线
        （靠沿线深色连续性过滤）
    """

    height, width = dark.shape

    edges = cv2.Canny(
        dark,
        50,
        150,
    )

    min_dimension = min(
        height,
        width,
    )

    min_line_length = max(
        20,
        min_dimension // 20,
    )

    max_gap = max(
        5,
        min_dimension // 100,
    )

    # 投票阈值随“最短线长”自适应：
    # 一条线的边缘点数量大致正比于线长，
    # 分辨率越高线越长、票数越多。
    hough_threshold = max(
        20,
        min_line_length,
    )

    # 墙半厚：用于连续性校验的线带宽自适应
    half_thick = _estimate_wall_half_thickness(
        dark
    )

    lines = cv2.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180,
        threshold=hough_threshold,
        minLineLength=min_line_length,
        maxLineGap=max_gap,
    )

    result = np.zeros_like(
        dark
    )

    if lines is None:

        return result

    # --------------------------------------------------------
    # 兼容不同 OpenCV 版本
    #
    # 可能：
    #
    # (N, 1, 4)
    #
    # 也可能：
    #
    # (N, 4)
    # --------------------------------------------------------

    lines = np.asarray(
        lines
    )

    if (
        lines.ndim == 3
        and
        lines.shape[1] == 1
        and
        lines.shape[2] == 4
    ):

        lines = lines.reshape(
            -1,
            4,
        )

    elif (
        lines.ndim == 2
        and
        lines.shape[1] == 4
    ):

        lines = lines.reshape(
            -1,
            4,
        )

    else:

        print(
            "⚠ HoughLinesP 返回格式异常：",
            lines.shape,
        )

        return result

    # --------------------------------------------------------
    # 线宽
    # --------------------------------------------------------

    thickness = max(
        1,
        min_dimension // 700,
    )

    thickness = min(
        thickness,
        3,
    )

    for line in lines:

        if len(line) != 4:

            continue

        x1, y1, x2, y2 = [
            int(value)
            for value in line
        ]

        dx = x2 - x1
        dy = y2 - y1

        length = float(
            np.sqrt(
                dx * dx
                +
                dy * dy
            )
        )

        if length < min_line_length:

            continue

        # ----------------------------------------------------
        # 验证：真正的墙沿线深色像素应该连续。
        #
        # 文字边缘会形成假直线，沿线深色是一段段
        # 断开的短笔画，这类直线需要过滤掉，
        # 否则文字会被误识别成一条贯穿房间的墙。
        #
        # 这条验证同时适用于水平/垂直/斜线，
        # 因此斜墙也能被正确识别。
        # ----------------------------------------------------

        if _line_dark_contiguity(
            dark,
            x1,
            y1,
            x2,
            y2,
            half_thick,
        ) < 0.6:

            continue

        cv2.line(
            result,
            (x1, y1),
            (x2, y2),
            255,
            thickness,
        )

    return result


# ============================================================
# 合并墙体候选
# ============================================================

def _build_wall_candidates(
    dark,
):
    """
    综合：

        水平长线
        垂直长线
        Hough 长直线

    """

    horizontal = (
        _extract_horizontal_walls(
            dark
        )
    )

    vertical = (
        _extract_vertical_walls(
            dark
        )
    )

    hough = (
        _extract_hough_walls(
            dark
        )
    )

    walls = cv2.bitwise_or(
        horizontal,
        vertical,
    )

    walls = cv2.bitwise_or(
        walls,
        hough,
    )

    return walls


# ============================================================
# 连接轻微断裂
# ============================================================

def _connect_walls(
    walls,
):
    """
    连接墙体中的小间隙。

    例如：

        ─────    ─────

    变成：

        ───────────────

    不使用过大的 kernel，
    防止文字和家具被连接起来。
    """

    height, width = walls.shape

    gap = max(
        2,
        min(
            height,
            width
        ) // 250,
    )

    gap = min(
        gap,
        8,
    )

    horizontal_kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (
                gap * 2 + 1,
                1,
            ),
        )
    )

    vertical_kernel = (
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (
                1,
                gap * 2 + 1,
            ),
        )
    )

    horizontal = cv2.morphologyEx(
        walls,
        cv2.MORPH_CLOSE,
        horizontal_kernel,
        iterations=1,
    )

    vertical = cv2.morphologyEx(
        walls,
        cv2.MORPH_CLOSE,
        vertical_kernel,
        iterations=1,
    )

    result = cv2.bitwise_or(
        horizontal,
        vertical,
    )

    return result


# ============================================================
# 连通域特征
# ============================================================

def _component_features(
    stats,
    num_labels,
    perimeters,
):
    """
    计算每个连通域的分类特征。

    特征：
        area      面积
        bbox_w    包围盒宽
        bbox_h    包围盒高
        cx        包围盒中心 x
        cy        包围盒中心 y
        stroke    近似笔画厚度 = 2 * area / perimeter
        elong     细长程度 = max(w,h) / min(w,h)

    stroke 的解释：

        细线      stroke ≈ 线宽
        空心文字  stroke ≈ 2 * 笔画宽
        实心块    stroke ≈ 块边长的一半

    这是区分“文字（细）”和
    “障碍物（粗实心）”的核心特征。
    """

    area = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    bbox_w = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    bbox_h = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    cx = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    cy = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    stroke = np.zeros(
        num_labels,
        dtype=np.float64,
    )
    elong = np.zeros(
        num_labels,
        dtype=np.float64,
    )

    for label in range(1, num_labels):

        a = float(
            stats[label, cv2.CC_STAT_AREA]
        )
        w = float(
            stats[label, cv2.CC_STAT_WIDTH]
        )
        h = float(
            stats[label, cv2.CC_STAT_HEIGHT]
        )

        area[label] = a
        bbox_w[label] = w
        bbox_h[label] = h
        cx[label] = (
            stats[label, cv2.CC_STAT_LEFT]
            +
            w / 2.0
        )
        cy[label] = (
            stats[label, cv2.CC_STAT_TOP]
            +
            h / 2.0
        )

        p = float(
            perimeters[label]
        )

        if p <= 0.0:

            p = 1.0

        stroke[label] = 2.0 * a / p
        elong[label] = (
            max(w, h)
            /
            max(1.0, min(w, h))
        )

    return (
        area,
        bbox_w,
        bbox_h,
        cx,
        cy,
        stroke,
        elong,
    )


# ============================================================
# 文字行聚类
# ============================================================

def _mark_text_lines(
    candidate_labels,
    cx,
    cy,
    bbox_w,
    bbox_h,
):
    """
    把水平方向相邻、高度相近的细小连通域
    聚成“文字行”。

    一行 >= 3 个候选即视为文字。

    候选已经经过“细 + 小”的筛选，
    所以这里只关注空间排列关系：

        文字通常是一排多个字符，
        而障碍物通常是孤立的一块。
    """

    text_labels = set()

    if len(candidate_labels) < 3:

        return text_labels

    candidate_labels = np.asarray(
        candidate_labels
    )

    median_h = float(
        np.median(
            bbox_h[candidate_labels]
        )
    )

    median_w = float(
        np.median(
            bbox_w[candidate_labels]
        )
    )

    # --------------------------------------------------------
    # 垂直方向允许的高度差
    # --------------------------------------------------------

    band = max(median_h, 1.0)

    # --------------------------------------------------------
    # 水平方向允许的最大字符间距
    # --------------------------------------------------------

    gap = max(6.0 * median_w, 2.0 * band)

    for label in candidate_labels.tolist():

        dy = np.abs(
            cy[candidate_labels] - cy[label]
        )

        dx = np.abs(
            cx[candidate_labels] - cx[label]
        )

        neighbors = candidate_labels[
            (dy <= band)
            &
            (dx > 0)
            &
            (dx <= gap)
        ]

        if len(neighbors) >= 2:

            text_labels.add(label)

            text_labels.update(
                neighbors.tolist()
            )

    return text_labels


# ============================================================
# 保留障碍物与短墙
# ============================================================

def _classify_solid_structures(
    dark,
):
    """
    对深色像素里的每个连通域，
    区分“墙 / 障碍物”与“文字 / 噪声”。

    返回两个掩码：

        wall_mask        细长的墙（长墙 / 短墙）
        obstacle_mask    实心障碍物（家具、柱子）

    删除：

        文字 / 数字 / 标注
        孤立噪声

    核心区分依据是笔画厚度 stroke：

        文字笔画细，stroke 小；
        障碍物实心，stroke 大；
        墙则细且细长。

    详见 _component_features 中 stroke 的解释。
    """

    height, width = dark.shape
    min_dim = min(height, width)

    num_labels, labels, stats, _ = (
        cv2.connectedComponentsWithStats(
            dark,
            connectivity=8,
        )
    )

    if num_labels <= 1:

        return (
            np.zeros_like(dark),
            np.zeros_like(dark),
        )

    # --------------------------------------------------------
    # 周长：用形态学梯度统计所有组件的边界像素，
    # 一次遍历即可，避免逐个组件做全图比较。
    # --------------------------------------------------------

    gradient = cv2.morphologyEx(
        dark,
        cv2.MORPH_GRADIENT,
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (3, 3),
        ),
    )

    perimeters = np.bincount(
        labels[gradient > 0],
        minlength=num_labels,
    ).astype(np.float64)

    (
        area,
        bbox_w,
        bbox_h,
        cx,
        cy,
        stroke,
        elong,
    ) = _component_features(
        stats,
        num_labels,
        perimeters,
    )

    # --------------------------------------------------------
    # 尺度自适应阈值
    #
    # ref 是“一个字符大约的高度”，
    # 所有阈值都相对 ref 缩放，
    # 从而适应不同分辨率的地图。
    # --------------------------------------------------------

    ref = max(10.0, min_dim / 40.0)

    noise_area = float(
        np.clip(ref * ref / 9.0, 4.0, 60.0)
    )

    text_stroke = float(
        np.clip(ref / 4.0, 3.0, 6.0)
    )

    text_area = float((2.0 * ref) ** 2)

    text_size = float(
        np.clip(2.0 * ref, 20.0, 200.0)
    )

    obstacle_area = float(
        np.clip(ref * ref / 6.0, 30.0, 400.0)
    )

    short_wall_len = float(
        np.clip(ref / 2.0, 8.0, 80.0)
    )

    # --------------------------------------------------------
    # 1. 文字行聚类
    #
    # 文字候选 = 细笔画 + 字符大小范围内的连通域。
    # --------------------------------------------------------

    max_dim = np.maximum(bbox_w, bbox_h)

    candidate = (
        (area >= noise_area)
        &
        (area <= text_area)
        &
        (stroke <= text_stroke)
        &
        (max_dim <= text_size)
    )

    candidate_labels = np.nonzero(candidate)[0]

    text_labels = _mark_text_lines(
        candidate_labels,
        cx,
        cy,
        bbox_w,
        bbox_h,
    )

    # --------------------------------------------------------
    # 2. 逐组件分类
    # --------------------------------------------------------

    wall_mask = np.zeros_like(dark)

    obstacle_mask = np.zeros_like(dark)

    for label in range(1, num_labels):

        if label in text_labels:

            continue

        if area[label] < noise_area:

            continue

        thin = stroke[label] <= text_stroke

        elongated = elong[label] >= 4.0

        # ------------------------------------------------
        # 细长 -> 墙（长墙 / 短墙都算）
        # ------------------------------------------------

        if thin and elongated:

            if max_dim[label] >= short_wall_len:

                wall_mask[labels == label] = 255

            continue

        # ------------------------------------------------
        # 细且不细长 -> 孤立文字字符
        #
        # 文字（字母、数字、中文）都是细笔画、
        # 不成行也非细长的小结构。
        #
        # 注意：这里会连同“极小的实心块”
        # （几个像素的点）一起删掉，
        # 但那种尺寸更接近噪声，
        # 而不是有意义的地图障碍物。
        # ------------------------------------------------

        if thin:

            continue

        # ------------------------------------------------
        # 粗（实心）-> 障碍物
        # ------------------------------------------------

        if area[label] >= obstacle_area:

            obstacle_mask[labels == label] = 255

    return wall_mask, obstacle_mask


# ============================================================
# 中轴骨架（距离变换局部极大）
# ============================================================

def _medial_axis(
    binary,
):
    """
    用距离变换的局部极大值求骨架（中轴）。

    相比细化算法，这个方法对“阶梯状”斜线、
    细斜线更鲁棒，不会把某几类斜墙误删成空。
    """

    if not np.any(binary):

        return binary

    distance = cv2.distanceTransform(
        (binary > 0).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )

    dilated = cv2.dilate(
        distance,
        cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (3, 3),
        ),
    )

    return (
        (distance >= dilated)
        &
        (binary > 0)
    ).astype(np.uint8) * 255


# ============================================================
# 统一墙厚
# ============================================================

def _uniformize_walls(
    walls,
    radius,
):
    """
    把所有墙统一成一致的厚度。

    步骤：

        1. 求中轴骨架
        2. 按统一半径 radius 膨胀

    最终墙厚约为 2 * radius + 1。

    这样无论原图里墙是 1px 还是 2px、
    是长墙还是短墙、是水平垂直还是斜墙，
    最终厚度都基本相同，不会出现
    “同样粗细的线识别出来有粗有细”。
    """

    if not np.any(walls):

        return walls

    skeleton = _medial_axis(
        walls
    )

    radius = max(1, int(radius))

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (
            2 * radius + 1,
            2 * radius + 1,
        ),
    )

    return cv2.dilate(
        skeleton,
        kernel,
        iterations=1,
    )


# ============================================================
# 估计墙半厚
# ============================================================

def _estimate_wall_half_thickness(
    binary,
):
    """
    估计墙的“半厚”（墙厚的一半）。

    用距离变换的低百分位（25%），
    反映最细结构的典型厚度。

    墙和文字都是细的，距离值集中在低处；
    实心障碍物内部距离值大，但在低百分位
    处影响很小，所以不会被障碍物带偏。
    """

    if not np.any(binary):

        return 1.0

    distance = cv2.distanceTransform(
        (binary > 0).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )

    values = distance[
        distance > 0
    ]

    if len(values) == 0:

        return 1.0

    half = float(
        np.percentile(
            values,
            25,
        )
    )

    return max(1.0, half)


# ============================================================
# 核心房间结构识别
# ============================================================

def _process_room_structure(
    image,
    threshold,
):
    """
    图片 -> 房间墙体 + 障碍物。

    最终只产生：

        0
        255
    """

    # --------------------------------------------------------
    # 1. 灰度
    # --------------------------------------------------------

    gray = _prepare_gray(
        image
    )

    # --------------------------------------------------------
    # 2. 提取深色候选
    # --------------------------------------------------------

    dark = _extract_dark_pixels(
        gray,
        threshold,
    )

    # --------------------------------------------------------
    # 3. 提取线结构（水平 / 垂直长线 + Hough）
    #
    # 墙彼此连成网，直接用连通域是一大块，
    # 无法区分单条墙和文字。
    #
    # 先用形态学开运算把网拆成一条条线。
    # 注意：实心障碍物也有长水平/垂直走向，
    # 会被一并保留，所以下一步用“厚度”把
    # 墙（细）和障碍物（实心）分开。
    # --------------------------------------------------------

    lines = _build_wall_candidates(
        dark
    )

    # --------------------------------------------------------
    # 4. 用“局部厚度”把线结构分成墙 / 障碍物
    #
    # 墙是细的（局部距离小），
    # 障碍物是实心的（内部局部距离大）。
    #
    # 只对 lines 做距离变换，文字不在 lines 里，
    # 所以不会被误当成障碍物。
    # --------------------------------------------------------

    half_thick = _estimate_wall_half_thickness(
        lines
    )

    thin_max = half_thick + 1.0

    distance = cv2.distanceTransform(
        (lines > 0).astype(np.uint8),
        cv2.DIST_L2,
        5,
    )

    obstacle_core = lines & (distance > thin_max)

    # 障碍物 = 实心内部 + 边缘（膨胀填回）
    fill = max(1, int(np.ceil(thin_max)))

    line_obstacles = cv2.dilate(
        obstacle_core,
        cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (2 * fill + 1, 2 * fill + 1),
        ),
    ) & lines

    line_walls = lines & cv2.bitwise_not(
        line_obstacles
    )

    # --------------------------------------------------------
    # 5. 残余（文字 / 短墙 / 小障碍物）
    # --------------------------------------------------------

    connect = max(3, min(dark.shape) // 300)
    connect = min(connect, 7)

    if connect % 2 == 0:

        connect += 1

    residue = dark & cv2.bitwise_not(
        cv2.dilate(
            lines,
            cv2.getStructuringElement(
                cv2.MORPH_RECT,
                (connect, connect),
            ),
        )
    )

    short_walls, other_obstacles = (
        _classify_solid_structures(
            residue
        )
    )

    walls = cv2.bitwise_or(
        line_walls,
        short_walls,
    )

    obstacles = cv2.bitwise_or(
        line_obstacles,
        other_obstacles,
    )

    # --------------------------------------------------------
    # 6. 连接轻微断裂
    # --------------------------------------------------------

    walls = _connect_walls(
        walls
    )

    # --------------------------------------------------------
    # 7. 统一墙厚
    #
    # 先细化成 1px 骨架，再按统一半径膨胀，
    # 保证所有墙粗细一致，不会
    # “同样粗细的线识别出来有粗有细”。
    # --------------------------------------------------------

    height, width = dark.shape

    resolution_scale = max(
        1,
        int(
            np.ceil(
                max(height, width)
                /
                TARGET_MAX_GRID_SIZE
            )
        ),
    )

    # 膨胀半径：让墙厚约为采样尺寸的 1.5 倍，
    # 这样墙在采样成元胞时能稳定占满至少一个元胞。
    radius = max(
        1,
        int(
            np.ceil(
                resolution_scale * 0.75
            )
        ),
    )

    walls = _uniformize_walls(
        walls,
        radius,
    )

    # --------------------------------------------------------
    # 8. 合并障碍物（保持实心）
    # --------------------------------------------------------

    structure = cv2.bitwise_or(
        walls,
        obstacles,
    )

    # --------------------------------------------------------
    # 9. 最终严格二值化
    # --------------------------------------------------------

    binary = np.where(
        structure > 0,
        255,
        0,
    ).astype(
        np.uint8
    )

    return binary


# ============================================================
# 自动检测图片像素比例
# ============================================================

def _auto_detect_cell_size(
    binary,
):
    """
    自动检测：

        图片多少像素
            ↓
        1 个 CA 元胞

    主要依据：

        房间墙体实际像素厚度

    原则：

        CA cell 不应该大到把细墙完全吞掉。

    同时考虑：

        图片分辨率不能过大，
        防止生成过大的 Grid。
    """

    if binary is None:

        return 1

    if binary.ndim != 2:

        raise ValueError(
            "binary 必须是二维数组"
        )

    height, width = (
        binary.shape
    )

    wall = (
        binary > 0
    ).astype(
        np.uint8
    )

    wall_pixels = int(
        np.count_nonzero(
            wall
        )
    )

    # --------------------------------------------------------
    # 没有墙
    # --------------------------------------------------------

    if wall_pixels == 0:

        print()
        print(
            "⚠ 自动比例检测："
            "没有检测到墙体。"
        )

        print(
            "使用：1 × 1 px / CA cell"
        )

        return 1

    # --------------------------------------------------------
    # 只对“细长墙体”做距离变换。
    #
    # 原因：
    #
    #     实心障碍物内部会贡献很大的距离值，
    #     会把墙厚估计得过大，导致比例失真。
    #
    #     细长墙体才是真正的“墙”，
    #     它们的距离值反映真实墙厚。
    # --------------------------------------------------------

    line_mask = cv2.bitwise_or(
        _extract_horizontal_walls(wall),
        _extract_vertical_walls(wall),
    )

    if not np.any(line_mask):

        line_mask = wall

    distance = cv2.distanceTransform(
        line_mask,
        cv2.DIST_L2,
        5,
    )

    values = distance[
        line_mask > 0
    ]

    values = values[
        values > 0
    ]

    if len(values) == 0:

        return 1

    # --------------------------------------------------------
    # 不使用最大值。
    #
    # 因为墙角、交叉点可能很厚，
    # 最大值会导致比例过大。
    #
    # 使用较低的百分位，
    # 偏向更细的墙，
    # 得到更稳定、更保守的典型墙厚。
    # --------------------------------------------------------

    half_thickness = float(
        np.percentile(
            values,
            30,
        )
    )

    estimated_wall_thickness = (
        half_thickness * 2.0
    )

    # --------------------------------------------------------
    # 一个 CA cell 不超过典型墙厚的一半，
    # 保证墙在采样后至少占 2 个元胞宽。
    # --------------------------------------------------------

    cell_size = int(
        np.floor(
            estimated_wall_thickness
            / 2.0
        )
    )

    cell_size = max(
        1,
        cell_size,
    )

    # --------------------------------------------------------
    # 上限。
    # --------------------------------------------------------

    cell_size = min(
        cell_size,
        10,
    )

    # --------------------------------------------------------
    # 图片太大时限制 Grid 尺寸。
    #
    # 例如：
    #
    # 5000 × 4000
    #
    # 不适合直接生成：
    #
    # 5000 × 4000
    #
    # 的 CA Grid。
    # --------------------------------------------------------

    max_dimension = max(
        height,
        width,
    )

    if (
        max_dimension
        >
        TARGET_MAX_GRID_SIZE
    ):

        resolution_scale = int(
            np.ceil(
                max_dimension
                /
                TARGET_MAX_GRID_SIZE
            )
        )

        cell_size = max(
            cell_size,
            resolution_scale,
        )

    # --------------------------------------------------------
    # 输出检测信息
    # --------------------------------------------------------

    grid_width = int(
        np.ceil(
            width
            /
            cell_size
        )
    )

    grid_height = int(
        np.ceil(
            height
            /
            cell_size
        )
    )

    print()
    print(
        "=" * 60
    )

    print(
        "自动图片像素比例检测"
    )

    print(
        "=" * 60
    )

    print(
        f"原始图片："
        f"{width} × {height} px"
    )

    print(
        f"检测墙体像素数："
        f"{wall_pixels}"
    )

    print(
        f"估计典型墙体厚度："
        f"{estimated_wall_thickness:.2f} px"
    )

    print(
        f"自动像素比例："
        f"{cell_size} × "
        f"{cell_size} px / CA cell"
    )

    print(
        f"预计 Grid："
        f"{grid_width} × "
        f"{grid_height}"
    )

    print(
        "=" * 60
    )

    return cell_size


# ============================================================
# 主接口 load_image
# ============================================================

def load_image(
    image_path,
    rotate=0,
    crop=None,
    threshold=200,
):
    """
    图片地图导入。

    原有接口保持不变。

    参数：

        image_path：
            PNG/JPG 图片路径。

        rotate：
            0 / 90 / 180 / 270。

        crop：
            (x1, y1, x2, y2)。

        threshold：
            二值化阈值。

    返回：

        ImageMap
    """

    # --------------------------------------------------------
    # 1. 读取
    # --------------------------------------------------------

    image = _read_image(
        image_path
    )

    # --------------------------------------------------------
    # 2. 旋转
    # --------------------------------------------------------

    image = _rotate_image(
        image,
        rotate,
    )

    # --------------------------------------------------------
    # 3. 裁剪
    # --------------------------------------------------------

    image = _crop_image(
        image,
        crop,
    )

    # --------------------------------------------------------
    # 4. threshold 检查
    # --------------------------------------------------------

    try:

        threshold = int(
            threshold
        )

    except (
        TypeError,
        ValueError,
    ) as exc:

        raise ValueError(
            "threshold 必须是整数"
        ) from exc

    threshold = max(
        0,
        min(
            threshold,
            255,
        ),
    )

    # --------------------------------------------------------
    # 5. 房间结构识别
    # --------------------------------------------------------

    binary = _process_room_structure(
        image,
        threshold,
    )

    # --------------------------------------------------------
    # 6. 创建 ImageMap
    # --------------------------------------------------------

    result = ImageMap(
        image=image,
        binary=binary,
        scale=1.0,
    )

    return result


# ============================================================
# 保存二值图
# ============================================================

def save_binary(
    binary,
    path,
):
    """
    保存二值化结果。

    接口保持不变。

    支持中文路径。
    """

    path = Path(
        path
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # --------------------------------------------------------
    # 强制保证只有 0 / 255
    # --------------------------------------------------------

    binary = np.where(
        binary > 0,
        255,
        0,
    ).astype(
        np.uint8
    )

    success, encoded = cv2.imencode(
        ".png",
        binary,
    )

    if not success:

        raise IOError(
            f"二值化图片编码失败：{path}"
        )

    encoded.tofile(
        str(path)
    )

    print(
        "二值化图片保存成功：",
        path
    )

    return path


# ============================================================
# 显示图片
# ============================================================

def show_image(
    image,
    title="Image",
):
    """
    显示图片。

    保持原有功能。
    """

    cv2.imshow(
        title,
        image,
    )

    cv2.waitKey(0)

    cv2.destroyAllWindows()


# ============================================================
# PNG / JPG -> Grid
# ============================================================

def load_image_grid(
    image_path,
    rotate=0,
    crop=None,
    threshold=200,
    cell_size=None,
):
    """
    图片直接转换 Grid。

    参数：

        image_path：
            PNG/JPG 图片路径。

        rotate：
            0 / 90 / 180 / 270。

        crop：
            (x1, y1, x2, y2)。

        threshold：
            图片二值化阈值。

        cell_size：

            None：
                自动检测。

            正整数：
                手动指定图片像素比例。

    例如：

        cell_size=1

            1 × 1 px
                ↓
            1 CA cell

        cell_size=5

            5 × 5 px
                ↓
            1 CA cell

        cell_size=10

            10 × 10 px
                ↓
            1 CA cell
    """

    # --------------------------------------------------------
    # 1. 图片 -> binary
    # --------------------------------------------------------

    image_map = load_image(
        image_path=image_path,
        rotate=rotate,
        crop=crop,
        threshold=threshold,
    )

    # --------------------------------------------------------
    # 2. 自动 / 手动确定比例
    # --------------------------------------------------------

    if cell_size is None:

        cell_size = _auto_detect_cell_size(
            image_map.binary
        )

    else:

        try:

            cell_size = int(
                cell_size
            )

        except (
            TypeError,
            ValueError,
        ) as exc:

            raise ValueError(
                "cell_size 必须是正整数，"
                "或者使用 None 自动检测。"
            ) from exc

        if cell_size <= 0:

            raise ValueError(
                "cell_size 必须大于 0。"
            )

        print()
        print(
            "=" * 60
        )

        print(
            "使用手动图片像素比例"
        )

        print(
            f"{cell_size} × "
            f"{cell_size} px / CA cell"
        )

        print(
            "=" * 60
        )

    # --------------------------------------------------------
    # 3. 保存到 ImageMap
    # --------------------------------------------------------

    image_map.pixel_per_cell = (
        cell_size
    )

    # --------------------------------------------------------
    # 4. 调用项目已有 binary_to_grid
    #
    # 不修改 binary_to_grid.py。
    # --------------------------------------------------------

    from map_import.binary_to_grid import (
        binary_to_grid
    )

    grid = binary_to_grid(
        image_map.binary,
        cell_size=cell_size,
    )

    # --------------------------------------------------------
    # 5. 保存图片比例信息
    #
    # 不修改 Grid schema。
    #
    # 这里只动态增加一个运行时属性。
    # --------------------------------------------------------

    grid.image_pixel_per_cell = (
        cell_size
    )

    # --------------------------------------------------------
    # 6. 设置项目统一的 CA 物理尺寸
    # --------------------------------------------------------

    grid.cell_size = (
        REAL_CELL_SIZE
    )

    # --------------------------------------------------------
    # 7. 输出信息
    # --------------------------------------------------------

    print()
    print(
        "=" * 60
    )

    print(
        "PNG / JPG -> Grid 完成"
    )

    print(
        f"图片："
        f"{Path(image_path).name}"
    )

    print(
        f"图片像素比例："
        f"{cell_size} px / CA cell"
    )

    print(
        f"Grid："
        f"{grid.width} × "
        f"{grid.height}"
    )

    print(
        f"CA 元胞物理尺寸："
        f"{REAL_CELL_SIZE} m × "
        f"{REAL_CELL_SIZE} m"
    )

    print(
        "=" * 60
    )

    return grid
