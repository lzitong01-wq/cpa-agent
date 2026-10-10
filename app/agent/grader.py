# -*- coding: utf-8 -*-
"""主观题（简答/综合）大模型语义评分适配器。

business.grading.grade(question, user_answer, llm_grader=...) 会以
llm_grader(question, user_answer, points) 的形式回调本模块的 llm_grader。
未配置 api_key 时返回 None，grading 自动回退到"关键要点命中法"，功能不中断。
"""
import logging

from app.agent import llm as llm_mod, prompts

log = logging.getLogger(__name__)


def available() -> bool:
    return llm_mod.get_client("grading").available()


def llm_grader(question: dict, user_answer, points: list):
    """返回 {'earned_ratio': float, 'hits': [...], 'detail': str} 或 None。"""
    client = llm_mod.get_client("grading")
    if not client.available():
        return None
    standard = "\n".join("%d. %s" % (i, p) for i, p in enumerate(points, 1))
    user_msg = prompts.render(prompts.GRADER_USER_TEMPLATE,
                              stem=(question.get("stem") or "")[:1500],
                              answer=standard,
                              user_answer=str(user_answer or "")[:2000])
    try:
        data = client.chat_json(prompts.GRADER_SYSTEM_PROMPT, user_msg)
    except llm_mod.LLMError as exc:
        log.warning("主观题大模型评分失败，回退要点命中法：%s", exc)
        return None
    if not isinstance(data, dict) or "earned_ratio" not in data:
        return None
    try:
        ratio = max(0.0, min(1.0, float(data["earned_ratio"])))
    except (TypeError, ValueError):
        return None
    hits = data.get("hit_points") or []
    detail = data.get("comment") or "大模型语义评分"
    missed = data.get("missed_points") or []
    if missed:
        detail += "；未覆盖：" + "；".join(str(m) for m in missed[:3])
    return {"earned_ratio": ratio, "hits": hits, "detail": detail}


def grader_or_none():
    """给 records.submit_answer 用：可用就给回调，不可用就给 None。"""
    return llm_grader if available() else None
