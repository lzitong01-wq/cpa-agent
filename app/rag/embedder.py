# -*- coding: utf-8 -*-
"""向量化后端。

三种后端，按 config.EMBEDDING_BACKEND 选择：
  local   —— 本地 transformers 模型 + mean pooling（默认，离线可用，768 维）
  api     —— 【需要二次开发】调用外部 embedding 接口
  hashing —— 纯 numpy 的 jieba + hashing trick 兜底，无任何模型依赖

注意：本项目刻意不使用 sentence-transformers。该环境下
`from sentence_transformers import SentenceTransformer` 会触发本地进程崩溃
（0xC0000005），因此直接用 transformers 的 AutoModel 自行做 mean pooling，
输出与 sentence-transformers 的默认池化方式一致。
"""
import hashlib
import logging
import threading

import numpy as np

import config

log = logging.getLogger(__name__)
_lock = threading.Lock()
_instance = None


class BaseEmbedder(object):
    name = "base"
    dim = 0

    def encode(self, texts, batch_size=None) -> np.ndarray:
        raise NotImplementedError

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


class LocalEmbedder(BaseEmbedder):
    """本地 HuggingFace 模型，mean pooling + L2 归一化。"""

    def __init__(self, model_name=None):
        import torch
        from transformers import AutoModel, AutoTokenizer

        self._torch = torch
        self.model_name = model_name or config.EMBEDDING_MODEL
        # local_files_only：只用本地缓存，避免无网络时卡在下载上
        self.tokenizer = AutoTokenizer.from_pretrained(self.model_name, local_files_only=True)
        self.model = AutoModel.from_pretrained(self.model_name, local_files_only=True)
        self.model.eval()
        self.dim = int(self.model.config.hidden_size)
        self.name = "local:" + self.model_name

    def encode(self, texts, batch_size=None) -> np.ndarray:
        torch = self._torch
        if isinstance(texts, str):
            texts = [texts]
        texts = [t if t else " " for t in texts]
        bs = int(batch_size or config.EMBEDDING_BATCH)
        out = []
        for i in range(0, len(texts), bs):
            batch = texts[i:i + bs]
            enc = self.tokenizer(batch, padding=True, truncation=True,
                                 max_length=config.EMBEDDING_MAX_LEN, return_tensors="pt")
            with torch.no_grad():
                hidden = self.model(**enc).last_hidden_state
            mask = enc["attention_mask"].unsqueeze(-1).float()
            vec = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            vec = torch.nn.functional.normalize(vec, p=2, dim=1)
            out.append(vec.cpu().numpy().astype("float32"))
        return np.vstack(out) if out else np.zeros((0, self.dim), dtype="float32")


class HashingEmbedder(BaseEmbedder):
    """零模型兜底：jieba 分词 → hashing trick → 亚线性词频加权 → L2 归一化。

    语义泛化能力弱于神经网络模型，但完全不依赖模型文件，
    保证任何机器上 RAG 链路都能跑通。
    """

    def __init__(self, dim=None):
        self.dim = int(dim or config.HASHING_DIM)
        self.name = "hashing:%d" % self.dim
        try:
            import jieba
            self._cut = lambda s: jieba.cut(s)
        except Exception:
            self._cut = lambda s: list(s)

    def _hash(self, token: str) -> int:
        h = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "little") % self.dim

    def encode(self, texts, batch_size=None) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        mat = np.zeros((len(texts), self.dim), dtype="float32")
        for i, t in enumerate(texts):
            counts = {}
            for w in self._cut(str(t or "")):
                w = w.strip()
                if len(w) < 2:
                    continue
                counts[w] = counts.get(w, 0) + 1
            for w, c in counts.items():
                mat[i, self._hash(w)] += 1.0 + np.log(c)
            # 双字切分补充，缓解 hashing 冲突下的召回缺失
            s = str(t or "")
            for j in range(len(s) - 1):
                bg = s[j:j + 2]
                if bg.strip():
                    mat[i, self._hash("##" + bg)] += 0.3
        norm = np.linalg.norm(mat, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        return mat / norm


class ApiEmbedder(BaseEmbedder):
    """【需要二次开发】接外部 embedding 接口，见 docs/LLM_INTEGRATION.md。"""

    def __init__(self):
        cfg = config.task_config("embedding")
        self.conf = cfg
        self.dim = config.EMBEDDING_DIM
        self.name = "api:%s" % (cfg.get("model") or "unset")
        if not cfg.get("enabled"):
            raise RuntimeError("embedding provider 未配置 api_key")

    def encode(self, texts, batch_size=None) -> np.ndarray:
        import json
        import urllib.request

        if isinstance(texts, str):
            texts = [texts]
        prov = self.conf["provider_conf"]
        url = prov["base_url"].rstrip("/") + "/embeddings"
        body = json.dumps({"model": self.conf["model"], "input": list(texts)}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer %s" % prov["api_key"],
        })
        with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        vecs = np.array([d["embedding"] for d in payload["data"]], dtype="float32")
        norm = np.linalg.norm(vecs, axis=1, keepdims=True)
        norm[norm == 0] = 1.0
        self.dim = vecs.shape[1]
        return vecs / norm


def get_embedder(force_backend=None) -> BaseEmbedder:
    """进程级单例；local 加载失败自动降级到 hashing，不让整站起不来。"""
    global _instance
    backend = force_backend or config.EMBEDDING_BACKEND
    if _instance is not None and force_backend is None:
        return _instance
    with _lock:
        if _instance is not None and force_backend is None:
            return _instance
        inst = None
        if backend == "local":
            try:
                inst = LocalEmbedder()
            except Exception as e:
                log.warning("本地嵌入模型加载失败(%s)，降级为 hashing 后端", e)
        elif backend == "api":
            try:
                inst = ApiEmbedder()
            except Exception as e:
                log.warning("API 嵌入后端不可用(%s)，降级为 hashing 后端", e)
        if inst is None:
            inst = HashingEmbedder()
        if force_backend is None:
            _instance = inst
        return inst


def reset_embedder() -> None:
    global _instance
    _instance = None
