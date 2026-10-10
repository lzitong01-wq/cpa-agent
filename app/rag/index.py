# -*- coding: utf-8 -*-
"""语料索引：把教材切片的稠密向量与 BM25 倒排一次性加载进内存。

- 向量存 SQLite 的 chunk_vectors.vec（float32 小端 BLOB），加载时
  numpy.frombuffer 零拷贝还原成矩阵，检索走一次矩阵乘法；
- BM25 用 corpus_chunks.tokens_json 里缓存的分词结果重建，避免重复分词；
- 切片行数变化时自动重建索引（入库脚本跑完不需要重启服务）。
"""
import json
import logging
import threading

import numpy as np

from app import db
from app.rag import bm25 as bm25_mod

log = logging.getLogger(__name__)
_lock = threading.Lock()
_index = None


class CorpusIndex(object):
    def __init__(self):
        self.chunk_ids = []          # 行号 -> chunk_id
        self.row_of = {}             # chunk_id -> 行号
        self.meta = []               # 行号 -> 元数据 dict
        self.matrix = None           # (n, dim) float32，已 L2 归一化
        self.subject_ids = None      # (n,) int32，-1 表示未标注科目
        self.bm25 = None
        self.dim = 0
        self.model = ""
        self.built_rows = 0

    # -------------------------------------------------------------- 加载
    def load(self) -> "CorpusIndex":
        rows = db.query_all(
            "SELECT c.id, c.doc_id, c.subject_id, c.subject_name, c.chapter_name, "
            "c.knowledge_point_name, c.text, c.tokens_json, d.title, "
            "v.vec, v.dim, v.model "
            "FROM corpus_chunks c "
            "JOIN corpus_docs d ON d.id = c.doc_id "
            "LEFT JOIN chunk_vectors v ON v.chunk_id = c.id "
            "WHERE d.visibility = 'public' "     # 管理员下架的教材不参与检索
            "ORDER BY c.id")
        self.chunk_ids, self.meta, self.row_of = [], [], {}
        vecs, subs = [], []
        idx = bm25_mod.BM25Index()
        dim = 0
        for r in rows:
            row_no = len(self.chunk_ids)
            self.chunk_ids.append(r["id"])
            self.row_of[r["id"]] = row_no
            self.meta.append({
                "chunk_id": r["id"],
                "doc_id": r["doc_id"],
                "doc_title": r["title"],
                "subject_id": r["subject_id"],
                "subject_name": r["subject_name"],
                "chapter_name": r["chapter_name"],
                "knowledge_point_name": r["knowledge_point_name"],
                "text": r["text"],
            })
            subs.append(r["subject_id"] if r["subject_id"] is not None else -1)
            if r["vec"]:
                dim = dim or int(r["dim"])
                self.model = self.model or (r["model"] or "")
                vecs.append(np.frombuffer(r["vec"], dtype="float32"))
            else:
                vecs.append(None)
            try:
                toks = json.loads(r["tokens_json"]) if r["tokens_json"] else None
            except Exception:
                toks = None
            idx.add(r["id"], toks if toks else bm25_mod.tokenize(r["text"]))

        self.dim = dim
        n = len(self.chunk_ids)
        if n and dim:
            mat = np.zeros((n, dim), dtype="float32")
            for i, v in enumerate(vecs):
                if v is not None and v.shape[0] == dim:
                    mat[i] = v
            self.matrix = mat
        else:
            self.matrix = np.zeros((n, dim or 1), dtype="float32")
        self.subject_ids = np.asarray(subs, dtype="int32")
        self.bm25 = idx.build()
        self.built_rows = n
        log.info("语料索引就绪：%d 个切片，向量维度 %s，模型 %s", n, dim, self.model or "-")
        return self

    # -------------------------------------------------------------- 检索
    def candidate_rows(self, subject_id=None) -> np.ndarray:
        if subject_id is None:
            return np.arange(len(self.chunk_ids), dtype="int64")
        return np.nonzero(self.subject_ids == int(subject_id))[0]

    def dense_scores(self, query_vec: np.ndarray, subject_id=None) -> dict:
        """余弦相似度（向量已归一化，点乘即余弦）。"""
        if self.matrix is None or not len(self.chunk_ids) or self.matrix.shape[1] < 1:
            return {}
        rows = self.candidate_rows(subject_id)
        if rows.size == 0:
            return {}
        q = np.asarray(query_vec, dtype="float32").reshape(-1)
        if q.shape[0] != self.matrix.shape[1]:
            return {}   # 维度不匹配（换过嵌入模型），交给 BM25 兜底
        sims = self.matrix[rows] @ q
        return {self.chunk_ids[int(r)]: float(s) for r, s in zip(rows, sims)}

    def sparse_scores(self, query_tokens, subject_id=None) -> dict:
        if self.bm25 is None or not len(self.bm25):
            return {}
        rows = self.candidate_rows(subject_id)
        return self.bm25.score_normalized(query_tokens, candidate_idx=[int(r) for r in rows])

    def get_meta(self, chunk_id: int) -> dict:
        row = self.row_of.get(chunk_id)
        return self.meta[row] if row is not None else {}

    def stats(self) -> dict:
        return {
            "chunks": len(self.chunk_ids),
            "dim": self.dim,
            "model": self.model,
            "vectorized": int((np.linalg.norm(self.matrix, axis=1) > 0).sum())
            if self.matrix is not None and self.matrix.size else 0,
            "subjects": sorted({int(s) for s in self.subject_ids.tolist()}) if
            self.subject_ids is not None and self.subject_ids.size else [],
        }


def visible_chunk_count() -> int:
    """参与检索的切片数。管理员新增/删除/上下架教材都会改变这个数，
    多进程部署时各 worker 靠它自动发现索引过期，不需要互相通知。"""
    return db.query_scalar(
        "SELECT COUNT(*) FROM corpus_chunks c JOIN corpus_docs d ON d.id = c.doc_id "
        "WHERE d.visibility = 'public'", (), 0)


def get_index(force_reload=False) -> CorpusIndex:
    """进程级单例；参与检索的切片数变化时自动重建。"""
    global _index
    with _lock:
        if _index is None or force_reload:
            _index = CorpusIndex().load()
            return _index
        current = visible_chunk_count()
        if current != _index.built_rows:
            log.info("检测到切片数变化 %d -> %d，重建索引", _index.built_rows, current)
            _index = CorpusIndex().load()
        return _index


def invalidate() -> None:
    global _index
    with _lock:
        _index = None
