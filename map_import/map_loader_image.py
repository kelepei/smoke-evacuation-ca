import cv2
import numpy as np


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



def load_image(
        image_path,
        rotate=0,
        crop=None,
        threshold=200
):
    """
    图片地图导入

    参数：

    image_path:
        图片路径

    rotate:
        旋转角度
        0/90/180/270

    crop:
        裁剪区域
        (x1,y1,x2,y2)

    threshold:
        二值化阈值


    返回:
        ImageMap
    """


    # ======================
    # 1.读取图片
    # ======================

    img=cv2.imread(
        image_path
    )


    if img is None:
        raise FileNotFoundError(
            image_path
        )



    # ======================
    # 2.灰度化
    # ======================

    gray=cv2.cvtColor(
        img,
        cv2.COLOR_BGR2GRAY
    )



    # ======================
    # 3.自动判断黑白背景
    # ======================

    mean=np.mean(gray)


    if mean>127:

        # 白底黑线

        binary=cv2.threshold(
            gray,
            threshold,
            255,
            cv2.THRESH_BINARY_INV
        )[1]


    else:

        # 黑底白线

        binary=cv2.threshold(
            gray,
            threshold,
            255,
            cv2.THRESH_BINARY
        )[1]



    # ======================
    # 4.裁剪
    # ======================

    if crop:

        x1,y1,x2,y2=crop

        binary=binary[
            y1:y2,
            x1:x2
        ]



    # ======================
    # 5.旋转
    # ======================

    if rotate==90:

        binary=cv2.rotate(
            binary,
            cv2.ROTATE_90_CLOCKWISE
        )

    elif rotate==180:

        binary=cv2.rotate(
            binary,
            cv2.ROTATE_180
        )

    elif rotate==270:

        binary=cv2.rotate(
            binary,
            cv2.ROTATE_90_COUNTERCLOCKWISE
        )



    return ImageMap(
        img,
        binary
    )



def save_binary(
        binary,
        path
):

    """
    保存二值化结果
    """

    cv2.imwrite(
        path,
        binary
    )
