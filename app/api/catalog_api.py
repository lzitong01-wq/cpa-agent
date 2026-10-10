# -*- coding: utf-8 -*-
"""三级分类（科目 / 章节 / 知识点）与题目查询接口。

这些是"公共读"接口：登录后即可访问，不区分角色，也不涉及个人数据，
但仍然要求登录（CurrentUser），避免未登录就能爬走整个题库。
"""
from fastapi import HTTPException, Query

from app.api import ok, router
from app.api.deps import CurrentUser, as_list, clean_filters
from app.business import catalog, questions as q_svc


@router.get("/subjects")
def api_subjects(user: CurrentUser):
    return ok(catalog.list_subjects())


@router.get("/tree")
def api_tree(user: CurrentUser):
    """题库页左侧导航：科目 → 章节 → 知识点，带各级题量。"""
    return ok(catalog.full_tree())


@router.get("/chapters")
def api_chapters(user: CurrentUser, subject_id: int | None = None):
    return ok(catalog.list_chapters(subject_id=subject_id))


@router.get("/knowledge_points")
def api_knowledge_points(user: CurrentUser, chapter_id: int | None = None,
                         subject_id: int | None = None):
    return ok(catalog.list_knowledge_points(chapter_id=chapter_id,
                                            subject_id=subject_id))


@router.get("/resolve_scope")
def api_resolve_scope(user: CurrentUser, subject: str = "", chapter: str = "",
                      knowledge_point: str = ""):
    """把中文话术解析成三级 id，聊天框与搜索框共用。"""
    return ok(catalog.resolve_scope(subject=subject or None, chapter=chapter or None,
                                    knowledge_point=knowledge_point or None))


@router.get("/questions")
def api_questions(user: CurrentUser,
                  subject_id: int | None = None,
                  chapter_id: int | None = None,
                  knowledge_point_id: int | None = None,
                  qtypes: str = "",
                  difficulty: int | None = None,
                  limit: int = Query(default=20, ge=1, le=100),
                  offset: int = Query(default=0, ge=0)):
    """按三级分类 + 题型 + 难度分页查询题目（不返回答案与解析）。"""
    kw = clean_filters(subject_id=subject_id, chapter_id=chapter_id,
                       knowledge_point_id=knowledge_point_id,
                       qtypes=as_list(qtypes) or None, difficulty=difficulty)
    items = q_svc.list_questions(limit=limit, offset=offset, **kw)
    return ok({"items": items, "total": q_svc.count_questions(**kw),
               "limit": limit, "offset": offset,
               "type_distribution": q_svc.type_distribution(**kw)})


@router.get("/questions/{question_id}")
def api_question(question_id: int, user: CurrentUser):
    """做题页取单题；未提交前不含答案与解析。"""
    q = q_svc.get_question(question_id, with_answer=False)
    if not q:
        raise HTTPException(404, "题目不存在")
    return ok(q)
