# -*- coding: utf-8 -*-
"""
知识工作台 · 变更日志工具
==========================
最小占用保存代码修改记录：用 unified diff（只存改动行），统一写入 CHANGELOG.md，
只保留最近 MAX_ENTRIES 次，超出自动裁剪（删最旧）。

归档策略（一次升级只归档一条）:
    1) 升级开始时:  python tools/changelog.py snapshot --files <本次升级涉及的所有文件>
                     —— 一次性暂存全部原始版本
    2) 迭代开发中:  随意多轮修改，不记录
    3) 方案最终确认后: python tools/changelog.py add "本次升级说明"
                     —— 一次性生成完整 diff 归档，只占 1 条名额

add 会自动读取 snapshot 暂存的文件清单，生成 diff、写入 CHANGELOG.md、清理暂存、
裁剪到最近 5 次。升级中途放弃时直接 add 即可（无变化则自动跳过、不生成记录）。
"""
import os
import sys
import json
import re
import shutil
import difflib
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANGELOG = os.path.join(BASE, "CHANGELOG.md")
STAGING = os.path.join(BASE, "data", "changelog_staging")
MANIFEST = os.path.join(STAGING, "manifest.json")
MAX_ENTRIES = 5

HEADER = """# 变更日志

> 本文件由 `tools/changelog.py` 自动维护，**仅保留最近 5 次代码变更**，超出自动删除。
> 每次修改以 unified diff 形式记录（只存改动行，最小占用）。

"""

ENTRY_SEP = "\n\n---\n\n"


def _rel(p):
    try:
        return os.path.relpath(os.path.abspath(p), BASE)
    except ValueError:  # 跨盘符（如 C: vs D:）时退回绝对路径
        return os.path.abspath(p).replace(":", "")


def snapshot(files):
    """修改前调用：把要修改的文件复制到暂存区；不存在的文件标记为「新增」。"""
    os.makedirs(STAGING, exist_ok=True)
    manifest = {}
    for f in files:
        f = os.path.abspath(f)
        if os.path.realpath(f) == os.path.realpath(CHANGELOG):
            print(f"  ! 跳过（CHANGELOG 自身不记录）")
            continue
        relf = _rel(f)
        if os.path.exists(f):
            dst = os.path.join(STAGING, relf.replace(os.sep, "__"))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(f, dst)
            manifest[relf] = {"old": dst, "new": f}
            print(f"  · 暂存: {relf}")
        else:
            manifest[relf] = {"old": None, "new": f}
            print(f"  · 标记新增: {relf}")
    with open(MANIFEST, "w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=1)
    print(f"  ✓ 已暂存 {len(manifest)} 个文件到变更暂存区")
    print(f"    请进行代码修改，完成后运行:  python tools/changelog.py add \"改动说明\"")


def _build_diff(old_path, new_path, relf):
    with open(old_path, encoding="utf-8", errors="ignore") as f1, \
         open(new_path, encoding="utf-8", errors="ignore") as f2:
        old_lines = f1.readlines()
        new_lines = f2.readlines()
    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile=relf, tofile=relf, lineterm="", n=2,
    ))
    return "\n".join(diff)


def _load_entries(content):
    """把 CHANGELOG 正文切分为 [头部, 条目1, 条目2, ...]（条目按时间新→旧排列）。
    每条目自动去掉尾部分隔符，join 时统一加 ENTRY_SEP，避免分隔符堆积。"""
    marks = [m.start() for m in re.finditer(r"^### ", content, re.M)]
    if not marks:
        return content, []
    header = content[: marks[0]]
    entries = []
    for i, m in enumerate(marks):
        end = marks[i + 1] if i + 1 < len(marks) else len(content)
        raw = content[m:end]
        raw = re.sub(r"\n---\s*$", "", raw).rstrip()
        entries.append(raw)
    return header, entries


def add(desc):
    """修改后调用：对比暂存版本与当前版本，生成 diff 写入 CHANGELOG.md。"""
    if not os.path.exists(MANIFEST):
        print("  ✗ 未找到暂存记录。请先运行:")
        print('    python tools/changelog.py snapshot --files <要修改的文件...>')
        sys.exit(1)

    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    changed = []  # (relf, diff, is_new)
    for relf, info in manifest.items():
        old = info.get("old")
        new = info.get("new")
        if old is None:
            # 新增文件：diff 展示全部行
            if not os.path.exists(new):
                print(f"  ! 跳过（新文件不存在）: {relf}")
                continue
            with open(new, encoding="utf-8", errors="ignore") as f:
                new_lines = f.read().splitlines()
            diff = f"--- /dev/null\n+++ {relf}\n@@ -0,0 +1,{len(new_lines)} @@\n" + \
                   "\n".join("+" + l for l in new_lines)
            changed.append((relf, diff, True))
            print(f"  ✓ 新增文件: {relf}（{len(new_lines)} 行）")
        elif not os.path.exists(new):
            print(f"  ! 跳过（当前文件不存在）: {relf}")
            continue
        else:
            diff = _build_diff(old, new, relf)
            if diff:
                changed.append((relf, diff, False))
                print(f"  ✓ 检测到变更: {relf}（diff {len(diff)} 字符）")
            else:
                print(f"  - 无变化: {relf}")

    # 清理暂存区（失败不阻塞主流程）
    try:
        shutil.rmtree(STAGING, ignore_errors=True)
    except Exception:
        pass

    if not changed:
        print("  · 所有文件无变化，本次未生成记录")
        return

    # 组装新条目（新 → 旧 顺序，新条目在最前）
    block = [f"### {datetime.now():%Y-%m-%d %H:%M:%S} | {desc}", ""]
    for relf, diff, is_new in changed:
        tag = "（新增文件）" if is_new else ""
        block.append(f"**文件:** `{relf}`{tag}")
        block.append("```diff")
        block.append(diff.rstrip("\n"))
        block.append("```")
        block.append("")
    entry = "\n".join(block).rstrip() + "\n"

    if os.path.exists(CHANGELOG):
        with open(CHANGELOG, encoding="utf-8") as fp:
            content = fp.read()
        header, entries = _load_entries(content)
    else:
        header, entries = HEADER, []

    entries.insert(0, entry)
    # 只保留最近 MAX_ENTRIES 条
    removed = entries[MAX_ENTRIES:]
    entries = entries[:MAX_ENTRIES]

    with open(CHANGELOG, "w", encoding="utf-8") as fp:
        fp.write(header)
        fp.write(ENTRY_SEP.join(entries))
        fp.write("\n")

    print(f"  ✓ 已写入 CHANGELOG.md（当前保留 {len(entries)} 次）")
    if removed:
        print(f"  · 已自动删除 {len(removed)} 条旧记录（仅保留最近 {MAX_ENTRIES} 次）")


def main():
    parser = argparse.ArgumentParser(description="知识工作台变更日志工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("snapshot", help="修改前：暂存当前文件版本")
    p1.add_argument("--files", nargs="+", required=True, help="要修改的文件路径列表")

    p2 = sub.add_parser("add", help="升级方案最终确认后：生成 diff 并归档")
    p2.add_argument("desc", help="本次升级说明")

    args = parser.parse_args()
    if args.cmd == "snapshot":
        snapshot(args.files)
    elif args.cmd == "add":
        add(args.desc)


if __name__ == "__main__":
    main()
