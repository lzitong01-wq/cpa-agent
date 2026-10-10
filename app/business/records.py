# -*- coding: utf-8 -*-
"""答题记录：提交判分 → 持久化 → 联动错题本。

submit_answer 是全站唯一的答题写入口，题库页、错题重做、Agent 对话内答题
都走这里，保证记录与错题本口径一致。
"""
from app import db
from app.business import grading, questions as q_svc, wrongbook


def submit_answer(user_id: str, question_id: int, user_answer, duration_ms: int = 0,
                  mode: str = "chapter", paper_id: str = None, llm_grader=None) -> dict:
    """判分 + 落库 + 更新错题本，返回即时反馈（含标准解析）。"""
    raw = q_svc.get_question_raw(question_id)
    if raw is None:
        raise ValueError("题目不存在：%s" % question_id)

    result = grading.grade(raw, user_answer, llm_grader=llm_grader)

    import json as _json
    with db.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO answer_records (user_id, question_id, subject_id, chapter_id, "
            "knowledge_point_id, user_answer_json, is_correct, score, duration_ms, mode, paper_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (user_id, question_id, raw["subject_id"], raw["chapter_id"],
             raw["knowledge_point_id"], _json.dumps(user_answer, ensure_ascii=False),
             1 if result["is_correct"] else 0, result["score"], int(duration_ms or 0),
             mode, paper_id))
        record_id = cur.lastrowid
        if result["is_correct"]:
            wrongbook.mark_right(conn, user_id, question_id)
        else:
            wrongbook.mark_wrong(conn, user_id, raw)

    return {
        "record_id": record_id,
        "question_id": question_id,
        "is_correct": result["is_correct"],
        "score": result["score"],
        "full_score": result["full_score"],
        "earned_ratio": result["earned_ratio"],
        "correct_answer": result["correct_answer"],
        "analysis": raw["analysis"],
        "detail": result["detail"],
        "hit_points": result["hit_points"],
        "missed_points": result["missed_points"],
        "knowledge_point": raw.get("knowledge_point_name"),
        "chapter": raw.get("chapter_name"),
        "subject": raw.get("subject_name"),
        "added_to_wrongbook": not result["is_correct"],
    }


def submit_batch(user_id: str, items: list, mode="paper", paper_id=None) -> dict:
    """整卷提交：items = [{question_id, answer, duration_ms}]。"""
    details, total, earned = [], 0.0, 0.0
    for it in items:
        r = submit_answer(user_id, int(it["question_id"]), it.get("answer"),
                          it.get("duration_ms", 0), mode=mode, paper_id=paper_id)
        details.append(r)
        total += r["full_score"]
        earned += r["score"]
    correct = sum(1 for d in details if d["is_correct"])
    return {
        "paper_id": paper_id, "count": len(details), "correct": correct,
        "accuracy": round(correct / len(details), 4) if details else 0.0,
        "score": round(earned, 2), "full_score": round(total, 2),
        "details": details,
    }


def _where(user_id, subject_id=None, chapter_id=None, knowledge_point_id=None,
           only_wrong=False, mode=None, paper_id=None):
    where = ["r.user_id = ?"]
    args = [user_id]
    if subject_id:
        where.append("r.subject_id = ?")
        args.append(subject_id)
    if chapter_id:
        where.append("r.chapter_id = ?")
        args.append(chapter_id)
    if knowledge_point_id:
        where.append("r.knowledge_point_id = ?")
        args.append(knowledge_point_id)
    if only_wrong:
        where.append("r.is_correct = 0")
    if mode:
        where.append("r.mode = ?")
        args.append(mode)
    if paper_id:
        where.append("r.paper_id = ?")
        args.append(paper_id)
    return " WHERE " + " AND ".join(where), args


def list_records(user_id: str, limit=20, offset=0, **kw) -> list:
    clause, args = _where(user_id, **kw)
    sql = ("SELECT r.id, r.question_id, r.user_answer_json, r.is_correct, r.score, "
           "r.duration_ms, r.mode, r.answered_at, q.qtype, q.stem, q.difficulty_label, "
           "s.name AS subject_name, c.name AS chapter_name, k.name AS knowledge_point_name "
           "FROM answer_records r "
           "JOIN questions q ON q.id = r.question_id "
           "JOIN subjects s ON s.id = r.subject_id "
           "JOIN chapters c ON c.id = r.chapter_id "
           "JOIN knowledge_points k ON k.id = r.knowledge_point_id" + clause +
           " ORDER BY r.id DESC LIMIT ? OFFSET ?")
    rows = db.query_all(sql, tuple(args) + (int(limit), int(offset)))
    for r in rows:
        r["stem_brief"] = (r["stem"] or "")[:60]
        r.pop("stem", None)
    return rows


def count_records(user_id: str, **kw) -> int:
    clause, args = _where(user_id, **kw)
    return db.query_scalar("SELECT COUNT(*) FROM answer_records r" + clause, tuple(args), 0)


def answered_ids(user_id: str, subject_id=None) -> set:
    clause, args = _where(user_id, subject_id=subject_id)
    rows = db.query_all(
        "SELECT DISTINCT r.question_id FROM answer_records r" + clause, tuple(args))
    return {r["question_id"] for r in rows}


def mastered_kp_ids(user_id: str, subject_id=None, min_total=2, min_acc=0.8) -> list:
    """已掌握知识点：作答量达标且正确率高，用于混合巩固题。"""
    args = [user_id]
    sql = ("SELECT knowledge_point_id FROM v_kp_accuracy WHERE user_id = ? "
           "AND total >= ? AND accuracy >= ?")
    args += [min_total, min_acc]
    if subject_id:
        sql += " AND subject_id = ?"
        args.append(subject_id)
    return [r["knowledge_point_id"] for r in db.query_all(sql, tuple(args))]
