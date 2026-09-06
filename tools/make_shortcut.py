# -*- coding: utf-8 -*-
"""生成工作区内的「知识工作台.lnk」标准快捷方式（双击即启动应用）。

为什么不用 pylnk3：pylnk3 的写入格式有缺陷（自产文件连自己都无法解析回读，
ExtraData 越界），Windows 双击无反应。改用 pywin32 (WScript.Shell COM)，
由 Windows 自身生成标准 .lnk —— 双击可靠触发。

用法：python tools/make_shortcut.py [快捷方式路径]
默认生成到项目根目录「知识工作台.lnk」。
"""
import os
import sys

import pythoncom
import win32com.client

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_DIR = os.path.join(os.path.dirname(sys.executable))  # 兼容：由 kb venv python 运行
# 目标解释器固定指向 kb venv 的 pythonw（无窗口）
PYTHONW = r"C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\pythonw.exe"
LAUNCHER = os.path.join(BASE, "tools", "kb_launcher.py")
ICON = os.path.join(BASE, "kb_icon.ico")
LNK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE, "知识工作台.lnk")

if not os.path.exists(PYTHONW):
    raise SystemExit(f"找不到 pythonw.exe: {PYTHONW}")

pythoncom.CoInitialize()
try:
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortCut(LNK)
    sc.Targetpath = PYTHONW
    sc.Arguments = '"%s"' % LAUNCHER
    sc.WorkingDirectory = BASE
    sc.IconLocation = "%s,0" % ICON
    sc.Description = "个人知识管理工作台（双击打开）"
    sc.WindowStyle = 7
    sc.Save()
finally:
    pythoncom.CoUninitialize()

print(f"已生成快捷方式: {LNK} ({os.path.getsize(LNK)} bytes)")
print(f"目标: {PYTHONW}")
print(f"参数: \"{LAUNCHER}\"")
