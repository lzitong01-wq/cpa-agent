# -*- coding: utf-8 -*-
"""试题读取、筛选与种子载入。

对外统一用 serialize()：做题时不返回答案与解析，提交后才下发，
避免前端源码里直接能看到答案。
"""
import json

from app import db

_BASE = """
SELECT q.*, s.name AS subject_name, c.name AS chapter_name, k.name AS knowledge_point_name
FROM questions q
JOIN subjects s ON s.id = q.subject_id
JOIN chapters c ON c.id = q.chapter_id
JOIN knowledge_points k ON k.id = q.knowledge_point_id
"""

QTYPE_LABELS = {
    "single": "单选题", "multiple": "多选题", "judge": "判断题",
    "short": "简答题", "comprehensive": "综合题",
}


def serialize(row: dict, with_answer: bool = False) -> dict:
    if not row:
        return None
    out = {
        "id": row["id"],
        "qtype": row["qtype"],
        "qtype_label": QTYPE_LABELS.get(row["qtype"], row["qtype"]),
        "subject_id": row["subject_id"],
        "subject_name": row.get("subject_name"),
        "chapter_id": row["chapter_id"],
        "chapter_name": row.get("chapter_name"),
        "knowledge_point_id": row["knowledge_point_id"],
        "knowledge_point_name": row.get("knowledge_point_name"),
        "stem": row["stem"],
        "material": row.get("material"),
        "options": json.loads(row["options_json"]) if row.get("options_json") else None,
        "difficulty": row["difficulty"],
        "difficulty_label": row["difficulty_label"],
        "score": row["score"],
        "source": row.get("source"),
    }
    if with_answer:
        out["answer"] = json.loads(row["answer_json"])
        out["analysis"] = row["analysis"]
    return out


def _filters(subject_id=None, chapter_id=None, knowledge_point_id=None,
             qtypes=None, difficulty=None, exclude_ids=None, only_ids=None):
    where = ["q.is_active = 1"]
    args = []
    if subject_id:
        where.append("q.subject_id = ?")
        args.append(subject_id)
    if chapter_id:
        where.append("q.chapter_id = ?")
        args.append(chapter_id)
    if knowledge_point_id:
        if isinstance(knowledge_point_id, (list, tuple, set)):
            ids = list(knowledge_point_id)
            if not ids:
                where.append("1 = 0")
            else:
                where.append("q.knowledge_point_id IN (%s)" % ",".join("?" * len(ids)))
                args.extend(ids)
        else:
            where.append("q.knowledge_point_id = ?")
            args.append(knowledge_point_id)
    if qtypes:
        qs = [qtypes] if isinstance(qtypes, str) else list(qtypes)
        where.append("q.qtype IN (%s)" % ",".join("?" * len(qs)))
        args.extend(qs)
    if difficulty:
        ds = [difficulty] if isinstance(difficulty, int) else list(difficulty)
        where.append("q.difficulty IN (%s)" % ",".join("?" * len(ds)))
        args.extend(ds)
    if exclude_ids:
        ex = list(exclude_ids)
        if ex:
            where.append("q.id NOT IN (%s)" % ",".join("?" * len(ex)))
            args.extend(ex)
    if only_ids is not None:
        oi = list(only_ids)
        if not oi:
            where.append("1 = 0")
        else:
            where.append("q.id IN (%s)" % ",".join("?" * len(oi)))
            args.extend(oi)
    return " WHERE " + " AND ".join(where), args


def get_question(question_id: int, with_answer: bool = False):
    row = db.query_one(_BASE + " WHERE q.id = ?", (question_id,))
    return serialize(row, with_answer)


def get_question_raw(question_id: int):
    """判分用：拿到含 answer_json / score 的原始行。"""
    return db.query_one(_BASE + " WHERE q.id = ?", (question_id,))


def list_questions(limit=20, offset=0, order="q.id", with_answer=False, **kw) -> list:
    clause, args = _filters(**kw)
    sql = _BASE + clause + " ORDER BY %s LIMIT ? OFFSET ?" % order
    rows = db.query_all(sql, tuple(args) + (int(limit), int(offset)))
    return [serialize(r, with_answer) for r in rows]


def count_questions(**kw) -> int:
    clause, args = _filters(**kw)
    return db.query_scalar("SELECT COUNT(*) FROM questions q" + clause, tuple(args), 0)


def random_questions(limit=10, with_answer=False, **kw) -> list:
    clause, args = _filters(**kw)
    sql = _BASE + clause + " ORDER BY RANDOM() LIMIT ?"
    rows = db.query_all(sql, tuple(args) + (int(limit),))
    return [serialize(r, with_answer) for r in rows]


def type_distribution(**kw) -> dict:
    clause, args = _filters(**kw)
    rows = db.query_all(
        "SELECT q.qtype, COUNT(*) AS n FROM questions q" + clause + " GROUP BY q.qtype",
        tuple(args))
    return {r["qtype"]: r["n"] for r in rows}


# ------------------------------------------------------------------ 种子载入
def seed_questions() -> dict:
    """按 (知识点, 题干) 去重写入，可重复执行。"""
    from data.seed import questions_seed

    inserted, skipped = 0, 0
    with db.transaction() as conn:
        for item in questions_seed.QUESTIONS:
            sub = conn.execute("SELECT id FROM subjects WHERE code = ?",
                              (item["subject"],)).fetchone()
            if not sub:
                skipped += 1
                continue
            ch = conn.execute("SELECT id FROM chapters WHERE subject_id = ? AND code = ?",
                              (sub[0], item["chapter"])).fetchone()
            if not ch:
                skipped += 1
                continue
            kp = conn.execute(
                "SELECT id FROM knowledge_points WHERE chapter_id = ? "
                "ORDER BY sort_order, id LIMIT 1 OFFSET ?",
                (ch[0], int(item["kp"]) - 1)).fetchone()
            if not kp:
                skipped += 1
                continue
            dup = conn.execute(
                "SELECT id FROM questions WHERE knowledge_point_id = ? AND stem = ?",
                (kp[0], item["stem"])).fetchone()
            if dup:
                skipped += 1
                continue
            options = item.get("options")
            options_json = json.dumps(
                [{"key": k, "text": t} for k, t in options], ensure_ascii=False
            ) if options else None
            conn.execute(
                "INSERT INTO questions (qtype, subject_id, chapter_id, knowledge_point_id, "
                "stem, material, options_json, answer_json, analysis, difficulty, "
                "difficulty_label, score, source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (item["qtype"], sub[0], ch[0], kp[0], item["stem"], item.get("material"),
                 options_json, json.dumps(item["answer"], ensure_ascii=False),
                 item["analysis"], item["difficulty"], item["difficulty_label"],
                 item["score"], item.get("source")))
            inserted += 1
    return {"inserted": inserted, "skipped": skipped,
            "total": db.query_scalar("SELECT COUNT(*) FROM questions", (), 0)}
