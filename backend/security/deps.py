# -*- coding: utf-8 -*-
"""FastAPI 鉴权依赖：所有业务路由必须经过 get_current_user。"""
from fastapi import Depends, HTTPException, Request

from .session import sessions


def get_current_user(request: Request):
    """从 Authorization: Bearer <token> 解析会话。未登录/过期 → 401。"""
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    sess = sessions.get(token)
    if not sess:
        raise HTTPException(401, "未登录或会话已过期")
    return sess


def require_admin(sess=Depends(get_current_user)):
    """管理员专用路由依赖。非管理员 → 403。"""
    if sess.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return sess
