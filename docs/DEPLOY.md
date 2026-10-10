# 部署与运行

本项目零外部服务依赖：数据库是单文件 SQLite，向量索引在进程内，未配置大模型 API
也能跑通全部功能（答疑降级为"纯检索模式"，直接返回教材原文片段与来源）。

## 1. 环境要求

| 项 | 要求 | 本机实测 |
| --- | --- | --- |
| Python | 3.10+ | 3.14.2（`C:\Python314\python.exe`） |
| 操作系统 | Windows / macOS / Linux | Windows 10 Pro |
| 内存 | 建议 2GB 以上（加载中文向量模型约 400MB） | — |
| 磁盘 | 约 500MB（含向量模型缓存） | — |

## 2. 安装依赖

```bash
cd D:\cpa-agent
pip install -r requirements.txt
```

说明：

- `fastapi` / `uvicorn` / `starlette` / `itsdangerous` / `python-multipart` / `jieba` / `numpy`
  是必需的（`starlette`/`itsdangerous` 是登录会话签名 cookie 用的，
  `python-multipart` 是管理员上传教材的表单解析用的）；
- `torch` + `transformers` 用于中文语义向量。**没装也能跑**，
  系统会自动降级到 `hashing` 向量后端（关键词能力保留、语义泛化变弱），
  启动日志里会打印一行警告；
- `PyMuPDF` / `python-docx` 只在你要导入 PDF / Word 教材时需要；
- 本项目**不使用 `sentence-transformers`**（该环境下 import 会触发
  0xC0000005 访问违例导致进程崩溃），改用 `transformers.AutoModel` +
  手写 mean pooling + L2 归一化实现，效果等价。详见
  [ARCHITECTURE.md](ARCHITECTURE.md#向量化为什么不用-sentence-transformers)。

首次运行会从 HuggingFace 缓存加载 `shibing624/text2vec-base-chinese`（768 维）。
如果本机没有该模型缓存且没有外网，把 `config.EMBEDDING_BACKEND` 改成 `"hashing"`
即可完全离线运行。

## 3. 一键启动（首次）

```bash
python run.py --setup
```

`--setup` 等价于 `--init --ingest`，会依次完成：

1. 按 `app/schema.sql` 建库建表（13 张表 / 18 个索引 / 2 个视图）；
2. 写入三级分类种子：6 科目 → 24 章节 → 72 知识点；
3. 写入 27 道示例题（覆盖单选/多选/判断/简答/综合五种题型）；
4. 把 `data/corpus/*.md` 六科教材切片、向量化入库（51 个片段）；
5. 保证至少有一个管理员账号存在（首次运行会创建，密码**只在控制台打印一次**，
   请立即记下；后续运行若已存在管理员账号则跳过这一步）；
6. 启动 Web 服务。

看到下面这几行就说明启动成功，**务必记下管理员密码**（只显示这一次）：

```text
==============================================================
已创建管理员账号：admin
初始密码（只显示这一次）：xxxxxxxxxxxx
下次可用 CPA_ADMIN_PASSWORD 环境变量指定固定密码
==============================================================
CPA 题库与 AI 答疑智能体已启动： http://127.0.0.1:8000
```

打开浏览器访问 `http://127.0.0.1:8000` 会先跳转到 `/login`，
用上面打印的 `admin` 账号密码登录后即可进入首页，登录后从右上角导航进入「管理后台」。

如果不想让密码随机生成，启动前设置固定密码：

```powershell
$env:CPA_ADMIN_PASSWORD = "你的密码"
python run.py --setup
```

> 忘记管理员密码怎么办？本项目没有邮箱找回流程（单机部署的合理简化），
> 只能直接改数据库：用另一个管理员账号在后台改密码；如果只有一个管理员且密码丢了，
> 需要手写脚本用 `app.business.auth.hash_password()` 重新生成
> `password_hash`/`password_salt`/`hash_iterations` 三列后写回 `users` 表。

## 4. 日常启动

```bash
python run.py                  # 直接启动
python run.py --port 9000      # 换端口
python run.py --reload         # 开发模式（改代码自动重载）
python run.py --host 0.0.0.0   # 允许局域网访问
```

## 5. 分步执行（可选）

```bash
python scripts/init_db.py                 # 只建库 + 种子数据
python scripts/init_db.py --reset         # 删库重建（会清空所有答题数据）
python scripts/ingest_corpus.py           # 教材入库（内容未变的文件自动跳过）
python scripts/ingest_corpus.py --force   # 强制重切片重向量化
python scripts/ingest_corpus.py --clear   # 先清空知识库再入库
python scripts/ingest_corpus.py --dir D:\my_corpus   # 指定其他语料目录
python scripts/smoke_test.py              # 端到端冒烟测试（131 项检查）
python scripts/calibrate_refusal.py       # 标定"教材里没有"的拒答阈值
```

## 6. 页面导航

| 路径 | 页面 | 主要功能 |
| --- | --- | --- |
| `/login` | 登录/注册页 | 学员登录，或自助注册（可用 `CPA_ALLOW_REGISTER=0` 关闭自助注册） |
| `/` | 首页 | 学习概览、知识点画像、快速取题、内嵌聊天框 |
| `/bank` | 科目章节题库页 | 三级分类树导航、题型/难度筛选、分页、组卷 |
| `/practice` | 答题页 | 顺序做题、提交即判分、标准解析、进度统计 |
| `/wrongbook` | 错题本页 | 按科目/知识点筛选、就地重做、移除、恢复 |
| `/stats` | 答题历史统计页 | 总览、每日趋势、分科目/章节/知识点/题型、明细分页 |
| `/chat` | AI 答疑页 | 聊天触发刷题、知识点答疑（带教材溯源）、就地做题 |
| `/admin` | 管理后台（仅管理员） | 账号管理、教材上传/下架/删除、上传任务进度 |

未登录访问以上任何业务页面都会跳转到 `/login?next=...`；学员访问 `/admin`
返回 403 页面，接口层同理返回 `403`。

## 7. 导入你自己的教材

1. 把文件放进 `data/corpus/`，支持 `.md` / `.txt` / `.pdf` / `.docx`；
2. 文件名带上科目名（如 `会计-第十八章所得税.md`），或在文件开头写 `# 会计`，
   系统据此判定科目归属，检索时才能按科目过滤；
3. 用标题层级标出章节与知识点，切片器会把它们变成检索元数据与引用来源：

   ```markdown
   # 会计                     ← 科目
   ## 第十八章 所得税          ← 章节
   ### 暂时性差异的确认        ← 知识点
   正文段落……
   ```

   PDF / Word 没有 `#` 标记时，会退化为按"第X章""第X节"正则识别；
   Word 的"标题 1/2/3"样式会自动映射为 `#` / `##` / `###`。
4. 执行 `python scripts/ingest_corpus.py`，然后重启服务（或调
   `POST /api/rag/reindex`）。

> `data/corpus/` 里自带的六个 `.md` 是**要点整理**，用于演示检索链路，不是官方教材原文。
> 正式使用请替换为你自己有权使用的教材文本。

## 8. 导入你自己的题目

`data/seed/questions_seed.py` 里用 `q(...)` 函数声明题目，字段与
`app/schema.sql` 的 `questions` 表一一对应：

```python
q("会计", "第十八章 所得税", 2, "single",
  "题干……", OPT4("选项A", "选项B", "选项C", "选项D"), "C",
  "标准解析……", difficulty=3, label="难", score=2.0, source="2023年真题")
```

第 3 个参数是该章节下的知识点序号（从 1 开始）。改完执行：

```bash
python scripts/init_db.py     # 幂等，按 (知识点, 题干) 去重，不会重复插入
```

要接入外部题库，写一个脚本按同样的字段调
`app/business/questions.py` 的插入逻辑即可，不必走种子文件。

## 9. 常见问题

**Q：AI 答疑一直回"CPA教材中没有对应相关描述"。**
知识库是空的，或者问题确实超出教材范围。先看 `GET /api/rag/health`：
`index.chunks` 为 0 就执行 `python scripts/ingest_corpus.py`。
如果确认教材里有、但仍被拒答，跑 `python scripts/calibrate_refusal.py`
重新标定阈值（换了语料或嵌入模型后必须重标）。

**Q：答疑回复开头是"当前未配置大模型 API"。**
这是设计行为。没有 API Key 时系统运行在纯检索模式，直接返回教材原文片段 + 来源，
不做任何自由生成。要开启大模型润色与讲解，见
[LLM_INTEGRATION.md](LLM_INTEGRATION.md)。

**Q：启动时打印"本地嵌入模型加载失败，降级为 hashing"。**
`torch`/`transformers` 没装，或模型缓存不存在。系统仍可用，但语义检索能力下降。

**Q：数据库锁死 / 报 database is locked。**
本项目已开启 WAL 与线程局部连接。若仍出现，检查是否有其他进程（如上一次未退出的
`run.py`）占用 `data/cpa.db`。

**Q：忘记管理员密码 / 想重置某个账号密码。**
用另一个管理员账号登录后台改密码；如果只有一个管理员且密码丢了，需要直接操作数据库
（见 [3. 一键启动](#3-一键启动首次)最后一条说明）。

**Q：想清空我的练习数据重新开始。**
`python scripts/init_db.py --reset`（会清空答题记录、错题本、对话历史，
随后需要重新执行 `scripts/ingest_corpus.py`；账号数据不受影响）。

## 10. 生产部署提示

本项目默认面向单机演示，正式上线还需要：

- **固定会话密钥**：设置环境变量 `CPA_SECRET` 为一个不泄露的随机字符串，
  否则每次重启进程都会生成新密钥，所有已登录用户被强制登出；
- 用多进程/多 worker 运行 uvicorn 提升并发承载，例如
  `python run.py --workers 4`（`--reload` 与 `--workers>1` 不能同时用），
  或用 `gunicorn -k uvicorn.workers.UvicornWorker -w 4 "app.webapp:create_app()"`（Linux）；
- 生产环境建议设置 `CPA_SESSION_HTTPS_ONLY=1`，配合反向代理的 HTTPS 终止；
- 给自助注册视情况关闭：`CPA_ALLOW_REGISTER=0`（内部工具场景通常应关闭，
  改成由管理员在后台建号）；
- 给 `POST /api/rag/reindex`、教材上传等管理接口的权限已经收在 `AdminUser` 依赖里，
  上线前确认没有被绕过（比如反向代理层不要转发未登录的 `/admin` 静态资源之外的内容）；
- 题量与语料变大后，把 `chunk_vectors` 的暴力检索换成向量库
  （见 [ARCHITECTURE.md](ARCHITECTURE.md#向量检索为什么先用暴力matmul)）；
- 用户量变大后，SQLite 单写者会成为瓶颈，评估迁移到 PostgreSQL
  （见 [ARCHITECTURE.md](ARCHITECTURE.md#九已知限制)）。
