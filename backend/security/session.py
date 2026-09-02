# -*- coding: utf-8 -*-
"""内存会话表：token → 会话对象（含 DEK）。

设计要点:
- 会话与 DEK 只存在于进程内存，绝不落盘（退出/过期即销毁）
- 线程安全（Lock 保护）；惰性过期清理
- 会话令牌 256bit 熵（secrets.token_urlsafe(32)）
"""
import time
import threading
import secrets
from dataclasses import dataclass, field

DEFAULT_TIMEOUT = 30 * 60  # 30 分钟闲置过期
REMEMBER_TIMEOUT = 30 * 24 * 3600  # 记住我：30 天闲置有效


@dataclass
class Session:
    user_id: int
    username: str
    role: str
    dek: bytes
    must_change_password: int = 0
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    master_key: bytes | None = None  # 管理员会话额外携带系统 Master Key
    cache: dict = field(default_factory=dict)  # 会话级搜索/元数据缓存（登出即销毁）
    timeout: float = DEFAULT_TIMEOUT  # 本会话闲置超时（记住我=30 天）


class SessionStore:
    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self._timeout = timeout
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user_id: int, username: str, role: str, dek: bytes,
               must_change_password: int = 0, master_key: bytes | None = None,
               timeout: float | None = None) -> str:
        token = secrets.token_urlsafe(32)
        sess_timeout = timeout if timeout is not None else self._timeout
        with self._lock:
            self._sessions[token] = Session(
                user_id=user_id, username=username, role=role,
                dek=dek, must_change_password=must_change_password,
                master_key=master_key, timeout=sess_timeout,
            )
        return token

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            sess = self._sessions.get(token)
            if not sess:
                return None
            now = time.time()
            if now - sess.last_seen > sess.timeout:
                del self._sessions[token]
                return None
            sess.last_seen = now
            return sess

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def delete_by_user(self, user_id: int) -> None:
        """用户改密/被重置后，踢掉其全部旧会话。"""
        with self._lock:
            stale = [t for t, s in self._sessions.items() if s.user_id == user_id]
            for t in stale:
                self._sessions.pop(t, None)

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            stale = [t for t, s in self._sessions.items() if now - s.last_seen > s.timeout]
            for t in stale:
                self._sessions.pop(t, None)


# 全局唯一会话存储
sessions = SessionStore()
