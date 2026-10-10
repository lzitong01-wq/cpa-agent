# -*- coding: utf-8 -*-
"""错题本接口：查看、按科目/知识点筛选、重做取题、移除、恢复、汇总。"""
from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.api import ok, router
from app.api.deps import CurrentUser, clean_filters
from app.business import wrongbook


class RedoIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    subject_id: int | None = None
    chapter_id: int | None = None
    knowledge_point_id: int | None = None
    status: str | None = None


class QuestionIdIn(BaseModel):
    question_id: int = Field(ge=1)


@router.get("/wrongbook")
def api_wrongbook(user: CurrentUser,
                  subject_id: int | None = None,
                  chapter_id: int | None = None,
                  knowledge_point_id: int | None = None,
                  status: str = "",
                  with_answer: bool = False,
                  limit: int = Query(default=20, ge=1, le=200),
                  offset: int = Query(default=0, ge=0)):
    kw = clean_filters(subject_id=subject_id, chapter_id=chapter_id,
                       knowledge_point_id=knowledge_point_id, status=status or None)
    return ok({"items": wrongbook.list_wrong(user["id"], limit=limit, offset=offset,
                                             with_answer=with_answer, **kw),
               "total": wrongbook.count_wrong(user["id"], **kw),
               "limit": limit, "offset": offset})


@router.get("/wrongbook/summary")
def api_wrongbook_summary(user: CurrentUser):
    """错题本概览：总数、按科目分布、错得最多的知识点。"""
    return ok(wrongbook.summary(user["id"]))


@router.post("/wrongbook/redo")
def api_wrongbook_redo(body: RedoIn, user: CurrentUser):
    """错题重做取题：按累计错次优先，返回的题目不含答案。"""
    kw = clean_filters(subject_id=body.subject_id, chapter_id=body.chapter_id,
                       knowledge_point_id=body.knowledge_point_id,
                       status=body.status)
    items = wrongbook.redo_set(user["id"], limit=body.limit, **kw)
    return ok({"count": len(items), "questions": items, "mode": "wrong"})


@router.post("/wrongbook/remove")
def api_wrongbook_remove(body: QuestionIdIn, user: CurrentUser):
    """从错题本移除（软删除，保留答题记录与历史错次）。"""
    if not wrongbook.remove_wrong(user["id"], body.question_id):
        raise HTTPException(404, "错题本中没有这道题")
    return ok({"question_id": body.question_id, "status": "removed"})


@router.post("/wrongbook/restore")
def api_wrongbook_restore(body: QuestionIdIn, user: CurrentUser):
    if not wrongbook.restore_wrong(user["id"], body.question_id):
        raise HTTPException(404, "错题本中没有这道题")
    return ok({"question_id": body.question_id, "status": "open"})
