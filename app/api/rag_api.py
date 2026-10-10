# -*- coding: utf-8 -*-
"""RAG 独立服务接口（HTTP 形态）。

把这几个路由单独部署即可成为独立的检索微服务；
Agent 层调用的是同一个 app.rag.service，换成远程调用只需改 service 的实现。

与 Flask 版的两处安全收紧：
1. 全部要求登录（检索会消耗嵌入模型算力，不能匿名压）；
2. 原来的 /rag/reindex 允许调用方指定 corpus_dir（等于让服务端读任意目录），
   已移到 /api/admin/corpus/reindex 且只扫固定目录、仅管理员可用。
"""
from fastapi import Query
from pydantic import BaseModel, Field

from app.api import ok, router
from app.api.deps import CurrentUser
from app.rag import service as rag


class SearchIn(BaseModel):
    query: str = Field(default="", max_length=1000)
    q: str = Field(default="", max_length=1000)          # 兼容别名
    subject: str | int | None = None
    top_k: int | None = Field(default=None, ge=1, le=20)
    max_chars: int = Field(default=3200, ge=500, le=12000)

    def text(self) -> str:
        return (self.query or self.q or "").strip()


@router.post("/rag/search")
def api_rag_search(body: SearchIn, user: CurrentUser):
    """检索教材片段。命中不足时 refused=True，并给出拒答话术。"""
    text = body.text()
    if not text:
        raise ValueError("query 不能为空")
    res = rag.search(text, subject=body.subject, top_k=body.top_k,
                     user_id=user["id"])
    res.pop("index", None)
    return ok(res)


@router.post("/rag/context")
def api_rag_context(body: SearchIn, user: CurrentUser):
    """检索 + 拼上下文 + 结构化引用，Agent 直接可用的形态。"""
    text = body.text()
    if not text:
        raise ValueError("query 不能为空")
    res = rag.retrieve_context(text, subject=body.subject, top_k=body.top_k,
                               user_id=user["id"], max_chars=body.max_chars)
    res.pop("hits", None)
    return ok(res)


@router.get("/rag/health")
def api_rag_health(user: CurrentUser):
    return ok(rag.health())


@router.get("/rag/docs")
def api_rag_docs(user: CurrentUser):
    """学员可见的教材清单（只列参与检索的，不含管理员下架的）。"""
    return ok(rag.list_docs())
