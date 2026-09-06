# -*- coding: utf-8 -*-
"""
知识工作台 · 独立桌面应用（原生窗口，无浏览器跳转、无控制台窗口）

双击「知识工作台」快捷方式 → 由 pythonw.exe 无窗口执行本脚本：
  1. 服务已在运行（8787 被监听）→ 直接弹出应用窗口
  2. 服务未运行 → 静默拉起 uvicorn 服务子进程 → 等待端口就绪 → 弹出应用窗口
  3. 应用窗口（pywebview 原生窗口，内嵌 WebView2 渲染前端）
  4. 系统托盘常驻：打开窗口 / 重启服务 / 开机自启开关 / 退出
  5. 关闭窗口（点 X）→ 缩到托盘，服务继续后台运行；托盘「退出」才彻底停止

用法：
  pythonw kb_launcher.py              # 默认启动（自动弹出应用窗口）
  pythonw kb_launcher.py --quiet      # 启动但不自动弹窗（供"开机自启"使用，仅托盘常驻）
  python  kb_launcher.py --stop       # 停止服务与托盘守护（优雅，带兜底强杀）
  python  kb_launcher.py --selftest   # 自检：临时数据目录拉起服务→等端口→关闭（不碰真实数据/托盘）
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser

try:
    import pystray
    from pystray import Menu, MenuItem
    HAS_TRAY = True
except Exception:
    HAS_TRAY = False

try:
    import webview
    HAS_WEBVIEW = True
except Exception:
    HAS_WEBVIEW = False

# 关键修复：清除沙箱/系统注入的代理环境变量。
# 否则 WebView2 会把 http://127.0.0.1:8787 也走代理（如 127.0.0.1:58464），
# 导致 localhost 请求被代理拦截 → 页面导航失败 → 应用窗口黑屏。
# 本应用只访问本地服务，无需代理；清除后 WebView2 与服务子进程均直连 localhost。
for _proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                   "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy"):
    os.environ.pop(_proxy_key, None)
os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
os.environ["no_proxy"] = "localhost,127.0.0.1,::1"

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 项目根
APP_PY = os.path.join(BASE, "app.py")
LAUNCHER = os.path.abspath(__file__)
PY_DIR = os.path.dirname(sys.executable)
PYTHON = os.path.join(PY_DIR, "python.exe")          # 有控制台的解释器（仅 --stop/--selftest 等命令行场景回退）
PYTHONW = os.path.join(PY_DIR, "pythonw.exe")         # 无窗口解释器（服务子进程 + 开机自启注册，杜绝闪黑框）
if not os.path.exists(PYTHON):
    PYTHON = sys.executable
if not os.path.exists(PYTHONW):
    PYTHONW = sys.executable

PORT = int(os.environ.get("PORT", "8787"))
URL = f"http://127.0.0.1:{PORT}/"
KB_DATA_DIR = os.environ.get("KB_DATA_DIR", r"D:\agent\知识工作台_数据")
ICON_PNG = os.path.join(BASE, "kb_icon.png")
ICON_ICO = os.path.join(BASE, "kb_icon.ico")

TMP = os.environ.get("TEMP", os.environ.get("TMP", "."))
PIDFILE = os.path.join(TMP, "kb_workbench_launcher.pid")
STOPFLAG = os.path.join(TMP, "kb_workbench_stop.flag")
OPENFLAG = os.path.join(TMP, "kb_workbench_open.flag")
LOG = os.path.join(TMP, "kb_launcher.log")
OUT_LOG = os.path.join(BASE, "server_run.out.log")
ERR_LOG = os.path.join(BASE, "server_run.err.log")
AUTOSTART_NAME = "知识工作台"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# WebView2 用户数据目录（持久化前端 localStorage/cookie，避免每次启动登录态丢失）
PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".workbuddy", "kb_webview_profile")

# 启动过渡页（内联 HTML）：窗口先立即显示此页，WebView2 初始化与后台服务启动并行，
# 服务就绪后再 load_url 真实地址，避免"双击→长时间黑屏/白屏等待"，感知启动更快。
LOADING_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>个人知识管理工作台</title>
<style>
  html,body{height:100%;margin:0;display:flex;align-items:center;justify-content:center;
    font-family:-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;background:#1c1c1e;color:#e5e5ea;}
  .wrap{text-align:center;}
  .spin{width:36px;height:36px;border:3px solid #3a3a3c;border-top-color:#0a84ff;border-radius:50%;
    animation:r 0.9s linear infinite;margin:0 auto 16px;}
  @keyframes r{to{transform:rotate(360deg)}}
  .t{font-size:15px;letter-spacing:1px;}
</style></head>
<body><div class="wrap"><div class="spin"></div><div class="t">正在启动…</div></div></body></html>
"""

_CR_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_server = {"proc": None, "quitting": False}
_gui = {"win": None, "tray": None}
_stopwatch = {"running": False}


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as fp:
            fp.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass


def msgbox(title, text):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except Exception:
        pass


def port_listening(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.4)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def open_browser():
    """兜底：pywebview 不可用时的浏览器打开方式。"""
    try:
        webbrowser.open(URL)
        log("已打开浏览器 " + URL)
    except Exception as e:
        log(f"打开浏览器失败: {e}")


def request_open_window():
    """通知已在运行的守护进程：弹出应用窗口（跨进程，通过标记文件）。"""
    try:
        with open(OPENFLAG, "w", encoding="utf-8") as fp:
            fp.write("1")
        log("已请求守护弹出窗口")
    except Exception as e:
        log(f"请求打开窗口失败: {e}")


def pid_alive(pid):
    """跨平台可靠的进程存活检测（避免 Windows 上 os.kill(0) 的副作用与本地化编码问题）。"""
    if not pid or pid <= 0:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, timeout=4,
        )
        # tasklist 输出为本地代码页，直接用字节匹配 PID 引号形式，避免解码异常
        needle = ('"' + str(pid) + '"').encode()
        return needle in out.stdout
    except Exception:
        return False


def read_pidfile():
    try:
        with open(PIDFILE, "r", encoding="utf-8") as fp:
            return int(json.load(fp).get("pid", 0) or 0)
    except Exception:
        return 0


def write_pidfile():
    with open(PIDFILE, "w", encoding="utf-8") as fp:
        json.dump({"pid": os.getpid(), "ts": int(time.time())}, fp)


def remove_pidfile():
    try:
        os.remove(PIDFILE)
    except OSError:
        pass


def start_server():
    """拉起服务子进程（无窗口、日志重定向），返回 Popen 对象。

    关键：用 pythonw.exe（无窗口解释器）而非 python.exe，杜绝双击后闪黑框。
    日志经 stdout/stderr 重定向到 server_run.out.log / server_run.err.log，
    pythonw 无控制台也不影响排障。
    """
    os.makedirs(KB_DATA_DIR, exist_ok=True)
    env = os.environ.copy()
    env["KB_DATA_DIR"] = KB_DATA_DIR
    env["PORT"] = str(PORT)
    try:
        out = open(OUT_LOG, "w", encoding="utf-8")
        err = open(ERR_LOG, "w", encoding="utf-8")
    except Exception:
        out, err = subprocess.DEVNULL, subprocess.DEVNULL
    proc = subprocess.Popen(
        [PYTHONW, APP_PY],
        cwd=BASE,
        env=env,
        stdout=out,
        stderr=err,
        creationflags=_CR_NO_WINDOW,
    )
    _server["proc"] = proc
    log(f"服务已拉起 PID={proc.pid}（{URL}）")
    return proc


def stop_server(proc):
    if proc is None or proc.poll() is not None:
        return
    log("正在停止服务…")
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    log("服务已停止")


def wait_port(timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if port_listening(PORT):
            return True
        time.sleep(0.1)
    return False


def set_autostart(enable):
    """开机自启开关：写入/删除 HKCU Run 键。值 = "pythonw.exe" "launcher.py" --quiet"""
    import winreg
    cmd = f'"{PYTHONW}" "{LAUNCHER}" --quiet'
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
    try:
        if enable:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_NAME)
            except FileNotFoundError:
                pass
    finally:
        key.Close()


def autostart_enabled():
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
            return True
        except FileNotFoundError:
            return False
        finally:
            key.Close()
    except Exception:
        return False


def _watcher_thread(icon_ref):
    """守护线程：①监听停止标记；②服务意外退出时自动拉起（防抖 60s 内至多 3 次）。"""
    restart_win = []  # [(ts,), ...] 60s 窗口内重启时间戳
    while not _server["quitting"]:
        # 停止标记
        if os.path.exists(STOPFLAG):
            log("收到停止标记，开始优雅退出")
            _server["quitting"] = True
            try:
                os.remove(STOPFLAG)
            except OSError:
                pass
            icon = icon_ref.get("icon")
            if icon is not None:
                try:
                    icon.stop()
                except Exception:
                    pass
            break
        # 打开窗口请求（跨进程标记）
        if os.path.exists(OPENFLAG):
            try:
                os.remove(OPENFLAG)
            except OSError:
                pass
            log("收到打开窗口请求")
            show_window()
        # 服务意外退出 → 自动重启
        proc = _server.get("proc")
        if proc is not None and proc.poll() is not None and not _server["quitting"]:
            now = time.time()
            restart_win = [t for t in restart_win if now - t < 60]
            if len(restart_win) < 3:
                log(f"服务异常退出(code={proc.returncode})，5 秒后自动重启")
                restart_win.append(now)
                time.sleep(5)
                if not _server["quitting"]:
                    try:
                        _server["proc"] = start_server()
                        icon = icon_ref.get("icon")
                        if icon is not None:
                            try:
                                icon.notify("服务异常退出，已自动重启", "个人知识管理工作台")
                            except Exception:
                                pass
                    except Exception as e:
                        log(f"自动重启失败: {e}")
            else:
                log("60s 内连续异常退出 3 次，停止自动重启，等待人工处理")
                _server["quitting"] = True
        time.sleep(1)
    _stopwatch["running"] = False


def _icon_image():
    try:
        from PIL import Image
        return Image.open(ICON_PNG)
    except Exception:
        return None


def show_window():
    """显示/恢复应用窗口（若已隐藏到托盘）。"""
    win = _gui.get("win")
    if win is None:
        return
    try:
        win.show()
        try:
            win.restore()
        except Exception:
            pass
    except Exception as e:
        log(f"恢复窗口失败: {e}")


def run_gui():
    """主线程：创建并运行 pywebview 独立应用窗口（阻塞，直到退出）。

    启动优化：窗口先显示内联「正在启动…」过渡页，WebView2 初始化与后台服务启动并行，
    服务端口就绪后再 load_url 真实地址，缩短用户感知的启动时间。
    """
    if not HAS_WEBVIEW:
        log("缺少 pywebview，回退到浏览器模式")
        open_browser()
        while not _server["quitting"]:
            time.sleep(0.5)
        return

    try:
        os.makedirs(PROFILE_DIR, exist_ok=True)
    except Exception:
        pass

    # 先用内联过渡页创建窗口：webview.start() 立即显示，WebView2 初始化与后台服务启动并行
    win = webview.create_window(
        "个人知识管理工作台",
        html=LOADING_HTML,
        width=1280,
        height=840,
        min_size=(980, 640),
        confirm_close=False,
    )
    _gui["win"] = win

    def on_closing():
        # 点 X：若正在退出则放行；否则缩到托盘
        if _server["quitting"]:
            return True
        log("窗口关闭 → 缩到托盘")
        try:
            win.hide()
        except Exception as e:
            log(f"隐藏窗口失败: {e}")
        return False  # 阻止真正关闭

    _nav_to_real = {"done": False}

    def on_loaded():
        # 过渡页首次加载完成后，后台等端口就绪再加载真实地址（仅触发一次，
        # 避免真实地址加载完成后 loaded 再次触发导致重复导航）。
        if _nav_to_real["done"]:
            return
        _nav_to_real["done"] = True

        def _load_when_ready():
            if wait_port(timeout=30):
                log("服务就绪，加载真实地址")
                try:
                    win.load_url(URL)
                except Exception as e:
                    log(f"加载真实地址失败: {e}")
                    msgbox("知识工作台", "页面加载失败，请通过托盘「重启服务」重试")
            else:
                log("服务启动超时，窗口停留过渡页")
                msgbox("知识工作台", "服务启动超时，请查看项目目录下 server_run.err.log")

        threading.Thread(target=_load_when_ready, daemon=True).start()

    try:
        win.events.loaded += on_loaded
    except Exception as e:
        log(f"注册加载事件失败: {e}")

    try:
        win.events.closing += on_closing
    except Exception as e:
        log(f"注册关闭事件失败: {e}")

    log("应用窗口已启动")
    try:
        webview.start(
            private_mode=False,
            storage_path=PROFILE_DIR,
            icon=ICON_ICO if os.path.exists(ICON_ICO) else None,
        )
    except Exception as e:
        log(f"窗口运行异常: {e}")
    log("应用窗口已退出")


def run_tray():
    """托盘（子线程）：常驻菜单。"""
    if not HAS_TRAY:
        msgbox("知识工作台", "缺少托盘组件 pystray，无法常驻。\n请运行: python -m pip install pystray")
        return

    img = _icon_image()
    state = {"icon": None}

    def act_open():
        show_window()

    def act_restart():
        proc = _server.get("proc")
        stop_server(proc)
        if not _server["quitting"]:
            _server["proc"] = start_server()
            wait_port(timeout=20)
            show_window()

    def act_toggle_autostart():
        enable = not autostart_enabled()
        try:
            set_autostart(enable)
        except Exception as e:
            log(f"设置开机自启失败: {e}")
            return
        state["icon"].notify(("已开启" if enable else "已关闭") + "开机自启", "个人知识管理工作台")

    def act_quit():
        log("用户选择退出")
        _server["quitting"] = True
        win = _gui.get("win")
        if win is not None:
            try:
                win.destroy()
            except Exception as e:
                log(f"关闭窗口失败: {e}")
        try:
            state["icon"].stop()
        except Exception:
            pass

    menu = Menu(
        MenuItem("打开窗口", act_open, default=True),
        MenuItem("重启服务", act_restart),
        Menu.SEPARATOR,
        MenuItem("开机自启", act_toggle_autostart,
                 checked=lambda item: autostart_enabled()),
        Menu.SEPARATOR,
        MenuItem("退出知识工作台", act_quit),
    )
    try:
        icon = pystray.Icon("kb_workbench", img or "知识工作台", "个人知识管理工作台", menu)
    except Exception:
        icon = pystray.Icon("kb_workbench", img, "个人知识管理工作台", menu)
    state["icon"] = icon
    _gui["tray"] = icon

    # 守护线程（停止标记 + 服务存活监控）
    _stopwatch["running"] = True
    threading.Thread(target=_watcher_thread, args=(state,), daemon=True).start()

    log("托盘已就绪")
    icon.run()  # 阻塞至 icon.stop()
    log("托盘退出，清理中")


def daemon_main(quiet=False):
    """完整守护流程（由 pythonw 无窗口执行）。"""
    # 已存在活着的守护（pidfile 有效）→ 仅弹窗
    pid = read_pidfile()
    if pid and not pid_alive(pid):
        # 僵尸 pidfile：守护进程已死但锁文件残留（异常退出/睡眠/强杀），清理后继续启动
        log(f"清理僵尸 pidfile（pid={pid} 已不在运行）")
        remove_pidfile()
        pid = 0
    if pid and pid_alive(pid) and pid != os.getpid():
        log(f"守护已在运行(pid={pid})，请求弹窗")
        request_open_window()
        return 0

    # 抢占 pidfile（O_EXCL 原子防双开竞态）
    acquired = False
    for _ in range(3):
        try:
            fd = os.open(PIDFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            p2 = read_pidfile()
            if p2 and pid_alive(p2) and p2 != os.getpid():
                log(f"守护已在运行(pid={p2})，请求弹窗")
                request_open_window()
                return 0
            # 僵尸锁：进程已死但 pidfile 残留 → 清理后重试抢锁
            log(f"抢锁遇残留 pidfile（pid={p2} 已死），清理重试")
            remove_pidfile()
            time.sleep(0.2)
    if not acquired:
        log("未能获取启动锁，放弃")
        return 1

    # 服务已被手动（start.bat 窗口）启动 → 不接管，开窗口退出
    if port_listening(PORT):
        remove_pidfile()
        log("检测到服务已在运行（窗口模式），仅打开窗口")
        request_open_window()
        return 0

    write_pidfile()
    log(f"守护启动 pid={os.getpid()} base={BASE}")
    try:
        start_server()

        # 托盘（子线程，非 daemon 保持进程存活）
        tray_thread = threading.Thread(target=run_tray, daemon=False)
        tray_thread.start()
        time.sleep(0.8)  # 等托盘就绪

        if quiet:
            log("--quiet 模式：后台运行，不自动弹窗")
            # 后台等服务就绪（quiet 不弹窗，watcher 会兜底监控服务存活与自动重启）
            threading.Thread(target=lambda: wait_port(timeout=30), daemon=True).start()
            while not _server["quitting"]:
                time.sleep(0.5)
        else:
            run_gui()  # 主线程阻塞；窗口先显「加载中」，服务就绪后自动加载真实地址

    finally:
        _server["quitting"] = True
        proc = _server.get("proc")
        stop_server(proc)
        tray = _gui.get("tray")
        if tray is not None:
            try:
                tray.stop()
            except Exception:
                pass
        remove_pidfile()
        log("守护退出")
    return 0


def cli_stop():
    """--stop：写停止标记 → 等守护优雅退出（≤12s）→ 兜底强杀。"""
    pid = read_pidfile()
    if pid and pid_alive(pid):
        open(STOPFLAG, "w", encoding="utf-8").write("1")
        print(f"已请求停止（守护 pid={pid}）…")
        t0 = time.time()
        while time.time() - t0 < 12:
            if not pid_alive(pid):
                print("✅ 知识工作台已停止")
                return 0
            time.sleep(0.5)
        print("守护未响应，强制结束…")
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
        print("✅ 已强制停止（服务与其子进程一并结束）")
        return 0
    # 无守护但端口被占 → 手动窗口模式
    if port_listening(PORT):
        print("⚠️  服务正由「窗口模式」（start.bat 黑窗口）运行，请直接关闭该窗口。")
        return 1
    print("ℹ️  知识工作台当前未在运行。")
    return 0


def cli_selftest():
    """--selftest：临时数据目录 + 随机端口拉起服务→等就绪→关闭。不碰真实数据与托盘。"""
    import random
    import shutil
    global KB_DATA_DIR, PORT, URL, PIDFILE, STOPFLAG
    tmpdir = os.path.join(TMP, f"kb_selftest_{int(time.time())}_{random.randint(100, 999)}")
    os.makedirs(tmpdir, exist_ok=True)
    KB_DATA_DIR = os.path.join(tmpdir, "data")
    PORT = 8790
    while port_listening(PORT):
        PORT += 1
    URL = f"http://127.0.0.1:{PORT}/"
    PIDFILE = os.path.join(tmpdir, "test.pid")
    STOPFLAG = os.path.join(tmpdir, "stop.flag")
    print(f"[selftest] 临时数据目录: {KB_DATA_DIR}")
    print(f"[selftest] 测试端口: {PORT}")
    try:
        proc = start_server()
        ok = wait_port(timeout=20)
        if ok:
            # 探测主页返回
            import urllib.request
            with urllib.request.urlopen(URL, timeout=5) as resp:
                code = resp.status
            print(f"[selftest] ✅ 服务就绪 HTTP {code}，PID={proc.pid}")
            result = 0
        else:
            print("[selftest] ❌ 服务 20s 内未就绪（见 server_run.err.log）")
            result = 1
    finally:
        stop_server(_server.get("proc"))
        shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"[selftest] {'PASS' if result == 0 else 'FAIL'}")
    return result


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        if arg == "--stop":
            sys.exit(cli_stop())
        if arg == "--selftest":
            sys.exit(cli_selftest())
        sys.exit(daemon_main(quiet=(arg == "--quiet")))
    except SystemExit:
        raise
    except Exception:
        import traceback
        log("未捕获异常:\n" + traceback.format_exc())
        msgbox("知识工作台", "启动出错，请查看项目目录下 server_run.err.log 及系统临时目录 kb_launcher.log")
        sys.exit(2)
