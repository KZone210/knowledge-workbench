#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GitHub 代码备份同步脚本（Contents API 通道，规避 git push 在代理下断连问题）

原理：
  - 遍历项目代码文件（与 .gitignore 同一套排除规则）
  - 以本地 sha256 状态缓存做增量判断，内容变化才上传
  - 每次上传 GitHub 自动生成一次 commit，云端历史完整
  - 本地已删除的文件 → 调用 DELETE 同步移除

用法：
  python tools/github_sync.py            # Token 自动从 ~/.git-credentials 读取
  GITHUB_TOKEN=xxx python tools/github_sync.py
  python tools/github_sync.py --dry-run  # 只预览不实际上传

仓库：KZone210/knowledge-workbench（公开，仅代码，不含数据/记忆/备份）
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "KZone210/knowledge-workbench"
API = f"https://api.github.com/repos/{REPO}/contents"
BRANCH = "main"
STATE_FILE = os.path.expanduser("~/.workbuddy/github_sync_state.json")
PROXY = os.environ.get("HTTPS_PROXY") or "http://127.0.0.1:7890"

# ---- 排除规则：与项目 .gitignore 保持一致 ----
EXCLUDE_DIR_NAMES = {
    ".git", ".workbuddy", "data", "backup", "docs", "__pycache__",
    ".idea", ".vscode", "venv", ".venv", "env", "node_modules",
}
EXCLUDE_FILE_PATTERNS = ("*.log", "*.pyc", "*.pyo", "*.swp")
EXCLUDE_FILE_NAMES = {"项目目标.txt"}


def get_token():
    tk = os.environ.get("GITHUB_TOKEN")
    if tk:
        return tk.strip()
    cred = os.path.expanduser("~/.git-credentials")
    if os.path.exists(cred):
        with open(cred, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if "github.com" in line:
                    after = line.split("://", 1)[1]
                    return after.split(":", 1)[1].split("@", 1)[0]
    raise SystemExit("未找到 GitHub Token：请设置 GITHUB_TOKEN 或确保 ~/.git-credentials 存在")


def api_request(method, url, token, body=None, retries=3):
    """带代理与重试的 GitHub API 请求，返回 (status, json_dict)。"""
    proxy = urllib.request.ProxyHandler({"https": PROXY, "http": PROXY})
    opener = urllib.request.build_opener(proxy)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "knowledge-workbench-backup")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    last_err = None
    for i in range(retries):
        try:
            with opener.open(req, timeout=60) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                info = json.loads(raw)
            except Exception:
                info = {"message": raw[:200].decode("utf-8", "replace")}
            if e.code in (403, 404) and "sha" not in str(body):
                # 409/422 类可重试；403 rate limit 判断
                if "rate limit" in str(info).lower():
                    time.sleep(5)
                    continue
                return e.code, info
            if e.code in (409, 422, 503):
                time.sleep(2 * (i + 1))
                last_err = info
                continue
            return e.code, info
        except Exception as e:  # 网络层错误，重试
            last_err = {"message": str(e)}
            time.sleep(2 * (i + 1))
    return -1, last_err


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_files():
    """收集待备份文件，返回相对路径列表（正斜杠）。"""
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIR_NAMES]
        for fn in filenames:
            if fn in EXCLUDE_FILE_NAMES:
                continue
            if any(fn.endswith(p.lstrip("*")) for p in EXCLUDE_FILE_PATTERNS if p.startswith("*")):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), ROOT).replace("\\", "/")
            files.append(rel)
    return sorted(files)


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只预览不执行")
    args = ap.parse_args()

    token = get_token()
    state = load_state()
    files = collect_files()

    uploaded, skipped, deleted, failed = [], [], [], []
    date = time.strftime("%Y-%m-%d %H:%M")

    # 1) 上传 / 跳过
    for rel in files:
        full = os.path.join(ROOT, rel)
        h = sha256_of(full)
        if state.get(rel) == h:
            skipped.append(rel)
            continue
        if args.dry_run:
            uploaded.append(rel)
            continue
        with open(full, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        url = API + "/" + urllib.parse.quote(rel)
        st, info = api_request("PUT", url, token, {
            "message": f"backup {rel} [{date}]",
            "content": b64,
            "branch": BRANCH,
        })
        if st in (200, 201):
            state[rel] = h
            uploaded.append(rel)
        else:
            failed.append(f"{rel} (HTTP {st}: {info.get('message', info)})")

    # 2) 删除远端已不存在的文件
    for rel in list(state.keys()):
        if rel not in files:
            if args.dry_run:
                deleted.append(rel)
                continue
            url = API + "/" + urllib.parse.quote(rel)
            # 先取 sha
            st, info = api_request("GET", url, token)
            if st == 200 and "sha" in info:
                st2, _ = api_request("DELETE", url, token, {
                    "message": f"remove {rel} [{date}]", "sha": info["sha"], "branch": BRANCH,
                })
                if st2 in (200, 204):
                    deleted.append(rel)
                    state.pop(rel, None)
                else:
                    failed.append(f"del {rel} (HTTP {st2})")
            elif st == 404:
                state.pop(rel, None)
            else:
                failed.append(f"del {rel} (HTTP {st})")

    if not args.dry_run:
        save_state(state)

    print(f"== GitHub 同步完成 ==")
    print(f"  上传/更新: {len(uploaded)} 个")
    for r in uploaded:
        print(f"    + {r}")
    print(f"  跳过(无变化): {len(skipped)} 个")
    print(f"  删除: {len(deleted)} 个")
    for r in deleted:
        print(f"    - {r}")
    if failed:
        print(f"  !! 失败 {len(failed)} 个:")
        for r in failed:
            print(f"    ! {r}")
        sys.exit(1)
    if args.dry_run:
        print("  (dry-run 预览，未实际写入)")


if __name__ == "__main__":
    main()
