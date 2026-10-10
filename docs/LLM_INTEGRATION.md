# 大模型接入指南（需二次开发的部分）

系统在**未配置任何 API Key 时可以完整运行**：题库、判分、错题本、推送算法、
统计、RAG 检索全部不依赖大模型；答疑会降级为"纯检索模式"，把教材原文片段
连同「科目 · 章节 · 知识点」来源原样返回。

配置 API Key 之后，大模型只负责"把检索到的教材原文组织成人话"，
不改变"必须基于教材、检索不到就拒答"这条硬约束。

---

## 一、需要二次开发的位置（共 4 处）

| # | 位置 | 作用 | 不配置的后果 |
| --- | --- | --- | --- |
| 1 | `config.py` → `LLM_PROVIDERS[*]["api_key"]` | **必填**。大模型鉴权 | 答疑走纯检索模式 |
| 2 | `config.py` → `TASK_MODELS` | 各环节用哪个供应商/模型 | 用默认映射 |
| 3 | `config.py` → `EMBEDDING_BACKEND = "api"` + `TASK_MODELS["embedding"]` | 改用云端向量化 | 用本地 `text2vec` 模型 |
| 4 | `app/rag/embedder.py` → `ApiEmbedder.encode` | 云端向量接口报文适配 | 同上 |

代码里这些位置都带 `【需要二次开发】` 注释，可以直接全局搜索定位：

```bash
findstr /S /N "需要二次开发" *.py
```

---

## 二、最小配置：3 分钟接上一个模型

### 方式 A：环境变量（推荐，不改代码）

```powershell
# DeepSeek
$env:CPA_DEEPSEEK_KEY = "sk-xxxxxxxx"
python run.py

# 阿里通义（OpenAI 兼容模式）
$env:CPA_DASHSCOPE_KEY = "sk-xxxxxxxx"

# OpenAI
$env:CPA_OPENAI_KEY = "sk-xxxxxxxx"

# Anthropic
$env:CPA_ANTHROPIC_KEY = "sk-ant-xxxxxxxx"
```

### 方式 B：直接改 `config.py`

```python
LLM_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-xxxxxxxx",          # ← 填这里
        "chat_path": "/chat/completions",
        "style": "openai",
    },
    ...
}
```

启动后在聊天页顶部会看到状态从
`纯检索模式（未配置大模型 API）` 变成 `大模型已接入`，
也可以直接查 `GET /api/chat/status`。

---

## 三、按环节配置不同模型

四个环节的成本与能力要求不同，`TASK_MODELS` 允许分别指定：

```python
TASK_MODELS = {
    # 意图识别：只输出 JSON，要快要便宜，temperature 必须为 0
    "intent": {"provider": "deepseek", "model": "deepseek-chat",
               "temperature": 0.0, "max_tokens": 200},

    # 知识点答疑：要严格遵循"只用教材片段"的指令，建议用最强的模型
    "qa": {"provider": "anthropic", "model": "claude-sonnet-5",
           "temperature": 0.2, "max_tokens": 1600},

    # 题目讲解：有标准解析兜底，中档模型足够
    "question_explain": {"provider": "openai_compatible", "model": "gpt-4o-mini",
                         "temperature": 0.3, "max_tokens": 1200},

    # 主观题评分：只输出 JSON 分数，要求稳定
    "grading": {"provider": "openai_compatible", "model": "gpt-4o-mini",
                "temperature": 0.0, "max_tokens": 600},

    # 向量化：仅当 EMBEDDING_BACKEND = "api" 时才用
    "embedding": {"provider": "", "model": ""},
}
```

每个环节独立判断可用性：只配了 `qa` 的 Key，那么答疑用大模型、
意图识别自动回落到规则引擎、主观题评分自动回落到要点命中法，互不影响。

---

## 四、支持的报文风格

`app/agent/llm.py` 只用标准库 `urllib` 发请求，**不需要安装任何 SDK**。
通过 `style` 字段适配两种主流报文：

### `style: "openai"`

```
POST {base_url}{chat_path}
Authorization: Bearer {api_key}

{"model": "...", "temperature": 0.2, "max_tokens": 1600,
 "messages": [{"role": "system", ...}, {"role": "user", ...}]}
```

适用：OpenAI、DeepSeek、阿里通义（兼容模式）、月之暗面 Kimi、智谱 GLM、
字节豆包、以及本地部署的 vLLM / Ollama / LM Studio 的 OpenAI 兼容端点。

### `style: "anthropic"`

```
POST {base_url}{chat_path}
x-api-key: {api_key}
anthropic-version: 2023-06-01

{"model": "...", "system": "...", "max_tokens": 1600,
 "messages": [{"role": "user", ...}]}
```

适用：Anthropic Claude 官方接口。

---

## 五、接入新供应商

### 情况 1：对方兼容 OpenAI 报文（大多数国产模型）

只加一段配置，不写代码：

```python
LLM_PROVIDERS["moonshot"] = {
    "base_url": "https://api.moonshot.cn/v1",
    "api_key": os.environ.get("CPA_MOONSHOT_KEY", ""),
    "chat_path": "/chat/completions",
    "style": "openai",
}
TASK_MODELS["qa"] = {"provider": "moonshot", "model": "moonshot-v1-8k",
                     "temperature": 0.2, "max_tokens": 1600}
```

本地 Ollama 同理（`api_key` 随便填个非空值即可通过可用性检查）：

```python
LLM_PROVIDERS["ollama"] = {
    "base_url": "http://127.0.0.1:11434/v1",
    "api_key": "ollama",
    "chat_path": "/chat/completions",
    "style": "openai",
}
```

### 情况 2：报文完全不同（如百度千帆的私有格式）

在 `app/agent/llm.py` 里扩三个地方，每处照抄现有分支即可：

```python
def _headers(self):
    if self.style == "qianfan":
        return {"Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key}
    ...

def _payload(self, system, user, temperature, max_tokens):
    if self.style == "qianfan":
        return {"messages": [{"role": "user", "content": system + "\n\n" + user}],
                "temperature": temperature or 0.01}
    ...

@staticmethod
def _extract(data):
    if "result" in data:            # 千帆把正文放在 result 字段
        return str(data["result"]).strip()
    ...
```

`_extract` 已经同时兼容 `content[].text`（Anthropic 风格）和
`choices[0].message.content`（OpenAI 风格），多数情况不用改。

---

## 六、云端向量化（可选）

默认用本地模型算向量，完全离线。要改用云端 Embedding API：

```python
EMBEDDING_BACKEND = "api"
EMBEDDING_DIM = 1536                    # 改成新模型的维度
TASK_MODELS["embedding"] = {"provider": "openai_compatible",
                            "model": "text-embedding-3-small"}
```

然后检查 `app/rag/embedder.py` 的 `ApiEmbedder.encode`，
默认按 `POST {base_url}/embeddings` + `{"model":..., "input":[...]}`
发送、从 `data[i].embedding` 取向量。供应商报文不同就改这一个方法。

**改完必须重建索引**，否则新旧向量维度不一致：

```bash
python scripts/ingest_corpus.py --force
```

> 维度不匹配时系统不会崩：`app/rag/index.py` 检测到维度不一致会跳过稠密检索、
> 只用 BM25 继续工作，并打日志提示重建索引。

---

## 七、切换模型后必须做的两件事

1. **重跑拒答阈值标定。** 不同嵌入模型的余弦相似度分布完全不同，
   沿用旧阈值会导致"教材里有的被拒答"或"教材里没有的被硬答"：

   ```bash
   python scripts/ingest_corpus.py --force
   python scripts/calibrate_refusal.py
   ```

   把输出的推荐值写回 `config.py` 的 `RELEVANCE_THRESHOLD` 与
   `SPARSE_PER_TOKEN_MIN`。仅换对话模型（不换嵌入模型）时不需要。

2. **跑一遍冒烟测试。**

   ```bash
   python scripts/smoke_test.py
   ```

   其中"教材没有则明确拒答，不编造"这一项专门验证换模型后
   反幻觉约束是否还成立。

---

## 八、提示词在哪里改

全部集中在 `app/agent/prompts.py`，全文见 [PROMPTS.md](PROMPTS.md)。
换模型后如果发现它不遵守"只用教材片段"，优先加强
`RAG_QA_SYSTEM_PROMPT` 的第 1、2 条，而不是放宽检索阈值。

---

## 九、成本与失败处理

- **不会静默烧钱**：意图识别 200 token 上限，答疑 1600 token 上限，
  上下文最长 3200 字（`retrieve_context(max_chars=...)` 可调）。
- **调用失败自动降级**：`LLMError` 会被 orchestrator 捕获，
  答疑退回纯检索模式并在回复里说明原因，不会给用户报 500。
- **超时**：`config.LLM_TIMEOUT`（默认 60 秒）。
- **不做重试**：避免一次提问被放大成多次计费；需要重试请在
  `LLMClient.chat` 外层自行包装。
