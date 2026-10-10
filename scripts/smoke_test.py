# -*- coding: utf-8 -*-
"""端到端冒烟测试：不启服务器，用 FastAPI 测试客户端把每条链路走一遍。

    python scripts/smoke_test.py

覆盖：登录与账号隔离 / 管理员权限 / 教材上传入库 / 页面渲染 / 三级分类 /
题库筛选 / 五种题型判分 / 答题记录 / 错题本增删重做 / 推送算法四桶配额 /
组卷 / 模拟卷 / 统计 / RAG 检索与拒答 / Agent 三类意图。
任何一步失败都会打印 FAIL 并以非零码退出。
"""
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CPA_SECRET", "smoke-test-secret")

import config  # noqa: E402
from app import db  # noqa: E402
from app.agent import prompts  # noqa: E402
from app.business import auth  # noqa: E402

USER, USER_PWD = "smoke_user", "smoke_pwd_123"
OTHER, OTHER_PWD = "smoke_other", "other_pwd_123"
ADMIN, ADMIN_PWD = "smoke_admin", "admin_pwd_123"
PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("  ok  " if cond else " FAIL ", name,
                       ("  → " + str(extra)) if extra else ""))
    return cond


def api(client, method, path, **kw):
    payload = kw.pop("json", None) or {}
    if method == "get":
        r = client.get(path, params=payload)
    else:
        r = client.post(path, json=payload)
    try:
        body = r.json()
    except Exception:
        body = {}
    return r.status_code, body


def login(client, username, password):
    r = client.post("/api/auth/login",
                    json={"username": username, "password": password})
    return r.status_code, (r.json() if r.headers.get("content-type", "").startswith(
        "application/json") else {})


def ensure_account(username, password, role="student"):
    """幂等地准备测试账号，保证每次跑的结果可重复。"""
    row = auth.get_user(username)
    if not row:
        auth.create_user(username, password, role=role, user_id=username)
    else:
        auth.set_password(username, password)
        if row["role"] != role:
            auth.set_role(username, role)
        if not row["is_active"]:
            auth.set_active(username, True)


def clean_user(*users):
    with db.transaction() as conn:
        for u in users:
            for t in ("answer_records", "wrong_questions", "chat_history"):
                conn.execute("DELETE FROM %s WHERE user_id = ?" % t, (u,))


def section(title):
    print("\n" + "=" * 70 + "\n" + title + "\n" + "=" * 70)

def main():
    from fastapi.testclient import TestClient
    from app.webapp import create_app

    app = create_app()
    for u, p, role in ((USER, USER_PWD, "student"), (OTHER, OTHER_PWD, "student"),
                       (ADMIN, ADMIN_PWD, "admin")):
        ensure_account(u, p, role)
    clean_user(USER, OTHER)

    client = TestClient(app)          # 学员会话
    other = TestClient(app)           # 另一个学员，用于验证数据隔离
    admin = TestClient(app)           # 管理员会话
    anon = TestClient(app)            # 未登录

    # ---------------------------------------------------------- 鉴权
    section("1. 登录与鉴权")
    st, _ = api(anon, "get", "/api/subjects")
    check("未登录访问接口返回 401", st == 401, "status=%s" % st)
    r = anon.get("/", follow_redirects=False)
    check("未登录访问页面跳转登录页", r.status_code == 302 and
          "/login" in r.headers.get("location", ""), r.headers.get("location"))
    check("登录页可直接打开", anon.get("/login").status_code == 200)

    st, b = login(client, USER, "wrong-password")
    check("密码错误被拒", st == 400 and b.get("ok") is False, b.get("error"))
    st, b = login(client, USER, USER_PWD)
    check("学员登录成功", st == 200 and b["data"]["user"]["username"] == USER,
          b.get("error"))
    st, b = api(client, "get", "/api/auth/me")
    check("登录后能取到当前用户", b["data"]["authenticated"] and
          b["data"]["user"]["role"] == "student", b["data"].get("user"))

    st, b = login(admin, ADMIN, ADMIN_PWD)
    check("管理员登录成功且被引导到后台",
          st == 200 and b["data"]["user"]["role"] == "admin" and
          b["data"]["redirect"] == "/admin", b["data"].get("redirect"))
    login(other, OTHER, OTHER_PWD)

    st, _ = api(client, "get", "/api/admin/users")
    check("学员访问管理员接口返回 403", st == 403, "status=%s" % st)
    r = client.get("/admin", follow_redirects=False)
    check("学员访问管理后台页面返回 403", r.status_code == 403, r.status_code)
    st, b = api(admin, "get", "/api/admin/users")
    check("管理员可列出账号", st == 200 and b["data"]["total"] >= 3,
          "total=%s" % b["data"].get("total"))

    # ---------------------------------------------------------- 页面
    section("2. 页面渲染")
    for path, kw in (("/", "智能推送"), ("/bank", "三级分类"), ("/practice", "本次练习"),
                     ("/wrongbook", "错题"), ("/stats", "答题历史"), ("/chat", "答疑")):
        r = client.get(path)
        check("GET %s" % path, r.status_code == 200 and kw in r.text,
              "status=%s" % r.status_code)
    r = admin.get("/admin")
    check("GET /admin（管理员）", r.status_code == 200 and "上传教材" in r.text,
          "status=%s" % r.status_code)
    check("学员页面不显示管理入口", "管理后台" not in client.get("/").text)
    check("管理员页面显示管理入口", "管理后台" in admin.get("/").text)
    check("GET /nope 返回 404", client.get("/nope").status_code == 404)

    # ---------------------------------------------------------- 分类与题库
    section("3. 三级分类与题库")
    _, b = api(client, "get", "/api/subjects")
    subjects = b.get("data") or []
    check("六个科目", len(subjects) == 6, [s["name"] for s in subjects])
    _, b = api(client, "get", "/api/tree")
    tree = b["data"]
    n_ch = sum(len(s["chapters"]) for s in tree)
    n_kp = sum(len(c["knowledge_points"]) for s in tree for c in s["chapters"])
    check("章节/知识点三级结构", n_ch == 24 and n_kp == 72,
          "章节 %d / 知识点 %d" % (n_ch, n_kp))
    _, b = api(client, "get", "/api/questions", json={"limit": 100})
    qs = b["data"]
    types = set(q["qtype"] for q in qs["items"])
    check("五种题型齐全",
          types >= {"single", "multiple", "judge", "short", "comprehensive"}, types)
    check("列表接口不泄露答案", all("answer" not in q for q in qs["items"]))
    _, b = api(client, "get", "/api/resolve_scope",
               json={"subject": "会计", "chapter": "所得税", "knowledge_point": "暂时性差异"})
    check("中文话术解析成三级 id",
          all(b["data"].get(k) for k in
              ("subject_id", "chapter_id", "knowledge_point_id")), b["data"])

    # ---------------------------------------------------------- 判分（五种题型）
    section("4. 五种题型判分 + 答题记录 + 错题本联动")
    by_type = {}
    for q in qs["items"]:
        by_type.setdefault(q["qtype"], q)
    for qtype in ("single", "multiple", "judge", "short", "comprehensive"):
        q = by_type.get(qtype)
        if not q:
            check("存在 %s 题" % qtype, False)
            continue
        bad = {"single": "Z", "multiple": ["Z"], "judge": "错",
               "short": "不知道", "comprehensive": "不知道"}[qtype]
        st, b = api(client, "post", "/api/practice/submit",
                    json={"question_id": q["id"], "answer": bad, "mode": "chapter"})
        d = b.get("data") or {}
        ok = st == 200 and d.get("is_correct") is False and d.get("analysis")
        check("%s 答错→判错+返回解析+进错题本" % qtype,
              ok and d.get("added_to_wrongbook") is True, d.get("detail"))

    import json as _json
    for qtype in ("single", "multiple", "judge"):
        q = by_type.get(qtype)
        raw = db.query_one("SELECT answer_json FROM questions WHERE id = ?", (q["id"],))
        right = _json.loads(raw["answer_json"])
        st, b = api(client, "post", "/api/practice/submit",
                    json={"question_id": q["id"], "answer": right, "mode": "wrong"})
        check("%s 答对→判对" % qtype, (b.get("data") or {}).get("is_correct") is True,
              (b.get("data") or {}).get("detail"))

    st, _ = api(client, "post", "/api/practice/submit",
                json={"question_id": 999999, "answer": "A"})
    check("提交不存在的题目返回 404", st == 404, "status=%s" % st)

    _, b = api(client, "get", "/api/records", json={"limit": 50})
    my_total = b["data"]["total"]
    check("答题记录已持久化", my_total >= 8, "total=%s" % my_total)
    _, b = api(client, "get", "/api/records", json={"limit": 50, "only_wrong": 1})
    check("只看错题的记录筛选可用", b["data"]["total"] >= 5, "total=%s" % b["data"]["total"])

    # ---------------------------------------------------------- 账号数据隔离
    section("5. 账号数据隔离")
    q0 = by_type["single"]
    api(other, "post", "/api/practice/submit",
        json={"question_id": q0["id"], "answer": "Z", "mode": "chapter"})
    _, b = api(other, "get", "/api/records", json={"limit": 50})
    check("另一个账号只看到自己的 1 条记录", b["data"]["total"] == 1,
          "total=%s（对方有 %s 条）" % (b["data"]["total"], my_total))
    # 伪造 user_id：请求里带别人的 id，服务端必须忽略
    _, b = api(other, "get", "/api/records", json={"limit": 50, "user_id": USER})
    check("请求里伪造 user_id 无效（GET）", b["data"]["total"] == 1,
          "total=%s" % b["data"]["total"])
    _, b = api(other, "get", "/api/stats/dashboard", json={"user_id": USER})
    check("伪造 user_id 拿不到别人的统计", b["data"]["overview"]["total"] == 1,
          b["data"]["overview"]["total"])
    _, b = api(other, "get", "/api/wrongbook", json={"limit": 50, "user_id": USER})
    check("错题本按账号隔离", b["data"]["total"] == 1, "total=%s" % b["data"]["total"])
    _, b = api(other, "post", "/api/chat",
               json={"message": "什么是可变现净值", "session_id": "iso", "user_id": USER})
    _, b = api(client, "get", "/api/chat/history", json={"session_id": "iso"})
    check("对话历史按账号隔离", len(b["data"]) == 0, "%d 条" % len(b["data"]))
    _, b = api(client, "get", "/api/records", json={"limit": 50})
    check("自己的记录数未被他人影响", b["data"]["total"] == my_total,
          "total=%s" % b["data"]["total"])

    # ---------------------------------------------------------- 错题本
    section("6. 错题本：收集 / 筛选 / 重做 / 移除 / 恢复")
    _, b = api(client, "get", "/api/wrongbook", json={"limit": 50})
    wb = b["data"]
    check("错题自动收集", wb["total"] >= 5, "total=%s" % wb["total"])
    _, b = api(client, "get", "/api/wrongbook/summary")
    check("错题本按科目汇总", b["data"]["open_total"] >= 1, b["data"]["open_total"])
    sid = subjects[0]["id"]
    _, b = api(client, "get", "/api/wrongbook", json={"subject_id": sid, "limit": 50})
    check("错题本按科目筛选", all(i["subject_id"] == sid for i in b["data"]["items"]))
    kp = wb["items"][0]["knowledge_point_id"]
    _, b = api(client, "get", "/api/wrongbook", json={"knowledge_point_id": kp, "limit": 50})
    check("错题本按知识点筛选", b["data"]["total"] >= 1 and
          all(i["knowledge_point_id"] == kp for i in b["data"]["items"]))
    _, b = api(client, "post", "/api/wrongbook/redo", json={"limit": 5})
    check("错题重做取题", b["data"]["count"] >= 1, "count=%s" % b["data"]["count"])
    target = wb["items"][0]["id"]
    st, b = api(client, "post", "/api/wrongbook/remove", json={"question_id": target})
    check("移出错题本", st == 200 and b["data"]["status"] == "removed")
    st, _ = api(client, "post", "/api/wrongbook/remove", json={"question_id": 999999})
    check("移出不存在的错题返回 404", st == 404, "status=%s" % st)
    st, b = api(client, "post", "/api/wrongbook/restore", json={"question_id": target})
    check("恢复错题", st == 200 and b["data"]["status"] == "open")

    from app.business import wrongbook as wb_svc
    q = by_type["single"]
    right = _json.loads(db.query_one(
        "SELECT answer_json FROM questions WHERE id = ?", (q["id"],))["answer_json"])
    for _ in range(2):
        api(client, "post", "/api/practice/submit",
            json={"question_id": q["id"], "answer": right, "mode": "wrong"})
    row = db.query_one("SELECT status, consecutive_right FROM wrong_questions "
                       "WHERE user_id = ? AND question_id = ?", (USER, q["id"]))
    check("连续答对 %d 次自动脱离错题本" % wb_svc.RESOLVE_STREAK,
          row and row["status"] == "resolved", row and dict(row))

    # ---------------------------------------------------------- 推送算法
    section("7. 推送算法（四桶配额，非随机）")
    _, b = api(client, "get", "/api/practice/profile")
    prof = b["data"]
    check("知识点画像四桶",
          set(prof["bucket_sizes"]) == {"wrong", "weak", "fresh", "mastered"},
          prof["bucket_sizes"])
    _, b = api(client, "post", "/api/practice/next", json={"mode": "recommend", "limit": 10})
    rec = b["data"]
    reasons = {}
    for it in rec["questions"]:
        reasons[it.get("reason")] = reasons.get(it.get("reason"), 0) + 1
    check("智能推送取到题", rec["count"] > 0, "count=%s" % rec["count"])
    check("每道题都带选题依据", all(it.get("reason") for it in rec["questions"]), reasons)
    check("优先出错题关联知识点",
          any("错题" in (it.get("reason") or "") for it in rec["questions"]), reasons)
    _, b2 = api(client, "post", "/api/practice/next",
                json={"mode": "recommend", "limit": 10, "seed": 7})
    ids1 = [q["id"] for q in rec["questions"]]
    ids2 = [q["id"] for q in b2["data"]["questions"]]
    check("推送结果稳定复现（非纯随机）",
          len(set(ids1) & set(ids2)) >= max(1, len(ids1) // 2), "%s vs %s" % (ids1, ids2))
    _, b = api(client, "post", "/api/practice/next",
               json={"mode": "chapter", "subject_id": sid, "limit": 5})
    check("专项章节刷题", all(q["subject_id"] == sid for q in b["data"]["questions"]))
    _, b = api(client, "post", "/api/practice/next", json={"mode": "mock", "limit": 10})
    check("模拟刷题按题型配比",
          len(set(q["qtype"] for q in b["data"]["questions"])) >= 3,
          b["data"].get("allocation"))
    _, b = api(client, "post", "/api/practice/next", json={"mode": "wrong", "limit": 5})
    check("错题刷题模式", b["data"]["count"] >= 1)
    st, _ = api(client, "post", "/api/practice/next", json={"mode": "nope"})
    check("非法模式被参数校验拦下", st == 422, "status=%s" % st)
    st, _ = api(client, "post", "/api/practice/next", json={"mode": "recommend", "limit": 999})
    check("超范围的 limit 被拦下", st == 422, "status=%s" % st)

    # ---------------------------------------------------------- 组卷
    section("8. 组卷")
    _, b = api(client, "post", "/api/paper/build", json={"subject_id": sid, "limit": 5})
    paper = b["data"]
    check("按科目组卷", paper["count"] >= 1 and paper["paper_id"], paper["count"])
    check("组卷返回卷面总分与题型分布",
          paper["total_score"] > 0 and paper["type_distribution"],
          paper["type_distribution"])
    items = [{"question_id": q["id"], "answer": "A"} for q in paper["questions"]]
    _, b = api(client, "post", "/api/practice/submit_batch",
               json={"items": items, "mode": "paper", "paper_id": paper["paper_id"]})
    check("整卷提交返回总分与逐题明细",
          b["data"]["count"] == len(items) and len(b["data"]["details"]) == len(items),
          "得分 %s/%s" % (b["data"]["score"], b["data"]["full_score"]))

    # ---------------------------------------------------------- 统计
    section("9. 答题历史统计")
    _, b = api(client, "get", "/api/stats/dashboard")
    dash = b["data"]
    for key in ("overview", "by_subject", "by_chapter", "weakest_kp", "by_qtype",
                "daily_trend", "mode_breakdown"):
        check("统计包含 %s" % key, key in dash)
    ov = dash["overview"]
    check("总览有答题量与正确率", ov["total"] > 0 and 0 <= ov["accuracy"] <= 1,
          "total=%s acc=%s" % (ov["total"], ov["accuracy"]))
    check("统计到知识点覆盖度", ov["kp_total"] == 72 and ov["kp_touched"] > 0,
          "%s/%s" % (ov["kp_touched"], ov["kp_total"]))
    check("按模式统计到多种刷题模式", len(dash["mode_breakdown"]) >= 2,
          [r["mode"] for r in dash["mode_breakdown"]])

    # ---------------------------------------------------------- RAG
    section("10. RAG 检索与拒答")
    _, b = api(client, "get", "/api/rag/health")
    health = b["data"]
    base_chunks = health["index"]["chunks"]
    check("知识库已就绪", health["ok"] and base_chunks > 0,
          "%s 个片段 / 后端 %s" % (base_chunks, health["embedding_backend"]))
    check("向量已全部生成", health["index"]["vectorized"] == base_chunks)
    _, b = api(client, "get", "/api/rag/docs")
    check("六科教材都已入库", len(b["data"]) >= 6, [d["title"] for d in b["data"]])

    _, b = api(client, "post", "/api/rag/search", json={"query": "什么是暂时性差异"})
    hit = b["data"]
    check("命中教材片段", hit["refused"] is False and hit["hits"], hit.get("reason"))
    check("检索元数据带科目", all(h.get("subject_name") for h in hit["hits"]))
    check("检索元数据带章节/知识点",
          any(h.get("chapter_name") and h.get("knowledge_point_name") for h in hit["hits"]))
    _, b = api(client, "post", "/api/rag/search",
               json={"query": "增值税进项税额不能抵扣的情形", "subject": "税法"})
    check("按科目过滤检索", b["data"]["hits"] and
          all(h["subject_name"] == "税法" for h in b["data"]["hits"]))
    _, b = api(client, "post", "/api/rag/context", json={"query": "薛定谔方程怎么推导"})
    check("范围外问题触发拒答", b["data"]["found"] is False and
          b["data"]["message"] == prompts.NO_CONTEXT_REPLY, b["data"]["message"])
    _, b = api(client, "post", "/api/rag/context", json={"query": "重要性水平怎么确定"})
    ctx = b["data"]
    check("上下文与结构化引用可用", ctx["found"] and ctx["context"] and ctx["citations"],
          "引用 %d 条" % len(ctx.get("citations") or []))
    check("引用含科目章节知识点",
          all("subject" in c and "chapter" in c and "knowledge_point" in c
              for c in ctx["citations"]))
    logged = db.query_scalar("SELECT COUNT(*) FROM retrieval_logs WHERE user_id = ?",
                             (USER,), 0)
    check("检索日志已落库", logged >= 4, "%d 条" % logged)

    # ---------------------------------------------------------- 管理员上传教材
    section("11. 管理员上传教材 → 全站共享")
    probe_q = "商誉减值测试的两步法怎么做"

    def cites_goodwill(client_):
        """检索结果里是否出现"上传教材"里的知识点。

        这里不断言"上传前必然拒答"——那取决于阈值与近邻内容，
        断言"引用是否来自新上传的教材"才是这条链路要验证的东西。
        """
        _, bb = api(client_, "post", "/api/rag/context", json={"query": probe_q})
        kps = [c.get("knowledge_point") or "" for c in (bb["data"].get("citations") or [])]
        return any("商誉" in k for k in kps), kps

    hit_before, kps_before = cites_goodwill(client)
    check("上传前检索不到商誉相关知识点", hit_before is False, kps_before)

    content = (
        "# 会计\n\n## 第八章 资产减值\n\n### 商誉减值测试的两步法\n\n"
        "企业合并所形成的商誉，应当结合与其相关的资产组或者资产组组合进行减值测试。\n"
        "商誉减值测试采用两步法：第一步，对不包含商誉的资产组进行减值测试，"
        "计算可收回金额并与账面价值比较，确认相应的减值损失；\n"
        "第二步，对包含商誉的资产组进行减值测试，比较这些相关资产组的账面价值"
        "（包含所分摊的商誉的账面价值部分）与其可收回金额，"
        "如相关资产组的可收回金额低于其账面价值的，应当就其差额确认减值损失，"
        "减值损失金额应当先抵减分摊至资产组中商誉的账面价值，"
        "再根据资产组中除商誉之外的其他各项资产的账面价值所占比重，"
        "按比例抵减其他各项资产的账面价值。\n"
        "商誉减值损失一经确认，在以后会计期间不得转回。\n").encode("utf-8")

    resp = admin.post("/api/admin/corpus/upload",
                      files={"file": ("会计-商誉减值.md", content, "text/markdown")},
                      data={"subject": "会计"})
    body = resp.json()
    check("管理员上传成功并返回任务号",
          resp.status_code == 200 and body["data"]["job"]["id"],
          body.get("error") or ("job #%s" % body["data"]["job"]["id"]))
    job_id = body["data"]["job"]["id"]

    _, b = api(admin, "get", "/api/admin/corpus/jobs/%d" % job_id)
    job = b["data"]
    check("后台任务已完成（TestClient 会同步跑完 BackgroundTask）",
          job["status"] == "done" and job["chunk_count"] > 0,
          "%s / %s" % (job["status"], job.get("message")))
    check("任务记录了上传者", job["uploaded_by"] == ADMIN, job.get("uploader_name"))
    check("任务不下发服务器绝对路径", "stored_path" not in job)

    st, _ = api(client, "post", "/api/admin/corpus/upload")
    check("学员无法调用上传接口", st in (403, 422), "status=%s" % st)

    _, b = api(client, "get", "/api/rag/health")
    check("知识库片段数增加", b["data"]["index"]["chunks"] > base_chunks,
          "%d → %d" % (base_chunks, b["data"]["index"]["chunks"]))
    hit_after, kps_after = cites_goodwill(client)
    check("学员账号立即检索到管理员上传的教材（引用溯源到新知识点）",
          hit_after is True, kps_after)
    _, b = api(other, "post", "/api/chat",
               json={"message": probe_q, "session_id": "iso2"})
    other_kps = [c.get("knowledge_point") or "" for c in (b["data"]["citations"] or [])]
    check("另一个账号也能用到（知识库全站共享）",
          any("商誉" in k for k in other_kps), other_kps)

    # 上下架
    doc_id = job["doc_id"]
    api(admin, "post", "/api/admin/corpus/visibility",
        json={"doc_id": doc_id, "visible": False})
    hit_hidden, kps_hidden = cites_goodwill(client)
    check("下架后不再参与检索", hit_hidden is False, kps_hidden)
    api(admin, "post", "/api/admin/corpus/visibility",
        json={"doc_id": doc_id, "visible": True})
    hit_back, _ = cites_goodwill(client)
    check("重新上架后恢复检索", hit_back is True)

    _, b = api(admin, "get", "/api/admin/corpus/docs")
    up = [d for d in b["data"] if d["id"] == doc_id]
    check("管理员教材列表标出来源与上传者",
          up and up[0]["origin"] == "upload" and up[0]["uploader_name"] == ADMIN,
          up[0] if up else None)
    _, b = api(client, "get", "/api/rag/docs")
    check("学员看到的教材清单不含服务器路径",
          all("source_path" not in d for d in b["data"]))

    st, b = api(admin, "post", "/api/admin/corpus/delete", json={"doc_id": doc_id})
    check("删除上传的教材", st == 200 and b["data"]["deleted"], b["data"])
    _, b = api(client, "get", "/api/rag/health")
    check("删除后片段数回落", b["data"]["index"]["chunks"] == base_chunks,
          "%d（基线 %d）" % (b["data"]["index"]["chunks"], base_chunks))

    resp = admin.post("/api/admin/corpus/upload",
                      files={"file": ("木马.exe", b"MZ\x90\x00",
                                      "application/octet-stream")})
    check("非法扩展名被拒", resp.status_code == 400, resp.json().get("error"))
    resp = admin.post("/api/admin/corpus/upload",
                      files={"file": ("空.md", b"", "text/markdown")})
    check("空文件被拒", resp.status_code == 400, resp.json().get("error"))

    # ---------------------------------------------------------- 账号管理
    section("12. 管理员账号管理")
    st, b = api(admin, "post", "/api/admin/users/create",
                json={"username": "smoke_tmp", "password": "tmp_pwd_123"})
    check("新建账号", st == 200 and b["data"]["username"] == "smoke_tmp", b.get("error"))
    st, b = api(admin, "post", "/api/admin/users/create",
                json={"username": "smoke_tmp", "password": "tmp_pwd_123"})
    check("重复用户名被拒", st == 400, b.get("error"))
    st, b = api(admin, "post", "/api/admin/users/create",
                json={"username": "x", "password": "123"})
    check("用户名/密码长度校验生效", st == 422, "status=%s" % st)

    tmp = TestClient(app)
    st, _ = login(tmp, "smoke_tmp", "tmp_pwd_123")
    check("新账号可登录", st == 200)
    api(admin, "post", "/api/admin/users/active",
        json={"user_id": auth.get_by_username("smoke_tmp")["id"], "active": False})
    st, _ = api(tmp, "get", "/api/subjects")
    check("停用后已登录的会话立即失效", st == 401, "status=%s" % st)
    st, b = login(tmp, "smoke_tmp", "tmp_pwd_123")
    check("停用账号无法登录", st == 400, b.get("error"))
    st, b = api(admin, "post", "/api/admin/users/delete",
                json={"user_id": auth.get_by_username("smoke_tmp")["id"]})
    check("删除账号", st == 200 and b["data"]["deleted"])
    st, b = api(admin, "post", "/api/admin/users/delete",
                json={"user_id": ADMIN})
    check("不能删除当前登录的管理员", st == 400, b.get("error"))

    st, b = api(client, "post", "/api/auth/password",
                json={"old_password": "wrong", "new_password": "whatever_123"})
    check("改密码需要原密码", st == 400, b.get("error"))
    st, _ = api(client, "post", "/api/auth/password",
                json={"old_password": USER_PWD, "new_password": "new_pwd_123"})
    check("改密码成功", st == 200)
    auth.set_password(USER, USER_PWD)          # 复位，保证脚本可重复运行

    # ---------------------------------------------------------- Agent
    section("13. AI 答疑智能体（三类意图）")
    _, b = api(client, "get", "/api/chat/status")
    st_data = b["data"]
    check("能力状态可查", "llm" in st_data and "rag" in st_data,
          "大模型：%s" % ("已配置" if st_data["llm"]["any_available"]
                        else "未配置（纯检索模式）"))
    sess = "smoke_session"
    db.execute("DELETE FROM chat_history WHERE user_id = ? AND session_id = ?",
               (USER, sess))

    def chat(msg, qid=None):
        _, bb = api(client, "post", "/api/chat",
                    json={"message": msg, "session_id": sess, "current_question_id": qid})
        return bb.get("data") or {}

    r = chat("给我推送会计收入章节的题")
    check("意图①刷题：识别正确", r.get("intent") == "practice", r.get("intent"))
    check("意图①刷题：定位到科目与章节",
          r["scope"].get("subject_name") == "会计" and r["scope"].get("chapter_id"),
          r.get("scope"))
    check("意图①刷题：返回题目并带选题依据",
          r["questions"] and all(q.get("reason") for q in r["questions"]),
          "%d 道题" % len(r["questions"]))
    first_q = r["questions"][0] if r["questions"] else None

    r = chat("来5道税法的多选")
    check("意图①刷题：数量与题型抽取",
          r.get("intent") == "practice" and
          all(q["qtype"] == "multiple" for q in r["questions"]),
          [q["qtype"] for q in r["questions"]])
    r = chat("我要刷错题")
    check("意图①刷题：错题模式",
          r.get("intent") == "practice" and r.get("mode") == "wrong", r.get("mode"))
    r = chat("模拟考一套")
    check("意图①刷题：模拟模式", r.get("mode") == "mock", r.get("mode"))

    r = chat("帮我解释什么是暂时性差异")
    check("意图②答疑：识别正确", r.get("intent") == "knowledge_qa", r.get("intent"))
    check("意图②答疑：基于教材作答且带引用来源",
          r["citations"] and not r.get("refused"), "引用 %d 条" % len(r["citations"]))
    check("意图②答疑：引用标注科目与章节",
          all(c.get("subject") for c in r["citations"]),
          r["citations"][0] if r["citations"] else None)

    r = chat("量子隧穿效应是什么")
    check("意图②答疑：教材没有则明确拒答，不编造",
          r.get("refused") is True and r["reply"] == prompts.NO_CONTEXT_REPLY,
          r["reply"][:40])

    if first_q:
        r = chat("这道题我没看懂", qid=first_q["id"])
        check("意图③题目答疑：识别正确", r.get("intent") == "question_qa", r.get("intent"))
        check("意图③题目答疑：带上题目与标准解析上下文",
              r.get("question") and r["question"]["id"] == first_q["id"]
              and "标准解析" in r["reply"], r.get("question", {}).get("id"))
        r = chat("题目 %d 为什么选这个答案" % first_q["id"])
        check("意图③题目答疑：能从话术里认出题号",
              r.get("intent") == "question_qa" and
              r.get("question", {}).get("id") == first_q["id"],
              r.get("question", {}).get("id"))

    r = chat("你好")
    check("非业务问候走引导话术", r.get("intent") == "other" and "CPA" in r["reply"])

    _, b = api(client, "get", "/api/chat/history", json={"session_id": sess, "limit": 100})
    hist = b["data"]
    check("对话历史已落库（含 user 与 assistant）",
          len(hist) >= 16 and {h["role"] for h in hist} == {"user", "assistant"},
          "%d 条" % len(hist))
    check("对话历史保存了意图与引用",
          any(h.get("intent") == "knowledge_qa" and h.get("citations") for h in hist))

    # ---------------------------------------------------------- 登出
    section("14. 登出")
    st, _ = api(client, "post", "/api/auth/logout")
    check("登出接口返回成功", st == 200)
    st, _ = api(client, "get", "/api/records")
    check("登出后接口返回 401", st == 401, "status=%s" % st)

    # APPEND_MARKER_MAIN





    # ---------------------------------------------------------- 汇总
    section("汇总")
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  - " + f)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())

