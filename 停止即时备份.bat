@echo off
chcp 65001 >nul
REM 停止 GitHub 即时备份监听
set "PIDFILE=%USERPROFILE%\.workbuddy\github_watch.pid"
if not exist "%PIDFILE%" (
    echo 即时备份未在运行（无 PID 记录）。
    pause
    exit /b 0
)
set /p PID=<"%PIDFILE%"
taskkill /PID %PID% /F >nul 2>&1
if %errorlevel%==0 (
    echo 已停止即时备份 PID=%PID%。
    del "%PIDFILE%" >nul 2>&1
) else (
    echo 进程可能已退出，清理 PID 记录。
    del "%PIDFILE%" >nul 2>&1
)
pause
