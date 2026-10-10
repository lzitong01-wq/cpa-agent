/* AI 答疑聊天组件：三类意图统一走 /api/chat。
   挂载点需含 .chat-log / .chat-input textarea / 发送按钮，见 chat.html 与 base.html。 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;

  const SUGGEST = [
    "帮我解释什么是暂时性差异",
    "给我推送会计收入章节的题",
    "来5道税法的题",
    "我要刷错题",
    "重要性水平怎么确定",
    "五力模型包括哪些内容"
  ];

  function bubble(role, text, extra) {
    const who = role === "user" ? "我" : (role === "sys" ? "!" : "AI");
    const wrap = el("div", { class: "msg " + role }, [
      el("div", { class: "who", text: who }),
      el("div", { class: "bubble" }, [el("div", { text: text })])
    ]);
    if (extra) wrap.querySelector(".bubble").appendChild(extra);
    return wrap;
  }

  function citeBlock(cites) {
    if (!cites || !cites.length) return null;
    const d = el("details", { class: "cites", open: "open" }, [
      el("summary", { text: "📖 教材来源（" + cites.length + " 条，点击展开/收起）" })
    ]);
    cites.forEach(function (c) {
      const src = [c.subject, c.chapter, c.knowledge_point].filter(Boolean).join(" · ");
      d.appendChild(el("div", { class: "cite" }, [
        el("b", { text: "[" + c.no + "] " + (src || c.doc_title || "教材") }),
        el("span", { class: "small muted", text: "  相似度 " + (c.score === undefined ? "-" : c.score) }),
        el("span", { class: "ex", text: c.excerpt || "" })
      ]));
    });
    return d;
  }

  /* mount: {root, onPickQuestion} */
  CPA.mountChat = function (mount) {
    const root = mount.root;
    const log = root.querySelector(".chat-log");
    const ta = root.querySelector(".chat-input textarea");
    const sendBtn = root.querySelector(".chat-send");
    const statusBar = root.querySelector(".chat-status");
    const pickedBar = root.querySelector(".chat-picked");
    const hints = root.querySelector(".chat-input .hints");
    let currentQuestionId = null;
    let busy = false;

    if (hints) {
      SUGGEST.forEach(function (s) {
        hints.appendChild(el("button", { type: "button", text: s, onclick: () => { ta.value = s; ta.focus(); } }));
      });
    }

    function scroll() { log.scrollTop = log.scrollHeight; }
    function setPicked(q) {
      currentQuestionId = q ? q.id : null;
      if (!pickedBar) return;
      if (!q) { pickedBar.style.display = "none"; return; }
      pickedBar.style.display = "block";
      CPA.clear(pickedBar);
      pickedBar.appendChild(el("span", {
        text: "当前追问的题目：#" + q.id + "（" + (q.knowledge_point_name || "") + "）  "
      }));
      pickedBar.appendChild(el("a", { href: "#", text: "取消", onclick: (e) => { e.preventDefault(); setPicked(null); } }));
    }
    mount.setPicked = setPicked;

    /* 状态条：是否接入大模型 + 知识库规模 */
    CPA.get("/api/chat/status").then(function (st) {
      if (!statusBar) return;
      CPA.clear(statusBar);
      const anyLlm = st.llm && st.llm.any_available;
      statusBar.appendChild(el("span", {
        class: "tag " + (anyLlm ? "ok" : "warn"),
        text: anyLlm ? "大模型已接入" : "纯检索模式（未配置大模型 API）"
      }));
      const idx = (st.rag && st.rag.index) || {};
      statusBar.appendChild(el("span", {
        class: "tag " + (st.rag && st.rag.ok ? "brand" : "bad"),
        text: "知识库 " + (idx.chunks || 0) + " 个教材片段"
      }));
      statusBar.appendChild(el("span", { class: "small", text: "答疑严格基于教材检索，检索不到会明确告知，不编造。" }));
    }).catch(() => { });

    async function send(text) {
      text = (text === undefined ? ta.value : text).trim();
      if (!text || busy) return;
      busy = true;
      sendBtn.disabled = true;
      ta.value = "";
      log.appendChild(bubble("user", text));
      const thinking = bubble("assistant", "思考中…");
      log.appendChild(thinking);
      scroll();
      try {
        const res = await CPA.post("/api/chat", {
          session_id: CPA.sessionId(), message: text,
          current_question_id: currentQuestionId
        });
        thinking.remove();
        log.appendChild(renderAnswer(res));
        if (res.question) setPicked(res.question);
        scroll();
      } catch (err) {
        thinking.remove();
        log.appendChild(bubble("sys", "请求失败：" + err.message));
        scroll();
      } finally {
        busy = false;
        sendBtn.disabled = false;
        ta.focus();
      }
    }
    mount.send = send;

    function renderAnswer(res) {
      const extra = el("div");
      const meta = el("div", { class: "meta" });
      const INTENT = {
        practice: "意图：刷题", knowledge_qa: "意图：知识点答疑",
        question_qa: "意图：题目答疑", other: "意图：引导"
      };
      meta.appendChild(el("span", { class: "tag brand", text: INTENT[res.intent] || res.intent }));
      if (res.mode) meta.appendChild(el("span", { class: "tag", text: "模式：" + res.mode }));
      if (res.refused) meta.appendChild(el("span", { class: "tag warn", text: "教材未命中，已拒答" }));
      if (res.degraded) meta.appendChild(el("span", { class: "tag warn", text: "纯检索模式" }));
      if (res.llm_used && res.model) meta.appendChild(el("span", { class: "tag ok", text: res.model }));
      if (res.detector) meta.appendChild(el("span", { class: "tag", text: "识别：" + res.detector }));
      extra.appendChild(meta);

      const cites = citeBlock(res.citations);
      if (cites) extra.appendChild(cites);

      if (res.questions && res.questions.length) {
        const box = el("div", { class: "chat-qs" });
        res.questions.forEach(function (q) {
          box.appendChild(el("div", {
            class: "chat-q",
            onclick: () => mount.onPickQuestion && mount.onPickQuestion(q, res.questions, res.mode)
          }, [
            el("div", { class: "top" }, [
              el("span", { class: "tag brand", text: q.qtype_label }),
              el("span", { class: "tag", text: q.knowledge_point_name || "" }),
              q.reason ? el("span", { class: "tag warn", text: q.reason }) : null
            ]),
            el("div", { text: (q.stem || "").slice(0, 88) + ((q.stem || "").length > 88 ? "…" : "") })
          ]));
        });
        extra.appendChild(box);
        if (mount.onPickQuestion) {
          extra.appendChild(el("div", { class: "row", style: "margin-top:10px" }, [
            el("button", {
              class: "primary sm", text: "开始做这 " + res.questions.length + " 道题 →",
              onclick: () => mount.onPickQuestion(res.questions[0], res.questions, res.mode)
            })
          ]));
        }
      }
      return bubble("assistant", res.reply, extra);
    }

    sendBtn.addEventListener("click", () => send());
    ta.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
    });

    /* 其他页面点"问 AI 这道题"时调过来 */
    CPA.askAboutQuestion = function (q) {
      setPicked(q);
      if (mount.onFocusChat) mount.onFocusChat();
      send("这道题我没看懂，帮我讲讲");
    };

    /* 恢复本会话历史 */
    CPA.get("/api/chat/history", { session_id: CPA.sessionId(), limit: 40 })
      .then(function (rows) {
        if (!rows || !rows.length) {
          log.appendChild(bubble("assistant",
            "我是 CPA 智能答疑助手。可以让我推送题目（如「给我推送会计收入章节的题」）、"
            + "讲解知识点（如「什么是暂时性差异」），或者针对某道题追问（如「这道题我没看懂」）。\n"
            + "知识点答疑严格基于 CPA 教材检索作答，并在末尾标注科目与章节来源；教材里没有的内容我会直接告诉你。"));
          return;
        }
        rows.forEach(function (r) {
          if (r.role === "user") { log.appendChild(bubble("user", r.content)); return; }
          const extra = el("div");
          const c = citeBlock(r.citations);
          if (c) extra.appendChild(c);
          log.appendChild(bubble("assistant", r.content, c ? extra : null));
        });
        scroll();
      }).catch(() => { });

    return mount;
  };
})();
