/* 科目章节题库页：三级分类树 + 题目列表分页 + 组卷 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  const QTYPE = {
    single: "单选题", multiple: "多选题", judge: "判断题",
    short: "简答题", comprehensive: "综合题"
  };
  const state = {
    subject_id: null, chapter_id: null, knowledge_point_id: null,
    scopeText: "全部科目", offset: 0, total: 0
  };

  /* ---------------------------------------------------------- 分类树 */
  async function loadTree() {
    const box = CPA.clear($("#tree"));
    const tree = await CPA.get("/api/tree");
    tree.forEach(function (s) {
      const subj = el("details", { class: "subj" }, [
        el("summary", {}, [
          el("span", { text: s.name }),
          el("span", { class: "cnt", text: s.question_count + " 题" })
        ])
      ]);
      subj.querySelector("summary").addEventListener("click", function () {
        pick({ subject_id: s.id, chapter_id: null, knowledge_point_id: null }, s.name);
      });
      (s.chapters || []).forEach(function (c) {
        const chap = el("details", { class: "chap" }, [
          el("summary", {}, [
            el("span", { text: c.name }),
            el("span", { class: "cnt", text: c.question_count + " 题" })
          ])
        ]);
        chap.querySelector("summary").addEventListener("click", function () {
          pick({ subject_id: s.id, chapter_id: c.id, knowledge_point_id: null },
            s.name + " · " + c.name);
        });
        (c.knowledge_points || []).forEach(function (k) {
          chap.appendChild(el("div", {
            class: "kp", "data-kp": k.id,
            onclick: () => pick(
              { subject_id: s.id, chapter_id: c.id, knowledge_point_id: k.id },
              s.name + " · " + c.name + " · " + k.name)
          }, [
            el("span", { text: k.name }),
            el("span", { class: "cnt", text: k.question_count + " 题" })
          ]));
        });
        subj.appendChild(chap);
      });
      box.appendChild(subj);
    });
  }

  function pick(scope, text) {
    Object.assign(state, scope, { offset: 0 });
    state.scopeText = text || "全部科目";
    $("#scope-label").textContent = state.scopeText;
    CPA.$$(".tree .kp").forEach(n => n.classList.toggle(
      "active", Number(n.dataset.kp) === Number(state.knowledge_point_id)));
    loadQuestions().catch(e => CPA.toast(e.message, "bad"));
  }

  /* ---------------------------------------------------------- 题目列表 */
  function filters() {
    return {
      subject_id: state.subject_id, chapter_id: state.chapter_id,
      knowledge_point_id: state.knowledge_point_id,
      qtypes: $("#f-qtype").value || null,
      difficulty: $("#f-difficulty").value || null
    };
  }

  async function loadQuestions() {
    const box = CPA.clear($("#qlist"));
    box.appendChild(el("div", { class: "loading", text: "加载中…" }));
    const limit = Number($("#f-limit").value);
    const data = await CPA.get("/api/questions",
      Object.assign({ limit: limit, offset: state.offset }, filters()));
    state.total = data.total;
    CPA.clear(box);

    const dist = Object.entries(data.type_distribution || {})
      .map(([k, v]) => QTYPE[k] + " " + v).join(" · ");
    $("#count-label").textContent = "共 " + data.total + " 题" + (dist ? "（" + dist + "）" : "");
    $("#page-label").textContent = data.total
      ? (state.offset + 1) + "-" + Math.min(state.offset + limit, data.total) + " / " + data.total
      : "0 / 0";
    $("#btn-prev").disabled = state.offset <= 0;
    $("#btn-next").disabled = state.offset + limit >= data.total;

    if (!data.items.length) {
      box.appendChild(el("div", { class: "empty", text: "该筛选条件下暂无题目。换个知识点或清除筛选试试。" }));
      return;
    }
    const list = el("div", { class: "qlist" });
    data.items.forEach(function (q) {
      list.appendChild(el("div", { class: "qcard" }, [
        el("header", {}, [
          el("span", { class: "tag brand", text: q.qtype_label }),
          el("span", { class: "tag", text: q.chapter_name || "" }),
          el("span", { class: "tag", text: q.knowledge_point_name || "" }),
          el("span", { class: "tag " + (q.difficulty_label === "难" ? "bad" : (q.difficulty_label === "易" ? "ok" : "warn")), text: "难度" + q.difficulty_label }),
          el("span", { class: "spacer" }),
          el("span", { class: "qid", text: "#" + q.id }),
          el("button", {
            class: "sm primary", text: "做这道题",
            onclick: () => start({ questions: [q], mode: "chapter" })
          })
        ]),
        el("div", { class: "body" }, el("div", { class: "stem", text: q.stem }))
      ]));
    });
    box.appendChild(list);
  }

  function start(payload) {
    CPA.setQueue(payload);
    location.href = "/practice";
  }

  /* ---------------------------------------------------------- 事件 */
  document.addEventListener("DOMContentLoaded", function () {
    loadTree().catch(e => CPA.toast(e.message, "bad"));
    loadQuestions().catch(e => CPA.toast(e.message, "bad"));

    ["#f-qtype", "#f-difficulty", "#f-limit"].forEach(function (sel) {
      $(sel).addEventListener("change", function () {
        state.offset = 0;
        loadQuestions().catch(e => CPA.toast(e.message, "bad"));
      });
    });
    $("#btn-prev").addEventListener("click", function () {
      state.offset = Math.max(0, state.offset - Number($("#f-limit").value));
      loadQuestions();
    });
    $("#btn-next").addEventListener("click", function () {
      state.offset += Number($("#f-limit").value);
      loadQuestions();
    });
    $("#btn-reset").addEventListener("click", function () {
      $("#f-qtype").value = "";
      $("#f-difficulty").value = "";
      pick({ subject_id: null, chapter_id: null, knowledge_point_id: null }, "全部科目");
    });

    $("#btn-paper").addEventListener("click", async function () {
      try {
        const f = filters();
        const paper = await CPA.post("/api/paper/build", {
          subject_id: f.subject_id, chapter_id: f.chapter_id,
          knowledge_point_ids: f.knowledge_point_id ? [f.knowledge_point_id] : null,
          qtypes: f.qtypes, difficulty: f.difficulty,
          limit: Number($("#paper-limit").value || 10)
        });
        if (!paper.count) { CPA.toast("该条件下没有可用题目", "bad"); return; }
        start({ questions: paper.questions, mode: "paper", paper_id: paper.paper_id, paper: paper });
      } catch (e) { CPA.toast(e.message, "bad"); }
    });

    $("#btn-chapter-practice").addEventListener("click", function () {
      const f = filters();
      start({
        fetch: {
          mode: "chapter", subject_id: f.subject_id, chapter_id: f.chapter_id,
          knowledge_point_id: f.knowledge_point_id, limit: Number($("#paper-limit").value || 10)
        }
      });
    });

    $("#fab").addEventListener("click", () => location.href = "/chat");
  });
})();
