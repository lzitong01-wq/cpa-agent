# -*- coding: utf-8 -*-
"""做题相关接口：取题（推送/章节/模拟/错题）、提交判分、组卷、答题历史。

所有接口的 user_id 都来自会话（CurrentUser），请求体里带 user_id 会被忽略，
因此拿不到、也改不了别人的数据。
"""
from typing import Any, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, Field

from app.agent import grader
from app.api import ok, router
from app.api.deps import CurrentUser, as_list, clean_filters
from app.business import paper, questions as q_svc, records, recommend


class NextIn(BaseModel):
    mode: Literal["recommend", "chapter", "mock", "wrong"] = "recommend"
    limit: int = Field(default=10, ge=1, le=50)
    subject_id: int | None = None
    chapter_id: int | None = None
    knowledge_point_id: int | None = None
    seed: int | None = None


class SubmitIn(BaseModel):
    question_id: int = Field(ge=1)
    answer: Any = None
    user_answer: Any = None            # 兼容旧字段名
    qtype: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    mode: str = "chapter"
    paper_id: str | None = None


class BatchItem(BaseModel):
    question_id: int = Field(ge=1)
    answer: Any = None
    duration_ms: int = Field(default=0, ge=0)


class SubmitBatchIn(BaseModel):
    items: list[BatchItem] = Field(min_length=1, max_length=200)
    mode: str = "paper"
    paper_id: str | None = None


class PaperIn(BaseModel):
    subject_id: int | None = None
    chapter_id: int | None = None
    knowledge_point_ids: Any = None
    qtypes: Any = None
    difficulty: int | None = None
    limit: int = Field(default=20, ge=1, le=100)
    shuffle: bool = True
    seed: int | None = None


@router.post("/practice/next")
def api_practice_next(body: NextIn, user: CurrentUser):
    """统一取题入口。mode: recommend / chapter / mock / wrong。

    推送算法基于答题历史、错题集合与各知识点正确率四桶配额抽题，不是随机出题，
    每道题都会带上 reason（选题依据），前端直接展示给用户。
    """
    return ok(recommend.recommend(
        user["id"], limit=body.limit, mode=body.mode, subject_id=body.subject_id,
        chapter_id=body.chapter_id, knowledge_point_id=body.knowledge_point_id,
        seed=body.seed))


@router.get("/practice/profile")
def api_practice_profile(user: CurrentUser, subject_id: int | None = None,
                         chapter_id: int | None = None):
    """当前用户的知识点画像：错题/薄弱/未练/已掌握四桶，推送算法的输入。"""
    prof = recommend.build_profile(user["id"], subject_id=subject_id,
                                   chapter_id=chapter_id)
    buckets = prof.pop("buckets", {})
    prof["bucket_sizes"] = {k: len(v) for k, v in buckets.items()}
    prof["quota"] = {k: v for k, v in recommend.QUOTA}
    return ok(prof)


@router.post("/practice/submit")
def api_practice_submit(body: SubmitIn, user: CurrentUser):
    """提交单题作答：即时判分 + 返回标准解析 + 落答题记录 + 维护错题本。"""
    if not q_svc.get_question(body.question_id):
        raise HTTPException(404, "题目不存在")
    answer = body.answer if body.answer is not None else body.user_answer
    if isinstance(answer, str) and "," in answer and body.qtype == "multiple":
        answer = as_list(answer)
    return ok(records.submit_answer(
        user["id"], body.question_id, answer, duration_ms=body.duration_ms,
        mode=body.mode, paper_id=body.paper_id,
        llm_grader=grader.grader_or_none()))


@router.post("/practice/submit_batch")
def api_practice_submit_batch(body: SubmitBatchIn, user: CurrentUser):
    """整卷提交：items = [{question_id, answer, duration_ms}]。"""
    items = [it.model_dump() for it in body.items]
    return ok(records.submit_batch(user["id"], items, mode=body.mode,
                                   paper_id=body.paper_id))


@router.post("/paper/build")
def api_paper_build(body: PaperIn, user: CurrentUser):
    """组卷：按科目/章节/知识点/题型/难度筛选生成练习试卷。"""
    kp_ids = [int(x) for x in as_list(body.knowledge_point_ids) if str(x).isdigit()]
    return ok(paper.build_paper(
        subject_id=body.subject_id, chapter_id=body.chapter_id,
        knowledge_point_ids=kp_ids or None,
        qtypes=as_list(body.qtypes) or None, difficulty=body.difficulty,
        limit=body.limit, shuffle=body.shuffle, seed=body.seed))


@router.get("/records")
def api_records(user: CurrentUser,
                subject_id: int | None = None,
                chapter_id: int | None = None,
                knowledge_point_id: int | None = None,
                mode: str = "",
                only_wrong: bool = False,
                limit: int = Query(default=20, ge=1, le=200),
                offset: int = Query(default=0, ge=0)):
    """答题历史分页（只可能看到自己的记录）。"""
    kw = clean_filters(subject_id=subject_id, chapter_id=chapter_id,
                       knowledge_point_id=knowledge_point_id, mode=mode or None)
    if only_wrong:
        kw["only_wrong"] = True
    return ok({"items": records.list_records(user["id"], limit=limit, offset=offset, **kw),
               "total": records.count_records(user["id"], **kw),
               "limit": limit, "offset": offset})
