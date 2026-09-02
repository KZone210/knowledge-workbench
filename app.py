# -*- coding: utf-8 -*-
"""知识管理工作台 - 后端服务（启用账号认证 + 全盘加密）

运行: python app.py  (或 uvicorn app:app)
安全模型: 磁盘恒密文 · 密码即钥匙 · 会话内存驻留 · 全部 API 鉴权
加固说明（2026-09-02 安全审计）:
- CORS 收紧为同源回环地址（本地单机部署，前端静态资源由本服务托管）
- 安全响应头（nosniff / frame / referrer / CSP / permissions-policy）
- 关闭 /docs /redoc /openapi 暴露
- 登录/注册/找回/改密接入 IP 级限流；账号锁定消息模糊化
- 上传文件大小/数量限制；serve_file 路径穿越纵深防御；limit 参数收敛
- 审计日志记录来源 IP
"""
import os
import uuid
import logging
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, Query, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import parser, nlp, classify, store
from backend.security import auth, deps, ratelimit
from backend.security.deps import get_current_user, require_admin

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kb")

BASE_DIR = Path(__file__).parent
FRONTEND_DIR = BASE_DIR / "frontend"

# ---------------- 安全常量 ----------------
MAX_FILE_SIZE = 200 * 1024 * 1024      # 单文件上传上限 200MB（解析/解密均走流式/内存可控）
MAX_FILES_PER_BATCH = 50               # 单次批量上传文件数上限
MAX_JSON_BODY = 1 * 1024 * 1024        # JSON 请求体上限 1MB（multipart 上传走流式，不在此限）
_ALLOWED_METHODS = ["GET", "POST", "DELETE", "OPTIONS"]
_ALLOWED_HEADERS = ["Authorization", "Content-Type"]

_PORT = int(os.environ.get("PORT", 8787))

app = FastAPI(
    title="个人知识管理工作台", version="2.0.0",
    docs_url=None, redoc_url=None, openapi_url=None,  # 纵深防御：关闭 API 结构暴露
)
# CORS 收紧：本地单机应用，前端与 API 同源托管，仅放行回环地址
app.add_middleware(
    CORSMiddleware,
    allow_origins=[f"http://127.0.0.1:{_PORT}", f"http://localhost:{_PORT}"],
    allow_methods=_ALLOWED_METHODS,
    allow_headers=_ALLOWED_HEADERS,
    allow_credentials=False,
    max_age=600,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """安全响应头：防 MIME 嗅探 / 点击劫持 / 信息泄露 / 权限滥用。"""
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")  # 允许同源 iframe 预览 PDF
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; "
        "frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'self'",
    )
    return resp


@app.middleware("http")
async def limit_json_body(request: Request, call_next):
    """JSON 请求体大小限制（multipart 上传为流式 SpooledTemporaryFile，不受此限）。"""
    if request.method in ("POST", "PUT", "PATCH") and not request.url.path.startswith("/api/upload"):
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_JSON_BODY:
            return JSONResponse({"detail": "请求体过大"}, status_code=413)
    return await call_next(request)


def _rl(limiter, action: str):
    """IP 限流依赖工厂：按 (来源IP, 动作) 滑动窗口计数，超限 429。"""
    def dep(request: Request):
        if not limiter.allow(f"{ratelimit.client_ip(request)}:{action}"):
            raise HTTPException(429, "操作过于频繁，请稍后再试")
    return dep


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """静态资源禁用浏览器缓存（无 Cache-Control 时浏览器启发式缓存旧版
    HTML/CSS/JS，导致其他浏览器访问时新 HTML 配旧 CSS 出现界面错乱）。"""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
    return resp


# ---------------- 启动初始化 ----------------
def init_all():
    store.init_db()
    auth.init_security()


# ---------------- 文档处理（登录态，全程加密） ----------------

def process_file(upload: UploadFile, rel_path: str, dek: bytes, user_id: int):
    """完整流水线：临时明文解析 → 加密落盘 → 关键词/分类/摘要 → 密文入库。

    磁盘最终只有密文；临时明文文件处理完立即删除。
    """
    filename = upload.filename or "未命名文件"
    ext = os.path.splitext(filename)[1].lower()

    if ext not in parser.SUPPORTED_EXTS:
        return {
            "ok": False, "filename": filename,
            "error": f"不支持的文件格式 {ext or '(无扩展名)'}，支持: {', '.join(sorted(parser.SUPPORTED_EXTS))}",
        }

    # 1) 上传流写随机明文临时文件（带原扩展名，供解析器按类型分发），流式计数防超大文件
    tmp = Path(store.DOCS_DIR) / f".{uuid.uuid4().hex}{ext}"
    try:
        written = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = upload.file.read(1 << 20)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    return {
                        "ok": False, "filename": filename,
                        "error": f"文件过大（上限 {MAX_FILE_SIZE // (1024 * 1024)}MB），已拒绝入库",
                    }
                f.write(chunk)

        # 2) 解析（明文阶段，仅内存/临时）
        try:
            text, title = parser.extract_text(str(tmp))
        except Exception as e:
            log.exception("解析失败 %s", filename)
            return {"ok": False, "filename": filename, "error": f"解析失败: {e}"}

        # 3) 加密落盘（此时磁盘写入的是密文副本）
        stored_name, abs_path = store.save_plain_to_enc(dek, str(tmp), ext)
        file_size = os.path.getsize(abs_path)

        if len(text.strip()) < 20:
            meta = {
                "filename": filename, "stored_name": stored_name, "file_size": file_size,
                "ext": ext, "title": title, "category": "未分类",
                "tags": [], "keywords": [], "summary": "（未提取到有效文本，可能是扫描件/图片型 PDF）",
                "word_count": 0, "content": text, "path": rel_path,
            }
            doc_id = store.insert_document(meta, user_id, dek)
            return {"ok": True, "id": doc_id, "filename": filename, "title": title, "category": "未分类",
                    "keywords": [], "summary": meta["summary"], "low_text": True}

        keywords = nlp.extract_keywords(text, top_n=8)
        category, score, matched = classify.classify(text, keywords)
        summary = nlp.summarize(text, top_n=3)
        tags = classify.build_tags(keywords, category)
        meta = {
            "filename": filename, "stored_name": stored_name, "file_size": file_size,
            "ext": ext, "title": title, "category": category,
            "tags": tags, "keywords": keywords, "summary": summary,
            "word_count": nlp.word_count(text), "content": text, "path": rel_path,
        }
        doc_id = store.insert_document(meta, user_id, dek)
        log.info("入库成功: %s -> [%s] %s", filename, category, title[:30])
        return {
            "ok": True, "id": doc_id, "filename": filename, "title": title,
            "category": category, "keywords": keywords, "summary": summary,
        }
    finally:
        if tmp.exists():
            try:
                os.remove(tmp)
            except OSError:
                # 明文临时文件残留即泄露风险：删除失败必须告警（Windows 沙箱等环境会拦截 os.remove）
                log.warning("临时明文文件删除失败，请手动清理: %s", tmp)


# ---------------- 认证路由 ----------------

@app.post("/api/auth/register")
def auth_register(payload: dict, request: Request,
                  _=Depends(_rl(ratelimit.register_limiter, "register"))):
    """注册普通用户。questions: [{"question","answer"}, ...] 1-3 个（可选但推荐）。"""
    try:
        result = auth.register_user(
            payload.get("username", ""),
            payload.get("password", ""),
            payload.get("questions"),
            ip=ratelimit.client_ip(request),
        )
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, **result}


@app.post("/api/auth/login")
def auth_login(payload: dict, request: Request,
               _=Depends(_rl(ratelimit.login_limiter, "login"))):
    try:
        result = auth.login(payload.get("username", ""), payload.get("password", ""),
                            bool(payload.get("remember", False)), ip=ratelimit.client_ip(request))
    except ValueError as e:
        raise HTTPException(401, str(e))
    return {"ok": True, **result}


@app.post("/api/auth/logout")
def auth_logout(request: Request):
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    auth.logout(token)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(sess=Depends(get_current_user)):
    return {
        "ok": True,
        "username": sess.username,
        "role": sess.role,
        "must_change_password": sess.must_change_password,
    }


@app.post("/api/auth/change-password")
def auth_change_password(payload: dict, request: Request,
                         sess=Depends(get_current_user),
                         _=Depends(_rl(ratelimit.change_pw_limiter, "change_pw"))):
    try:
        auth.change_password(sess.user_id, payload.get("old_password", ""),
                             payload.get("new_password", ""), ip=ratelimit.client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/auth/questions")
def auth_questions(username: str = Query("", max_length=64),
                   _=Depends(_rl(ratelimit.questions_limiter, "questions"))):
    return {"ok": True, "questions": auth.get_security_questions(username)}


@app.post("/api/auth/reset-password")
def auth_reset_password(payload: dict, request: Request,
                        _=Depends(_rl(ratelimit.reset_limiter, "reset"))):
    """安全问题找回：answers 顺序与注册时一致，全部答对才放行。"""
    try:
        auth.reset_password_via_questions(
            payload.get("username", ""),
            payload.get("answers") or [],
            payload.get("new_password", ""),
            ip=ratelimit.client_ip(request),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------------- 管理员路由 ----------------

@app.get("/api/admin/users")
def admin_users(_=Depends(require_admin)):
    return {"ok": True, "items": auth.admin_list_users()}


@app.post("/api/admin/users/{username}/reset-password")
def admin_reset(username: str, payload: dict, request: Request, sess=Depends(require_admin)):
    try:
        auth.admin_reset_password(sess.username, username, payload.get("new_password", ""),
                                  ip=ratelimit.client_ip(request))
    except (ValueError, PermissionError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/admin/audit")
def admin_audit(limit: int = Query(100, ge=1, le=500), _=Depends(require_admin)):
    return {"ok": True, "items": auth.audit_log_recent(limit)}


# ---------------- 文档 API（全部鉴权 + 用户隔离 + 实时解密） ----------------

@app.post("/api/upload")
async def upload(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(default=[]),
    sess=Depends(get_current_user),
):
    """批量上传。paths 与 files 一一对应，保存每个文件的原始文件夹路径。"""
    if not files:
        raise HTTPException(400, "未选择文件")
    if len(files) > MAX_FILES_PER_BATCH:
        raise HTTPException(400, f"单次最多上传 {MAX_FILES_PER_BATCH} 个文件")
    dek, user_id = sess.dek, sess.user_id
    results = []
    for i, f in enumerate(files):
        rel = paths[i] if i < len(paths) else (f.filename or "")
        results.append(process_file(f, rel, dek, user_id))
    ok_count = sum(1 for r in results if r.get("ok"))
    return {"ok": True, "success": ok_count, "failed": len(results) - ok_count, "results": results}


@app.get("/api/documents")
def documents(
    category: str = Query("全部"),
    q: str = Query(""),
    tag: str = Query(""),
    limit: int = Query(200, ge=1, le=500),
    sess=Depends(get_current_user),
):
    return {"ok": True, "items": store.list_documents(sess.dek, sess.user_id, category, q, tag, limit, sess.cache)}


@app.get("/api/documents/{doc_id}")
def document_detail(doc_id: int, sess=Depends(get_current_user)):
    doc = store.get_document(doc_id, sess.dek, sess.user_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    return {"ok": True, "item": doc}


@app.delete("/api/documents/{doc_id}")
def delete_doc(doc_id: int, sess=Depends(get_current_user)):
    if not store.delete_document(doc_id, sess.user_id):
        raise HTTPException(404, "文档不存在")
    return {"ok": True}


@app.get("/api/categories")
def categories(sess=Depends(get_current_user)):
    total, counts = store.category_counts(sess.dek, sess.user_id)
    return {"ok": True, "total": total, "categories": counts}


@app.get("/api/stats")
def stats(sess=Depends(get_current_user)):
    return {"ok": True, **store.stats(sess.dek, sess.user_id)}


@app.get("/api/files/{stored_name}")
def serve_file(stored_name: str, sess=Depends(get_current_user)):
    """下载文档：先校验归属（纵深防御），再密文解密到内存字节直接响应。

    路径穿越纵深防御：stored_name 必须是纯文件名（服务端生成），含路径分隔符一律拒绝，
    防止历史异常数据/数据库被篡改后拼接 DOCS_DIR 越权读取。
    """
    if not stored_name or os.path.basename(stored_name) != stored_name:
        raise HTTPException(404, "文件不存在")
    if not store.doc_owned_by(stored_name, sess.user_id):
        raise HTTPException(404, "文件不存在")
    try:
        data = store.decrypt_doc_bytes(sess.dek, stored_name)
    except FileNotFoundError:
        raise HTTPException(404, "文件不存在")
    except Exception:
        raise HTTPException(400, "文件解密失败")
    ext = os.path.splitext(stored_name)[1].lower()
    mime = {
        ".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".htm": "text/html; charset=utf-8",
    }.get(ext, "application/octet-stream")
    return Response(content=data, media_type=mime,
                    headers={"Content-Disposition": f"attachment; filename={stored_name}"})


# ---------------- 前端静态资源 ----------------
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    init_all()
    port = int(os.environ.get("PORT", 8787))
    print(f"\n  个人知识管理工作台已启动  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
