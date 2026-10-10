# -*- coding: utf-8 -*-
"""本地一键启动（FastAPI + uvicorn）。

    python run.py                 # 默认 http://127.0.0.1:8000
    python run.py --port 9000
    python run.py --init          # 启动前初始化数据库并载入题库种子
    python run.py --ingest        # 启动前把 data/corpus 的教材入库
    python run.py --setup         # = --init + --ingest（第一次运行推荐）
    python run.py --reload        # 改代码自动重载（开发用）

首次使用：python run.py --setup
启动时会保证至少有一个管理员账号，密码打印在控制台（或取自 CPA_ADMIN_PASSWORD）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402


def bootstrap(args) -> None:
    """建库、载种子、入教材、保证管理员存在。"""
    from app import db
    fresh = not os.path.exists(config.DB_PATH)
    if args.init or args.setup or fresh:
        from app.business import catalog, questions as q_svc
        db.init_db()
        print("三级分类：", catalog.seed_catalog())
        print("题库种子：", q_svc.seed_questions())
    else:
        db.init_db()

    if args.ingest or args.setup:
        from app.rag import ingest
        print("教材入库中（首次加载嵌入模型约需 10~40 秒）……")
        res = ingest.ingest_dir()
        print("教材入库：%d 个文件 / %d 个切片，后端 %s"
              % (res["files"], res["chunks"], res.get("backend")))

    from app.business import auth
    r = auth.ensure_bootstrap_admin()
    if r["created"]:
        print("=" * 62)
        print("已创建管理员账号：%s" % r["username"])
        if r["generated"]:
            print("初始密码（只显示这一次）：%s" % r["password"])
            print("下次可用 CPA_ADMIN_PASSWORD 环境变量指定固定密码")
        else:
            print("密码取自环境变量 CPA_ADMIN_PASSWORD")
        print("=" * 62)
    else:
        print("管理员账号：已存在 %d 个账号" % auth.count_users())

    from app.rag import service as rag
    if not rag.health()["ok"]:
        print("提示：知识库为空，AI 答疑会一直回「教材中没有对应相关描述」。"
              "登录管理后台上传教材，或执行 python run.py --ingest。")

    from app.agent import llm as llm_mod
    st = llm_mod.llm_status()
    print("大模型接入：%s" % ("已配置" if st["any_available"] else
                             "未配置（纯检索模式，见 docs/LLM_INTEGRATION.md）"))
    if not config.SECRET_KEY:
        print("提示：未设置 CPA_SECRET，重启后所有人需重新登录（多用户部署请设置）。")


def main():
    ap = argparse.ArgumentParser(description="CPA 题库与 AI 答疑智能体")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--init", action="store_true", help="初始化数据库 + 载入种子数据")
    ap.add_argument("--ingest", action="store_true", help="教材入库并建向量索引")
    ap.add_argument("--setup", action="store_true", help="等于 --init --ingest")
    ap.add_argument("--reload", action="store_true", help="代码变更自动重载（开发用）")
    ap.add_argument("--workers", type=int, default=1,
                    help="worker 进程数；每个 worker 会独立加载一份嵌入模型（约 400MB）")
    args = ap.parse_args()

    bootstrap(args)

    import uvicorn
    url = "http://%s:%d" % ("127.0.0.1" if args.host == "0.0.0.0" else args.host,
                            args.port)
    print("=" * 62)
    print("CPA 题库与 AI 答疑智能体已启动： %s" % url)
    print("登录页 %s/login    管理后台 %s/admin    接口文档 %s/api/docs"
          % (url, url, url))
    print("=" * 62)

    if args.reload or args.workers > 1:
        # reload / 多 worker 模式下 uvicorn 需要拿到 import 字符串自己去 import
        uvicorn.run("app.webapp:create_app", factory=True, host=args.host,
                    port=args.port, reload=args.reload,
                    workers=None if args.reload else args.workers,
                    reload_dirs=[os.path.join(config.BASE_DIR, "app"),
                                 config.WEB_DIR] if args.reload else None)
    else:
        from app.webapp import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
