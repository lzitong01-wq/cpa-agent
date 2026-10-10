# 技术栈与实现技术说明

本文回答三个问题：**用了什么技术**、**每个部分是什么逻辑**、**这个逻辑用什么技术落地的**。

- 设计取舍与算法细节 → [ARCHITECTURE.md](ARCHITECTURE.md)
- 运行与部署 → [DEPLOY.md](DEPLOY.md)
- 大模型接入 → [LLM_INTEGRATION.md](LLM_INTEGRATION.md)
- 提示词全文 → [PROMPTS.md](PROMPTS.md)

---

## 一、技术栈总览

### 实测环境（本机已验证运行）

| 层次 | 技术 | 版本 | 是否必需 |
| --- | --- | --- | --- |
| 语言 | Python | 3.14.2 | 必需 |
| 数据库 | SQLite（标准库 `sqlite3`） | 3.50.4 | 标准库自带 |
| Web 框架 | Flask | 3.1.3 | 必需 |
| 模板引擎 | Jinja2 | 3.1.6 | Flask 自带 |
| 数值计算 | NumPy | 2.4.2 | 必需 |
| 中文分词 | jieba | 0.42.1 | 必需 |
| 深度学习运行时 | PyTorch（CPU 版） | 2.10.0+cpu | 建议 |
| 模型加载 | Transformers | 5.9.0 | 建议 |
| 嵌入模型 | `shibing624/text2vec-base-chinese`（768 维） | — | 建议 |
| PDF 解析 | PyMuPDF (`fitz`) | 1.27.2.3 | 可选 |
| Word 解析 | python-docx | 1.2.0 | 可选 |
| 大模型调用 | 标准库 `urllib.request` | — | 标准库自带 |
| 前端 | 原生 HTML/CSS/JavaScript（ES6） | — | 无构建步骤 |

"建议"= 缺失时自动降级到 hashing 向量后端，系统仍可运行；
"可选"= 缺失时只是跳过对应格式的教材文件。

### 代码规模

| 类型 | 文件数 | 行数 |
| --- | --- | --- |
| Python | 44 | 4432 |
| SQL（`schema.sql`） | 1 | 208 |
| JavaScript | 9 | 1223 |
| CSS | 2 | 273 |
| HTML 模板 | 8 | 402 |
| Markdown 文档 | 11 | 1625 |

### 刻意没有用的技术

| 没用 | 常见做法 | 这里的替代 | 原因 |
| --- | --- | --- | --- |
| ORM（SQLAlchemy / Django ORM） | 模型类映射 | 手写 `schema.sql` + `sqlite3` | 需求明确要"完整 SQLite 建表语句"，DDL 本身是交付物；ORM 会把它藏进 Python 类 |
| sentence-transformers | `SentenceTransformer(model)` | `transformers.AutoModel` + 手写池化 | **本机 import 即崩溃**（0xC0000005），且它对这类模型只是 20 行池化封装 |
| 向量数据库（FAISS / Chroma / sqlite-vec） | ANN 索引 | NumPy 暴力矩阵乘法 | 51×768 的矩阵乘法是微秒级，索引构建开销反而更大 |
| LangChain / LlamaIndex | 链式编排 | 手写 300 行编排 | 拒答阈值、降级路径、意图路由都需要精确控制，框架的抽象层会挡住这些 |
| openai / anthropic SDK | `client.chat.completions` | 标准库 `urllib` | 换供应商不用装新包；两种报文风格覆盖全部主流 API |
| React / Vue | 组件化 + 构建 | 原生 JS + `fetch` | 要求"本地一键启动"，加构建步骤就得先装 Node |
| FastAPI | 异步 + Pydantic | Flask | 本机 fastapi/uvicorn/pydantic **未安装**，Flask 已就绪；这是纯粹的环境事实判断 |
| Redis / Celery | 缓存与队列 | 进程内单例 + 同步调用 | 单机单用户，引入即负债 |
| Docker | 容器化 | `python run.py` | 需求要"本地一键启动" |

---

## 二、分层与数据流

```text
        用户浏览器
            │  ① 点击/输入
            ▼
┌───────────────────────────────────────────────────┐
│ web/templates(Jinja2) + web/static(原生 JS)        │
│ question.js 渲染题卡 · chat.js 渲染对话             │
└───────────────────┬───────────────────────────────┘
                    │ ② fetch(JSON) 19 个接口
                    ▼
┌───────────────────────────────────────────────────┐
│ app/webapp.py   Flask 工厂：6 页面路由 + 43 条路由  │
│ app/api/*       Blueprint：统一 {ok,data,error}    │
└──────┬──────────────────────────┬─────────────────┘
       │ ③ 题库类请求              │ ④ 聊天请求
       ▼                          ▼
┌──────────────────┐   ┌────────────────────────────┐
│ app/business/*   │   │ app/agent/orchestrator.py  │
│ 纯 Python 算法    │◀──│ 意图识别 → 路由 → 组织回复   │
│ 无 AI 依赖        │   │ intent.py(正则+实体链接)    │
└──────┬───────────┘   │ llm.py(urllib 双风格)      │
       │               └──────────┬─────────────────┘
       │                          │ ⑤ 强制先检索
       │                          ▼
       │               ┌────────────────────────────┐
       │               │ app/rag/service.py         │
       │               │ retriever 混合检索 + 拒答   │
       │               │ embedder(torch) bm25(纯Py) │
       │               │ index(numpy 矩阵)          │
       │               └──────────┬─────────────────┘
       ▼                          ▼
┌───────────────────────────────────────────────────┐
│ app/db.py（threading.local + 事务）                │
│ data/cpa.db  SQLite WAL：11 表 / 15 索引 / 2 视图  │
└───────────────────────────────────────────────────┘
```

一句话概括每层用的技术：
**Jinja2 出骨架 → 原生 JS 发 fetch → Flask Blueprint 收 → 纯 Python 算业务 /
torch+numpy 算检索 → 标准库 sqlite3 落盘。**

---

## 三、数据存储层

### 用什么技术

标准库 `sqlite3`，DDL 手写在 [app/schema.sql](../app/schema.sql)（208 行，11 表 / 15 索引 / 2 视图），
连接管理在 [app/db.py](../app/db.py)。零第三方依赖。

### 关键实现

**四个 PRAGMA 决定了并发行为**

```python
conn = sqlite3.connect(config.DB_PATH, timeout=30.0, check_same_thread=False)
conn.row_factory = sqlite3.Row          # 结果按列名取，等价轻量 ORM
conn.execute("PRAGMA foreign_keys = ON")    # SQLite 默认关闭，不开 CASCADE 无效
conn.execute("PRAGMA journal_mode = WAL")   # 读写不互斥
conn.execute("PRAGMA synchronous = NORMAL") # WAL 下的安全/速度平衡点
```

`check_same_thread=False` 配合 `threading.local()` 使用：Flask 多线程模式下每个线程
持有独立连接，请求结束由 `teardown_appcontext` 归还。**不是**跨线程共享一条连接。

`foreign_keys = ON` 是必须显式打开的——SQLite 为了向后兼容默认关闭外键约束。
不开这一行，`corpus_chunks` 的 `ON DELETE CASCADE` 就是一句注释，
删教材文档会留下一堆孤儿切片和孤儿向量。

**事务用上下文管理器包住多表写入**

```python
with db.transaction() as conn:      # 判分 → 写记录 → 更新错题本
    ...                            # 三步同生共死
```

`records.submit_answer()` 里这三步在同一事务内，所以不会出现"答题记录写进去了、
错题本没更新"的半成品状态。`wrongbook.mark_wrong/mark_right` 都接受外部传入的 `conn`
就是为了参与调用方的事务。

**两个视图把正确率计算下沉到 SQL**

```sql
CREATE VIEW v_kp_accuracy AS
SELECT user_id, subject_id, chapter_id, knowledge_point_id,
       COUNT(*) total, SUM(is_correct) correct,
       ROUND(1.0*SUM(is_correct)/COUNT(*), 4) accuracy, MAX(answered_at) last_answered_at
FROM answer_records GROUP BY user_id, knowledge_point_id;
```

推送算法、统计页、首页画像三处都要"每个知识点的正确率"。写成视图，
三处共用一份口径，避免三份 SQL 各自漂移。

**答题记录冗余三级 id**：`answer_records` 和 `wrong_questions` 都存了
`subject_id/chapter_id/knowledge_point_id`，而不是每次 `JOIN questions`。
两个收益：统计页聚合变成单表 `GROUP BY`；题目日后被改分类，
历史记录仍反映当时归属——这在统计口径上才是对的。

**五种题型共用一张 `questions` 表**，靠 `qtype` CHECK 约束区分：

```sql
qtype TEXT CHECK (qtype IN ('single','multiple','judge','short','comprehensive'))
```

不拆表的原因：筛选、组卷、推送、统计全都要跨题型混合查询，拆表会把每个查询变成 5 路 UNION。
题型差异用两个 JSON 列吸收——`options_json` 存 `[["A","文本"],...]`（判断题与主观题为 NULL），
`answer_json` 存 `"C"` / `["A","C"]` / `true` / `{"points":[...]}`。
主观题的"答案"就是采分点数组，判分逻辑直接按点命中率算分。

---

## 四、业务层：纯 Python，零 AI 依赖

`app/business/` 8 个模块全部是标准库 + SQL，**不 import 任何 AI 相关的包**。
把 `app/agent/` 和 `app/rag/` 整个删掉，题库刷题这条线依然完整可用。

### 1. 中文话术 → 数据库 id（`catalog.py`）

**逻辑**：用户说"会计收入章节"，要变成 `subject_id=1, chapter_id=4`。

**技术**：三级递进的 SQL 匹配，不用任何 NLP 模型。

```sql
-- 科目：先试 id，再试精确 code/name，最后反向模糊匹配
SELECT * FROM subjects WHERE ? LIKE '%' || name || '%' ORDER BY LENGTH(name) DESC
-- 章节：三种写法都接受，命中多条取最短名（最精确的那个）
SELECT * FROM chapters WHERE (code = ? OR name = ? OR name LIKE '%' || ? || '%')
  AND subject_id = ? ORDER BY LENGTH(name) LIMIT 1
```

反向 `LIKE`（把用户输入放左边）是关键技巧：它能吃下"会计科目""《会计》"这类带修饰的说法。
知识点命中后**反向补齐** `chapter_id` 与 `subject_id`——用户只说"暂时性差异"，
也能定位到 会计 → 第十八章 所得税。

### 2. 五种题型判分（`grading.py`）

**逻辑**：客观题比对答案，主观题算采分点命中率。

**技术**：Python 集合运算 + 归一化函数，无外部依赖。

| 题型 | 判分技术 |
| --- | --- |
| 单选 | `_norm_keys()` 归一化后字符串全等 |
| 多选 | `set` 运算：相等满分；**真子集且无错选得 50%**；有错选得 0 |
| 判断 | `_norm_bool()` 归一化，接受 `对/错/正确/错误/√/×/true/false/A/B` |
| 简答/综合 | `_tokens()` 分词后按 `POINT_HIT_RATIO=0.5` 判定单个采分点是否命中 |

多选"漏选给一半"是对 CPA 真实计分规则（漏选按比例、错选不得分）的近似。
主观题 `SUBJECTIVE_PASS_RATIO=0.6`：得分率达 6 成算"正确"。

主观题有大模型钩子：`grade(question, user_answer, llm_grader=None)`。
传入回调就走语义采点，不传或调用失败就 `try/except` **静默回退**关键词命中法。
这个钩子是业务层与 AI 唯一的接触点，而且方向是"业务层被动接受注入"，不是主动依赖。

### 3. 推送算法（`recommend.py`）：四桶配额

**逻辑**：需求点名"避免纯随机出题"，所以要有可解释的分层。

**技术**：SQL 聚合（走 `v_kp_accuracy` 视图）+ `math.log` 加权 + `random.Random(seed)` 无放回抽样。

```python
QUOTA = (("wrong", 0.40), ("weak", 0.35), ("fresh", 0.15), ("mastered", 0.10))
```

四个桶的判定与权重：

| 桶 | 判定 | 权重公式 |
| --- | --- | --- |
| `wrong` 错题关联 | 错题本 `status='open'` 涉及的知识点 | `SUM(wrong_count)` 累计错次 |
| `weak` 薄弱 | 正确率 < 0.7 | `(1-acc) × log(total+1) + 0.1` |
| `fresh` 未练 | 有题但从未作答 | 等权 |
| `mastered` 已掌握 | 正确率 ≥ 0.8 且答题数 ≥ 2 | 等权 |

`log(total+1)` 这个因子是有意义的：做过 1 题错 1 题（正确率 0）和做过 10 题对 3 题（正确率 0.3），
后者的"薄弱"证据更充分。不乘这个因子，样本量为 1 的噪声会霸占推送位。

空桶配额会**重新分配**给非空桶（`_alloc`），所以新用户不会只拿到 1.5 道题，
而是 `fresh` 8~9 道 + `mastered` 1~2 道。

**随机数只用于"同权重知识点之间的选择"**，不用于选题本身：
`_weighted_pick` 按权重无放回抽样知识点，`_order_candidates` 再在知识点内
按"没做过 > 做错过 > 做对过"确定性排序（`wrong` 桶反过来，`mastered` 桶优先难题）。
每道题都带 `reason` 与 `reason_bucket` 透传到前端，用户能看到"选题依据：错题关联知识点"。

三种刷题模式在同一个 `recommend()` 入口用 `mode` 分派：
`chapter` 带章节约束跑上面的算法，`mock` 转 `paper.build_mock()` 按题型配比抽题，
`wrong` 转 `wrongbook.redo_set()` 按错次降序。

### 4. 错题本状态机（`wrongbook.py`）

**逻辑**：错题不能物理删除，否则"移除后再做错"就丢了历史错次。

**技术**：`status` 字段 + `UNIQUE(user_id, question_id)` 上的 UPSERT。

```text
做错 → open ──答对1次──▶ consecutive_right=1
                └──再答对──▶ resolved（自动脱离，RESOLVE_STREAK=2）
open ──用户手动──▶ removed ──再做错──▶ 回到 open，wrong_count 继续累加
```

"连对两次才算掌握"避免蒙对一次就把题放走。`resolved` 的题仍在库里，
统计页因此能算出"攻克率"。

---

## 五、RAG 层：这是技术密度最高的部分

六个内部模块 + 一个门面，对外只暴露 [app/rag/service.py](../app/rag/service.py) 的 7 个函数。

```text
教材文件 ──chunker──▶ 切片(带三级元数据) ──embedder(torch)──▶ 768维向量 ──▶ SQLite BLOB
                                        └──jieba────────▶ tokens 缓存 ──▶ SQLite JSON
用户提问 ──▶ 向量 + 分词 ──▶ 余弦(numpy) ⊕ BM25(纯Python) ──融合排序──▶ Top5
                              └────── 双绝对阈值判定 ──▶ 不达标直接拒答（不调大模型）
```

### 1. 切片（`chunker.py`）：标题层级就是检索元数据

**逻辑**：`#` = 科目、`##` = 章节、`###` = 知识点。正文按 420 字切、80 字重叠。
切片时把标题层级记进元数据，答疑才能标注"来源：会计 · 第十八章 所得税 · 暂时性差异"。

**技术**：正则 + 状态机（不用 markdown 解析库，因为要同时吃 PDF 抽出的无标记文本）。

```python
H1 = re.compile(r"^#\s+(.+?)\s*$")          # 科目
H2 = re.compile(r"^##\s+(.+?)\s*$")         # 章节
H3 = re.compile(r"^###\s+(.+?)\s*$")        # 知识点
CHAPTER_PAT = re.compile(r"^\s*(第[一二三四五六七八九十百零〇\d]+章\s*.{0,40})\s*$")  # PDF 兜底
```

两个不显然但关键的处理：

**(a) 标题边界不带重叠。** 早期版本无条件重叠 80 字，结果"五力模型"这个切片的正文
开头是上一节 PEST 的内容——**引用来源标着五力模型，展示的原文却是宏观环境分析**。
改成 `close(carry_overlap=False)`：只在因长度超限而切分时才重叠，标题换段时不重叠。

**(b) 每个切片存两份文本。**

```python
text        = 纯正文                                    # 用于展示与引用摘录
search_text = "科目 · 章节 · 知识点\n" + 正文            # 用于向量化与 BM25 分词
```

因为教材正文往往不重复标题里的词。用户问"五力模型"，那段正文可能通篇在说
"潜在进入者的进入威胁、替代品的替代威胁……"，字面上根本没有"五力模型"四个字。
把面包屑并入检索文本后命中率明显改善，且这一步让切片数从 79 降到 51（重叠碎片消失），
检索质量反而提升。

### 2. 向量化（`embedder.py`）：为什么手写池化

**逻辑**：中文句子 → 768 维单位向量，余弦相似度可比。

**技术**：`transformers.AutoTokenizer` + `AutoModel` + 手写 mean pooling + L2 归一化。

```python
enc = self.tokenizer(batch, padding=True, truncation=True, max_length=256, return_tensors="pt")
with torch.no_grad():
    hidden = self.model(**enc).last_hidden_state          # (B, L, 768)
mask = enc["attention_mask"].unsqueeze(-1).float()        # (B, L, 1)
vec = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)  # 按有效 token 平均
vec = torch.nn.functional.normalize(vec, p=2, dim=1)      # 归一化后点乘即余弦
```

`local_files_only=True` 强制只读本地缓存，避免无网络时卡在下载上。

**为什么不用 sentence-transformers**：本机 `from sentence_transformers import
SentenceTransformer` 直接以 `0xC0000005`（ACCESS_VIOLATION，退出码 `-1073741819`）
崩掉进程，没有 traceback。逐库排查确认 numpy / scipy / sklearn / jieba / torch /
transformers / PyMuPDF 全部正常，只有它崩。而它对 text2vec 这类模型做的事情
就是上面那 5 行，所以直接手写。实测加载 0.9s，768 维，三条测试查询 top-1 全部命中正确文档。

**三级后端 + 静默降级**，`get_embedder()` 是带锁的进程级单例：

| 后端 | 技术 | 触发条件 |
| --- | --- | --- |
| `local` | torch + transformers，768 维 | 默认 |
| `api` | `urllib` POST `/embeddings` | `CPA_EMBED_BACKEND=api`（需二次开发） |
| `hashing` | jieba + md5 hashing trick + bigram(0.3 权重)，512 维 | 上面两个失败时**自动**降级 |

hashing 后端是纯 numpy 实现的兜底：`md5(token)` 前 4 字节取模映射到维度，
词频用 `1 + log(c)` 亚线性加权。语义泛化弱，但保证任何机器上 RAG 链路都跑得通。

### 3. BM25（`bm25.py`）：纯 Python 手写

**逻辑**：向量擅长"换个说法"，BM25 擅长"专业术语精确命中"，两者互补。

**技术**：`collections.Counter` + `math.log`，102 行，无第三方库。

```python
idf = log(1 + (n - df + 0.5) / (df + 0.5))                      # 加一平滑，避免负 idf
score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))   # k1=1.5, b=0.75
```

分词是 **jieba 精确模式 + 停用词过滤 + 中文 bigram 补充**：

```python
zh = re.sub(r"[^一-鿿]", "", text)
out.extend(zh[i:i+2] for i in range(len(zh) - 1))   # 补相邻二字组合
```

补 bigram 的原因：jieba 会把"暂时性差异"切成"暂时/性/差异"，
补上"暂时/时性/性差/差异"这些二字组合，专业术语的命中率明显提高。
jieba 不可用时退化为纯二元字切分，仍然能工作。

### 4. 索引（`index.py`）：一条 SQL + 一次矩阵乘法

**逻辑**：进程启动后把全部切片的向量与倒排常驻内存，查询时一次算完。

**技术**：`numpy.frombuffer` 零拷贝还原 BLOB + 矩阵乘法。

```python
vecs.append(np.frombuffer(r["vec"], dtype="float32"))   # 零拷贝，不做 list 转换
...
sims = self.matrix[rows] @ q            # (n, 768) @ (768,) -> (n,) 余弦相似度
```

向量已在写入前归一化，所以**点乘就是余弦**，不需要再除模长。

- **一条 SQL 装载全部**：`corpus_chunks JOIN corpus_docs LEFT JOIN chunk_vectors`，
  避免 N+1 查询；
- **BM25 复用分词缓存**：`corpus_chunks.tokens_json` 里存着入库时的 jieba 结果，
  重建索引直接读缓存，不用重新分词 51 个片段；
- **科目过滤在打分前做**：`candidate_rows(subject_id)` 用 `np.nonzero` 先筛行号，
  只对候选行做矩阵乘法，不是先算全库再过滤；
- **自动重建**：`get_index()` 比对 `COUNT(*) FROM corpus_chunks` 与已加载行数，
  不一致就重载——入库脚本跑完不需要重启服务；
- **维度不匹配时优雅退化**：换了嵌入模型但没重建索引时，
  `dense_scores()` 返回空字典而不是抛异常，检索自动退化为纯 BM25。

**为什么暴力检索够用**：51 × 768 = 39168 个浮点数，一次矩阵乘法微秒级。
任何 ANN 库的索引构建 + 查询开销在这个规模上都比暴力算更慢。
涨到万级片段再换——`enable_load_extension` 在本机可用，
接 `sqlite-vec` 只需改 `dense_scores()` 一个方法。

### 5. 混合检索与拒答（`retriever.py`）：反幻觉核心

**排序公式**（归一化融合，只影响名次）：

```python
fused = 0.65 × max(cos, 0) + 0.35 × bm25_normalized      # HYBRID_ALPHA = 0.65
```

**拒答公式**（两个绝对量，决定是否调用大模型）：

```python
sparse_per_token = bm25_raw_top / max(len(query_tokens), 1)
relevant = (dense_top >= 0.53) or (sparse_per_token >= 1.15)
if not relevant:
    return "CPA教材中没有对应相关描述，请核对提问。"     # 根本不调用大模型
```

三个设计要点：

**(a) 用绝对量，不用排名。** 排名永远有第一名，用排名判定等于永不拒答。
稠密路取原始余弦（跨查询可比），稀疏路取 BM25 原始分。

**(b) BM25 必须除以查询词数。** BM25 原始分随查询词数近似线性增长：
"WACC"（1 词）原始分 4.10，"内含报酬率和净现值冲突怎么办"（19 词）原始分 60.15，差 15 倍。
按词平均后是 4.10 与 3.17，量级一致才能用同一个阈值卡。
早期版本用原始分卡 `1.2`，结果范围外的长问题（原始分 5~6）轻松越过阈值被当成命中教材。

**(c) 用 OR 而不是 AND。** `"WACC"` 的稠密相似度只有 0.41（低于 0.53），
但 BM25 按词均值 4.10 极高——教材里确实写了 `WACC = Σ(...)`。
反过来，口语化提问"请问一下老师，可变现净值到底怎么理解呢"稠密 0.50 而关键词稀疏。
两条路互为补充，任一达标即认为命中。

**阈值是标定出来的，不是拍脑袋。** [scripts/calibrate_refusal.py](../scripts/calibrate_refusal.py)
用 35 条范围内问题（含极短查询、口语化提问、换一种说法）
以及 18 条范围外问题（含"注会考试报名费多少钱""我该先考哪一科"这类
看起来像 CPA 但教材没写的干扰项）标定：

```text
范围内  dense [0.4084, 0.7148]   BM25/词 [1.36, 6.53]
范围外  dense [0.2663, 0.4737]   BM25/词 [0.00, 0.78]
当前阈值 relevance=0.53  sparse_per_token=1.15 → 误拒 0 条，误答 0 条
```

两组完全可分。**换语料或换嵌入模型后必须重跑标定**——不同模型的余弦分布完全不同，
沿用旧阈值必然两头都错。

每次检索都写 `retrieval_logs`（含 `refused` 标记），所以拒答率是可观测指标，
阈值标定坏了能立刻发现。

### 6. 增量入库（`ingest.py`）

**技术**：`hashlib.md5` 内容指纹 + 多格式解析。

| 格式 | 技术 |
| --- | --- |
| `.md` / `.txt` | 内置读取，utf-8 → utf-8-sig → gbk 依次尝试 |
| `.pdf` | PyMuPDF (`fitz`) 抽取纯文本 |
| `.docx` | python-docx，把 Heading 1/2/3 映射成 `#`/`##`/`###` |

`md5(文件内容)` 存进 `corpus_docs.file_hash`：内容没变直接跳过；变了则先
`DELETE FROM corpus_chunks WHERE doc_id=?`（级联删向量）再重插。
科目归属判定顺序：文件名 → 文件头 `# 科目` → 父目录名。
单个文件失败只记录该文件的 error，不中断整个目录。

---

## 六、Agent 层：意图识别 + 编排 + 提示词

### 1. 意图识别（`intent.py`）：规则与大模型互为兜底

**逻辑**：三类业务意图 —— `practice` 刷题 / `knowledge_qa` 知识点答疑 /
`question_qa` 针对某道题提问，外加一个非业务分支 `other`（问候/超范围）。

**技术**：正则 + 数据库实体链接（规则层），JSON 输出（大模型层）。

```text
detect(message) ──▶ rule_detect()   正则引擎，永远先跑一遍
              └──▶ llm_detect()    有 API Key 才跑，要求输出 JSON
                     ├─ 成功 ──▶ 以大模型结果为主，用规则结果补齐空字段
                     └─ 失败 ──▶ 完全采用规则结果（对话不中断）
```

规则层不是简单的关键词表，它做**实体链接**：`_link_entities()` 把用户话术
与数据库里**真实存在的**章节名/知识点名做字面匹配（含去掉"第X章"前缀后的字面），
取最长命中。所以"给我推送会计收入章节的题"能定位到 会计 → 第二十三章 收入，
而代码里没有硬编码任何章节名——换教材目录，意图识别自动跟着变。

意图判定的优先级：

1. 先用正则抽题号：`第\s*([0-9一二三四五六七八九十]+)\s*题`、`题目\s*(?:id|编号)?\s*[:：#]?\s*(\d{1,6})`
2. `wants_practice`：命中刷题动词表，**或**命中量词结构
   `[0-9一二两三四五六七八九十]+\s*道` / `(来|给我|出|做|抽|考|练)\s*[数字]\s*[道题个]`
3. `refers_question`：命中"这道题/这题/为什么选/没看懂"等
4. `practice` 优先于 `question_qa`（除非同时命中"指题"和"问解析"）
5. 短问候（≤12 字且命中问候词）→ `other`
6. 其余 → `knowledge_qa`

第 2 条的量词正则是修 bug 补上的：早期"来5道税法的多选"被判成 `knowledge_qa`，
因为动词表里只有"来几道""来十道"，没有"来5道"这种数字写法。

大模型的输出会被**收紧**而不是照抄：`subject` 过一遍别名归一化表
（"财务成本管理"→"财管"、"公司战略与风险管理"→"战略"）、`qtypes` 过白名单、
`limit` 截断到 1~50。模型返回越界值不会污染下游。

### 2. 大模型客户端（`llm.py`）：标准库实现多供应商

**逻辑**：四个环节（意图识别 / 答疑 / 题目讲解 / 主观题评分）各自独立配置模型，
互不影响。

**技术**：`urllib.request` + 两种报文风格适配，不装任何 SDK。

```python
if self.style == "anthropic":
    headers["x-api-key"] = key
    payload = {"model":..., "system": system, "messages":[{"role":"user",...}], ...}
else:  # openai 风格
    headers["Authorization"] = "Bearer " + key
    payload = {"model":..., "messages":[{"role":"system",...},{"role":"user",...}], ...}
```

两种风格覆盖 OpenAI、DeepSeek、通义（兼容模式）、Kimi、智谱、
本地 vLLM/Ollama 的 OpenAI 兼容端点，以及 Anthropic。加新供应商只需在
`config.LLM_PROVIDERS` 加一条 `base_url + api_key + style`，不写代码。

响应解析同时兼容两种结构（`content[].text` 与 `choices[0].message.content`），
所以同一份代码不需要分支就能处理两家的返回。

`chat_json()` 用 `parse_json_loose()` 容忍 markdown 代码块包裹与前后废话：
先整体 `json.loads`，失败再用 `re.compile(r"\{.*\}", re.S)` 抠最外层对象，
仍失败返回 `None` 交给上层兜底。**不做自动重试**——避免一次提问被静默放大成多次计费。

### 3. 编排（`orchestrator.py`）：路由与降级

```python
chat(message, user_id, session_id, current_question_id) -> {
    intent, reply, questions[], citations[], question, scope,
    mode, llm_used, degraded, refused, detector, rag, message_id }
```

流程：`detect` → `resolve_scope` → 写 user 轮 → 分派 → 写 assistant 轮。

**意图① 刷题**：调 `recommend.recommend()`，回复由 Python 模板拼装，
**不过大模型**——所以零 API Key 也完全可用。

**意图② 知识点答疑**：代码结构上**不存在**"跳过检索直接问大模型"的分支。

```python
rag_res = rag.retrieve_context(query, subject=..., user_id=...)
if not rag_res["found"]:
    return NO_CONTEXT_REPLY                    # 不调用大模型
if client.available():
    return client.chat(RAG_QA_SYSTEM_PROMPT, 问题 + 教材片段)
return _retrieval_only_answer(rag_res["hits"])  # 纯检索模式，原文直出
```

**意图③ 题目答疑**：三段上下文拼装。定位题目的兜底链是
话术里的题号 → 前端传的 `current_question_id` → 回溯本会话最近讲过的题 →
用户最近一条答题记录。检索式用 `知识点名 + 章节名 + 题干前80字 + 用户疑问` 拼成——
因为"这道题我没看懂"本身没有任何可检索的信息量。

### 4. 提示词（`prompts.py`）

**技术**：Python 字符串常量 + `str.format_map` 安全渲染。

```python
def render(template, **kwargs):
    class _Safe(dict):
        def __missing__(self, key): return ""     # 缺失占位符填空，不抛 KeyError
    return template.format_map(_Safe(**kwargs))
```

用 `format_map` + `__missing__` 而不是 f-string 或 `%`：
提示词模板有十几个占位符，任何一个缺失都不应该让整轮对话崩掉。

提示词的分层：`SYSTEM_PROMPT`（全局身份与红线）被
`RAG_QA_SYSTEM_PROMPT` 和 `QUESTION_QA_SYSTEM_PROMPT` 用字符串拼接复用，
所以"不许编造法条编号"这类约束只写一遍。全文见 [PROMPTS.md](PROMPTS.md)。

### 5. 降级矩阵：没有任何一格会导致 500

| 组件 | 首选技术 | 降级技术 | 用户可见影响 |
| --- | --- | --- | --- |
| 嵌入 | torch + text2vec | hashing 向量 | 语义泛化变弱 |
| 检索 | 稠密 + BM25 | 仅 BM25 | 换说法命中率下降 |
| 意图识别 | 大模型 JSON | 正则规则引擎 | 复杂长句抽参变粗 |
| 知识点答疑 | 大模型组织语言 | 教材原文直出 | 需自己读原文 |
| 题目答疑 | 大模型讲解 | 标准解析 + 原文 | 同上 |
| 主观题评分 | 大模型采点 | 关键词命中 | 评分粒度变粗 |
| PDF/Word 教材 | PyMuPDF / python-docx | 跳过该文件 | 少一份语料 |

需求要求"大模型先不配置"，所以未配置必须是**一等公民路径**而不是错误状态。

---

## 七、Web 层与前端

### 1. Flask 应用工厂 + Blueprint

**技术**：`create_app()` 工厂模式 + 单个 `/api` Blueprint + `teardown_appcontext`。

```python
app = Flask(__name__, template_folder=config.TEMPLATE_DIR, static_folder=config.STATIC_DIR)
app.json.ensure_ascii = False            # 中文 JSON 不转义成 \uXXXX

@app.teardown_appcontext
def _close_db(_exc=None):
    db.close_conn()                      # 请求结束归还线程连接
```

6 个 API 模块共用一个 `api_bp = Blueprint("api", __name__, url_prefix="/api")`，
在 `register_blueprints()` 里靠 import 触发路由注册。共 43 条路由。

**统一响应体**，前端只判断一个字段：

```python
def ok(data=None, **extra):   return jsonify({"ok": True, "data": data, **extra})
def fail(error, code=400):    return jsonify({"ok": False, "error": str(error)}), code
```

**`payload()` 同时兼容三种入参**（JSON body / form / query string），
所以同一个接口既能被 `fetch` 的 JSON 调用，也能直接在浏览器地址栏里试。

**`current_user()` 是接真实登录的唯一改动点**：

```python
def current_user() -> str:
    return payload().get("user_id") or config.DEFAULT_USER_ID
```

所有业务函数的第一个入参都是 `user_id`，表结构里也都有这一列，
接登录只改这一个函数，不动表也不动业务层。

### 2. 前端：原生 JS，无构建步骤

**技术**：ES6 + `fetch` + `sessionStorage` + CSS 自定义属性。8 个模板、9 个 JS、2 个 CSS。

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `common.js` | 87 | `CPA.get/post` 统一解包 `{ok,data}`、`CPA.el()` 声明式建 DOM、跨页题目队列 |
| `question.js` | 194 | **题卡唯一实现** `CPA.renderQuestion`：五种题型作答控件 + 提交判分 + 结果渲染 |
| `chat.js` | 188 | 聊天组件 `CPA.mountChat`：意图标签、教材来源折叠区、可点击题卡 |
| `page_*.js` | 754 | 六个页面各自的装配逻辑 |

三个技术选择值得说明：

**(a) `el()` 而不是模板字符串拼 HTML。**

```javascript
el('div', {class: 'q-card'}, [el('p', {text: q.stem}), ...])
```

`{text: ...}` 走 `node.textContent` 赋值，`el()` 虽然也支持 `{html: ...}`（`innerHTML`），
但全站 9 个 JS 文件里**没有一处调用它**——所有来自数据库的文本都走 `textContent`。
这不是洁癖：教材原文和题干里有大量 `<` `>` `&`（比如"账面价值 > 计税基础"），
用 `innerHTML` 拼接会直接吞掉内容或错位。

**(b) 题卡只有一份实现。** 题库页、答题页、错题本页、聊天页四处都调
`CPA.renderQuestion(q, opts)`。五种题型的作答控件、判分后的对错高亮、
正确答案、采分点命中、标准解析——改一处四处同时生效，交互天然一致。

**(c) `sessionStorage` 做跨页题目队列。**

```javascript
CPA.setQueue(questions);          // 题库页/聊天页组好一组题
location.href = '/practice';      // 跳转
CPA.takeQueue();                  // 答题页取出，取完即清
```

"从题库页选一批题去答题页做"这个动作不需要后端建会话，也不用把题目 id
全塞进 URL。

**关键交互链路**：聊天里点题目 → 就地作答 → 判分结果回写错题本，
这条链兑现了"Agent 必须能用聊天方式触发刷题"。
反向也通了：做题时点「问 AI 这道题」，`CPA.askAboutQuestion(q)`
会把该题设为聊天上下文并自动发出追问。

### 3. 静态一致性检查

前端写错接口路径这类 bug 只在浏览器里暴露，后端冒烟测试抓不到。
[scripts/check_frontend.py](../scripts/check_frontend.py) 用
`app.url_map.iter_rules()` 取出全部已注册路由，正则扫出 JS 里所有 `/api/...` 字符串
和模板里所有 `url_for(...)`，逐一比对。当前 19 个接口、全部静态资源与端点引用一致。

---

## 八、脚本与验证

| 脚本 | 技术 | 作用 |
| --- | --- | --- |
| `init_db.py` | `argparse` + `db.init_db()` | 建库 + 种子（`--reset` 重建） |
| `ingest_corpus.py` | 调 `rag.ingest` | 教材入库（`--force/--clear/--dir`） |
| `calibrate_refusal.py` | 53 条标注问题跑 `retriever.search` | 标定拒答阈值，输出两组分数区间 |
| `smoke_test.py` | Flask `test_client()` | **81 项**端到端检查，覆盖九大块 |
| `check_frontend.py` | `url_map` + 正则 | 前后端引用一致性 |

`smoke_test.py` 用 Flask 自带的 `test_client()` 而不是 `requests` + 起服务：
不占端口、不需要等启动、可以直接在 CI 里跑。

**实测数字**（本机，CPU）：

| 项目 | 耗时/结果 |
| --- | --- |
| 嵌入模型冷加载 | 0.9s |
| 6 个教材文件入库（含模型加载） | 38.3s → 51 个切片全部向量化 |
| 单次混合检索（51 切片） | 毫秒级 |
| BM25 索引重建（读分词缓存） | ~20ms（不读缓存约 300ms） |
| 冒烟测试 | 81 项通过，0 失败 |
| 拒答标定 | 范围内 35 条误拒 0，范围外 18 条误答 0 |

---

## 九、技术清单速查

| 想改什么 | 改哪个文件 | 涉及技术 |
| --- | --- | --- |
| 表结构 | `app/schema.sql` | SQL DDL |
| 连接/事务行为 | `app/db.py` | `sqlite3` + `threading.local` |
| 判分规则 | `app/business/grading.py` | Python 集合运算 |
| 推送配额与权重 | `app/business/recommend.py` | `math.log` + `random.Random` |
| 模拟卷题型配比 | `app/business/paper.py` | `MOCK_MIX` 常量 |
| 切片粒度 | `config.CHUNK_SIZE / CHUNK_OVERLAP` | 正则状态机 |
| 嵌入模型 | `config.EMBEDDING_MODEL` | transformers |
| 检索融合权重 | `config.HYBRID_ALPHA` | numpy |
| 拒答松紧 | `config.RELEVANCE_THRESHOLD / SPARSE_PER_TOKEN_MIN` | 标定脚本 |
| 大模型供应商 | `config.LLM_PROVIDERS / TASK_MODELS` | urllib |
| 提示词 | `app/agent/prompts.py` | 字符串常量 |
| 意图关键词/正则 | `app/agent/intent.py` | `re` |
| 题卡渲染 | `web/static/js/question.js` | 原生 DOM |
| 配色/暗色主题 | `web/static/css/app.css` | CSS 自定义属性 |

### 环境变量（不改代码即可调整）

```powershell
$env:CPA_PORT = "9000"                    # 端口
$env:CPA_DB_PATH = "D:\other\cpa.db"      # 数据库位置
$env:CPA_EMBED_BACKEND = "hashing"        # 强制不用 torch
$env:CPA_RELEVANCE_THRESHOLD = "0.50"     # 放宽拒答
$env:CPA_SPARSE_PER_TOKEN_MIN = "1.00"
$env:CPA_DEEPSEEK_KEY = "sk-xxxx"         # 启用大模型
$env:CPA_DEBUG = "1"                      # 改代码自动重载
```

---

## 十、这套技术栈的边界

诚实说明它撑不住什么：

1. **单机单用户**：没有登录、没有权限、没有并发压测。`user_id` 已贯穿所有表和函数，
   接登录不需要改表，但会话、密码、越权校验都得另做。
2. **SQLite 的写并发**：WAL 允许多读一写，但仍是**单写者**。
   几十人同时提交答案没问题，几百人并发写需要换 PostgreSQL
   （业务层的 SQL 基本可平移，`db.py` 要重写）。
3. **暴力向量检索**：万级片段以上要换向量库（只改 `index.dense_scores()` 一处）。
4. **拒答阈值与语料强绑定**：换语料/换模型不重标阈值，会出现
   "教材里有的被拒答"或"教材里没有的被硬答"。这是这套方案最需要维护的一个参数。
5. **Flask 开发服务器**：`app.run()` 只适合本地。上线要用
   `waitress-serve --port=8000 "app.webapp:create_app()"`（Windows）
   或 gunicorn + nginx（Linux）。
6. **主观题评分**：无大模型时是关键词命中，只能给粗粒度反馈，不能替代人工阅卷。
7. **示例数据是演示规模**：27 道题、51 个教材片段。算法逻辑与规模无关，
   但推送算法要在有几百条答题记录后才明显体现分层效果。

