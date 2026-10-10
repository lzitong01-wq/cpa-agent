# -*- coding: utf-8 -*-
"""建库 + 载入种子数据。

用法：
    python scripts/init_db.py            # 幂等建库并补齐种子数据
    python scripts/init_db.py --reset     # 删库重建（开发调试用，会清空答题记录）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from app import db  # noqa: E402
from app.business import catalog, questions  # noqa: E402


def main(argv) -> int:
    reset = "--reset" in argv
    if reset:
        print("[!] 删库重建：%s" % config.DB_PATH)
        db.reset_db()
    else:
        db.init_db()
        print("[+] 表结构已就绪：%s" % config.DB_PATH)

    c = catalog.seed_catalog()
    print("[+] 三级分类：科目 %(subjects)d / 章节 %(chapters)d / 知识点 %(knowledge_points)d" % c)

    q = questions.seed_questions()
    print("[+] 试题：新增 %(inserted)d，跳过 %(skipped)d，库内合计 %(total)d" % q)

    print("[+] 各表行数：")
    for t, n in db.db_stats().items():
        print("      %-18s %s" % (t, n))
    print("\n下一步：python scripts/ingest_corpus.py   # 教材入库并向量化")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
