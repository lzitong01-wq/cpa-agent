# -*- coding: utf-8 -*-
"""BM25 关键词检索（jieba 分词，纯 Python 实现）。

作用：和稠密向量互补。术语类提问（"暂时性差异""进项税额抵扣"）关键词命中更准，
换了说法的提问靠向量召回，两路融合后再截断，能显著降低"检索不到 → 拒答"的误伤。
"""
import math
import re
from collections import Counter

_STOP = set("的 了 和 与 及 或 是 在 对 为 以 及其 等 中 上 下 由 从 并 而 也 都 就 "
            "这 那 其 之 一个 一种 可以 应当 需要 如果 但是 因此 所以 关于 下列 各项 "
            "说法 正确 错误 属于 包括 什么 怎么 如何 为什么 请 我 你 他".split())
_NON_WORD = re.compile(r"[\s\W_]+", re.UNICODE)


def tokenize(text: str) -> list:
    """jieba 精确模式 + 停用词过滤；jieba 不可用时退化为二元字切分。"""
    text = str(text or "")
    try:
        import jieba
        words = [w.strip() for w in jieba.cut(text)]
    except Exception:
        words = _NON_WORD.split(text)
    out = []
    for w in words:
        w = w.strip().lower()
        if not w or w in _STOP or _NON_WORD.fullmatch(w):
            continue
        if len(w) == 1 and not w.isalnum():
            continue
        out.append(w)
    # 补充二元字组合，提高中文短语命中率
    zh = re.sub(r"[^一-鿿]", "", text)
    out.extend(zh[i:i + 2] for i in range(len(zh) - 1))
    return out


class BM25Index(object):
    """经典 BM25（k1=1.5, b=0.75）。文档量在万级以内，纯 Python 完全够用。"""

    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids = []
        self.doc_tf = []       # [Counter]
        self.doc_len = []
        self.df = Counter()
        self.avgdl = 0.0
        self.n = 0

    def add(self, doc_id, tokens) -> None:
        tf = Counter(tokens)
        self.doc_ids.append(doc_id)
        self.doc_tf.append(tf)
        self.doc_len.append(sum(tf.values()) or 1)
        for t in tf:
            self.df[t] += 1

    def build(self) -> "BM25Index":
        self.n = len(self.doc_ids)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 1.0
        self._idf = {}
        for t, df in self.df.items():
            # 加一平滑，避免高频词出现负 idf
            self._idf[t] = math.log(1.0 + (self.n - df + 0.5) / (df + 0.5))
        return self

    def score(self, query_tokens, candidate_idx=None) -> dict:
        if not self.n:
            return {}
        q = Counter(query_tokens)
        idxs = range(self.n) if candidate_idx is None else candidate_idx
        out = {}
        for i in idxs:
            tf = self.doc_tf[i]
            dl = self.doc_len[i]
            s = 0.0
            for t, qc in q.items():
                f = tf.get(t)
                if not f:
                    continue
                idf = self._idf.get(t, 0.0)
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                s += idf * (f * (self.k1 + 1)) / denom
            if s > 0:
                out[self.doc_ids[i]] = s
        return out

    def score_normalized(self, query_tokens, candidate_idx=None) -> dict:
        """归一化到 0~1，便于和余弦相似度加权融合。"""
        raw = self.score(query_tokens, candidate_idx)
        if not raw:
            return {}
        top = max(raw.values())
        if top <= 0:
            return {}
        return {k: v / top for k, v in raw.items()}

    def __len__(self):
        return len(self.doc_ids)
