# -*- coding: utf-8 -*-
"""答题历史统计：总览、分科目/章节/知识点正确率、每日趋势、薄弱点排行。"""
from app import db


def overview(user_id: str, subject_id=None) -> dict:
    args = [user_id]
    cond = "user_id = ?"
    if subject_id:
        cond += " AND subject_id = ?"
        args.append(subject_id)
    row = db.query_one(
        "SELECT COUNT(*) AS total, SUM(is_correct) AS correct, "
        "SUM(score) AS score, SUM(duration_ms) AS duration_ms, "
        "COUNT(DISTINCT question_id) AS distinct_questions, "
        "COUNT(DISTINCT knowledge_point_id) AS kp_touched, "
        "COUNT(DISTINCT date(answered_at)) AS active_days "
        "FROM answer_records WHERE " + cond, tuple(args)) or {}
    total = row.get("total") or 0
    correct = row.get("correct") or 0
    wrong_open = db.query_scalar(
        "SELECT COUNT(*) FROM wrong_questions WHERE user_id = ? AND status = 'open'"
        + (" AND subject_id = ?" if subject_id else ""),
        tuple(args), 0)
    kp_total = db.query_scalar(
        "SELECT COUNT(*) FROM knowledge_points"
        + (" WHERE subject_id = ?" if subject_id else ""),
        (subject_id,) if subject_id else (), 0)
    q_total = db.query_scalar(
        "SELECT COUNT(*) FROM questions WHERE is_active = 1"
        + (" AND subject_id = ?" if subject_id else ""),
        (subject_id,) if subject_id else (), 0)
    return {
        "total": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "score": round(row.get("score") or 0.0, 2),
        "avg_seconds": round((row.get("duration_ms") or 0) / 1000.0 / total, 1) if total else 0.0,
        "distinct_questions": row.get("distinct_questions") or 0,
        "kp_touched": row.get("kp_touched") or 0,
        "kp_total": kp_total,
        "question_total": q_total,
        "active_days": row.get("active_days") or 0,
        "wrongbook_open": wrong_open,
    }


def by_subject(user_id: str) -> list:
    return db.query_all(
        "SELECT s.id AS subject_id, s.name AS subject_name, "
        "COALESCE(v.total, 0) AS total, COALESCE(v.correct, 0) AS correct, "
        "COALESCE(v.accuracy, 0) AS accuracy, v.last_answered_at "
        "FROM subjects s LEFT JOIN v_subject_accuracy v "
        "ON v.subject_id = s.id AND v.user_id = ? ORDER BY s.sort_order", (user_id,))


def by_chapter(user_id: str, subject_id=None) -> list:
    sql = ("SELECT c.id AS chapter_id, c.name AS chapter_name, s.name AS subject_name, "
           "COUNT(r.id) AS total, SUM(COALESCE(r.is_correct, 0)) AS correct, "
           "ROUND(1.0 * SUM(COALESCE(r.is_correct, 0)) / NULLIF(COUNT(r.id), 0), 4) AS accuracy "
           "FROM chapters c JOIN subjects s ON s.id = c.subject_id "
           "LEFT JOIN answer_records r ON r.chapter_id = c.id AND r.user_id = ? ")
    args = [user_id]
    if subject_id:
        sql += "WHERE c.subject_id = ? "
        args.append(subject_id)
    sql += "GROUP BY c.id ORDER BY s.sort_order, c.sort_order"
    return db.query_all(sql, tuple(args))


def by_knowledge_point(user_id: str, subject_id=None, limit=50, weakest_first=True) -> list:
    sql = ("SELECT v.knowledge_point_id, k.name AS knowledge_point_name, "
           "c.name AS chapter_name, s.name AS subject_name, "
           "v.total, v.correct, v.accuracy, v.last_answered_at "
           "FROM v_kp_accuracy v "
           "JOIN knowledge_points k ON k.id = v.knowledge_point_id "
           "JOIN chapters c ON c.id = k.chapter_id "
           "JOIN subjects s ON s.id = k.subject_id WHERE v.user_id = ?")
    args = [user_id]
    if subject_id:
        sql += " AND v.subject_id = ?"
        args.append(subject_id)
    sql += " ORDER BY v.accuracy %s, v.total DESC LIMIT ?" % ("ASC" if weakest_first else "DESC")
    args.append(int(limit))
    return db.query_all(sql, tuple(args))


def by_qtype(user_id: str, subject_id=None) -> list:
    sql = ("SELECT q.qtype, COUNT(*) AS total, SUM(r.is_correct) AS correct, "
           "ROUND(1.0 * SUM(r.is_correct) / COUNT(*), 4) AS accuracy "
           "FROM answer_records r JOIN questions q ON q.id = r.question_id "
           "WHERE r.user_id = ?")
    args = [user_id]
    if subject_id:
        sql += " AND r.subject_id = ?"
        args.append(subject_id)
    sql += " GROUP BY q.qtype"
    return db.query_all(sql, tuple(args))


def daily_trend(user_id: str, days=14, subject_id=None) -> list:
    sql = ("SELECT date(answered_at) AS day, COUNT(*) AS total, SUM(is_correct) AS correct, "
           "ROUND(1.0 * SUM(is_correct) / COUNT(*), 4) AS accuracy "
           "FROM answer_records WHERE user_id = ? "
           "AND date(answered_at) >= date('now', 'localtime', ?)")
    args = [user_id, "-%d day" % int(days)]
    if subject_id:
        sql += " AND subject_id = ?"
        args.append(subject_id)
    sql += " GROUP BY day ORDER BY day"
    return db.query_all(sql, tuple(args))


def mode_breakdown(user_id: str) -> list:
    return db.query_all(
        "SELECT mode, COUNT(*) AS total, SUM(is_correct) AS correct, "
        "ROUND(1.0 * SUM(is_correct) / COUNT(*), 4) AS accuracy "
        "FROM answer_records WHERE user_id = ? GROUP BY mode ORDER BY total DESC", (user_id,))


def dashboard(user_id: str, subject_id=None) -> dict:
    """统计页一次拉全。"""
    return {
        "overview": overview(user_id, subject_id),
        "by_subject": by_subject(user_id),
        "by_chapter": by_chapter(user_id, subject_id),
        "weakest_kp": by_knowledge_point(user_id, subject_id, limit=10, weakest_first=True),
        "strongest_kp": by_knowledge_point(user_id, subject_id, limit=5, weakest_first=False),
        "by_qtype": by_qtype(user_id, subject_id),
        "daily_trend": daily_trend(user_id, 14, subject_id),
        "mode_breakdown": mode_breakdown(user_id),
    }
