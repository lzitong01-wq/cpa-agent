# -*- coding: utf-8 -*-
"""教材切分：按标题层级带出检索元数据（科目 / 章节 / 知识点）。

约定的教材文本格式（.md / .txt 同样适用）：
    # 会计                     -> 科目
    ## 第十八章 所得税          -> 章节
    ### 暂时性差异的确认        -> 知识点
    正文段落……
PDF/DOCX 抽出的纯文本没有 # 标记时，退化为按"第X章""第X节"正则识别，
识别不到则元数据留空，检索时仍可用但引用只能标到文档级。
"""
import re

import config

H1 = re.compile(r"^#\s+(.+?)\s*$")
H2 = re.compile(r"^##\s+(.+?)\s*$")
H3 = re.compile(r"^###\s+(.+?)\s*$")
CHAPTER_PAT = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+章\s*.{0,40})\s*$")
SECTION_PAT = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+节\s*.{0,40})\s*$")


def _flush(buf, meta, ord_counter, out):
    text = "\n".join(buf).strip()
    if not text:
        return
    # 检索用文本额外带上"科目 · 章节 · 知识点"面包屑：
    # 用户按知识点名提问（如"五力模型"）时，命中的正文可能压根没重复标题里的词，
    # 带上面包屑能同时抬高稠密相似度与 BM25 命中，避免被误判成"教材里没有"。
    crumb = " · ".join(x for x in [meta.get("subject"), meta.get("chapter"),
                                   meta.get("kp")] if x)
    out.append({
        "ord": ord_counter,
        "text": text,
        "search_text": (crumb + "\n" + text) if crumb else text,
        "char_count": len(text),
        "subject_name": meta.get("subject"),
        "chapter_name": meta.get("chapter"),
        "knowledge_point_name": meta.get("kp"),
    })


def split_text(raw: str, default_subject: str = None, chunk_size: int = None,
               overlap: int = None) -> list:
    """返回 [{ord, text, char_count, subject_name, chapter_name, knowledge_point_name}]。"""
    chunk_size = int(chunk_size or config.CHUNK_SIZE)
    overlap = int(overlap or config.CHUNK_OVERLAP)
    meta = {"subject": default_subject, "chapter": None, "kp": None}
    chunks, buf, size, ordn = [], [], 0, 0
    seen_section = False  # 是否已经进入正文章节（用于识别文档开头的说明性引用块）

    def close(carry_overlap=True):
        """落一个切片。

        carry_overlap=True  仅因长度超限而切分，下一片带上尾部重叠，保住句子连贯；
        carry_overlap=False 遇到标题换段，不能把上一节的尾巴带进新节，
                            否则新切片的正文开头是别的知识点的内容，引用来源就标错了。
        """
        nonlocal buf, size, ordn
        if buf:
            _flush(buf, meta, ordn, chunks)
            ordn += 1
            tail = "\n".join(buf)[-overlap:] if (overlap and carry_overlap) else ""
            buf = [tail] if tail.strip() else []
            size = len(tail)

    for line in (raw or "").splitlines():
        s = line.strip()
        if not s:
            continue

        m3 = H3.match(s)
        m2 = H2.match(s)
        m1 = H1.match(s)
        if m1:
            close(carry_overlap=False)
            meta.update(subject=m1.group(1), chapter=None, kp=None)
            continue
        if m2:
            close(carry_overlap=False)
            meta.update(chapter=m2.group(1), kp=None)
            seen_section = True
            continue
        if m3:
            close(carry_overlap=False)
            meta["kp"] = m3.group(1)
            seen_section = True
            continue
        if CHAPTER_PAT.match(s) and len(s) < 45:
            close(carry_overlap=False)
            meta.update(chapter=CHAPTER_PAT.match(s).group(1).strip(), kp=None)
            seen_section = True
            continue
        if SECTION_PAT.match(s) and len(s) < 45:
            close(carry_overlap=False)
            meta["kp"] = SECTION_PAT.match(s).group(1).strip()
            seen_section = True
            continue
        # 文档开头的引用块通常是版本说明/免责声明，不属于教材正文，跳过，
        # 避免它们被切成独立片段后在模糊查询下抢占检索排名。
        # 正文内的引用块（法条摘录等）照常保留。
        if s.startswith(">") and not seen_section:
            continue

        # 超长段落先按句号切开，避免单块过大
        for piece in _split_sentences(s, chunk_size):
            if size + len(piece) > chunk_size and buf:
                close()
            buf.append(piece)
            size += len(piece)

    if buf:
        _flush(buf, meta, ordn, chunks)
    return [c for c in chunks if c["char_count"] >= 20]


def _split_sentences(s: str, limit: int) -> list:
    if len(s) <= limit:
        return [s]
    parts, cur = [], ""
    for seg in re.split(r"(?<=[。；！？])", s):
        if not seg:
            continue
        if len(cur) + len(seg) > limit and cur:
            parts.append(cur)
            cur = seg
        else:
            cur += seg
    if cur:
        parts.append(cur)
    return parts
