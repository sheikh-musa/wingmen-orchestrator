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

    // Controls (R2b). A select is shown ONLY where a local pointer write actually
    // takes effect (fix (a)) — otherwise a NOTE, never a silent no-op. A remote
    // (VPS) body is set on its own host; some bodies' token/model are env-driven.
    var s = esc(r.session);
    var ctrls = "";
    if (r.remote) {
      ctrls = '<div class="ctlnote">remote (VPS) — set its token/model on the hub host; cross-host apply lands in R3/R4</div>';
    } else {
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
      if (r.model_settable) {
        var mopts = '<option value="">— default —</option>' + registry.models.map(function (m) {
          var sel = (r.model_pointer === m) ? " selected" : "";
          return '<option value="' + esc(m) + '"' + sel + '>' + esc(shortModel(m)) + '</option>';
        }).join("");
        ctrls += '<label class="ctl"><span>Model</span>' +
          '<select data-kind="model" data-session="' + s + '">' + mopts + '</select></label>';
      } else {
        ctrls += '<div class="ctlnote">model: env-driven at boot (not pointer-settable)</div>';
      }
      // Apply buttons. PREVIEW = R3 dry-run (safe, shows the plan). APPLY = R4
      // armed relaunch, which the OPERATOR drives — it 503s while disabled and 403s
      // until the operator arms via the Telegram bridge; a tap asks the operator to
      // type the body name to confirm. No agent ever drives it.
      var ab = "";
      if (r.token_settable) {
        ab += '<button class="prevbtn" data-apply="token" data-session="' + s + '">Preview token</button>';
        ab += '<button class="armbtn" data-armapply="token" data-session="' + s + '">Apply token</button>';
      }
      if (r.model_settable) {
        ab += '<button class="prevbtn" data-apply="model" data-session="' + s + '">Preview model</button>';
        ab += '<button class="armbtn" data-armapply="model" data-session="' + s + '">Apply model</button>';
      }
      if (ab) ctrls += '<div class="applyrow">' + ab + '</div>';
    }

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

  // Add-token: register a new key file. The raw token is POSTed once and cleared
  // from the field immediately; the server returns ONLY the fingerprint to show.
  function addToken() {
    if (busy) return;
    var name = ($("tokName").value || "").trim().toLowerCase();
    var tok = ($("tokVal").value || "").trim();
    if (!name || !tok) { toast("name + token required", true); return; }
    busy = true; $("tokAdd").disabled = true;
    fetch("/api/add-token", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ name: name, token: tok }),
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        $("tokVal").value = "";  // never retain the raw token in the field
        if (!res.ok) { toast((res.j && res.j.error) || "add failed", true); }
        else {
          $("tokName").value = ""; $("addtok").open = false;
          toast("added " + res.j.name + " · fp " + res.j.fp);
        }
      })
      .catch(function (e) { toast("error: " + (e && e.message), true); })
      .finally(function () { busy = false; $("tokAdd").disabled = false; load(); });
  }
  $("tokAdd").addEventListener("click", addToken);

  // Event delegation: any select change writes its pointer.
  $("rows").addEventListener("change", function (e) {
    var el = e.target;
    if (el && el.tagName === "SELECT" && el.dataset.kind) {
      setPointer(el.dataset.session, el.dataset.kind, el.value);
    }
  });

  // Apply PREVIEW (R3 dry-run): fetch the plan + show it in a modal. No relaunch.
  function previewApply(session, kind) {
    if (busy) return;
    busy = true;
    fetch("/api/apply-dry-run", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ session: session, kind: kind }),
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) { toast((res.j && res.j.error) || "preview failed", true); }
        else {
          $("pvTitle").textContent = session + " · " + kind + " apply";
          $("pvText").textContent = res.j.preview || "(no output)";
          $("previewModal").classList.add("show");
        }
      })
      .catch(function (e) { toast("error: " + (e && e.message), true); })
      .finally(function () { busy = false; });
  }
  // R4 ARMED apply — OPERATOR-DRIVEN. Typed body-name confirm, then POST. The
  // endpoint 503s while disabled and 403s until the operator arms via Telegram;
  // the toast relays that. No agent path drives this.
  function applyArmed(session, kind) {
    if (busy) return;
    var typed = window.prompt("ARMED " + kind + " apply for '" + session +
      "'.\nThis RELAUNCHES the body (reversible).\nType the exact body name to confirm:");
    if (typed == null) return;                 // operator cancelled
    if (typed.trim() !== session) { toast("name did not match — not applied", true); return; }
    busy = true;
    fetch("/api/apply-armed", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ session: session, kind: kind, confirm: typed.trim() }),
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (res.status === 503) { toast("Armed apply is DISABLED (pending cai + Nazim)", true); }
        else if (res.status === 403) { toast((res.j && res.j.error) || "arm via Telegram first", true); }
        else if (!res.ok) { toast((res.j && res.j.error) || "apply failed", true); }
        else { toast(kind + " applied to " + session + (res.j && res.j.ok ? "" : " (check output)")); }
      })
      .catch(function (e) { toast("error: " + (e && e.message), true); })
      .finally(function () { busy = false; load(); });
  }
  $("rows").addEventListener("click", function (e) {
    var el = e.target; if (!el || !el.dataset) return;
    if (el.dataset.apply) previewApply(el.dataset.session, el.dataset.apply);
    else if (el.dataset.armapply) applyArmed(el.dataset.session, el.dataset.armapply);
  });
  $("pvClose").addEventListener("click", function () { $("previewModal").classList.remove("show"); });

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
