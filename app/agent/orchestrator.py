# -*- coding: utf-8 -*-
"""Agent 编排层：意图识别 → 路由 → 组织回复 → 落库对话历史。

与业务层的边界（需求要求业务层与 Agent 层分离）：
- 本文件不写任何 SQL 业务逻辑，全部通过 app.business.* 与 app.rag.service 调用；
- 题目推送走 business.recommend（四桶配额算法），不是随机取题；
- 知识点答疑与题目答疑一律先走 rag.service 检索，检索不到就返回固定拒答话术，
  不允许把问题直接丢给大模型自由作答；
- 未配置大模型时降级为"纯检索模式"：直接把教材原文片段+来源返回给用户。
"""
import json
import logging
import uuid

import config
from app import db
from app.agent import intent as intent_mod, llm as llm_mod, prompts
from app.business import catalog, questions as q_svc, recommend, wrongbook
from app.rag import service as rag

log = logging.getLogger(__name__)

DEFAULT_PRACTICE_LIMIT = 5
MODE_TEXT = {"recommend": "智能推送", "chapter": "专项章节", "mock": "模拟刷题",
             "wrong": "错题重做", "paper": "组卷练习"}


def new_session_id() -> str:
    return uuid.uuid4().hex[:16]


def _scope_text(scope: dict) -> str:
    names = []
    if scope.get("subject_name"):
        names.append(scope["subject_name"])
    if scope.get("chapter_name"):
        names.append(scope["chapter_name"])
    if scope.get("knowledge_point_name"):
        names.append(scope["knowledge_point_name"])
    return " · ".join(names) if names else "全科目"


def _resolve(det: dict) -> dict:
    """把意图里的中文话术解析成 id，并回填名称，供展示与检索过滤使用。"""
    scope = catalog.resolve_scope(subject=det.get("subject"),
                                  chapter=det.get("chapter"),
                                  knowledge_point=det.get("knowledge_point"))
    if scope.get("subject_id"):
        row = catalog.get_subject(subject_id=scope["subject_id"])
        scope["subject_name"] = row["name"] if row else None
    if scope.get("chapter_id"):
        row = db.query_one("SELECT name FROM chapters WHERE id = ?", (scope["chapter_id"],))
        scope["chapter_name"] = row["name"] if row else None
    if scope.get("knowledge_point_id"):
        row = db.query_one("SELECT name FROM knowledge_points WHERE id = ?",
                           (scope["knowledge_point_id"],))
        scope["knowledge_point_name"] = row["name"] if row else None
    return scope

def _log_turn(user_id, session_id, role, content, intent=None, subject_id=None,
              question_id=None, citations=None, meta=None) -> int:
    return db.execute(
        "INSERT INTO chat_history (user_id, session_id, role, content, intent, "
        "subject_id, question_id, citations_json, meta_json) VALUES (?,?,?,?,?,?,?,?,?)",
        (user_id, session_id, role, content, intent, subject_id, question_id,
         json.dumps(citations, ensure_ascii=False) if citations else None,
         json.dumps(meta, ensure_ascii=False) if meta else None))


def history(user_id: str, session_id=None, limit=50) -> list:
    sql = ("SELECT id, session_id, role, content, intent, subject_id, question_id, "
           "citations_json, meta_json, created_at FROM chat_history WHERE user_id = ?")
    args = [user_id]
    if session_id:
        sql += " AND session_id = ?"
        args.append(session_id)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(int(limit))
    rows = db.query_all(sql, tuple(args))
    for r in rows:
        r["citations"] = json.loads(r.pop("citations_json") or "null")
        r["meta"] = json.loads(r.pop("meta_json") or "null")
    return list(reversed(rows))


def clear_history(user_id: str, session_id=None) -> int:
    with db.transaction() as conn:
        if session_id:
            cur = conn.execute(
                "DELETE FROM chat_history WHERE user_id = ? AND session_id = ?",
                (user_id, session_id))
        else:
            cur = conn.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        return cur.rowcount


def last_intent(user_id: str, session_id: str):
    row = db.query_one(
        "SELECT intent FROM chat_history WHERE user_id = ? AND session_id = ? "
        "AND role = 'assistant' AND intent IS NOT NULL ORDER BY id DESC LIMIT 1",
        (user_id, session_id))
    return row["intent"] if row else None


def last_question_id(user_id: str, session_id: str):
    """用户说"这道题"但前端没传题号时，回溯本会话最近一次推送/讲解过的题目。"""
    row = db.query_one(
        "SELECT question_id FROM chat_history WHERE user_id = ? AND session_id = ? "
        "AND question_id IS NOT NULL ORDER BY id DESC LIMIT 1", (user_id, session_id))
    if row:
        return row["question_id"]
    row = db.query_one(
        "SELECT question_id FROM answer_records WHERE user_id = ? "
        "ORDER BY id DESC LIMIT 1", (user_id,))
    return row["question_id"] if row else None

# ---------------------------------------------------------------------------
# 意图① 刷题
# ---------------------------------------------------------------------------
def _handle_practice(user_id, det, scope) -> dict:
    limit = int(det.get("limit") or DEFAULT_PRACTICE_LIMIT)
    mode = det.get("mode") or "recommend"
    if mode not in MODE_TEXT:
        mode = "recommend"
    if mode == "wrong" and not wrongbook.count_wrong(user_id):
        return {"reply": "你的错题本目前是空的，先做几道题吧。要不要我按知识点给你推送 5 道？",
                "questions": [], "mode": mode, "practice": None}

    res = recommend.recommend(
        user_id, limit=limit, mode=mode, subject_id=scope.get("subject_id"),
        chapter_id=scope.get("chapter_id"),
        knowledge_point_id=scope.get("knowledge_point_id"))
    items = res["questions"]

    qtypes = det.get("qtypes") or []
    if qtypes:  # 用户明确指定题型：先过滤，再按同条件补齐
        items = [q for q in items if q["qtype"] in qtypes]
        if len(items) < limit:
            used = {q["id"] for q in items}
            extra = q_svc.list_questions(
                subject_id=scope.get("subject_id"), chapter_id=scope.get("chapter_id"),
                knowledge_point_id=scope.get("knowledge_point_id"), qtypes=qtypes,
                exclude_ids=list(used) or None, limit=limit * 3)
            for q in extra:
                if len(items) >= limit:
                    break
                q.setdefault("reason", "按你指定的题型补充")
                items.append(q)
        items = items[:limit]

    scope_text = _scope_text(scope)
    if qtypes:
        scope_text += "，题型：" + "/".join(
            q_svc.QTYPE_LABELS.get(t, t) for t in qtypes)
    if not items:
        return {"reply": prompts.render(prompts.PRACTICE_EMPTY_REPLY, scope_text=scope_text),
                "questions": [], "mode": mode, "practice": res}

    buckets = {}
    for q in items:
        buckets[q.get("reason") or "范围内补充题目"] = buckets.get(
            q.get("reason") or "范围内补充题目", 0) + 1
    reason_summary = "选题依据：" + "；".join(
        "%s %d 道" % (k, v) for k, v in buckets.items()) + "。"
    reply = prompts.render(prompts.PRACTICE_REPLY_TEMPLATE, count=len(items),
                           scope_text=scope_text,
                           mode_text=MODE_TEXT.get(mode, mode),
                           reason_summary=reason_summary)
    return {"reply": reply, "questions": items, "mode": mode, "practice": res}

# ---------------------------------------------------------------------------
# 无大模型时的降级作答：直接呈现教材原文 + 来源，绝不自行组织知识内容
# ---------------------------------------------------------------------------
def _retrieval_only_answer(hits, header) -> str:
    lines = [header, ""]
    for i, h in enumerate(hits, 1):
        src = " · ".join(x for x in [h.get("subject_name"), h.get("chapter_name"),
                                     h.get("knowledge_point_name")] if x)
        lines.append("【片段 %d】%s" % (i, src or h.get("doc_title") or "教材"))
        lines.append((h.get("text") or "").strip())
        lines.append("")
    lines.append("【引用来源】")
    for i, h in enumerate(hits, 1):
        src = " · ".join(x for x in [h.get("subject_name"), h.get("chapter_name"),
                                     h.get("knowledge_point_name")] if x)
        lines.append("[%d] %s" % (i, src or h.get("doc_title") or "教材"))
    return "\n".join(lines).strip()


DEGRADE_HEADER = ("当前未配置大模型 API，我以「纯检索模式」把教材原文原样返回给你"
                  "（内容未经改写，可直接对照）：")


# ---------------------------------------------------------------------------
# 意图② 知识点答疑（强制 RAG）
# ---------------------------------------------------------------------------
def _handle_knowledge_qa(user_id, det, scope, top_k=None) -> dict:
    query = det.get("query") or ""
    rag_res = rag.retrieve_context(query, subject=scope.get("subject_id"),
                                   top_k=top_k, user_id=user_id)
    if not rag_res["found"]:
        return {"reply": rag_res["message"] or prompts.NO_CONTEXT_REPLY,
                "citations": [], "rag": rag_res.get("debug"), "refused": True,
                "llm_used": False}

    client = llm_mod.get_client("qa")
    if client.available():
        user_msg = prompts.render(prompts.RAG_QA_USER_TEMPLATE, question=query,
                                  context=rag_res["context"])
        try:
            text = client.chat(prompts.RAG_QA_SYSTEM_PROMPT, user_msg)
            return {"reply": text, "citations": rag_res["citations"],
                    "rag": rag_res.get("debug"), "refused": False, "llm_used": True,
                    "model": client.model}
        except llm_mod.LLMError as exc:
            log.warning("答疑调用大模型失败，降级为纯检索：%s", exc)
            note = "（大模型调用失败：%s，已降级为纯检索模式）\n\n" % exc
            return {"reply": note + _retrieval_only_answer(rag_res["hits"], DEGRADE_HEADER),
                    "citations": rag_res["citations"], "rag": rag_res.get("debug"),
                    "refused": False, "llm_used": False, "degraded": True}

    return {"reply": _retrieval_only_answer(rag_res["hits"], DEGRADE_HEADER),
            "citations": rag_res["citations"], "rag": rag_res.get("debug"),
            "refused": False, "llm_used": False, "degraded": True}

# ---------------------------------------------------------------------------
# 意图③ 针对某道具体题目提问（题目+解析上下文 + 教材 RAG 拓展）
# ---------------------------------------------------------------------------
def _fmt_options(q) -> str:
    opts = q.get("options")
    if not opts:
        return "（本题无选项）"
    out = []
    for o in opts:
        if isinstance(o, dict):
            out.append("%s. %s" % (o.get("key"), o.get("text")))
        elif isinstance(o, (list, tuple)) and len(o) >= 2:
            out.append("%s. %s" % (o[0], o[1]))
        else:
            out.append(str(o))
    return "\n".join(out)


def _pick_question(user_id, session_id, det, current_question_id):
    ref = det.get("question_ref")
    if ref and str(ref).isdigit():
        q = q_svc.get_question(int(ref), with_answer=True)
        if q:
            return q
    if current_question_id:
        q = q_svc.get_question(int(current_question_id), with_answer=True)
        if q:
            return q
    qid = last_question_id(user_id, session_id)
    return q_svc.get_question(int(qid), with_answer=True) if qid else None


def _handle_question_qa(user_id, session_id, det, current_question_id, top_k=None) -> dict:
    q = _pick_question(user_id, session_id, det, current_question_id)
    if not q:
        return {"reply": "我还不知道你指的是哪道题。你可以先让我推送题目（例如"
                         "「给我推送会计收入章节的题」），或者直接把题号告诉我，"
                         "例如「题目12 为什么选C」。",
                "citations": [], "question": None, "llm_used": False}

    scope_text = " · ".join(x for x in [q.get("subject_name"), q.get("chapter_name"),
                                        q.get("knowledge_point_name")] if x)
    # 检索式：知识点名 + 题干（截断）+ 用户疑问，保证能命中教材原文
    query = " ".join(x for x in [q.get("knowledge_point_name"), q.get("chapter_name"),
                                 (q.get("stem") or "")[:80],
                                 det.get("query") or ""] if x)
    rag_res = rag.retrieve_context(query, subject=q.get("subject_id"),
                                   top_k=top_k, user_id=user_id)
    context = rag_res["context"] if rag_res["found"] else "（知识库中未检索到更多相关教材原文）"
    citations = rag_res["citations"] if rag_res["found"] else []

    answer = q.get("answer")
    answer_text = "、".join(answer) if isinstance(answer, list) else str(answer)
    client = llm_mod.get_client("question_explain")
    if client.available():
        user_msg = prompts.render(
            prompts.QUESTION_QA_USER_TEMPLATE, question_id=q["id"], scope=scope_text,
            qtype=q.get("qtype_label"), difficulty_label=q.get("difficulty_label"),
            stem=q.get("stem"), options=_fmt_options(q), answer=answer_text,
            analysis=q.get("analysis"), question=det.get("query") or "请讲解这道题",
            context=context)
        try:
            text = client.chat(prompts.QUESTION_QA_SYSTEM_PROMPT, user_msg)
            return {"reply": text, "citations": citations, "question": q,
                    "rag": rag_res.get("debug"), "llm_used": True, "model": client.model}
        except llm_mod.LLMError as exc:
            log.warning("题目讲解调用大模型失败，降级：%s", exc)

    # 降级：标准解析 + 教材原文，不做任何自由生成
    lines = ["【题目】%s（%s · 难度%s）" % (q["id"], q.get("qtype_label"),
                                       q.get("difficulty_label")),
             "考点定位：" + (scope_text or "-"), "",
             q.get("stem") or "", _fmt_options(q), "",
             "【正确答案】" + answer_text, "",
             "【标准解析】", (q.get("analysis") or "").strip(), ""]
    if rag_res["found"]:
        lines.append(_retrieval_only_answer(rag_res["hits"], "【教材原文拓展】"))
    else:
        lines.append("【教材原文拓展】" + prompts.NO_CONTEXT_REPLY)
    return {"reply": "\n".join(lines).strip(), "citations": citations, "question": q,
            "rag": rag_res.get("debug"), "llm_used": False, "degraded": True}

# ---------------------------------------------------------------------------
# 对外唯一入口
# ---------------------------------------------------------------------------
def chat(message: str, user_id: str = None, session_id: str = None,
         current_question_id=None, top_k=None) -> dict:
    """处理一轮对话。返回结构直接供 /api/chat 与前端聊天框使用。"""
    user_id = user_id or config.DEFAULT_USER_ID
    session_id = session_id or new_session_id()
    message = (message or "").strip()
    if not message:
        return {"session_id": session_id, "intent": "other",
                "reply": prompts.OUT_OF_SCOPE_REPLY, "questions": [], "citations": [],
                "question": None, "scope": {}, "llm_used": False}

    prev = last_intent(user_id, session_id)
    if not current_question_id:
        current_question_id = None
    det = intent_mod.detect(message, current_question_id=current_question_id,
                            last_intent=prev)
    scope = _resolve(det)
    _log_turn(user_id, session_id, "user", message, intent=det["intent"],
              subject_id=scope.get("subject_id"),
              question_id=int(current_question_id) if current_question_id else None,
              meta={"detector": det.get("source"), "detected": det})

    out = {"session_id": session_id, "intent": det["intent"],
           "detector": det.get("source"), "scope": scope, "questions": [],
           "citations": [], "question": None, "mode": None, "llm_used": False,
           "degraded": False, "refused": False, "rag": None}

    if det["intent"] == "practice":
        res = _handle_practice(user_id, det, scope)
        out.update({"reply": res["reply"], "questions": res["questions"],
                    "mode": res.get("mode"), "practice": res.get("practice")})
    elif det["intent"] == "question_qa":
        res = _handle_question_qa(user_id, session_id, det, current_question_id,
                                  top_k=top_k)
        out.update({"reply": res["reply"], "citations": res.get("citations") or [],
                    "question": res.get("question"), "rag": res.get("rag"),
                    "llm_used": res.get("llm_used", False),
                    "degraded": res.get("degraded", False),
                    "model": res.get("model")})
    elif det["intent"] == "other":
        out["reply"] = prompts.OUT_OF_SCOPE_REPLY
    else:
        res = _handle_knowledge_qa(user_id, det, scope, top_k=top_k)
        out.update({"reply": res["reply"], "citations": res.get("citations") or [],
                    "rag": res.get("rag"), "refused": res.get("refused", False),
                    "llm_used": res.get("llm_used", False),
                    "degraded": res.get("degraded", False),
                    "model": res.get("model")})

    qid = None
    if out.get("question"):
        qid = out["question"]["id"]
    elif out["questions"]:
        qid = out["questions"][0]["id"]
    out["message_id"] = _log_turn(
        user_id, session_id, "assistant", out["reply"], intent=det["intent"],
        subject_id=scope.get("subject_id"), question_id=qid,
        citations=out["citations"] or None,
        meta={"llm_used": out.get("llm_used"), "degraded": out.get("degraded"),
              "refused": out.get("refused"), "model": out.get("model"),
              "mode": out.get("mode"), "question_ids": [q["id"] for q in out["questions"]],
              "rag": out.get("rag")})
    return out


def status() -> dict:
    """聊天框顶部的能力提示：大模型是否接入、知识库是否就绪。"""
    return {"llm": llm_mod.llm_status(), "rag": rag.health(),
            "intents": {"practice": "刷题（推送/章节/模拟/错题）",
                        "knowledge_qa": "知识点答疑（RAG 教材溯源）",
                        "question_qa": "针对某道题提问"}}





