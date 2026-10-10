/* AI 答疑页：聊天 + 就地做题。
   在对话里点题目 → 右侧「做题区」直接作答 → 判分结果与错题本联动，
   实现需求里的"Agent 必须能用聊天方式触发刷题并进入答题流程"。 */
(function () {
  "use strict";
  const CPA = window.CPA;
  const el = CPA.el;
  const $ = CPA.$;

  document.addEventListener("DOMContentLoaded", function () {
    const stage = $("#inline-stage");
    let queue = [], idx = 0, mode = "agent", chat = null;

    function showAt(i) {
      queue = queue || [];
      idx = i;
      CPA.clear(stage);
      if (idx >= queue.length) {
        stage.appendChild(el("div", { class: "empty small", text: "这一组题做完了，可以让我再推送一组。" }));
        return;
      }
      const q = queue[idx];
      if (chat) chat.setPicked(q);
      stage.appendChild(el("div", { class: "small muted", style: "margin-bottom:8px",
        text: "第 " + (idx + 1) + " / " + queue.length + " 道" }));
      stage.appendChild(CPA.renderQuestion(q, {
        mode: mode,
        onNext: queue.length > 1 ? function () { showAt(idx + 1); } : null
      }));
      stage.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }

    chat = CPA.mountChat({
      root: $("#chat-root"),
      onPickQuestion: function (q, questions, resMode) {
        queue = questions && questions.length ? questions : [q];
        mode = resMode === "wrong" ? "wrong" : (resMode === "mock" ? "mock" : "agent");
        const at = queue.findIndex(x => x.id === q.id);
        showAt(at < 0 ? 0 : at);
      },
      onFocusChat: function () {
        $("#chat-root").querySelector(".chat-input textarea").focus();
      }
    });

    $("#btn-clear").addEventListener("click", async function () {
      if (!confirm("清空当前会话的对话记录？（答题记录与错题本不受影响）")) return;
      try {
        await CPA.post("/api/chat/clear", { session_id: CPA.sessionId() });
        location.reload();
      } catch (e) { CPA.toast(e.message, "bad"); }
    });
  });
})();
