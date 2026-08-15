// irsyad.js — dedicated operator IRSYAD page (op#12501). READ-ONLY: the irsyad
// lane-family (session/status/token/model/context %) + the musa2 weekly pool the
// family runs on, from /api/irsyad. No actions, no relaunch — mirrors the fleet
// view's derivation (live-pane bucket, process-verified token, context gauge).
// Vanilla JS, bearer auth from localStorage (same key as fleet.js / lanes.js).
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
  function fmtAge(s) {
    if (s == null) return "";
    s = Math.max(0, Math.round(s));
    if (s < 90) return s + "s";
    if (s < 5400) return Math.round(s / 60) + "m";
    if (s < 172800) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function fmtTok(n) {
    if (n == null) return "—";
    n = Number(n);
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "k";
    return String(n);
  }

  // fingerprint → friendly account name. Shares fleet.js's Musa/Syed mapping,
  // PLUS the musa2 fingerprint (e1dfa48eec85) — the account this whole family
  // runs on, which the fleet-wide token registry labels "Max (unknown acct)"
  // (no named key file), so we name it here where the family is known.
  function acctForFp(fp) {
    fp = fp || "";
    if (fp.indexOf("e1dfa48eec85") === 0) return "musa2";
    if (fp.indexOf("68142948") === 0) return "Musa";
    if (fp.indexOf("582043088") === 0) return "Syed";
    return "";
  }
  // 🔑 token badge (drawer detail).
  function tokenBadge(fp) {
    var name = acctForFp(fp);
    var color = (fp || "").indexOf("582043088") === 0 ? "#fbbf24" : (name ? "#4ade80" : "#94a3b8");
    if (!fp) return "";
    return '<span title="' + esc(fp) + '" style="color:' + color + '">🔑 ' + esc(name || fp.slice(0, 8)) + '</span>';
  }
  // The account pill label: the process-verified account when it's a NAMED one,
  // else the fp-derived name (so a known-but-unregistered fp like musa2 reads as
  // "musa2", not "Max (unknown acct)"), else the raw value — never a guess.
  function acctLabel(r) {
    if (r.account && !/unknown/i.test(r.account)) return r.account;
    return acctForFp(r.auth_fp) || r.account || (r.verified ? "verified" : "unverified");
  }

  // Weekly Max-pool card for musa2 (same green/warn/bad thresholds as the fleet
  // header + the SRE's weekly monitor: <75 good, 75-90 warn, >=90 bad; a reading
  // older than STALE greys out rather than showing a frozen number).
  var POOL_STALE_S = 1800;
  function renderPool(p) {
    var el = $("pool");
    if (!el) return;
    if (!p || p.pct_7d == null) { el.innerHTML = ""; return; }
    var pct = Math.round(Number(p.pct_7d));
    var stale = (p.updated_age_s != null && p.updated_age_s > POOL_STALE_S);
    var cls = stale ? "stale" : (pct >= 90 ? "bad" : (pct >= 75 ? "warn" : "good"));
    var meta = [];
    if (p.pct_5h != null) meta.push('<span><b>5h</b> ' + Math.round(Number(p.pct_5h)) + '%</span>');
    if (p.resets_at) meta.push('<span><b>resets</b> ' + esc(String(p.resets_at).replace("+00:00", " UTC")) + '</span>');
    // Pace layer (op#12617) — advisory only, never a gate. pace > 1 = burning
    // faster than the weekly budget; projected_pct = where the week lands at pace.
    if (p.pace != null) {
      var pace = Number(p.pace);
      var hot = pace > 1;
      meta.push('<span class="' + (hot ? "warnpace" : "") + '"><b>pace</b> ' + pace.toFixed(2) + 'x</span>');
    }
    if (p.projected_pct != null)
      meta.push('<span><b>proj</b> ' + Math.round(Number(p.projected_pct)) + '%</span>');
    if (p.runway_days != null)
      meta.push('<span><b>runway</b> ' + Number(p.runway_days).toFixed(1) + 'd</span>');
    if (p.updated_age_s != null)
      meta.push('<span><b>read</b> ' + fmtAge(p.updated_age_s) + ' ago' + (stale ? ' ⚠' : '') + '</span>');
    el.innerHTML =
      '<div class="pool ' + cls + '">' +
        '<div class="phead">' +
          '<span class="pname">musa2 · weekly pool</span>' +
          '<span class="ppct">' + pct + '<span class="u">%</span></span>' +
        '</div>' +
        '<div class="pbar"><i style="width:' + Math.min(100, pct) + '%"></i></div>' +
        '<div class="pmeta">' + meta.join("") + '</div>' +
      '</div>';
  }

  // A lane row: collapsed spine (status colour) + identity + account badge +
  // context meter; tap opens a read-only detail drawer.
  function laneHtml(r) {
    var cls, badge;
    if (r.metered) { cls = "flag"; badge = "METERED"; }
    else if (!r.verified) { cls = "unver"; badge = "UNVERIFIED"; }
    else if (r.off_account) { cls = "flag"; badge = "OFF-ACCT"; }
    else if (r.flagged) { cls = "flag"; badge = "DARK"; }
    else if (r.bucket === "working") { cls = "ok"; badge = "working"; }
    else if (r.bucket === "offline") { cls = "unver"; badge = "offline"; }
    else { cls = "idle"; badge = "idle"; }

    var s = esc(r.session);
    var acct = acctLabel(r);

    // technical sub-line (mono): model · fingerprint · host
    var subBits = [];
    subBits.push(r.model ? esc(shortModel(r.model)) : "model: default");
    if (r.auth_fp) subBits.push(esc(r.auth_fp));
    if (r.host) subBits.push(esc(r.host));
    var sub2 = subBits.join(" · ");

    // context meter (of the model window; same green/amber/red as the fleet view)
    var ctxHtml;
    if (r.ctx_pct == null) {
      ctxHtml = '<div class="ctx"><span class="cnull">context —</span></div>';
    } else {
      var lvl = r.ctx_level === "red" ? "red" : (r.ctx_level === "amber" ? "amber" : "");
      ctxHtml = '<div class="ctx ' + lvl + '">' +
        '<span class="meter"><i style="width:' + Math.min(100, r.ctx_pct) + '%"></i></span>' +
        '<span class="cpct">' + r.ctx_pct + '% · ' + fmtTok(r.ctx_tokens) + '</span></div>';
    }

    var expLine = (r.off_account && r.expected)
      ? '<div class="exp">off-account — expected ' + esc(r.expected) + '</div>' : "";

    // detail drawer (read-only): current activity + the raw numbers
    var kv = "";
    if (r.activity) kv += '<div class="k">activity</div><div class="v txt">' + esc(r.activity) +
      (r.activity_age_s != null ? '  <span style="color:var(--faint)">(' + fmtAge(r.activity_age_s) + ' ago)</span>' : '') + '</div>';
    kv += '<div class="k">status</div><div class="v">' + esc(r.bucket) + (r.flagged ? ' · DARK (wanted up)' : '') + '</div>';
    kv += '<div class="k">token</div><div class="v">' + tokenBadge(r.auth_fp) + ' ' + esc(acct) +
      (r.verified ? ' · verified' : ' · unverified') + (r.off_account ? ' · OFF-ACCOUNT' : '') + '</div>';
    kv += '<div class="k">model</div><div class="v">' + (r.model ? esc(shortModel(r.model)) : 'default (unpinned)') + '</div>';
    if (r.ctx_pct != null)
      kv += '<div class="k">context</div><div class="v">' + r.ctx_pct + '% · ' + fmtTok(r.ctx_tokens) + ' / ' +
        fmtTok(r.ctx_window) + (r.ctx_age_s != null ? ' · read ' + fmtAge(r.ctx_age_s) + ' ago' : '') + '</div>';
    kv += '<div class="k">base</div><div class="v">' + esc(r.base_agent_id || "") + '</div>';
    if (r.heartbeat_age_s != null)
      kv += '<div class="k">heartbeat</div><div class="v">' + fmtAge(r.heartbeat_age_s) + ' ago</div>';

    return '<div class="lane ' + cls + '">' +
        '<span class="spine"></span>' +
        '<div class="rowtop">' +
          '<span class="stdot"></span>' +
          '<div class="idwrap">' +
            '<div class="id">' + s + ' <span class="st">' + esc(badge) + '</span></div>' +
            '<div class="sub2">' + sub2 + '</div>' +
            ctxHtml +
          '</div>' +
          '<span class="acct">' + esc(acct) + '</span>' +
          '<span class="chev">&#8250;</span>' +
        '</div>' +
        '<div class="drawer">' + expLine +
          '<div class="kv">' + kv + '</div>' +
        '</div>' +
      '</div>';
  }

  function render(data) {
    var lanes = (data && data.lanes) || [];
    renderPool(data && data.pool);
    var c = (data && data.counts) || {};
    var subBits = [
      '<b class="good">' + (c.working || 0) + '</b> working',
      (c.idle || 0) + ' idle',
    ];
    if (c.offline) subBits.push('<b class="warn">' + c.offline + '</b> offline');
    if (c.flagged) subBits.push('<b class="bad">' + c.flagged + '</b> dark');
    subBits.push(lanes.length + ' irsyad lane' + (lanes.length === 1 ? '' : 's'));
    $("sub").innerHTML = subBits.join(" · ");

    $("rows").innerHTML = lanes.length
      ? lanes.map(laneHtml).join("")
      : '<div class="empty">No irsyad lanes live.</div>';
    $("stamp").textContent = "updated " + new Date().toLocaleTimeString();
  }

  // tap-to-expand (event delegation; read-only — no writes bind here)
  $("rows").addEventListener("click", function (e) {
    var row = e.target.closest && e.target.closest(".lane");
    if (row) row.classList.toggle("open");
  });

  // ── version badge (matches fleet.js): baked APP_BUILD vs server /api/version ──
  var APP_BUILD = 'fc-v53';
  function verNum(v) { var m = /^fc-v(\d+)$/.exec(String(v == null ? "" : v)); return m ? parseInt(m[1], 10) : null; }
  function renderBuild(sv, sha) {
    var el = $("build");
    if (!el) return;
    var svn = verNum(sv), cvn = verNum(APP_BUILD);
    if (svn != null && cvn != null && svn > cvn) {
      el.innerHTML = '<span class="d" style="background:#f6c453;box-shadow:0 0 6px #f6c453"></span>' + APP_BUILD + ' → ' + esc(sv);
    } else {
      el.innerHTML = '<span class="d"></span>' + APP_BUILD + (sha ? ' · ' + esc(sha) : '');
    }
  }
  function checkVersion() {
    fetch("/api/version", { headers: authHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) { if (v) renderBuild(v.version, v.sha); })
      .catch(function () {});
  }

  var loading = false;
  function load() {
    if (loading) return;
    loading = true;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 8000);
    fetch("/api/irsyad", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) {
        if (r.status === 401) { $("sub").innerHTML = '<b class="bad">Unauthorized</b> — open from the Fleet page so the token is set.'; return null; }
        return r.ok ? r.json() : null;
      })
      .then(function (data) { if (data) render(data); })
      .catch(function () { $("stamp").textContent = "offline — retrying"; })
      .finally(function () { clearTimeout(timer); loading = false; });
  }

  $("refresh").addEventListener("click", load);
  load();
  checkVersion();
  setInterval(load, 30000);
})();
