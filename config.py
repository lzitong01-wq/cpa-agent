# -*- coding: utf-8 -*-
"""全局配置。

需要二次开发替换的部分集中在 LLM_PROVIDERS / TASK_MODELS 两处，
其余保持默认即可本地零配置启动。详见 docs/LLM_INTEGRATION.md。
"""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CORPUS_DIR = os.path.join(DATA_DIR, "corpus")
DB_PATH = os.environ.get("CPA_DB_PATH", os.path.join(DATA_DIR, "cpa.db"))
SCHEMA_PATH = os.path.join(BASE_DIR, "app", "schema.sql")

WEB_DIR = os.path.join(BASE_DIR, "web")
TEMPLATE_DIR = os.path.join(WEB_DIR, "templates")
STATIC_DIR = os.path.join(WEB_DIR, "static")

HOST = os.environ.get("CPA_HOST", "127.0.0.1")
PORT = int(os.environ.get("CPA_PORT", "8000"))
DEBUG = os.environ.get("CPA_DEBUG", "0") == "1"

# 单机演示用的默认用户；接入真实登录后从会话中取
DEFAULT_USER_ID = "demo_user"

# ---------------------------------------------------------------- 登录与会话
# 【部署必改】会话 cookie 的签名密钥。留空时启动会随机生成，
# 但那样每次重启都会让所有人掉线；多用户部署务必设置 CPA_SECRET。
SECRET_KEY = os.environ.get("CPA_SECRET", "")
SESSION_COOKIE = "cpa_session"
SESSION_MAX_AGE = int(os.environ.get("CPA_SESSION_MAX_AGE", str(14 * 24 * 3600)))
# 走 HTTPS 时置 1，cookie 只在加密连接上发送
SESSION_HTTPS_ONLY = os.environ.get("CPA_SESSION_HTTPS_ONLY", "0") == "1"
# 是否允许游客自助注册；关掉之后只能由管理员建号
ALLOW_SELF_REGISTER = os.environ.get("CPA_ALLOW_REGISTER", "1") == "1"

# ---------------------------------------------------------------- 教材上传
UPLOAD_DIR = os.path.join(CORPUS_DIR, "uploads")
UPLOAD_MAX_BYTES = int(os.environ.get("CPA_UPLOAD_MAX_MB", "40")) * 1024 * 1024
UPLOAD_ALLOWED_EXT = (".md", ".markdown", ".txt", ".pdf", ".docx")

# ---------------------------------------------------------------- 向量 / RAG
# 本地离线嵌入模型（已在 HuggingFace 缓存中）。
# 注意：本项目不使用 sentence-transformers（该环境下 import 会触发本地崩溃），
# 而是用 transformers 的 AutoModel + mean pooling 自行实现，效果等价。
EMBEDDING_BACKEND = os.environ.get("CPA_EMBED_BACKEND", "local")  # local | api | hashing
EMBEDDING_MODEL = os.environ.get("CPA_EMBED_MODEL", "shibing624/text2vec-base-chinese")
EMBEDDING_DIM = 768
EMBEDDING_MAX_LEN = 256
EMBEDDING_BATCH = 16
HASHING_DIM = 512  # hashing 兜底后端的维度

CHUNK_SIZE = 420          # 每个教材切片目标字数
CHUNK_OVERLAP = 80        # 切片重叠字数
RETRIEVE_TOP_K = 5        # 最终交给大模型的片段数
RETRIEVE_CANDIDATES = 30  # 混合召回候选数
HYBRID_ALPHA = 0.65       # 稠密向量权重，(1-alpha) 为 BM25 权重
# 低于阈值视为"教材中检索不到"，触发拒答话术，杜绝幻觉。
# 判定用两个**绝对**信号（与排名无关），任一达标即认为检索到了教材内容：
#   1) RELEVANCE_THRESHOLD  ：稠密向量的原始余弦相似度最高分
#   2) SPARSE_PER_TOKEN_MIN ：BM25 原始分 ÷ 查询词数（按词平均，长短查询可比）
# BM25 原始分随查询词数近似线性增长，必须按词平均后才能跨查询比较：
# "WACC"（1 个词）与"内含报酬率和净现值冲突怎么办"（19 个词）原始分差 15 倍，
# 按词平均后分别是 4.10 与 3.17，量级一致。
# 当前取值由 scripts/calibrate_refusal.py 在 35 条范围内问题 + 18 条范围外问题上标定，
# 两组完全可分。更换教材语料或嵌入模型后应重跑该脚本重新标定。
RELEVANCE_THRESHOLD = float(os.environ.get("CPA_RELEVANCE_THRESHOLD", "0.53"))
SPARSE_PER_TOKEN_MIN = float(os.environ.get("CPA_SPARSE_PER_TOKEN_MIN", "1.15"))

# ---------------------------------------------------------------- 大模型接入
# 【需要二次开发】把 api_key 填上即可启用；留空时系统自动降级为
# 纯检索模式（直接返回教材原文片段），保证无 API 也能演示全流程。
LLM_PROVIDERS = {
    "openai_compatible": {
        "base_url": os.environ.get("CPA_OPENAI_BASE", "https://api.openai.com/v1"),
        "api_key": os.environ.get("CPA_OPENAI_KEY", ""),
        "chat_path": "/chat/completions",
        "style": "openai",
    },
    "anthropic": {
        "base_url": os.environ.get("CPA_ANTHROPIC_BASE", "https://api.anthropic.com/v1"),
        "api_key": os.environ.get("CPA_ANTHROPIC_KEY", ""),
        "chat_path": "/messages",
        "style": "anthropic",
        "extra_headers": {"anthropic-version": "2023-06-01"},
    },
    "dashscope": {  # 阿里通义，OpenAI 兼容模式
        "base_url": os.environ.get(
            "CPA_DASHSCOPE_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ),
        "api_key": os.environ.get("CPA_DASHSCOPE_KEY", ""),
        "chat_path": "/chat/completions",
        "style": "openai",
    },
    "deepseek": {
        "base_url": os.environ.get("CPA_DEEPSEEK_BASE", "https://api.deepseek.com/v1"),
        "api_key": os.environ.get("CPA_DEEPSEEK_KEY", ""),
        "chat_path": "/chat/completions",
        "style": "openai",
    },
}

# 不同环节用不同模型：意图识别要快要便宜，答疑要强，题目讲解居中
TASK_MODELS = {
    "intent": {"provider": "deepseek", "model": "deepseek-chat",
               "temperature": 0.0, "max_tokens": 200},
    "qa": {"provider": "anthropic", "model": "claude-sonnet-5",
           "temperature": 0.2, "max_tokens": 1600},
    "question_explain": {"provider": "openai_compatible", "model": "gpt-4o-mini",
                         "temperature": 0.3, "max_tokens": 1200},
    "grading": {"provider": "openai_compatible", "model": "gpt-4o-mini",
                "temperature": 0.0, "max_tokens": 600},
    "embedding": {"provider": "", "model": ""},  # EMBEDDING_BACKEND=api 时才用
}

LLM_TIMEOUT = int(os.environ.get("CPA_LLM_TIMEOUT", "60"))


def task_config(task: str) -> dict:
    """取某个环节的模型配置，并合并 provider 连接信息。"""
    cfg = dict(TASK_MODELS.get(task) or {})
    prov = LLM_PROVIDERS.get(cfg.get("provider") or "", {})
    cfg["provider_conf"] = dict(prov)
    cfg["enabled"] = bool(prov.get("api_key"))
    return cfg
