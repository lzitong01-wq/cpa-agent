/* 题目卡片：渲染 → 收集作答 → 提交判分 → 展示解析。
   题库页 / 答题页 / 错题本页 / 聊天页共用同一份实现，保证交互一致。 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;

  const OBJECTIVE = { single: 1, multiple: 1, judge: 1 };

  /* 把答案统一成便于展示的字符串 */
  function answerText(a) {
    if (a === null || a === undefined) return "-";
    if (Array.isArray(a)) return a.join("、");
    if (typeof a === "object") {
      if (Array.isArray(a.points)) return a.points.join("；");
      return JSON.stringify(a);
    }
    if (a === true) return "正确";
    if (a === false) return "错误";
    return String(a);
  }

  /* ---------------------------------------------------------------- 渲染 */
  /* opts: {mode, showReason, onSubmitted(result, question), onNext, compact} */
  CPA.renderQuestion = function (q, opts) {
    opts = opts || {};
    const state = { picked: [], judge: null, locked: false, startedAt: Date.now() };
    const card = el("div", { class: "qcard", "data-qid": q.id });

    card.appendChild(el("header", {}, [
      el("span", { class: "tag brand", text: q.qtype_label }),
      el("span", { class: "tag", text: (q.subject_name || "") + " · " + (q.chapter_name || "") }),
      el("span", { class: "tag", text: q.knowledge_point_name || "" }),
      el("span", { class: "tag " + (q.difficulty_label === "难" ? "bad" : (q.difficulty_label === "易" ? "ok" : "warn")), text: "难度" + (q.difficulty_label || "") }),
      el("span", { class: "spacer" }),
      el("span", { class: "qid", text: "#" + q.id + " · " + q.score + "分" })
    ]));

    const body = el("div", { class: "body" });
    card.appendChild(body);
    if (q.material) body.appendChild(el("div", { class: "material", text: q.material }));
    body.appendChild(el("div", { class: "stem", text: q.stem }));
    if (opts.showReason !== false && q.reason) {
      body.appendChild(el("div", { class: "reason", text: "选题依据：" + q.reason }));
    }

    let collect = () => null;
    const optNodes = {};

    if (q.qtype === "single" || q.qtype === "multiple") {
      const box = el("div", { class: "opts" });
      (q.options || []).forEach(function (o) {
        const key = Array.isArray(o) ? o[0] : o.key;
        const text = Array.isArray(o) ? o[1] : o.text;
        const input = el("input", {
          type: q.qtype === "single" ? "radio" : "checkbox",
          name: "q" + q.id, value: key
        });
        const row = el("label", { class: "opt" }, [
          input, el("span", { class: "k", text: key }), el("span", { text: text })
        ]);
        optNodes[key] = row;
        input.addEventListener("change", function () {
          if (state.locked) return;
          if (q.qtype === "single") {
            state.picked = [key];
            Object.values(optNodes).forEach(n => n.classList.remove("picked"));
            row.classList.add("picked");
          } else {
            state.picked = Object.keys(optNodes).filter(k => optNodes[k].querySelector("input").checked);
            row.classList.toggle("picked", input.checked);
          }
          syncSubmit();
        });
        box.appendChild(row);
      });
      body.appendChild(box);
      collect = () => q.qtype === "single" ? (state.picked[0] || null) : state.picked.slice().sort();

    } else if (q.qtype === "judge") {
      const box = el("div", { class: "judge-box" });
      [["对", true], ["错", false]].forEach(function (pair) {
        const b = el("button", {
          type: "button", text: pair[0],
          onclick: function () {
            if (state.locked) return;
            state.judge = pair[1];
            Array.prototype.forEach.call(box.children, c => c.classList.remove("primary"));
            b.classList.add("primary");
            syncSubmit();
          }
        });
        box.appendChild(b);
      });
      body.appendChild(box);
      collect = () => state.judge;

    } else {
      const ta = el("textarea", {
        placeholder: q.qtype === "comprehensive"
          ? "请分点作答，写出计算过程与依据……" : "请分点作答，写出关键要点……"
      });
      ta.addEventListener("input", syncSubmit);
      body.appendChild(el("div", { style: "margin-top:14px" }, ta));
      collect = () => (ta.value || "").trim();
    }

    const submitBtn = el("button", { class: "primary", text: "提交答案", disabled: true });
    const actions = el("div", { class: "row", style: "margin-top:14px" }, [submitBtn]);
    if (opts.onNext) {
      actions.appendChild(el("button", { class: "ghost", text: "跳过", onclick: () => opts.onNext(q) }));
    }
    actions.appendChild(el("button", {
      class: "ghost sm", text: "🤖 问 AI 这道题",
      onclick: () => CPA.askAboutQuestion && CPA.askAboutQuestion(q)
    }));
    body.appendChild(actions);

    function syncSubmit() {
      const v = collect();
      const empty = v === null || v === undefined || v === "" || (Array.isArray(v) && !v.length);
      submitBtn.disabled = state.locked || empty;
    }

    submitBtn.addEventListener("click", async function () {
      if (state.locked) return;
      submitBtn.disabled = true;
      submitBtn.textContent = "判分中…";
      try {
        const res = await CPA.post("/api/practice/submit", {
          question_id: q.id, answer: collect(),
          duration_ms: Date.now() - state.startedAt, mode: opts.mode || "chapter",
          paper_id: opts.paperId || null
        });
        state.locked = true;
        lock(res);
        body.appendChild(buildResult(res, q, opts));
        if (opts.onSubmitted) opts.onSubmitted(res, q);
      } catch (err) {
        CPA.toast("提交失败：" + err.message, "bad");
        submitBtn.disabled = false;
        submitBtn.textContent = "提交答案";
      }
    });

    function lock(res) {
      submitBtn.remove();
      const right = [].concat(res.correct_answer === null ? [] : res.correct_answer)
        .map(x => String(x));
      Object.keys(optNodes).forEach(function (k) {
        const n = optNodes[k];
        n.classList.add("locked");
        n.querySelector("input").disabled = true;
        if (right.indexOf(k) >= 0) n.classList.add("right");
        else if (n.classList.contains("picked")) n.classList.add("wrong");
      });
      body.querySelectorAll(".judge-box button").forEach(b => b.disabled = true);
      body.querySelectorAll("textarea").forEach(t => t.readOnly = true);
    }
    return card;
  };

  /* ---------------------------------------------------------------- 判分结果 */
  function buildResult(res, q, opts) {
    const box = el("div", { class: "result " + (res.is_correct ? "right" : "wrong") });
    box.appendChild(el("div", { class: "verdict" }, [
      el("span", { text: res.is_correct ? "✅ 回答正确" : "❌ 回答错误" }),
      el("span", { class: "score", text: "得分 " + res.score + " / " + res.full_score }),
      res.added_to_wrongbook ? el("span", { class: "tag bad", text: "已加入错题本" }) : null
    ]));
    box.appendChild(el("div", { class: "kv" }, [
      el("b", { text: "正确答案：" }), el("span", { text: answerText(res.correct_answer) })
    ]));
    if (res.detail) box.appendChild(el("div", { class: "kv muted small", text: res.detail }));
    if (!OBJECTIVE[q.qtype]) {
      if ((res.hit_points || []).length) {
        box.appendChild(el("div", { class: "kv small" }, [
          el("b", { text: "已答到：" }), el("span", { text: res.hit_points.join("；") })
        ]));
      }
      if ((res.missed_points || []).length) {
        box.appendChild(el("div", { class: "kv small" }, [
          el("b", { text: "还缺：" }), el("span", { text: res.missed_points.join("；") })
        ]));
      }
    }
    box.appendChild(el("div", { class: "analysis" }, [
      el("b", { text: "标准解析\n" }), el("span", { text: res.analysis || "" })
    ]));

    const acts = el("div", { class: "actions" });
    acts.appendChild(el("button", {
      class: "sm", text: "🤖 让 AI 再讲一遍",
      onclick: () => CPA.askAboutQuestion && CPA.askAboutQuestion(q)
    }));
    if (opts.onRemoveWrong) {
      acts.appendChild(el("button", {
        class: "sm", text: "移出错题本",
        onclick: (e) => opts.onRemoveWrong(q, e.target)
      }));
    }
    if (opts.onNext) {
      acts.appendChild(el("button", { class: "sm primary", text: "下一题 →", onclick: () => opts.onNext(q) }));
    }
    box.appendChild(acts);
    return box;
  }

  CPA.answerText = answerText;
})();
