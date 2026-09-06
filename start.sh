#!/bin/bash
# 个人知识管理工作台启动脚本（Git Bash / Linux / macOS 环境）
cd "$(dirname "$0")"

# 检测 Python（需已安装并加入 PATH）
PY="python"
command -v python >/dev/null 2>&1 || { echo "[错误] 未检测到 Python，请先安装 Python 3.10+"; exit 1; }

# 数据目录：优先 .kb_data_dir 配置，否则项目内 data/
KB_DATA_DIR="$(pwd)/data"
if [ -f "$(pwd)/.kb_data_dir" ]; then
  KB_DATA_DIR="$(head -1 "$(pwd)/.kb_data_dir" | tr -d '\r')"
fi
mkdir -p "$KB_DATA_DIR"
export KB_DATA_DIR

echo "========================================"
echo "  个人知识管理工作台  正在启动..."
echo "  访问 http://127.0.0.1:8787"
echo "  Ctrl+C 停止服务"
echo "========================================"
"$PY" app.py
