import json
import random
import math
from collections import defaultdict



# ==================================================
# JSON读取
# ==================================================

def load_json(path):

    with open(
        path,
        "r",
        encoding="utf-8"
    ) as f:

        return json.load(f)



# ==================================================
# 根据角色匹配区域
# ==================================================

PROFILE_SEMANTIC = {


    # 教室相关

    "student":[
        "classroom",
        "dorm",
        "library"
    ],


    "teacher":[
        "classroom"
    ],


    # 工作人员

    "staff":[
        "corridor",
        "hall"
    ],


    "security":[
        "hall",
        "corridor"
    ],


    # 商业区域

    "customer":[
        "shop",
        "hall"
    ],


    "child":[
        "shop",
        "hall"
    ],


    "elderly":[
        "hall",
        "hospital"
    ],


    # 医院

    "patient":[
        "hospital"
    ],


    "doctor":[
        "hospital"
    ],


    "family_member":[
        "hospital"
    ]

}





# ==================================================
# 获取可用元胞
# ==================================================

def get_available_cells(
        map_data
):


    cells=[]


    for cell in map_data["cells"]:


        if (

            cell["type"]=="free"

        ):


            cells.append(

                (
                    cell["x"],
                    cell["y"],
                    cell.get(
                        "semantic",
                        ""
                    )

                )

            )


    return cells





# ==================================================
# 根据角色筛选区域
# ==================================================

def select_cells_for_profile(
        cells,
        profile
):


    target_semantics = PROFILE_SEMANTIC.get(

        profile,

        ["hall"]

    )



    result=[]



    for x,y,semantic in cells:


        if semantic in target_semantics:

            result.append(
                (
                    x,
                    y
                )
            )



    # 没找到对应区域

    if len(result)==0:


        result=[

            (
                x,
                y
            )

            for x,y,_ in cells

        ]



    return result





# ==================================================
# 计算附近元胞
# ==================================================

def get_nearby_cells(

        center,

        candidates,

        number

):


    cx,cy=center


    distance_cells=[]



    for cell in candidates:


        x,y=cell


        d=math.sqrt(

            (x-cx)**2

            +

            (y-cy)**2

        )


        distance_cells.append(

            (
                d,
                cell
            )

        )



    distance_cells.sort(

        key=lambda x:x[0]

    )


    return [

        c

        for _,c in distance_cells[:number]

    ]





# ==================================================
# 群体位置生成
# ==================================================

def generate_group_position(

        members,

        candidates,

        occupied

):


    group_size=len(members)



    # 找一个中心

    random.shuffle(

        candidates

    )



    for center in candidates:



        nearby=get_nearby_cells(

            center,

            candidates,

            group_size*5

        )



        available=[

            c

            for c in nearby

            if c not in occupied

        ]



        if len(available)>=group_size:


            selected=random.sample(

                available,

                group_size

            )


            return selected



    return []





# ==================================================
# 主函数
# ==================================================

def allocate_people_position(

        people_file,

        map_file,

        output_file

):


    people_data=load_json(

        people_file

    )


    map_data=load_json(

        map_file

    )



    people=people_data["persons"]



    cells=get_available_cells(

        map_data

    )



    # ==========================
    # 按group_id分组
    # ==========================


    groups=defaultdict(list)



    for person in people:


        groups[

            str(
                person["group_id"]
            )

        ].append(person)





    occupied=set()



    # ==========================
    # 一个群体一个区域
    # ==========================


    for group_id,members in groups.items():


        profile=members[0]["profile"]



        candidates=select_cells_for_profile(

            cells,

            profile

        )



        positions=generate_group_position(

            members,

            candidates,

            occupied

        )



        # 如果失败，单独找

        if len(positions)==0:


            random.shuffle(

                candidates

            )


            positions=candidates[:len(members)]



        for person,pos in zip(

            members,

            positions

        ):


            person["x"]=pos[0]

            person["y"]=pos[1]


            occupied.add(pos)



    # ==========================
    # 输出
    # ==========================


    with open(

        output_file,

        "w",

        encoding="utf-8"

    ) as f:


        json.dump(

            people_data,

            f,

            indent=4,

            ensure_ascii=False

        )


    print("================")

    print(
        "人员初始位置生成完成"
    )

    print(
        "输出:",
        output_file
    )

    print("================")
