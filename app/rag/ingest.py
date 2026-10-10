# -*- coding: utf-8 -*-
"""教材入库：读文件 → 切片 → 向量化 → 落库。

支持 .md / .txt / .pdf / .docx（PDF 用 PyMuPDF，DOCX 用 python-docx，都已在环境中）。
科目归属判定顺序：文件名前缀 > 文件内 `# 科目` 标题 > 目录名，落到 corpus_chunks.subject_id，
检索时即可按科目过滤。同一文件重复入库按内容哈希跳过，改动后自动覆盖旧切片。
"""
import hashlib
import json
import logging
import os

import numpy as np

import config
from app import db
from app.business import catalog
from app.rag import bm25 as bm25_mod, chunker, embedder as embedder_mod, index as index_mod

log = logging.getLogger(__name__)

SUPPORTED = (".md", ".markdown", ".txt", ".pdf", ".docx")


# ------------------------------------------------------------------ 读文件
def read_file(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".md", ".markdown", ".txt"):
        for enc in ("utf-8", "utf-8-sig", "gbk"):
            try:
                with open(path, "r", encoding=enc) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    if ext == ".pdf":
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        try:
            return "\n".join(page.get_text("text") for page in doc)
        finally:
            doc.close()
    if ext == ".docx":
        import docx
        d = docx.Document(path)
        lines = []
        for p in d.paragraphs:
            style = (p.style.name or "").lower()
            text = p.text.strip()
            if not text:
                continue
            # 把 Word 标题层级映射成 markdown，切片器就能拿到章节元数据
            if "heading 1" in style:
                lines.append("# " + text)
            elif "heading 2" in style:
                lines.append("## " + text)
            elif "heading 3" in style:
                lines.append("### " + text)
            else:
                lines.append(text)
        return "\n".join(lines)
    raise ValueError("不支持的文件类型：%s" % ext)


def guess_subject(path: str, raw: str):
    """返回 (subject_id, subject_name)，判不出来则 (None, None)。"""
    subjects = catalog.list_subjects()
    base = os.path.basename(path)
    for s in subjects:                      # 文件名含科目名，如 "会计-第十八章.md"
        if s["name"] in base or s["code"] in base.lower():
            return s["id"], s["name"]
    head = "\n".join((raw or "").splitlines()[:6])
    for s in subjects:                      # 文件开头的 `# 会计`
        if s["name"] in head:
            return s["id"], s["name"]
    parent = os.path.basename(os.path.dirname(path))
    for s in subjects:
        if s["name"] in parent:
            return s["id"], s["name"]
    return None, None


# ------------------------------------------------------------------ 入库
def ingest_file(path: str, force=False, emb=None, verbose=True,
                uploaded_by=None, origin=None, title=None) -> dict:
    """入库单个文件。

    uploaded_by / origin 供管理员上传功能标注来源；命令行入库时留空，
    此时 origin 默认 'bundled'（随项目自带的语料）。
    title 留空则取文件名——上传的文件落盘时带了随机前缀，
    所以上传路径会显式传入原始文件名，避免前缀出现在界面上。
    """
    raw = read_file(path)
    if not (raw or "").strip():
        return {"path": path, "skipped": "空文件", "chunks": 0}

    file_hash = hashlib.md5(raw.encode("utf-8", "replace")).hexdigest()
    src = os.path.abspath(path)
    exist = db.query_one("SELECT id, file_hash FROM corpus_docs WHERE source_path = ?", (src,))
    if exist and exist["file_hash"] == file_hash and not force:
        return {"path": path, "skipped": "内容未变", "chunks": 0, "doc_id": exist["id"]}

    subject_id, subject_name = guess_subject(path, raw)
    chunks = chunker.split_text(raw, default_subject=subject_name)
    if not chunks:
        return {"path": path, "skipped": "切不出有效内容", "chunks": 0}

    emb = emb or embedder_mod.get_embedder()
    # 用带面包屑的 search_text 做向量化与分词（见 chunker._flush），
    # 存库的 text 仍是纯正文，前端展示与引用摘录不受影响。
    vectors = emb.encode([c.get("search_text") or c["text"] for c in chunks])

    title = title or os.path.splitext(os.path.basename(path))[0]
    origin = origin or "bundled"
    with db.transaction() as conn:
        if exist:
            conn.execute("DELETE FROM corpus_chunks WHERE doc_id = ?", (exist["id"],))
            conn.execute(
                "UPDATE corpus_docs SET subject_id = ?, title = ?, file_hash = ?, "
                "char_count = ?, chunk_count = ?, ingested_at = datetime('now','localtime'), "
                "uploaded_by = COALESCE(?, uploaded_by), origin = ? "
                "WHERE id = ?",
                (subject_id, title, file_hash, len(raw), len(chunks), uploaded_by,
                 origin, exist["id"]))
            doc_id = exist["id"]
        else:
            cur = conn.execute(
                "INSERT INTO corpus_docs (subject_id, title, source_path, file_hash, "
                "char_count, chunk_count, uploaded_by, origin) VALUES (?,?,?,?,?,?,?,?)",
                (subject_id, title, src, file_hash, len(raw), len(chunks),
                 uploaded_by, origin))
            doc_id = cur.lastrowid

        for i, c in enumerate(chunks):
            toks = bm25_mod.tokenize(c.get("search_text") or c["text"])
            cur = conn.execute(
                "INSERT INTO corpus_chunks (doc_id, subject_id, subject_name, chapter_name, "
                "knowledge_point_name, ord, text, char_count, tokens_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (doc_id, subject_id, c.get("subject_name") or subject_name,
                 c.get("chapter_name"), c.get("knowledge_point_name"), c["ord"],
                 c["text"], c["char_count"], json.dumps(toks, ensure_ascii=False)))
            chunk_id = cur.lastrowid
            vec = np.asarray(vectors[i], dtype="float32")
            conn.execute(
                "INSERT OR REPLACE INTO chunk_vectors (chunk_id, dim, model, vec) "
                "VALUES (?,?,?,?)",
                (chunk_id, int(vec.shape[0]), emb.name, vec.tobytes()))

    if verbose:
        log.info("入库 %s：%d 个切片，科目=%s", os.path.basename(path), len(chunks),
                 subject_name or "未标注")
    return {"path": path, "doc_id": doc_id, "chunks": len(chunks),
            "subject": subject_name, "dim": int(np.asarray(vectors[0]).shape[0])}


def ingest_dir(root=None, force=False) -> dict:
    root = root or config.CORPUS_DIR
    if not os.path.isdir(root):
        return {"root": root, "files": 0, "chunks": 0, "details": [],
                "error": "语料目录不存在"}
    emb = embedder_mod.get_embedder()
    details, total_chunks = [], 0
    for dirpath, _dirs, files in os.walk(root):
        for fn in sorted(files):
            if not fn.lower().endswith(SUPPORTED):
                continue
            p = os.path.join(dirpath, fn)
            try:
                r = ingest_file(p, force=force, emb=emb)
            except Exception as e:
                r = {"path": p, "error": str(e), "chunks": 0}
                log.warning("入库失败 %s：%s", p, e)
            details.append(r)
            total_chunks += r.get("chunks", 0)
    index_mod.invalidate()
    return {
        "root": root, "files": len(details), "chunks": total_chunks,
        "backend": emb.name, "details": details,
        "index": index_mod.get_index(force_reload=True).stats(),
    }


def clear_corpus() -> None:
    """清空知识库（切片与向量随文档级联删除）。"""
    db.execute("DELETE FROM corpus_docs")
    index_mod.invalidate()
