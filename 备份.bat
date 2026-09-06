@echo off
chcp 65001 >nul
title 一键备份 · 个人知识管理工作台
cd /d "%~dp0"

set PY=python
where python >nul 2>&1
if errorlevel 1 (
    echo  [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
    pause
    exit /b 1
)

echo.
echo  ========================================
echo    增量备份（内容去重，相同文件不重复存储）
echo    备份位置: backup\
echo  ========================================
echo.
"%PY%" tools\backup.py backup %*
echo.
pause
