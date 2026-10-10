# -*- coding: utf-8 -*-
"""判分引擎：覆盖单选 / 多选 / 判断 / 简答 / 综合五种题型。

客观题（单选、多选、判断）精确判分；
主观题（简答、综合）用「要点命中率」给部分分，命中率达阈值算正确，
并保留 llm_grader 钩子，接入大模型后可换成语义评分（见 docs/LLM_INTEGRATION.md）。
"""
import json
import re

OBJECTIVE = ("single", "multiple", "judge")
SUBJECTIVE = ("short", "comprehensive")

# 主观题命中率达到该比例即判为正确
SUBJECTIVE_PASS_RATIO = 0.6
# 单个要点命中所需的关键词重合比例
POINT_HIT_RATIO = 0.5

_TRUE_WORDS = {"true", "t", "1", "对", "正确", "是", "y", "yes", "a", "√"}
_FALSE_WORDS = {"false", "f", "0", "错", "错误", "否", "n", "no", "b", "×"}
_PUNCT = re.compile(r"[\s,，、;；.。/|]+")


def _norm_keys(value) -> list:
    """把 'A,C' / ['a','c'] / 'AC' 统一成 ['A','C']。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        s = str(value).strip()
        parts = [p for p in _PUNCT.split(s) if p]
        items = parts if len(parts) > 1 else list(s)
    out = []
    for it in items:
        k = str(it).strip().upper()
        if k and k not in out:
            out.append(k)
    return sorted(out)


def _norm_bool(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None


def _tokens(text: str) -> set:
    try:
        import jieba
        return {w for w in jieba.cut(str(text)) if len(w.strip()) > 1}
    except Exception:
        return {w for w in _PUNCT.split(str(text)) if len(w.strip()) > 1}


def _load(value):
    if isinstance(value, (dict, list, bool, int, float)) or value is None:
        return value
    try:
        return json.loads(value)
    except Exception:
        return value


def grade(question: dict, user_answer, llm_grader=None) -> dict:
    """返回 {is_correct, score, earned_ratio, detail, correct_answer}。"""
    qtype = question["qtype"]
    correct = _load(question.get("answer_json"))
    full_score = float(question.get("score") or 2.0)

    if qtype == "single":
        got = _norm_keys(user_answer)
        want = _norm_keys(correct)
        ok = got == want
        return _pack(ok, full_score if ok else 0.0, full_score, want,
                     "选择 %s，正确答案 %s" % ("".join(got) or "未作答", "".join(want)))

    if qtype == "multiple":
        got, want = _norm_keys(user_answer), _norm_keys(correct)
        ok = got == want
        # 多选题少选给一半分（无错选且至少选中一项），多选或错选不得分
        partial = 0.0
        if not ok and got and set(got).issubset(set(want)):
            partial = round(full_score * 0.5, 2)
        return _pack(ok, full_score if ok else partial, full_score, want,
                     "选择 %s，正确答案 %s%s" % ("".join(got) or "未作答", "".join(want),
                                            "（少选得一半分）" if partial else ""))

    if qtype == "judge":
        got, want = _norm_bool(user_answer), _norm_bool(correct)
        ok = got is not None and got == want
        return _pack(ok, full_score if ok else 0.0, full_score, want,
                     "作答 %s，正确答案 %s" % (_zh(got), _zh(want)))

    if qtype in SUBJECTIVE:
        return _grade_subjective(question, user_answer, correct, full_score, llm_grader)

    return _pack(False, 0.0, full_score, correct, "未知题型：%s" % qtype)


def _grade_subjective(question, user_answer, correct, full_score, llm_grader) -> dict:
    points = []
    if isinstance(correct, dict):
        points = list(correct.get("points") or [])
    if not points:
        points = [str(correct)]

    if llm_grader is not None:  # 【需要二次开发】接入大模型语义评分
        try:
            r = llm_grader(question, user_answer, points)
            if isinstance(r, dict) and "earned_ratio" in r:
                ratio = max(0.0, min(1.0, float(r["earned_ratio"])))
                ok = ratio >= SUBJECTIVE_PASS_RATIO
                return _pack(ok, round(full_score * ratio, 2), full_score, correct,
                             r.get("detail") or "大模型评分", hit_points=r.get("hits"))
        except Exception:
            pass  # 评分失败静默回退到要点命中法

    user_tokens = _tokens(user_answer or "")
    hits, missed = [], []
    for p in points:
        pt = _tokens(p)
        if pt and len(pt & user_tokens) / len(pt) >= POINT_HIT_RATIO:
            hits.append(p)
        else:
            missed.append(p)
    ratio = len(hits) / len(points) if points else 0.0
    ok = ratio >= SUBJECTIVE_PASS_RATIO
    detail = "命中 %d/%d 个要点" % (len(hits), len(points))
    if missed:
        detail += "；未覆盖：" + "；".join(missed[:3])
    return _pack(ok, round(full_score * ratio, 2), full_score, correct, detail,
                 hit_points=hits, missed_points=missed)


def _pack(ok, score, full_score, correct, detail, hit_points=None, missed_points=None) -> dict:
    return {
        "is_correct": bool(ok),
        "score": round(float(score), 2),
        "full_score": full_score,
        "earned_ratio": round(float(score) / full_score, 4) if full_score else 0.0,
        "correct_answer": correct,
        "detail": detail,
        "hit_points": hit_points or [],
        "missed_points": missed_points or [],
    }


def _zh(v):
    return {True: "正确", False: "错误"}.get(v, "未作答")
