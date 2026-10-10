# -*- coding: utf-8 -*-
"""静态一致性检查：前端引用的接口与静态文件必须真实存在。

冒烟测试覆盖后端接口，本脚本覆盖"前端写错路径"这类只在浏览器里才暴露的问题：
1. web/static/js/*.js 里出现的每个 /api/... 路径，都要能匹配到 FastAPI 路由；
2. 模板里 static_url(...) 引用的文件都要存在；
3. 模板里 page_url(...) 引用的页面名都在 webapp.PAGE_PATHS 里；
4. 前端不能再往接口里塞 user_id（身份只能来自服务端会话）。

    python scripts/check_frontend.py
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CPA_SECRET", "check-frontend-secret")

import config  # noqa: E402

JS_DIR = os.path.join(config.STATIC_DIR, "js")
API_RE = re.compile(r"[\"'](/api/[A-Za-z0-9_/]*)")
STATIC_RE = re.compile(r"static_url\(\s*'([^']+)'")
PAGE_RE = re.compile(r"page_url\(\s*'([^']+)'")
USER_ID_RE = re.compile(r"user_id\s*:")

problems = []


def api_paths(app) -> set:
    """从 OpenAPI schema 取所有接口路径。

    不直接遍历 app.routes：FastAPI 的路由树里有 Mount / _IncludedRouter 这类
    没有 .path 的节点，版本之间结构还会变；OpenAPI schema 是稳定的公开契约。
    """
    paths = set(app.openapi().get("paths") or {})
    return {re.sub(r"\{[^}]+\}", "@", p) for p in paths}


def main():
    from app.webapp import create_app, PAGE_PATHS
    app = create_app()

    norm_rules = api_paths(app)
    print("已注册接口 %d 个，页面 %d 个" % (len(norm_rules), len(PAGE_PATHS)))

    # 1. JS 里的 /api 路径
    used, has_user_id = {}, []
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(JS_DIR, fn), "r", encoding="utf-8") as f:
            src = f.read()
        for path in API_RE.findall(src):
            used.setdefault(path, set()).add(fn)
        # page_admin.js 例外：管理接口里的 user_id 是"要操作哪个账号"，
        # 不是"我是谁"，服务端仍然从会话取操作者身份。
        if fn == "page_admin.js":
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if USER_ID_RE.search(line) and not line.lstrip().startswith(("/*", "*", "//")):
                has_user_id.append("%s:%d %s" % (fn, i, line.strip()))

    print("\n前端引用的接口 %d 个：" % len(used))
    for path in sorted(used):
        norm = re.sub(r"/\d+$", "/@", path)
        hit = norm in norm_rules or path in norm_rules
        print("  %s %-34s ← %s" % ("ok  " if hit else "缺失", path,
                                   ", ".join(sorted(used[path]))))
        if not hit:
            problems.append("前端调用了不存在的接口：%s（%s）"
                            % (path, ", ".join(sorted(used[path]))))

    print("\n前端是否仍在传 user_id：%s" % ("否 ✅" if not has_user_id else "是（必须清掉）"))
    for line in has_user_id:
        print("  " + line)
        problems.append("前端仍在往接口传 user_id：" + line)

    # 2/3. 模板里的静态文件与页面名
    missing_static, missing_page = [], []
    for fn in sorted(os.listdir(config.TEMPLATE_DIR)):
        if not fn.endswith(".html"):
            continue
        with open(os.path.join(config.TEMPLATE_DIR, fn), "r", encoding="utf-8") as f:
            src = f.read()
        for rel in STATIC_RE.findall(src):
            if not os.path.exists(os.path.join(config.STATIC_DIR,
                                               rel.replace("/", os.sep))):
                missing_static.append("%s → static/%s" % (fn, rel))
        for name in PAGE_RE.findall(src):
            if name not in PAGE_PATHS:
                missing_page.append("%s → page_url('%s')" % (fn, name))
        if "url_for(" in src:
            problems.append("%s 仍在用 Flask 的 url_for()" % fn)

    print("\n模板静态资源：%s" % ("全部存在" if not missing_static else "缺失如下"))
    for m in missing_static:
        print("  缺失 " + m)
        problems.append("模板引用了不存在的静态文件：" + m)
    print("模板页面引用：%s" % ("全部已注册" if not missing_page else "缺失如下"))
    for m in missing_page:
        print("  缺失 " + m)
        problems.append("模板引用了未注册的页面：" + m)

    # 4. 每个页面模板都要有对应路由
    print("\n页面路由：%s" % ", ".join("%s→%s" % (k, v) for k, v in PAGE_PATHS.items()))

    print("\n" + "=" * 60)
    if problems:
        print("发现 %d 个问题：" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    print("前端与后端引用一致 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
