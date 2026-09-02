# -*- coding: utf-8 -*-
"""
知识工作台 · 增量备份工具（零依赖 · 纯本地 · 不依赖 Git）
==========================================================
内容寻址对象库（git 对象模型的极简版）。省空间三招：

  1) 内容去重：每个文件按 sha256 内容指纹存储，相同内容跨快照只存一份
  2) 快照即清单：每次备份只生成一个 JSON 清单（路径 -> 对象ID），
     文件没变时仅追加几百字节引用，不复制任何字节
  3) 自动裁剪：prune 删除最旧快照并回收无人引用的孤儿对象

用法:
  python tools/backup.py backup  [--comment "说明"] [--dir 备份根] [--data-dir 数据目录]  # 一键备份
  python tools/backup.py list   [--dir 备份根]                      # 查看快照
  python tools/backup.py restore <快照名> [--to 目标目录] [--dir 备份根]  # 恢复
  python tools/backup.py prune  --keep 10 [--dir 备份根]            # 只留最近 N 份

备份根默认 <项目根>/backup/，--dir 可指向其他位置（U 盘 / 网盘同步目录）。
外置数据目录（KB_DATA_DIR / --data-dir）以 kbdata/ 前缀并入同一快照，
restore 时自动拆回数据目录；代码与用户数据一起备份、一起回滚。
knowledge.db 等 SQLite 文件用 Online Backup API 生成一致性副本，运行中备份也安全。
"""
import os
import sys
import json
import shutil
import hashlib
import tempfile
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BACKUP_ROOT = os.path.join(BASE, "backup")
CHUNK = 1 << 20  # 1MB 分块，大文件不占内存


# ---------- 排除规则 ----------
def is_ignored(rel: str) -> bool:
    rel = rel.replace("\\", "/").strip("/")
    if not rel:
        return True
    if rel == "backup" or rel.startswith("backup/"):
        return True  # 备份根自身（默认位置）
    if rel == ".workbuddy" or rel.startswith(".workbuddy/"):
        return True
    if rel == "data/changelog_staging" or rel.startswith("data/changelog_staging/"):
        return True
    parts = rel.split("/")
    if "__pycache__" in parts or rel.endswith(".pyc"):
        return True
    return False


def _fmt(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _obj_path(root: str, oid: str) -> str:
    return os.path.join(root, "objects", oid[:2], oid[2:])


def _store_object(root: str, src: str, oid: str) -> None:
    dst = _obj_path(root, oid)
    if os.path.exists(dst):
        return  # 内容已存在：去重，零写入
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".tmp"
    shutil.copyfile(src, tmp)
    os.replace(tmp, dst)  # 原子提交


def _ingest(root: str, path: str, rel: str) -> dict:
    """收进对象库；SQLite 走 backup API 保证运行中备份的一致性。"""
    src, tmpdb = path, None
    if rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
        try:
            import sqlite3
            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
            tf.close()
            tmpdb = tf.name
            s = sqlite3.connect(str(path))
            d = sqlite3.connect(tmpdb)
            with d:
                s.backup(d)
            s.close()
            d.close()
            src = tmpdb
        except Exception:
            tmpdb = None  # 非 SQLite 或失败：回退普通复制
    try:
        oid = _sha256(src)
        _store_object(root, src, oid)
        return {"oid": oid, "size": os.path.getsize(src),
                "mtime": os.path.getmtime(path)}
    finally:
        if tmpdb:
            try:
                os.unlink(tmpdb)
            except OSError:
                pass


def _list_snapshots(root: str):
    d = os.path.join(root, "snapshots")
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))


def _store_size(root: str) -> int:
    od = os.path.join(root, "objects")
    total = 0
    if os.path.isdir(od):
        for dp, _, fns in os.walk(od):
            for fn in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, fn))
                except OSError:
                    pass
    return total


def _resolve_data_dir(args) -> str | None:
    """外置数据目录：--data-dir > 环境变量 KB_DATA_DIR；未配置且不在项目内则 None。"""
    d = getattr(args, "data_dir", None) or os.environ.get("KB_DATA_DIR", "").strip()
    if not d:
        return None
    d = os.path.abspath(d)
    # 若指向项目内 data/（兼容旧部署），BASE 扫描已覆盖，无需单独收集
    if d == os.path.join(BASE, "data"):
        return None
    return d


# ---------- 子命令 ----------
def cmd_backup(args):
    root = args.dir
    os.makedirs(os.path.join(root, "objects"), exist_ok=True)
    os.makedirs(os.path.join(root, "snapshots"), exist_ok=True)

    files, total = {}, 0
    for dirpath, dirnames, filenames in os.walk(BASE):
        dirnames[:] = [d for d in dirnames if not is_ignored(
            os.path.relpath(os.path.join(dirpath, d), BASE))]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, BASE)
            if is_ignored(rel):
                continue
            info = _ingest(root, full, rel)
            files[rel.replace("\\", "/")] = info
            total += info["size"]

    # 外置数据目录：以 kbdata/ 前缀并入同一快照（与代码一起、对象库去重仍然生效）
    data_dir = _resolve_data_dir(args)
    data_count = 0
    if data_dir and os.path.isdir(data_dir):
        for dirpath, dirnames, filenames in os.walk(data_dir):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, data_dir).replace("\\", "/")
                info = _ingest(root, full, "kbdata/" + rel)
                files["kbdata/" + rel] = info
                total += info["size"]
                data_count += 1

    name = datetime.now().strftime("%Y%m%d_%H%M%S")
    snap = os.path.join(root, "snapshots", name + ".json")
    i = 2
    while os.path.exists(snap):
        snap = os.path.join(root, "snapshots", f"{name}_{i}.json")
        i += 1
    prev = _latest(root)  # 必须在写入前取上一快照，否则会对比到自身
    meta = {
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "root": BASE,
        "comment": args.comment or "",
        "n_files": len(files),
        "total_size": total,
        "files": files,
    }
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)

    added = added_bytes = 0
    if prev:
        prev_objs = {i["oid"] for i in prev["files"].values()}
        for info in files.values():
            if info["oid"] not in prev_objs:
                added += 1
                added_bytes += info["size"]

    print(f"  ✓ 备份完成: {os.path.basename(snap)[:-5]}")
    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}"
          + (f"（其中数据目录 {data_count} 个）" if data_count else ""))
    if prev:
        print(f"    相对上次: 新增/变更 {added} 个文件, 新增存储 {_fmt(added_bytes)}")
    else:
        print("    首次备份: 全量入库")
    print(f"    对象库物理占用: {_fmt(_store_size(root))}")
    print(f"    备份位置: {root}")


def _latest(root: str):
    snaps = _list_snapshots(root)
    if not snaps:
        return None
    with open(snaps[-1], encoding="utf-8") as f:
        return json.load(f)


def cmd_list(args):
    root = args.dir
    snaps = _list_snapshots(root)
    if not snaps:
        print("  (还没有备份快照) 运行: python tools/backup.py backup")
        return
    print(f"备份位置: {root}")
    print(f"对象库物理占用: {_fmt(_store_size(root))}")
    print("-" * 80)
    prev_objs = None
    for p in snaps:
        meta = json.load(open(p, encoding="utf-8"))
        name = os.path.basename(p)[:-5]
        objs = {i["oid"] for i in meta["files"].values()}
        chg = f"  (+{len(objs - prev_objs)} 文件变化)" if prev_objs is not None else "  (首次)"
        prev_objs = objs
        cmt = f"  「{meta.get('comment', '')}」" if meta.get("comment") else ""
        print(f"  {name}  {meta['n_files']} 文件 {_fmt(meta['total_size'])}{chg}{cmt}")


def cmd_restore(args):
    root = args.dir
    snap = args.snapshot if args.snapshot.endswith(".json") else args.snapshot + ".json"
    p = os.path.join(root, "snapshots", snap)
    if not os.path.exists(p):
        print(f"  ✗ 快照不存在: {snap}")
        sys.exit(1)
    meta = json.load(open(p, encoding="utf-8"))
    data_dir = _resolve_data_dir(args)
    dst_root = os.path.abspath(args.to) if args.to else BASE
    print(f"  恢复快照 {os.path.basename(p)[:-5]} -> {dst_root}")
    n = 0
    for rel, info in meta["files"].items():
        # kbdata/ 前缀 → 外置数据目录（未配置则回落项目内 data/）
        if rel.startswith("kbdata/"):
            target = data_dir or os.path.join(BASE, "data")
            rel = rel[len("kbdata/"):]
        else:
            target = dst_root
        full = os.path.realpath(os.path.join(target, rel))
        if not full.startswith(os.path.realpath(target) + os.sep):
            raise ValueError(f"非法路径: {rel}")
        os.makedirs(os.path.dirname(full), exist_ok=True)
        shutil.copy2(_obj_path(root, info["oid"]), full)
        if "mtime" in info:
            try:
                os.utime(full, (info["mtime"], info["mtime"]))
            except OSError:
                pass
        n += 1
    print(f"  ✓ 已恢复 {n} 个文件")


def cmd_prune(args):
    root = args.dir
    snaps = _list_snapshots(root)
    if len(snaps) <= args.keep:
        print(f"  当前 {len(snaps)} 份快照 ≤ 保留 {args.keep} 份，无需裁剪")
        return
    for p in snaps[:-args.keep]:
        os.remove(p)
        print(f"  - 删除快照 {os.path.basename(p)[:-5]}")
    keep_objs = set()
    for p in snaps[-args.keep:]:
        meta = json.load(open(p, encoding="utf-8"))
        keep_objs |= {i["oid"] for i in meta["files"].values()}
    od = os.path.join(root, "objects")
    freed = freed_bytes = 0
    if os.path.isdir(od):
        for d in os.listdir(od):
            dd = os.path.join(od, d)
            if not os.path.isdir(dd):
                continue
            for fn in os.listdir(dd):
                oid = d + fn
                if oid not in keep_objs:
                    try:
                        freed_bytes += os.path.getsize(os.path.join(dd, fn))
                        os.remove(os.path.join(dd, fn))
                        freed += 1
                    except OSError:
                        pass
            if not os.listdir(dd):
                try:
                    os.rmdir(dd)
                except OSError:
                    pass
    print(f"  ✓ 已清理孤儿对象 {freed} 个，释放 {_fmt(freed_bytes)}")


def main():
    ap = argparse.ArgumentParser(description="知识工作台增量备份工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--dir", default=DEFAULT_BACKUP_ROOT,
                       help=f"备份根目录(默认 {DEFAULT_BACKUP_ROOT})")
        p.add_argument("--data-dir", default=None,
                       help="外置数据目录(默认取环境变量 KB_DATA_DIR；未配置则只备份代码)")

    p1 = sub.add_parser("backup")
    common(p1)
    p1.add_argument("--comment", default="", help="本次备份说明")
    p2 = sub.add_parser("list")
    common(p2)
    p3 = sub.add_parser("restore")
    common(p3)
    p3.add_argument("snapshot", help="快照名，如 20260902_113000")
    p3.add_argument("--to", default=None, help="恢复目标目录(默认项目根)")
    p4 = sub.add_parser("prune")
    common(p4)
    p4.add_argument("--keep", type=int, default=10, help="保留最近 N 份快照(默认10)")
    args = ap.parse_args()
    {"backup": cmd_backup, "list": cmd_list,
     "restore": cmd_restore, "prune": cmd_prune}[args.cmd](args)


if __name__ == "__main__":
    main()
