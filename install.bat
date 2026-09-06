@echo off
chcp 65001 >nul
title 个人知识管理工作台 · 一键安装
cd /d "%~dp0"

echo.
echo  ================================================
echo     个人知识管理工作台 · 一键安装启动
echo  ================================================
echo.

rem ============ 1) 检测 Python ============
set PY=python
where python >nul 2>&1
if errorlevel 1 (
    echo  [X] 未检测到 Python
    echo  请先安装 Python 3.10+，安装时勾选 "Add Python to PATH"
    echo  下载地址: https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)
for /f "delims=" %%v in ('python --version 2^>^&1') do echo  [1/3] 检测到 %%v

rem ============ 2) 安装依赖 ============
echo.
echo  [2/3] 安装依赖（首次约 1-3 分钟，请耐心等待）...
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo  [X] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

rem ============ 3) 生成快捷方式 + 启动 ============
echo.
echo  [3/3] 生成快捷方式...
"%PY%" tools\make_shortcut.py >nul 2>&1
if errorlevel 1 (
    echo   （快捷方式生成失败，可稍后手动运行 python tools\make_shortcut.py）
)

echo.
echo  ================================================
echo     安装完成！正在启动应用...
echo     以后双击项目目录下的「知识工作台.lnk」即可
echo  ================================================
echo.

rem 用 pythonw 无窗口启动（不闪终端）；找不到 pythonw 则回退 python
set PYTHONW=pythonw
where pythonw >nul 2>&1
if errorlevel 1 set PYTHONW=%PY%
start "" "%PYTHONW%" tools\kb_launcher.py

timeout /t 3 >nul
exit /b 0
