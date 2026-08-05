// lanes.js — mobile-first Lane manager (op#10706 B + R2b). READ: every body's
// PROCESS-VERIFIED token + model (incl. the remote VPS hub via SSH) from
// /api/token-truth — never a declared/guessed value. WRITE (R2b): SELECT a token
// + model DEFAULT per body -> POST /api/set-pointer (writes the pointer file only;
// REVERSIBLE, NO relaunch, NO live billing change — applies on next relaunch).
// gazzabyte is never selectable; model is allowlist-validated server-side.
// Vanilla JS, bearer auth from localStorage (same as fleet.js).
(function () {
  "use strict";
  var token = localStorage.getItem("console_token") || "";
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function shortModel(m) { return m ? String(m).replace(/^claude-/, "") : ""; }

  var registry = { tokens: [], models: [] };
  var busy = false;

  // Stacked layout — each fact on its OWN wrapped line so nothing clips at 390px.
  function rowHtml(r) {
    var cls, badge, bcls, acct;
    if (r.metered) { cls = "bad"; badge = "METERED"; bcls = "bad"; acct = r.account || "metered (API)"; }
    else if (!r.verified) { cls = "unver"; badge = "UNVERIFIED"; bcls = "unver"; acct = "unverified"; }
    else if (r.mismatch) { cls = "bad"; badge = "OFF-ACCOUNT"; bcls = "bad"; acct = r.account; }
    else { cls = "ok"; badge = "VERIFIED"; bcls = "ok"; acct = r.account; }

    var expLine = (r.mismatch && r.expected)
      ? '<div class="line exp">expected ' + esc(r.expected) + '</div>' : "";
    var model = r.model
      ? '<span class="model">' + esc(shortModel(r.model)) + '</span>'
      : '<span class="model none">model: default</span>';
    var host = r.host ? '<span class="kv">host <b>' + esc(r.host) + '</b></span>' : "";
    var fp = r.fp ? '<span class="fp">' + esc(r.fp) + '</span>' : "";

    // Controls (R2b). Token: registry accounts; disabled + noted when the body
    // boots off .env. Lanes share one pointer, so label it "all lanes".
    var s = esc(r.session);
    var ctrls = "";
    if (r.token_settable) {
      var tlabel = (r.token_pointer === ".lane_default_token") ? "Token · all lanes" : "Token";
      var topts = '<option value="">— default —</option>' + registry.tokens.map(function (t) {
        var sel = (r.token_pointer_name === t.name) ? " selected" : "";
        var dis = t.available ? "" : " disabled";
        return '<option value="' + esc(t.name) + '"' + sel + dis + '>' + esc(t.name) + (t.fp ? " (" + esc(t.fp) + ")" : "") + '</option>';
      }).join("");
      ctrls += '<label class="ctl"><span>' + tlabel + '</span>' +
        '<select data-kind="token" data-session="' + s + '">' + topts + '</select></label>';
    } else {
      ctrls += '<div class="ctlnote">token: .env default (not pointer-settable)</div>';
    }
    var mopts = '<option value="">— default —</option>' + registry.models.map(function (m) {
      var sel = (r.model_pointer === m) ? " selected" : "";
      return '<option value="' + esc(m) + '"' + sel + '>' + esc(shortModel(m)) + '</option>';
    }).join("");
    ctrls += '<label class="ctl"><span>Model</span>' +
      '<select data-kind="model" data-session="' + s + '">' + mopts + '</select></label>';

    return '<div class="row ' + cls + '">' +
        '<div class="r1"><span class="who">' + s + '</span>' +
          '<span class="badge ' + bcls + '">' + badge + '</span></div>' +
        '<div class="line"><span class="acct">' + esc(acct) + '</span></div>' +
        expLine +
        '<div class="line meta">' + model + host + fp + '</div>' +
        '<div class="controls">' + ctrls + '</div>' +
      '</div>';
  }

  function render(d) {
    registry = (d && d.registry) || { tokens: [], models: [] };
    var rows = (d && d.rows) || [];
    var s = (d && d.summary) || {};
    $("rows").innerHTML = rows.length ? rows.map(rowHtml).join("") : '<div class="empty">No bodies found.</div>';
    var bits = [];
    if (s.mismatched) bits.push('<b class="bad">' + s.mismatched + ' off-account</b>');
    if (s.metered) bits.push('<b class="bad">' + s.metered + ' metered</b>');
    if (s.unverified) bits.push(s.unverified + ' unverified');
    bits.push('<b class="good">' + (s.verified || 0) + '/' + (s.total || rows.length) + ' verified</b>');
    $("sub").innerHTML = bits.join(" · ");
  }

  function toast(msg, bad) {
    var t = $("toast");
    t.textContent = msg; t.className = "toast show" + (bad ? " bad" : "");
    setTimeout(function () { t.className = "toast"; }, 3200);
  }

  function setPointer(session, kind, value) {
    if (busy) return;
    busy = true;
    var payload = value ? { kind: kind, session: session, value: value }
                        : { kind: kind, session: session, clear: true };
    fetch("/api/set-pointer", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify(payload),
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { toast((res.j && res.j.error) || "failed", true); }
        else { toast(kind + " default set for " + session + " — applies on next relaunch"); }
      })
      .catch(function (e) { toast("error: " + (e && e.message), true); })
      .finally(function () { busy = false; load(); });
  }

  // Event delegation: any select change writes its pointer.
  $("rows").addEventListener("change", function (e) {
    var el = e.target;
    if (el && el.tagName === "SELECT" && el.dataset.kind) {
      setPointer(el.dataset.session, el.dataset.kind, el.value);
    }
  });

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
      .then(function (d) { render(d); $("stamp").textContent = "updated " + new Date().toLocaleTimeString(); })
      .catch(function (e) {
        var msg = (e && e.message === "unauthorized")
          ? "Unauthorized — set a breakglass token in localStorage('console_token')."
          : "Could not load (" + (e && e.message) + "). Tap ↻ to retry.";
        $("sub").innerHTML = '<b class="bad">' + esc(msg) + '</b>';
      })
      .finally(function () { clearTimeout(timer); $("refresh").disabled = false; });
  }

  $("refresh").addEventListener("click", load);
  load();
  setInterval(load, 30000);  // server caches the SSH scan (~45s), so cheap
})();
