#!/bin/bash
# 个人知识管理工作台启动脚本（Git Bash 环境）
cd "$(dirname "$0")"

PY="C:/Users/King/.workbuddy/binaries/python/envs/kb/Scripts/python.exe"
[ -x "$PY" ] || PY=python

# 数据目录外置：程序与用户数据隔离（更新程序不影响数据）
export KB_DATA_DIR="D:/agent/知识工作台_数据"
mkdir -p "$KB_DATA_DIR"

echo "========================================"
echo "  个人知识管理工作台  正在启动..."
echo "  访问 http://127.0.0.1:8787"
echo "  Ctrl+C 停止服务"
echo "========================================"
"$PY" app.py
