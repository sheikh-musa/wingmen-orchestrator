// lanes.js — mobile-first Lane manager (op#10706 B). Read-only ground-truth view:
// every body's PROCESS-VERIFIED token + model (incl. the remote VPS hub via SSH,
// op#10706 C) from /api/token-truth. Never shows a declared/guessed value — a body
// the server couldn't verify shows UNVERIFIED. Vanilla JS, no deps. Bearer auth
// from localStorage (same as fleet.js). R2/R3 select/apply controls land here later.
(function () {
  "use strict";
  var token = localStorage.getItem("console_token") || "";
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function shortModel(m) { return m ? String(m).replace(/^claude-/, "") : ""; }

  function rowHtml(r) {
    var cls, badge, bcls, acct;
    if (r.metered) { cls = "bad"; badge = "METERED"; bcls = "bad"; acct = r.account || "metered (API)"; }
    else if (!r.verified) { cls = "unver"; badge = "UNVERIFIED"; bcls = "unver"; acct = "unverified"; }
    else if (r.mismatch) { cls = "bad"; badge = "OFF-ACCOUNT"; bcls = "bad"; acct = r.account; }
    else { cls = "ok"; badge = "VERIFIED"; bcls = "ok"; acct = r.account; }

    var exp = (r.mismatch && r.expected)
      ? '<span class="exp">expected ' + esc(r.expected) + '</span>' : "";
    var model = r.model
      ? '<span class="model">' + esc(shortModel(r.model)) + '</span>'
      : '<span class="model none">model: default</span>';
    var host = r.host ? '<span class="kv">host <b>' + esc(r.host) + '</b></span>' : "";
    var fp = r.fp ? '<span class="fp">' + esc(r.fp) + '</span>' : "";

    return '<div class="row ' + cls + '">' +
        '<div class="r1">' +
          '<span class="who">' + esc(r.session) + '</span>' +
          '<span class="badge ' + bcls + '">' + badge + '</span>' +
        '</div>' +
        '<div class="r2">' +
          '<span class="acct">' + esc(acct) + '</span>' + exp +
          model + host + fp +
        '</div>' +
      '</div>';
  }

  function render(d) {
    var rows = (d && d.rows) || [];
    var s = (d && d.summary) || {};
    var box = $("rows"), sub = $("sub");
    if (!rows.length) {
      box.innerHTML = '<div class="empty">No bodies found.</div>';
    } else {
      box.innerHTML = rows.map(rowHtml).join("");
    }
    var bits = [];
    if (s.mismatched) bits.push('<b class="bad">' + s.mismatched + ' off-account</b>');
    if (s.metered) bits.push('<b class="bad">' + s.metered + ' metered</b>');
    if (s.unverified) bits.push(s.unverified + ' unverified');
    bits.push('<b class="good">' + (s.verified || 0) + '/' + (s.total || rows.length) + ' verified</b>');
    var bm = s.by_model || {};
    Object.keys(bm).forEach(function (k) { bits.push(bm[k] + '× ' + shortModel(k)); });
    sub.innerHTML = bits.join(" · ");
  }

  var FETCH_TIMEOUT_MS = 20000;  // the remote-hub SSH scan can take a few seconds
  function load() {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT_MS);
    $("refresh").disabled = true;
    return fetch("/api/token-truth", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) {
        if (r.status === 401) throw new Error("unauthorized");
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (d) {
        render(d);
        $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
      })
      .catch(function (e) {
        var msg = (e && e.message === "unauthorized")
          ? "Unauthorized — set a breakglass token in localStorage('console_token')."
          : "Could not load (" + (e && e.message) + "). Tap ↻ to retry.";
        $("sub").innerHTML = '<b class="bad">' + esc(msg) + '</b>';
      })
      .finally(function () {
        clearTimeout(timer);
        $("refresh").disabled = false;
      });
  }

  $("refresh").addEventListener("click", load);
  load();
  // Gentle auto-refresh; the server caches the SSH scan (~45s) so this is cheap.
  setInterval(load, 30000);
})();
