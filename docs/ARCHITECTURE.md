# 实现逻辑说明

本文说明每个部分"为什么这么做、具体怎么做"。只讲设计与关键算法，
技术栈清单见 [TECH_STACK.md](TECH_STACK.md)，
接口清单见 [DEPLOY.md](DEPLOY.md)，提示词全文见 [PROMPTS.md](PROMPTS.md)。

---

## 一、总体分层

```text
┌─────────────────────────────────────────────────────────────┐
│  web/templates + web/static        8 个页面，无前端框架        │
└───────────────────────────┬─────────────────────────────────┘
                            │ fetch JSON（带会话 cookie）
┌───────────────────────────▼─────────────────────────────────┐
│  app/api/*                 HTTP 层：鉴权依赖 + 参数解析与序列化 │
│  app/api/deps.py           CurrentUser / AdminUser 依赖注入   │
└──────────┬────────────────────────────────┬─────────────────┘
           │                                │
┌──────────▼──────────────┐    ┌────────────▼─────────────────┐
│  app/business/*         │    │  app/agent/*                 │
│  业务层（无 AI 依赖）     │◀───│  Agent 层（意图/编排/提示词）  │
│  auth·分类·题目·判分·记录 │    │  只调业务层与 RAG 服务         │
│  错题本·推送·组卷·统计   │    └────────────┬─────────────────┘
└──────────┬──────────────┘                 │
           │                   ┌────────────▼─────────────────┐
           │                   │  app/rag/service.py          │
           │                   │  RAG 独立服务门面             │
           │                   │  切片·向量·BM25·混合检索·拒答  │
           │                   └────────────┬─────────────────┘
┌──────────▼──────────────────────────────────▼───────────────┐
│  app/db.py + app/schema.sql          SQLite（单文件，WAL）    │
└─────────────────────────────────────────────────────────────┘
```

四条边界是刻意维持的：

1. **业务层不 import agent 层，也不 import rag 层。** 所以把 `app/agent`
   整个删掉，题库、判分、错题本、推送算法、统计依然能跑。
   唯一的"钩子"是 `records.submit_answer(..., llm_grader=None)`——
   传入才用，不传就用规则评分。
2. **Agent 层不写 SQL。** 它拿到的是 `business.recommend.recommend()`、
   `catalog.resolve_scope()` 这样的业务函数，以及 `rag.service` 的检索结果。
   换 Web 框架、换成命令行、换成微信机器人，Agent 层不动。
3. **RAG 只通过 `app/rag/service.py` 对外。** 里面的 embedder / chunker /
   bm25 / index / retriever 都是内部实现。要把检索拆成独立微服务，
   只需把 `service.py` 的 6 个函数改成 HTTP 调用，上层零改动
   （`app/api/rag_api.py` 已经把这 6 个函数暴露成了 HTTP 接口）。
4. **鉴权只在 API 层的依赖注入里做一次。** 业务函数的签名不认识
   "请求""会话"这些概念，只认识已经解析好的 `user_id: str`；
   `app/api/deps.py` 的 `CurrentUser`/`AdminUser` 是唯一知道"当前是谁、
   是不是管理员"的地方，业务层和 Agent 层永远拿到的是一个确定的字符串。

---

## 二、数据模型（`app/schema.sql`：13 表 / 18 索引 / 2 视图）

### 三级分类：严格三层，不折叠

```sql
subjects(id, code, name, full_name, sort_order)
chapters(id, subject_id→subjects, code, name, sort_order)      UNIQUE(subject_id, code)
knowledge_points(id, chapter_id→chapters, subject_id→subjects,  UNIQUE(chapter_id, code)
                 code, name, sort_order)
```

`knowledge_points` 冗余存了 `subject_id`。这是有意的：推送算法和统计要
"按科目筛知识点"，冗余一列换掉每次 `JOIN chapters`，
一致性由 `seed_catalog()` 单点写入保证。

### 试题：五种题型共用一张表

```sql
questions(id, qtype CHECK IN ('single','multiple','judge','short','comprehensive'),
          subject_id, chapter_id, knowledge_point_id,   -- 三级定位，缺一不可
          stem, material, options_json, answer_json, analysis,
          difficulty 1-5, difficulty_label CHECK IN ('易','中','难'),
          score, source, is_active)
```

- `options_json`：`[["A","选项文本"], ...]`，判断题与主观题为 `NULL`；
- `answer_json`：单选 `"C"`；多选 `["A","C"]`；判断 `true`；
  主观题 `{"points": ["要点1", "要点2", ...]}` —— 主观题的"答案"就是采分点数组，
  判分逻辑直接按点命中率算分；
- `material`：综合题的背景资料，与题干分开存，前端单独渲染成引用块；
- 五种题型不拆表，因为筛选、组卷、推送、统计全都要跨题型混合查询，
  拆表会把每个查询变成 5 路 UNION。

### 答题记录：宽表 + 冗余三级 id

```sql
answer_records(id, user_id, question_id, subject_id, chapter_id, knowledge_point_id,
               user_answer_json, is_correct, score, duration_ms,
               mode CHECK IN ('chapter','mock','wrong','recommend','agent','paper'),
               paper_id, answered_at)
```

需求要求"持久保存每条答题记录，包含科目章节知识点"。这里把三级 id
冗余进记录行，而不是每次 `JOIN questions`：

- 统计页要按科目/章节/知识点聚合，冗余后是单表 `GROUP BY`；
- 题目将来被修改分类（比如从"第二章"挪到"第三章"），历史记录仍反映当时的归属，
  这在统计口径上是正确的。

### 错题本：状态机而非简单删除

```sql
wrong_questions(id, user_id, question_id, subject_id, chapter_id, knowledge_point_id,
                wrong_count,        -- 累计错次，推送算法的权重
                redo_count,         -- 重做次数
                consecutive_right,  -- 连续做对次数
                status CHECK IN ('open','resolved','removed'),
                first_wrong_at, last_wrong_at, updated_at)
UNIQUE(user_id, question_id)
```

三个状态的含义：`open` 待攻克 / `resolved` 连对 2 次自动脱离 / `removed` 用户手动移除。
用 `status` 而非物理删除，是因为：

- 用户手动移除后，如果再次做错，`mark_wrong()` 会把它重新置为 `open`
  并且 `wrong_count` 继续累加——历史错次不丢；
- "已攻克"的题仍留在库里，统计页能算出"攻克率"。

### 对话历史

```sql
chat_history(id, user_id, session_id, role CHECK IN ('user','assistant','system'),
             content, intent, subject_id, question_id,
             citations_json,   -- 教材溯源引用，前端直接复原折叠区
             meta_json,        -- 模型名、是否降级、是否拒答、检索分数
             created_at)
```

`meta_json` 里记了 `refused` / `degraded` / `llm_used` / `rag`，
所以事后能审计"哪些提问被拒答了、哪些是纯检索模式回答的"。

### RAG 四张表

```sql
corpus_docs(id, subject_id, title, source_path UNIQUE, file_hash, char_count, chunk_count,
            uploaded_by→users, visibility CHECK IN ('public','hidden'),
            origin CHECK IN ('bundled','upload'))
corpus_chunks(id, doc_id→corpus_docs ON DELETE CASCADE, subject_id,
              subject_name, chapter_name, knowledge_point_name,  -- 溯源元数据
              ord, text, char_count, tokens_json)
chunk_vectors(chunk_id PRIMARY KEY →corpus_chunks ON DELETE CASCADE, dim, model, vec BLOB)
retrieval_logs(id, user_id, query, subject_id, top_score, hit_count, refused, elapsed_ms)
```

- `file_hash`：内容指纹，重复入库自动跳过，改动后自动覆盖旧切片；
- `tokens_json`：jieba 分词结果缓存。BM25 索引重建时直接读缓存，
  不用把 51 个片段重新分词一遍（冷启动从约 300ms 降到约 20ms）；
- `vec BLOB`：float32 小端字节序，`numpy.frombuffer` 零拷贝还原；
- `retrieval_logs.refused`：拒答次数是可观测指标，阈值标定坏了能立刻发现；
- `corpus_docs.uploaded_by/visibility/origin`：支持管理员上传教材全站共享——
  `origin='upload'` 区分随项目自带的教材，`visibility='hidden'` 可以临时下架
  一份教材而不删除它（下架后立即不参与检索，重新上架立即恢复，见五、6）。

### 两个新增表：账号与上传任务

```sql
users(id, username UNIQUE COLLATE NOCASE, display_name,
      password_hash, password_salt, hash_iterations,
      role CHECK IN ('admin','student'), is_active, created_at, last_login_at)
ingest_jobs(id, doc_id→corpus_docs, filename, stored_path, subject_id,
            size_bytes, status CHECK IN ('pending','processing','done','failed'),
            chunk_count, message, uploaded_by→users, created_at, started_at, finished_at)
```

`users.id` 是 `TEXT` 而不是自增整数——历史数据（加登录前）里的 `answer_records.user_id`
等字段存的是 `'demo_user'` 这类裸字符串，`scripts/migrate.py` 把它们收编成同名账号
（初始密码 = 用户名 + `'123'`）时，账号 id 直接复用这个字符串，不需要做一次 id 换算。

`ingest_jobs` 存在的原因：教材向量化耗时较长（单文件约 6 秒，长 PDF 可达数分钟），
管理员上传接口不能同步等到向量化完成才返回。上传接口先落一行 `pending`，
后台线程处理并更新 `status`/`chunk_count`/`message`，前端轮询这张表拿进度；
服务重启时若发现 `processing` 状态的任务（说明上次是被杀掉的），会在启动时补跑。

### 两个视图：把正确率计算下沉到数据库

```sql
CREATE VIEW v_kp_accuracy AS
SELECT user_id, subject_id, chapter_id, knowledge_point_id,
       COUNT(*) total, SUM(is_correct) correct,
       ROUND(1.0*SUM(is_correct)/COUNT(*), 4) accuracy, MAX(answered_at) last_answered_at
FROM answer_records GROUP BY user_id, knowledge_point_id;
-- v_subject_accuracy 同理，按 user_id, subject_id 聚合
```

推送算法、统计页、首页画像三处都要"每个知识点的正确率"，
写成视图避免三份重复 SQL 漂移。

### 连接管理（`app/db.py`）

- `PRAGMA journal_mode=WAL`：读写不互斥，聊天写对话历史时不会挡住做题；
- `PRAGMA foreign_keys=ON`：SQLite 默认关闭外键，必须显式打开，
  `ON DELETE CASCADE` 才生效（删一个 doc 会带走它的 chunks 与 vectors）；
- `threading.local()` 存连接：FastAPI 默认用线程池执行同步的 `def` 路由/依赖，
  每个线程持有一条独立连接，进程内长期复用（不是每请求开关一次）；
  应用关闭时 `lifespan` 钩子调用 `db.close_conn()` 统一收尾；
- `transaction()` 上下文管理器：`with db.transaction() as conn` 内的多条语句
  同生共死。判分 → 写记录 → 更新错题本三步就是在同一个事务里，
  不会出现"记录写了但错题本没更新"。

---

## 三、业务层实现

### 1. 三级分类与"中文话术 → id"解析

`catalog.resolve_scope(subject=, chapter=, knowledge_point=)` 是 Agent 层能听懂
"给我推送会计收入章节的题"的关键。它接受**中文名 / code / 数字 id 混合输入**，
逐级解析并互相补齐：

- 科目：先试 id → 试 code/name 精确匹配 → 再用
  `WHERE ? LIKE '%'||name||'%'` 反向模糊匹配（能吃下"会计科目""《会计》"）；
- 章节：`code = ? OR name = ? OR name LIKE '%'||?||'%'`，
  已知科目时加科目约束，命中多条取 `LENGTH(name)` 最短的（最精确的那个）；
- 知识点：同理，命中后**反向补齐** `chapter_id` 与 `subject_id`
  —— 用户只说"暂时性差异"，也能定位到 会计 → 第十八章 所得税。

实测：`resolve_scope(subject="会计", chapter="所得税", knowledge_point="暂时性差异")`
→ `{'subject_id': 1, 'chapter_id': 3, 'knowledge_point_id': 8}`。

`full_tree()` 用 3 条查询 + 2 个聚合拼出整棵带题量的树（而不是 N+1 查询），
题库页左侧导航一次请求渲染完。

### 2. 判分（`business/grading.py`）：五种题型五套规则

```python
grade(question, user_answer, llm_grader=None) -> {
    is_correct, score, full_score, earned_ratio,
    correct_answer, detail, hit_points, missed_points }
```

| 题型 | 规则 |
| --- | --- |
| 单选 | 归一化后全等 |
| 多选 | 集合相等得满分；**是正确答案的真子集且无错选得 50%**；有错选得 0 |
| 判断 | `_norm_bool` 归一化，接受 `对/错/正确/错误/√/×/true/false/A/B` |
| 简答 | 采分点命中率 |
| 综合 | 采分点命中率（与简答同逻辑，只是采分点更多） |

多选的"漏选给一半"不是随手定的：CPA 多选题实际计分规则是漏选按比例得分、
错选不得分，这里用 50% 近似，比"非全对即错"更贴近真实反馈。
阈值集中在 `SUBJECTIVE_PASS_RATIO = 0.6`（主观题得分率达到 6 成算"正确"）
和 `POINT_HIT_RATIO = 0.5`（一个采分点的关键词命中一半算答到）。

主观题两条路径：

- 配了大模型 → `app/agent/grader.py` 采点评分，返回 `earned_ratio`；
- 没配 / 调用失败 / 返回值不合法 → **静默回退**关键词命中法。
  回退是 `try/except` 兜住的，用户不会看到报错，只是评分粒度粗一些。

### 3. 答题记录：单一写入口

`records.submit_answer()` 是全站**唯一**的答题写入路径。
题库页做题、错题本就地重做、聊天里点题作答，三条 UI 路径最终都走它：

```text
grade() → INSERT answer_records → wrongbook.mark_wrong/mark_right → 返回反馈+解析
                       └──────────── 同一个事务 ────────────┘
```

`mark_wrong` / `mark_right` 的签名是 `(conn, user_id, ...)` ——
它们接收调用方的连接，而不是自己开一个。这样错题本的更新和答题记录的写入
在同一事务里，不可能只成功一半。

`mark_right` 的逻辑值得单独说：答对时 `consecutive_right += 1`，
达到 `RESOLVE_STREAK = 2` 就把 `status` 置为 `resolved` 自动脱离错题本。
"连对两次才算掌握"避免了蒙对一次就把题从错题本里放走。

### 4. 推送算法（`business/recommend.py`）：四桶配额，明确不随机

需求点名"避免纯随机出题"。算法分三步：

**第一步：把知识点分进四个桶**（`build_profile`）

| 桶 | 判定条件 | 权重公式 |
| --- | --- | --- |
| `wrong` 错题关联 | 错题本 `status='open'` 涉及的知识点 | 累计错次 `SUM(wrong_count)` |
| `weak` 薄弱 | 正确率 < `WEAK_ACC_MAX=0.7` | `(1-acc) × log(total+1) + 0.1` |
| `fresh` 未练 | 该知识点下有题但从未作答 | 等权 |
| `mastered` 已掌握 | 正确率 ≥ 0.8 且答题数 ≥ 2 | 等权 |

`weak` 的权重里乘 `log(total+1)`：只做过 1 题错了 1 题（正确率 0）
和做过 10 题对了 3 题（正确率 0.3），后者的"薄弱"证据更充分，
应该优先补。不乘这个因子，样本量 1 的噪声会霸占推送位。

**第二步：按配额分配题数**（`_alloc`）

```python
QUOTA = (("wrong", 0.40), ("weak", 0.35), ("fresh", 0.15), ("mastered", 0.10))
```

配额是"错题优先、其次薄弱、混少量已掌握巩固"的直接编码。
空桶（比如新用户没有错题）的配额会**重新分配**给非空桶，
所以新用户 10 道题会变成 `fresh` 8~9 道 + `mastered` 1~2 道，
而不是只出 1.5 道题。

**第三步：桶内选知识点，知识点内选题**

- 选知识点：`_weighted_pick` 按权重做无放回抽样
  （权重高的知识点更可能被选中，但不是永远只出最高的那一个，避免刷题单调）；
- 选题：`_order_candidates` 按"没做过 > 做错过 > 做对过"排序；
  `wrong` 桶反过来，优先出做错过的那道题本身；
  `mastered` 桶优先挑难题（巩固要有挑战性）；
- 兜底：配额没填满时用范围内未做过的题回填，标注为"范围内补充题目"。

**每道题都带 `reason` 与 `reason_bucket`**，一路透传到前端显示
"选题依据：错题关联知识点"。这既是产品透明度，也是算法的自证——
用户能看出这不是随机出题。

实测（`scripts/smoke_test.py`）：
`{'错题关联知识点': 4, '尚未练习的知识点': 2, '范围内补充题目': 4}`。

三种刷题模式在同一个 `recommend()` 入口分派：

- `mode='chapter'` 专项章节：带 `chapter_id`/`knowledge_point_id` 约束跑上面的算法；
- `mode='mock'` 模拟：转 `paper.build_mock()`，按题型配比
  `单选40% 多选24% 判断16% 简答12% 综合8%` 抽题，抽不满的题型用其他题型补总量；
- `mode='wrong'` 错题：转 `wrongbook.redo_set()`，按累计错次降序。

### 5. 组卷（`business/paper.py`）

`build_paper()` 支持三级分类任意层级 + 题型 + 难度筛选，返回
`paper_id` / `questions` / `total_score` / `type_distribution` /
`available`（符合条件的题库总量，用于提示"你只筛出了 3 题但库里有 40 题"）。
整卷提交走 `records.submit_batch()`，逐题判分后汇总总分与正确率，
`paper_id` 落到每条记录上，事后可按卷查回。

### 6. 统计（`business/stats.py`）

`dashboard()` 一次返回统计页需要的 7 组数据（总览 / 分科目 / 分章节 /
最薄弱知识点 / 最擅长知识点 / 分题型 / 每日趋势 / 模式分布），
避免前端串行发 7 个请求。分科目、分章节用 `LEFT JOIN` 保证
"一道题都没做的科目"也出现在列表里（正确率显示 0 而不是消失）。

### 7. 账号与权限（`business/auth.py` + `api/deps.py`）

**密码存储**：`hashlib.pbkdf2_hmac('sha256', ...)`，迭代 240,000 次，
每账号独立随机 salt，校验用 `hmac.compare_digest` 常量时间比较。
不引入 bcrypt/argon2 是为了不增加二进制依赖；迭代次数写进表里而不是写死在代码里，
将来提高强度时旧账号仍能登录（登录成功后按新参数自动重算）。

**登录态**：会话数据存在 Starlette `SessionMiddleware` 签名的 cookie 里
（只放 `id`/`username`/`role`，不放密码），依赖 `config.SECRET_KEY`（环境变量
`CPA_SECRET`）签名。**每个请求都会用 cookie 里的 `id` 回库查一次最新状态**——
账号被停用或角色被管理员改动后，下一个请求立刻生效，不需要等 cookie 过期或用户重新登录。
这是 `app/api/deps.py::session_user()` 做的事，比只信任 cookie 内容更安全但多一次
主键查询（`users` 表几十到几千行规模下可忽略）。

**鉴权是依赖注入，不是装饰器**：

```python
CurrentUser = Annotated[dict, Depends(require_user)]   # 未登录 → 401
AdminUser   = Annotated[dict, Depends(require_admin)]  # 非管理员 → 403
```

业务接口的函数签名里写 `user: CurrentUser` 才能拿到 `user_id`；忘了写，
接口在类型检查阶段就缺一个必需参数，而不是"忘了加装饰器但代码照样跑，鉴权被跳过"。
这比 Flask 时代"手写 `current_user()` 从请求体取 `user_id`、默认值兜底"的方式
从结构上杜绝了漏鉴权。

**账号数据隔离**：所有业务函数的第一个入参都是 `user_id`，且这个值只可能来自
`CurrentUser`/`AdminUser` 依赖（从会话解析），**接口不接受请求体/query string 里的
`user_id`**——客户端传什么都会被忽略。`scripts/smoke_test.py` 专门测了"请求里伪造
`user_id` 拿不到别人的数据"这一条。

**旧数据迁移**（`ensure_legacy_users()`）：加登录之前，`answer_records` 等表的
`user_id` 是裸字符串（如 `'demo_user'`）。`scripts/migrate.py` 扫描这些表里出现过的
所有 `user_id`，为每个建一个同名账号（初始密码 = 用户名 + `'123'`），
这样老数据不用改一行就能在登录体系下继续访问。

**首次启动保证有管理员**（`ensure_bootstrap_admin()`）：`users` 表为空时自动创建
`admin` 账号，密码优先取环境变量 `CPA_ADMIN_PASSWORD`，否则随机生成并**只在
控制台打印这一次**——所以首次 `python run.py --setup` 之后一定要记下这行输出，
忘记了只能去数据库里直接重置 `password_hash`/`password_salt`（没有"忘记密码"邮件找回，
这是单机部署的合理简化）。

---

## 六、RAG 层实现

整条链路：

```text
教材文件 ──chunker──▶ 切片(带三级元数据) ──embedder──▶ 768维向量 ──▶ SQLite
                                        └──jieba────▶ tokens 缓存 ──▶ SQLite
用户提问 ──▶ 向量 + 分词 ──▶ 稠密余弦 ⊕ BM25 ──融合排序──▶ Top5
                              └────── 绝对阈值判定 ──▶ 不达标则拒答
```

### 1. 切片（`rag/chunker.py`）：标题层级就是检索元数据

约定 `#` = 科目、`##` = 章节、`###` = 知识点，正文按 420 字切、80 字重叠。
PDF/Word 没有 `#` 标记时退化为 `第X章`/`第X节` 正则识别；
Word 的"标题 1/2/3"在读取阶段就映射成了 `#`/`##`/`###`。

两个不显然但关键的处理：

**(a) 标题边界不带重叠。** `close(carry_overlap=False)`：
遇到标题换段时，新切片**不能**带上一节的尾巴。
早期版本无条件重叠，结果"五力模型"这个切片的正文开头是上一节 PEST 的内容，
引用来源标着"产业环境分析（五力模型）"但展示的原文是宏观环境分析——**溯源标错了**。
只在因长度超限而切分时才重叠。

**(b) 检索文本额外带面包屑。** 每个切片存两份文本：

- `text`：纯正文，用于展示与引用摘录；
- `search_text = "科目 · 章节 · 知识点\n" + text`：用于向量化与分词。

因为教材正文往往不重复标题里的词。用户问"五力模型"，
而那段正文可能通篇在说"潜在进入者的进入威胁、替代品的替代威胁……"。
把面包屑并入检索文本后，同一个问题的 BM25 按词均值从 1.16 升到更稳的区间，
不再被误判成"教材里没有"。这一步把切片数从 79 降到 51（重叠碎片消失），
检索质量反而提升。

**(c) 文档开头的引用块跳过。** `> 本文件为……要点整理` 这类声明性文字
会被切成独立片段，并在模糊查询下抢占排名第一。
只跳过"第一个 `##` 之前的 `>` 行"——正文里的引用块（法条摘录）照常保留。

### 2. 向量化（`rag/embedder.py`）：三级后端 + 静默降级

```text
local（默认）  transformers AutoModel + mean pooling + L2 归一化
                shibing624/text2vec-base-chinese，768 维，完全离线
api            POST {base_url}/embeddings，需二次开发适配报文
hashing（兜底） jieba + md5 哈希技巧 + 中文 bigram（0.3 权重），512 维
```

`get_embedder()` 是单例；本地模型加载失败（没装 torch、没有模型缓存）
时打一条 warning 然后返回 `HashingEmbedder`，**不抛异常**。
后果是语义泛化能力下降、关键词能力保留，系统继续可用。

#### 向量化为什么不用 sentence-transformers

本机环境下 `from sentence_transformers import SentenceTransformer`
会以 `0xC0000005`（ACCESS_VIOLATION，退出码 `-1073741819`）直接崩掉进程。
逐库排查确认 numpy / scipy / sklearn / jieba / torch / transformers / PyMuPDF
全部正常，只有 sentence-transformers 的 import 崩溃。

sentence-transformers 对 `text2vec` 这类模型做的事情就是
"AutoModel forward → 按 attention_mask 做 mean pooling → L2 归一化"，
所以直接用 `transformers.AutoModel` 手写这 20 行，效果等价、依赖更少：

```python
out = self.model(**enc)                       # last_hidden_state
mask = enc["attention_mask"].unsqueeze(-1).float()
vec = (out.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
vec = torch.nn.functional.normalize(vec, p=2, dim=1)
```

实测：加载 0.9s，768 维，三条测试查询 top-1 全部命中正确文档
（余弦 0.712 / 0.768 / 0.479）。

### 3. BM25（`rag/bm25.py`）：中文分词 + bigram 补充

`k1=1.5, b=0.75`，`idf = log(1 + (n - df + 0.5)/(df + 0.5))`。
分词是 jieba + 停用词过滤 + **中文 bigram 补充**：
jieba 会把"暂时性差异"切成"暂时"/"性"/"差异"，
补上相邻二字组合能提高专业术语的命中率。

### 4. 混合检索与排序（`rag/index.py` + `rag/retriever.py`）

```python
fused = HYBRID_ALPHA × max(cos, 0) + (1 - HYBRID_ALPHA) × bm25_normalized
        # HYBRID_ALPHA = 0.65
```

排序用**归一化**后的 BM25（把当前查询的 BM25 分压到 0~1），
只影响名次。科目过滤在打分前做（`candidate_rows(subject_id)`），
不是先算全库再筛，省掉无用计算。

#### 向量检索为什么先用暴力matmul

51 个片段 × 768 维 = 一个 39K 浮点数的矩阵，一次 `numpy` 矩阵乘法
在这个规模上是**微秒级**，比任何向量库的索引构建 + 查询开销都低。
`CorpusIndex.load()` 用**一条** SQL 把 chunks + docs + vectors 关联出来，
`numpy.frombuffer` 零拷贝还原成矩阵常驻内存，
并在 `COUNT(*) FROM corpus_chunks` 与已加载行数不一致时自动重建。

教材涨到几万片段（约 10 万行以上教材）时再换向量库：
`sqlite3.enable_load_extension` 在本机是可用的，
接 `sqlite-vec` 只需改 `index.dense_scores()` 一个方法。

维度不匹配（换了嵌入模型但没重建索引）时，`dense_scores()` 返回空字典
而不是抛异常，检索退化为纯 BM25 并打日志提示重建。

### 5. 反幻觉：拒答判定

这是需求里最硬的一条：**"检索不到必须明确告知，禁止编造"**。
实现上做了三重保障，而不是只靠提示词：

**第一重（程序级，最关键）：绝对阈值判定。**

```python
relevant = (dense_top >= RELEVANCE_THRESHOLD)        # 0.53，原始余弦
        or (sparse_per_token >= SPARSE_PER_TOKEN_MIN) # 1.15，BM25 ÷ 查询词数
if not relevant:
    return NO_CONTEXT_MESSAGE   # 根本不调用大模型
```

两个信号都是**绝对量**（跨查询可比），不是排名——
排名永远有第一名，用排名判定等于永不拒答。

为什么 BM25 要除以查询词数：BM25 原始分随查询词数近似线性增长。
"WACC"（1 个词）原始分 4.10，"内含报酬率和净现值冲突怎么办"（19 个词）原始分 60.15，
差 15 倍；按词平均后是 4.10 与 3.17，量级一致，才能用同一个阈值卡。
早期版本用原始分卡 `BM25_ABS_MIN=1.2`，结果范围外的长问题（原始分 5~6）
轻松越过阈值被当成"命中教材"。

为什么是 OR 而不是 AND：`"WACC"` 这种英文缩写的稠密相似度只有 0.41
（低于 0.53），但 BM25 按词均值 4.10 极高——教材里确实写了 `WACC = Σ(...)`。
反过来，口语化提问"请问一下老师，可变现净值到底怎么理解呢"
稠密 0.50 而关键词稀疏。两条路互为补充，任一达标即认为命中。

**阈值不是拍脑袋定的。** `scripts/calibrate_refusal.py` 用
35 条范围内问题（含极短查询、口语化提问、换一种说法）
以及 18 条范围外问题（含"注会考试报名费多少钱""我该先考哪一科"
这类看起来像 CPA 但教材没写的干扰项）标定：

```text
范围内  dense [0.4084, 0.7148]   BM25/词 [1.36, 6.53]
范围外  dense [0.2663, 0.4737]   BM25/词 [0.00, 0.78]
当前阈值 relevance=0.53 sparse_per_token=1.15 → 误拒 0 条，误答 0 条
```

两组完全可分。**换语料或换嵌入模型后必须重跑标定**，
因为不同模型的余弦分布完全不同，沿用旧阈值必然两头都错。

**第二重（提示词级）：** `RAG_QA_SYSTEM_PROMPT` 第 3 条要求
"如果全部片段都与问题无关，只输出这一句话"。
覆盖"检索分数够了但片段其实答不了这个问题"的情况。

**第三重（降级级）：** 没配大模型时，答疑**不生成任何文字**，
直接把教材原文片段 + 来源列出来。这条路径下幻觉概率为零。

**引用来源**：`format_citations()` 输出
`{no, subject, chapter, knowledge_point, doc_title, score, excerpt}`，
编号与提示词里 `[n]` 一一对应，前端渲染成可折叠的"教材来源"区。

### 6. 增量入库（`rag/ingest.py`）

`md5(文件内容)` 存进 `corpus_docs.file_hash`：内容没变直接跳过；
变了则先 `DELETE FROM corpus_chunks WHERE doc_id=?`（级联删向量）再重插。
科目归属判定顺序：文件名 → 文件头 `# 科目` → 父目录名。
单个文件失败只记录该文件的 error，不中断整个目录的入库。

---

## 七、Agent 层实现

### 1. 意图识别（`agent/intent.py`）：规则与大模型互为兜底

```text
detect(message) ──▶ rule_detect()  规则引擎，永远先跑一遍
              └──▶ llm_detect()   有 API Key 才跑，输出 JSON
                     ├─ 成功 ──▶ 以大模型结果为主，用规则结果补齐空字段
                     └─ 失败 ──▶ 完全采用规则结果（对话不中断）
```

**规则引擎不是简单的关键词表**，它做了实体链接：
`_link_entities()` 把用户话术与**数据库里真实存在的**章节名/知识点名做
字面匹配（含去掉"第X章"前缀后的字面），取最长命中。
所以"给我推送会计收入章节的题"能定位到 会计 → 第二十三章 收入，
而不需要在代码里硬编码章节名。

意图判定的优先级（`rule_detect`）：

1. 先抽题号（`第3题` / `题目12`）；
2. `wants_practice`：命中刷题动词表，**或**命中量词结构
   `[数字]道` / `(来|给我|出|做|抽)[数字](道|题|个)`；
3. `refers_question`：命中"这道题/这题/为什么选/没看懂"等；
4. `practice` 优先于 `question_qa`（除非同时命中"指题"和"问解析"）；
5. 短问候（≤12 字命中问候词）→ `other`，走引导话术；
6. 其余 → `knowledge_qa`。

第 2 条的量词结构是补上的：早期版本 "来5道税法的多选" 被判成
`knowledge_qa`（动词表里只有"来几道""来十道"，没有"来5道"），
改成正则匹配任意数字后修复。

`mode` 判定：命中错题词 → `wrong`；模拟/套卷 → `mock`；
识别出章节或知识点 → `chapter`；否则 → `recommend`。

大模型结果会被**收紧**而不是照抄：`subject` 过一遍别名归一化表、
`qtypes` 过白名单、`limit` 截断到 1~50。模型返回越界值不会污染下游。

### 2. 编排（`agent/orchestrator.py`）

```python
chat(message, user_id, session_id, current_question_id) -> {
    intent, reply, questions[], citations[], question, scope,
    mode, llm_used, degraded, refused, detector, rag, message_id }
```

流程：`detect` → `resolve_scope` → 写 user 轮 → 分派 → 写 assistant 轮。

**意图① 刷题** → `recommend.recommend()`，回复由模板拼装（不过大模型），
所以零 API Key 也完全可用。用户指定了题型时，先按题型过滤推送结果，
不足再用同条件的题库补齐（标注"按你指定的题型补充"）。
错题本为空时不返回空列表，而是主动提示"要不要我按知识点给你推送 5 道"。

**意图② 知识点答疑** → **必须先** `rag.retrieve_context()`：

```python
if not rag_res["found"]:
    return NO_CONTEXT_REPLY        # 不调用大模型
if llm.available():
    return llm.chat(RAG_QA_SYSTEM_PROMPT, 问题 + 检索到的教材片段)
return 教材原文片段 + 引用来源      # 纯检索模式
```

代码结构上**不存在**"跳过检索直接问大模型"的分支。

**意图③ 题目答疑** → 三段上下文拼装：

1. 定位题目：话术里的题号 → 前端传的 `current_question_id` →
   回溯本会话最近一次推送/讲解过的题 → 用户最近一条答题记录；
2. 取题目全文 + 正确答案 + 标准解析（这一路才需要 `with_answer=True`）；
3. 检索式用 `知识点名 + 章节名 + 题干前80字 + 用户疑问` 拼成
   （"这道题我没看懂"本身没有可检索的信息量），
   并用该题的 `subject_id` 过滤检索范围。

降级路径下输出"题目 + 正确答案 + 标准解析 + 教材原文拓展"，
不做任何自由生成。

### 3. 大模型客户端（`agent/llm.py`）

只用标准库 `urllib`，不装任何 SDK。两种报文风格 `openai` / `anthropic`
覆盖了 OpenAI、DeepSeek、通义、Kimi、智谱、本地 vLLM/Ollama 与 Anthropic。
四个环节（`intent` / `qa` / `question_explain` / `grading`）
**各自独立判断可用性**：只配了 `qa` 的 Key，答疑用大模型、
意图识别回落规则、主观题评分回落要点命中法，互不影响。

`chat_json()` 容忍 markdown 代码块包裹与前后废话（`parse_json_loose`
先整体解析，失败再正则抠最外层 `{...}`），解析失败返回 `None` 交给上层兜底。
不做自动重试——避免一次提问被静默放大成多次计费。

### 4. 降级矩阵

| 组件 | 首选 | 失败/未配置时 | 用户可见影响 |
| --- | --- | --- | --- |
| 嵌入 | 本地 text2vec | hashing 向量 | 语义泛化变弱 |
| 检索 | 稠密 + BM25 | 仅 BM25 | 换说法命中率下降 |
| 意图识别 | 大模型 JSON | 规则引擎 | 复杂长句抽参变粗 |
| 知识点答疑 | 大模型组织 | 教材原文直出 | 需自己读原文 |
| 题目答疑 | 大模型讲解 | 标准解析 + 原文 | 同上 |
| 主观题评分 | 大模型采点 | 关键词命中 | 评分粒度变粗 |

**没有任何一格的失败会导致 500 或功能不可用**，这是刻意设计的：
需求要求"大模型先不配置"，那么未配置就必须是一等公民路径，而不是错误状态。

---

## 八、API 与前端

### API 层（`app/api/`）

统一响应体 `{"ok": bool, "data": ..., "error": str}`，
`payload()` 同时兼容 JSON body / form / query string。

**鉴权已从"信任客户端"改成"服务端会话"**——见六、7 的详细说明。
简单说：`app/api/deps.py` 定义 `CurrentUser`/`AdminUser` 两个 FastAPI 依赖，
业务接口在函数签名里声明 `user: CurrentUser` 即可拿到经会话校验的 `user_id`，
未登录自动 401、非管理员访问管理接口自动 403，都不需要接口自己写 if 判断。
业务层函数签名不变（第一个参数仍是 `user_id: str`），改动只发生在 API 层。

按业务切 8 个文件：`catalog_api` / `practice_api` / `wrongbook_api` /
`stats_api` / `chat_api` / `rag_api` / `auth_api` / `admin_api`，全部挂在
`/api` 前缀下，共 54 条路由（不含页面路由）。

### 前端（`web/`）：不用框架的理由与做法

8 个页面（含登录页、管理后台）、约 1500+ 行 JS，无构建步骤、无 node_modules、
`python run.py` 之后直接可用。三个共享模块避免重复：

- `common.js`：`CPA.get/post`（统一解包 `{ok, data}` 并把 `ok:false` 抛成异常）、
  `el()` 声明式建 DOM、`setQueue/takeQueue`（用 `sessionStorage`
  在页面间传递"当前要做的一组题"）；
- `question.js`：**题目卡片的唯一实现**。五种题型的作答控件、提交判分、
  结果渲染（对错高亮、正确答案、采分点命中、标准解析）都在这里。
  题库页/答题页/错题本页/聊天页四处复用同一份，交互天然一致；
- `chat.js`：聊天组件，被首页内嵌聊天框和 `/chat` 页共用。
  渲染意图标签、教材来源折叠区、可点击的题目卡片。

关键交互：**聊天里点题目 → 立即在旁边作答 → 判分结果回写错题本**，
这条链路兑现了"Agent 必须能用聊天方式触发刷题"。
`CPA.askAboutQuestion(q)` 反向打通——做题时点"问 AI 这道题"，
会把该题设为聊天上下文并自动发出追问。

---

## 九、已知限制

1. **示例数据是演示规模**：27 道题、6 个科目各 4 章、51 个教材片段。
   算法逻辑与真实规模无关，但推送算法要在有几百条答题记录后才明显体现分层。
2. **教材语料是要点整理，不是官方原文**，仅用于验证检索链路。
   正式使用必须替换为有权使用的教材文本，并重跑
   `ingest_corpus.py --force` + `calibrate_refusal.py`。
3. **登录与权限已实现，但仍是单机简化版**：会话密钥 `CPA_SECRET` 未设置时
   每次重启随机生成（所有人掉线重新登录），多用户部署必须固定设置；
   没有"忘记密码"找回流程，管理员密码丢失只能直接改数据库；
   没有登录失败次数限制/账号锁定（暴力破解防护）。
4. **拒答阈值与语料强绑定**：换语料/模型不重标阈值，
   会出现"教材里有的被拒答"或"教材里没有的被硬答"。
5. **暴力向量检索**：万级片段以上需要换向量库（改 `index.dense_scores()` 一处）。
6. **主观题评分**：无大模型时是关键词命中，只能给出粗粒度反馈，
   不能替代人工阅卷。
7. **SQLite 单写者**：WAL 允许多读一写，几十人同时提交答案没问题，
   几百人并发写需要换 PostgreSQL（业务层 SQL 基本可平移，`app/db.py` 要重写）。
