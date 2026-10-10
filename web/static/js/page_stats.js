/* 答题历史统计页：概览 + 趋势 + 多维正确率 + 明细分页 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  const QT = { single: "单选题", multiple: "多选题", judge: "判断题", short: "简答题", comprehensive: "综合题" };
  const MODE = { recommend: "智能推送", chapter: "专项章节", mock: "模拟刷题", wrong: "错题重做", paper: "组卷练习", agent: "AI 推送" };
  const state = { offset: 0 };

  function table(head, rows, render) {
    if (!rows.length) return el("div", { class: "empty", text: "暂无数据" });
    const t = el("table", {}, [el("thead", {}, el("tr", {},
      head.map(h => el("th", { class: h.num ? "num" : "", text: h.text, style: h.style || "" }))))]);
    const tb = el("tbody");
    rows.forEach(r => tb.appendChild(render(r)));
    t.appendChild(tb);
    return t;
  }

  function accCell(r) {
    return el("td", { class: "num " + CPA.accClass(r.accuracy), text: CPA.pct(r.accuracy) });
  }
  function barCell(r) {
    return el("td", {}, el("div", { class: "bar" },
      el("i", { class: CPA.accClass(r.accuracy), style: "width:" + ((r.accuracy || 0) * 100) + "%" })));
  }

  function renderTrend(rows) {
    const box = CPA.clear($("#trend"));
    if (!rows.length) { box.appendChild(el("div", { class: "empty", text: "暂无数据" })); return; }
    const max = Math.max.apply(null, rows.map(r => r.total)) || 1;
    const chart = el("div", { class: "row", style: "align-items:flex-end;gap:6px;height:150px;padding-top:8px" });
    rows.forEach(function (r) {
      const h = Math.max(4, Math.round(r.total / max * 120));
      chart.appendChild(el("div", {
        style: "flex:1;display:flex;flex-direction:column;align-items:center;gap:4px",
        title: r.day + "：答 " + r.total + " 题，正确率 " + CPA.pct(r.accuracy)
      }, [
        el("div", { class: "small muted", text: r.total || "" }),
        el("div", {
          style: "width:100%;border-radius:6px 6px 0 0;height:" + h + "px;background:"
            + (CPA.accClass(r.accuracy) === "ok" ? "#12a15e"
              : CPA.accClass(r.accuracy) === "warn" ? "#d98207"
                : CPA.accClass(r.accuracy) === "bad" ? "#e0403f" : "#dfe4ee")
        }),
        el("div", { class: "small muted", text: (r.day || "").slice(5) })
      ]));
    });
    box.appendChild(chart);
  }

  async function loadAll() {
    const sid = $("#f-subject").value || null;
    const days = Number($("#f-days").value);
    const dash = await CPA.get("/api/stats/dashboard", { subject_id: sid });
    const ov = dash.overview || {};
    $("#s-total").textContent = ov.total || 0;
    $("#s-acc").textContent = CPA.pct(ov.accuracy);
    $("#s-avg").textContent = ov.avg_seconds || 0;
    $("#s-days").textContent = ov.active_days || 0;

    if (days !== 14) {
      dash.daily_trend = await CPA.get("/api/stats/daily_trend", { subject_id: sid, days: days });
    }
    renderTrend(dash.daily_trend || []);

    CPA.clear($("#by-qtype")).appendChild(table(
      [{ text: "题型" }, { text: "答题", num: true }, { text: "正确", num: true }, { text: "正确率", num: true }, { text: "", style: "width:110px" }],
      dash.by_qtype || [],
      r => el("tr", {}, [el("td", { text: QT[r.qtype] || r.qtype }),
        el("td", { class: "num", text: r.total }), el("td", { class: "num", text: r.correct || 0 }),
        accCell(r), barCell(r)])));

    CPA.clear($("#by-subject")).appendChild(table(
      [{ text: "科目" }, { text: "答题", num: true }, { text: "正确率", num: true }, { text: "", style: "width:110px" }],
      dash.by_subject || [],
      r => el("tr", {}, [el("td", { text: r.subject_name }),
        el("td", { class: "num", text: r.total }), accCell(r), barCell(r)])));

    CPA.clear($("#by-chapter")).appendChild(table(
      [{ text: "章节" }, { text: "答题", num: true }, { text: "正确率", num: true }],
      (dash.by_chapter || []).filter(r => r.total > 0),
      r => el("tr", {}, [
        el("td", {}, [el("span", { class: "small muted", text: r.subject_name + " · " }), el("span", { text: r.chapter_name })]),
        el("td", { class: "num", text: r.total }), accCell(r)])));

    CPA.clear($("#weak")).appendChild(table(
      [{ text: "知识点" }, { text: "答题", num: true }, { text: "正确率", num: true }, { text: "" }],
      (dash.weakest_kp || []).filter(r => r.total > 0),
      r => el("tr", {}, [
        el("td", {}, [el("span", { class: "small muted", text: r.subject_name + " · " }), el("span", { text: r.knowledge_point_name })]),
        el("td", { class: "num", text: r.total }), accCell(r),
        el("td", {}, el("button", {
          class: "sm", text: "专项刷题",
          onclick: function () {
            CPA.setQueue({ fetch: { mode: "chapter", knowledge_point_id: r.knowledge_point_id, limit: 5 } });
            location.href = "/practice";
          }
        }))])));

    CPA.clear($("#by-mode")).appendChild(table(
      [{ text: "模式" }, { text: "答题", num: true }, { text: "正确率", num: true }, { text: "", style: "width:110px" }],
      dash.mode_breakdown || [],
      r => el("tr", {}, [el("td", { text: MODE[r.mode] || r.mode }),
        el("td", { class: "num", text: r.total }), accCell(r), barCell(r)])));
  }

  async function loadRecords() {
    const box = CPA.clear($("#records"));
    box.appendChild(el("div", { class: "loading", text: "加载中…" }));
    const limit = 20;
    const data = await CPA.get("/api/records", {
      subject_id: $("#f-subject").value || null,
      only_wrong: $("#only-wrong").checked ? 1 : null,
      limit: limit, offset: state.offset
    });
    CPA.clear(box);
    $("#page-label").textContent = data.total
      ? (state.offset + 1) + "-" + Math.min(state.offset + limit, data.total) + " / " + data.total
      : "0 / 0";
    $("#btn-prev").disabled = state.offset <= 0;
    $("#btn-next").disabled = state.offset + limit >= data.total;
    box.appendChild(table(
      [{ text: "时间" }, { text: "题目" }, { text: "考点" }, { text: "题型" }, { text: "结果" }, { text: "得分", num: true }, { text: "模式" }],
      data.items || [],
      r => el("tr", {}, [
        el("td", { class: "small nowrap", text: r.answered_at }),
        el("td", {}, [el("span", { class: "small muted mono", text: "#" + r.question_id + " " }), el("span", { text: r.stem_brief })]),
        el("td", { class: "small" }, [el("span", { class: "muted", text: r.subject_name + " · " }), el("span", { text: r.knowledge_point_name })]),
        el("td", { class: "small nowrap", text: QT[r.qtype] || r.qtype }),
        el("td", {}, el("span", { class: "tag " + (r.is_correct ? "ok" : "bad"), text: r.is_correct ? "正确" : "错误" })),
        el("td", { class: "num", text: r.score }),
        el("td", { class: "small nowrap", text: MODE[r.mode] || r.mode })
      ])));
  }

  document.addEventListener("DOMContentLoaded", async function () {
    try {
      const subjects = await CPA.get("/api/subjects");
      subjects.forEach(s => $("#f-subject").appendChild(el("option", { value: s.id, text: s.name })));
    } catch (e) { /* 忽略 */ }

    const reload = function () {
      state.offset = 0;
      loadAll().catch(e => CPA.toast(e.message, "bad"));
      loadRecords().catch(e => CPA.toast(e.message, "bad"));
    };
    $("#f-subject").addEventListener("change", reload);
    $("#f-days").addEventListener("change", () => loadAll().catch(e => CPA.toast(e.message, "bad")));
    $("#only-wrong").addEventListener("change", function () { state.offset = 0; loadRecords(); });
    $("#btn-prev").addEventListener("click", function () { state.offset = Math.max(0, state.offset - 20); loadRecords(); });
    $("#btn-next").addEventListener("click", function () { state.offset += 20; loadRecords(); });
    $("#fab").addEventListener("click", () => location.href = "/chat");
    reload();
  });
})();
