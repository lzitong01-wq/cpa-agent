# -*- coding: utf-8 -*-
"""FastAPI 依赖：登录态、角色校验、通用查询参数。

这是把"每个接口都必须拿到已认证用户"变成类型强制的地方：
业务接口的签名里写 `user: CurrentUser`，忘了写就拿不到 user_id，
而不是"忘了加装饰器 → 接口照样能跑但没鉴权"。

会话存在签名 cookie 里（Starlette SessionMiddleware），只放 id/用户名/角色，
每个请求再回库确认账号仍然启用、角色没被改——所以停用或降权立即生效，
不用等 cookie 过期。
"""
import logging
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Request, status

from app.business import auth

log = logging.getLogger(__name__)

SESSION_KEY = "user"


# ------------------------------------------------------------------ 会话读写
def login_session(request: Request, user: dict) -> None:
    request.session[SESSION_KEY] = {"id": user["id"], "username": user["username"],
                                    "role": user["role"]}


def logout_session(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)


def session_user(request: Request) -> Optional[dict]:
    """返回数据库里的最新用户信息；会话无效/账号停用时清掉会话并返回 None。"""
    raw = request.session.get(SESSION_KEY)
    if not isinstance(raw, dict) or not raw.get("id"):
        return None
    row = auth.get_user(raw["id"])
    if not row or not row["is_active"]:
        request.session.pop(SESSION_KEY, None)
        return None
    if row["role"] != raw.get("role"):          # 角色被管理员改过，刷新会话
        login_session(request, row)
    return {k: row[k] for k in auth.PUBLIC_FIELDS}


# ------------------------------------------------------------------ 依赖
def optional_user(request: Request) -> Optional[dict]:
    return session_user(request)


def require_user(request: Request) -> dict:
    user = session_user(request)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if user["role"] != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该操作仅管理员可用")
    return user


CurrentUser = Annotated[dict, Depends(require_user)]
AdminUser = Annotated[dict, Depends(require_admin)]
MaybeUser = Annotated[Optional[dict], Depends(optional_user)]


# ------------------------------------------------------------------ 参数工具
def as_list(value) -> list:
    """接受 ["a","b"]、"a,b" 两种写法（前端两种都在用）。"""
    if value in (None, "", []):
        return []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend([p.strip() for p in str(v).split(",") if p.strip()])
        return out
    return [p.strip() for p in str(value).split(",") if p.strip()]


def as_int(value, default=None):
    try:
        if value in (None, "", "null", "all", "undefined"):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def clean_filters(**kw) -> dict:
    """去掉 None / 空串，避免把"未指定"当成筛选条件传进业务层。"""
    return {k: v for k, v in kw.items() if v is not None and v != ""}
