#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 即时备份监听器：代码文件一有变化，自动上传到云端仓库。

实现：
  - 周期性扫描项目代码文件（mtime+size 指纹，复用 github_sync 的收集/排除规则）
  - 检测到变化 → 短暂防抖（避免编辑器连续保存风暴）→ 调用 tools/github_sync.py 上传
  - 上传失败自动退避重试；期间新改动会累积，恢复后一次性补传
  - 本地新增/删除文件同样会被同步（新增=上传，删除=云端同步移除）

启动：
  双击「开启即时备份.bat」，或命令行：
    pythonw tools/github_watch.py            # 无窗口后台
    python  tools/github_watch.py --interval 3   # 3 秒扫描（调试用）

停止：双击「停止即时备份.bat」；PID 记录在 ~/.workbuddy/github_watch.pid
日志：项目根 github_watch.log（已被 .gitignore 排除，不会入库）
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.expanduser("~/.workbuddy/github_watch.pid")
LOG_FILE = os.path.join(ROOT, "github_watch.log")

sys.path.insert(0, TOOLS)
import github_sync as gs  # noqa: E402


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def fingerprint():
    """返回 {相对路径: (mtime_ns, size)}，与 github_sync 排除规则一致。"""
    fp = {}
    for rel in gs.collect_files():
        full = os.path.join(gs.ROOT, rel)
        try:
            st = os.stat(full)
            fp[rel] = (st.st_mtime_ns, st.st_size)
        except OSError:
            pass
    return fp


def sync_now():
    py = sys.executable or "python"
    try:
        r = subprocess.run(
            [py, os.path.join(TOOLS, "github_sync.py")],
            capture_output=True, text=True, timeout=240, cwd=gs.ROOT,
            encoding="utf-8", errors="replace",
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, out.strip()
    except Exception as e:
        return False, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=30, help="扫描周期秒数（默认 30）")
    ap.add_argument("--debounce", type=int, default=6, help="变化后防抖秒数（默认 6）")
    args = ap.parse_args()

    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log(f"即时备份监听启动 PID={os.getpid()}，每 {args.interval}s 扫描，防抖 {args.debounce}s，代理 {gs.PROXY}")

    # 启动先做一次基线同步，确保起点一致
    ok, out = sync_now()
    log("基线同步成功" if ok else "基线同步失败，稍后自动重试：" + ("\n" + out[-400:] if out else ""))

    last = fingerprint()
    retry_after = 0.0
    while True:
        time.sleep(args.interval)
        try:
            cur = fingerprint()
            changed = [r for r in cur if r not in last or cur[r] != last.get(r)]
            changed += [r for r in last if r not in cur]
            if not changed:
                continue
            log(f"检测到 {len(changed)} 个文件变化: {', '.join(changed[:6])}{' ...' if len(changed) > 6 else ''}，防抖 {args.debounce}s 后上传")
            time.sleep(args.debounce)
            if time.time() < retry_after:
                log("上次上传失败处于冷却期，本次变化留待重试")
                continue
            ok, out = sync_now()
            if ok:
                tail = out.splitlines()[-1] if out else ""
                log("上传成功: " + tail)
                last = fingerprint()
                retry_after = 0.0
            else:
                log("上传失败，将退避重试:\n" + (out[-500:] if out else "无输出"))
                retry_after = time.time() + 120
        except KeyboardInterrupt:
            log("监听已手动停止")
            break
        except Exception as e:
            log("监听异常: " + str(e))
            time.sleep(5)
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


if __name__ == "__main__":
    main()
