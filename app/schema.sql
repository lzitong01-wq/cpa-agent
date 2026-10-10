-- =============================================================
-- CPA 考试题库与答疑助手 —— SQLite 表结构
-- 七张核心业务表：科目 / 章节 / 知识点 / 试题 / 答题记录 / 错题关联 / 对话历史
-- 另附四张 RAG 支撑表：教材文档 / 教材切片 / 切片向量 / 检索日志
-- 执行方式：python scripts/init_db.py
-- =============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ------------------------------------------------- 1. 科目表（一级分类）
CREATE TABLE IF NOT EXISTS subjects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT    NOT NULL UNIQUE,          -- accounting / audit / finance / tax / law / strategy
    name        TEXT    NOT NULL UNIQUE,          -- 会计 / 审计 / 财管 / 税法 / 经济法 / 战略
    full_name   TEXT,                             -- 《会计》科目全称
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 2. 章节表（二级分类）
CREATE TABLE IF NOT EXISTS chapters (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    code        TEXT    NOT NULL,                 -- 科目内唯一，如 ch03
    name        TEXT    NOT NULL,                 -- 第三章 存货
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (subject_id, code)
);

-- ------------------------------------------------- 3. 知识点表（三级分类）
CREATE TABLE IF NOT EXISTS knowledge_points (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter_id  INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    -- subject_id 为冗余字段：按科目直接筛知识点时避免 join，写入由业务层保证一致
    subject_id  INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    code        TEXT    NOT NULL,                 -- 章节内唯一，如 kp0301
    name        TEXT    NOT NULL,                 -- 存货的初始计量
    description TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (chapter_id, code)
);

-- ------------------------------------------------- 4. 试题表
-- qtype: single 单选 / multiple 多选 / judge 判断 / short 简答 / comprehensive 综合
-- options_json: [{"key":"A","text":"..."}]，判断题与简答题可为 NULL
-- answer_json:  single "A" | multiple ["A","C"] | judge true
--               short/comprehensive {"reference":"...","points":["要点1","要点2"]}
CREATE TABLE IF NOT EXISTS questions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    qtype              TEXT    NOT NULL
        CHECK (qtype IN ('single', 'multiple', 'judge', 'short', 'comprehensive')),
    subject_id         INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    chapter_id         INTEGER NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    knowledge_point_id INTEGER NOT NULL REFERENCES knowledge_points(id) ON DELETE CASCADE,
    stem               TEXT    NOT NULL,          -- 题干
    material           TEXT,                      -- 综合题的背景资料
    options_json       TEXT,                      -- 选项
    answer_json        TEXT    NOT NULL,          -- 正确答案
    analysis           TEXT    NOT NULL,          -- 标准解析
    difficulty         INTEGER NOT NULL DEFAULT 2 CHECK (difficulty BETWEEN 1 AND 5),
    difficulty_label   TEXT    NOT NULL DEFAULT '中' CHECK (difficulty_label IN ('易', '中', '难')),
    score              REAL    NOT NULL DEFAULT 2.0,
    source             TEXT,                      -- 真题年份 / 自编
    is_active          INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 5. 答题记录表
-- 每次提交一行，永不覆盖，是推送算法和统计页的唯一数据源
CREATE TABLE IF NOT EXISTS answer_records (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT    NOT NULL,
    question_id        INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    -- 三级分类冗余落库：历史统计不受题目后续改分类影响
    subject_id         INTEGER NOT NULL,
    chapter_id         INTEGER NOT NULL,
    knowledge_point_id INTEGER NOT NULL,
    user_answer_json   TEXT,                      -- 用户作答原文
    is_correct         INTEGER NOT NULL CHECK (is_correct IN (0, 1)),
    score              REAL    NOT NULL DEFAULT 0,
    duration_ms        INTEGER NOT NULL DEFAULT 0,
    mode               TEXT    NOT NULL DEFAULT 'chapter'
        CHECK (mode IN ('chapter', 'mock', 'wrong', 'recommend', 'agent', 'paper')),
    paper_id           TEXT,                      -- 组卷练习归组用
    answered_at        TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 6. 错题关联表
-- 答错自动 upsert；status=removed 表示用户手动移出错题本（保留痕迹不物理删除）
CREATE TABLE IF NOT EXISTS wrong_questions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            TEXT    NOT NULL,
    question_id        INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    subject_id         INTEGER NOT NULL,
    chapter_id         INTEGER NOT NULL,
    knowledge_point_id INTEGER NOT NULL,
    wrong_count        INTEGER NOT NULL DEFAULT 1,   -- 累计错次，推送算法按此加权
    redo_count         INTEGER NOT NULL DEFAULT 0,   -- 重做次数
    consecutive_right  INTEGER NOT NULL DEFAULT 0,   -- 连续做对次数，达标可自动脱离
    status             TEXT    NOT NULL DEFAULT 'open'
        CHECK (status IN ('open', 'resolved', 'removed')),
    first_wrong_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_wrong_at      TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at         TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (user_id, question_id)
);

-- ------------------------------------------------- 7. 对话历史表
-- intent: practice 刷题 / knowledge 知识点答疑 / question 针对题目提问 / other
-- citations_json: [{"subject":"会计","chapter":"第十八章 所得税","score":0.82,"text":"..."}]
CREATE TABLE IF NOT EXISTS chat_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT    NOT NULL,
    session_id     TEXT    NOT NULL,
    role           TEXT    NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content        TEXT    NOT NULL,
    intent         TEXT,
    subject_id     INTEGER,                       -- 本轮限定的科目（若有）
    question_id    INTEGER,                       -- 意图③时关联的题目
    citations_json TEXT,                          -- 教材溯源引用
    meta_json      TEXT,                          -- 模型名、耗时、降级标记等
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ================================================= RAG 支撑表
-- ------------------------------------------------- 8. 教材文档表
CREATE TABLE IF NOT EXISTS corpus_docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    title       TEXT    NOT NULL,
    source_path TEXT    NOT NULL UNIQUE,
    file_hash   TEXT,                             -- 内容指纹，重复入库自动跳过
    char_count  INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    ingested_at TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- 以下三列供管理员上传功能使用（旧库由 scripts/migrate.py 补齐）
    uploaded_by TEXT    REFERENCES users(id) ON DELETE SET NULL,  -- NULL = 随项目自带
    visibility  TEXT    NOT NULL DEFAULT 'public'
                CHECK (visibility IN ('public', 'hidden')),        -- hidden 不参与检索
    origin      TEXT    NOT NULL DEFAULT 'bundled'
                CHECK (origin IN ('bundled', 'upload'))            -- 来源，便于区分与清理
);

-- ------------------------------------------------- 9. 教材切片表
-- 检索元数据随切片一起落库：科目 / 章节 / 知识点，支持按科目过滤检索
CREATE TABLE IF NOT EXISTS corpus_chunks (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id               INTEGER NOT NULL REFERENCES corpus_docs(id) ON DELETE CASCADE,
    subject_id           INTEGER,
    subject_name         TEXT,
    chapter_name         TEXT,                    -- 从教材标题行解析
    knowledge_point_name TEXT,
    ord                  INTEGER NOT NULL,        -- 文档内顺序
    text                 TEXT    NOT NULL,
    char_count           INTEGER NOT NULL DEFAULT 0,
    tokens_json          TEXT,                    -- jieba 分词结果缓存，供 BM25 使用
    created_at           TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 10. 切片向量表
-- vec 为 float32 小端字节串，加载时 numpy.frombuffer 直接还原
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY REFERENCES corpus_chunks(id) ON DELETE CASCADE,
    dim      INTEGER NOT NULL,
    model    TEXT    NOT NULL,
    vec      BLOB    NOT NULL,
    created_at TEXT  NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 11. 检索日志表（可观测性）
CREATE TABLE IF NOT EXISTS retrieval_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       TEXT,
    query         TEXT    NOT NULL,
    subject_id    INTEGER,
    top_score     REAL,
    hit_count     INTEGER NOT NULL DEFAULT 0,
    refused       INTEGER NOT NULL DEFAULT 0,     -- 1 = 触发"教材中无对应描述"
    elapsed_ms    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- ------------------------------------------------- 12. 用户表（登录与角色）
-- id 为业务主键，其余表的 user_id 都引用它（历史数据用 'demo_user' 这类字符串，
-- 迁移脚本会把它们挂到同名账号下，所以类型保持 TEXT 而不是 INTEGER）。
-- password_hash 为 PBKDF2-HMAC-SHA256 的十六进制串，salt 独立存储，绝不存明文。
CREATE TABLE IF NOT EXISTS users (
    id             TEXT    PRIMARY KEY,
    username       TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    display_name   TEXT,
    password_hash  TEXT    NOT NULL,
    password_salt  TEXT    NOT NULL,
    hash_iterations INTEGER NOT NULL DEFAULT 240000,
    role           TEXT    NOT NULL DEFAULT 'student'
                   CHECK (role IN ('admin', 'student')),
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_login_at  TEXT
);

-- ------------------------------------------------- 13. 教材上传任务表
-- 向量化耗时较长（单文件约 6 秒，长 PDF 可达数分钟），上传接口不能同步等待。
-- 管理员上传后先落一行 pending，后台线程处理并更新状态，前端轮询这张表。
CREATE TABLE IF NOT EXISTS ingest_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER REFERENCES corpus_docs(id) ON DELETE SET NULL,
    filename     TEXT    NOT NULL,          -- 用户上传时的原始文件名
    stored_path  TEXT    NOT NULL,          -- 落盘后的安全路径
    subject_id   INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    status       TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    chunk_count  INTEGER NOT NULL DEFAULT 0,
    message      TEXT,                      -- 失败原因 / 成功摘要
    uploaded_by  TEXT    REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    started_at   TEXT,
    finished_at  TEXT
);

-- ================================================= 索引
CREATE INDEX IF NOT EXISTS idx_chapters_subject     ON chapters (subject_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_kp_chapter           ON knowledge_points (chapter_id, sort_order);
CREATE INDEX IF NOT EXISTS idx_kp_subject           ON knowledge_points (subject_id);
CREATE INDEX IF NOT EXISTS idx_q_subject            ON questions (subject_id, is_active);
CREATE INDEX IF NOT EXISTS idx_q_chapter            ON questions (chapter_id, is_active);
CREATE INDEX IF NOT EXISTS idx_q_kp                 ON questions (knowledge_point_id, is_active);
CREATE INDEX IF NOT EXISTS idx_q_type               ON questions (qtype, is_active);
CREATE INDEX IF NOT EXISTS idx_rec_user_time        ON answer_records (user_id, answered_at DESC);
CREATE INDEX IF NOT EXISTS idx_rec_user_kp          ON answer_records (user_id, knowledge_point_id);
CREATE INDEX IF NOT EXISTS idx_rec_user_subject     ON answer_records (user_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_wrong_user_status    ON wrong_questions (user_id, status);
CREATE INDEX IF NOT EXISTS idx_wrong_user_kp        ON wrong_questions (user_id, knowledge_point_id, status);
CREATE INDEX IF NOT EXISTS idx_chat_session         ON chat_history (user_id, session_id, id);
CREATE INDEX IF NOT EXISTS idx_chunk_doc            ON corpus_chunks (doc_id, ord);
CREATE INDEX IF NOT EXISTS idx_chunk_subject        ON corpus_chunks (subject_id);
CREATE INDEX IF NOT EXISTS idx_users_role           ON users (role, is_active);
CREATE INDEX IF NOT EXISTS idx_jobs_status          ON ingest_jobs (status, id DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_uploader        ON ingest_jobs (uploaded_by, id DESC);

-- ================================================= 视图
-- 知识点维度正确率：推送算法「薄弱知识点」判定与统计页共用
CREATE VIEW IF NOT EXISTS v_kp_accuracy AS
SELECT r.user_id,
       r.subject_id,
       r.chapter_id,
       r.knowledge_point_id,
       COUNT(*)                                             AS total,
       SUM(r.is_correct)                                    AS correct,
       ROUND(1.0 * SUM(r.is_correct) / COUNT(*), 4)         AS accuracy,
       MAX(r.answered_at)                                   AS last_answered_at
FROM answer_records r
GROUP BY r.user_id, r.knowledge_point_id;

-- 科目维度概览：统计页首屏
CREATE VIEW IF NOT EXISTS v_subject_accuracy AS
SELECT r.user_id,
       r.subject_id,
       COUNT(*)                                             AS total,
       SUM(r.is_correct)                                    AS correct,
       ROUND(1.0 * SUM(r.is_correct) / COUNT(*), 4)         AS accuracy,
       COUNT(DISTINCT r.knowledge_point_id)                 AS kp_touched,
       MAX(r.answered_at)                                   AS last_answered_at
FROM answer_records r
GROUP BY r.user_id, r.subject_id;
