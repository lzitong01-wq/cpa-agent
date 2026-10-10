/* 首页：概览统计 + 快速取题 + 知识点画像 + 内嵌聊天框 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  async function loadSubjects() {
    const subjects = await CPA.get("/api/subjects");
    const sel = $("#subject-pick");
    subjects.forEach(s => sel.appendChild(el("option", { value: s.id, text: s.name })));
  }

  async function loadDashboard() {
    const [dash, wb] = await Promise.all([
      CPA.get("/api/stats/dashboard"),
      CPA.get("/api/wrongbook/summary")
    ]);
    const ov = dash.overview || {};
    $("#s-total").textContent = ov.total || 0;
    $("#s-acc").textContent = CPA.pct(ov.accuracy);
    $("#s-wrong").textContent = wb.open_total || 0;
    $("#s-kp").textContent = (ov.kp_touched || 0) + " / " + (ov.kp_total || 0);

    const box = CPA.clear($("#subject-table"));
    if (!(dash.by_subject || []).length) {
      box.appendChild(el("div", { class: "empty", text: "还没有答题记录，先做几道题吧。" }));
    } else {
      const t = el("table", {}, [el("thead", {}, el("tr", {}, [
        el("th", { text: "科目" }), el("th", { class: "num", text: "答题" }),
        el("th", { class: "num", text: "正确" }), el("th", { class: "num", text: "正确率" }),
        el("th", { style: "width:120px", text: "" })
      ]))]);
      const tb = el("tbody");
      dash.by_subject.forEach(function (r) {
        tb.appendChild(el("tr", {}, [
          el("td", { text: r.subject_name }),
          el("td", { class: "num", text: r.total }),
          el("td", { class: "num", text: r.correct }),
          el("td", { class: "num " + CPA.accClass(r.accuracy), text: CPA.pct(r.accuracy) }),
          el("td", {}, el("div", { class: "bar" },
            el("i", { class: CPA.accClass(r.accuracy), style: "width:" + ((r.accuracy || 0) * 100) + "%" })))
        ]));
      });
      t.appendChild(tb);
      box.appendChild(t);
    }

    const weak = CPA.clear($("#weak-box"));
    const list = (dash.weakest_kp || []).filter(r => r.total > 0);
    if (!list.length) {
      weak.appendChild(el("div", { class: "empty", text: "暂无数据：做题后这里会按正确率排出最薄弱的知识点。" }));
    } else {
      list.slice(0, 8).forEach(function (r) {
        weak.appendChild(el("div", {
          class: "row", style: "padding:7px 0;border-bottom:1px solid var(--border)"
        }, [
          el("span", { class: "tag", text: r.subject_name }),
          el("span", { text: r.knowledge_point_name }),
          el("span", { class: "spacer", style: "flex:1" }),
          el("span", { class: "small " + CPA.accClass(r.accuracy), text: CPA.pct(r.accuracy) + "（" + r.total + "题）" }),
          el("button", {
            class: "sm", text: "专项刷题",
            onclick: () => go({ mode: "chapter", knowledge_point_id: r.knowledge_point_id, limit: 5 })
          })
        ]));
      });
    }
  }

  async function loadProfile() {
    const box = CPA.clear($("#profile-box"));
    const p = await CPA.get("/api/practice/profile");
    const NAMES = { wrong: "错题关联知识点", weak: "薄弱知识点", fresh: "尚未练习", mastered: "已掌握" };
    const sizes = p.bucket_sizes || {};
    box.appendChild(el("div", { class: "small muted", text: "已答 " + p.answered_total + " 题，覆盖 " + p.kp_total + " 个知识点" }));
    Object.keys(NAMES).forEach(function (k) {
      const quota = Math.round((p.quota[k] || 0) * 100);
      box.appendChild(el("div", { style: "margin-top:9px" }, [
        el("div", { class: "row small" }, [
          el("span", { text: NAMES[k] }),
          el("span", { class: "spacer", style: "flex:1" }),
          el("span", { class: "muted", text: (sizes[k] || 0) + " 个 · 配额 " + quota + "%" })
        ]),
        el("div", { class: "bar", style: "margin-top:4px" },
          el("i", { class: k === "wrong" ? "bad" : (k === "weak" ? "warn" : (k === "mastered" ? "ok" : "")), style: "width:" + quota + "%" }))
      ]));
    });
  }

  function go(params) {
    CPA.setQueue({ fetch: params });
    location.href = "/practice";
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadSubjects().catch(e => CPA.toast(e.message, "bad"));
    loadDashboard().catch(e => CPA.toast(e.message, "bad"));
    loadProfile().catch(e => CPA.toast(e.message, "bad"));

    const subjOf = () => $("#subject-pick").value || null;
    const limitOf = () => Number($("#limit-pick").value || 10);
    $("#btn-recommend").addEventListener("click", () => go({ mode: "recommend", subject_id: subjOf(), limit: limitOf() }));
    $("#btn-mock").addEventListener("click", () => go({ mode: "mock", subject_id: subjOf(), limit: limitOf() }));
    $("#btn-wrong").addEventListener("click", () => go({ mode: "wrong", subject_id: subjOf(), limit: limitOf() }));

    CPA.mountChat({
      root: $("#chat-root"),
      onPickQuestion: function (q, questions) {
        CPA.setQueue({ questions: questions, mode: "agent" });
        location.href = "/practice";
      }
    });
  });
})();
