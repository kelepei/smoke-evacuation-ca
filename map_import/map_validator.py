def validate_map(data):


    # 基础字段

    required_fields=[

        "width",

        "height",

        "cell_size",

        "cells"

    ]


    for field in required_fields:


        if field not in data:


            raise ValueError(
                f"地图缺少字段:{field}"
            )




    # 检查元胞

    for cell in data["cells"]:


        required_cell=[

            "x",

            "y",

            "type"

        ]


        for key in required_cell:


            if key not in cell:


                raise ValueError(
                    f"元胞缺少字段:{key}"
                )




    # 检查尺寸

    if data["width"]<=0:

        raise ValueError(
            "地图宽度错误"
        )


    if data["height"]<=0:

        raise ValueError(
            "地图高度错误"
        )



    return True
