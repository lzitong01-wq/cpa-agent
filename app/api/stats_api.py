# -*- coding: utf-8 -*-
"""答题历史统计接口：总览、按科目/章节/知识点/题型、每日趋势、模式分布。

统计口径始终是"当前登录用户"，无法通过参数查看他人数据。
"""
from fastapi import Query

from app.api import ok, router
from app.api.deps import CurrentUser
from app.business import stats


@router.get("/stats/dashboard")
def api_stats_dashboard(user: CurrentUser, subject_id: int | None = None):
    """统计页一次拉全，避免前端串行发多个请求。"""
    return ok(stats.dashboard(user["id"], subject_id=subject_id))


@router.get("/stats/overview")
def api_stats_overview(user: CurrentUser, subject_id: int | None = None):
    return ok(stats.overview(user["id"], subject_id=subject_id))


@router.get("/stats/by_subject")
def api_stats_by_subject(user: CurrentUser):
    return ok(stats.by_subject(user["id"]))


@router.get("/stats/by_chapter")
def api_stats_by_chapter(user: CurrentUser, subject_id: int | None = None):
    return ok(stats.by_chapter(user["id"], subject_id=subject_id))


@router.get("/stats/by_knowledge_point")
def api_stats_by_kp(user: CurrentUser, subject_id: int | None = None,
                    weakest_first: bool = True,
                    limit: int = Query(default=50, ge=1, le=200)):
    return ok(stats.by_knowledge_point(user["id"], subject_id=subject_id,
                                       limit=limit, weakest_first=weakest_first))


@router.get("/stats/by_qtype")
def api_stats_by_qtype(user: CurrentUser, subject_id: int | None = None):
    return ok(stats.by_qtype(user["id"], subject_id=subject_id))


@router.get("/stats/daily_trend")
def api_stats_daily_trend(user: CurrentUser, subject_id: int | None = None,
                          days: int = Query(default=14, ge=1, le=90)):
    return ok(stats.daily_trend(user["id"], days=days, subject_id=subject_id))
