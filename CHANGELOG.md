# 变更日志

> 本文件由 `tools/changelog.py` 自动维护，**仅保留最近 5 次代码变更**，超出自动删除。
> 每次修改以 unified diff 形式记录（只存改动行，最小占用）。

### 2026-09-02 12:30:40 | 安全审计与加固：CORS收紧/安全响应头/关闭API文档/IP限流/锁定消息模糊化/上传限制/路径穿越防御/审计记录来源IP

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -4,7 +4,13 @@
 运行: python app.py  (或 uvicorn app:app)

 安全模型: 磁盘恒密文 · 密码即钥匙 · 会话内存驻留 · 全部 API 鉴权

+加固说明（2026-09-02 安全审计）:

+- CORS 收紧为同源回环地址（本地单机部署，前端静态资源由本服务托管）

+- 安全响应头（nosniff / frame / referrer / CSP / permissions-policy）

+- 关闭 /docs /redoc /openapi 暴露

+- 登录/注册/找回/改密接入 IP 级限流；账号锁定消息模糊化

+- 上传文件大小/数量限制；serve_file 路径穿越纵深防御；limit 参数收敛

+- 审计日志记录来源 IP

 """

 import os

-import shutil

 import uuid

 import logging

@@ -13,9 +19,9 @@
 from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Depends, Request

 from fastapi.middleware.cors import CORSMiddleware

-from fastapi.responses import Response

+from fastapi.responses import Response, JSONResponse

 from fastapi.staticfiles import StaticFiles

 

 from backend import parser, nlp, classify, store

-from backend.security import auth, deps

+from backend.security import auth, deps, ratelimit

 from backend.security.deps import get_current_user, require_admin

 

@@ -26,8 +32,62 @@
 FRONTEND_DIR = BASE_DIR / "frontend"

 

-app = FastAPI(title="个人知识管理工作台", version="2.0.0")

+# ---------------- 安全常量 ----------------

+MAX_FILE_SIZE = 200 * 1024 * 1024      # 单文件上传上限 200MB（解析/解密均走流式/内存可控）

+MAX_FILES_PER_BATCH = 50               # 单次批量上传文件数上限

+MAX_JSON_BODY = 1 * 1024 * 1024        # JSON 请求体上限 1MB（multipart 上传走流式，不在此限）

+_ALLOWED_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]

+_ALLOWED_HEADERS = ["Authorization", "Content-Type"]

+

+_PORT = int(os.environ.get("PORT", 8787))

+

+app = FastAPI(

+    title="个人知识管理工作台", version="2.0.0",

+    docs_url=None, redoc_url=None, openapi_url=None,  # 纵深防御：关闭 API 结构暴露

+)

+# CORS 收紧：本地单机应用，前端与 API 同源托管，仅放行回环地址

 app.add_middleware(

-    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],

+    CORSMiddleware,

+    allow_origins=[f"http://127.0.0.1:{_PORT}", f"http://localhost:{_PORT}"],

+    allow_methods=_ALLOWED_METHODS,

+    allow_headers=_ALLOWED_HEADERS,

+    allow_credentials=False,

+    max_age=600,

 )

+

+

+@app.middleware("http")

+async def security_headers(request: Request, call_next):

+    """安全响应头：防 MIME 嗅探 / 点击劫持 / 信息泄露 / 权限滥用。"""

+    resp = await call_next(request)

+    resp.headers.setdefault("X-Content-Type-Options", "nosniff")

+    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # 允许同源 iframe 预览 PDF

+    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")

+    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")

+    resp.headers.setdefault(

+        "Content-Security-Policy",

+        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "

+        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "

+        "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "

+        "frame-ancestors 'self'",

+    )

+    return resp

+

+

+@app.middleware("http")

+async def limit_json_body(request: Request, call_next):

+    """JSON 请求体大小限制（multipart 上传为流式 SpooledTemporaryFile，不受此限）。"""

+    if request.method in ("POST", "PUT", "PATCH") and not request.url.path.startswith("/api/upload"):

+        cl = request.headers.get("content-length")

+        if cl and cl.isdigit() and int(cl) > MAX_JSON_BODY:

+            return JSONResponse({"detail": "请求体过大"}, status_code=413)

+    return await call_next(request)

+

+

+def _rl(limiter, action: str):

+    """IP 限流依赖工厂：按 (来源IP, 动作) 滑动窗口计数，超限 429。"""

+    def dep(request: Request):

+        if not limiter.allow(f"{ratelimit.client_ip(request)}:{action}"):

+            raise HTTPException(429, "操作过于频繁，请稍后再试")

+    return dep

 

 

@@ -65,9 +125,20 @@
         }

 

-    # 1) 上传流写随机明文临时文件（带原扩展名，供解析器按类型分发）

+    # 1) 上传流写随机明文临时文件（带原扩展名，供解析器按类型分发），流式计数防超大文件

     tmp = Path(store.DOCS_DIR) / f".{uuid.uuid4().hex}{ext}"

     try:

+        written = 0

         with open(tmp, "wb") as f:

-            shutil.copyfileobj(upload.file, f)

+            while True:

+                chunk = upload.file.read(1 << 20)

+                if not chunk:

+                    break

+                written += len(chunk)

+                if written > MAX_FILE_SIZE:

+                    return {

+                        "ok": False, "filename": filename,

+                        "error": f"文件过大（上限 {MAX_FILE_SIZE // (1024 * 1024)}MB），已拒绝入库",

+                    }

+                f.write(chunk)

 

         # 2) 解析（明文阶段，仅内存/临时）

@@ -114,5 +185,6 @@
                 os.remove(tmp)

             except OSError:

-                pass

+                # 明文临时文件残留即泄露风险：删除失败必须告警（Windows 沙箱等环境会拦截 os.remove）

+                log.warning("临时明文文件删除失败，请手动清理: %s", tmp)

 

 

@@ -120,5 +192,6 @@
 

 @app.post("/api/auth/register")

-def auth_register(payload: dict):

+def auth_register(payload: dict, request: Request,

+                  _=Depends(_rl(ratelimit.register_limiter, "register"))):

     """注册普通用户。questions: [{"question","answer"}, ...] 1-3 个（可选但推荐）。"""

     try:

@@ -127,4 +200,5 @@
             payload.get("password", ""),

             payload.get("questions"),

+            ip=ratelimit.client_ip(request),

         )

     except (ValueError, PermissionError) as e:

@@ -134,8 +208,9 @@
 

 @app.post("/api/auth/login")

-def auth_login(payload: dict):

+def auth_login(payload: dict, request: Request,

+               _=Depends(_rl(ratelimit.login_limiter, "login"))):

     try:

         result = auth.login(payload.get("username", ""), payload.get("password", ""),

-                            bool(payload.get("remember", False)))

+                            bool(payload.get("remember", False)), ip=ratelimit.client_ip(request))

     except ValueError as e:

         raise HTTPException(401, str(e))

@@ -163,7 +238,10 @@
 

 @app.post("/api/auth/change-password")

-def auth_change_password(payload: dict, sess=Depends(get_current_user)):

-    try:

-        auth.change_password(sess.user_id, payload.get("old_password", ""), payload.get("new_password", ""))

+def auth_change_password(payload: dict, request: Request,

+                         sess=Depends(get_current_user),

+                         _=Depends(_rl(ratelimit.change_pw_limiter, "change_pw"))):

+    try:

+        auth.change_password(sess.user_id, payload.get("old_password", ""),

+                             payload.get("new_password", ""), ip=ratelimit.client_ip(request))

     except ValueError as e:

         raise HTTPException(400, str(e))

@@ -172,10 +250,12 @@
 

 @app.get("/api/auth/questions")

-def auth_questions(username: str = Query("")):

+def auth_questions(username: str = Query("", max_length=64),

+                   _=Depends(_rl(ratelimit.questions_limiter, "questions"))):

     return {"ok": True, "questions": auth.get_security_questions(username)}

 

 

 @app.post("/api/auth/reset-password")

-def auth_reset_password(payload: dict):

+def auth_reset_password(payload: dict, request: Request,

+                        _=Depends(_rl(ratelimit.reset_limiter, "reset"))):

     """安全问题找回：answers 顺序与注册时一致，全部答对才放行。"""

     try:

@@ -184,4 +264,5 @@
             payload.get("answers") or [],

             payload.get("new_password", ""),

+            ip=ratelimit.client_ip(request),

         )

     except ValueError as e:

@@ -198,7 +279,8 @@
 

 @app.post("/api/admin/users/{username}/reset-password")

-def admin_reset(username: str, payload: dict, sess=Depends(require_admin)):

-    try:

-        auth.admin_reset_password(sess.username, username, payload.get("new_password", ""))

+def admin_reset(username: str, payload: dict, request: Request, sess=Depends(require_admin)):

+    try:

+        auth.admin_reset_password(sess.username, username, payload.get("new_password", ""),

+                                  ip=ratelimit.client_ip(request))

     except (ValueError, PermissionError) as e:

         raise HTTPException(400, str(e))

@@ -207,5 +289,5 @@
 

 @app.get("/api/admin/audit")

-def admin_audit(limit: int = Query(100), _=Depends(require_admin)):

+def admin_audit(limit: int = Query(100, ge=1, le=500), _=Depends(require_admin)):

     return {"ok": True, "items": auth.audit_log_recent(limit)}

 

@@ -220,4 +302,8 @@
 ):

     """批量上传。paths 与 files 一一对应，保存每个文件的原始文件夹路径。"""

+    if not files:

+        raise HTTPException(400, "未选择文件")

+    if len(files) > MAX_FILES_PER_BATCH:

+        raise HTTPException(400, f"单次最多上传 {MAX_FILES_PER_BATCH} 个文件")

     dek, user_id = sess.dek, sess.user_id

     results = []

@@ -234,5 +320,5 @@
     q: str = Query(""),

     tag: str = Query(""),

-    limit: int = Query(200),

+    limit: int = Query(200, ge=1, le=500),

     sess=Depends(get_current_user),

 ):

@@ -268,5 +354,11 @@
 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):

-    """下载文档：先校验归属（纵深防御），再密文解密到内存字节直接响应。"""

+    """下载文档：先校验归属（纵深防御），再密文解密到内存字节直接响应。

+

+    路径穿越纵深防御：stored_name 必须是纯文件名（服务端生成），含路径分隔符一律拒绝，

+    防止历史异常数据/数据库被篡改后拼接 DOCS_DIR 越权读取。

+    """

+    if not stored_name or os.path.basename(stored_name) != stored_name:

+        raise HTTPException(404, "文件不存在")

     if not store.doc_owned_by(stored_name, sess.user_id):

         raise HTTPException(404, "文件不存在")
```

**文件:** `backend\security\auth.py`
```diff
--- backend\security\auth.py
+++ backend\security\auth.py
@@ -94,8 +94,11 @@
 # ---------------- 限流 ----------------

 def _check_lock(row) -> None:

+    """账号锁定检查。锁定期间返回与密码错误一致的提示（防账号存在性/锁定状态枚举），

+    且执行一次伪校验使耗时对齐（防时序侧信道）。"""

     if row["lock_until"]:

         until = datetime.strptime(row["lock_until"], "%Y-%m-%d %H:%M:%S")

         if until > datetime.now():

-            raise PermissionError(f"失败次数过多，已锁定至 {row['lock_until']}，请稍后再试")

+            verify_password(_DUMMY_HASH, "dummy-password-zz")

+            raise PermissionError("用户名或密码错误")

 

 

@@ -121,5 +124,9 @@
 

 # ---------------- 审计 ----------------

-def audit(actor: str, action: str, target: str = "", detail: str = "") -> None:

+def audit(actor: str, action: str, target: str = "", detail: str = "", ip: str = "") -> None:

+    """写入审计日志。ip 为来源地址（本地回环归一化为 loopback），并入 detail 前缀。

+    审计失败不阻断主流程。"""

+    if ip:

+        detail = f"[{ip}] {detail}".strip()

     try:

         conn = _conn()

@@ -342,5 +349,15 @@
 

 # ---------------- 注册 ----------------

-def register_user(username: str, password: str, questions: list[dict] | None = None) -> dict:

+def _validate_password(pw: str, ctx: str = "密码") -> str:

+    """统一密码强度校验（含长度上限，防超大输入耗尽 Argon2 计算资源）。"""

+    if not isinstance(pw, str) or len(pw) < 8 or len(pw) > 128:

+        raise ValueError(f"{ctx}需 8-128 位")

+    if not any(c.isalpha() for c in pw) or not any(c.isdigit() for c in pw):

+        raise ValueError(f"{ctx}至少 8 位且同时包含字母和数字")

+    return pw

+

+

+def register_user(username: str, password: str, questions: list[dict] | None = None,

+                  ip: str = "") -> dict:

     """注册普通用户。questions: [{"question": str, "answer": str}, ...]（1-3 个）。

 

@@ -352,6 +369,5 @@
     if not all(c.isalnum() or c in "_-" for c in username):

         raise ValueError("用户名仅允许字母/数字/下划线/中划线")

-    if len(password) < 8 or not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):

-        raise ValueError("密码至少 8 位且同时包含字母和数字")

+    _validate_password(password)

 

     master = get_master_key()

@@ -404,13 +420,17 @@
 

     _archive_wrap(uid, salt_kek, wrapped, retired=1)

-    audit("system", "register", username)

+    audit("system", "register", username, ip=ip)

     return {"id": uid, "username": username, "questions": len(questions)}

 

 

 # ---------------- 登录 / 登出 ----------------

-def login(username: str, password: str, remember: bool = False) -> dict:

+def login(username: str, password: str, remember: bool = False, ip: str = "") -> dict:

     """登录。成功 → 创建会话；失败 → 统一提示（不泄露账号存在性）。

     remember=True → 30 天长期会话（"记住我"，同一设备免密访问）。"""

     username = username.strip().lower()

+    if not isinstance(password, str) or not password or len(password) > 128:

+        # 超大/缺失密码直接按失败处理（耗时对齐防探测）

+        verify_password(_DUMMY_HASH, "dummy-password-zz")

+        raise ValueError("用户名或密码错误")

     conn = _conn()

     row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()

@@ -456,5 +476,5 @@
         timeout=timeout,

     )

-    audit("auth", "login", row["username"])

+    audit("auth", "login", row["username"], ip=ip)

     return {

         "token": token, "username": row["username"], "role": row["role"],

@@ -493,8 +513,7 @@
 

 # ---------------- 改密（用户自助） ----------------

-def change_password(user_id: int, old_password: str, new_password: str) -> None:

+def change_password(user_id: int, old_password: str, new_password: str, ip: str = "") -> None:

     """改密：只重新包裹 DEK，不重加密数据。事务 + 版本化 + 自检。"""

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

 

     conn = _conn()

@@ -567,11 +586,14 @@
 

     sessions.delete_by_user(user_id)  # 改密后旧会话全部失效

-    audit("auth", "change_password", f"user#{user_id}")

+    audit("auth", "change_password", f"user#{user_id}", ip=ip)

 

 

 # ---------------- 安全问题找回 ----------------

 def get_security_questions(username: str) -> list[str]:

-    """找回第一步：返回该用户的问题列表（不暴露任何其他信息）。"""

-    username = username.strip().lower()

+    """找回第一步：返回该用户的问题列表（不暴露任何其他信息）。

+

+    枚举防护依赖两层：IP 限流（app 路由层）+ 输入长度限制；问题本身不敏感。

+    """

+    username = username.strip().lower()[:64]

     conn = _conn()

     row = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()

@@ -586,11 +608,11 @@
 

 

-def reset_password_via_questions(username: str, answers: list[str], new_password: str) -> None:

+def reset_password_via_questions(username: str, answers: list[str], new_password: str,

+                                 ip: str = "") -> None:

     """找回第二步：全部答对 → 解 DEK → 设新密码（单事务原子）。

 

     限流：任一题连续失败 5 次锁定 15 分钟（复用 users.login_fail/lock_until）。

     """

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

     username = username.strip().lower()

     conn = _conn()

@@ -655,5 +677,5 @@
         raise

     sessions.delete_by_user(user["id"])

-    audit("auth", "reset_via_questions", username)

+    audit("auth", "reset_via_questions", username, ip=ip)

 

 

@@ -671,11 +693,11 @@
 

 

-def admin_reset_password(admin_username: str, target_username: str, new_password: str) -> None:

+def admin_reset_password(admin_username: str, target_username: str, new_password: str,

+                         ip: str = "") -> None:

     """管理员重置用户密码（Master Key 通道，不依赖旧密码）。

 

     事务原子 + 版本化 + 自检；重置后强制用户下次改密并踢掉旧会话。

     """

-    if len(new_password) < 8 or not any(c.isalpha() for c in new_password) or not any(c.isdigit() for c in new_password):

-        raise ValueError("新密码至少 8 位且同时包含字母和数字")

+    _validate_password(new_password)

     target_username = target_username.strip().lower()

     master = get_master_key()

@@ -729,5 +751,5 @@
         raise

     sessions.delete_by_user(target["id"])

-    audit("admin", "reset_password", target_username, f"by {admin_username}")

+    audit("admin", "reset_password", target_username, f"by {admin_username}", ip=ip)

 

 
```

**文件:** `backend\security\ratelimit.py`（新增文件）
```diff
--- /dev/null
+++ backend\security\ratelimit.py
@@ -0,0 +1,58 @@
+# -*- coding: utf-8 -*-
+"""IP 级限流：内存滑动窗口（按 来源IP + 动作 独立计数）。
+
+防御目标（账号级锁定挡不住的场景）：
+- 登录/找回/改密等认证接口被分布式暴力破解（多用户名轮换绕开单账号锁定）
+- 注册接口被批量滥用（垃圾账号泛滥）
+- 认证接口被高频探测（用户名枚举 / 存在性确认）
+
+实现：进程内存 dict[key -> deque[时间戳]]，滑动窗口内超限即拒绝（429）。
+惰性清理过期条目与空队列，防止 key 无限膨胀。
+"""
+import time
+from collections import deque, defaultdict
+from threading import Lock
+
+
+class RateLimiter:
+    def __init__(self, limit: int, window: float, max_keys: int = 10000):
+        self.limit = limit
+        self.window = window
+        self.max_keys = max_keys
+        self._hits: dict[str, deque] = defaultdict(deque)
+        self._lock = Lock()
+
+    def allow(self, key: str) -> bool:
+        """窗口内允许则记录本次并返回 True；超限返回 False。"""
+        now = time.time()
+        with self._lock:
+            q = self._hits[key]
+            while q and now - q[0] > self.window:
+                q.popleft()
+            if len(q) >= self.limit:
+                return False
+            q.append(now)
+            if len(self._hits) > self.max_keys:
+                self._sweep(now)
+            return True
+
+    def _sweep(self, now: float):
+        """清理空队列，防止 key 无限膨胀。"""
+        for k in [k for k, q in self._hits.items() if not q]:
+            del self._hits[k]
+
+
+# 全局限流器实例（按动作维度，独立窗口）
+login_limiter     = RateLimiter(limit=10, window=60)    # 登录：10 次/分钟/IP
+register_limiter  = RateLimiter(limit=20, window=3600)  # 注册：20 次/小时/IP
+questions_limiter = RateLimiter(limit=20, window=60)    # 安全问题获取：20 次/分钟/IP
+reset_limiter     = RateLimiter(limit=10, window=60)    # 安全问题重置：10 次/分钟/IP
+change_pw_limiter = RateLimiter(limit=10, window=60)    # 改密：10 次/分钟/IP
+
+
+def client_ip(request) -> str:
+    """取客户端 IP（回环地址归一化；不透传不可信的 X-Forwarded-For，本地直连无需代理头）。"""
+    host = getattr(request.client, "host", "") or ""
+    if host in ("127.0.0.1", "::1"):
+        return "loopback"
+    return host
```


---

### 2026-09-02 12:05:21 | 实现数据与程序本体隔离（根治更新程序后文件丢失）：新增 backend/paths.py 统一数据路径（环境变量 KB_DATA_DIR 优先，回落项目内 data/ 兼容旧部署）；store.py/auth.py 改走统一路径；用户数据已迁移至外置目录 D:/agent/知识工作台_数据（documents/knowledge.db/kbtest）；start.bat/start.sh 注入 KB_DATA_DIR；tools/backup.py 支持多根联合备份（外置数据以 kbdata/ 前缀并入快照，restore 自动拆分回数据目录）。app.py 本轮无代码变更

**文件:** `backend\paths.py`（新增文件）
```diff
--- /dev/null
+++ backend\paths.py
@@ -0,0 +1,35 @@
+# -*- coding: utf-8 -*-
+"""
+统一数据路径解析：实现「数据与程序本体隔离」。
+================================================
+数据目录优先级：
+  1. 环境变量 KB_DATA_DIR（start.bat / start.sh 注入，指向外置数据目录）
+  2. 程序根目录下 data/（兼容旧部署 / 直接 python app.py 运行）
+
+数据目录独立于程序目录后，更新/替换程序代码不会影响任何用户数据。
+"""
+import os
+
+PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+
+
+def get_data_dir() -> str:
+    """返回数据根目录（自动创建由各使用方负责，或在此确保存在）。"""
+    env = os.environ.get("KB_DATA_DIR", "").strip()
+    if env:
+        return env
+    return os.path.join(PROJECT_ROOT, "data")
+
+
+def get_docs_dir() -> str:
+    return os.path.join(get_data_dir(), "documents")
+
+
+def get_db_path() -> str:
+    return os.path.join(get_data_dir(), "knowledge.db")
+
+
+def ensure_data_dir() -> str:
+    d = get_data_dir()
+    os.makedirs(d, exist_ok=True)
+    return d
```

**文件:** `backend\store.py`
```diff
--- backend\store.py
+++ backend\store.py
@@ -18,9 +18,10 @@
 

 from .security import vault, crypto

+from .paths import get_data_dir, get_docs_dir, get_db_path

 

 BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

-DATA_DIR = os.path.join(BASE_DIR, "data")

-DOCS_DIR = os.path.join(DATA_DIR, "documents")

-DB_PATH = os.path.join(DATA_DIR, "knowledge.db")

+DATA_DIR = get_data_dir()          # 数据根目录（外置 KB_DATA_DIR 或项目内 data/）

+DOCS_DIR = get_docs_dir()

+DB_PATH = get_db_path()

 

 _SCHEMA = """
```

**文件:** `backend\security\auth.py`
```diff
--- backend\security\auth.py
+++ backend\security\auth.py
@@ -29,4 +29,5 @@
 from . import crypto

 from .session import sessions, REMEMBER_TIMEOUT

+from ..paths import get_db_path, get_data_dir, get_docs_dir

 

 # ---------------- Argon2id ----------------

@@ -42,8 +43,5 @@
 _master_lock = threading.Lock()

 

-DB_PATH = os.path.join(

-    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),

-    "data", "knowledge.db",

-)

+DB_PATH = get_db_path()

 BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

 

@@ -271,5 +269,5 @@
 def _cleanup_stray_tmp() -> None:

     """启动清理：删除崩溃残留的明文临时文件（. 开头的隐藏临时文件），杜绝明文残留。"""

-    docs_dir = os.path.join(BASE_DIR, "data", "documents")

+    docs_dir = get_docs_dir()

     if os.path.isdir(docs_dir):

         for f in os.listdir(docs_dir):

@@ -307,5 +305,5 @@
         "SELECT id, enc_ver, stored_name, filename, title, category, tags, keywords, summary, content, path FROM documents"

     ).fetchall()

-    docs_dir = os.path.join(BASE_DIR, "data", "documents")

+    docs_dir = get_docs_dir()

     for r in rows:

         # 1) 文件副本：按文件头检测，非 KBENC 密文则加密覆盖（幂等，兼容字段已加密但文件漏加密的历史状态）
```

**文件:** `start.bat`
```diff
--- start.bat
+++ start.bat
@@ -6,4 +6,8 @@
 set PY=C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\python.exe

 if not exist "%PY%" set PY=python

+

+rem ===== 数据目录外置：程序与用户数据隔离（更新程序不影响数据）=====

+set KB_DATA_DIR=D:\agent\知识工作台_数据

+if not exist "%KB_DATA_DIR%" mkdir "%KB_DATA_DIR%"

 

 rem ===== 检测服务是否已在运行（端口 8787 被占用 = 已在运行）=====
```

**文件:** `start.sh`
```diff
--- start.sh
+++ start.sh
@@ -6,4 +6,8 @@
 [ -x "$PY" ] || PY=python

 

+# 数据目录外置：程序与用户数据隔离（更新程序不影响数据）

+export KB_DATA_DIR="D:/agent/知识工作台_数据"

+mkdir -p "$KB_DATA_DIR"

+

 echo "========================================"

 echo "  个人知识管理工作台  正在启动..."
```

**文件:** `tools\backup.py`
```diff
--- tools\backup.py
+++ tools\backup.py
@@ -11,5 +11,5 @@
 

 用法:

-  python tools/backup.py backup  [--comment "说明"] [--dir 备份根]   # 一键备份

+  python tools/backup.py backup  [--comment "说明"] [--dir 备份根] [--data-dir 数据目录]  # 一键备份

   python tools/backup.py list   [--dir 备份根]                      # 查看快照

   python tools/backup.py restore <快照名> [--to 目标目录] [--dir 备份根]  # 恢复

@@ -17,4 +17,6 @@
 

 备份根默认 <项目根>/backup/，--dir 可指向其他位置（U 盘 / 网盘同步目录）。

+外置数据目录（KB_DATA_DIR / --data-dir）以 kbdata/ 前缀并入同一快照，

+restore 时自动拆回数据目录；代码与用户数据一起备份、一起回滚。

 knowledge.db 等 SQLite 文件用 Online Backup API 生成一致性副本，运行中备份也安全。

 """

@@ -135,4 +137,16 @@
 

 

+def _resolve_data_dir(args) -> str | None:

+    """外置数据目录：--data-dir > 环境变量 KB_DATA_DIR；未配置且不在项目内则 None。"""

+    d = getattr(args, "data_dir", None) or os.environ.get("KB_DATA_DIR", "").strip()

+    if not d:

+        return None

+    d = os.path.abspath(d)

+    # 若指向项目内 data/（兼容旧部署），BASE 扫描已覆盖，无需单独收集

+    if d == os.path.join(BASE, "data"):

+        return None

+    return d

+

+

 # ---------- 子命令 ----------

 def cmd_backup(args):

@@ -153,4 +167,17 @@
             files[rel.replace("\\", "/")] = info

             total += info["size"]

+

+    # 外置数据目录：以 kbdata/ 前缀并入同一快照（与代码一起、对象库去重仍然生效）

+    data_dir = _resolve_data_dir(args)

+    data_count = 0

+    if data_dir and os.path.isdir(data_dir):

+        for dirpath, dirnames, filenames in os.walk(data_dir):

+            for fn in filenames:

+                full = os.path.join(dirpath, fn)

+                rel = os.path.relpath(full, data_dir).replace("\\", "/")

+                info = _ingest(root, full, "kbdata/" + rel)

+                files["kbdata/" + rel] = info

+                total += info["size"]

+                data_count += 1

 

     name = datetime.now().strftime("%Y%m%d_%H%M%S")

@@ -181,5 +208,6 @@
 

     print(f"  ✓ 备份完成: {os.path.basename(snap)[:-5]}")

-    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}")

+    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}"

+          + (f"（其中数据目录 {data_count} 个）" if data_count else ""))

     if prev:

         print(f"    相对上次: 新增/变更 {added} 个文件, 新增存储 {_fmt(added_bytes)}")

@@ -226,10 +254,17 @@
         sys.exit(1)

     meta = json.load(open(p, encoding="utf-8"))

+    data_dir = _resolve_data_dir(args)

     dst_root = os.path.abspath(args.to) if args.to else BASE

     print(f"  恢复快照 {os.path.basename(p)[:-5]} -> {dst_root}")

     n = 0

     for rel, info in meta["files"].items():

-        full = os.path.realpath(os.path.join(dst_root, rel))

-        if not full.startswith(os.path.realpath(dst_root) + os.sep):

+        # kbdata/ 前缀 → 外置数据目录（未配置则回落项目内 data/）

+        if rel.startswith("kbdata/"):

+            target = data_dir or os.path.join(BASE, "data")

+            rel = rel[len("kbdata/"):]

+        else:

+            target = dst_root

+        full = os.path.realpath(os.path.join(target, rel))

+        if not full.startswith(os.path.realpath(target) + os.sep):

             raise ValueError(f"非法路径: {rel}")

         os.makedirs(os.path.dirname(full), exist_ok=True)

@@ -288,4 +323,6 @@
         p.add_argument("--dir", default=DEFAULT_BACKUP_ROOT,

                        help=f"备份根目录(默认 {DEFAULT_BACKUP_ROOT})")

+        p.add_argument("--data-dir", default=None,

+                       help="外置数据目录(默认取环境变量 KB_DATA_DIR；未配置则只备份代码)")

 

     p1 = sub.add_parser("backup")
```

---

### 2026-09-02 11:25:36 | 撤除程序内嵌的「历史版本」备份逻辑，改由独立增量备份工具接管：删除 backend/versions.py（CHANGELOG 解析+反向 patch 恢复）与 /api/versions、/api/versions/{index}/restore 两个接口；前端移除侧边栏入口、历史版本面板、恢复确认弹窗及全部 JS/CSS（含 loadAll、Esc/Tab 键盘处理、enterApp 中的残留分支）。备份职责完全移交 tools/backup.py（内容寻址增量备份，已存 20260902_112513 基线快照）

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -266,23 +266,4 @@
 

 

-# ---------------- 历史版本（代码级，鉴权即可） ----------------

-from backend import versions

-

-

-@app.get("/api/versions")

-def version_list(_=Depends(require_admin)):

-    """历史版本（代码回滚）仅管理员可用。"""

-    return {"ok": True, "items": versions.load_versions()}

-

-

-@app.post("/api/versions/{index}/restore")

-def version_restore(index: int, _=Depends(require_admin)):

-    try:

-        result = versions.restore_version(index)

-    except ValueError as e:

-        raise HTTPException(400, str(e))

-    return {"ok": True, **result}

-

-

 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):
```

**文件:** `frontend\app.js`
```diff
--- frontend\app.js
+++ frontend\app.js
@@ -137,8 +137,5 @@
 /* ---------- 加载数据 ---------- */

 async function loadAll() {

-  // 历史版本仅管理员可加载（普通用户后端 403，直接跳过避免报错）

-  const jobs = [loadStats(), loadCategories(), loadDocuments()];

-  if (auth.user?.role === "admin") jobs.push(loadVersions());

-  await Promise.all(jobs);

+  await Promise.all([loadStats(), loadCategories(), loadDocuments()]);

 }

 

@@ -656,176 +653,10 @@
 $("sidebarMask").addEventListener("click", closeSidebar);

 

-/* ---------- 历史版本 ---------- */

-const versionState = {

-  items: [],

-  open: false,

-  expanded: new Set(),

-  restoringIndex: null,

-};

-

-async function loadVersions() {

-  try {

-    const d = await api("/api/versions");

-    versionState.items = d.items || [];

-    const badge = $("versionBadge");

-    const n = versionState.items.length;

-    if (n > 0) {

-      badge.textContent = n;

-      badge.hidden = false;

-    } else {

-      badge.hidden = true;

-    }

-  } catch (e) { /* ignore */ }

-}

-

-function openVersionPanel() {

-  versionState.open = true;

-  state.lastFocused = document.activeElement;

-  $("versionMask").hidden = false;

-  renderVersions();

-  requestAnimationFrame(() => {

-    $("versionMask").classList.add("show");

-    $("versionPanel").classList.add("open");

-    $("versionPanel").setAttribute("aria-hidden", "false");

-    $("versionClose").focus();

-  });

-}

-

-function closeVersionPanel() {

-  $("versionPanel").classList.remove("open");

-  $("versionMask").classList.remove("show");

-  $("versionPanel").setAttribute("aria-hidden", "true");

-  versionState.open = false;

-  setTimeout(() => {

-    $("versionMask").hidden = true;

-    if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();

-    state.lastFocused = null;

-  }, 450);

-}

-

-function renderVersions() {

-  const body = $("versionBody");

-  if (!versionState.items.length) {

-    body.innerHTML = '<div class="version-empty">暂无历史版本<br>每次升级确认方案后会自动归档到这里</div>';

-    return;

-  }

-  body.innerHTML = versionState.items

-    .map(

-      (v, i) => `

-      <div class="version-item ${i === 0 ? "latest" : ""} ${versionState.expanded.has(v.index) ? "expanded" : ""}" data-vindex="${v.index}" style="animation-delay:${Math.min(i * 40, 300)}ms">

-        <div class="version-item-head" role="button" tabindex="0" aria-expanded="${versionState.expanded.has(v.index)}">

-          <span class="version-time">${esc(v.time)}${i === 0 ? " · 最新" : ""}</span>

-          <span class="version-desc">${esc(v.desc || "（无说明）")}</span>

-          <span class="version-files">${v.files.length} 个文件</span>

-          <span class="version-arrow">▼</span>

-        </div>

-        <div class="version-detail">

-          ${v.files.map((f) => {

-            const lines = f.diff ? f.diff.split("\n").length : 0;

-            return `

-            <div class="version-file-block">

-              <button class="version-file-head" role="button" aria-expanded="false">

-                <span class="version-file-name">${esc(f.path)}</span>

-                <span class="version-file-meta">${lines} 行</span>

-                <span class="version-file-arrow">▼</span>

-              </button>

-              <div class="version-diff">${renderDiff(f.diff)}</div>

-            </div>`;

-          }).join("")}

-          <div class="version-actions">

-            <button class="restore-btn" data-rindex="${v.index}">${i === 0 ? "撤销此升级" : "恢复到该版本"}</button>

-            <span class="restore-hint">恢复会覆盖当前代码，操作前自动留档</span>

-          </div>

-        </div>

-      </div>`

-    )

-    .join("");

-

-  body.querySelectorAll(".version-item-head").forEach((el) => {

-    const toggle = () => {

-      const vindex = Number(el.closest(".version-item").dataset.vindex);

-      if (versionState.expanded.has(vindex)) versionState.expanded.delete(vindex);

-      else versionState.expanded.add(vindex);

-      renderVersions();

-    };

-    el.addEventListener("click", toggle);

-    el.addEventListener("keydown", (e) => {

-      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }

-    });

-  });

-  body.querySelectorAll(".version-file-head").forEach((el) => {

-    const toggle = () => {

-      const block = el.closest(".version-file-block");

-      const open = block.classList.toggle("open");

-      el.setAttribute("aria-expanded", String(open));

-    };

-    el.addEventListener("click", toggle);

-  });

-  body.querySelectorAll(".restore-btn").forEach((el) =>

-    el.addEventListener("click", () => {

-      versionState.restoringIndex = Number(el.dataset.rindex);

-      const v = versionState.items.find((x) => x.index === versionState.restoringIndex);

-      const isLatest = versionState.items[0] && versionState.items[0].index === v.index;

-      $("restoreTitle").textContent = isLatest ? "撤销此升级？" : "恢复到该版本？";

-      $("restoreSub").textContent = `将${isLatest ? "撤销" : "恢复到"} ${esc(v.time)} 的「${esc(v.desc || "无说明")}」，涉及 ${v.files.length} 个文件，当前代码会被覆盖（恢复前会自动留档）。`;

-      $("restoreMask").hidden = false;

-      requestAnimationFrame(() => $("restoreCancel").focus());

-    })

-  );

-}

-

-/* diff 语法着色 */

-function renderDiff(diff) {

-  return esc(diff)

-    .split("\n")

-    .map((line) => {

-      if (line.startsWith("@@")) return `<span class="hl">${line}</span>`;

-      if (line.startsWith("-") && !line.startsWith("---")) return `<span class="dl">${line}</span>`;

-      if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="al">${line}</span>`;

-      if (line.startsWith(" ") || line.startsWith("-") || line.startsWith("+") || line.startsWith("\\")) return `<span class="cl">${line}</span>`;

-      return `<span class="cl">${line}</span>`;

-    })

-    .join("\n");

-}

-

-async function confirmRestore() {

-  if (versionState.restoringIndex === null) return;

-  const idx = versionState.restoringIndex;

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-  try {

-    const res = await api(`/api/versions/${idx}/restore`, { method: "POST" });

-    if (res.has_errors) {

-      toast("恢复完成，但有 " + res.results.filter((r) => !r.ok).length + " 个文件失败，请查看");

-    } else {

-      toast("已恢复到 " + res.target.time + " 的版本");

-    }

-    closeVersionPanel();

-    await loadAll();

-    setTimeout(() => toast("代码已切换，若界面异常请重启服务（双击 start.bat）"), 800);

-  } catch (e) {

-    toast("恢复失败: " + e.message);

-  }

-}

-

-$("versionEntry").addEventListener("click", openVersionPanel);

-$("versionClose").addEventListener("click", closeVersionPanel);

-$("versionMask").addEventListener("click", closeVersionPanel);

-$("restoreCancel").addEventListener("click", () => {

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-});

-$("restoreConfirm").addEventListener("click", confirmRestore);

-

 /* ---------- 全局键盘 ---------- */

 document.addEventListener("keydown", (e) => {

-  // Esc 优先关弹窗 → 版本面板 → 抽屉 → 侧边栏

+  // Esc 优先关弹窗 → 抽屉 → 侧边栏

   if (e.key === "Escape") {

     if (state.modalOpen) {

       $("modalCancel").click();

-    } else if (!versionState.restoringIndex && !$("restoreMask").hidden) {

-      $("restoreCancel").click();

-    } else if (versionState.open) {

-      closeVersionPanel();

     } else if (state.drawerOpen) {

       closeDrawer();

@@ -842,15 +673,7 @@
     return;

   }

-  // 抽屉/版本面板焦点陷阱

+  // 抽屉焦点陷阱

   if (e.key === "Tab") {

-    if (versionState.open) {

-      const focusables = $("versionPanel").querySelectorAll('button, [tabindex]:not([tabindex="-1"])');

-      if (focusables.length) {

-        const first = focusables[0];

-        const last = focusables[focusables.length - 1];

-        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }

-        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }

-      }

-    } else if (state.drawerOpen) {

+    if (state.drawerOpen) {

       const focusables = $("drawer").querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])');

       if (focusables.length) {

@@ -862,5 +685,5 @@
     }

   }

-  // 删除/恢复弹窗焦点陷阱

+  // 删除弹窗焦点陷阱

   if (e.key === "Tab" && state.modalOpen) {

     const btns = $("modalMask").querySelectorAll("button");

@@ -932,6 +755,4 @@
   renderUserMenu();

   $("menuAdmin").hidden = user.role !== "admin";

-  // 历史版本（代码回滚）仅管理员可用：非管理员隐藏入口

-  $("versionEntry").hidden = user.role !== "admin";

   loadAll().catch((e) => toast("加载失败: " + e.message));

 }
```

**文件:** `frontend\index.html`
```diff
--- frontend\index.html
+++ frontend\index.html
@@ -265,10 +265,4 @@
     </div>

 

-    <button class="version-entry" id="versionEntry" title="查看历史版本">

-      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>

-      <span>历史版本</span>

-      <span class="version-badge" id="versionBadge" hidden>0</span>

-    </button>

-

     <div class="sidebar-foot">

       <div class="drop-hint">把报告 / 笔记拖进窗口即可入库</div>

@@ -381,29 +375,4 @@
 </div>

 

-<!-- 历史版本面板 -->

-<div class="version-mask" id="versionMask" hidden></div>

-<aside class="version-panel" id="versionPanel" role="dialog" aria-modal="true" aria-hidden="true" aria-label="历史版本">

-  <div class="drawer-head">

-    <button class="icon-btn" id="versionClose" title="关闭（Esc）" aria-label="关闭历史版本">✕</button>

-    <div class="version-head-title">

-      <span>🕘 历史版本</span>

-      <span class="version-head-sub">最近 5 次代码升级，点击可查看改动 / 恢复</span>

-    </div>

-  </div>

-  <div class="version-body" id="versionBody"></div>

-</aside>

-

-<!-- 恢复确认弹窗 -->

-<div class="modal-mask" id="restoreMask" hidden>

-  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="restoreTitle" aria-describedby="restoreSub">

-    <div class="modal-title" id="restoreTitle">恢复到该版本？</div>

-    <div class="modal-sub" id="restoreSub"></div>

-    <div class="modal-actions">

-      <button class="modal-btn cancel" id="restoreCancel">取消</button>

-      <button class="modal-btn danger" id="restoreConfirm">恢复</button>

-    </div>

-  </div>

-</div>

-

 <!-- 修改密码弹窗 -->

 <div class="modal-mask" id="pwModalMask" hidden>
```

**文件:** `frontend\style.css`
```diff
--- frontend\style.css
+++ frontend\style.css
@@ -162,130 +162,4 @@
 .drop-hint { font-size: 11px; color: var(--label-2); text-align: center; line-height: 1.6; }

 

-/* 历史版本入口 */

-.version-entry {

-  display: flex; align-items: center; gap: 9px;

-  width: 100%; margin-bottom: 14px;

-  padding: 10px 12px; border-radius: 10px;

-  background: var(--card); color: var(--label);

-  font-size: 13.5px; font-weight: 600;

-  border: 1px solid var(--separator);

-  transition: all 0.2s var(--spring);

-}

-.version-entry:hover { background: var(--card-hover); border-color: var(--accent); transform: translateY(-1px); }

-.version-entry:active { transform: scale(0.98); }

-.version-badge {

-  margin-left: auto;

-  font-size: 11px; font-weight: 700; color: var(--accent-2);

-  background: rgba(100, 210, 255, 0.12);

-  border-radius: 999px; padding: 1px 8px;

-}

-

-/* 历史版本面板 */

-.version-mask {

-  position: fixed; inset: 0; z-index: 90;

-  background: rgba(0, 0, 0, 0.5);

-  backdrop-filter: blur(4px);

-  -webkit-backdrop-filter: blur(4px);

-  opacity: 0; transition: opacity 0.3s var(--ease);

-}

-.version-mask.show { opacity: 1; }

-

-.version-panel {

-  position: fixed; top: 0; right: 0; bottom: 0; z-index: 91;

-  width: min(620px, 94vw);

-  background: var(--surface);

-  border-left: 1px solid var(--separator);

-  box-shadow: -20px 0 60px rgba(0, 0, 0, 0.5);

-  transform: translateX(102%);

-  transition: transform 0.45s var(--spring);

-  display: flex; flex-direction: column;

-}

-.version-panel.open { transform: translateX(0); }

-.version-head-title { display: flex; flex-direction: column; gap: 2px; margin-left: 12px; }

-.version-head-title span:first-child { font-size: 15px; font-weight: 700; }

-.version-head-sub { font-size: 11px; color: var(--label-2); }

-

-.version-body { flex: 1; overflow-y: auto; padding: 18px 20px 50px; }

-.version-body::-webkit-scrollbar { width: 8px; }

-.version-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

-

-.version-empty { text-align: center; padding: 60px 20px; color: var(--label-2); font-size: 13px; line-height: 1.8; }

-

-.version-item {

-  background: var(--card);

-  border: 1px solid var(--separator);

-  border-radius: 14px;

-  margin-bottom: 12px;

-  overflow: hidden;

-  animation: fadeUp 0.35s var(--spring) backwards;

-}

-.version-item.latest { border-color: rgba(10, 132, 255, 0.4); }

-.version-item-head {

-  display: flex; align-items: center; gap: 10px;

-  padding: 13px 16px; cursor: pointer;

-  transition: background 0.2s;

-}

-.version-item-head:hover { background: var(--hover-soft); }

-.version-time { font-size: 12.5px; font-weight: 700; color: var(--accent-2); flex-shrink: 0; }

-.version-desc {

-  flex: 1; min-width: 0;

-  font-size: 13px; line-height: 1.5;

-  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;

-}

-.version-files { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-arrow { color: var(--label-2); font-size: 12px; transition: transform 0.3s var(--spring); flex-shrink: 0; }

-.version-item.expanded .version-arrow { transform: rotate(180deg); }

-

-.version-detail { display: none; padding: 0 16px 14px; }

-.version-item.expanded .version-detail { display: block; animation: fadeIn 0.25s var(--ease); }

-.version-file-block { margin-bottom: 10px; border: 1px solid var(--separator); border-radius: 10px; overflow: hidden; }

-.version-file-head {

-  display: flex; align-items: center; gap: 8px;

-  width: 100%; padding: 9px 12px;

-  background: var(--hover-soft);

-  cursor: pointer;

-  transition: background 0.2s;

-}

-.version-file-head:hover { background: var(--chip-bg); }

-.version-file-name {

-  flex: 1; min-width: 0;

-  font-size: 12px; font-weight: 600; color: var(--label);

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;

-}

-.version-file-meta { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-file-arrow { color: var(--label-2); font-size: 10px; flex-shrink: 0; transition: transform 0.3s var(--spring); }

-.version-file-block.open .version-file-arrow { transform: rotate(180deg); }

-.version-diff {

-  background: var(--code-bg);

-  padding: 12px 14px;

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  font-size: 12px; line-height: 1.7;

-  max-height: 320px; overflow: auto;

-  white-space: pre-wrap; word-break: break-all;

-  color: rgba(242, 242, 247, 0.75);

-  border-top: 1px solid var(--separator);

-  display: none;

-}

-.version-file-block.open .version-diff { display: block; animation: fadeIn 0.2s var(--ease); }

-.version-diff::-webkit-scrollbar { width: 6px; height: 6px; }

-.version-diff::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }

-.version-diff .dl { color: var(--danger); }

-.version-diff .al { color: var(--green); }

-.version-diff .cl { color: var(--label-2); }

-.version-diff .hl { color: var(--accent-2); }

-

-.version-actions { display: flex; gap: 10px; align-items: center; margin-top: 4px; }

-.restore-btn {

-  font-size: 12.5px; font-weight: 700; color: #fff;

-  background: var(--accent); border-radius: 10px;

-  padding: 8px 18px;

-  transition: all 0.2s var(--spring);

-}

-.restore-btn:hover { filter: brightness(1.12); transform: translateY(-1px); }

-.restore-btn:active { transform: scale(0.96); }

-.restore-btn:disabled { background: var(--separator); color: var(--label-2); cursor: not-allowed; transform: none; }

-.restore-hint { font-size: 11px; color: var(--label-2); }

-

 /* ============ 主区域 ============ */

 .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
```

---

### 2026-09-02 11:25:35 | 撤除程序内嵌的「历史版本」备份逻辑，改由独立增量备份工具接管：删除 backend/versions.py（CHANGELOG 解析+反向 patch 恢复）与 /api/versions、/api/versions/{index}/restore 两个接口；前端移除侧边栏入口、历史版本面板、恢复确认弹窗及全部 JS/CSS（含 loadAll、Esc/Tab 键盘处理、enterApp 中的残留分支）。备份职责完全移交 tools/backup.py（内容寻址增量备份，已存 20260902_112513 基线快照）

**文件:** `app.py`
```diff
--- app.py
+++ app.py
@@ -266,23 +266,4 @@
 

 

-# ---------------- 历史版本（代码级，鉴权即可） ----------------

-from backend import versions

-

-

-@app.get("/api/versions")

-def version_list(_=Depends(require_admin)):

-    """历史版本（代码回滚）仅管理员可用。"""

-    return {"ok": True, "items": versions.load_versions()}

-

-

-@app.post("/api/versions/{index}/restore")

-def version_restore(index: int, _=Depends(require_admin)):

-    try:

-        result = versions.restore_version(index)

-    except ValueError as e:

-        raise HTTPException(400, str(e))

-    return {"ok": True, **result}

-

-

 @app.get("/api/files/{stored_name}")

 def serve_file(stored_name: str, sess=Depends(get_current_user)):
```

**文件:** `frontend\app.js`
```diff
--- frontend\app.js
+++ frontend\app.js
@@ -137,8 +137,5 @@
 /* ---------- 加载数据 ---------- */

 async function loadAll() {

-  // 历史版本仅管理员可加载（普通用户后端 403，直接跳过避免报错）

-  const jobs = [loadStats(), loadCategories(), loadDocuments()];

-  if (auth.user?.role === "admin") jobs.push(loadVersions());

-  await Promise.all(jobs);

+  await Promise.all([loadStats(), loadCategories(), loadDocuments()]);

 }

 

@@ -656,176 +653,10 @@
 $("sidebarMask").addEventListener("click", closeSidebar);

 

-/* ---------- 历史版本 ---------- */

-const versionState = {

-  items: [],

-  open: false,

-  expanded: new Set(),

-  restoringIndex: null,

-};

-

-async function loadVersions() {

-  try {

-    const d = await api("/api/versions");

-    versionState.items = d.items || [];

-    const badge = $("versionBadge");

-    const n = versionState.items.length;

-    if (n > 0) {

-      badge.textContent = n;

-      badge.hidden = false;

-    } else {

-      badge.hidden = true;

-    }

-  } catch (e) { /* ignore */ }

-}

-

-function openVersionPanel() {

-  versionState.open = true;

-  state.lastFocused = document.activeElement;

-  $("versionMask").hidden = false;

-  renderVersions();

-  requestAnimationFrame(() => {

-    $("versionMask").classList.add("show");

-    $("versionPanel").classList.add("open");

-    $("versionPanel").setAttribute("aria-hidden", "false");

-    $("versionClose").focus();

-  });

-}

-

-function closeVersionPanel() {

-  $("versionPanel").classList.remove("open");

-  $("versionMask").classList.remove("show");

-  $("versionPanel").setAttribute("aria-hidden", "true");

-  versionState.open = false;

-  setTimeout(() => {

-    $("versionMask").hidden = true;

-    if (state.lastFocused && document.contains(state.lastFocused)) state.lastFocused.focus();

-    state.lastFocused = null;

-  }, 450);

-}

-

-function renderVersions() {

-  const body = $("versionBody");

-  if (!versionState.items.length) {

-    body.innerHTML = '<div class="version-empty">暂无历史版本<br>每次升级确认方案后会自动归档到这里</div>';

-    return;

-  }

-  body.innerHTML = versionState.items

-    .map(

-      (v, i) => `

-      <div class="version-item ${i === 0 ? "latest" : ""} ${versionState.expanded.has(v.index) ? "expanded" : ""}" data-vindex="${v.index}" style="animation-delay:${Math.min(i * 40, 300)}ms">

-        <div class="version-item-head" role="button" tabindex="0" aria-expanded="${versionState.expanded.has(v.index)}">

-          <span class="version-time">${esc(v.time)}${i === 0 ? " · 最新" : ""}</span>

-          <span class="version-desc">${esc(v.desc || "（无说明）")}</span>

-          <span class="version-files">${v.files.length} 个文件</span>

-          <span class="version-arrow">▼</span>

-        </div>

-        <div class="version-detail">

-          ${v.files.map((f) => {

-            const lines = f.diff ? f.diff.split("\n").length : 0;

-            return `

-            <div class="version-file-block">

-              <button class="version-file-head" role="button" aria-expanded="false">

-                <span class="version-file-name">${esc(f.path)}</span>

-                <span class="version-file-meta">${lines} 行</span>

-                <span class="version-file-arrow">▼</span>

-              </button>

-              <div class="version-diff">${renderDiff(f.diff)}</div>

-            </div>`;

-          }).join("")}

-          <div class="version-actions">

-            <button class="restore-btn" data-rindex="${v.index}">${i === 0 ? "撤销此升级" : "恢复到该版本"}</button>

-            <span class="restore-hint">恢复会覆盖当前代码，操作前自动留档</span>

-          </div>

-        </div>

-      </div>`

-    )

-    .join("");

-

-  body.querySelectorAll(".version-item-head").forEach((el) => {

-    const toggle = () => {

-      const vindex = Number(el.closest(".version-item").dataset.vindex);

-      if (versionState.expanded.has(vindex)) versionState.expanded.delete(vindex);

-      else versionState.expanded.add(vindex);

-      renderVersions();

-    };

-    el.addEventListener("click", toggle);

-    el.addEventListener("keydown", (e) => {

-      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }

-    });

-  });

-  body.querySelectorAll(".version-file-head").forEach((el) => {

-    const toggle = () => {

-      const block = el.closest(".version-file-block");

-      const open = block.classList.toggle("open");

-      el.setAttribute("aria-expanded", String(open));

-    };

-    el.addEventListener("click", toggle);

-  });

-  body.querySelectorAll(".restore-btn").forEach((el) =>

-    el.addEventListener("click", () => {

-      versionState.restoringIndex = Number(el.dataset.rindex);

-      const v = versionState.items.find((x) => x.index === versionState.restoringIndex);

-      const isLatest = versionState.items[0] && versionState.items[0].index === v.index;

-      $("restoreTitle").textContent = isLatest ? "撤销此升级？" : "恢复到该版本？";

-      $("restoreSub").textContent = `将${isLatest ? "撤销" : "恢复到"} ${esc(v.time)} 的「${esc(v.desc || "无说明")}」，涉及 ${v.files.length} 个文件，当前代码会被覆盖（恢复前会自动留档）。`;

-      $("restoreMask").hidden = false;

-      requestAnimationFrame(() => $("restoreCancel").focus());

-    })

-  );

-}

-

-/* diff 语法着色 */

-function renderDiff(diff) {

-  return esc(diff)

-    .split("\n")

-    .map((line) => {

-      if (line.startsWith("@@")) return `<span class="hl">${line}</span>`;

-      if (line.startsWith("-") && !line.startsWith("---")) return `<span class="dl">${line}</span>`;

-      if (line.startsWith("+") && !line.startsWith("+++")) return `<span class="al">${line}</span>`;

-      if (line.startsWith(" ") || line.startsWith("-") || line.startsWith("+") || line.startsWith("\\")) return `<span class="cl">${line}</span>`;

-      return `<span class="cl">${line}</span>`;

-    })

-    .join("\n");

-}

-

-async function confirmRestore() {

-  if (versionState.restoringIndex === null) return;

-  const idx = versionState.restoringIndex;

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-  try {

-    const res = await api(`/api/versions/${idx}/restore`, { method: "POST" });

-    if (res.has_errors) {

-      toast("恢复完成，但有 " + res.results.filter((r) => !r.ok).length + " 个文件失败，请查看");

-    } else {

-      toast("已恢复到 " + res.target.time + " 的版本");

-    }

-    closeVersionPanel();

-    await loadAll();

-    setTimeout(() => toast("代码已切换，若界面异常请重启服务（双击 start.bat）"), 800);

-  } catch (e) {

-    toast("恢复失败: " + e.message);

-  }

-}

-

-$("versionEntry").addEventListener("click", openVersionPanel);

-$("versionClose").addEventListener("click", closeVersionPanel);

-$("versionMask").addEventListener("click", closeVersionPanel);

-$("restoreCancel").addEventListener("click", () => {

-  versionState.restoringIndex = null;

-  $("restoreMask").hidden = true;

-});

-$("restoreConfirm").addEventListener("click", confirmRestore);

-

 /* ---------- 全局键盘 ---------- */

 document.addEventListener("keydown", (e) => {

-  // Esc 优先关弹窗 → 版本面板 → 抽屉 → 侧边栏

+  // Esc 优先关弹窗 → 抽屉 → 侧边栏

   if (e.key === "Escape") {

     if (state.modalOpen) {

       $("modalCancel").click();

-    } else if (!versionState.restoringIndex && !$("restoreMask").hidden) {

-      $("restoreCancel").click();

-    } else if (versionState.open) {

-      closeVersionPanel();

     } else if (state.drawerOpen) {

       closeDrawer();

@@ -842,15 +673,7 @@
     return;

   }

-  // 抽屉/版本面板焦点陷阱

+  // 抽屉焦点陷阱

   if (e.key === "Tab") {

-    if (versionState.open) {

-      const focusables = $("versionPanel").querySelectorAll('button, [tabindex]:not([tabindex="-1"])');

-      if (focusables.length) {

-        const first = focusables[0];

-        const last = focusables[focusables.length - 1];

-        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }

-        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }

-      }

-    } else if (state.drawerOpen) {

+    if (state.drawerOpen) {

       const focusables = $("drawer").querySelectorAll('button, a[href], [tabindex]:not([tabindex="-1"])');

       if (focusables.length) {

@@ -862,5 +685,5 @@
     }

   }

-  // 删除/恢复弹窗焦点陷阱

+  // 删除弹窗焦点陷阱

   if (e.key === "Tab" && state.modalOpen) {

     const btns = $("modalMask").querySelectorAll("button");

@@ -932,6 +755,4 @@
   renderUserMenu();

   $("menuAdmin").hidden = user.role !== "admin";

-  // 历史版本（代码回滚）仅管理员可用：非管理员隐藏入口

-  $("versionEntry").hidden = user.role !== "admin";

   loadAll().catch((e) => toast("加载失败: " + e.message));

 }
```

**文件:** `frontend\index.html`
```diff
--- frontend\index.html
+++ frontend\index.html
@@ -265,10 +265,4 @@
     </div>

 

-    <button class="version-entry" id="versionEntry" title="查看历史版本">

-      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/></svg>

-      <span>历史版本</span>

-      <span class="version-badge" id="versionBadge" hidden>0</span>

-    </button>

-

     <div class="sidebar-foot">

       <div class="drop-hint">把报告 / 笔记拖进窗口即可入库</div>

@@ -381,29 +375,4 @@
 </div>

 

-<!-- 历史版本面板 -->

-<div class="version-mask" id="versionMask" hidden></div>

-<aside class="version-panel" id="versionPanel" role="dialog" aria-modal="true" aria-hidden="true" aria-label="历史版本">

-  <div class="drawer-head">

-    <button class="icon-btn" id="versionClose" title="关闭（Esc）" aria-label="关闭历史版本">✕</button>

-    <div class="version-head-title">

-      <span>🕘 历史版本</span>

-      <span class="version-head-sub">最近 5 次代码升级，点击可查看改动 / 恢复</span>

-    </div>

-  </div>

-  <div class="version-body" id="versionBody"></div>

-</aside>

-

-<!-- 恢复确认弹窗 -->

-<div class="modal-mask" id="restoreMask" hidden>

-  <div class="modal" role="alertdialog" aria-modal="true" aria-labelledby="restoreTitle" aria-describedby="restoreSub">

-    <div class="modal-title" id="restoreTitle">恢复到该版本？</div>

-    <div class="modal-sub" id="restoreSub"></div>

-    <div class="modal-actions">

-      <button class="modal-btn cancel" id="restoreCancel">取消</button>

-      <button class="modal-btn danger" id="restoreConfirm">恢复</button>

-    </div>

-  </div>

-</div>

-

 <!-- 修改密码弹窗 -->

 <div class="modal-mask" id="pwModalMask" hidden>
```

**文件:** `frontend\style.css`
```diff
--- frontend\style.css
+++ frontend\style.css
@@ -162,130 +162,4 @@
 .drop-hint { font-size: 11px; color: var(--label-2); text-align: center; line-height: 1.6; }

 

-/* 历史版本入口 */

-.version-entry {

-  display: flex; align-items: center; gap: 9px;

-  width: 100%; margin-bottom: 14px;

-  padding: 10px 12px; border-radius: 10px;

-  background: var(--card); color: var(--label);

-  font-size: 13.5px; font-weight: 600;

-  border: 1px solid var(--separator);

-  transition: all 0.2s var(--spring);

-}

-.version-entry:hover { background: var(--card-hover); border-color: var(--accent); transform: translateY(-1px); }

-.version-entry:active { transform: scale(0.98); }

-.version-badge {

-  margin-left: auto;

-  font-size: 11px; font-weight: 700; color: var(--accent-2);

-  background: rgba(100, 210, 255, 0.12);

-  border-radius: 999px; padding: 1px 8px;

-}

-

-/* 历史版本面板 */

-.version-mask {

-  position: fixed; inset: 0; z-index: 90;

-  background: rgba(0, 0, 0, 0.5);

-  backdrop-filter: blur(4px);

-  -webkit-backdrop-filter: blur(4px);

-  opacity: 0; transition: opacity 0.3s var(--ease);

-}

-.version-mask.show { opacity: 1; }

-

-.version-panel {

-  position: fixed; top: 0; right: 0; bottom: 0; z-index: 91;

-  width: min(620px, 94vw);

-  background: var(--surface);

-  border-left: 1px solid var(--separator);

-  box-shadow: -20px 0 60px rgba(0, 0, 0, 0.5);

-  transform: translateX(102%);

-  transition: transform 0.45s var(--spring);

-  display: flex; flex-direction: column;

-}

-.version-panel.open { transform: translateX(0); }

-.version-head-title { display: flex; flex-direction: column; gap: 2px; margin-left: 12px; }

-.version-head-title span:first-child { font-size: 15px; font-weight: 700; }

-.version-head-sub { font-size: 11px; color: var(--label-2); }

-

-.version-body { flex: 1; overflow-y: auto; padding: 18px 20px 50px; }

-.version-body::-webkit-scrollbar { width: 8px; }

-.version-body::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 4px; }

-

-.version-empty { text-align: center; padding: 60px 20px; color: var(--label-2); font-size: 13px; line-height: 1.8; }

-

-.version-item {

-  background: var(--card);

-  border: 1px solid var(--separator);

-  border-radius: 14px;

-  margin-bottom: 12px;

-  overflow: hidden;

-  animation: fadeUp 0.35s var(--spring) backwards;

-}

-.version-item.latest { border-color: rgba(10, 132, 255, 0.4); }

-.version-item-head {

-  display: flex; align-items: center; gap: 10px;

-  padding: 13px 16px; cursor: pointer;

-  transition: background 0.2s;

-}

-.version-item-head:hover { background: var(--hover-soft); }

-.version-time { font-size: 12.5px; font-weight: 700; color: var(--accent-2); flex-shrink: 0; }

-.version-desc {

-  flex: 1; min-width: 0;

-  font-size: 13px; line-height: 1.5;

-  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;

-}

-.version-files { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-arrow { color: var(--label-2); font-size: 12px; transition: transform 0.3s var(--spring); flex-shrink: 0; }

-.version-item.expanded .version-arrow { transform: rotate(180deg); }

-

-.version-detail { display: none; padding: 0 16px 14px; }

-.version-item.expanded .version-detail { display: block; animation: fadeIn 0.25s var(--ease); }

-.version-file-block { margin-bottom: 10px; border: 1px solid var(--separator); border-radius: 10px; overflow: hidden; }

-.version-file-head {

-  display: flex; align-items: center; gap: 8px;

-  width: 100%; padding: 9px 12px;

-  background: var(--hover-soft);

-  cursor: pointer;

-  transition: background 0.2s;

-}

-.version-file-head:hover { background: var(--chip-bg); }

-.version-file-name {

-  flex: 1; min-width: 0;

-  font-size: 12px; font-weight: 600; color: var(--label);

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;

-}

-.version-file-meta { font-size: 11px; color: var(--label-2); flex-shrink: 0; }

-.version-file-arrow { color: var(--label-2); font-size: 10px; flex-shrink: 0; transition: transform 0.3s var(--spring); }

-.version-file-block.open .version-file-arrow { transform: rotate(180deg); }

-.version-diff {

-  background: var(--code-bg);

-  padding: 12px 14px;

-  font-family: ui-monospace, "Cascadia Code", Consolas, monospace;

-  font-size: 12px; line-height: 1.7;

-  max-height: 320px; overflow: auto;

-  white-space: pre-wrap; word-break: break-all;

-  color: rgba(242, 242, 247, 0.75);

-  border-top: 1px solid var(--separator);

-  display: none;

-}

-.version-file-block.open .version-diff { display: block; animation: fadeIn 0.2s var(--ease); }

-.version-diff::-webkit-scrollbar { width: 6px; height: 6px; }

-.version-diff::-webkit-scrollbar-thumb { background: var(--scroll-thumb); border-radius: 3px; }

-.version-diff .dl { color: var(--danger); }

-.version-diff .al { color: var(--green); }

-.version-diff .cl { color: var(--label-2); }

-.version-diff .hl { color: var(--accent-2); }

-

-.version-actions { display: flex; gap: 10px; align-items: center; margin-top: 4px; }

-.restore-btn {

-  font-size: 12.5px; font-weight: 700; color: #fff;

-  background: var(--accent); border-radius: 10px;

-  padding: 8px 18px;

-  transition: all 0.2s var(--spring);

-}

-.restore-btn:hover { filter: brightness(1.12); transform: translateY(-1px); }

-.restore-btn:active { transform: scale(0.96); }

-.restore-btn:disabled { background: var(--separator); color: var(--label-2); cursor: not-allowed; transform: none; }

-.restore-hint { font-size: 11px; color: var(--label-2); }

-

 /* ============ 主区域 ============ */

 .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
```

---

### 2026-09-02 11:09:29 | 新增增量备份工具：tools/backup.py（内容寻址对象库，sha256 去重 + 快照清单，跨版本零冗余；支持 backup/list/restore/prune，SQLite 用 backup API 保证运行中一致性，不依赖 Git/境外网络）+ 根目录备份.bat 一键双击备份

**文件:** `tools\backup.py`（新增文件）
```diff
--- /dev/null
+++ tools\backup.py
@@ -0,0 +1,309 @@
+# -*- coding: utf-8 -*-
+"""
+知识工作台 · 增量备份工具（零依赖 · 纯本地 · 不依赖 Git）
+==========================================================
+内容寻址对象库（git 对象模型的极简版）。省空间三招：
+
+  1) 内容去重：每个文件按 sha256 内容指纹存储，相同内容跨快照只存一份
+  2) 快照即清单：每次备份只生成一个 JSON 清单（路径 -> 对象ID），
+     文件没变时仅追加几百字节引用，不复制任何字节
+  3) 自动裁剪：prune 删除最旧快照并回收无人引用的孤儿对象
+
+用法:
+  python tools/backup.py backup  [--comment "说明"] [--dir 备份根]   # 一键备份
+  python tools/backup.py list   [--dir 备份根]                      # 查看快照
+  python tools/backup.py restore <快照名> [--to 目标目录] [--dir 备份根]  # 恢复
+  python tools/backup.py prune  --keep 10 [--dir 备份根]            # 只留最近 N 份
+
+备份根默认 <项目根>/backup/，--dir 可指向其他位置（U 盘 / 网盘同步目录）。
+knowledge.db 等 SQLite 文件用 Online Backup API 生成一致性副本，运行中备份也安全。
+"""
+import os
+import sys
+import json
+import shutil
+import hashlib
+import tempfile
+import argparse
+from datetime import datetime
+
+try:
+    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
+except Exception:
+    pass
+
+BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
+DEFAULT_BACKUP_ROOT = os.path.join(BASE, "backup")
+CHUNK = 1 << 20  # 1MB 分块，大文件不占内存
+
+
+# ---------- 排除规则 ----------
+def is_ignored(rel: str) -> bool:
+    rel = rel.replace("\\", "/").strip("/")
+    if not rel:
+        return True
+    if rel == "backup" or rel.startswith("backup/"):
+        return True  # 备份根自身（默认位置）
+    if rel == ".workbuddy" or rel.startswith(".workbuddy/"):
+        return True
+    if rel == "data/changelog_staging" or rel.startswith("data/changelog_staging/"):
+        return True
+    parts = rel.split("/")
+    if "__pycache__" in parts or rel.endswith(".pyc"):
+        return True
+    return False
+
+
+def _fmt(n):
+    for unit in ("B", "KB", "MB", "GB"):
+        if n < 1024 or unit == "GB":
+            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
+        n /= 1024
+
+
+def _sha256(path: str) -> str:
+    h = hashlib.sha256()
+    with open(path, "rb") as f:
+        for chunk in iter(lambda: f.read(CHUNK), b""):
+            h.update(chunk)
+    return h.hexdigest()
+
+
+def _obj_path(root: str, oid: str) -> str:
+    return os.path.join(root, "objects", oid[:2], oid[2:])
+
+
+def _store_object(root: str, src: str, oid: str) -> None:
+    dst = _obj_path(root, oid)
+    if os.path.exists(dst):
+        return  # 内容已存在：去重，零写入
+    os.makedirs(os.path.dirname(dst), exist_ok=True)
+    tmp = dst + ".tmp"
+    shutil.copyfile(src, tmp)
+    os.replace(tmp, dst)  # 原子提交
+
+
+def _ingest(root: str, path: str, rel: str) -> dict:
+    """收进对象库；SQLite 走 backup API 保证运行中备份的一致性。"""
+    src, tmpdb = path, None
+    if rel.lower().endswith((".db", ".sqlite", ".sqlite3")):
+        try:
+            import sqlite3
+            tf = tempfile.NamedTemporaryFile(delete=False, suffix=".db")
+            tf.close()
+            tmpdb = tf.name
+            s = sqlite3.connect(str(path))
+            d = sqlite3.connect(tmpdb)
+            with d:
+                s.backup(d)
+            s.close()
+            d.close()
+            src = tmpdb
+        except Exception:
+            tmpdb = None  # 非 SQLite 或失败：回退普通复制
+    try:
+        oid = _sha256(src)
+        _store_object(root, src, oid)
+        return {"oid": oid, "size": os.path.getsize(src),
+                "mtime": os.path.getmtime(path)}
+    finally:
+        if tmpdb:
+            try:
+                os.unlink(tmpdb)
+            except OSError:
+                pass
+
+
+def _list_snapshots(root: str):
+    d = os.path.join(root, "snapshots")
+    if not os.path.isdir(d):
+        return []
+    return sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".json"))
+
+
+def _store_size(root: str) -> int:
+    od = os.path.join(root, "objects")
+    total = 0
+    if os.path.isdir(od):
+        for dp, _, fns in os.walk(od):
+            for fn in fns:
+                try:
+                    total += os.path.getsize(os.path.join(dp, fn))
+                except OSError:
+                    pass
+    return total
+
+
+# ---------- 子命令 ----------
+def cmd_backup(args):
+    root = args.dir
+    os.makedirs(os.path.join(root, "objects"), exist_ok=True)
+    os.makedirs(os.path.join(root, "snapshots"), exist_ok=True)
+
+    files, total = {}, 0
+    for dirpath, dirnames, filenames in os.walk(BASE):
+        dirnames[:] = [d for d in dirnames if not is_ignored(
+            os.path.relpath(os.path.join(dirpath, d), BASE))]
+        for fn in filenames:
+            full = os.path.join(dirpath, fn)
+            rel = os.path.relpath(full, BASE)
+            if is_ignored(rel):
+                continue
+            info = _ingest(root, full, rel)
+            files[rel.replace("\\", "/")] = info
+            total += info["size"]
+
+    name = datetime.now().strftime("%Y%m%d_%H%M%S")
+    snap = os.path.join(root, "snapshots", name + ".json")
+    i = 2
+    while os.path.exists(snap):
+        snap = os.path.join(root, "snapshots", f"{name}_{i}.json")
+        i += 1
+    prev = _latest(root)  # 必须在写入前取上一快照，否则会对比到自身
+    meta = {
+        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
+        "root": BASE,
+        "comment": args.comment or "",
+        "n_files": len(files),
+        "total_size": total,
+        "files": files,
+    }
+    with open(snap, "w", encoding="utf-8") as f:
+        json.dump(meta, f, ensure_ascii=False, indent=1)
+
+    added = added_bytes = 0
+    if prev:
+        prev_objs = {i["oid"] for i in prev["files"].values()}
+        for info in files.values():
+            if info["oid"] not in prev_objs:
+                added += 1
+                added_bytes += info["size"]
+
+    print(f"  ✓ 备份完成: {os.path.basename(snap)[:-5]}")
+    print(f"    文件 {len(files)} 个 / 逻辑大小 {_fmt(total)}")
+    if prev:
+        print(f"    相对上次: 新增/变更 {added} 个文件, 新增存储 {_fmt(added_bytes)}")
+    else:
+        print("    首次备份: 全量入库")
+    print(f"    对象库物理占用: {_fmt(_store_size(root))}")
+    print(f"    备份位置: {root}")
+
+
+def _latest(root: str):
+    snaps = _list_snapshots(root)
+    if not snaps:
+        return None
+    with open(snaps[-1], encoding="utf-8") as f:
+        return json.load(f)
+
+
+def cmd_list(args):
+    root = args.dir
+    snaps = _list_snapshots(root)
+    if not snaps:
+        print("  (还没有备份快照) 运行: python tools/backup.py backup")
+        return
+    print(f"备份位置: {root}")
+    print(f"对象库物理占用: {_fmt(_store_size(root))}")
+    print("-" * 80)
+    prev_objs = None
+    for p in snaps:
+        meta = json.load(open(p, encoding="utf-8"))
+        name = os.path.basename(p)[:-5]
+        objs = {i["oid"] for i in meta["files"].values()}
+        chg = f"  (+{len(objs - prev_objs)} 文件变化)" if prev_objs is not None else "  (首次)"
+        prev_objs = objs
+        cmt = f"  「{meta.get('comment', '')}」" if meta.get("comment") else ""
+        print(f"  {name}  {meta['n_files']} 文件 {_fmt(meta['total_size'])}{chg}{cmt}")
+
+
+def cmd_restore(args):
+    root = args.dir
+    snap = args.snapshot if args.snapshot.endswith(".json") else args.snapshot + ".json"
+    p = os.path.join(root, "snapshots", snap)
+    if not os.path.exists(p):
+        print(f"  ✗ 快照不存在: {snap}")
+        sys.exit(1)
+    meta = json.load(open(p, encoding="utf-8"))
+    dst_root = os.path.abspath(args.to) if args.to else BASE
+    print(f"  恢复快照 {os.path.basename(p)[:-5]} -> {dst_root}")
+    n = 0
+    for rel, info in meta["files"].items():
+        full = os.path.realpath(os.path.join(dst_root, rel))
+        if not full.startswith(os.path.realpath(dst_root) + os.sep):
+            raise ValueError(f"非法路径: {rel}")
+        os.makedirs(os.path.dirname(full), exist_ok=True)
+        shutil.copy2(_obj_path(root, info["oid"]), full)
+        if "mtime" in info:
+            try:
+                os.utime(full, (info["mtime"], info["mtime"]))
+            except OSError:
+                pass
+        n += 1
+    print(f"  ✓ 已恢复 {n} 个文件")
+
+
+def cmd_prune(args):
+    root = args.dir
+    snaps = _list_snapshots(root)
+    if len(snaps) <= args.keep:
+        print(f"  当前 {len(snaps)} 份快照 ≤ 保留 {args.keep} 份，无需裁剪")
+        return
+    for p in snaps[:-args.keep]:
+        os.remove(p)
+        print(f"  - 删除快照 {os.path.basename(p)[:-5]}")
+    keep_objs = set()
+    for p in snaps[-args.keep:]:
+        meta = json.load(open(p, encoding="utf-8"))
+        keep_objs |= {i["oid"] for i in meta["files"].values()}
+    od = os.path.join(root, "objects")
+    freed = freed_bytes = 0
+    if os.path.isdir(od):
+        for d in os.listdir(od):
+            dd = os.path.join(od, d)
+            if not os.path.isdir(dd):
+                continue
+            for fn in os.listdir(dd):
+                oid = d + fn
+                if oid not in keep_objs:
+                    try:
+                        freed_bytes += os.path.getsize(os.path.join(dd, fn))
+                        os.remove(os.path.join(dd, fn))
+                        freed += 1
+                    except OSError:
+                        pass
+            if not os.listdir(dd):
+                try:
+                    os.rmdir(dd)
+                except OSError:
+                    pass
+    print(f"  ✓ 已清理孤儿对象 {freed} 个，释放 {_fmt(freed_bytes)}")
+
+
+def main():
+    ap = argparse.ArgumentParser(description="知识工作台增量备份工具")
+    sub = ap.add_subparsers(dest="cmd", required=True)
+
+    def common(p):
+        p.add_argument("--dir", default=DEFAULT_BACKUP_ROOT,
+                       help=f"备份根目录(默认 {DEFAULT_BACKUP_ROOT})")
+
+    p1 = sub.add_parser("backup")
+    common(p1)
+    p1.add_argument("--comment", default="", help="本次备份说明")
+    p2 = sub.add_parser("list")
+    common(p2)
+    p3 = sub.add_parser("restore")
+    common(p3)
+    p3.add_argument("snapshot", help="快照名，如 20260902_113000")
+    p3.add_argument("--to", default=None, help="恢复目标目录(默认项目根)")
+    p4 = sub.add_parser("prune")
+    common(p4)
+    p4.add_argument("--keep", type=int, default=10, help="保留最近 N 份快照(默认10)")
+    args = ap.parse_args()
+    {"backup": cmd_backup, "list": cmd_list,
+     "restore": cmd_restore, "prune": cmd_prune}[args.cmd](args)
+
+
+if __name__ == "__main__":
+    main()
```

**文件:** `备份.bat`（新增文件）
```diff
--- /dev/null
+++ 备份.bat
@@ -0,0 +1,17 @@
+@echo off
+chcp 65001 >nul
+title 一键备份 · 个人知识管理工作台
+cd /d "%~dp0"
+
+set PY=C:\Users\King\.workbuddy\binaries\python\envs\kb\Scripts\python.exe
+if not exist "%PY%" set PY=python
+
+echo.
+echo  ========================================
+echo    增量备份（内容去重，相同文件不重复存储）
+echo    备份位置: backup\
+echo  ========================================
+echo.
+"%PY%" tools\backup.py backup %*
+echo.
+pause
```
