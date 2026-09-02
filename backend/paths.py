# -*- coding: utf-8 -*-
"""
统一数据路径解析：实现「数据与程序本体隔离」。
================================================
数据目录优先级：
  1. 环境变量 KB_DATA_DIR（start.bat / start.sh 注入，指向外置数据目录）
  2. 程序根目录下 data/（兼容旧部署 / 直接 python app.py 运行）

数据目录独立于程序目录后，更新/替换程序代码不会影响任何用户数据。
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_data_dir() -> str:
    """返回数据根目录（自动创建由各使用方负责，或在此确保存在）。"""
    env = os.environ.get("KB_DATA_DIR", "").strip()
    if env:
        return env
    return os.path.join(PROJECT_ROOT, "data")


def get_docs_dir() -> str:
    return os.path.join(get_data_dir(), "documents")


def get_db_path() -> str:
    return os.path.join(get_data_dir(), "knowledge.db")


def ensure_data_dir() -> str:
    d = get_data_dir()
    os.makedirs(d, exist_ok=True)
    return d
