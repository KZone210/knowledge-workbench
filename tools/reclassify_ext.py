# -*- coding: utf-8 -*-
"""存量文档重分类工具：按文件扩展名更新 documents 的 category/tags。

背景
====
早期版本把文档按「内容主题 8 大类」分类并存入 documents.category / tags。
本次把自动分类改为「按文件扩展名归类为文件类型」（文档/图片/视频/音频/压缩包/
代码/其他）。本工具用于将存量文档（此前按主题分类入库的数据）统一改为按扩展名
归类，只更新 category 与 tags 两列，绝不动文件本体、内容、keywords 等其他字段。

数据红线（务必遵守）
====================
1) 仅更新 documents.category 与 documents.tags 两列。
2) 执行前自动把 knowledge.db 备份到同目录（带时间戳副本），不删除原库。
3) 数据与程序本体隔离：默认连接 KB_DATA_DIR（环境变量）指向的数据库；
   也可用 --db / --data-dir 显式指定外置库。
4) 敏感列（category/tags/keywords/...）在库内为密文（enc_ver=1，AES-GCM）。
   因此本工具需要账号密码解开该用户 DEK，先解密再按扩展名重算，再加密写回。

用法
====
  # 预览（不写库）：列出将变化的行数、旧→新分布
  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --dry-run

  # 正式执行（自动备份后写库）
  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --yes
  # 指定用户名（默认询问；单用户环境通常为 king）
  python tools/reclassify_ext.py --data-dir "D:/agent/知识工作台_数据" --username king --yes
"""
from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime

# Windows 控制台默认 GBK 无法输出中文时转为 UTF-8，避免脚本报 UnicodeEncodeError
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 允许从项目根直接以 `python tools/reclassify_ext.py` 运行
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from backend import classify
from backend.security import auth, crypto  # noqa: E402

# 早期「内容主题」类别名：存量 tags 中若混入这些标签，重分类时剔除
_LEGACY_TOPIC_NAMES = frozenset({
    "技术开发", "人工智能", "金融投资", "营销运营", "教育学习",
    "健康养生", "职场管理", "生活随笔", "未分类",
})
# 文件类型名同样不作为内容标签保留
_FILE_TYPE_NAMES = frozenset(classify.FILE_TYPE_NAMES)


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def resolve_db_path(args) -> str:
    """解析数据库路径：--db > --data-dir > 环境 KB_DATA_DIR > 项目内 data/。"""
    if getattr(args, "db", None):
        return os.path.abspath(args.db)
    data_dir = getattr(args, "data_dir", None) or os.environ.get("KB_DATA_DIR", "").strip()
    if not data_dir:
        data_dir = os.path.join(BASE, "data")
    return os.path.join(os.path.abspath(data_dir), "knowledge.db")


def backup_db(db_path: str) -> str:
    """同目录生成带时间戳的 SQLite 一致性备份，返回备份路径。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    backup_path = os.path.join(
        os.path.dirname(db_path),
        f"knowledge_{_now_stamp()}_reclass.bak.db",
    )
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def list_users(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT u.id, u.username, u.role,
                  (SELECT COUNT(*) FROM documents d WHERE d.user_id = u.id) AS doc_count
           FROM users u ORDER BY u.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_docs(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, stored_name, ext, filename, category, tags, keywords, enc_ver
           FROM documents WHERE user_id=? ORDER BY id""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def resolve_user_dek(conn: sqlite3.Connection, username: str, password: str) -> bytes:
    """按登录同款流程解出该用户 DEK：KEK=Argon2id(password,salt) → 解 wrapped_dek。"""
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT salt_kek, wrapped_dek, dek_check FROM users WHERE username=?",
        (username,),
    ).fetchone()
    if row is None:
        raise ValueError(f"用户不存在: {username}")
    try:
        kek = auth._derive_kek(password, row["salt_kek"])
        dek = crypto.dec_bytes(kek, base64.b64decode(row["wrapped_dek"]))
    except Exception:
        raise PermissionError("密码错误，无法解开该用户密钥")
    if not crypto.verify_dek(dek, row["dek_check"]):
        raise PermissionError("密钥自检失败：请确认账号密码正确")
    return dek


def _ext_of_stored(stored_name: str, ext_col: str, filename: str) -> str:
    """优先 stored_name（服务端生成的存储名，扩展名可靠），其次 ext 列/原始文件名。"""
    for src in (stored_name or "", filename or ""):
        name = src.strip()
        if name:
            ext = os.path.splitext(name)[1]
            if ext:
                return ext
    if ext_col:
        return ext_col
    return ""


def compute_new_tags(old_tags: list[str]) -> list[str]:
    """剔除旧主题标签与文件类型名，保留真正的关键词标签。"""
    out: list[str] = []
    for t in old_tags or []:
        s = str(t).strip()
        if not s or s in _LEGACY_TOPIC_NAMES or s in _FILE_TYPE_NAMES:
            continue
        if s not in out:
            out.append(s)
    return out


def plan_doc(doc: dict, dek: bytes) -> dict:
    """对单篇文档计算重分类方案。

    仅解密读取 category/tags 以规划新值；keywords 仅用于观测，不修改。
    返回 {doc, old_category, old_tags, new_category, new_tags, changed}
    """
    decrypted = None
    enc_ver = doc.get("enc_ver") or 0
    if enc_ver == 1:
        row = dict(doc)
        from backend.security import vault
        decrypted = vault.dec_row(dek, row)
    else:
        # enc_ver != 1（正常经过启动迁移后应不存在）：category/tags 可能明文或异常
        raise RuntimeError(
            f"文档 id={doc['id']} enc_ver={enc_ver} 非预期（期望 1）。"
            "请先正常启动一次服务完成旧库迁移，再执行重分类。"
        )

    old_category = decrypted.get("category") or classify.DEFAULT_CATEGORY
    old_tags = decrypted.get("tags") or []
    ext = _ext_of_stored(doc.get("stored_name") or "", doc.get("ext") or "",
                         decrypted.get("filename") or "")
    new_category = classify.classify_by_ext(ext)
    new_tags = compute_new_tags(old_tags)
    changed = (new_category != old_category) or (new_tags != old_tags)
    return {
        "doc": doc,
        "old_category": old_category,
        "old_tags": old_tags,
        "new_category": new_category,
        "new_tags": new_tags,
        "changed": changed,
        "enc_ver": enc_ver,
    }


def encode_fields(dek: bytes, new_category: str, new_tags: list[str]) -> tuple[str, str]:
    """把新 category/tags 加密为库内密文（tags 先 JSON 序列化）。"""
    cat_raw = crypto.enc_field(dek, new_category)
    tags_raw = crypto.enc_field(dek, json.dumps(new_tags, ensure_ascii=False))
    return cat_raw, tags_raw


def write_doc(conn: sqlite3.Connection, doc_id: int, dek: bytes,
              new_category: str, new_tags: list[str]) -> None:
    cat_raw, tags_raw = encode_fields(dek, new_category, new_tags)
    conn.execute(
        "UPDATE documents SET category=?, tags=? WHERE id=?",
        (cat_raw, tags_raw, doc_id),
    )


def summarize(plans: list[dict]) -> dict:
    transitions = Counter()
    new_counts = Counter()
    old_counts = Counter()
    changed_ids = []
    for p in plans:
        old_counts[p["old_category"]] += 1
        new_counts[p["new_category"]] += 1
        if p["changed"]:
            transitions[(p["old_category"], p["new_category"])] += 1
            changed_ids.append(p["doc"]["id"])
    return {
        "total": len(plans),
        "changed": len(changed_ids),
        "unchanged": len(plans) - len(changed_ids),
        "old_counts": old_counts,
        "new_counts": new_counts,
        "transitions": transitions,
        "changed_ids": changed_ids,
    }


def print_summary(summary: dict, dry_run: bool) -> None:
    mode = "DRY-RUN（仅预览，未写库）" if dry_run else "已写库"
    print(f"\n===== 重分类结果 [{mode}] =====")
    print(f"存量文档总数      : {summary['total']}")
    print(f"需要更新(category或tags变化): {summary['changed']}")
    print(f"无需变化          : {summary['unchanged']}")
    if summary["total"] == 0:
        return
    print("\n[按扩展名归类后的新分布]")
    for cat in classify.FILE_CATEGORY_ORDER:
        if summary["new_counts"][cat]:
            print(f"  {cat:<4}: {summary['new_counts'][cat]}")
    print("\n[旧分布]")
    for cat, n in summary["old_counts"].most_common():
        print(f"  {cat:<4}: {n}")
    print("\n[主题→文件类型迁移明细(仅发生变化的)]")
    for (old, new), n in sorted(summary["transitions"].items(), key=lambda x: -x[1]):
        if n:
            print(f"  {old} -> {new} : {n}")


def main() -> int:
    ap = argparse.ArgumentParser(description="存量文档按扩展名重分类（只动 category/tags，执行前自动备份）")
    ap.add_argument("--db", default=None, help="直接指定 knowledge.db 路径（优先级最高）")
    ap.add_argument("--data-dir", default=None,
                    help="数据目录（含 knowledge.db 与 documents/；默认取 KB_DATA_DIR 环境变量，未设置则项目 data/）")
    ap.add_argument("--username", default=None, help="要处理的用户名（默认交互询问）")
    ap.add_argument("--password", default=None, help="该用户密码（不建议明文传参，默认交互输入）")
    ap.add_argument("--dry-run", action="store_true", help="仅预览影响行数与分布，不写库、不备份")
    ap.add_argument("--yes", action="store_true", help="跳过确认提示（正式写库仍会自动备份）")
    args = ap.parse_args()

    db_path = resolve_db_path(args)
    if not os.path.exists(db_path):
        print(f"[错误] 数据库不存在: {db_path}\n提示：可用 --data-dir 或 --db 指定外置数据目录。")
        return 2
    print(f"[信息] 使用数据库: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        users = list_users(conn)
        if not users:
            print("[错误] 库中无用户，无法继续。")
            return 2
        username = (args.username or "").strip()
        if not username:
            print("可选用户: " + ", ".join(f"{u['username']}(文档{u['doc_count']})" for u in users))
            username = input("输入要处理的用户名: ").strip() or (users[0]["username"] if len(users) == 1 else "")
        target = next((u for u in users if u["username"] == username.lower()), None)
        if target is None:
            print(f"[错误] 用户不存在: {username}")
            return 2
        print(f"[信息] 处理用户: {target['username']}（角色 {target['role']}，文档 {target['doc_count']} 条）")

        docs = fetch_docs(conn, target["id"])
        if not docs:
            print("[信息] 该用户没有存量文档，无需重分类。")
            print_summary({"total": 0, "changed": 0, "unchanged": 0,
                           "old_counts": Counter(), "new_counts": Counter(),
                           "transitions": Counter()}, dry_run=args.dry_run)
            return 0

        password = args.password
        if password is None:
            password = getpass.getpass(f"输入用户 {target['username']} 的密码（仅用于内存解开DEK）: ")
        if not password:
            print("[错误] 未提供密码，无法解密存量数据。")
            return 2
        dek = resolve_user_dek(conn, target["username"], password)

        plans = []
        for doc in docs:
            try:
                plans.append(plan_doc(doc, dek))
            except RuntimeError as e:
                print(f"[跳过] {e}")
        summary = summarize(plans)
        print_summary(summary, dry_run=args.dry_run)

        if summary["changed"] == 0:
            print("\n[信息] 无变化，无需写库。")
            return 0
        if args.dry_run:
            print("\n[提示] 已加 --dry-run，未写库、未备份。去掉 --dry-run 并加 --yes 正式执行。")
            return 0

        if not args.yes:
            ans = input(f"\n将更新 {summary['changed']} 条文档的 category/tags，"
                        f"执行前自动备份 knowledge.db。确认执行？[y/N] ").strip().lower()
            if ans not in ("y", "yes"):
                print("已取消。")
                return 0

        backup_path = backup_db(db_path)
        print(f"[备份] knowledge.db -> {backup_path}")

        conn.execute("BEGIN")
        try:
            for p in plans:
                if p["changed"]:
                    write_doc(conn, p["doc"]["id"], dek, p["new_category"], p["new_tags"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        print(f"[完成] 已更新 {summary['changed']} 条文档（category/tags）。")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(130)
