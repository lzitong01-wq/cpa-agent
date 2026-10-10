# -*- coding: utf-8 -*-
"""可配置的大模型调用层（【需要二次开发】：填 api_key 即启用）。

只用标准库 urllib 发 HTTPS 请求，不引入 openai / anthropic SDK，
因此换供应商不需要装新包，只改 config.LLM_PROVIDERS 即可。

支持两种报文风格：
- style="openai"    ：/chat/completions，messages 含 system 角色（OpenAI、DeepSeek、通义、
                      Kimi、智谱、本地 vLLM / Ollama 的 OpenAI 兼容端点都属于这一类）
- style="anthropic" ：/messages，system 为顶层字段，鉴权头为 x-api-key

未配置 api_key 时 available() 返回 False，上层（orchestrator / grading）
自动降级为"纯检索 / 规则"模式，保证零配置也能跑通全流程。
"""
import json
import logging
import re
import urllib.error
import urllib.request

import config

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """大模型调用失败（网络、鉴权、限流、报文异常统一抛这个）。"""


class LLMClient:
    """一个任务（intent / qa / question_explain / grading）对应一个客户端。"""

    def __init__(self, task: str):
        self.task = task
        cfg = config.task_config(task)
        self.cfg = cfg
        conf = cfg.get("provider_conf") or {}
        self.provider = cfg.get("provider") or ""
        self.model = cfg.get("model") or ""
        self.style = conf.get("style") or "openai"
        self.base_url = (conf.get("base_url") or "").rstrip("/")
        self.chat_path = conf.get("chat_path") or "/chat/completions"
        self.api_key = conf.get("api_key") or ""
        self.extra_headers = dict(conf.get("extra_headers") or {})
        self.temperature = cfg.get("temperature", 0.2)
        self.max_tokens = cfg.get("max_tokens", 1200)
        self.timeout = getattr(config, "LLM_TIMEOUT", 60)

    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def describe(self) -> dict:
        return {"task": self.task, "provider": self.provider, "model": self.model,
                "style": self.style, "base_url": self.base_url,
                "available": self.available()}

    # ------------------------------------------------------------ 报文构造
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.style == "anthropic":
            h["x-api-key"] = self.api_key
        else:
            h["Authorization"] = "Bearer " + self.api_key
        h.update(self.extra_headers)
        return h

    def _payload(self, system: str, user: str, temperature, max_tokens) -> dict:
        if self.style == "anthropic":
            return {"model": self.model, "system": system, "max_tokens": max_tokens,
                    "temperature": temperature,
                    "messages": [{"role": "user", "content": user}]}
        return {"model": self.model, "temperature": temperature,
                "max_tokens": max_tokens,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}

    @staticmethod
    def _extract(data: dict) -> str:
        # anthropic: {"content":[{"type":"text","text":"..."}]}
        blocks = data.get("content")
        if isinstance(blocks, list):
            parts = [b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type") in (None, "text")]
            if parts:
                return "".join(parts).strip()
        # openai: {"choices":[{"message":{"content":"..."}}]}
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") or {}
            content = msg.get("content")
            if isinstance(content, list):  # 部分兼容端点返回分块
                content = "".join(p.get("text", "") for p in content
                                  if isinstance(p, dict))
            if content:
                return str(content).strip()
        raise LLMError("无法从响应中解析出文本：" + json.dumps(data, ensure_ascii=False)[:400])

    # ------------------------------------------------------------ 调用
    def chat(self, system: str, user: str, temperature=None, max_tokens=None) -> str:
        """同步调用，返回纯文本。未配置 key 时抛 LLMError，由上层降级处理。"""
        if not self.available():
            raise LLMError(
                "任务 %s 未配置大模型（provider=%s）。请参考 docs/LLM_INTEGRATION.md "
                "填写 config.LLM_PROVIDERS 中的 api_key。" % (self.task, self.provider))
        url = self.base_url + self.chat_path
        body = json.dumps(
            self._payload(system, user,
                          self.temperature if temperature is None else temperature,
                          self.max_tokens if max_tokens is None else max_tokens),
            ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers=self._headers(),
                                     method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise LLMError("HTTP %s %s：%s" % (exc.code, exc.reason, detail)) from exc
        except Exception as exc:  # 超时 / DNS / TLS
            raise LLMError("请求 %s 失败：%s" % (url, exc)) from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise LLMError("响应不是合法 JSON：" + raw[:400]) from exc
        if isinstance(data, dict) and data.get("error"):
            raise LLMError(json.dumps(data["error"], ensure_ascii=False)[:400])
        return self._extract(data)

    def chat_json(self, system: str, user: str, **kw):
        """要求模型输出 JSON 的场景；容忍 ```json 包裹与前后废话。"""
        text = self.chat(system, user, **kw)
        return parse_json_loose(text)


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json_loose(text: str):
    """从模型输出里尽量抠出 JSON 对象；失败返回 None（由规则兜底）。"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    m = _JSON_BLOCK.search(s)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


_CLIENTS = {}


def get_client(task: str) -> LLMClient:
    """按任务缓存客户端；改了 config 后调 reset_clients() 重新读取。"""
    if task not in _CLIENTS:
        _CLIENTS[task] = LLMClient(task)
    return _CLIENTS[task]


def reset_clients():
    _CLIENTS.clear()


def llm_status() -> dict:
    """给 /api/chat/status 与首页用：各环节是否已接入大模型。"""
    tasks = [t for t in config.TASK_MODELS if t != "embedding"]
    info = {t: get_client(t).describe() for t in tasks}
    return {"tasks": info,
            "any_available": any(v["available"] for v in info.values()),
            "note": "全部不可用时系统运行在纯检索模式：答疑直接返回教材原文片段+来源，"
                    "刷题、判分、错题本、推送算法均不依赖大模型。"}


