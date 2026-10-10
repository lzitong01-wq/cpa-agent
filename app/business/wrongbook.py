# -*- coding: utf-8 -*-
"""错题本：自动收集、查看、重做、移除，支持按科目/知识点筛选。

设计要点：
- 答错自动 upsert（wrong_count 累加），不需要用户手动加入；
- 移除用 status='removed' 软删除，保留错题痕迹，统计口径不受影响；
- 重做答对累计 consecutive_right，达到 RESOLVE_STREAK 自动转 resolved（已掌握），
  仍可在"已掌握"筛选下回看。
"""
from app import db
from app.business import questions as q_svc

RESOLVE_STREAK = 2  # 连续做对几次算掌握


def mark_wrong(conn, user_id: str, question: dict) -> None:
    """答错时调用（在调用方事务内）。"""
    row = conn.execute(
        "SELECT id, wrong_count, status FROM wrong_questions WHERE user_id = ? AND question_id = ?",
        (user_id, question["id"])).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO wrong_questions (user_id, question_id, subject_id, chapter_id, "
            "knowledge_point_id) VALUES (?, ?, ?, ?, ?)",
            (user_id, question["id"], question["subject_id"], question["chapter_id"],
             question["knowledge_point_id"]))
    else:
        conn.execute(
            "UPDATE wrong_questions SET wrong_count = wrong_count + 1, status = 'open', "
            "consecutive_right = 0, last_wrong_at = datetime('now','localtime'), "
            "updated_at = datetime('now','localtime') WHERE id = ?", (row[0],))


def mark_right(conn, user_id: str, question_id: int) -> None:
    """错题重做答对时调用（在调用方事务内）。"""
    row = conn.execute(
        "SELECT id, consecutive_right FROM wrong_questions "
        "WHERE user_id = ? AND question_id = ? AND status != 'removed'",
        (user_id, question_id)).fetchone()
    if row is None:
        return
    streak = (row[1] or 0) + 1
    status = "resolved" if streak >= RESOLVE_STREAK else "open"
    conn.execute(
        "UPDATE wrong_questions SET consecutive_right = ?, redo_count = redo_count + 1, "
        "status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
        (streak, status, row[0]))


def _where(user_id, subject_id=None, knowledge_point_id=None, chapter_id=None,
           status="open"):
    where = ["w.user_id = ?"]
    args = [user_id]
    if status == "all":
        where.append("w.status != 'removed'")
    elif status:
        where.append("w.status = ?")
        args.append(status)
    if subject_id:
        where.append("w.subject_id = ?")
        args.append(subject_id)
    if chapter_id:
        where.append("w.chapter_id = ?")
        args.append(chapter_id)
    if knowledge_point_id:
        where.append("w.knowledge_point_id = ?")
        args.append(knowledge_point_id)
    return " WHERE " + " AND ".join(where), args


def list_wrong(user_id: str, limit=20, offset=0, with_answer=False, **kw) -> list:
    clause, args = _where(user_id, **kw)
    sql = ("SELECT w.question_id, w.wrong_count, w.redo_count, w.status, w.last_wrong_at, "
           "q.*, s.name AS subject_name, c.name AS chapter_name, k.name AS knowledge_point_name "
           "FROM wrong_questions w "
           "JOIN questions q ON q.id = w.question_id "
           "JOIN subjects s ON s.id = q.subject_id "
           "JOIN chapters c ON c.id = q.chapter_id "
           "JOIN knowledge_points k ON k.id = q.knowledge_point_id" + clause +
           " ORDER BY w.wrong_count DESC, w.last_wrong_at DESC LIMIT ? OFFSET ?")
    rows = db.query_all(sql, tuple(args) + (int(limit), int(offset)))
    out = []
    for r in rows:
        item = q_svc.serialize(r, with_answer=with_answer)
        item.update({
            "wrong_count": r["wrong_count"], "redo_count": r["redo_count"],
            "status": r["status"], "last_wrong_at": r["last_wrong_at"],
        })
        out.append(item)
    return out


def count_wrong(user_id: str, **kw) -> int:
    clause, args = _where(user_id, **kw)
    return db.query_scalar("SELECT COUNT(*) FROM wrong_questions w" + clause, tuple(args), 0)


def wrong_question_ids(user_id: str, status="open", **kw) -> list:
    clause, args = _where(user_id, status=status, **kw)
    rows = db.query_all(
        "SELECT w.question_id FROM wrong_questions w" + clause +
        " ORDER BY w.wrong_count DESC, w.last_wrong_at DESC", tuple(args))
    return [r["question_id"] for r in rows]


def wrong_kp_weights(user_id: str, subject_id=None) -> dict:
    """错题涉及的知识点 → 累计错次，推送算法的第一优先级输入。"""
    clause, args = _where(user_id, subject_id=subject_id, status="open")
    rows = db.query_all(
        "SELECT w.knowledge_point_id AS kp, SUM(w.wrong_count) AS n "
        "FROM wrong_questions w" + clause + " GROUP BY w.knowledge_point_id", tuple(args))
    return {r["kp"]: r["n"] for r in rows}


def remove_wrong(user_id: str, question_id: int) -> bool:
    """软删除：保留答题记录与历史错次，只把状态置为 removed。

    返回是否真的命中了一条错题记录（db.execute 返回的是 lastrowid，
    对 UPDATE 没有意义，所以这里直接取 cursor.rowcount）。
    """
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE wrong_questions SET status = 'removed', "
            "updated_at = datetime('now','localtime') WHERE user_id = ? AND question_id = ?",
            (user_id, question_id))
        return cur.rowcount > 0


def restore_wrong(user_id: str, question_id: int) -> bool:
    with db.transaction() as conn:
        cur = conn.execute(
            "UPDATE wrong_questions SET status = 'open', consecutive_right = 0, "
            "updated_at = datetime('now','localtime') WHERE user_id = ? AND question_id = ?",
            (user_id, question_id))
        return cur.rowcount > 0


def redo_set(user_id: str, limit=10, **kw) -> list:
    """错题刷题模式取题：错次多、最近错的优先。"""
    ids = wrong_question_ids(user_id, **kw)[: int(limit)]
    if not ids:
        return []
    rows = q_svc.list_questions(only_ids=ids, limit=len(ids))
    order = {qid: i for i, qid in enumerate(ids)}
    return sorted(rows, key=lambda x: order.get(x["id"], 999))


def summary(user_id: str) -> dict:
    rows = db.query_all(
        "SELECT s.id AS subject_id, s.name AS subject_name, "
        "SUM(CASE WHEN w.status = 'open' THEN 1 ELSE 0 END) AS open_count, "
        "SUM(CASE WHEN w.status = 'resolved' THEN 1 ELSE 0 END) AS resolved_count "
        "FROM wrong_questions w JOIN subjects s ON s.id = w.subject_id "
        "WHERE w.user_id = ? AND w.status != 'removed' GROUP BY s.id ORDER BY s.sort_order",
        (user_id,))
    return {
        "by_subject": rows,
        "open_total": sum(r["open_count"] or 0 for r in rows),
        "resolved_total": sum(r["resolved_count"] or 0 for r in rows),
    }
