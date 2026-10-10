/* 答题页：顺序做题、即时判分、标准解析、进度统计。
   题目来源三种：其他页面通过 sessionStorage 传入的题组、传入的取题参数、默认智能推送。 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  const MODE_TEXT = {
    recommend: "智能推送", chapter: "专项章节", mock: "模拟刷题",
    wrong: "错题重做", paper: "组卷练习", agent: "AI 推送"
  };
  const state = { queue: [], idx: 0, mode: "recommend", paperId: null, done: 0, right: 0, wrong: 0 };

  function renderSession() {
    const total = state.queue.length;
    $("#session-box").textContent = total
      ? "模式：" + (MODE_TEXT[state.mode] || state.mode) + "，共 " + total + " 道题，当前第 "
        + Math.min(state.idx + 1, total) + " 道"
      : "当前没有题目，点击右侧「智能推送」开始。";
    $("#progress-bar").style.width = total ? (state.done / total * 100) + "%" : "0%";
    $("#p-done").textContent = state.done;
    $("#p-right").textContent = state.right;
    $("#p-wrong").textContent = state.wrong;
  }

  function showCurrent() {
    const stage = CPA.clear($("#stage"));
    CPA.clear($("#done-box"));
    renderSession();
    if (state.idx >= state.queue.length) { showFinish(); return; }
    const q = state.queue[state.idx];
    stage.appendChild(CPA.renderQuestion(q, {
      mode: state.mode,
      paperId: state.paperId,
      onSubmitted: function (res) {
        state.done += 1;
        if (res.is_correct) state.right += 1; else state.wrong += 1;
        renderSession();
      },
      onNext: function () { state.idx += 1; showCurrent(); }
    }));
  }

  function showFinish() {
    const box = CPA.clear($("#done-box"));
    if (!state.queue.length) {
      $("#stage").appendChild(el("div", { class: "card" },
        el("div", { class: "empty", text: "没有可做的题目。换个科目，或者去题库页挑一个知识点。" })));
      return;
    }
    const acc = state.done ? state.right / state.done : 0;
    box.appendChild(el("div", { class: "card" }, [
      el("h2", { text: "🎉 本组完成" }),
      el("div", { class: "row" }, [
        el("div", { class: "stat", style: "flex:1" }, [
          el("span", { class: "num", text: state.done }), el("span", { class: "label", text: "作答" })]),
        el("div", { class: "stat ok", style: "flex:1" }, [
          el("span", { class: "num", text: state.right }), el("span", { class: "label", text: "答对" })]),
        el("div", { class: "stat bad", style: "flex:1" }, [
          el("span", { class: "num", text: state.wrong }), el("span", { class: "label", text: "答错" })]),
        el("div", { class: "stat brand", style: "flex:1" }, [
          el("span", { class: "num", text: CPA.pct(acc) }), el("span", { class: "label", text: "正确率" })])
      ]),
      el("div", { class: "row", style: "margin-top:12px" }, [
        el("button", { class: "primary", text: "再来一组（智能推送）", onclick: () => fetchQueue({ mode: "recommend", limit: 10 }) }),
        el("button", { text: "去错题本", onclick: () => location.href = "/wrongbook" }),
        el("button", { text: "看统计", onclick: () => location.href = "/stats" })
      ])
    ]));
  }

  async function fetchQueue(params) {
    const stage = CPA.clear($("#stage"));
    stage.appendChild(el("div", { class: "card" }, el("div", { class: "loading", text: "取题中…" })));
    CPA.clear($("#paper-summary"));
    try {
      const res = await CPA.post("/api/practice/next", params);
      setQueue(res.questions || [], params.mode || "recommend", res.paper_id || null);
      if (!res.questions || !res.questions.length) {
        CPA.toast("该条件下没有可用题目", "bad");
      }
    } catch (e) {
      CPA.toast(e.message, "bad");
      CPA.clear($("#stage")).appendChild(el("div", { class: "card" },
        el("div", { class: "empty", text: "取题失败：" + e.message })));
    }
  }

  function setQueue(questions, mode, paperId) {
    state.queue = questions || [];
    state.idx = 0; state.done = 0; state.right = 0; state.wrong = 0;
    state.mode = mode || "recommend";
    state.paperId = paperId || null;
    showCurrent();
  }

  function showPaperSummary(paper) {
    const QT = { single: "单选", multiple: "多选", judge: "判断", short: "简答", comprehensive: "综合" };
    const dist = Object.entries(paper.type_distribution || {}).map(([k, v]) => QT[k] + " " + v).join(" · ");
    CPA.clear($("#paper-summary")).appendChild(el("div", { class: "card tight" }, [
      el("div", { class: "row" }, [
        el("span", { class: "tag brand", text: "试卷 " + paper.paper_id }),
        el("span", { class: "tag", text: paper.count + " 题 / " + paper.total_score + " 分" }),
        dist ? el("span", { class: "tag", text: dist }) : null,
        paper.available !== undefined ? el("span", { class: "small muted", text: "符合条件的题库总量 " + paper.available + " 题" }) : null
      ])
    ]));
  }

  document.addEventListener("DOMContentLoaded", async function () {
    try {
      const subjects = await CPA.get("/api/subjects");
      const sel = $("#subject-pick");
      subjects.forEach(s => sel.appendChild(el("option", { value: s.id, text: s.name })));
    } catch (e) { /* 不阻塞做题 */ }

    $("#btn-recommend").addEventListener("click", () => fetchQueue({
      mode: "recommend", subject_id: $("#subject-pick").value || null,
      limit: Number($("#limit-pick").value || 10)
    }));
    $("#btn-mock").addEventListener("click", () => fetchQueue({
      mode: "mock", subject_id: $("#subject-pick").value || null,
      limit: Number($("#limit-pick").value || 10)
    }));
    $("#btn-wrong").addEventListener("click", () => fetchQueue({
      mode: "wrong", subject_id: $("#subject-pick").value || null,
      limit: Number($("#limit-pick").value || 10)
    }));
    $("#fab").addEventListener("click", () => location.href = "/chat");

    const q = CPA.takeQueue();
    if (q && q.questions && q.questions.length) {
      if (q.paper) showPaperSummary(q.paper);
      setQueue(q.questions, q.mode, q.paper_id);
    } else if (q && q.fetch) {
      fetchQueue(q.fetch);
    } else {
      fetchQueue({ mode: "recommend", limit: 10 });
    }
  });
})();
