"""
PNG 图片地图导入模块
"""

import cv2
import numpy as np
from pathlib import Path


REAL_CELL_SIZE = 0.5


class ImageMap:

    def __init__(
            self,
            image,
            binary,
            scale=1.0
    ):
        self.image = image
        self.binary = binary
        self.scale = scale

        self.height, self.width = binary.shape


def _get_background_value(gray):

    height, width = gray.shape

    border_size = max(
        1,
        min(height, width) // 50
    )

    top = gray[
        :border_size,
        :
    ].reshape(-1)

    bottom = gray[
        -border_size:,
        :
    ].reshape(-1)

    left = gray[
        :,
        :border_size
    ].reshape(-1)

    right = gray[
        :,
        -border_size:
    ].reshape(-1)

    pixels = np.concatenate([
        top,
        bottom,
        left,
        right
    ])

    return float(
        np.median(pixels)
    )


def load_image(
        image_path,
        rotate=0,
        crop=None,
        threshold=200
):
    """
    图片地图导入
    """

    img = cv2.imread(
        str(image_path)
    )

    if img is None:
        raise FileNotFoundError(
            f"无法读取图片: {image_path}"
        )

    gray = cv2.cvtColor(
        img,
        cv2.COLOR_BGR2GRAY
    )

    # 裁剪
    if crop is not None:

        if len(crop) != 4:
            raise ValueError(
                "crop 必须是 (x1, y1, x2, y2)"
            )

        x1, y1, x2, y2 = map(
            int,
            crop
        )

        image_height, image_width = (
            gray.shape
        )

        x1 = max(
            0,
            min(x1, image_width)
        )

        x2 = max(
            0,
            min(x2, image_width)
        )

        y1 = max(
            0,
            min(y1, image_height)
        )

        y2 = max(
            0,
            min(y2, image_height)
        )

        if x2 <= x1 or y2 <= y1:
            raise ValueError(
                f"无效的 crop 区域: {crop}"
            )

        gray = gray[
            y1:y2,
            x1:x2
        ]

    # 旋转
    if rotate == 90:

        gray = cv2.rotate(
            gray,
            cv2.ROTATE_90_CLOCKWISE
        )

    elif rotate == 180:

        gray = cv2.rotate(
            gray,
            cv2.ROTATE_180
        )

    elif rotate == 270:

        gray = cv2.rotate(
            gray,
            cv2.ROTATE_90_COUNTERCLOCKWISE
        )

    elif rotate != 0:

        raise ValueError(
            "rotate 只支持 0、90、180、270"
        )

    if gray.size == 0:
        raise ValueError(
            "处理后的图片为空"
        )

    # 去噪
    gray_filtered = cv2.GaussianBlur(
        gray,
        (3, 3),
        0
    )

    # 背景判断
    background_value = (
        _get_background_value(
            gray_filtered
        )
    )

    # OTSU
    otsu_threshold, _ = cv2.threshold(
        gray_filtered,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )

    if (
        otsu_threshold <= 0
        or
        otsu_threshold >= 255
    ):
        used_threshold = float(
            threshold
        )
    else:
        used_threshold = float(
            otsu_threshold
        )

    # 统一：
    # 255 = WALL
    # 0   = FREE

    if background_value >= used_threshold:

        binary = cv2.threshold(
            gray_filtered,
            used_threshold,
            255,
            cv2.THRESH_BINARY_INV
        )[1]

    else:

        binary = cv2.threshold(
            gray_filtered,
            used_threshold,
            255,
            cv2.THRESH_BINARY
        )[1]

    # 去除小噪声
    kernel = np.ones(
        (3, 3),
        np.uint8
    )

    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        kernel,
        iterations=1
    )

    # 连接小断裂
    binary = cv2.morphologyEx(
        binary,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=1
    )

    binary = np.where(
        binary > 0,
        255,
        0
    ).astype(
        np.uint8
    )

    return ImageMap(
        img,
        binary,
        scale=1.0
    )


def load_image_grid(
        image_path,
        rotate=0,
        crop=None,
        threshold=200
):
    """
    PNG/JPG → Grid
    """

    image_map = load_image(
        image_path=image_path,
        rotate=rotate,
        crop=crop,
        threshold=threshold
    )

    from map_import.binary_to_grid import (
        binary_to_grid
    )

    grid = binary_to_grid(
        image_map.binary
    )

    grid.cell_size = REAL_CELL_SIZE

    return grid


def save_binary(
        binary,
        path=None
):
    """
    保存二值化图片。

    默认保存到：

        maps/processed/
    """

    if path is None:

        path = (
            Path(__file__).resolve().parent.parent
            / "maps"
            / "processed"
            / "binary.png"
        )

    path = Path(path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    success = cv2.imwrite(
        str(path),
        binary
    )

    if not success:
        raise IOError(
            f"二值化图片保存失败: {path}"
        )

    print(
        "二值化图片保存成功:",
        path
    )
