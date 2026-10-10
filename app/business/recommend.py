# -*- coding: utf-8 -*-
"""个性化题目推送算法。

输入：用户答题历史（answer_records）、错题集合（wrong_questions）、
      各知识点正确率（视图 v_kp_accuracy）。
输出：带推荐理由的题目列表。

分配策略（杜绝纯随机）：把 limit 按四档配额切分，档内再按权重分到具体知识点，
最后在知识点内选题时优先未做过的题、其次做错过的题。
    ① 错题关联知识点        40%   权重 = 累计错次
    ② 薄弱知识点（正确率低） 35%   权重 = (1 - 正确率) × log(作答量+1)
    ③ 未接触知识点（探索）   15%   权重 = 1（冷启动时这一档自动放大）
    ④ 已掌握知识点巩固       10%   权重 = 正确率
配额不足时按 ①→②→③→④ 顺序回填，仍不足才从范围内补题。
"""
import math
import random
import uuid

from app import db
from app.business import questions as q_svc, records, wrongbook

QUOTA = (("wrong", 0.40), ("weak", 0.35), ("fresh", 0.15), ("mastered", 0.10))

WEAK_ACC_MAX = 0.7      # 正确率低于此值算薄弱
MASTERED_ACC_MIN = 0.8  # 正确率不低于此值且作答量达标算已掌握
MASTERED_MIN_TOTAL = 2

REASON_TEXT = {
    "wrong": "错题关联知识点",
    "weak": "薄弱知识点（正确率偏低）",
    "fresh": "尚未练习的知识点",
    "mastered": "已掌握知识点巩固",
    "fill": "范围内补充题目",
}


def _kp_pool(subject_id=None, chapter_id=None) -> list:
    """推荐范围内所有有题的知识点。"""
    sql = ("SELECT k.id, k.name, k.chapter_id, k.subject_id, COUNT(q.id) AS n "
           "FROM knowledge_points k JOIN questions q ON q.knowledge_point_id = k.id "
           "AND q.is_active = 1 WHERE 1 = 1")
    args = []
    if subject_id:
        sql += " AND k.subject_id = ?"
        args.append(subject_id)
    if chapter_id:
        sql += " AND k.chapter_id = ?"
        args.append(chapter_id)
    sql += " GROUP BY k.id HAVING n > 0"
    return db.query_all(sql, tuple(args))


def _kp_accuracy(user_id: str, subject_id=None, chapter_id=None) -> dict:
    sql = ("SELECT knowledge_point_id AS kp, total, correct, accuracy "
           "FROM v_kp_accuracy WHERE user_id = ?")
    args = [user_id]
    if subject_id:
        sql += " AND subject_id = ?"
        args.append(subject_id)
    if chapter_id:
        sql += " AND chapter_id = ?"
        args.append(chapter_id)
    return {r["kp"]: r for r in db.query_all(sql, tuple(args))}


def build_profile(user_id: str, subject_id=None, chapter_id=None) -> dict:
    """把三类输入汇总成画像，四档知识点权重一次算清（也供统计页展示）。"""
    pool = _kp_pool(subject_id, chapter_id)
    acc = _kp_accuracy(user_id, subject_id, chapter_id)
    wrong_w = wrongbook.wrong_kp_weights(user_id, subject_id=subject_id)

    buckets = {"wrong": {}, "weak": {}, "fresh": {}, "mastered": {}}
    for kp in pool:
        kid = kp["id"]
        st = acc.get(kid)
        if kid in wrong_w:
            buckets["wrong"][kid] = float(wrong_w[kid])
        if st is None or not st["total"]:
            buckets["fresh"][kid] = 1.0
            continue
        a = float(st["accuracy"] or 0.0)
        if a < WEAK_ACC_MAX:
            buckets["weak"][kid] = (1.0 - a) * math.log(st["total"] + 1.0) + 0.1
        elif a >= MASTERED_ACC_MIN and st["total"] >= MASTERED_MIN_TOTAL:
            buckets["mastered"][kid] = a
    return {
        "kp_total": len(pool),
        "buckets": buckets,
        "accuracy": acc,
        "wrong_weights": wrong_w,
        "answered_total": records.count_records(
            user_id, subject_id=subject_id, chapter_id=chapter_id),
    }


def _alloc(buckets: dict, limit: int) -> dict:
    """按配额比例分档；空档的配额顺延给非空档，保证总数不缩水。"""
    live = [(name, ratio) for name, ratio in QUOTA if buckets.get(name)]
    if not live:
        return {}
    total_ratio = sum(r for _, r in live)
    out, assigned = {}, 0
    for i, (name, ratio) in enumerate(live):
        if i == len(live) - 1:
            n = limit - assigned
        else:
            n = int(round(limit * ratio / total_ratio))
        n = max(0, min(n, limit - assigned))
        out[name] = n
        assigned += n
    return out


def _weighted_pick(weights: dict, n: int, rnd: random.Random) -> list:
    """按权重不重复抽取知识点：权重高的更容易被选中，但不退化成纯随机。"""
    items = [(k, max(w, 1e-6)) for k, w in weights.items()]
    picked = []
    for _ in range(min(n, len(items))):
        total = sum(w for _, w in items)
        r = rnd.random() * total
        upto = 0.0
        for idx, (k, w) in enumerate(items):
            upto += w
            if r <= upto:
                picked.append(k)
                items.pop(idx)
                break
    return picked


def _kp_questions(kp_id: int, subject_id=None, chapter_id=None) -> list:
    return q_svc.list_questions(knowledge_point_id=kp_id, subject_id=subject_id,
                                chapter_id=chapter_id, limit=100)


def _order_candidates(cands: list, answered: set, wrong_ids: set, bucket: str) -> list:
    """知识点内选题优先级：没做过 > 做错过 > 做对过；错题档反过来优先做错过的。"""
    def key(q):
        qid = q["id"]
        if bucket == "wrong":
            tier = 0 if qid in wrong_ids else (1 if qid not in answered else 2)
        else:
            tier = 0 if qid not in answered else (1 if qid in wrong_ids else 2)
        # 巩固档挑难题，其余档从易到难
        diff = -q["difficulty"] if bucket == "mastered" else q["difficulty"]
        return (tier, diff, qid)
    return sorted(cands, key=key)


def recommend(user_id: str, limit: int = 10, mode: str = "recommend", subject_id=None,
              chapter_id=None, knowledge_point_id=None, seed=None) -> dict:
    """统一取题入口。mode: recommend 智能推送 / chapter 专项章节 / mock 模拟 / wrong 错题。"""
    limit = max(1, int(limit))
    rnd = random.Random(seed)

    if mode == "wrong":
        items = wrongbook.redo_set(user_id, limit=limit, subject_id=subject_id,
                                   chapter_id=chapter_id,
                                   knowledge_point_id=knowledge_point_id)
        for it in items:
            it["reason"] = "错题重做"
        return {"mode": mode, "count": len(items), "questions": items,
                "profile": None, "allocation": {"wrong": len(items)}}

    if mode == "mock":
        from app.business import paper
        p = paper.build_mock(subject_id=subject_id, limit=limit, seed=seed)
        for it in p["questions"]:
            it.setdefault("reason", "模拟卷按题型配比抽取")
        return {"mode": mode, "count": len(p["questions"]), "questions": p["questions"],
                "profile": None, "allocation": p.get("type_distribution", {}),
                "paper_id": p.get("paper_id")}

    if knowledge_point_id:  # 指定知识点：直接在该知识点内按优先级出题
        answered = records.answered_ids(user_id, subject_id=subject_id)
        wrong_ids = set(wrongbook.wrong_question_ids(user_id, status="all"))
        cands = _order_candidates(_kp_questions(knowledge_point_id), answered,
                                  wrong_ids, "weak")[:limit]
        for it in cands:
            it["reason"] = "指定知识点练习"
        return {"mode": mode, "count": len(cands), "questions": cands,
                "profile": None, "allocation": {"weak": len(cands)}}

    profile = build_profile(user_id, subject_id, chapter_id)
    buckets = profile["buckets"]
    alloc = _alloc(buckets, limit)

    answered = records.answered_ids(user_id, subject_id=subject_id)
    wrong_ids = set(wrongbook.wrong_question_ids(user_id, status="all"))
    chosen, used = [], set()

    for bucket, _ratio in QUOTA:
        need = alloc.get(bucket, 0)
        if need <= 0:
            continue
        weights = buckets.get(bucket) or {}
        # 知识点数可能少于配额，循环补齐
        kp_order = _weighted_pick(weights, len(weights), rnd)
        idx = 0
        while need > 0 and kp_order:
            kp_id = kp_order[idx % len(kp_order)]
            idx += 1
            if idx > len(kp_order) * 4:
                break
            cands = [q for q in _order_candidates(
                _kp_questions(kp_id, subject_id, chapter_id), answered, wrong_ids, bucket)
                if q["id"] not in used]
            if not cands:
                continue
            pick = cands[0]
            pick["reason"] = REASON_TEXT[bucket]
            pick["reason_bucket"] = bucket
            chosen.append(pick)
            used.add(pick["id"])
            need -= 1

    if len(chosen) < limit:  # 回填：范围内还没做过的题优先
        fill = q_svc.list_questions(subject_id=subject_id, chapter_id=chapter_id,
                                    exclude_ids=list(used) or None, limit=limit * 3)
        fill = _order_candidates(fill, answered, wrong_ids, "fresh")
        for q in fill:
            if len(chosen) >= limit:
                break
            if q["id"] in used:
                continue
            q["reason"] = REASON_TEXT["fill"]
            q["reason_bucket"] = "fill"
            chosen.append(q)
            used.add(q["id"])

    return {
        "mode": mode,
        "count": len(chosen),
        "questions": chosen[:limit],
        "allocation": alloc,
        "profile": {
            "answered_total": profile["answered_total"],
            "kp_total": profile["kp_total"],
            "wrong_kp": len(buckets["wrong"]),
            "weak_kp": len(buckets["weak"]),
            "fresh_kp": len(buckets["fresh"]),
            "mastered_kp": len(buckets["mastered"]),
        },
    }


def new_paper_id() -> str:
    return uuid.uuid4().hex[:12]
