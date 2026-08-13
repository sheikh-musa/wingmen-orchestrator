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

  // Collapsed-by-default row: a status spine + one-line identity, tap to open the
  // drawer (token/model controls). Attention rows (metered / unverified /
  // off-account) render OPEN so the action needing you is already visible.
  // The control markup + every data-* attribute is unchanged, so the #rows event
  // delegation (change -> setPointer, click -> preview/armed-apply) still binds.
  function rowHtml(r) {
    var cls, badge, acct;
    if (r.metered) { cls = "flag"; badge = "METERED"; acct = r.account || "metered (API)"; }
    else if (!r.verified) { cls = "unver"; badge = "UNVERIFIED"; acct = "unverified"; }
    else if (r.mismatch) { cls = "flag"; badge = "OFF-ACCOUNT"; acct = r.account; }
    else { cls = "ok"; badge = "VERIFIED"; acct = r.account; }
    if (r.remote) cls += " remote";
    var attention = r.metered || !r.verified || r.mismatch;

    var s = esc(r.session);
    // one-line technical sub (mono): host · model · fingerprint
    var subBits = [];
    if (r.host) subBits.push("host " + esc(r.host));
    subBits.push(r.model ? esc(shortModel(r.model)) : "model: default");
    if (r.fp) subBits.push(esc(r.fp));
    var sub2 = subBits.join(" · ");
    var expLine = (r.mismatch && r.expected)
      ? '<div class="exp">expected ' + esc(r.expected) + '</div>' : "";
    var roleTag = r.remote ? ' <span class="role">remote</span>' : "";

    // Controls (R2b) — UNCHANGED logic + data-attributes. A select shows ONLY
    // where a local pointer write takes effect; otherwise a NOTE, never a silent
    // no-op. A remote (VPS) body is set on its own host; some are env-driven.
    var ctrls = "";
    if (r.remote) {
      ctrls = '<div class="ctlnote">remote (VPS) — set its token/model on the hub host; cross-host apply lands in R3/R4</div>';
    } else {
      if (r.token_settable) {
        // GAP-B: a lane governed by a per-GROUP pin shows its family tier
        // ("Token · irsyad group") instead of the fleet-wide "Token · all lanes".
        var tlabel = r.token_group
          ? ("Token · " + esc(r.token_group) + " group")
          : (r.token_pointer === ".lane_default_token") ? "Token · all lanes" : "Token";
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
      // PREVIEW = R3 dry-run (safe). APPLY = R4 armed relaunch — OPERATOR-driven;
      // 503s while disabled, 403s until armed via Telegram; taps ask for a typed
      // body-name confirm. No agent drives it.
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

    return '<div class="lane ' + cls + (attention ? " open" : "") + '">' +
        '<span class="spine"></span>' +
        '<div class="rowtop">' +
          '<span class="stdot"></span>' +
          '<div class="idwrap">' +
            '<div class="id">' + s + roleTag + '</div>' +
            '<div class="sub2">' + sub2 + '</div>' +
          '</div>' +
          '<span class="acct">' + esc(acct) + '</span>' +
          '<span class="chev">&#8250;</span>' +
        '</div>' +
        '<div class="drawer">' + expLine +
          '<div class="controls">' + ctrls + '</div>' +
        '</div>' +
      '</div>';
  }

  // Queue-on-busy applies (op#10861): show queued (waiting for idle) + held
  // (arm expired while waiting) items, each cancellable.
  function renderQueue(items) {
    var q = $("queue");
    if (!q) return;
    items = items || [];
    q.innerHTML = items.map(function (it) {
      var held = it.status !== "queued";
      var label = (it.status === "queued") ? "queued" : it.status;
      return '<div class="qitem' + (held ? " held" : "") + '">' +
        '<span class="qwho">' + esc(it.session) + ' · ' + esc(it.kind) + '</span>' +
        '<span>' + esc(label) + '</span>' +
        '<button class="qcancel" data-qcancel="' + esc(it.session) + '" data-qkind="' + esc(it.kind) + '">Cancel</button>' +
        '<span class="qnote">' + esc(it.note || "") + '</span>' +
      '</div>';
    }).join("");
  }

  // ── Fleet-level bulk account switch (switch-group / switch-all) ─────────────
  // ONE toolbar above the body list: pick a TARGET account, then either switch a
  // whole family (the live session prefix up to its first "-") or the WHOLE fleet.
  // Every fire is DRY-RUN previewed FIRST (the safety surface) then EXPLICITLY
  // confirmed — NEVER a one-tap bulk switch. Ported verbatim from fleet.js's
  // renderFleetSwitch/fs* block, adapted to lanes.js's data shapes: the account
  // list comes from the /api/token-truth registry (registry.tokens), NOT a fleet.js
  // SWITCH_ACCOUNTS constant, and families are built from row.session (fleet.js used
  // lane.tmux_session). The held lane `irsyad-import` is pre-excluded for switch-ALL;
  // the operator can also DESELECT any would-switch lane before the real fire.
  var FS_HELD = ["irsyad-import"];   // held lanes: pre-excluded from switch-ALL
  var fsFamKey = null;               // last-rendered family set (skip needless rebuilds)
  var fsPlan = null;                 // active dry-run context: {kind, token, family}
  var fsAccounts = [];               // [{name,label}] built from registry.tokens

  // Selectable bulk-switch accounts = the AVAILABLE registry tokens the backend
  // enforces, minus the forbidden gazzabyte consumer token (CAI-729; the endpoint
  // also refuses it). No fleet.js-style {fp} needed — the endpoints take token_name.
  function fsBuildAccounts(reg) {
    var toks = (reg && reg.tokens) || [];
    return toks.filter(function (t) {
      return t && t.name && t.available !== false && !/gazza/i.test(t.name);
    }).map(function (t) { return { name: t.name, label: t.name }; });
  }
  function fsAcctLabel(name) {
    for (var i = 0; i < fsAccounts.length; i++)
      if (fsAccounts[i].name === name) return fsAccounts[i].label;
    return name || "";
  }
  // Families = unique session prefix (up to first "-") of the LOCAL bodies (remote
  // VPS bodies aren't local tmux switch targets), e.g. "cosem-exams" -> "cosem".
  // Families WITH lane counts. op#12490: the picker listed every family flat, but
  // most families are a single lane (caai, cai, finance…) so it read as "every
  // lane" and the ACTUAL multi-lane groups (irsyad/cosem/ihsanos) were lost. Now
  // each carries {name,count}; real groups (>1) sort first.
  function familiesFromRows(rows) {
    var counts = {};
    (rows || []).forEach(function (r) {
      if (!r || r.remote) return;             // remote (VPS) bodies are set on their own host
      var s = r.session;
      if (!s) return;
      var fam = String(s).split("-")[0];
      if (fam) counts[fam] = (counts[fam] || 0) + 1;
    });
    var out = Object.keys(counts).map(function (f) { return { name: f, count: counts[f] }; });
    out.sort(function (a, b) {                 // groups (>1 lane) first, then A-Z within each block
      var ag = a.count > 1, bg = b.count > 1;
      if (ag !== bg) return ag ? -1 : 1;
      return a.name < b.name ? -1 : (a.name > b.name ? 1 : 0);
    });
    return out;
  }
  // Build the family <select> body: real GROUPS in their own optgroup (labelled
  // with the lane count, e.g. "irsyad · 5"), single lanes below. `cur` re-selects.
  function famOptionsHtml(fams, cur) {
    function opts(arr) {
      return arr.map(function (f) {
        return '<option value="' + esc(f.name) + '"' + (f.name === cur ? " selected" : "") +
               '>' + esc(f.name) + " · " + f.count + "</option>";
      }).join("");
    }
    var groups = fams.filter(function (f) { return f.count > 1; });
    var singles = fams.filter(function (f) { return f.count === 1; });
    var h = "";
    if (groups.length) h += '<optgroup label="Groups (multi-lane)">' + opts(groups) + "</optgroup>";
    if (singles.length) h += '<optgroup label="Single lanes">' + opts(singles) + "</optgroup>";
    return h;
  }
  function renderFleetSwitch(rows, reg) {
    var el = $("fleetSwitch");
    if (!el) return;
    fsAccounts = fsBuildAccounts(reg);
    var fams = familiesFromRows(rows);
    var key = fams.map(function (f) { return f.name + ":" + f.count; }).join(",");
    if (el._built) {
      // On a live refresh: update the family list ONLY when it actually changed,
      // and NEVER clobber a plan the operator is mid-read on (a background tick
      // must not wipe his dry-run preview).
      if (key !== fsFamKey && !fsPlan) {
        var famSel = el.querySelector(".fs-fam");
        if (famSel && document.activeElement !== famSel) {
          var cur = famSel.value;
          famSel.innerHTML = famOptionsHtml(fams, cur);
          fsFamKey = key;
        }
      }
      return;
    }
    // Don't lock in an EMPTY account select on an early/degraded first render — wait
    // for a registry with at least one selectable token, then build once (fleet.js's
    // SWITCH_ACCOUNTS was a constant so it never faced this; token-truth is fetched).
    if (!fsAccounts.length) return;
    el._built = true; fsFamKey = key;
    var acctOpts = fsAccounts.map(function (a) {
      return '<option value="' + esc(a.name) + '">' + esc(a.label) + '</option>';
    }).join("");
    var famOpts = famOptionsHtml(fams, null);
    el.innerHTML =
      '<div class="fs-row">' +
        '<select class="fs-sel fs-acct" title="Target Claude account for the bulk switch">' + acctOpts + '</select>' +
        '<select class="fs-sel fs-fam" title="Family = the live session prefix up to its first dash">' + famOpts + '</select>' +
        '<button class="fs-btn fs-group" title="Preview then switch every lane in the selected family onto the target account">⇄ switch group</button>' +
        '<button class="fs-btn fs-all" title="Preview then switch the WHOLE fleet onto the target account (held lanes pre-excluded)">⇄ switch ALL</button>' +
      '</div>' +
      '<div class="fs-result" id="fsResult"></div>';
    var g = el.querySelector(".fs-group"), a = el.querySelector(".fs-all");
    if (g) g.addEventListener("click", function () { fsDryRun("group"); });
    if (a) a.addEventListener("click", function () { fsDryRun("all"); });
  }

  function fsPost(url, body) {
    return fetch(url, {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(
        function (j) { return { ok: r.ok, s: r.status, j: j || {} }; },
        function () { return { ok: r.ok, s: r.status, j: {} }; }   // non-JSON body
      );
    });
  }
  // A non-200 / non-JSON failure worded like the per-lane switch error handling.
  function fsErrText(res) {
    if (res.s === 400) return "✗ " + (res.j.error || "unknown token / bad request");
    if (res.s === 401) return "✗ not authorized";
    if (res.s === 429) return "⏳ rate-limited — wait a moment";
    if (res.s === 500) return "✗ " + (res.j.error || "enumerate failed on the server");
    if (res.s === 504) return "✗ timed out — check the lanes";
    return "✗ " + (res.j.error || ("failed (http " + res.s + ")"));
  }

  function fsDryRun(kind) {
    var el = $("fleetSwitch"), out = $("fsResult");
    if (!el || !out) return;
    var token = el.querySelector(".fs-acct").value;
    var family = el.querySelector(".fs-fam").value;
    if (kind === "group" && !family) { out.innerHTML = '<span class="fs-err">No family selected.</span>'; return; }
    fsPlan = null;   // clear any prior plan while this dry-run is in flight
    out.innerHTML = '<span class="fs-skip">Previewing…</span>';
    var url = kind === "group" ? "/api/switch-group" : "/api/switch-all";
    var body = kind === "group"
      ? { family: family, token_name: token, dry_run: true }
      : { token_name: token, dry_run: true, exclude: FS_HELD.slice() };
    fsPost(url, body).then(function (res) {
      if (!(res.ok && res.j && res.j.dry_run)) {
        out.innerHTML = '<span class="fs-err">' + esc(fsErrText(res)) + '</span>'; return;
      }
      fsPlan = { kind: kind, token: token, family: family };
      renderPlan(out, kind, token, res.j);
    }).catch(function () {
      // A dropped dry-run RESPONSE is safe (a dry-run changes nothing) — just retry.
      out.innerHTML = '<span class="fs-err">✗ network dropped during preview — tap again</span>';
    });
  }

  // The DRY-RUN plan: summary + the lanes that WOULD switch (by name), skipped
  // breakdown, excluded lanes shown muted (proves held/SELF are protected). For
  // switch-ALL each would-switch lane is a checkbox (checked) so the operator can
  // deselect before firing; switch-group has no exclude on the endpoint so its
  // list is read-only. Nothing fires until the explicit Confirm button.
  function renderPlan(out, kind, token, j) {
    var targets = j.targets || [], sum = j.summary || {};
    var would = targets.filter(function (t) { return t.action === "switch"; });
    var excluded = targets.filter(function (t) { return t.action === "skipped:excluded"; });
    var already = targets.filter(function (t) { return t.action === "skipped:already"; }).length;
    var busy = targets.filter(function (t) { return t.action === "skipped:busy"; }).length;
    var lbl = esc(fsAcctLabel(token));
    var skippedTotal = (sum.skipped != null) ? sum.skipped : (targets.length - would.length);
    var head = '<div class="fs-sum">Would switch <b>' + would.length + '</b> lane' + (would.length === 1 ? '' : 's') +
      ' to ' + lbl + ' — skipped ' + skippedTotal +
      ' (already ' + already + ' / busy ' + busy + ' / excluded ' + excluded.length + ')</div>';
    var lanesHtml;
    if (!would.length) {
      lanesHtml = '<div class="fs-skip">Nothing to switch.</div>';
    } else if (kind === "all") {
      lanesHtml = '<div class="fs-lanes">' + would.map(function (t) {
        return '<label class="fs-lane"><input type="checkbox" class="fs-ck" value="' + esc(t.lane) + '" checked> ' + esc(t.lane) + '</label>';
      }).join("") + '</div>';
    } else {
      lanesHtml = '<div class="fs-lanes">' + would.map(function (t) {
        return '<div class="fs-lane">' + esc(t.lane) + '</div>';
      }).join("") + '</div>';
    }
    var exclHtml = excluded.length
      ? '<div class="fs-excluded">excluded: ' + excluded.map(function (t) {
          return esc(t.lane) + (t.detail ? " (" + esc(t.detail) + ")" : "");
        }).join(", ") + '</div>'
      : '';
    var confirmHtml = would.length
      ? '<button class="fs-btn fs-confirm" id="fsConfirm">Confirm switch ' + would.length + ' lane' + (would.length === 1 ? '' : 's') + '</button>'
      : '';
    out.innerHTML = head + lanesHtml + exclHtml + confirmHtml;
    var cb = $("fsConfirm");
    if (cb) cb.addEventListener("click", function () { fsFire(out); });
  }

  function fsFire(out) {
    if (!fsPlan) return;
    var kind = fsPlan.kind, token = fsPlan.token, family = fsPlan.family;
    var cb = $("fsConfirm");
    if (cb) { cb.disabled = true; cb.textContent = "⇄ switching…"; }
    var url, body;
    if (kind === "group") {
      url = "/api/switch-group";
      body = { family: family, token_name: token, dry_run: false };
    } else {
      // exclude = held lanes + any lane the operator unchecked in the plan.
      var excl = FS_HELD.slice();
      out.querySelectorAll(".fs-ck").forEach(function (ck) {
        if (!ck.checked && excl.indexOf(ck.value) < 0) excl.push(ck.value);
      });
      url = "/api/switch-all";
      body = { token_name: token, dry_run: false, exclude: excl };
    }
    fsPost(url, body).then(function (res) {
      fsPlan = null;
      if (res.j && res.j.targets && res.j.targets.length) { renderResults(out, token, res.j); }
      else { out.innerHTML = '<span class="fs-err">' + esc(fsErrText(res)) + '</span>'; }
    }).catch(function () {
      // A dropped RESPONSE on the REAL fire does NOT mean nothing ran — the bulk
      // kill+relaunch may have fired before the reply was lost.
      fsPlan = null;
      out.innerHTML = '<span class="fs-err">✗ network dropped — may have run; check the lanes</span>';
    });
  }

  // Itemized results: per-lane action (switch / skipped:* / failed) + detail, with
  // FAIL-LOUD red on any "failed", plus the summary counts. ok=false (any failure)
  // still renders the full per-lane breakdown so nothing is hidden.
  function renderResults(out, token, j) {
    var targets = j.targets || [], sum = j.summary || {};
    var lbl = esc(fsAcctLabel(token));
    var anyFail = (sum.failed || 0) > 0 || j.ok === false;
    var head = '<div class="fs-sum">' + (anyFail ? '⚠ ' : '✓ ') +
      'switched ' + (sum.switched || 0) + ' · skipped ' + (sum.skipped || 0) +
      ' · failed ' + (sum.failed || 0) + ' → ' + lbl + '</div>';
    var items = targets.map(function (t) {
      var a = t.action || "";
      var cls = a === "switch" ? "ok" : (a === "failed" ? "fail" : "skip");
      return '<div class="fs-item"><span class="fs-lname">' + esc(t.lane) + '</span>' +
        '<span class="fs-act ' + cls + '">' + esc(a) + '</span>' +
        (t.detail ? '<span class="fs-detail">' + esc(t.detail) + '</span>' : '') +
      '</div>';
    }).join("");
    out.innerHTML = head + items;
  }

  // ── Group the main lane list (op#12600) ─────────────────────────────────────
  // Family = the live session prefix up to its first "-" (same convention as the
  // switch-group picker's familiesFromRows), so the display groups the way the
  // operator already switches: irsyad/coord/prog1/prog2/import -> "irsyad".
  function famKey(r) { return String((r && r.session) || "").split("-")[0] || "?"; }

  // The GROUP's current token, shown at the group level. Consensus across the
  // family's VERIFIED lanes: one shared account -> that name (e.g. "musa2"); a
  // split -> "mixed (…)" SURFACED with the per-account counts, never hidden
  // (op#12600 — the operator explicitly asked that ambiguity be visible, e.g. the
  // irsyad group with import still on syed reads "mixed (musa2·4, syed·1)").
  function groupToken(rows) {
    var accts = {};
    (rows || []).forEach(function (r) {
      if (!r || r.remote) return;                 // remote (VPS) bodies are set on their own host
      if (!r.verified || r.metered) return;       // only a verified lane has a trustworthy account
      var a = r.account || "";
      if (a) accts[a] = (accts[a] || 0) + 1;
    });
    var names = Object.keys(accts);
    if (!names.length) return { label: "", mixed: false };
    if (names.length === 1) return { label: names[0], mixed: false };
    names.sort(function (a, b) { return accts[b] - accts[a]; });   // dominant account first
    return { label: "mixed (" + names.map(function (n) { return n + "·" + accts[n]; }).join(", ") + ")", mixed: true };
  }

  function render(d) {
    registry = (d && d.registry) || { tokens: [], models: [] };
    var rows = (d && d.rows) || [];
    var s = (d && d.summary) || {};
    renderQueue(d && d.apply_queue);
    // Attention-first: metered / unverified / off-account pinned to the top under
    // "Needs attention"; healthy lanes collapse under "All lanes".
    function isAttn(r) { return r.metered || !r.verified || r.mismatch; }
    var attn = rows.filter(isAttn);
    var rest = rows.filter(function (r) { return !isAttn(r); });
    var html = "";
    // Attention-first is preserved: metered / unverified / off-account lanes stay
    // pinned FLAT at the top so a flagged lane never hides inside a group block.
    if (attn.length) html += '<div class="pin attn">Needs attention</div>' + attn.map(rowHtml).join("");
    // The rest of the fleet, organized BY GROUP (op#12600) mirroring the switch
    // picker: each multi-lane group heads its own block showing the group's token
    // (mixed surfaced); single lanes bucket under one heading (each row already
    // shows its own account in-line).
    if (rest.length) {
      var byFam = {};
      rest.forEach(function (r) { var k = famKey(r); (byFam[k] = byFam[k] || []).push(r); });
      var keys = Object.keys(byFam);
      var groupKeys = keys.filter(function (k) { return byFam[k].length > 1; }).sort();
      var singleKeys = keys.filter(function (k) { return byFam[k].length === 1; }).sort();
      groupKeys.forEach(function (k) {
        var gt = groupToken(byFam[k]);
        var badge = gt.label
          ? '<span class="grptok' + (gt.mixed ? " mixed" : "") + '">' + esc(gt.label) + '</span>' : '';
        html += '<div class="pin grp"><span>' + esc(k) + ' · ' + byFam[k].length + '</span>' + badge + '</div>' +
                byFam[k].map(rowHtml).join("");
      });
      if (singleKeys.length) {
        html += '<div class="pin">Single lanes · ' + singleKeys.length + '</div>' +
                singleKeys.map(function (k) { return rowHtml(byFam[k][0]); }).join("");
      }
    }
    $("rows").innerHTML = rows.length ? html : '<div class="empty">No bodies found.</div>';
    var bits = ['<span><b class="good">' + (s.verified || 0) + '/' + (s.total || rows.length) + '</b> verified</span>'];
    if (s.mismatched) bits.push('<span><b class="bad">' + s.mismatched + '</b> off-account</span>');
    if (s.metered) bits.push('<span><b class="bad">' + s.metered + '</b> metered</span>');
    if (s.unverified) bits.push('<span>' + s.unverified + ' unverified</span>');
    $("sub").innerHTML = bits.join("");
    renderFleetSwitch(rows, registry);   // fleet-level bulk switch toolbar (built once, above the list)
  }

  // Tap a collapsed row's header to open/close its control drawer. The drawer's
  // selects + buttons are NOT inside .rowtop, so this never fires on a control tap;
  // their own delegated handlers (change/click below) are unaffected.
  $("rows").addEventListener("click", function (e) {
    var rt = e.target.closest ? e.target.closest(".rowtop") : null;
    if (!rt) return;
    var lane = rt.parentNode;
    if (lane && lane.classList && lane.classList.contains("lane")) lane.classList.toggle("open");
  });

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
        else if (res.status === 202) { toast(kind + " QUEUED for " + session + " — fires when idle (arm re-checked)"); }
        else if (res.status === 409 || res.status === 429) { toast((res.j && res.j.error) || "busy — try again shortly", true); }
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

  // Cancel a queued-on-busy apply (op#10861).
  $("queue").addEventListener("click", function (e) {
    var el = e.target;
    if (!el || !el.dataset || !el.dataset.qcancel) return;
    if (busy) return;
    busy = true;
    fetch("/api/apply-queue-cancel", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ session: el.dataset.qcancel, kind: el.dataset.qkind }),
    }).then(function (r) { return r.json(); })
      .then(function (j) { toast(j.cancelled ? "cancelled" : "not found"); })
      .catch(function (e2) { toast("error: " + (e2 && e2.message), true); })
      .finally(function () { busy = false; load(); });
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
