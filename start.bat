@echo off
chcp 65001 >nul
title 个人知识管理工作台
cd /d "%~dp0"

set PY=C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\python.exe
if not exist "%PY%" set PY=python

rem ===== 数据目录外置：程序与用户数据隔离（更新程序不影响数据）=====
set KB_DATA_DIR=D:\agent\知识工作台_数据
if not exist "%KB_DATA_DIR%" mkdir "%KB_DATA_DIR%"

rem ===== 检测服务是否已在运行（端口 8787 被占用 = 已在运行）=====
netstat -ano | findstr ":8787" | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo.
    echo  [提示] 服务已在运行，直接打开浏览器...
    echo.
    start "" "http://127.0.0.1:8787/"
    exit /b 0
)

echo.
echo  ========================================
echo    个人知识管理工作台  正在启动...
echo    浏览器将自动打开  http://127.0.0.1:8787
echo    请保持本窗口开启，关闭即停止服务
echo    若页面打不开，检查本窗口是否还在运行
echo  ========================================
echo.
start "" "http://127.0.0.1:8787/"
"%PY%" app.py
pause
