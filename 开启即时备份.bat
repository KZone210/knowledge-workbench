@echo off
chcp 65001 >nul
REM 开启 GitHub 即时备份：代码一改动（约 1 分钟内）自动上传云端
set "PY=C:\Users\King\.workbuddy\binaries\python\versions\3.13.12\pythonw.exe"
set "WD=D:\agent\个人知识管理工作平台\tools\github_watch.py"
if exist "%USERPROFILE%\.workbuddy\github_watch.pid" (
    set /p OLD=<"%USERPROFILE%\.workbuddy\github_watch.pid"
    tasklist /FI "PID eq %OLD%" 2>nul | find "%OLD%" >nul && (
        echo 即时备份已在运行 PID=%OLD%，无需重复启动。
        pause
        exit /b 0
    )
)
start "" "%PY%" "%WD%"
echo 即时备份已启动（后台无窗口运行）。
echo 日志：D:\agent\个人知识管理工作平台\github_watch.log
pause
