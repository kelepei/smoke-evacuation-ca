import json

from core.schema import CellType, SemanticType



def validate_map(data):
    """
    检查上传的JSON地图是否符合接口要求

    返回:
        True  格式正确
        False 格式错误
    """



    # ==========================
    # 1. 检查基础字段
    # ==========================

    required_keys = [

        "name",

        "width",

        "height",

        "cell_size",

        "cells"

    ]


    for key in required_keys:

        if key not in data:

            print(
                "缺少字段:",
                key
            )

            return False



    # ==========================
    # 2. 检查地图尺寸
    # ==========================


    if not isinstance(data["width"], int):

        print("width必须为整数")

        return False



    if not isinstance(data["height"], int):

        print("height必须为整数")

        return False



    if data["width"] <= 0:

        print("width错误")

        return False



    if data["height"] <= 0:

        print("height错误")

        return False



    # ==========================
    # 3. 检查cells
    # ==========================


    if not isinstance(
            data["cells"],
            list
    ):

        print(
            "cells必须为列表"
        )

        return False



    # 获取schema中允许的类型

    cell_types = [

        item.value

        for item in CellType

    ]



    semantic_types = [

        item.value

        for item in SemanticType

    ]



    for cell in data["cells"]:



        # ----------------------
        # 元胞基本字段
        # ----------------------

        for key in [

            "x",

            "y",

            "type"

        ]:


            if key not in cell:

                print(
                    "元胞缺少字段:",
                    key
                )

                return False



        # ----------------------
        # 坐标范围
        # ----------------------

        if cell["x"] < 0 or cell["x"] >= data["width"]:

            print(
                "x坐标越界:",
                cell["x"]
            )

            return False



        if cell["y"] < 0 or cell["y"] >= data["height"]:

            print(
                "y坐标越界:",
                cell["y"]
            )

            return False



        # ----------------------
        # 类型检查
        # ----------------------

        if cell["type"] not in cell_types:

            print(
                "错误Cell类型:",
                cell["type"]
            )

            return False

        # ----------------------
        # semantic检查
        # ----------------------

    if "semantic" in cell:

        # semantic允许为空
        if cell["semantic"] is None:

            pass

        else:

            if cell["semantic"] not in semantic_types:
                print(
                    "错误semantic类型:",
                    cell["semantic"]
                )

                return False



    return True
