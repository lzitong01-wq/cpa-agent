# -*- coding: utf-8 -*-
"""SQLite 访问层：连接管理、建库、通用查询helper。

只用标准库 sqlite3，不引入 ORM —— 需求要求交付完整建表 SQL，
把 DDL 留在 app/schema.sql 里本身就是交付物。
"""
import os
import sqlite3
import threading
from contextlib import contextmanager

import config

_local = threading.local()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_conn() -> sqlite3.Connection:
    """每线程一个连接（Flask 多线程 + 后台脚本共用同一套代码）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _local.conn = _connect()
    return conn


def close_conn(_exc=None) -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def transaction():
    """显式事务；异常自动回滚。"""
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query_all(sql: str, params=()) -> list:
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params=()):
    row = get_conn().execute(sql, params).fetchone()
    return dict(row) if row is not None else None


def query_scalar(sql: str, params=(), default=None):
    row = get_conn().execute(sql, params).fetchone()
    if row is None or row[0] is None:
        return default
    return row[0]


def execute(sql: str, params=()) -> int:
    """单条写入，返回 lastrowid。"""
    with transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def executemany(sql: str, seq) -> int:
    with transaction() as conn:
        cur = conn.executemany(sql, seq)
        return cur.rowcount


def table_exists(name: str) -> bool:
    return query_one(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name = ?", (name,)
    ) is not None


def init_db(schema_path: str = None) -> None:
    """执行 schema.sql 建库（全部语句都是 IF NOT EXISTS，可重复执行）。"""
    schema_path = schema_path or config.SCHEMA_PATH
    with open(schema_path, "r", encoding="utf-8") as f:
        ddl = f.read()
    conn = get_conn()
    conn.executescript(ddl)
    conn.commit()


def db_stats() -> dict:
    """首页/健康检查用的行数概览。"""
    tables = [
        "subjects", "chapters", "knowledge_points", "questions",
        "answer_records", "wrong_questions", "chat_history",
        "corpus_docs", "corpus_chunks", "chunk_vectors",
    ]
    out = {}
    for t in tables:
        out[t] = query_scalar("SELECT COUNT(*) FROM %s" % t, (), 0) if table_exists(t) else -1
    return out


def reset_db() -> None:
    """删库重建，仅供开发调试（scripts/init_db.py --reset）。"""
    close_conn()
    for suffix in ("", "-wal", "-shm"):
        p = config.DB_PATH + suffix
        if os.path.exists(p):
            os.remove(p)
    init_db()
