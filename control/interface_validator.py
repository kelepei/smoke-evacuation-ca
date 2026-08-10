import json



def validate_people_file(

        people_file,

        map_file

):


    with open(

        people_file,

        "r",

        encoding="utf-8"

    ) as f:

        people=json.load(f)



    with open(

        map_file,

        "r",

        encoding="utf-8"

    ) as f:

        map_data=json.load(f)



    persons=people["persons"]



    width=map_data["width"]

    height=map_data["height"]



    free_cells=set()


    for cell in map_data["cells"]:


        if cell["type"]=="free":

            free_cells.add(

                (
                    cell["x"],
                    cell["y"]
                )

            )



    error=[]



    for p in persons:


        pid=p["id"]



        # 必要字段

        required=[

            "id",

            "x",

            "y",

            "profile",

            "group_id"

        ]



        for r in required:


            if r not in p:

                error.append(

                    f"{pid}缺少字段:{r}"

                )



        x=p["x"]

        y=p["y"]



        # 坐标范围

        if (

            x<0

            or x>=width

            or y<0

            or y>=height

        ):

            error.append(

                f"{pid}坐标越界"

            )



        # 是否可通行

        if (

            x,y

        ) not in free_cells:


            error.append(

                f"{pid}位置不是free元胞"

            )



    if error:


        print("================")

        print("接口检查失败")


        for e in error:

            print(e)


        print("================")


        return False



    else:


        print("================")

        print(
            "接口检查通过"
        )

        print(
            "人数:",
            len(persons)
        )

        print("================")


        return True
