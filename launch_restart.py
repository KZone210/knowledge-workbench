# -*- coding: utf-8 -*-
"""临时启动器：设置数据目录与端口后启动 app.py（由安全加固重启使用）"""
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# 数据目录：优先 .kb_data_dir 配置，否则项目内 data/
_data_dir = os.path.join(BASE, "data")
_cfg = os.path.join(BASE, ".kb_data_dir")
if os.path.exists(_cfg):
    with open(_cfg, encoding="utf-8") as f:
        v = f.read().strip()
        if v:
            _data_dir = v
os.environ["KB_DATA_DIR"] = _data_dir
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
