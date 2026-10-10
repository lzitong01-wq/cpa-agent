# -*- coding: utf-8 -*-
"""管理员上传教材：落盘 → 建任务 → 后台线程切片向量化 → 更新索引。

为什么要后台任务：单文件向量化实测约 6 秒，长 PDF 可达数分钟，
同步处理会让上传请求超时。这里用"任务表 + 后台线程"而不是 Celery，
因为不想为一个班级规模的应用引入 Redis；接口形态一致，
将来换 Celery 只需替换 `_spawn()` 的实现。

安全约束（全部在 accept_upload 里做，调用方无需重复检查）：
- 扩展名白名单（config.UPLOAD_ALLOWED_EXT），不信任 Content-Type；
- 大小上限（config.UPLOAD_MAX_BYTES）；
- 文件名消毒 + 随机前缀，落盘路径强制在 config.UPLOAD_DIR 之内，
  杜绝 ../ 穿越；上传目录不在 static 之下，不会被直接下载。
"""
import logging
import os
import re
import secrets
import threading
import time

import config
from app import db
from app.rag import index as index_mod, ingest

log = logging.getLogger(__name__)

_UNSAFE = re.compile(r"[^\w一-鿿.\-]+")
MAX_NAME_LEN = 80


class UploadError(ValueError):
    """上传被拒（扩展名/大小/空文件），消息可直接给用户看。"""


def safe_filename(name: str) -> str:
    """只保留中英文、数字、下划线、点、短横线；去掉路径成分。"""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = _UNSAFE.sub("_", base).strip("._") or "upload"
    root, ext = os.path.splitext(base)
    return root[:MAX_NAME_LEN] + ext.lower()


def check_extension(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext not in config.UPLOAD_ALLOWED_EXT:
        raise UploadError("不支持的文件类型 %s，只接受 %s"
                          % (ext or "（无扩展名）", " / ".join(config.UPLOAD_ALLOWED_EXT)))
    return ext


def accept_upload(filename: str, data: bytes, uploaded_by: str,
                  subject_hint: str = None) -> dict:
    """校验并落盘，登记一条 pending 任务，返回任务行。不做切片（交给后台）。"""
    filename = safe_filename(filename)
    check_extension(filename)
    if not data:
        raise UploadError("文件内容为空")
    if len(data) > config.UPLOAD_MAX_BYTES:
        raise UploadError("文件超过 %d MB 上限"
                          % (config.UPLOAD_MAX_BYTES // (1024 * 1024)))

    os.makedirs(config.UPLOAD_DIR, exist_ok=True)
    # 科目提示写进文件名，ingest.guess_subject 会据此判定科目归属
    prefix = ""
    if subject_hint:
        hint = safe_filename(subject_hint)
        if hint and hint not in filename:
            prefix = hint + "-"
    stored = os.path.join(config.UPLOAD_DIR,
                          "%s%s_%s" % (prefix, secrets.token_hex(4), filename))
    stored = os.path.abspath(stored)
    if not stored.startswith(os.path.abspath(config.UPLOAD_DIR) + os.sep):
        raise UploadError("非法的上传路径")
    with open(stored, "wb") as f:
        f.write(data)

    subject_id = None
    if subject_hint:
        from app.business import catalog
        subject_id = catalog.resolve_scope(subject=subject_hint).get("subject_id")

    job_id = db.execute(
        "INSERT INTO ingest_jobs (filename, stored_path, subject_id, size_bytes, "
        "status, uploaded_by) VALUES (?,?,?,?, 'pending', ?)",
        (filename, stored, subject_id, len(data), uploaded_by))
    log.info("收到上传 %s（%.1f KB），任务 #%d", filename, len(data) / 1024.0, job_id)
    return get_job(job_id)

# ------------------------------------------------------------------ 任务处理
def process_job(job_id: int) -> dict:
    """真正干活的函数：切片 → 向量化 → 落库 → 失效索引。可被后台线程或 CLI 调用。"""
    job = _job_row(job_id)          # 内部用，保留 stored_path
    if not job:
        return {"error": "任务不存在：%s" % job_id}
    if job["status"] not in ("pending", "failed"):
        return {"skipped": "任务状态为 %s，无需处理" % job["status"], "job": job}

    db.execute("UPDATE ingest_jobs SET status = 'processing', "
               "started_at = datetime('now','localtime'), message = NULL WHERE id = ?",
               (job_id,))
    t0 = time.time()
    try:
        res = ingest.ingest_file(job["stored_path"], force=True,
                                 uploaded_by=job["uploaded_by"], origin="upload",
                                 title=os.path.splitext(job["filename"])[0])
        if res.get("skipped"):
            raise ValueError(res["skipped"])
        index_mod.invalidate()
        msg = "切出 %d 个片段，科目：%s，耗时 %.1fs" % (
            res["chunks"], res.get("subject") or "未标注", time.time() - t0)
        db.execute("UPDATE ingest_jobs SET status = 'done', doc_id = ?, chunk_count = ?, "
                   "subject_id = COALESCE(?, subject_id), message = ?, "
                   "finished_at = datetime('now','localtime') WHERE id = ?",
                   (res["doc_id"], res["chunks"],
                    _subject_id_of_doc(res["doc_id"]), msg, job_id))
        log.info("任务 #%d 完成：%s", job_id, msg)
    except Exception as exc:                      # 入库失败不能让线程静默死掉
        db.execute("UPDATE ingest_jobs SET status = 'failed', message = ?, "
                   "finished_at = datetime('now','localtime') WHERE id = ?",
                   (str(exc)[:500], job_id))
        log.warning("任务 #%d 失败：%s", job_id, exc)
    finally:
        db.close_conn()                           # 后台线程有独立连接，用完归还
    return get_job(job_id)


def _subject_id_of_doc(doc_id):
    row = db.query_one("SELECT subject_id FROM corpus_docs WHERE id = ?", (doc_id,))
    return row["subject_id"] if row else None


def spawn(job_id: int) -> None:
    """在后台线程里处理任务。上传接口立即返回，前端轮询任务状态。"""
    t = threading.Thread(target=process_job, args=(job_id,),
                         name="ingest-job-%d" % job_id, daemon=True)
    t.start()


def process_pending(limit=20) -> list:
    """把积压的 pending 任务串行处理掉（进程重启后补跑，或 CLI 手动触发）。"""
    rows = db.query_all(
        "SELECT id FROM ingest_jobs WHERE status IN ('pending','processing') "
        "ORDER BY id LIMIT ?", (limit,))
    out = []
    for r in rows:
        # 把上次异常中断的 processing 复位成 pending，否则 process_job 会跳过
        db.execute("UPDATE ingest_jobs SET status = 'pending' WHERE id = ? "
                   "AND status = 'processing'", (r["id"],))
        out.append(process_job(r["id"]))
    return out

# ------------------------------------------------------------------ 查询
JOB_SQL = (
    "SELECT j.*, u.username AS uploader_name, s.name AS subject_name, "
    "       d.title AS doc_title, d.visibility "
    "FROM ingest_jobs j "
    "LEFT JOIN users u ON u.id = j.uploaded_by "
    "LEFT JOIN subjects s ON s.id = j.subject_id "
    "LEFT JOIN corpus_docs d ON d.id = j.doc_id ")


def _job_row(job_id: int):
    """内部用：保留 stored_path（对外的 get_job 会把它摘掉）。"""
    return db.query_one(JOB_SQL + "WHERE j.id = ?", (job_id,))


def get_job(job_id: int):
    return _clean(_job_row(job_id))


def list_jobs(limit=50, status=None) -> list:
    sql, args = JOB_SQL, []
    if status:
        sql += "WHERE j.status = ? "
        args.append(status)
    sql += "ORDER BY j.id DESC LIMIT ?"
    args.append(int(limit))
    return [_clean(r) for r in db.query_all(sql, tuple(args))]


def _clean(row):
    """不把服务器绝对路径下发到前端，只给文件名。"""
    if not row:
        return None
    row = dict(row)
    row.pop("stored_path", None)
    return row


# ------------------------------------------------------------------ 教材管理
def list_docs() -> list:
    """管理员的教材列表：含来源、上传者、可见性、片段数。"""
    return db.query_all(
        "SELECT d.id, d.title, d.subject_id, s.name AS subject_name, d.char_count, "
        "d.chunk_count, d.ingested_at, d.visibility, d.origin, d.uploaded_by, "
        "u.username AS uploader_name "
        "FROM corpus_docs d "
        "LEFT JOIN subjects s ON s.id = d.subject_id "
        "LEFT JOIN users u ON u.id = d.uploaded_by "
        "ORDER BY d.id DESC")


def set_visibility(doc_id: int, visible: bool) -> dict:
    """上下架教材。下架只是不参与检索，切片与向量保留，可随时恢复。"""
    row = db.query_one("SELECT id FROM corpus_docs WHERE id = ?", (doc_id,))
    if not row:
        raise ValueError("教材不存在：%s" % doc_id)
    db.execute("UPDATE corpus_docs SET visibility = ? WHERE id = ?",
               ("public" if visible else "hidden", doc_id))
    index_mod.invalidate()
    return {"doc_id": doc_id, "visibility": "public" if visible else "hidden"}


def delete_doc(doc_id: int, remove_file=True) -> dict:
    """从知识库删除教材（切片与向量随外键级联删除），并可一并删掉上传的原文件。"""
    row = db.query_one("SELECT id, title, source_path, origin FROM corpus_docs "
                       "WHERE id = ?", (doc_id,))
    if not row:
        raise ValueError("教材不存在：%s" % doc_id)
    db.execute("DELETE FROM corpus_docs WHERE id = ?", (doc_id,))
    removed_file = False
    if remove_file and row["origin"] == "upload":
        path = os.path.abspath(row["source_path"] or "")
        # 只允许删上传目录里的文件，绝不碰项目自带语料
        if path.startswith(os.path.abspath(config.UPLOAD_DIR) + os.sep) \
                and os.path.exists(path):
            try:
                os.remove(path)
                removed_file = True
            except OSError as exc:
                log.warning("删除上传文件失败 %s：%s", path, exc)
    index_mod.invalidate()
    log.info("已删除教材 #%d %s", doc_id, row["title"])
    return {"doc_id": doc_id, "title": row["title"], "deleted": True,
            "file_removed": removed_file}


def storage_stats() -> dict:
    docs = db.query_one(
        "SELECT COUNT(*) AS docs, COALESCE(SUM(chunk_count),0) AS chunks, "
        "COALESCE(SUM(char_count),0) AS chars, "
        "SUM(CASE WHEN visibility='public' THEN 1 ELSE 0 END) AS public_docs, "
        "SUM(CASE WHEN origin='upload' THEN 1 ELSE 0 END) AS uploaded_docs "
        "FROM corpus_docs") or {}
    jobs = db.query_all("SELECT status, COUNT(*) AS n FROM ingest_jobs GROUP BY status")
    return {"docs": docs.get("docs", 0), "public_docs": docs.get("public_docs", 0),
            "uploaded_docs": docs.get("uploaded_docs", 0),
            "chunks": docs.get("chunks", 0), "chars": docs.get("chars", 0),
            "jobs": {r["status"]: r["n"] for r in jobs},
            "upload_dir": config.UPLOAD_DIR,
            "max_upload_mb": config.UPLOAD_MAX_BYTES // (1024 * 1024),
            "allowed_ext": list(config.UPLOAD_ALLOWED_EXT)}


