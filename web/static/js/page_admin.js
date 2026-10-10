/* 管理后台：上传教材、入库任务、知识库教材、账号管理 */
(function () {
  "use strict";
  const { el, $, clear, toast } = CPA;
  let pollTimer = null;

  /* ------------------------------------------------------------ 概览 */
  function tile(label, value, hint, kind) {
    return el("div", { class: "card stat" + (kind ? " " + kind : "") }, [
      el("div", { class: "num", text: String(value) }),
      el("div", { class: "label", text: label }),
      hint ? el("div", { class: "hint", text: hint }) : null
    ]);
  }

  async function loadStats() {
    const d = await CPA.get("/api/admin/corpus/stats");
    const s = d.storage, r = d.rag;
    $("#max-mb").textContent = s.max_upload_mb;
    const box = clear($("#admin-stats"));
    box.appendChild(tile("知识库教材", s.docs, s.public_docs + " 份参与检索", "brand"));
    box.appendChild(tile("教材片段", s.chunks, "向量维度 " + (r.index.dim || "-")));
    box.appendChild(tile("管理员上传", s.uploaded_docs, "其余为项目自带"));
    box.appendChild(tile("检索状态", r.ok ? "就绪" : "空库",
      String(r.embedding_backend).split(":")[0], r.ok ? "ok" : "bad"));
  }

  /* ------------------------------------------------------------ 上传 */
  async function loadSubjects() {
    const subjects = await CPA.get("/api/subjects");
    const sel = $("#upload-subject");
    subjects.forEach(s => sel.appendChild(el("option", { value: s.name, text: s.name })));
  }

  function bindUpload() {
    const form = $("#upload-form");
    const btn = $("#upload-btn");
    const msg = $("#upload-msg");
    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      const input = $("#upload-file");
      if (!input.files || !input.files.length) return;
      const fd = new FormData();
      fd.append("file", input.files[0]);
      fd.append("subject", $("#upload-subject").value || "");
      btn.disabled = true;
      btn.textContent = "上传中…";
      clear(msg);
      try {
        const res = await CPA.upload("/api/admin/corpus/upload", fd);
        msg.appendChild(el("p", { class: "ok-line", text: "✅ " + res.hint }));
        input.value = "";
        await refreshJobs();
        startPolling();
      } catch (e) {
        msg.appendChild(el("p", { class: "bad-line", text: "❌ " + e.message }));
      } finally {
        btn.disabled = false;
        btn.textContent = "上传并入库";
      }
    });
  }

  /* ------------------------------------------------------------ 任务 */
  const JOB = {
    pending: { text: "排队中", cls: "warn" },
    processing: { text: "处理中", cls: "warn" },
    done: { text: "已完成", cls: "ok" },
    failed: { text: "失败", cls: "bad" }
  };

  function table(headers, rows) {
    if (!rows.length) return el("div", { class: "empty", text: "暂无数据" });
    const t = el("table");
    t.appendChild(el("thead", {}, el("tr", {}, headers.map(h => el("th", { text: h })))));
    t.appendChild(el("tbody", {}, rows));
    return el("div", { class: "table-wrap" }, t);
  }

  async function refreshJobs() {
    const jobs = await CPA.get("/api/admin/corpus/jobs", { limit: 20 });
    const rows = jobs.map(j => {
      const st = JOB[j.status] || { text: j.status, cls: "" };
      return el("tr", {}, [
        el("td", { class: "mono small", text: "#" + j.id }),
        el("td", {}, [
          el("div", { text: j.filename }),
          el("div", { class: "muted small", text: (j.size_bytes / 1024).toFixed(0) + " KB" })
        ]),
        el("td", { text: j.subject_name || "自动识别" }),
        el("td", {}, el("span", { class: "tag " + st.cls, text: st.text })),
        el("td", { class: "num", text: j.chunk_count || "-" }),
        el("td", { class: "muted small", text: j.message || "" }),
        el("td", {}, j.status === "failed"
          ? el("button", {
              class: "ghost sm", text: "重试",
              onclick: async () => {
                try {
                  await CPA.post("/api/admin/corpus/jobs/retry", { job_id: j.id });
                  toast("已重新排队");
                  await refreshJobs();
                  startPolling();
                } catch (e) { toast(e.message, "bad"); }
              }
            })
          : null)
      ]);
    });
    clear($("#jobs-table")).appendChild(
      table(["任务", "文件", "科目", "状态", "片段", "说明", ""], rows));
    return jobs;
  }

  function busyIn(jobs) {
    return jobs.some(j => j.status === "pending" || j.status === "processing");
  }

  function startPolling() {
    if (pollTimer) return;
    pollTimer = setInterval(async function () {
      let jobs = [];
      try { jobs = await refreshJobs(); } catch (e) { return; }  /* 忽略瞬时错误 */
      if (busyIn(jobs)) return;
      clearInterval(pollTimer);
      pollTimer = null;
      await Promise.all([loadStats(), refreshDocs()]);
      toast("知识库已更新，所有账号立即可检索");
    }, 2500);
  }

  /* ------------------------------------------------------------ 教材 */
  async function refreshDocs() {
    const docs = await CPA.get("/api/admin/corpus/docs");
    const rows = docs.map(d => el("tr", {}, [
      el("td", { class: "mono small", text: "#" + d.id }),
      el("td", {}, [
        el("div", { text: d.title }),
        el("div", {
          class: "muted small",
          text: (d.origin === "upload"
            ? "上传者：" + (d.uploader_name || "-")
            : "项目自带") + " · " + d.ingested_at
        })
      ]),
      el("td", { text: d.subject_name || "未标注" }),
      el("td", { class: "num", text: d.chunk_count }),
      el("td", {}, el("span", {
        class: "tag " + (d.visibility === "public" ? "ok" : ""),
        text: d.visibility === "public" ? "参与检索" : "已下架"
      })),
      el("td", {}, el("div", { class: "row" }, [
        el("button", {
          class: "ghost sm",
          text: d.visibility === "public" ? "下架" : "上架",
          onclick: async () => {
            try {
              await CPA.post("/api/admin/corpus/visibility",
                { doc_id: d.id, visible: d.visibility !== "public" });
              await Promise.all([refreshDocs(), loadStats()]);
            } catch (e) { toast(e.message, "bad"); }
          }
        }),
        el("button", {
          class: "danger sm", text: "删除",
          onclick: async () => {
            if (!confirm("彻底删除《" + d.title + "》？其教材片段与向量会一并删除。")) return;
            try {
              await CPA.post("/api/admin/corpus/delete", { doc_id: d.id });
              toast("已删除");
              await Promise.all([refreshDocs(), loadStats()]);
            } catch (e) { toast(e.message, "bad"); }
          }
        })
      ]))
    ]));
    clear($("#docs-table")).appendChild(
      table(["ID", "教材", "科目", "片段", "状态", "操作"], rows));
  }

  /* ------------------------------------------------------------ 账号 */
  async function refreshUsers() {
    const data = await CPA.get("/api/admin/users");
    const me = CPA.username();
    const rows = data.items.map(u => el("tr", {}, [
      el("td", {}, [
        el("div", { text: u.display_name || u.username }),
        el("div", { class: "muted small", text: u.username })
      ]),
      el("td", {}, el("span", {
        class: "tag " + (u.role === "admin" ? "warn" : ""),
        text: u.role === "admin" ? "管理员" : "学员"
      })),
      el("td", {}, el("span", {
        class: "tag " + (u.is_active ? "ok" : "bad"),
        text: u.is_active ? "启用" : "停用"
      })),
      el("td", { class: "num", text: u.answer_count }),
      el("td", { class: "num", text: u.wrong_open }),
      el("td", { class: "muted small nowrap", text: u.last_login_at || "从未登录" }),
      el("td", {}, u.username === me
        ? el("span", { class: "muted small", text: "当前账号" })
        : el("div", { class: "row" }, [
          el("button", {
            class: "ghost sm",
            text: u.role === "admin" ? "降为学员" : "设为管理员",
            onclick: () => act("/api/admin/users/role",
              { user_id: u.id, role: u.role === "admin" ? "student" : "admin" })
          }),
          el("button", {
            class: "ghost sm", text: u.is_active ? "停用" : "启用",
            onclick: () => act("/api/admin/users/active",
              { user_id: u.id, active: !u.is_active })
          }),
          el("button", {
            class: "ghost sm", text: "重置密码",
            onclick: () => {
              const pwd = prompt("为 " + u.username + " 设置新密码（至少 6 位）：");
              if (pwd) act("/api/admin/users/password", { user_id: u.id, new_password: pwd });
            }
          }),
          el("button", {
            class: "danger sm", text: "删除",
            onclick: () => {
              if (confirm("删除账号 " + u.username + " 及其全部学习数据？不可恢复。"))
                act("/api/admin/users/delete", { user_id: u.id });
            }
          })
        ]))
    ]));
    clear($("#users-table")).appendChild(
      table(["账号", "角色", "状态", "答题数", "待攻克错题", "最近登录", "操作"], rows));
  }

  async function act(path, body) {
    try {
      await CPA.post(path, body);
      toast("已生效");
      await refreshUsers();
    } catch (e) { toast(e.message, "bad"); }
  }

  function bindUserNew() {
    $("#user-new").addEventListener("click", async function () {
      const username = prompt("新账号用户名（2~32 位）：");
      if (!username) return;
      const password = prompt("初始密码（至少 6 位）：");
      if (!password) return;
      const asAdmin = confirm("设为管理员？点「取消」创建为普通学员。");
      try {
        await CPA.post("/api/admin/users/create", {
          username: username.trim(), password: password,
          role: asAdmin ? "admin" : "student"
        });
        toast("账号已创建");
        await refreshUsers();
      } catch (e) { toast(e.message, "bad"); }
    });
  }

  /* ------------------------------------------------------------ 启动 */
  document.addEventListener("DOMContentLoaded", async function () {
    bindUpload();
    bindUserNew();
    $("#jobs-refresh").addEventListener("click",
      () => refreshJobs().catch(e => toast(e.message, "bad")));
    $("#docs-reindex").addEventListener("click", async function () {
      if (!confirm("重新扫描 data/corpus 目录并入库？内容未变的文件会自动跳过。")) return;
      try {
        const r = await CPA.post("/api/admin/corpus/reindex", {});
        toast("已重扫：" + r.files + " 个文件 / " + r.chunks + " 个片段");
        await Promise.all([refreshDocs(), loadStats()]);
      } catch (e) { toast(e.message, "bad"); }
    });
    try {
      await loadSubjects();
      const [, jobs] = await Promise.all([
        loadStats(), refreshJobs(), refreshDocs(), refreshUsers()
      ]);
      if (busyIn(jobs || [])) startPolling();
    } catch (e) { toast(e.message, "bad"); }
  });
})();
