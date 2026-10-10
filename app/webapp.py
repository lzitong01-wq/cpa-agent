# -*- coding: utf-8 -*-
"""FastAPI 应用工厂：页面路由 + API 注册 + 会话中间件 + 静态资源。

页面（Jinja2 模板）：
    /login       登录 / 注册
    /            首页（学习概览 + 快速入口 + 聊天入口）
    /bank        科目章节题库页（三级分类导航 + 题目列表 + 组卷）
    /practice    答题页面（做题 / 判分 / 解析 / 追问）
    /wrongbook   错题本页面
    /stats       答题历史统计页
    /chat        AI 答疑智能体聊天页
    /admin       管理员后台（上传教材 / 知识库管理 / 账号管理）

未登录访问任何页面都会跳到 /login；非管理员访问 /admin 返回 403 页面。
接口层的鉴权由 app/api/deps.py 的依赖完成，这里只管页面。
"""
import contextlib
import logging
import os
import secrets
import threading

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from app import db
from app.api import register_routers
from app.api.deps import session_user

log = logging.getLogger(__name__)

# 需要登录才能看的页面 → (端点名, 路径, 标题, 模板)
PAGES = [
    ("index", "/", "首页", "index.html"),
    ("bank", "/bank", "科目章节题库", "bank.html"),
    ("practice", "/practice", "答题", "practice.html"),
    ("wrongbook", "/wrongbook", "错题本", "wrongbook.html"),
    ("stats", "/stats", "答题历史统计", "stats.html"),
    ("chat", "/chat", "AI 答疑", "chat.html"),
]

NAV_ITEMS = [
    ("index", "首页", "🏠", False),
    ("bank", "题库", "📚", False),
    ("practice", "答题", "✍️", False),
    ("wrongbook", "错题本", "❌", False),
    ("stats", "统计", "📊", False),
    ("chat", "AI 答疑", "🤖", False),
    ("admin", "管理后台", "🛠️", True),      # 最后一项：仅管理员可见
]

PAGE_PATHS = {name: path for name, path, _t, _tpl in PAGES}
PAGE_PATHS["admin"] = "/admin"
PAGE_PATHS["login"] = "/login"


def _secret_key() -> str:
    if config.SECRET_KEY:
        return config.SECRET_KEY
    key = secrets.token_urlsafe(32)
    log.warning("未设置 CPA_SECRET，本次运行使用随机会话密钥："
                "重启后所有人需要重新登录。多用户部署请设置该环境变量。")
    return key


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    """启动/关闭钩子：建上传目录、补跑中断的入库任务。"""
    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    from app.rag import uploads
    stuck = uploads.list_jobs(limit=50, status="processing")
    if stuck:
        log.info("发现 %d 个上次中断的入库任务，后台补跑", len(stuck))
        threading.Thread(target=uploads.process_pending, daemon=True).start()
    yield
    db.close_conn()

def create_app() -> FastAPI:
    app = FastAPI(title="CPA 题库与 AI 答疑智能体", version="2.0.0",
                  description="题库刷题 + RAG 教材答疑，账号隔离，管理员可上传教材。",
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json",
                  lifespan=_lifespan)

    app.add_middleware(SessionMiddleware, secret_key=_secret_key(),
                       session_cookie=config.SESSION_COOKIE,
                       max_age=config.SESSION_MAX_AGE,
                       same_site="lax", https_only=config.SESSION_HTTPS_ONLY)

    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")
    templates = Jinja2Templates(directory=config.TEMPLATE_DIR)
    # 模板里用 static_url()/page_url() 取地址，不依赖具体 Web 框架
    templates.env.globals["static_url"] = lambda p: "/static/" + str(p).lstrip("/")
    templates.env.globals["page_url"] = lambda name: PAGE_PATHS.get(name, "/")

    db.init_db()
    register_routers(app)

    def render(request: Request, template: str, title: str, endpoint: str,
               user=None, status_code: int = 200, **extra):
        ctx = {"page_title": title, "current_endpoint": endpoint,
               "nav_items": NAV_ITEMS, "user": user,
               "is_admin": bool(user and user["role"] == "admin"),
               "allow_register": config.ALLOW_SELF_REGISTER}
        ctx.update(extra)
        return templates.TemplateResponse(request, template, ctx,
                                          status_code=status_code)

    # ------------------------------------------------------------------ 登录
    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if session_user(request):
            return RedirectResponse("/", status_code=302)
        return render(request, "login.html", "登录", "login")

    @app.get("/logout", include_in_schema=False)
    def logout_page(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    # ------------------------------------------------------------------ 业务页面
    def _make_page(endpoint, title, template):
        def page(request: Request):
            user = session_user(request)
            if not user:
                return RedirectResponse("/login?next=" + request.url.path,
                                        status_code=302)
            return render(request, template, title, endpoint, user=user)
        page.__name__ = "page_" + endpoint
        return page

    for _name, _path, _title, _tpl in PAGES:
        app.get(_path, include_in_schema=False)(_make_page(_name, _title, _tpl))

    @app.get("/admin", include_in_schema=False)
    def admin_page(request: Request):
        user = session_user(request)
        if not user:
            return RedirectResponse("/login?next=/admin", status_code=302)
        if user["role"] != "admin":
            return render(request, "403.html", "无权访问", "admin", user=user,
                          status_code=403,
                          message="这个页面只有管理员能进。如需上传教材，请联系管理员。")
        return render(request, "admin.html", "管理后台", "admin", user=user)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return RedirectResponse("/static/favicon.svg", status_code=302)

    # ------------------------------------------------------------------ 兜底
    @app.exception_handler(404)
    def _404(request: Request, exc):
        # 注意：Starlette 的状态码处理器优先于类处理器，所以业务代码里
        # raise HTTPException(404, "题目不存在") 也会走到这里，要把原文带出去。
        detail = getattr(exc, "detail", None)
        if request.url.path.startswith("/api/"):
            from app.api import envelope
            return envelope(detail or ("接口不存在：%s" % request.url.path), 404)
        return render(request, "404.html", "页面不存在", "", status_code=404,
                      user=session_user(request),
                      message=detail if isinstance(detail, str) else None)

    @app.exception_handler(500)
    def _500(request: Request, exc):
        log.exception("服务端异常")
        if request.url.path.startswith("/api/"):
            from app.api import envelope
            return envelope("服务端异常，请查看控制台日志", 500)
        return render(request, "404.html", "服务端异常", "", status_code=500,
                      user=session_user(request),
                      message="服务端异常，请查看控制台日志")

    return app

