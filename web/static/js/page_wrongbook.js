/* 错题本页：筛选、重做、移除、恢复 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  const state = { offset: 0 };

  function filters() {
    const status = $("#f-status").value;
    return {
      subject_id: $("#f-subject").value || null,
      knowledge_point_id: $("#f-kp").value || null,
      status: status
    };
  }

  async function loadSummary() {
    const s = await CPA.get("/api/wrongbook/summary");
    $("#w-open").textContent = s.open_total || 0;
    $("#w-resolved").textContent = s.resolved_total || 0;
    $("#w-subject").textContent = (s.by_subject || []).filter(r => (r.open_count || 0) > 0).length;
  }

  async function loadSubjects() {
    const subjects = await CPA.get("/api/subjects");
    const sel = $("#f-subject");
    subjects.forEach(s => sel.appendChild(el("option", { value: s.id, text: s.name })));
  }

  async function loadKps() {
    const sel = CPA.clear($("#f-kp"));
    sel.appendChild(el("option", { value: "", text: "全部知识点" }));
    const sid = $("#f-subject").value;
    if (!sid) return;
    const kps = await CPA.get("/api/knowledge_points", { subject_id: sid });
    kps.forEach(k => sel.appendChild(el("option", { value: k.id, text: k.name })));
  }

  async function loadList() {
    const box = CPA.clear($("#list"));
    box.appendChild(el("div", { class: "card" }, el("div", { class: "loading", text: "加载中…" })));
    const limit = Number($("#f-limit").value);
    const data = await CPA.get("/api/wrongbook",
      Object.assign({ limit: limit, offset: state.offset, with_answer: 0 }, filters()));
    CPA.clear(box);
    $("#count-label").textContent = "共 " + data.total + " 条错题记录";
    $("#page-label").textContent = data.total
      ? (state.offset + 1) + "-" + Math.min(state.offset + limit, data.total) + " / " + data.total
      : "0 / 0";
    $("#btn-prev").disabled = state.offset <= 0;
    $("#btn-next").disabled = state.offset + limit >= data.total;

    if (!data.items.length) {
      box.appendChild(el("div", { class: "card" }, el("div", {
        class: "empty",
        text: $("#f-status").value === "open"
          ? "错题本是空的 —— 做错的题会自动收进来。" : "该状态下没有记录。"
      })));
      return;
    }

    const list = el("div", { class: "qlist" });
    data.items.forEach(function (q) {
      const card = el("div", { class: "qcard" });
      const bodyWrap = el("div", { class: "body", style: "display:none" });
      const header = el("header", {}, [
        el("span", { class: "tag brand", text: q.qtype_label }),
        el("span", { class: "tag", text: q.subject_name + " · " + q.chapter_name }),
        el("span", { class: "tag", text: q.knowledge_point_name }),
        el("span", { class: "tag bad", text: "错 " + q.wrong_count + " 次" }),
        q.redo_count ? el("span", { class: "tag", text: "重做 " + q.redo_count + " 次" }) : null,
        q.status === "resolved" ? el("span", { class: "tag ok", text: "已攻克" }) : null,
        el("span", { class: "spacer" }),
        el("span", { class: "qid", text: "#" + q.id }),
        el("button", { class: "sm", text: "重做这道题", onclick: () => openRedo(q, card, bodyWrap) }),
        el("button", {
          class: "sm danger", text: "移出",
          onclick: async function (e) {
            e.target.disabled = true;
            try {
              await CPA.post("/api/wrongbook/remove", { question_id: q.id });
              CPA.toast("已移出错题本");
              refresh();
            } catch (err) { CPA.toast(err.message, "bad"); e.target.disabled = false; }
          }
        })
      ]);
      card.appendChild(header);
      card.appendChild(el("div", { class: "body" }, [
        el("div", { class: "stem", text: (q.stem || "").slice(0, 150) + ((q.stem || "").length > 150 ? "…" : "") }),
        el("div", { class: "small muted", style: "margin-top:6px", text: "最近错于 " + (q.last_wrong_at || "") })
      ]));
      card.appendChild(bodyWrap);
      list.appendChild(card);
    });
    box.appendChild(list);
  }

  /* 就地重做：复用统一的题目卡片，mode=wrong 让记录归到"错题重做" */
  function openRedo(q, card, holder) {
    if (holder.dataset.open === "1") { holder.style.display = "none"; holder.dataset.open = "0"; return; }
    CPA.clear(holder);
    holder.dataset.open = "1";
    holder.style.display = "block";
    holder.appendChild(CPA.renderQuestion(q, {
      mode: "wrong",
      showReason: false,
      onSubmitted: function (res) {
        loadSummary().catch(() => { });
        if (res.is_correct) CPA.toast("答对了！连续答对 2 次会自动从错题本脱离");
      },
      onRemoveWrong: async function (qq, btn) {
        btn.disabled = true;
        try {
          await CPA.post("/api/wrongbook/remove", { question_id: qq.id });
          CPA.toast("已移出错题本");
          refresh();
        } catch (err) { CPA.toast(err.message, "bad"); btn.disabled = false; }
      }
    }));
  }

  function refresh() {
    loadSummary().catch(e => CPA.toast(e.message, "bad"));
    loadList().catch(e => CPA.toast(e.message, "bad"));
  }

  document.addEventListener("DOMContentLoaded", function () {
    loadSubjects().catch(() => { });
    refresh();

    $("#f-subject").addEventListener("change", function () {
      state.offset = 0;
      loadKps().catch(() => { });
      loadList().catch(e => CPA.toast(e.message, "bad"));
    });
    ["#f-kp", "#f-status", "#f-limit"].forEach(function (s) {
      $(s).addEventListener("change", function () {
        state.offset = 0;
        loadList().catch(e => CPA.toast(e.message, "bad"));
      });
    });
    $("#btn-prev").addEventListener("click", function () {
      state.offset = Math.max(0, state.offset - Number($("#f-limit").value));
      loadList();
    });
    $("#btn-next").addEventListener("click", function () {
      state.offset += Number($("#f-limit").value);
      loadList();
    });

    $("#btn-redo").addEventListener("click", async function () {
      try {
        const f = filters();
        const res = await CPA.post("/api/wrongbook/redo", {
          subject_id: f.subject_id,
          knowledge_point_id: f.knowledge_point_id, limit: 10
        });
        if (!res.count) { CPA.toast("没有待重做的错题", "bad"); return; }
        CPA.setQueue({ questions: res.questions, mode: "wrong" });
        location.href = "/practice";
      } catch (e) { CPA.toast(e.message, "bad"); }
    });

    $("#fab").addEventListener("click", () => location.href = "/chat");
  });
})();
