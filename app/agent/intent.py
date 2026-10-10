# -*- coding: utf-8 -*-
"""意图识别：把自然语言分派到三类业务意图。

三类业务意图（需求规定）：
- practice       刷题：调题库、触发推送算法、进入答题流程
- knowledge_qa   知识点答疑：走 RAG 检索教材原文作答
- question_qa    针对某道具体题目提问：题目+解析上下文 + 教材 RAG 拓展
另有一个非业务分支 other（问候/超范围），返回引导话术，不占用上述三类语义。

两层实现：
1. 规则层（本文件 rule_detect）：零依赖、零成本、无需 API key 即可工作，
   并负责从库里真实存在的章节/知识点名称中做实体链接；
2. 大模型层（llm_detect）：配置了 api_key 时优先使用，输出 JSON；
   解析失败或调用失败自动回落到规则层，绝不阻断对话。
"""
import logging
import re

from app.agent import llm as llm_mod, prompts
from app.business import catalog

log = logging.getLogger(__name__)

INTENTS = ("practice", "knowledge_qa", "question_qa", "other")

# 科目别名 → 库中的科目 name
SUBJECT_ALIASES = {
    "会计": "会计", "中级会计": "会计", "财务会计": "会计",
    "审计": "审计", "审计学": "审计",
    "财管": "财管", "财务成本管理": "财管", "财务管理": "财管", "成本管理": "财管",
    "税法": "税法", "税务": "税法", "涉税": "税法",
    "经济法": "经济法", "法律": "经济法",
    "战略": "战略", "公司战略": "战略", "公司战略与风险管理": "战略",
    "风险管理": "战略", "战略与风险管理": "战略",
}

QTYPE_ALIASES = [
    (("单选", "单项选择", "单选题"), "single"),
    (("多选", "多项选择", "多选题", "不定项"), "multiple"),
    (("判断", "判断题", "对错题"), "judge"),
    (("简答", "简答题", "问答题"), "short"),
    (("综合", "综合题", "案例题", "大题"), "comprehensive"),
]

PRACTICE_WORDS = ("刷题", "做题", "做几道", "做一道", "出题", "出几道", "推送", "推荐几道",
                  "来几道", "来一道", "来十道", "练习", "练一练", "练几道", "考考我",
                  "测试一下", "模拟", "模考", "套卷", "组卷", "试卷", "题目给我",
                  "给我题", "抽题", "复习题", "巩固")
WRONG_WORDS = ("错题", "做错的", "错的题", "重做")
MOCK_WORDS = ("模拟", "模考", "套卷", "整套", "全真")
QUESTION_REF_WORDS = ("这道题", "这题", "该题", "此题", "这一题", "上一题", "上道题",
                      "刚才那道", "刚那题", "刚才那题", "那道题", "本题")
QUESTION_ASK_WORDS = ("为什么选", "为什么不选", "为什么是", "怎么选", "没看懂", "看不懂",
                      "不明白", "不懂", "解析", "答案错了", "答案不对", "讲讲", "再讲",
                      "怎么算的", "怎么来的")
GREET_WORDS = ("你好", "您好", "hi", "hello", "嗨", "在吗", "你是谁", "能做什么",
               "怎么用", "帮助", "help")

CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
          "八": 8, "九": 9, "十": 10, "十五": 15, "二十": 20, "三十": 30}

_QNO_RE = re.compile(r"第\s*([0-9一二三四五六七八九十]+)\s*题")
_QID_RE = re.compile(r"(?:题目\s*(?:id|ID|编号)?\s*[:：#]?\s*)(\d{1,6})")
_NUM_RE = re.compile(r"([0-9]{1,3})\s*(?:道|题|个)")
_CN_NUM_RE = re.compile(r"([一二两三四五六七八九十]+)\s*(?:道|题)")
# "来5道""出十道""给我三题"这类量词结构本身就是刷题诉求，
# 不依赖动词表，避免"来5道税法的多选"被误判成知识点答疑。
_QUANTITY_RE = re.compile(r"[0-9一二两三四五六七八九十]+\s*道")
_QUANTITY_VERB_RE = re.compile(
    r"(来|给我|给|出|做|抽|考|练|整|发)\s*[0-9一二两三四五六七八九十]+\s*[道题个]")


def _blank_result(message=""):
    return {"intent": "knowledge_qa", "subject": None, "chapter": None,
            "knowledge_point": None, "qtypes": [], "limit": None, "mode": None,
            "question_ref": None, "query": (message or "").strip(), "source": "rule"}


def _pick_limit(text):
    m = _NUM_RE.search(text)
    if m:
        n = int(m.group(1))
        return max(1, min(n, 50))
    m = _CN_NUM_RE.search(text)
    if m:
        n = CN_NUM.get(m.group(1))
        if n:
            return max(1, min(n, 50))
    return None


def _pick_subject(text):
    """按别名长度倒序匹配，避免"财务管理"被"会计"之类的短词抢先。"""
    for alias in sorted(SUBJECT_ALIASES, key=len, reverse=True):
        if alias in text:
            return SUBJECT_ALIASES[alias]
    return None


def _pick_qtypes(text):
    out = []
    for words, code in QTYPE_ALIASES:
        if any(w in text for w in words) and code not in out:
            out.append(code)
    return out

def _strip_chapter_prefix(name):
    return re.sub(r"^第[0-9一二三四五六七八九十百]+[章节篇]\s*", "", name or "").strip()


def _link_entities(text, subject_name=None):
    """把用户话术链接到库里真实存在的章节 / 知识点名称。

    只做字面（含去掉"第X章"前缀后的字面）匹配，取最长命中，
    保证"给我推送会计收入章节的题"能定位到 会计 → 收入。
    """
    subject_id = None
    if subject_name:
        subject_id = catalog.resolve_scope(subject=subject_name).get("subject_id")

    def _best(rows):
        hit, hit_len = None, 0
        for r in rows:
            for cand in {r["name"], _strip_chapter_prefix(r["name"])}:
                if cand and len(cand) >= 2 and cand in text and len(cand) > hit_len:
                    hit, hit_len = r, len(cand)
        return hit

    chapter = _best(catalog.list_chapters(subject_id=subject_id))
    kp_rows = catalog.list_knowledge_points(
        chapter_id=chapter["id"] if chapter else None,
        subject_id=None if chapter else subject_id)
    kp = _best(kp_rows)
    # kp 命中但章节没命中时不强行猜章节，交给 catalog.resolve_scope 由知识点反推
    return (chapter["name"] if chapter else None), (kp["name"] if kp else None)


def rule_detect(message, current_question_id=None, last_intent=None) -> dict:
    """纯规则意图识别。无需任何外部依赖，是系统的默认路径与兜底路径。"""
    text = (message or "").strip()
    res = _blank_result(text)
    if not text:
        res["intent"] = "other"
        return res
    low = text.lower()

    res["subject"] = _pick_subject(text)
    res["chapter"], res["knowledge_point"] = _link_entities(text, res["subject"])
    res["qtypes"] = _pick_qtypes(text)
    res["limit"] = _pick_limit(text)

    m = _QID_RE.search(text) or _QNO_RE.search(text)
    if m:
        res["question_ref"] = m.group(1)

    refers_question = bool(res["question_ref"]) or any(w in text for w in QUESTION_REF_WORDS)
    asks_about_question = any(w in text for w in QUESTION_ASK_WORDS)
    wants_practice = (any(w in text for w in PRACTICE_WORDS)
                      or any(w in text for w in WRONG_WORDS)
                      or bool(_QUANTITY_RE.search(text))
                      or bool(_QUANTITY_VERB_RE.search(text)))

    if wants_practice and not (refers_question and asks_about_question):
        res["intent"] = "practice"
        if any(w in text for w in WRONG_WORDS):
            res["mode"] = "wrong"
        elif any(w in text for w in MOCK_WORDS):
            res["mode"] = "mock"
        elif res["chapter"] or res["knowledge_point"]:
            res["mode"] = "chapter"
        else:
            res["mode"] = "recommend"
    elif refers_question or (asks_about_question and current_question_id):
        res["intent"] = "question_qa"
        if not res["question_ref"] and current_question_id:
            res["question_ref"] = str(current_question_id)
    elif any(w in low for w in GREET_WORDS) and len(text) <= 12:
        res["intent"] = "other"
    else:
        res["intent"] = "knowledge_qa"

    # 检索式：去掉口语壳，保留专业术语
    q = re.sub(r"(请问|帮我|麻烦|我想问|我想知道|一下|谢谢|呀|吧|啊|呢|哦|嘛)", "", text)
    q = re.sub(r"^(什么是|解释|讲讲|说说|介绍)", "", q).strip(" ？?。，,、")
    res["query"] = q or text
    return res


def llm_detect(message, current_question_id=None, last_intent=None):
    """大模型意图识别；不可用或解析失败返回 None，由 rule_detect 兜底。"""
    client = llm_mod.get_client("intent")
    if not client.available():
        return None
    user = prompts.render(prompts.INTENT_USER_TEMPLATE, message=message,
                          current_question_id=current_question_id or "无",
                          last_intent=last_intent or "无")
    try:
        data = client.chat_json(prompts.INTENT_SYSTEM_PROMPT, user)
    except llm_mod.LLMError as exc:
        log.warning("意图识别调用大模型失败，回落规则：%s", exc)
        return None
    if not isinstance(data, dict) or data.get("intent") not in INTENTS:
        log.warning("意图识别返回不可用，回落规则：%r", data)
        return None
    out = _blank_result(message)
    out.update({k: data.get(k) for k in
                ("intent", "subject", "chapter", "knowledge_point", "mode",
                 "question_ref", "query")})
    qtypes = data.get("qtypes") or []
    valid = {c for _, c in QTYPE_ALIASES}
    out["qtypes"] = [t for t in qtypes if t in valid]
    limit = data.get("limit")
    out["limit"] = max(1, min(int(limit), 50)) if isinstance(limit, int) else None
    out["subject"] = SUBJECT_ALIASES.get(out["subject"] or "", out["subject"])
    out["query"] = (out["query"] or message or "").strip()
    out["source"] = "llm"
    return out


def detect(message, current_question_id=None, last_intent=None) -> dict:
    """对外唯一入口：优先大模型，失败即规则；再用规则结果补齐缺失字段。"""
    rule = rule_detect(message, current_question_id, last_intent)
    out = llm_detect(message, current_question_id, last_intent)
    if out is None:
        return rule
    for key in ("subject", "chapter", "knowledge_point", "mode", "question_ref"):
        if not out.get(key):
            out[key] = rule.get(key)
    if not out.get("qtypes"):
        out["qtypes"] = rule.get("qtypes") or []
    if not out.get("limit"):
        out["limit"] = rule.get("limit")
    if out["intent"] == "question_qa" and not out["question_ref"] and current_question_id:
        out["question_ref"] = str(current_question_id)
    return out



