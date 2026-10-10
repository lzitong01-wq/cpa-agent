# -*- coding: utf-8 -*-
"""RAG 独立服务接口层。

对上只暴露这一个模块：Agent 层、HTTP API、命令行脚本都调这里，
内部换向量后端 / 换检索策略不影响调用方。
也可以把本文件单独抽出去部署成独立微服务（对应 app/api/rag_api.py 的 HTTP 路由）。
"""
import logging

import config
from app.business import catalog
from app.rag import embedder as embedder_mod, index as index_mod, ingest, retriever

log = logging.getLogger(__name__)

NO_CONTEXT_MESSAGE = retriever.NO_CONTEXT_MESSAGE


def resolve_subject(subject=None):
    """接受科目 id / code / 中文名，统一返回 subject_id。"""
    if subject in (None, "", "all", "全部"):
        return None
    scope = catalog.resolve_scope(subject=subject)
    return scope.get("subject_id")


def search(query: str, subject=None, top_k=None, user_id=None) -> dict:
    """检索教材片段。返回结构见 retriever.search。"""
    return retriever.search(query, subject_id=resolve_subject(subject),
                            top_k=top_k, user_id=user_id)


def retrieve_context(query: str, subject=None, top_k=None, user_id=None,
                     max_chars=3200) -> dict:
    """检索 + 拼上下文 + 结构化引用，供 Agent 直接塞进 prompt。"""
    res = search(query, subject=subject, top_k=top_k, user_id=user_id)
    if res["refused"]:
        return {
            "found": False,
            "context": "",
            "citations": [],
            "message": NO_CONTEXT_MESSAGE,
            "debug": {k: res[k] for k in
                      ("dense_top", "sparse_top", "sparse_per_token", "reason",
                       "backend", "elapsed_ms")},
        }
    return {
        "found": True,
        "context": retriever.format_context(res["hits"], max_chars=max_chars),
        "citations": retriever.format_citations(res["hits"]),
        "hits": res["hits"],
        "message": "",
        "debug": {k: res[k] for k in
                  ("top_score", "dense_top", "sparse_top", "sparse_per_token",
                   "backend", "elapsed_ms")},
    }


def reindex(force=False, corpus_dir=None) -> dict:
    """重新扫描语料目录入库并重建索引。"""
    return ingest.ingest_dir(root=corpus_dir, force=force)


def reload_index() -> dict:
    return index_mod.get_index(force_reload=True).stats()


def health() -> dict:
    idx = index_mod.get_index()
    emb = embedder_mod.get_embedder()
    st = idx.stats()
    return {
        "ok": st["chunks"] > 0,
        "embedding_backend": emb.name,
        "embedding_dim": getattr(emb, "dim", 0),
        "index": st,
        "corpus_dir": config.CORPUS_DIR,
        "thresholds": {
            "relevance": config.RELEVANCE_THRESHOLD,
            "sparse_per_token_min": retriever.SPARSE_PER_TOKEN_MIN,
            "hybrid_alpha": config.HYBRID_ALPHA,
            "top_k": config.RETRIEVE_TOP_K,
        },
        "hint": "" if st["chunks"] else "知识库为空，执行 python scripts/ingest_corpus.py",
    }


def list_docs() -> list:
    """学员可见的教材清单：只列参与检索的（管理员下架的不出现）。

    管理员视角的完整清单（含下架、来源、上传者）见 app.rag.uploads.list_docs()。
    """
    from app import db
    return db.query_all(
        "SELECT d.id, d.title, d.subject_id, s.name AS subject_name, d.char_count, "
        "d.chunk_count, d.ingested_at "
        "FROM corpus_docs d LEFT JOIN subjects s ON s.id = d.subject_id "
        "WHERE d.visibility = 'public' "
        "ORDER BY d.id")
