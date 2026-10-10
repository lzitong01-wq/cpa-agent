# -*- coding: utf-8 -*-
"""增量迁移：给已有数据库补上账号系统需要的表与列，不动业务数据。

schema.sql 里全是 CREATE TABLE IF NOT EXISTS，新表会自动建，
但给已存在的表加列必须显式 ALTER。本脚本幂等，可以反复执行。

    python scripts/migrate.py            # 迁移 + 收编历史 user_id + 建首个管理员
    python scripts/migrate.py --dry-run  # 只看要做什么
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from app import db  # noqa: E402

# (表, 列, 列定义) —— 顺序即执行顺序
ADD_COLUMNS = [
    ("corpus_docs", "uploaded_by", "TEXT"),
    ("corpus_docs", "visibility", "TEXT NOT NULL DEFAULT 'public'"),
    ("corpus_docs", "origin", "TEXT NOT NULL DEFAULT 'bundled'"),
]


def columns_of(table: str) -> set:
    return {r["name"] for r in db.query_all("PRAGMA table_info(%s)" % table)}


def main():
    ap = argparse.ArgumentParser(description="CPA 数据库迁移")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作")
    args = ap.parse_args()

    if not os.path.exists(config.DB_PATH):
        print("数据库不存在，直接跑 python scripts/init_db.py 即可（无需迁移）")
        return 1
    print("数据库：%s" % config.DB_PATH)

    # 1. 建新表（users / ingest_jobs 等）
    print("\n[1/4] 执行 schema.sql（新表与索引会被创建，已存在的跳过）")
    if not args.dry_run:
        db.init_db()
    for t in ("users", "ingest_jobs"):
        print("  %-12s %s" % (t, "存在" if db.table_exists(t) else "缺失"))

    # 2. 补列
    print("\n[2/4] 检查 corpus_docs 的新列")
    todo = []
    for table, col, decl in ADD_COLUMNS:
        have = columns_of(table)
        if col in have:
            print("  已有  %s.%s" % (table, col))
            continue
        todo.append("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
        print("  待加  %s.%s  %s" % (table, col, decl))
    for sql in todo:
        if args.dry_run:
            print("  DRY   " + sql)
        else:
            db.execute(sql)
            print("  完成  " + sql)

    # 3. 历史 user_id → 正式账号
    print("\n[3/4] 收编历史数据里的 user_id")
    from app.business import auth
    if args.dry_run:
        ids = set()
        for t in ("answer_records", "wrong_questions", "chat_history"):
            for r in db.query_all("SELECT DISTINCT user_id FROM %s" % t):
                if r["user_id"]:
                    ids.add(r["user_id"])
        print("  DRY   将为这些 id 建号：%s" % (sorted(ids) or "无"))
    else:
        res = auth.ensure_legacy_users()
        print("  新建账号 %d 个：%s" % (res["count"], res["created"] or "无"))
        if res["created"]:
            print("  初始密码 = 用户名 + '123'，登录后请立即修改")

    # 4. 首个管理员
    print("\n[4/4] 确认管理员账号")
    if args.dry_run:
        print("  DRY   admin_exists=%s" % auth.admin_exists())
    else:
        r = auth.ensure_bootstrap_admin()
        if not r["created"]:
            print("  已存在管理员，未改动")
        else:
            print("  已创建管理员：%s" % r["username"])
            if r["generated"]:
                print("  " + "=" * 52)
                print("  初始密码（只显示这一次）：%s" % r["password"])
                print("  " + "=" * 52)
            else:
                print("  密码取自环境变量 CPA_ADMIN_PASSWORD")

    print("\n迁移完成。" if not args.dry_run else "\n以上为预演，未改动数据库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
