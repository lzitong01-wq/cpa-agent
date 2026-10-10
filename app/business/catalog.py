# -*- coding: utf-8 -*-
"""三级分类（科目 → 章节 → 知识点）的读取与种子载入。"""
from app import db


# ------------------------------------------------------------------ 查询
def list_subjects() -> list:
    return db.query_all(
        "SELECT id, code, name, full_name, description FROM subjects ORDER BY sort_order, id"
    )


def get_subject(subject_id=None, code=None, name=None):
    if subject_id is not None:
        return db.query_one("SELECT * FROM subjects WHERE id = ?", (subject_id,))
    if code:
        return db.query_one("SELECT * FROM subjects WHERE code = ?", (code,))
    if name:
        return db.query_one("SELECT * FROM subjects WHERE name = ?", (name,))
    return None


def list_chapters(subject_id=None) -> list:
    if subject_id:
        return db.query_all(
            "SELECT id, subject_id, code, name FROM chapters WHERE subject_id = ? "
            "ORDER BY sort_order, id", (subject_id,))
    return db.query_all(
        "SELECT id, subject_id, code, name FROM chapters ORDER BY subject_id, sort_order, id")


def list_knowledge_points(chapter_id=None, subject_id=None) -> list:
    if chapter_id:
        return db.query_all(
            "SELECT id, chapter_id, subject_id, code, name FROM knowledge_points "
            "WHERE chapter_id = ? ORDER BY sort_order, id", (chapter_id,))
    if subject_id:
        return db.query_all(
            "SELECT id, chapter_id, subject_id, code, name FROM knowledge_points "
            "WHERE subject_id = ? ORDER BY chapter_id, sort_order, id", (subject_id,))
    return db.query_all("SELECT id, chapter_id, subject_id, code, name FROM knowledge_points")


def full_tree() -> list:
    """一次查询拼出完整三级树，供题库页左侧导航使用。"""
    subjects = list_subjects()
    chapters = list_chapters()
    kps = list_knowledge_points()
    q_counts = {
        r["chapter_id"]: r["n"] for r in db.query_all(
            "SELECT chapter_id, COUNT(*) AS n FROM questions WHERE is_active = 1 "
            "GROUP BY chapter_id")
    }
    kp_counts = {
        r["knowledge_point_id"]: r["n"] for r in db.query_all(
            "SELECT knowledge_point_id, COUNT(*) AS n FROM questions WHERE is_active = 1 "
            "GROUP BY knowledge_point_id")
    }
    by_chapter = {}
    for kp in kps:
        kp["question_count"] = kp_counts.get(kp["id"], 0)
        by_chapter.setdefault(kp["chapter_id"], []).append(kp)
    by_subject = {}
    for ch in chapters:
        ch["knowledge_points"] = by_chapter.get(ch["id"], [])
        ch["question_count"] = q_counts.get(ch["id"], 0)
        by_subject.setdefault(ch["subject_id"], []).append(ch)
    for s in subjects:
        s["chapters"] = by_subject.get(s["id"], [])
        s["question_count"] = sum(c["question_count"] for c in s["chapters"])
    return subjects


def resolve_scope(subject=None, chapter=None, knowledge_point=None) -> dict:
    """把中文名/code/id 混合输入统一解析成 id，Agent 层解析用户话术时使用。"""
    out = {"subject_id": None, "chapter_id": None, "knowledge_point_id": None}
    if subject:
        row = None
        if str(subject).isdigit():
            row = get_subject(subject_id=int(subject))
        if row is None:
            row = get_subject(code=str(subject)) or get_subject(name=str(subject))
        if row is None:  # 支持"会计科目""《会计》"这类模糊说法
            row = db.query_one(
                "SELECT * FROM subjects WHERE ? LIKE '%' || name || '%' ORDER BY LENGTH(name) DESC",
                (str(subject),))
        if row:
            out["subject_id"] = row["id"]
    if chapter:
        sql = "SELECT * FROM chapters WHERE (code = ? OR name = ? OR name LIKE '%' || ? || '%')"
        args = [str(chapter), str(chapter), str(chapter)]
        if out["subject_id"]:
            sql += " AND subject_id = ?"
            args.append(out["subject_id"])
        row = db.query_one(sql + " ORDER BY LENGTH(name) LIMIT 1", tuple(args))
        if row:
            out["chapter_id"] = row["id"]
            out["subject_id"] = out["subject_id"] or row["subject_id"]
    if knowledge_point:
        sql = ("SELECT * FROM knowledge_points WHERE (code = ? OR name = ? "
               "OR name LIKE '%' || ? || '%')")
        args = [str(knowledge_point)] * 3
        if out["chapter_id"]:
            sql += " AND chapter_id = ?"
            args.append(out["chapter_id"])
        elif out["subject_id"]:
            sql += " AND subject_id = ?"
            args.append(out["subject_id"])
        row = db.query_one(sql + " ORDER BY LENGTH(name) LIMIT 1", tuple(args))
        if row:
            out["knowledge_point_id"] = row["id"]
            out["chapter_id"] = out["chapter_id"] or row["chapter_id"]
            out["subject_id"] = out["subject_id"] or row["subject_id"]
    return out


# ------------------------------------------------------------------ 种子载入
def seed_catalog() -> dict:
    """幂等写入三级分类；已存在的按 code 复用，不产生重复。"""
    from data.seed import catalog_seed

    s_ids, c_ids = {}, {}
    with db.transaction() as conn:
        for (s_code, s_name, s_full, s_i, c_code, c_name, c_i,
             kp_code, kp_name, k_i) in catalog_seed.iter_rows():
            if s_code not in s_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO subjects (code, name, full_name, sort_order) "
                    "VALUES (?, ?, ?, ?)", (s_code, s_name, s_full, s_i))
                s_ids[s_code] = conn.execute(
                    "SELECT id FROM subjects WHERE code = ?", (s_code,)).fetchone()[0]
            sid = s_ids[s_code]
            ckey = (sid, c_code)
            if ckey not in c_ids:
                conn.execute(
                    "INSERT OR IGNORE INTO chapters (subject_id, code, name, sort_order) "
                    "VALUES (?, ?, ?, ?)", (sid, c_code, c_name, c_i))
                c_ids[ckey] = conn.execute(
                    "SELECT id FROM chapters WHERE subject_id = ? AND code = ?",
                    (sid, c_code)).fetchone()[0]
            conn.execute(
                "INSERT OR IGNORE INTO knowledge_points "
                "(chapter_id, subject_id, code, name, sort_order) VALUES (?, ?, ?, ?, ?)",
                (c_ids[ckey], sid, kp_code, kp_name, k_i))
    return {
        "subjects": db.query_scalar("SELECT COUNT(*) FROM subjects", (), 0),
        "chapters": db.query_scalar("SELECT COUNT(*) FROM chapters", (), 0),
        "knowledge_points": db.query_scalar("SELECT COUNT(*) FROM knowledge_points", (), 0),
    }
