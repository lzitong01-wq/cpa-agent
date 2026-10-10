# -*- coding: utf-8 -*-
"""AI 答疑智能体聊天接口。

一个 /api/chat 端点承载三类意图（刷题 / 知识点答疑 / 针对某道题提问），
意图识别、RAG 检索、拒答判定、对话落库全部在 Agent 层完成，本文件只做转发。
对话历史按 (user_id, session_id) 隔离，换账号看不到别人的对话。
"""
from fastapi import Query
from pydantic import BaseModel, Field

from app.agent import orchestrator
from app.api import ok, router
from app.api.deps import CurrentUser


class ChatIn(BaseModel):
    message: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=2000)      # 兼容别名
    session_id: str | None = Field(default=None, max_length=64)
    current_question_id: int | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)


class SessionIn(BaseModel):
    session_id: str | None = Field(default=None, max_length=64)


@router.post("/chat")
def api_chat(body: ChatIn, user: CurrentUser):
    message = (body.message or body.text or "").strip()
    if not message:
        raise ValueError("message 不能为空")
    return ok(orchestrator.chat(
        message, user_id=user["id"], session_id=body.session_id or None,
        current_question_id=body.current_question_id, top_k=body.top_k))


@router.get("/chat/history")
def api_chat_history(user: CurrentUser, session_id: str = "",
                     limit: int = Query(default=50, ge=1, le=500)):
    return ok(orchestrator.history(user["id"], session_id=session_id or None,
                                   limit=limit))


@router.post("/chat/clear")
def api_chat_clear(body: SessionIn, user: CurrentUser):
    return ok({"deleted": orchestrator.clear_history(user["id"],
                                                     session_id=body.session_id)})


@router.get("/chat/session")
def api_chat_session(user: CurrentUser):
    """前端首次打开聊天框时取一个会话号。"""
    return ok({"session_id": orchestrator.new_session_id()})


@router.get("/chat/status")
def api_chat_status(user: CurrentUser):
    """展示当前是"大模型模式"还是"纯检索模式"，以及知识库是否就绪。"""
    return ok(orchestrator.status())
