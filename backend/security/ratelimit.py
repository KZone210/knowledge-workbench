# -*- coding: utf-8 -*-
"""IP 级限流：内存滑动窗口（按 来源IP + 动作 独立计数）。

防御目标（账号级锁定挡不住的场景）：
- 登录/找回/改密等认证接口被分布式暴力破解（多用户名轮换绕开单账号锁定）
- 注册接口被批量滥用（垃圾账号泛滥）
- 认证接口被高频探测（用户名枚举 / 存在性确认）

实现：进程内存 dict[key -> deque[时间戳]]，滑动窗口内超限即拒绝（429）。
惰性清理过期条目与空队列，防止 key 无限膨胀。
"""
import time
from collections import deque, defaultdict
from threading import Lock


class RateLimiter:
    def __init__(self, limit: int, window: float, max_keys: int = 10000):
        self.limit = limit
        self.window = window
        self.max_keys = max_keys
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        """窗口内允许则记录本次并返回 True；超限返回 False。"""
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > self.max_keys:
                self._sweep(now)
            return True

    def _sweep(self, now: float):
        """清理空队列，防止 key 无限膨胀。"""
        for k in [k for k, q in self._hits.items() if not q]:
            del self._hits[k]


# 全局限流器实例（按动作维度，独立窗口）
login_limiter     = RateLimiter(limit=10, window=60)    # 登录：10 次/分钟/IP
register_limiter  = RateLimiter(limit=20, window=3600)  # 注册：20 次/小时/IP
questions_limiter = RateLimiter(limit=20, window=60)    # 安全问题获取：20 次/分钟/IP
reset_limiter     = RateLimiter(limit=10, window=60)    # 安全问题重置：10 次/分钟/IP
change_pw_limiter = RateLimiter(limit=10, window=60)    # 改密：10 次/分钟/IP


def client_ip(request) -> str:
    """取客户端 IP（回环地址归一化；不透传不可信的 X-Forwarded-For，本地直连无需代理头）。"""
    host = getattr(request.client, "host", "") or ""
    if host in ("127.0.0.1", "::1"):
        return "loopback"
    return host
