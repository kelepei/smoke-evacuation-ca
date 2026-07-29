import tkinter as tk
from tkinter import filedialog



def select_map_file():


    root=tk.Tk()

    root.withdraw()



    file_path=filedialog.askopenfilename(

        title="选择地图文件",

        filetypes=[

            ("地图文件",
             "*.json *.csv")

        ]

    )


    root.destroy()


    return file_path
