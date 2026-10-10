# -*- coding: utf-8 -*-
"""把 data/corpus 下的教材文件切片、向量化并入库。

用法：
    python scripts/ingest_corpus.py            # 增量（内容未变的文件跳过）
    python scripts/ingest_corpus.py --force    # 强制重建全部切片与向量
    python scripts/ingest_corpus.py --clear    # 先清空知识库再入库
    python scripts/ingest_corpus.py --dir D:\\my_corpus   # 指定其他语料目录
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
from app import db  # noqa: E402
from app.rag import ingest, service as rag  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="CPA 教材知识库入库")
    ap.add_argument("--force", action="store_true", help="忽略内容指纹，强制重新切片入库")
    ap.add_argument("--clear", action="store_true", help="先清空 corpus_* 表")
    ap.add_argument("--dir", default=None, help="语料目录，默认 data/corpus")
    args = ap.parse_args()

    if not os.path.exists(config.DB_PATH):
        print("数据库不存在，先执行：python scripts/init_db.py")
        return 1
    db.init_db()

    if args.clear:
        print("清空知识库：", ingest.clear_corpus())

    root = args.dir or config.CORPUS_DIR
    print("语料目录：", root)
    t0 = time.time()
    res = ingest.ingest_dir(root=root, force=args.force)
    print("入库结果：", json.dumps(res, ensure_ascii=False, indent=2))
    print("耗时 %.1fs" % (time.time() - t0))

    health = rag.health()
    print("索引状态：", json.dumps(health, ensure_ascii=False, indent=2))
    if not health["ok"]:
        print("知识库为空，请检查语料目录中是否有 .md / .txt / .pdf / .docx 文件")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
