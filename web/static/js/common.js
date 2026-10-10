/* 全站共用的小工具：API 调用、DOM、题目渲染与判分交互 */
(function () {
  "use strict";

  const CPA = window.CPA = {};

  /* ------------------------------------------------------------ API */
  async function request(url, opts) {
    const res = await fetch(url, opts);
    if (res.status === 401) {
      /* 会话过期或未登录：直接跳登录页并带上回跳地址 */
      location.href = "/login?next=" + encodeURIComponent(location.pathname);
      throw new Error("请先登录");
    }
    let body;
    try { body = await res.json(); }
    catch (e) { throw new Error("服务端返回了非 JSON 内容（HTTP " + res.status + "）"); }
    if (!res.ok || body.ok === false) throw new Error(body.error || ("HTTP " + res.status));
    return body.data;
  }
  CPA.get = function (path, params) {
    const qs = params ? "?" + new URLSearchParams(
      Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== "")
    ).toString() : "";
    return request(path + qs);
  };
  CPA.post = function (path, data) {
    return request(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data || {})
    });
  };
  /* multipart 上传：不要手动设 Content-Type，交给浏览器带 boundary */
  CPA.upload = function (path, formData) {
    return request(path, { method: "POST", body: formData });
  };

  /* ------------------------------------------------------------ DOM */
  const el = CPA.el = function (tag, attrs, children) {
    const node = document.createElement(tag);
    if (attrs) Object.entries(attrs).forEach(([k, v]) => {
      if (v === null || v === undefined || v === false) return;
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else if (k === "text") node.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    });
    (Array.isArray(children) ? children : (children ? [children] : []))
      .forEach(c => c !== null && c !== undefined &&
        node.appendChild(typeof c === "object" ? c : document.createTextNode(String(c))));
    return node;
  };
  CPA.$ = s => document.querySelector(s);
  CPA.$$ = s => Array.prototype.slice.call(document.querySelectorAll(s));
  CPA.clear = n => { while (n && n.firstChild) n.removeChild(n.firstChild); return n; };

  CPA.pct = function (v) {
    if (v === null || v === undefined || v === "") return "-";
    return (Number(v) * 100).toFixed(1) + "%";
  };
  CPA.accClass = function (v) {
    if (v === null || v === undefined || v === "") return "";
    const n = Number(v);
    return n >= 0.8 ? "ok" : (n >= 0.6 ? "warn" : "bad");
  };
  CPA.toast = function (text, kind) {
    const n = el("div", { class: "card tight", text: text });
    Object.assign(n.style, {
      position: "fixed", right: "24px", bottom: "92px", zIndex: 99, maxWidth: "340px",
      borderColor: kind === "bad" ? "#f6cfcf" : "#c7ebd8",
      background: kind === "bad" ? "#fdecec" : "#e6f7ef"
    });
    document.body.appendChild(n);
    setTimeout(() => n.remove(), 2600);
  };

  /* ------------------------------------------------------------ 会话 */
  /* 用户身份由服务端会话决定，前端不再传 user_id（传了服务端也会忽略）。
     这里的 username/role 只用于界面展示与显隐管理入口。 */
  CPA.username = function () { return document.body.dataset.username || ""; };
  CPA.role = function () { return document.body.dataset.role || ""; };
  CPA.isAdmin = function () { return CPA.role() === "admin"; };
  /* 会话号按账号隔离，换账号登录不会串到上一个人的对话上 */
  CPA.sessionId = function () {
    const key = "cpa_session_id:" + (CPA.username() || "anon");
    let sid = localStorage.getItem(key);
    if (!sid) {
      sid = "s" + Math.random().toString(36).slice(2, 12);
      localStorage.setItem(key, sid);
    }
    return sid;
  };
  /* 跨页面传递"当前要做的一组题" */
  CPA.setQueue = function (payload) {
    sessionStorage.setItem("cpa_queue", JSON.stringify(payload || {}));
  };
  CPA.takeQueue = function () {
    const raw = sessionStorage.getItem("cpa_queue");
    if (!raw) return null;
    sessionStorage.removeItem("cpa_queue");
    try { return JSON.parse(raw); } catch (e) { return null; }
  };
})();
