@echo off
chcp 65001 >nul
title 个人知识管理工作台
cd /d "%~dp0"

rem ===== 检测 Python（需已安装并加入 PATH）=====
set PY=python
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  [错误] 未检测到 Python，请先安装 Python 3.10+ 并勾选 "Add to PATH"
    echo.
    pause
    exit /b 1
)

rem ===== 数据目录：优先 .kb_data_dir 配置，否则项目内 data/ =====
set KB_DATA_DIR=%~dp0data
if exist "%~dp0.kb_data_dir" set /p KB_DATA_DIR=<"%~dp0.kb_data_dir"
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
