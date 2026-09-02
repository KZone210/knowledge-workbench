# -*- coding: utf-8 -*-
"""认证与密钥管理模块。

职责:
- Argon2id 密码哈希 / 验证（不可逆）
- 两层密钥体系：密码 → KEK → 解开 wrapped_dek → DEK（AES-256-GCM 包裹）
- Master Key 双通道：wrapped_dek_admin = AES-GCM(master_key, DEK)
- 安全问题第二钥匙通道：answer_KEK 单独包裹 DEK
- 注册 / 登录 / 登出 / 改密 / 安全问题找回 / 管理员重置密码
- 登录与答题限流（连续失败锁定）
- 系统初始化：默认管理员 King/King + system_vault + 老明文库迁移

安全约束:
- DEK / KEK / master_key / 密码 / 安全答案 只在内存，绝不落盘、绝不进日志
- 所有密钥包裹更新走 SQLite 单事务 + key_wraps 版本化，防半更新
"""
import os
import base64
import secrets
import sqlite3
import threading
import time
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError
from argon2.low_level import hash_secret_raw, Type

from . import crypto
from .session import sessions, REMEMBER_TIMEOUT
from ..paths import get_db_path, get_data_dir, get_docs_dir

# ---------------- Argon2id ----------------
_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=1)
# 登录失败时用于"伪校验"的假哈希（耗时一致，不泄露账号是否存在）
_DUMMY_HASH = _ph.hash("dummy-password-zz")

MAX_FAILS = 5
LOCK_MINUTES = 15

# 全局进程级 Master Key 缓存（首次管理员登录/创建时激活；注册与管理员重置依赖它）
_master_key: bytes | None = None
_master_lock = threading.Lock()

DB_PATH = get_db_path()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------- 工具 ----------------
def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_master_key() -> bytes | None:
    """进程内 Master Key（线程安全读取）。"""
    with _master_lock:
        return _master_key


def _set_master_key(k: bytes | None) -> None:
    global _master_key
    with _master_lock:
        _master_key = k


# ---------------- 密码与密钥派生 ----------------
def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(pw_hash: str, password: str) -> bool:
    try:
        return _ph.verify(pw_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _derive_kek(password: str, salt_b64: str) -> bytes:
    """KEK = Argon2id(password, salt) → 32B 原始密钥（不落盘）。"""
    salt = base64.b64decode(salt_b64)
    return hash_secret_raw(
        password.encode("utf-8"), salt,
        time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, type=Type.ID,
    )


# ---------------- 限流 ----------------
def _check_lock(row) -> None:
    """账号锁定检查。锁定期间返回与密码错误一致的提示（防账号存在性/锁定状态枚举），
    且执行一次伪校验使耗时对齐（防时序侧信道）。"""
    if row["lock_until"]:
        until = datetime.strptime(row["lock_until"], "%Y-%m-%d %H:%M:%S")
        if until > datetime.now():
            verify_password(_DUMMY_HASH, "dummy-password-zz")
            raise PermissionError("用户名或密码错误")


def _register_fail(user_id: int) -> None:
    conn = _conn()
    conn.execute("UPDATE users SET login_fail = login_fail + 1 WHERE id = ?", (user_id,))
    row = conn.execute("SELECT login_fail FROM users WHERE id = ?", (user_id,)).fetchone()
    if row and row["login_fail"] >= MAX_FAILS:
        from datetime import timedelta
        lock_until = (datetime.now() + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute("UPDATE users SET lock_until = ?, login_fail = 0 WHERE id = ?",
                     (lock_until, user_id))
    conn.commit()
    conn.close()


def _clear_fail(user_id: int) -> None:
    conn = _conn()
    conn.execute("UPDATE users SET login_fail = 0, lock_until = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


# ---------------- 审计 ----------------
def audit(actor: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:
    """写入审计日志。ip 为来源地址（本地回环归一化为 loopback），并入 detail 前缀。
    审计失败不阻断主流程。"""
    if ip:
        detail = f"[{ip}] {detail}".strip()
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO audit_log (actor, action, target, detail, created_at) VALUES (?,?,?,?,?)",
            (actor, action, target, detail[:200], _now()))
        conn.commit()
        conn.close()
    except Exception:
        pass  # 审计失败不阻断主流程


# ---------------- 系统初始化 ----------------
def init_security() -> None:
    """建安全表 → 清理残留明文临时文件 → 确保默认管理员 → 迁移老明文库。幂等。"""
    conn = _conn()
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()

    _cleanup_stray_tmp()
    row = _get_admin()
    if row is None:
        _create_default_admin()
    _migrate_legacy_documents()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE,
    role          TEXT NOT NULL DEFAULT 'user',
    password_hash TEXT NOT NULL,
    salt_hash     TEXT NOT NULL,
    salt_kek      TEXT NOT NULL,
    wrapped_dek   TEXT NOT NULL,
    wrapped_dek_admin TEXT,
    dek_check     TEXT NOT NULL,
    recovery_hash TEXT,
    recovery_wrap TEXT,
    must_change_password INTEGER DEFAULT 0,
    created_at    TEXT,
    last_login    TEXT,
    login_fail    INTEGER DEFAULT 0,
    lock_until    TEXT
);
CREATE TABLE IF NOT EXISTS system_vault (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    wrapped_master TEXT NOT NULL,
    salt_admin_kek TEXT NOT NULL,
    created_at     TEXT,
    updated_at     TEXT
);
CREATE TABLE IF NOT EXISTS security_questions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER NOT NULL,
    question         TEXT NOT NULL,
    answer_hash      TEXT NOT NULL,
    salt_answer      TEXT NOT NULL,
    salt_answer_kek  TEXT NOT NULL,
    wrapped_dek_answer TEXT NOT NULL,
    created_at       TEXT
);
CREATE TABLE IF NOT EXISTS key_wraps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    version     INTEGER NOT NULL,
    salt_kek    TEXT NOT NULL,
    wrapped_dek TEXT NOT NULL,
    created_at  TEXT,
    retired     INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target      TEXT,
    detail      TEXT DEFAULT '',
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_questions_user ON security_questions(user_id);
CREATE INDEX IF NOT EXISTS idx_wraps_user ON key_wraps(user_id);
"""


def _get_admin():
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    conn.close()
    return row


def _create_default_admin() -> None:
    """首次启动创建默认管理员 King/King + system_vault（Master Key 即刻激活）。"""
    username, password = "king", "king"
    salt_hash = base64.b64encode(secrets.token_bytes(16)).decode()
    salt_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    pw_hash = hash_password(password)
    kek = _derive_kek(password, salt_kek)
    dek = crypto.gen_key()
    wrapped = base64.b64encode(crypto.enc_bytes(kek, dek)).decode()

    master = crypto.gen_key()
    salt_admin_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    admin_kek = _derive_kek(password, salt_admin_kek)
    wrapped_master = base64.b64encode(crypto.enc_bytes(admin_kek, master)).decode()
    wrapped_admin = base64.b64encode(crypto.enc_bytes(master, dek)).decode()
    dek_check = crypto.make_dek_check(dek)

    conn = _conn()
    cur = conn.execute(
        """INSERT INTO users (username, role, password_hash, salt_hash, salt_kek, wrapped_dek,
           wrapped_dek_admin, dek_check, must_change_password, created_at)
           VALUES (?,?,?,?,?,?,?,?,1,?)""",
        (username, "admin", pw_hash, salt_hash, salt_kek, wrapped,
         wrapped_admin, dek_check, _now()))
    admin_id = cur.lastrowid
    conn.execute(
        "INSERT INTO system_vault (id, wrapped_master, salt_admin_kek, created_at) VALUES (1,?,?,?)",
        (wrapped_master, salt_admin_kek, _now()))
    conn.commit()
    conn.close()

    _set_master_key(master)
    audit("system", "init_default_admin", username)
    # 管理员 DEK 入 key_wraps 版本历史
    _archive_wrap(admin_id, salt_kek, wrapped, retired=1)
    print("\n  [初始化] 已创建默认管理员账号: king / king（首次登录请立即修改密码）\n")


def _archive_wrap(user_id: int, salt_kek: str, wrapped_dek: str, retired: int = 0) -> None:
    try:
        conn = _conn()
        ver = conn.execute(
            "SELECT COALESCE(MAX(version),0)+1 FROM key_wraps WHERE user_id=?", (user_id,)
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO key_wraps (user_id, version, salt_kek, wrapped_dek, created_at, retired) VALUES (?,?,?,?,?,?)",
            (user_id, ver, salt_kek, wrapped_dek, _now(), retired))
        conn.commit()
        conn.close()
    except Exception:
        pass


# ---------------- 老明文库迁移 ----------------
def _cleanup_stray_tmp() -> None:
    """启动清理：删除崩溃残留的明文临时文件（. 开头的隐藏临时文件），杜绝明文残留。"""
    docs_dir = get_docs_dir()
    if os.path.isdir(docs_dir):
        for f in os.listdir(docs_dir):
            if f.startswith("."):  # 临时明文文件统一以 . 开头（如 .abc.xlsx）
                try:
                    os.remove(os.path.join(docs_dir, f))
                except OSError:
                    pass


def _migrate_legacy_documents() -> None:
    """将 enc_ver=0 的明文行迁移为密文（DB 字段 + 文件副本双重加密，归属管理员）。"""
    conn = _conn()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(documents)").fetchall()}
    if "enc_ver" not in cols:
        conn.close()
        return  # store.init_db 尚未加列

    admin = _get_admin()
    if not admin:
        conn.close()
        return
    # 解出管理员 DEK（默认密码 king；若已改密则无法自动迁移，跳过）
    kek = _derive_kek("king", admin["salt_kek"]) if admin["username"] == "king" else None
    if kek is None:
        conn.close()
        return
    try:
        dek = crypto.dec_bytes(kek, base64.b64decode(admin["wrapped_dek"]))
    except Exception:
        conn.close()
        return

    rows = conn.execute(
        "SELECT id, enc_ver, stored_name, filename, title, category, tags, keywords, summary, content, path FROM documents"
    ).fetchall()
    docs_dir = get_docs_dir()
    for r in rows:
        # 1) 文件副本：按文件头检测，非 KBENC 密文则加密覆盖（幂等，兼容字段已加密但文件漏加密的历史状态）
        fpath = os.path.join(docs_dir, r["stored_name"])
        if os.path.exists(fpath):
            try:
                with open(fpath, "rb") as f:
                    head = f.read(len(crypto.MAGIC))
                if head != crypto.MAGIC:
                    crypto.encrypt_file(dek, fpath, fpath)
            except Exception:
                pass
        # 2) DB 敏感字段：enc_ver=0 才加密
        if r["enc_ver"] == 0:
            conn.execute(
                """UPDATE documents SET filename=?, title=?, category=?, tags=?, keywords=?,
                   summary=?, content=?, path=?, enc_ver=1, user_id=? WHERE id=?""",
                (
                    crypto.enc_field(dek, r["filename"] or ""),
                    crypto.enc_field(dek, r["title"] or ""),
                    crypto.enc_field(dek, r["category"] or "未分类"),
                    crypto.enc_field(dek, r["tags"] or "[]"),
                    crypto.enc_field(dek, r["keywords"] or "[]"),
                    crypto.enc_field(dek, r["summary"] or ""),
                    crypto.enc_field(dek, r["content"] or ""),
                    crypto.enc_field(dek, r["path"] or ""),
                    admin["id"],
                    r["id"],
                ))
    if rows:
        conn.commit()
        audit("system", "migrate_legacy_documents", f"{len(rows)} docs")
    conn.close()
    _set_master_key(None)  # 迁移完成不再保留进程密钥（登录时重新激活）


# ---------------- 注册 ----------------
def _validate_password(pw: str, ctx: str = "密码") -> str:
    """统一密码强度校验（含长度上限，防超大输入耗尽 Argon2 计算资源）。"""
    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 128:
        raise ValueError(f"{ctx}需 8-128 位")
    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):
        raise ValueError(f"{ctx}至少 8 位且同时包含字母和数字")
    return pw


def register_user(username: str, password: str, questions: list[dict] | None = None,
                  ip: str = "") -> dict:
    """注册普通用户。questions: [{"question": str, "answer": str}, ...]（1-3 个）。

    Master Key 未激活（管理员未登录过）时拒绝注册 —— 保证 wrapped_dek_admin 一定可建。
    """
    username = username.strip().lower()
    if not (3 <= len(username) <= 32):
        raise ValueError("用户名需 3-32 位")
    if not all(c.isalnum() or c in "_-" for c in username):
        raise ValueError("用户名仅允许字母/数字/下划线/中划线")
    _validate_password(password)

    master = get_master_key()
    if master is None:
        raise PermissionError("系统尚未初始化，请先以管理员身份登录一次")

    salt_hash = base64.b64encode(secrets.token_bytes(16)).decode()
    salt_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    pw_hash = hash_password(password)
    kek = _derive_kek(password, salt_kek)
    dek = crypto.gen_key()
    wrapped = base64.b64encode(crypto.enc_bytes(kek, dek)).decode()
    wrapped_admin = base64.b64encode(crypto.enc_bytes(master, dek)).decode()
    dek_check = crypto.make_dek_check(dek)

    questions = questions or []
    if questions:
        if not (1 <= len(questions) <= 3):
            raise ValueError("安全问题数量需为 1-3 个")
        for q in questions:
            if len(q.get("answer", "")) < 4:
                raise ValueError("安全答案至少 4 位")

    conn = _conn()
    try:
        cur = conn.execute(
            """INSERT INTO users (username, role, password_hash, salt_hash, salt_kek, wrapped_dek,
               wrapped_dek_admin, dek_check, created_at) VALUES (?,?,?,?,?,?,?,?,?)""",
            (username, "user", pw_hash, salt_hash, salt_kek, wrapped,
             wrapped_admin, dek_check, _now()))
        uid = cur.lastrowid
        for q in questions:
            answer = q["answer"]
            s_answer = base64.b64encode(secrets.token_bytes(16)).decode()
            s_akek = base64.b64encode(secrets.token_bytes(16)).decode()
            a_hash = hash_password(answer)
            a_kek = _derive_kek(answer, s_akek)
            w_answer = base64.b64encode(crypto.enc_bytes(a_kek, dek)).decode()
            conn.execute(
                """INSERT INTO security_questions (user_id, question, answer_hash, salt_answer,
                   salt_answer_kek, wrapped_dek_answer, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (uid, q["question"][:200], a_hash, s_answer, s_akek, w_answer, _now()))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        conn.close()
        raise ValueError("用户名已存在")
    conn.close()

    _archive_wrap(uid, salt_kek, wrapped, retired=1)
    audit("system", "register", username, ip=ip)
    return {"id": uid, "username": username, "questions": len(questions)}


# ---------------- 登录 / 登出 ----------------
def login(username: str, password: str, remember: bool = False, ip: str = "") -> dict:
    """登录。成功 → 创建会话；失败 → 统一提示（不泄露账号存在性）。
    remember=True → 30 天长期会话（"记住我"，同一设备免密访问）。"""
    username = username.strip().lower()
    if not isinstance(password, str) or not password or len(password) > 128:
        # 超大/缺失密码直接按失败处理（耗时对齐防探测）
        verify_password(_DUMMY_HASH, "dummy-password-zz")
        raise ValueError("用户名或密码错误")
    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        conn.close()
        # 伪造一次哈希校验，耗时不泄露账号是否存在
        verify_password(_DUMMY_HASH, "dummy-password-zz")
        raise ValueError("用户名或密码错误")
    try:
        _check_lock(row)
    except PermissionError as e:
        conn.close()
        raise ValueError(str(e))

    if not verify_password(row["password_hash"], password):
        _register_fail(row["id"])
        conn.close()
        raise ValueError("用户名或密码错误")

    _clear_fail(row["id"])
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (_now(), row["id"]))
    conn.commit()
    conn.close()

    # 解出 DEK
    kek = _derive_kek(password, row["salt_kek"])
    try:
        dek = crypto.dec_bytes(kek, base64.b64decode(row["wrapped_dek"]))
    except Exception:
        raise ValueError("用户名或密码错误")
    if not crypto.verify_dek(dek, row["dek_check"]):
        raise ValueError("密钥校验失败，请联系管理员")

    # 管理员登录 → 激活 Master Key（从 system_vault 解出并缓存）
    master = None
    if row["role"] == "admin":
        master = _activate_master(password)

    timeout = REMEMBER_TIMEOUT if remember else None
    token = sessions.create(
        user_id=row["id"], username=row["username"], role=row["role"],
        dek=dek, must_change_password=row["must_change_password"], master_key=master,
        timeout=timeout,
    )
    audit("auth", "login", row["username"], ip=ip)
    return {
        "token": token, "username": row["username"], "role": row["role"],
        "must_change_password": row["must_change_password"],
    }


def _activate_master(admin_password: str) -> bytes:
    """管理员登录时解出并缓存系统 Master Key。"""
    cached = get_master_key()
    if cached:
        return cached
    conn = _conn()
    vault = conn.execute("SELECT * FROM system_vault WHERE id=1").fetchone()
    admin = conn.execute("SELECT * FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    conn.close()
    if not vault or not admin:
        raise ValueError("系统未初始化")
    admin_kek = _derive_kek(admin_password, vault["salt_admin_kek"])
    try:
        master = crypto.dec_bytes(admin_kek, base64.b64decode(vault["wrapped_master"]))
    except Exception:
        raise ValueError("管理员密码错误")
    _set_master_key(master)
    return master


def logout(token: str | None) -> None:
    if token:
        sessions.delete(token)


def get_session(token: str | None):
    return sessions.get(token)


# ---------------- 改密（用户自助） ----------------
def change_password(user_id: int, old_password: str, new_password: str, ip: str = "") -> None:
    """改密：只重新包裹 DEK，不重加密数据。事务 + 版本化 + 自检。"""
    _validate_password(new_password)

    conn = _conn()
    row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        conn.close()
        raise ValueError("用户不存在")
    if not verify_password(row["password_hash"], old_password):
        conn.close()
        raise ValueError("原密码错误")

    kek = _derive_kek(old_password, row["salt_kek"])
    try:
        dek = crypto.dec_bytes(kek, base64.b64decode(row["wrapped_dek"]))
    except Exception:
        conn.close()
        raise ValueError("原密码错误")
    if not crypto.verify_dek(dek, row["dek_check"]):
        conn.close()
        raise ValueError("密钥自检失败，操作已中止（数据未变动）")

    new_salt_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    new_kek = _derive_kek(new_password, new_salt_kek)
    new_wrapped = base64.b64encode(crypto.enc_bytes(new_kek, dek)).decode()
    new_hash = hash_password(new_password)

    # 管理员改密：必须同步用新密码 KEK 重新包裹 system_vault 的 master_key，
    # 否则改密后管理员登录将无法解出 Master Key（"管理员密码错误"）。
    vault_update = None  # (new_wrapped_master, new_salt_admin_kek)
    if row["role"] == "admin":
        try:
            master = get_master_key()
            if master is None:
                # 服务重启后缓存缺失：用旧密码解出 master
                v = _conn()
                vault = v.execute("SELECT * FROM system_vault WHERE id=1").fetchone()
                v.close()
                if vault:
                    old_admin_kek = _derive_kek(old_password, vault["salt_admin_kek"])
                    master = crypto.dec_bytes(old_admin_kek, base64.b64decode(vault["wrapped_master"]))
            if master is not None:
                new_admin_salt = base64.b64encode(secrets.token_bytes(16)).decode()
                new_admin_kek = _derive_kek(new_password, new_admin_salt)
                new_wrapped_master = base64.b64encode(crypto.enc_bytes(new_admin_kek, master)).decode()
                vault_update = (new_wrapped_master, new_admin_salt)
        except Exception:
            # master 重包失败不阻断用户改密（master 可稍后由管理员旧会话/旧密码恢复）
            vault_update = None

    try:
        with conn:  # 单事务
            old_row = conn.execute("SELECT wrapped_dek, salt_kek FROM users WHERE id=?", (user_id,)).fetchone()
            conn.execute(
                "UPDATE users SET password_hash=?, salt_kek=?, wrapped_dek=?, must_change_password=0 WHERE id=?",
                (new_hash, new_salt_kek, new_wrapped, user_id))
            ver = conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM key_wraps WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO key_wraps (user_id, version, salt_kek, wrapped_dek, created_at, retired) VALUES (?,?,?,?,?,?)",
                (user_id, ver, old_row["salt_kek"], old_row["wrapped_dek"], _now(), 1))
            if vault_update:
                conn.execute(
                    "UPDATE system_vault SET wrapped_master=?, salt_admin_kek=?, updated_at=? WHERE id=1",
                    (vault_update[0], vault_update[1], _now()))
    except Exception:
        conn.close()
        raise
    conn.close()

    sessions.delete_by_user(user_id)  # 改密后旧会话全部失效
    audit("auth", "change_password", f"user#{user_id}", ip=ip)


# ---------------- 安全问题找回 ----------------
def get_security_questions(username: str) -> list[str]:
    """找回第一步：返回该用户的问题列表（不暴露任何其他信息）。

    枚举防护依赖两层：IP 限流（app 路由层）+ 输入长度限制；问题本身不敏感。
    """
    username = username.strip().lower()[:64]
    conn = _conn()
    row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        conn.close()
        return []
    qs = conn.execute(
        "SELECT question FROM security_questions WHERE user_id=? ORDER BY id", (row["id"],)
    ).fetchall()
    conn.close()
    return [q["question"] for q in qs]


def reset_password_via_questions(username: str, answers: list[str], new_password: str,
                                 ip: str = "") -> None:
    """找回第二步：全部答对 → 解 DEK → 设新密码（单事务原子）。

    限流：任一题连续失败 5 次锁定 15 分钟（复用 users.login_fail/lock_until）。
    """
    _validate_password(new_password)
    username = username.strip().lower()
    conn = _conn()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        conn.close()
        raise ValueError("用户名或答案错误")
    try:
        _check_lock(user)
    except PermissionError as e:
        conn.close()
        raise ValueError(str(e))

    qs = conn.execute(
        "SELECT * FROM security_questions WHERE user_id=? ORDER BY id", (user["id"],)
    ).fetchall()
    conn.close()
    if not qs:
        raise ValueError("该用户未设置安全问题")

    # 全部答对才放行
    ok = len(qs) == len(answers)
    if ok:
        for q, ans in zip(qs, answers):
            if not verify_password(q["answer_hash"], ans):
                ok = False
                break
    if not ok:
        _register_fail(user["id"])
        raise ValueError("用户名或答案错误")

    # 解 DEK：用第一个问题的 answer_KEK
    q0 = qs[0]
    a_kek = _derive_kek(answers[0], q0["salt_answer_kek"])
    try:
        dek = crypto.dec_bytes(a_kek, base64.b64decode(q0["wrapped_dek_answer"]))
    except Exception:
        _register_fail(user["id"])
        raise ValueError("用户名或答案错误")
    if not crypto.verify_dek(dek, user["dek_check"]):
        _register_fail(user["id"])
        raise ValueError("密钥校验失败，请联系管理员")

    new_salt_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    new_kek = _derive_kek(new_password, new_salt_kek)
    new_wrapped = base64.b64encode(crypto.enc_bytes(new_kek, dek)).decode()
    new_hash = hash_password(new_password)

    try:
        with _conn() as c:
            old = c.execute("SELECT wrapped_dek, salt_kek FROM users WHERE id=?", (user["id"],)).fetchone()
            c.execute(
                "UPDATE users SET password_hash=?, salt_kek=?, wrapped_dek=?, must_change_password=0, login_fail=0, lock_until=NULL WHERE id=?",
                (new_hash, new_salt_kek, new_wrapped, user["id"]))
            ver = c.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM key_wraps WHERE user_id=?", (user["id"],)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO key_wraps (user_id, version, salt_kek, wrapped_dek, created_at, retired) VALUES (?,?,?,?,?,?)",
                (user["id"], ver, old["salt_kek"], old["wrapped_dek"], _now(), 1))
    except Exception:
        raise
    sessions.delete_by_user(user["id"])
    audit("auth", "reset_via_questions", username, ip=ip)


# ---------------- 管理员操作 ----------------
def admin_list_users() -> list[dict]:
    """管理员：查看用户基本信息（不含密钥材料、不含内容）。"""
    conn = _conn()
    rows = conn.execute(
        """SELECT id, username, role, must_change_password, created_at, last_login,
                  (SELECT COUNT(*) FROM security_questions sq WHERE sq.user_id=users.id) AS question_count,
                  (SELECT COUNT(*) FROM documents d WHERE d.user_id=users.id) AS doc_count
           FROM users ORDER BY id""").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def admin_reset_password(admin_username: str, target_username: str, new_password: str,
                         ip: str = "") -> None:
    """管理员重置用户密码（Master Key 通道，不依赖旧密码）。

    事务原子 + 版本化 + 自检；重置后强制用户下次改密并踢掉旧会话。
    """
    _validate_password(new_password)
    target_username = target_username.strip().lower()
    master = get_master_key()
    if master is None:
        raise PermissionError("Master Key 未激活，请重新以管理员身份登录")

    conn = _conn()
    target = conn.execute("SELECT * FROM users WHERE username=?", (target_username,)).fetchone()
    conn.close()
    if not target:
        raise ValueError("目标用户不存在")
    if target["role"] == "admin":
        raise ValueError("不能重置管理员密码")

    # 解 DEK：优先 wrapped_dek_admin（master 通道）；缺失则用 key_wraps 历史中的用户包裹尝试
    dek = None
    if target["wrapped_dek_admin"]:
        try:
            dek = crypto.dec_bytes(master, base64.b64decode(target["wrapped_dek_admin"]))
        except Exception:
            dek = None
    if dek is None:
        # 回退：从 key_wraps 历史取最新活跃包裹，但需要用户旧密码的 KEK——没有。
        # 因此无法解出 → 拒绝并提示
        raise ValueError("该用户缺少管理员密钥通道，无法重置（请让用户本人通过安全问题找回）")

    if not crypto.verify_dek(dek, target["dek_check"]):
        raise ValueError("密钥自检失败，操作已中止")

    new_salt_kek = base64.b64encode(secrets.token_bytes(16)).decode()
    new_kek = _derive_kek(new_password, new_salt_kek)
    new_wrapped = base64.b64encode(crypto.enc_bytes(new_kek, dek)).decode()
    new_hash = hash_password(new_password)
    # 用 master 重新包裹 DEK（保持管理员通道可用）
    new_wrapped_admin = base64.b64encode(crypto.enc_bytes(master, dek)).decode()

    try:
        with _conn() as c:
            old = c.execute("SELECT wrapped_dek, salt_kek FROM users WHERE id=?", (target["id"],)).fetchone()
            c.execute(
                """UPDATE users SET password_hash=?, salt_kek=?, wrapped_dek=?, wrapped_dek_admin=?,
                   must_change_password=1, login_fail=0, lock_until=NULL WHERE id=?""",
                (new_hash, new_salt_kek, new_wrapped, new_wrapped_admin, target["id"]))
            ver = c.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM key_wraps WHERE user_id=?", (target["id"],)
            ).fetchone()[0]
            c.execute(
                "INSERT INTO key_wraps (user_id, version, salt_kek, wrapped_dek, created_at, retired) VALUES (?,?,?,?,?,?)",
                (target["id"], ver, old["salt_kek"], old["wrapped_dek"], _now(), 1))
    except Exception:
        raise
    sessions.delete_by_user(target["id"])
    audit("admin", "reset_password", target_username, f"by {admin_username}", ip=ip)


def audit_log_recent(limit: int = 100) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT actor, action, target, detail, created_at FROM audit_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
