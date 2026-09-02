@echo off
chcp 65001 >nul
title 一键备份 · 个人知识管理工作台
cd /d "%~dp0"

set PY=C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\python.exe
if not exist "%PY%" set PY=python

echo.
echo  ========================================
echo    增量备份（内容去重，相同文件不重复存储）
echo    备份位置: backup\
echo  ========================================
echo.
"%PY%" tools\backup.py backup %*
echo.
pause
