# -*- coding: utf-8 -*-
"""登录 / 注册 / 登出 / 当前用户 / 改密码。"""
import logging

from fastapi import Request
from pydantic import BaseModel, Field

import config
from app.api import ok, router
from app.api.deps import CurrentUser, MaybeUser, login_session, logout_session
from app.business import auth

log = logging.getLogger(__name__)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RegisterIn(BaseModel):
    username: str = Field(min_length=2, max_length=32)
    password: str = Field(min_length=auth.MIN_PASSWORD_LEN, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=auth.MIN_PASSWORD_LEN, max_length=256)


@router.post("/auth/login")
def api_login(body: LoginIn, request: Request):
    """登录成功后把身份写进签名 cookie；此后所有接口的用户都从这里取。"""
    user = auth.authenticate(body.username, body.password)
    login_session(request, user)
    log.info("登录：%s（%s）", user["username"], user["role"])
    return ok({"user": user, "redirect": "/admin" if user["role"] == "admin" else "/"})


@router.post("/auth/register")
def api_register(body: RegisterIn, request: Request):
    """自助注册，固定为 student 角色；可用 CPA_ALLOW_REGISTER=0 关闭。"""
    if not config.ALLOW_SELF_REGISTER:
        raise ValueError("本站已关闭自助注册，请联系管理员开通账号")
    user = auth.create_user(body.username, body.password, role="student",
                            display_name=body.display_name)
    login_session(request, user)          # 注册即登录
    return ok({"user": user, "redirect": "/"})


@router.post("/auth/logout")
def api_logout(request: Request):
    logout_session(request)
    return ok({"logged_out": True, "redirect": "/login"})


@router.get("/auth/me")
def api_me(user: MaybeUser):
    """未登录返回 authenticated=false，前端据此跳登录页。"""
    return ok({"authenticated": bool(user), "user": user,
               "allow_register": config.ALLOW_SELF_REGISTER})


@router.post("/auth/password")
def api_change_password(body: ChangePasswordIn, user: CurrentUser):
    auth.change_password(user["id"], body.old_password, body.new_password)
    return ok({"changed": True})
