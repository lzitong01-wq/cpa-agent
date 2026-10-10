# -*- coding: utf-8 -*-
"""HTTP API 层（FastAPI）：参数校验 + 鉴权 + 调用业务/Agent 层 + 序列化。

约定不变（前端零改动）：统一响应体 {"ok": bool, "data": ..., "error": str}。
- 成功：各路由返回 ok(data)；
- 失败：抛 HTTPException 或业务层的 ValueError，由这里的异常处理器统一成同样的外壳。

与 Flask 版的关键差别：
1. 用户来自**服务端会话**（app/api/deps.py），不再读请求里的 user_id；
   请求体里带 user_id 也会被 Pydantic 忽略，伪造别人身份不再可能；
2. 参数校验交给 Pydantic / Query，类型不对直接 422，不用手写 as_int；
3. 路由函数是普通 `def`（不是 async def）——业务层是同步 SQLite + torch，
   FastAPI 会把它们丢进线程池执行，既不阻塞事件循环，
   也正好配合 db.py 的 threading.local 连接。文件上传那一个例外，见 admin_api。
"""
import logging

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")


def ok(data=None, **extra) -> dict:
    body = {"ok": True, "data": data}
    body.update(extra)
    return body


def envelope(error: str, code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(error)}, status_code=code)


def register_routers(app: FastAPI) -> FastAPI:
    """挂载所有子路由并注册统一异常处理器。"""
    from app.api import (admin_api, auth_api, catalog_api, chat_api,  # noqa: F401
                         practice_api, rag_api, stats_api, wrongbook_api)

    app.include_router(router)

    @app.exception_handler(HTTPException)
    def _http_error(request: Request, exc: HTTPException):
        # 页面请求遇到 401/403 时跳登录页，而不是丢一段 JSON 给浏览器
        if not request.url.path.startswith("/api/") and exc.status_code in (401, 403):
            return RedirectResponse("/login?next=" + request.url.path, status_code=302)
        return envelope(exc.detail, exc.status_code)

    @app.exception_handler(RequestValidationError)
    def _validation_error(request: Request, exc: RequestValidationError):
        first = (exc.errors() or [{}])[0]
        field = ".".join(str(x) for x in (first.get("loc") or [])[1:]) or "参数"
        return envelope("参数不合法：%s %s" % (field, first.get("msg", "")), 422)

    @app.exception_handler(ValueError)
    def _value_error(request: Request, exc: ValueError):
        # 业务层用 ValueError 表达"用户输入有问题"（如密码太短、题目不存在）。
        # 仍然记 warning，避免把真正的 bug 静默成 400。
        log.warning("业务校验失败 %s：%s", request.url.path, exc)
        return envelope(exc, 400)

    return app
