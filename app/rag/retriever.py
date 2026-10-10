# -*- coding: utf-8 -*-
"""混合检索：稠密向量 + BM25 融合，按科目过滤，低于阈值即判定"教材中无相关描述"。

拒答判定用的是**绝对**信号，不是排名：
- 稠密路取原始余弦相似度（跨查询可比），低于 config.RELEVANCE_THRESHOLD 视为不相关；
- 稀疏路取 BM25 原始分 ÷ 查询词数（按词平均后跨查询可比），
  低于 config.SPARSE_PER_TOKEN_MIN 视为无实质关键词命中；
- 两路都不达标才拒答，避免"换个说法就说教材里没有"的误伤。
排序用的融合分里 BM25 走归一化，只影响名次不影响拒答。
阈值标定脚本：scripts/calibrate_refusal.py。
"""
import logging
import time

import config
from app import db
from app.rag import bm25 as bm25_mod, embedder as embedder_mod, index as index_mod

log = logging.getLogger(__name__)

SPARSE_PER_TOKEN_MIN = getattr(config, "SPARSE_PER_TOKEN_MIN", 1.10)
NO_CONTEXT_MESSAGE = "CPA教材中没有对应相关描述，请核对提问。"


def search(query: str, subject_id=None, top_k=None, candidates=None,
           user_id=None, log_it=True) -> dict:
    """返回带溯源元数据的检索结果；refused=True 时 hits 为空。"""
    t0 = time.time()
    top_k = int(top_k or config.RETRIEVE_TOP_K)
    candidates = int(candidates or config.RETRIEVE_CANDIDATES)
    query = (query or "").strip()

    idx = index_mod.get_index()
    emb = embedder_mod.get_embedder()
    out = {
        "query": query, "subject_id": subject_id, "hits": [],
        "top_score": 0.0, "dense_top": 0.0, "sparse_top": 0.0, "sparse_per_token": 0.0,
        "refused": True, "reason": "", "backend": emb.name,
        "index": idx.stats(), "elapsed_ms": 0,
    }

    if not query:
        out["reason"] = "空查询"
        return _finish(out, t0, user_id, log_it)
    if not idx.chunk_ids:
        out["reason"] = "知识库为空，请先执行 python scripts/ingest_corpus.py"
        return _finish(out, t0, user_id, log_it)

    tokens = bm25_mod.tokenize(query)
    dense = {}
    try:
        qv = emb.encode_one(query)
        dense = idx.dense_scores(qv, subject_id=subject_id)
    except Exception as e:
        log.warning("稠密检索失败，仅用 BM25：%s", e)

    sparse_rel = idx.sparse_scores(tokens, subject_id=subject_id)
    sparse_raw = {}
    if idx.bm25 is not None and len(idx.bm25):
        rows = idx.candidate_rows(subject_id)
        sparse_raw = idx.bm25.score(tokens, candidate_idx=[int(r) for r in rows])

    dense_top = max(dense.values()) if dense else 0.0
    sparse_raw_top = max(sparse_raw.values()) if sparse_raw else 0.0
    # 按查询词数平均，抹掉"查询越长 BM25 越高"的量纲影响
    sparse_per_token = sparse_raw_top / max(1, len(tokens))
    out["dense_top"] = round(float(dense_top), 4)
    out["sparse_top"] = round(float(sparse_raw_top), 4)
    out["sparse_per_token"] = round(float(sparse_per_token), 4)

    alpha = float(config.HYBRID_ALPHA)
    fused = {}
    for cid in set(list(dense.keys()) + list(sparse_rel.keys())):
        d = max(float(dense.get(cid, 0.0)), 0.0)
        s = float(sparse_rel.get(cid, 0.0))
        fused[cid] = alpha * d + (1.0 - alpha) * s

    ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:candidates]

    # 绝对信号判定是否真的检索到了教材内容
    relevant = (dense_top >= config.RELEVANCE_THRESHOLD) or (
        sparse_per_token >= SPARSE_PER_TOKEN_MIN)
    if not relevant or not ranked:
        out["reason"] = ("最高余弦 %.3f < %.2f 且 BM25 按词均值 %.2f < %.2f，"
                         "判定教材中无对应描述"
                         % (dense_top, config.RELEVANCE_THRESHOLD,
                            sparse_per_token, SPARSE_PER_TOKEN_MIN))
        return _finish(out, t0, user_id, log_it)

    hits = []
    for cid, sc in ranked[:top_k]:
        meta = dict(idx.get_meta(cid))
        meta["score"] = round(float(sc), 4)
        meta["dense"] = round(float(dense.get(cid, 0.0)), 4)
        meta["sparse"] = round(float(sparse_raw.get(cid, 0.0)), 4)
        hits.append(meta)

    out["hits"] = hits
    out["top_score"] = hits[0]["score"] if hits else 0.0
    out["refused"] = False
    return _finish(out, t0, user_id, log_it)


def _finish(out, t0, user_id, log_it) -> dict:
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    if log_it:
        try:
            db.execute(
                "INSERT INTO retrieval_logs (user_id, query, subject_id, top_score, "
                "hit_count, refused, elapsed_ms) VALUES (?,?,?,?,?,?,?)",
                (user_id, out["query"][:500], out["subject_id"], out["top_score"],
                 len(out["hits"]), 1 if out["refused"] else 0, out["elapsed_ms"]))
        except Exception as e:
            log.debug("写检索日志失败：%s", e)
    return out


def format_context(hits: list, max_chars=3200) -> str:
    """拼成给大模型的教材上下文，每段带编号与来源，供回答末尾溯源引用。"""
    parts, used = [], 0
    for i, h in enumerate(hits, 1):
        src = " · ".join(x for x in [h.get("subject_name"), h.get("chapter_name"),
                                     h.get("knowledge_point_name")] if x)
        block = "[%d] 来源：%s\n%s" % (i, src or h.get("doc_title") or "教材", h.get("text") or "")
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    return "\n\n".join(parts)


def format_citations(hits: list) -> list:
    """结构化引用，前端渲染"教材来源"折叠区用。"""
    out = []
    for i, h in enumerate(hits, 1):
        out.append({
            "no": i,
            "subject": h.get("subject_name"),
            "chapter": h.get("chapter_name"),
            "knowledge_point": h.get("knowledge_point_name"),
            "doc_title": h.get("doc_title"),
            "score": h.get("score"),
            "excerpt": (h.get("text") or "")[:220],
        })
    return out
