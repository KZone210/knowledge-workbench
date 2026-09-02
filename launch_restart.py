# -*- coding: utf-8 -*-
"""临时启动器：设置外置数据目录与端口后启动 app.py（由安全加固重启使用）"""
import os
import subprocess
import sys

BASE = r"D:\agent\个人知识管理工作平台"
os.environ["KB_DATA_DIR"] = r"D:\agent\知识工作台_数据"
os.environ["PORT"] = "8787"

with open(os.path.join(BASE, "server_run.out.log"), "w", encoding="utf-8") as out, \
     open(os.path.join(BASE, "server_run.err.log"), "w", encoding="utf-8") as err:
    proc = subprocess.run(
        [sys.executable, "app.py"],
        cwd=BASE,
        stdout=out,
        stderr=err,
    )
sys.exit(proc.returncode)
