# -*- coding: utf-8 -*-
"""组卷：按科目 / 章节 / 知识点筛选生成练习试卷，以及模拟卷按题型配比抽题。"""
import random
import uuid

from app.business import questions as q_svc

# 模拟卷题型配比（可按科目实际卷面调整）
MOCK_MIX = (("single", 0.40), ("multiple", 0.24), ("judge", 0.16),
            ("short", 0.12), ("comprehensive", 0.08))


def new_paper_id() -> str:
    return uuid.uuid4().hex[:12]


def build_paper(subject_id=None, chapter_id=None, knowledge_point_ids=None,
                qtypes=None, difficulty=None, limit=20, shuffle=True, seed=None) -> dict:
    """自由组卷：三级分类任意层级筛选 + 题型/难度过滤。"""
    kw = {
        "subject_id": subject_id,
        "chapter_id": chapter_id,
        "knowledge_point_id": list(knowledge_point_ids) if knowledge_point_ids else None,
        "qtypes": qtypes,
        "difficulty": difficulty,
    }
    kw = {k: v for k, v in kw.items() if v}
    total_available = q_svc.count_questions(**kw)
    if shuffle:
        items = q_svc.random_questions(limit=limit, **kw)
    else:
        items = q_svc.list_questions(limit=limit, order="q.qtype, q.difficulty, q.id", **kw)
    if seed is not None:
        random.Random(seed).shuffle(items)
    return {
        "paper_id": new_paper_id(),
        "questions": items,
        "count": len(items),
        "available": total_available,
        "total_score": round(sum(q["score"] for q in items), 2),
        "type_distribution": _dist(items),
        "filters": {"subject_id": subject_id, "chapter_id": chapter_id,
                    "knowledge_point_ids": list(knowledge_point_ids or []),
                    "qtypes": list(qtypes or []), "difficulty": difficulty},
    }


def build_mock(subject_id=None, limit=20, seed=None) -> dict:
    """模拟刷题：按题型配比抽题，不足的题型用其他题型补齐。"""
    rnd = random.Random(seed)
    picked, used = [], set()
    for qtype, ratio in MOCK_MIX:
        need = int(round(limit * ratio))
        if need <= 0:
            continue
        cands = q_svc.random_questions(limit=need * 3, subject_id=subject_id, qtypes=[qtype])
        rnd.shuffle(cands)
        for q in cands:
            if need <= 0:
                break
            if q["id"] in used:
                continue
            q["reason"] = "模拟卷 · %s" % q["qtype_label"]
            picked.append(q)
            used.add(q["id"])
            need -= 1

    if len(picked) < limit:  # 配比抽不满时补齐总量
        extra = q_svc.random_questions(limit=(limit - len(picked)) * 3,
                                       subject_id=subject_id,
                                       exclude_ids=list(used) or None)
        for q in extra:
            if len(picked) >= limit:
                break
            if q["id"] in used:
                continue
            q["reason"] = "模拟卷 · 补充"
            picked.append(q)
            used.add(q["id"])

    order = {t: i for i, (t, _) in enumerate(MOCK_MIX)}
    picked.sort(key=lambda x: (order.get(x["qtype"], 9), x["difficulty"]))
    return {
        "paper_id": new_paper_id(),
        "questions": picked[:limit],
        "count": len(picked[:limit]),
        "total_score": round(sum(q["score"] for q in picked[:limit]), 2),
        "type_distribution": _dist(picked[:limit]),
    }


def _dist(items) -> dict:
    out = {}
    for q in items:
        out[q["qtype"]] = out.get(q["qtype"], 0) + 1
    return out
