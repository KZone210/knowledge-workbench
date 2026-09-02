# -*- coding: utf-8 -*-
"""存储模块：SQLite 元数据（敏感字段加密）+ 入库文件副本（AES-GCM 密文）。

安全模型：
- 磁盘上任何时刻不存在明文：documents 敏感列加密（enc_ver=1），文件副本为 .enc 密文
- 按用户隔离：每行归属 user_id，查询强制过滤当前用户
- 搜索：会话级内存倒排索引（jieba 分词 + 字段加权），登录态构建、登出销毁
- 解密一律走内存/响应流，绝不产生明文临时文件
"""
import os
import re
import json
import sqlite3
import uuid
from datetime import datetime

import jieba

from .security import vault, crypto
from .paths import get_data_dir, get_docs_dir, get_db_path

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = get_data_dir()          # 数据根目录（外置 KB_DATA_DIR 或项目内 data/）
DOCS_DIR = get_docs_dir()
DB_PATH = get_db_path()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER DEFAULT NULL,
    filename    TEXT NOT NULL,
    stored_name TEXT NOT NULL,
    file_size   INTEGER DEFAULT 0,
    ext         TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    category    TEXT DEFAULT '未分类',
    tags        TEXT DEFAULT '[]',
    keywords    TEXT DEFAULT '[]',
    summary     TEXT DEFAULT '',
    word_count  INTEGER DEFAULT 0,
    content     TEXT DEFAULT '',
    path        TEXT DEFAULT '',
    enc_ver     INTEGER DEFAULT 0,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_docs_category ON documents(category);
CREATE INDEX IF NOT EXISTS idx_docs_created ON documents(created_at);
"""

# 可搜索字段：(列名, 命中来源标签)。加密后仅在解密出的明文上匹配。
SEARCH_FIELDS = [
    ("filename", "文件名"),
    ("title", "标题"),
    ("category", "分类"),
    ("path", "路径"),
    ("tags", "标签"),
    ("keywords", "关键词"),
    ("summary", "摘要"),
    ("content", "内容"),
    ("stored_name", "存储名"),
]

# ---------------- 内存倒排索引（会话级缓存，登出销毁） ----------------
# 字段权重：标题/关键词命中比正文命中更相关
FIELD_WEIGHT = {
    "filename": 2, "title": 3, "category": 2, "tags": 2,
    "keywords": 3, "summary": 2, "path": 1,
}
# 正文索引截断上限（每篇前 N 字符），控制构建时间与内存
CONTENT_INDEX_LIMIT = 20000
STOPWORDS = {
    "的", "了", "是", "在", "和", "与", "及", "或", "等", "个", "这", "那",
    "我们", "你们", "他们", "一个", "没有", "可以", "这个", "那个", "进行",
    "以及", "并且", "但是", "因为", "所以", "如果", "就是", "不是", "对于",
    "the", "and", "for", "with", "that", "this", "are", "was", "were",
}


def _tokenize(text):
    """jieba 搜索引擎模式分词 + 英文/数字正则 token。过滤停用词与单字噪声。"""
    if not text:
        return []
    words = []
    for w in jieba.cut_for_search(text):
        w = w.strip().lower()
        if w and w not in STOPWORDS and (len(w) >= 2 or w.isdigit()):
            words.append(w)
    for m in re.finditer(r"[a-z0-9]+", text.lower()):
        t = m.group()
        if len(t) >= 2 and t not in STOPWORDS:
            words.append(t)
    return words


def _get_content(dek: bytes, doc_id: int) -> str:
    conn = _connect()
    row = conn.execute("SELECT content FROM documents WHERE id=?", (doc_id,)).fetchone()
    conn.close()
    if not row:
        return ""
    return crypto.dec_field(dek, row["content"] or "")


def _get_doc_cache(dek: bytes, user_id: int, cache: dict) -> dict:
    """会话级元数据缓存：全量解密 meta（不含 content），自校验 count+max_id 失效。"""
    conn = _connect()
    stat = conn.execute(
        "SELECT COUNT(*) AS c, COALESCE(MAX(id),0) AS m FROM documents WHERE user_id=?",
        (user_id,)).fetchone()
    conn.close()
    stamp = (stat["c"], stat["m"])
    data = cache.get("docs_meta")
    if data and data["stamp"] == stamp:
        return data

    conn = _connect()
    rows = conn.execute(
        """SELECT id, stored_name, file_size, ext, word_count, created_at, filename, title,
                  category, tags, keywords, summary, path
           FROM documents WHERE user_id=? ORDER BY id""", (user_id,)).fetchall()
    conn.close()
    docs, order = {}, []
    for r in rows:
        d = vault.dec_row(dek, dict(r))
        docs[d["id"]] = {
            "id": d["id"], "stored_name": d["stored_name"], "file_size": d["file_size"],
            "ext": d["ext"], "word_count": d["word_count"], "created_at": d["created_at"],
            "filename": d["filename"], "title": d["title"], "category": d["category"],
            "tags": d["tags"] or [], "keywords": d["keywords"] or [],
            "summary": d["summary"] or "", "path": d["path"] or "",
        }
        order.append(d["id"])
    data = {"stamp": stamp, "docs": docs, "order": order}
    cache["docs_meta"] = data
    return data


def _build_postings(dek: bytes, data: dict) -> dict:
    """构建倒排索引：term → {doc_id: 加权词频分}。"""
    tf = {}
    for doc_id, d in data["docs"].items():
        doc_tf = {}
        for field, weight in FIELD_WEIGHT.items():
            raw = d.get(field) or ""
            if isinstance(raw, list):
                raw = " ".join(raw)
            for w in _tokenize(raw):
                doc_tf[w] = doc_tf.get(w, 0) + weight
        content = _get_content(dek, doc_id)[:CONTENT_INDEX_LIMIT]
        for w in _tokenize(content):
            doc_tf[w] = doc_tf.get(w, 0) + 1
        for w, sc in doc_tf.items():
            m = tf.setdefault(w, {})
            m[doc_id] = m.get(doc_id, 0) + sc
    return tf


def search_documents(dek: bytes, user_id: int, q: str, category=None, tag=None,
                     limit=200, cache: dict | None = None):
    """倒排索引搜索：分词 → 命中评分 → 排序 → meta 过滤 → 按需解密 snippet。"""
    cache = cache if cache is not None else {}
    data = _get_doc_cache(dek, user_id, cache)
    postings = cache.get("postings")
    if not postings or postings["stamp"] != data["stamp"]:
        postings = {"stamp": data["stamp"], "map": _build_postings(dek, data)}
        cache["postings"] = postings

    terms = _tokenize(q)
    if not terms:
        return []
    scores = {}
    for t in terms:
        for doc_id, sc in postings["map"].get(t, {}).items():
            scores[doc_id] = scores.get(doc_id, 0) + sc
    if not scores:
        return []

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    out = []
    for doc_id, sc in ranked:
        d = data["docs"].get(doc_id)
        if not d:
            continue
        if category and category != "全部" and d["category"] != category:
            continue
        if tag and tag not in (d.get("tags") or []):
            continue
        matched = [label for col, label in SEARCH_FIELDS if q in str(d.get(col) or "")]
        item = dict(d)
        if matched:
            item["matched"] = matched
        content = _get_content(dek, doc_id)
        item["snippet"] = _snippet(content, q) or _snippet(d.get("summary") or "", q) or ""
        out.append(item)
        if len(out) >= limit:
            break
    return out


def _connect():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """建表 + 老库加列（user_id/enc_ver）。enc_ver=0 的明文行由 security 迁移。"""
    os.makedirs(DOCS_DIR, exist_ok=True)
    conn = _connect()
    conn.executescript(_SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "path" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN path TEXT DEFAULT ''")
    if "user_id" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN user_id INTEGER DEFAULT NULL")
    if "enc_ver" not in cols:
        conn.execute("ALTER TABLE documents ADD COLUMN enc_ver INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_user ON documents(user_id)")
    conn.commit()
    conn.close()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def save_plain_to_enc(dek: bytes, plain_path: str, ext: str) -> tuple[str, str]:
    """把明文临时文件加密落盘为唯一密文副本。

    返回 (stored_name, enc_abs_path)。明文临时文件由调用方负责删除。
    """
    os.makedirs(DOCS_DIR, exist_ok=True)
    ext = (ext or ".bin").lower()
    stored_name = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}{ext}"
    enc_path = os.path.join(DOCS_DIR, stored_name)
    vault.encrypt_doc_file(dek, plain_path, enc_path)
    return stored_name, enc_path


def insert_document(meta: dict, user_id: int, dek: bytes) -> int:
    """元数据字段加密后入库（enc_ver=1）。"""
    m = vault.enc_meta(dek, meta)
    conn = _connect()
    cur = conn.execute(
        """INSERT INTO documents
           (user_id, filename, stored_name, file_size, ext, title, category, tags, keywords, summary, word_count, content, path, enc_ver, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
        (
            user_id, m["filename"], m["stored_name"], m["file_size"], m["ext"],
            m["title"], m["category"], m["tags"], m["keywords"],
            m["summary"], m["word_count"], m["content"], m["path"], _now(),
        ),
    )
    conn.commit()
    doc_id = cur.lastrowid
    conn.close()
    return doc_id


def _snippet(text, q, width=42):
    """提取命中词附近的内容片段。"""
    if not text or not q:
        return ""
    idx = text.find(q)
    if idx < 0:
        return ""
    start = max(0, idx - width)
    end = min(len(text), idx + len(q) + width)
    frag = re.sub(r"\s+", " ", text[start:end]).strip()
    return ("…" if start > 0 else "") + frag + ("…" if end < len(text) else "")


def _dec_rows(dek: bytes, rows):
    """批量解密行（统一处理 tags/keywords JSON）。"""
    docs = []
    for r in rows:
        d = vault.dec_row(dek, dict(r))
        docs.append(d)
    return docs


def list_documents(dek: bytes, user_id: int, category=None, q=None, tag=None, limit=200,
                   cache: dict | None = None):
    """列表（不含全文 content），按时间倒序。
    - 有搜索词：走倒排索引（search_documents）
    - 浏览/过滤：走会话级 meta 缓存（首次构建后毫秒级）
    """
    if q:
        return search_documents(dek, user_id, q, category, tag, limit, cache)

    cache = cache if cache is not None else {}
    data = _get_doc_cache(dek, user_id, cache)
    docs = []
    for doc_id in data["order"]:
        d = data["docs"][doc_id]
        if category and category != "全部" and d.get("category") != category:
            continue
        if tag and tag not in (d.get("tags") or []):
            continue
        item = dict(d)
        item["matched"] = []
        item["snippet"] = ""
        docs.append(item)
        if len(docs) >= limit:
            break
    return docs


def get_document(doc_id: int, dek: bytes, user_id: int):
    conn = _connect()
    row = conn.execute(
        "SELECT * FROM documents WHERE id=? AND user_id=?", (doc_id, user_id)).fetchone()
    conn.close()
    if not row:
        return None
    return vault.dec_row(dek, dict(row))


def delete_document(doc_id: int, user_id: int) -> bool:
    conn = _connect()
    row = conn.execute(
        "SELECT stored_name FROM documents WHERE id=? AND user_id=?", (doc_id, user_id)).fetchone()
    if not row:
        conn.close()
        return False
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit()
    conn.close()
    path = os.path.join(DOCS_DIR, row["stored_name"])
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
    return True


def decrypt_doc_bytes(dek: bytes, stored_name: str) -> bytes:
    """解密文档副本为内存字节（serve_file 流式响应用）。"""
    path = os.path.join(DOCS_DIR, stored_name)
    if not os.path.exists(path):
        raise FileNotFoundError(stored_name)
    return vault.decrypt_doc_file(dek, path)


def doc_owned_by(stored_name: str, user_id: int) -> bool:
    """归属校验：该文件存储名必须属于指定用户（serve_file 纵深防御）。"""
    conn = _connect()
    row = conn.execute(
        "SELECT id FROM documents WHERE stored_name=? AND user_id=?",
        (stored_name, user_id)).fetchone()
    conn.close()
    return row is not None


def category_counts(dek: bytes, user_id: int):
    """分类统计（解密后内存分组）。"""
    conn = _connect()
    rows = conn.execute(
        "SELECT category FROM documents WHERE user_id=?", (user_id,)).fetchall()
    conn.close()
    counts = {}
    for r in rows:
        cat = vault.dec_row(dek, dict(r)).get("category") or "未分类"
        counts[cat] = counts.get(cat, 0) + 1
    total = sum(counts.values())
    return total, counts


def stats(dek: bytes, user_id: int):
    """统计（解密后聚合）。"""
    conn = _connect()
    rows = conn.execute(
        """SELECT category, tags, word_count, file_size FROM documents WHERE user_id=?""",
        (user_id,)).fetchall()
    conn.close()
    total = len(rows)
    total_words = sum(r["word_count"] or 0 for r in rows)
    total_size = sum(r["file_size"] or 0 for r in rows)
    tag_counter = {}
    for r in rows:
        d = vault.dec_row(dek, dict(r))
        for t in (d.get("tags") or []):
            tag_counter[t] = tag_counter.get(t, 0) + 1
    top_tags = sorted(tag_counter.items(), key=lambda x: -x[1])[:40]
    return {
        "total": total,
        "total_words": total_words,
        "total_size": total_size,
        "top_tags": [{"name": k, "count": v} for k, v in top_tags],
    }
