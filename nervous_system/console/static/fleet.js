// Fleet view — attention-first monitor (redesign #7576). One /api/fleet call
// (pulse + needs-you + lanes + deploys, all live-derived), periodic refresh,
// tap a lane for a READABLE peek (activity feed, not a terminal wall). Auth is
// IP-allowlist-first; a breakglass token (rare) rides the Authorization header
// from localStorage. Vanilla JS, no deps.
(function () {
  "use strict";

  var token = localStorage.getItem("console_token") || "";
  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }
  // Map a coordinator/lane agent_id to the reset "body" the console can clear
  // (POST /api/reset). Only the three singletons that don't self-reset are
  // resettable; everything else returns "" (no button). Mirrors the Telegram
  // clear-buttons' allowlist — the backend re-checks, this only hides the affordance.
  function resetBodyFor(agentId) {
    if (agentId === "orch-console") return "nazim";
    if (agentId === "cai") return "cai";
    if (agentId === "cc-orchestrator") return "hub";
    return "";
  }
  function resetBtnHtml(agentId) {
    var b = resetBodyFor(agentId);
    return b ? '<button class="reset-btn" data-reset="' + b + '" title="Reset context: clear + reboot from the latest handoff (refuses if busy)">↻ reset</button>' : "";
  }
  function doReset(btn) {
    var body = btn.getAttribute("data-reset");
    if (!body) return;
    // Two-tap confirm — NOT window.confirm(): in an iOS standalone (home-screen)
    // PWA the native confirm() is suppressed and returns false, so the reset
    // silently never fired (operator 2026-08-02: console reset "nothing
    // happened", had to use the Telegram button). First tap arms for 3s; a
    // second tap within the window actually resets. Reset still refuses if the
    // body is busy and preserves staged composer text — that's the backend.
    if (!btn._armed) {
      btn._armed = true;
      btn.classList.add("arm");
      btn.textContent = "tap again to reset";
      btn._disarm = setTimeout(function () {
        btn._armed = false; btn.classList.remove("arm"); btn.textContent = "↻ reset";
      }, 3000);
      return;
    }
    clearTimeout(btn._disarm); btn._armed = false; btn.classList.remove("arm");
    var orig = "↻ reset";
    btn.disabled = true; btn.textContent = "↻ resetting…";
    fetch("/api/reset", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ body: body })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }); })
      .then(function (res) {
        // Human message per outcome (op#9262) — a bare "✗ error" read as "nothing
        // happened" and hid WHY. 409/429 are the guard doing its job, not a fault.
        var msg;
        if (res.ok && res.j.ok) { msg = "✓ reset sent"; }
        else if (res.s === 409) { msg = "⏳ already resetting"; }
        else if (res.s === 429) { msg = "⏳ just reset — wait " + (res.j.retry_after_s || 60) + "s"; }
        else { msg = "✗ " + (res.j.error || "failed"); }
        btn.textContent = msg;
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 5000);
      })
      .catch(function () {
        // A dropped RESPONSE (Mini network flake / Errno54) does NOT mean the reset
        // didn't run — the POST may have reached the server and fired the script
        // before the reply was lost. Say so honestly rather than a bare "error".
        btn.textContent = "✗ network dropped — may have run; wait ~60s";
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 6000);
      });
  }
  function bindResets() {
    document.querySelectorAll(".reset-btn").forEach(function (btn) {
      if (btn._bound) return; btn._bound = true;
      btn.addEventListener("click", function (ev) { ev.stopPropagation(); doReset(btn); });
    });
  }

  // ── Lane OAuth-account switch (token-pool conservation) ────────────────────
  // Per-lane account selector + a confirm-then-switch button (same two-tap guard
  // as reset — a native confirm() is suppressed in the iOS standalone PWA). The
  // account list is the terms-clean ALLOWLIST the backend enforces; the FORBIDDEN
  // gazzabyte consumer token (CAI-729) is never offered here and is refused by
  // both the endpoint allowlist and switch_lane_token.sh. Only rendered for a
  // lane with a live tmux_session (the switch targets that session by name).
  var SWITCH_ACCOUNTS = [
    { name: "musa", label: "Musa", fp: "68142948" },
    { name: "syed", label: "Syed", fp: "582043088" }
  ];
  function currentAccountName(fp) {
    fp = fp || "";
    for (var i = 0; i < SWITCH_ACCOUNTS.length; i++) {
      if (fp.indexOf(SWITCH_ACCOUNTS[i].fp) === 0) return SWITCH_ACCOUNTS[i].name;
    }
    return "";
  }
  function switchCtlHtml(session, fp) {
    if (!session) return "";   // no live session -> nothing to re-token
    var cur = currentAccountName(fp);
    var opts = SWITCH_ACCOUNTS.map(function (a) {
      return '<option value="' + a.name + '"' + (a.name === cur ? " selected" : "") + '>' + esc(a.label) + '</option>';
    }).join("");
    return '<span class="switch-ctl" data-switch-session="' + esc(session) + '">' +
      '<select class="tok-sel" title="Choose the Claude account to run this lane on">' + opts + '</select>' +
      '<button class="switch-btn" title="Re-token this lane onto the selected account. Restarts the lane process and RESUMES the conversation on the new account (falls back to fresh if no session is found); identity is preserved. Refuses if the lane is busy.">⇄ switch</button>' +
      '</span>';
  }
  function doSwitch(btn) {
    var ctl = btn.closest ? btn.closest(".switch-ctl") : btn.parentNode;
    if (!ctl) return;
    var session = ctl.getAttribute("data-switch-session");
    var sel = ctl.querySelector(".tok-sel");
    var tokenName = sel ? sel.value : "";
    if (!session || !tokenName) return;
    // Two-tap confirm (iOS PWA suppresses window.confirm — see doReset). First
    // tap arms for 3s; a second tap actually switches. The backend re-tokens the
    // lane and RESUMES its conversation on the new account (falls back to fresh if
    // no session is found) and refuses a busy lane, so this only guards a mis-tap.
    if (!btn._armed) {
      btn._armed = true;
      btn.classList.add("arm");
      btn.textContent = "tap again — restarts lane";
      btn._disarm = setTimeout(function () {
        btn._armed = false; btn.classList.remove("arm"); btn.textContent = "⇄ switch";
      }, 3000);
      return;
    }
    clearTimeout(btn._disarm); btn._armed = false; btn.classList.remove("arm");
    var orig = "⇄ switch";
    btn.disabled = true; btn.textContent = "⇄ switching…";
    fetch("/api/switch-token", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ lane: session, token_name: tokenName })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }); })
      .then(function (res) {
        var msg;
        if (res.ok && res.j.ok) { msg = "✓ switched"; }
        else if (res.s === 409) { msg = "⏳ already switching"; }
        else if (res.s === 429) { msg = "⏳ just switched — wait " + (res.j.retry_after_s || 30) + "s"; }
        else if (res.s === 504) { msg = "✗ timed out — check the lane"; }
        else { msg = "✗ " + (res.j.error || "failed"); }
        btn.textContent = msg;
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 6000);
      })
      .catch(function () {
        // A dropped response does NOT mean the switch didn't run — the kill +
        // relaunch may have fired before the reply was lost (Mini network flake).
        btn.textContent = "✗ network dropped — may have run; check lane";
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 7000);
      });
  }
  function bindSwitches() {
    document.querySelectorAll(".switch-ctl").forEach(function (ctl) {
      if (ctl._bound) return; ctl._bound = true;
      // Stop card-level clicks (which open the peek) from firing on the control.
      ctl.addEventListener("click", function (ev) { ev.stopPropagation(); });
      var sel = ctl.querySelector(".tok-sel");
      if (sel) sel.addEventListener("change", function (ev) { ev.stopPropagation(); });
      var btn = ctl.querySelector(".switch-btn");
      if (btn) btn.addEventListener("click", function (ev) { ev.stopPropagation(); doSwitch(btn); });
    });
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }
  function fmtAge(s) {
    if (s == null) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m";
    if (s < 86400) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function setLive(ok, label) {
    var d = $("liveDot");
    d.className = "live" + (ok ? "" : " down");
    d.textContent = label || (ok ? "● live" : "● offline");
  }

  // The 🔑 token/auth badge shared by lanes, coordinators, and context-bloat rows
  // (op#9017/9088). auth_fp = sha256(OAuth token)[:12]; Musa 68142948… (green) vs
  // Syed 582043088… (amber) vs any other fp (grey). No fp -> no badge (a body that
  // never self-registered its auth, e.g. the cross-host hub).
  function tokenBadge(fp) {
    fp = fp || "";
    if (fp.indexOf("68142948") === 0)
      return '<span class="tok" style="color:#4ade80" title="' + esc(fp) + '">🔑 Musa</span>';
    if (fp.indexOf("582043088") === 0)
      return '<span class="tok" style="color:#fbbf24" title="' + esc(fp) + '">🔑 Syed</span>';
    return fp ? '<span class="tok" style="color:#94a3b8" title="' + esc(fp) + '">🔑 ' + esc(fp.slice(0, 8)) + '</span>' : '';
  }

  // The 📍 physical-host badge — the twin of tokenBadge, keyed on agent_status.host.
  // Reuses the .tok chip (distinct blue + a pin so it never reads as the 🔑 token
  // badge). Raw host maps to a friendly label: Mini / Studio / VPS; empty/null (e.g.
  // a singleton that never registered a host) -> no badge.
  function hostBadge(host) {
    host = host || "";
    var h = host.toLowerCase();
    var lbl = "";
    if (h.indexOf("mini") >= 0) lbl = "Mini";
    else if (h.indexOf("studio") >= 0) lbl = "Studio";
    else if (h.indexOf("vps") >= 0 || h.indexOf("core") >= 0) lbl = "VPS";
    return lbl ? '<span class="tok" style="color:#60a5fa" title="' + esc(host) + '">📍 ' + lbl + '</span>' : '';
  }

  // Weekly Max-pool usage in the header (op#9770), from pool_usage (the SRE's
  // weekly_limit_monitor writes it each poll). Colour tracks the same thresholds
  // the monitor pages on: <75 good, 75-90 warn, >=90 bad. A reading whose
  // updated_age_s is beyond STALE (the monitor stalled) greys out + flags ⚠ —
  // never a frozen number shown as live (op#9770's whole point). Tooltip carries
  // the 5h window + reset + freshness so the chip itself stays compact.
  var POOL_STALE_S = 1800;   // 30min: the monitor runs well under this
  function poolChip(p) {
    var pct = p.pct_7d;
    if (pct == null) return "";
    var stale = (p.updated_age_s != null && p.updated_age_s > POOL_STALE_S);
    var cls = stale ? "stale" : (pct >= 90 ? "bad" : (pct >= 75 ? "warn" : "good"));
    var title = p.pool + " Max weekly pool: " + Math.round(pct) + "% (7d)"
      + (p.pct_5h != null ? ", " + Math.round(p.pct_5h) + "% (5h)" : "")
      + (p.resets_at ? " · resets " + esc(p.resets_at) : "")
      + (p.updated_age_s != null ? " · read " + fmtAge(p.updated_age_s) + " ago" : "")
      + (stale ? " · STALE (monitor stalled)" : "");
    return '<span class="poolchip ' + cls + '" title="' + esc(title) + '">'
      + esc(p.pool) + ' <b>' + Math.round(pct) + '%</b> wk' + (stale ? " ⚠" : "") + '</span>';
  }
  function renderPoolUsage(rows) {
    var el = document.getElementById("poolUsage");
    if (!el) return;
    el.innerHTML = (rows && rows.length) ? rows.map(poolChip).join("") : "";
  }

  // Last-good /api/fleet payload, persisted so a cold launch (or a launch on a
  // slow/degraded tailnet) paints real data INSTANTLY from cache and NEVER
  // shows a hard error — the network fetch then quietly refreshes it. This is
  // the data-layer twin of the SW serving the cached shell: shell + last-good
  // data together mean the operator always sees his console, reconnect happens
  // in the background (2026-07-11: the slow first fetch used to hard-error).
  // Build identity of THIS cached bundle. MUST be bumped in lockstep with sw.js
  // VERSION on every deploy. Baked in (not fetched) so the badge reflects the
  // build the DEVICE actually loaded — a stale cached page shows its OLD version,
  // exposing staleness instead of a live fetch hiding it (PWA-cache-loop fix).
  var APP_BUILD = 'fc-v31';
  function verNum(v) {                       // "fc-v10" -> 10 ; unparseable -> null
    var m = /^fc-v(\d+)$/.exec(String(v == null ? "" : v));
    return m ? parseInt(m[1], 10) : null;
  }
  function renderBuild(serverVersion, serverSha) {
    var el = $("build");
    if (!el) return;
    var sv = verNum(serverVersion), cv = verNum(APP_BUILD);
    if (sv != null && cv != null && sv > cv) {
      // a STRICTLY-newer build is deployed than this cached bundle -> amber; the
      // page-level version gate (checkVersion) hard-resets the device onto it.
      // Only forward (sv>cv): a server BEHIND this page (the window after a
      // static deploy but before the server process restarts, when /api/version
      // still reports the OLD baked-in build) is NOT stale — don't cry wolf.
      el.textContent = APP_BUILD + " → " + serverVersion;
      el.className = "build stale";
      return;
    }
    el.textContent = APP_BUILD + (serverSha ? " · " + serverSha : "");
    el.className = "build";
  }

  // ---- bulletproof version gate (op #3640) --------------------------------
  // A wedged / orphaned service worker must NEVER strand the operator on a stale
  // build (iOS PWA stuck on an old SW; full close+reopen didn't help — "I can't
  // be deleting and re-adding for every update"). This page-level gate does NOT
  // depend on the SW updating (controllerchange is the unreliable path): it asks
  // the server its version and, if the server is STRICTLY AHEAD of the build THIS
  // page is running, force-clears every SW + cache and reloads from network —
  // bypassing the SW-update path entirely. /api/version is open and never
  // SW-cached (sw.js returns early for /api/*), and we cache-bust the URL, so the
  // read is trustworthy even under a wedged old SW that might intercept /api/*.
  function hardResetForVersion(target) {
    // Loop-safe: hard-reset AT MOST once per target version per tab session. If
    // the network somehow still serves the old bundle after the reset, we do NOT
    // loop — the target is stamped, so we fall back to the amber "stale" badge.
    var key = "fleet_hardreset_" + target;
    try { if (sessionStorage.getItem(key)) return; } catch (e) {}
    // CONFIRM-THEN-CLEAR (op#10291 — the "totally dropped" fix). The old path
    // cleared the SW + ALL caches and THEN reloaded from network; if that reload
    // dropped on the operator's marginal Abu-Dhabi<->Singapore relay he was left
    // with cleared-cache + no-shell = a BLANK console (the Aug-3 incident). So:
    // PRE-FETCH the fresh shell + app bundle and confirm they are ACTUALLY
    // reachable FIRST; only then clear + reload. If the pre-fetch fails (link
    // dropped), do NOT clear — keep the WORKING cached shell + the amber "stale"
    // badge, and leave the key UNSTAMPED so checkVersion retries when the link
    // recovers. Cache-bust + no-store so a wedged old SW can't pass its own cached
    // copy off as "reachable".
    var bust = "?_hr=" + Date.now();
    var okText = function (r) {
      if (!r || !r.ok) return Promise.reject(new Error("unreachable"));
      return r.text();
    };
    Promise.all([
      fetch("/" + bust, { cache: "no-store" }).then(okText),
      fetch("/static/fleet.js" + bust, { cache: "no-store" }).then(okText)
    ]).then(function (parts) {
      if (parts.some(function (t) { return !t || t.length < 200; }))
        throw new Error("shell incomplete");
      // Fresh shell + bundle CONFIRMED reachable — now it is safe to clear.
      try { sessionStorage.setItem(key, "1"); } catch (e) {}
      var finish = function () { window.location.reload(); };
      var jobs = [];
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        jobs.push(navigator.serviceWorker.getRegistrations()
          .then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); })
          .catch(function () {}));
      }
      if (window.caches && caches.keys) {
        jobs.push(caches.keys()
          .then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); })
          .catch(function () {}));
      }
      Promise.all(jobs).then(finish, finish);
    }).catch(function () {
      // Link dropped mid-confirm — keep the working cached shell (NEVER blank),
      // retry on the next checkVersion. Nothing to clean up: we cleared nothing.
    });
  }
  function checkVersion() {
    // cache-bust so even a mis-behaving old SW doing cache-first on /api/* can't
    // feed us a stale version (a never-cached unique URL misses its cache).
    return fetch("/api/version?_=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) {
        if (!v) return;
        renderBuild(v.version, v.sha);
        var sv = verNum(v.version), cv = verNum(APP_BUILD);
        if (sv != null && cv != null && sv > cv) hardResetForVersion(v.version);
      })
      .catch(function () {});
  }
  function loadBuild() {
    renderBuild(null, null);   // show the device build instantly (works pre-auth / offline)
    checkVersion();
    // Re-check periodically so a deploy lands even while the PWA stays OPEN — the
    // belt that makes a wedged SW unable to strand a still-open session (not just
    // a reopen). Loop-safe via the per-target sessionStorage stamp.
    setInterval(checkVersion, 60000);
  }

  var LAST_GOOD_KEY = "fleet_last_good";
  function saveLastGood(d) {
    try { localStorage.setItem(LAST_GOOD_KEY, JSON.stringify(d)); } catch (e) {}
  }
  function loadLastGood() {
    try { return JSON.parse(localStorage.getItem(LAST_GOOD_KEY) || "null"); }
    catch (e) { return null; }
  }

  var UNAUTH =
    '<div class="empty">Not authorized from this device/network. If this is expected ' +
    "(off-tailnet, IP changed), set a breakglass token in localStorage('console_token') and pull to refresh.</div>";

  // ---- render -------------------------------------------------------------
  function renderPulse(p) {
    var needs = p.needs_you || 0;
    var pl = $("pulse");
    pl.className = "pulse " + (needs > 0 ? "attn" : "clear");
    $("pulseBig").textContent = needs > 0
      ? (needs === 1 ? "1 thing needs you" : needs + " things need you")
      : "All clear";
    $("strip").innerHTML =
      '<span class="chip"><span class="d good"></span><b>' + (p.working||0) + '</b> working</span>' +
      '<span class="chip"><span class="d dim"></span><b>' + (p.idle||0) + '</b> idle</span>' +
      (p.flagged ? '<span class="chip"><span class="d bad"></span><b>' + p.flagged + '</b> flagged</span>' : '') +
      (p.offline ? '<span class="chip"><span class="d bad"></span><b>' + p.offline + '</b> offline</span>' : '');
  }

  // Glance banner (operator ask, extended to TOP 3): the three most context-
  // bloated WORKER lanes, rendered at the top of the pulse so the worst offenders
  // are visible without scanning cards. context_bloat is already the per-lane list
  // (coordinators excluded — they own their cards), but we sort here defensively
  // rather than trust payload order, then take the top 3 (fewer if <3, hidden if
  // 0). Each pct is color-coded by ITS OWN level (so #1 can be amber while #2/#3
  // are green); the leading dot reflects the worst lane's level as the overall
  // severity signal. Label = the instance's sub_tag (== its tmux_session) or the
  // base id for a solo lane — the same name its card shows. Display-only; no new
  // data. Entries wrap on a narrow phone; each carries a tokens/age tooltip.
  function renderTopBloat(rows) {
    var el = $("topBloat");
    if (!el) return;
    var list = (rows || []).filter(function (r) { return r && r.pct != null; });
    if (!list.length) { el.className = "topbloat"; el.removeAttribute("title"); el.innerHTML = ""; return; }
    // Collapse each family (rows sharing one cc_identity `agent`) to a SINGLE entry
    // so the top-3 shows DISTINCT lanes — otherwise a family's live instance row AND
    // its legacy base-NULL aggregate row can BOTH place (both irsyad @74% today),
    // re-creating the op#10550 duplicate-irsyad look. Prefer a real per-instance row
    // (sub_tag set = a live lane) over the base aggregate; among those, the fullest.
    var best = {};
    list.forEach(function (r) {
      var k = r.agent || r.sub_tag;
      var cur = best[k];
      if (!cur) { best[k] = r; return; }
      var rTag = r.sub_tag ? 1 : 0, cTag = cur.sub_tag ? 1 : 0;
      if (rTag !== cTag ? rTag > cTag : r.pct > cur.pct) best[k] = r;
    });
    var uniq = Object.keys(best).map(function (k) { return best[k]; });
    uniq.sort(function (a, b) { return b.pct - a.pct; });   // fullest first
    var top = uniq.slice(0, 3);
    var lead = top[0].level || "green";                     // overall severity = worst lane
    var parts = top.map(function (r) {
      var who = r.sub_tag || r.agent || "?";
      var lvl = r.level || "green";
      var tip = fmtTok(r.ctx_tokens) + " / " + fmtTok(r.window || 1000000) +
        (r.age_s != null ? " · " + fmtAge(r.age_s) + " ago" : "");
      return '<span class="ent" title="' + esc(tip) + '">' +
        '<span class="who">' + esc(who) + '</span> ' +
        '<span class="pct ' + esc(lvl) + '">' + r.pct + '%</span></span>';
    });
    el.className = "topbloat show";
    el.setAttribute("title", "Top " + top.length + " context-bloated lanes (of " + list.length + ")");
    el.innerHTML =
      '<span class="dot ' + esc(lead) + '"></span>' +
      '<span class="lbl">Top bloat</span> ' +
      parts.join('<span class="sep">·</span>');
  }

  // who -> peek session, so a NEEDS-YOU item can jump to its lane. Keyed by
  // every identifier a needs `who` might carry (instance id, base id, lane
  // label, tmux session) since kinds differ: blocked_lane.who is an agent_id,
  // response.who is a from_agent (base id), blocked_task.who is a lane label.
  var laneIndex = {};
  var lastLanes = [];
  // Per-lane context readings (the former "Context bloat" section), keyed by the
  // family id so each lane card can fold in its own /1M window gauge. context_bloat
  // rows carry `agent` = cc_identity = agent_status.base_agent_id (see db.py
  // build_context_bloat_query), so a lane looks itself up by base_agent_id (falling
  // back to agent_id). Rebuilt on every applyData; a module var so a background
  // refresh + jumpToLane re-render (which reuses lastLanes) still find it.
  var laneCtxIndex = {};

  // A tmux session hosts exactly ONE live lane, so two lane cards carrying the
  // same data-peek/data-peekbox can only mean a stale duplicate row (e.g. a
  // cc-infra-2 left in the registry alongside the live cc-infra-1, same
  // tmux_session 'infra'). Two nodes sharing that key collide in the peek DOM
  // lookup — querySelector('.lane[data-peek=…]') / currentPeekBox() grab the
  // WRONG card, so the peek renders another agent's pane (operator, 2026-07-11).
  // Collapse to one card per peek key here, keeping the liveliest row, so the
  // collision can't recur even if a duplicate registry row slips back in.
  // Lower rank = livelier = the row we keep.
  function laneRank(l) {
    if (l.bucket === "working" || l.flagged) return 0;   // active beats idle/dead
    var hb = l.heartbeat_age_s;
    if (hb == null) return 1e12;                          // no heartbeat = least alive
    return hb;                                            // otherwise freshest hb wins
  }
  function dedupeLanes(lanes) {
    var slots = [], idxByKey = {};
    (lanes || []).forEach(function (l) {
      var key = l.tmux_session || l.lane;
      if (!key) { slots.push(l); return; }               // no peek target → can't collide
      if (idxByKey[key] === undefined) { idxByKey[key] = slots.length; slots.push(l); }
      else if (laneRank(l) < laneRank(slots[idxByKey[key]])) slots[idxByKey[key]] = l;
    });
    return slots;
  }

  function buildLaneIndex(lanes) {
    laneIndex = {};
    (lanes || []).forEach(function (l) {
      var sess = l.tmux_session || l.lane;
      if (!sess) return;
      [l.agent_id, l.base_agent_id, l.lane, l.tmux_session].forEach(function (k) {
        if (k) laneIndex[k] = sess;
      });
    });
  }

  var NEED_ICON = { blocked_deploy: "⛔", blocked_lane: "\u{1F6AB}", blocked_task: "⏸", response: "\u{1F4AC}" };

  // One needs-you row. `handling` = a fleet/hub-facing item shown in the lower-
  // emphasis "Fleet is handling" group: the operator reaches lanes only through
  // the hub, so these aren't his to answer — they're demoted (never crit-red)
  // and their tap affordance reads "peek ›" (open the lane's peek to WATCH), not
  // a reply chevron (operator flag, 2026-07-12).
  function needCard(n, handling) {
    var crit = !handling && (n.priority === "P0" || n.kind === "blocked_deploy");
    var jump = laneIndex[n.who] || "";   // only lane-backed items are tappable
    return '<div class="need' + (crit ? ' crit' : '') + (handling ? ' handling' : '') +
        (jump ? ' tappable' : '') + '"' + (jump ? ' data-jump="' + esc(jump) + '"' : '') + '>' +
      '<div class="ico">' + (NEED_ICON[n.kind] || "❗") + '</div>' +
      '<div class="m"><div class="k">' +
        '<span class="who">' + esc(n.who) + '</span>' +
        '<span class="tag">' + esc(n.tag) + '</span>' +
        '<span class="age">' + esc(fmtAge(n.age_s)) + '</span>' +
      '</div><div class="what">' + esc(n.what) + '</div></div>' +
      (jump ? '<span class="go">peek ›</span>' : '') +
    '</div>';
  }

  // Split the feed by audience: 'operator' rows are genuinely HIS (a
  // requires_response addressed to musa/operator) and own the "Needs you" hero;
  // everything else ('fleet' — hub-directed decisions, blocked lanes/tasks/
  // deploys) drops to a lower-emphasis "Fleet is handling" group so the operator
  // isn't tricked into thinking he must respond to the hub's work.
  function renderNeeds(items) {
    var ops = [], fleet = [];
    (items || []).forEach(function (n) {
      (n.audience === "operator" ? ops : fleet).push(n);
    });
    $("needsCount").textContent = ops.length ? String(ops.length) : "";
    $("needs").innerHTML = ops.length
      ? ops.map(function (n) { return needCard(n, false); }).join("")
      : '<div class="empty">Nothing waiting on you. ✨</div>';
    var hsec = $("handlingSec");
    if (fleet.length) {
      $("handling").innerHTML = fleet.map(function (n) { return needCard(n, true); }).join("");
      $("handlingCount").textContent = String(fleet.length);
      if (hsec) hsec.style.display = "";
    } else {
      $("handling").innerHTML = "";
      $("handlingCount").textContent = "";
      if (hsec) hsec.style.display = "none";
    }
    bindNeeds();
  }

  // Tapping a needs/handling row opens that lane's peek (watch), never a reply.
  function bindNeeds() {
    document.querySelectorAll("#needs .need.tappable, #handling .need.tappable").forEach(function (row) {
      if (row._bound) return; row._bound = true;
      row.addEventListener("click", function () { jumpToLane(row.getAttribute("data-jump")); });
    });
  }

  // Jump to a lane card: expand the collapsed 'idle & fine' group if the card
  // lives there, scroll it into view, and open its peek — the natural next look
  // when something needs the operator.
  function jumpToLane(session) {
    if (!session) return;
    var sel = '.lane[data-peek="' + ((window.CSS && CSS.escape) ? CSS.escape(session) : session) + '"]';
    var card = document.querySelector(sel);
    if (!card && !routineExpanded) {   // hidden inside the collapsed routine group
      routineExpanded = true;
      renderLanes(lastLanes);
      card = document.querySelector(sel);
    }
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    openPeek_(session);
  }

  // The per-lane context gauge, folded into the card (was the standalone
  // "Context bloat" section). Reads laneCtxIndex by the lane's family id and
  // renders the same bar + pct + token/window readout the old section did.
  // Returns "" when there's no reading for this lane (e.g. a lane that never
  // reported a current-context size) so the card simply omits the gauge.
  function laneCtxHtml(l) {
    var base = l.base_agent_id || l.agent_id;
    // obs-1 (op#10550): a FAMILY instance carries its own current-context row keyed
    // by (base, sub_tag) where sub_tag == this lane's tmux_session — match that
    // first so each perimeter card shows ITS OWN gauge, not a shared one. Fall back
    // to the bare base id for a solo lane (its row has no sub_tag).
    var sess = l.tmux_session || l.lane;
    var cx = null;
    if (base && sess) cx = laneCtxIndex[base + CTX_SEP + sess];
    if (!cx && base) cx = laneCtxIndex[base];
    if (!cx || cx.pct == null) return "";
    var pct = cx.pct;
    var stale = cx.age_s != null && cx.age_s > 86400;   // >1d old reading -> flag it
    return '<div class="lanectx">' +
      '<div class="bar"><div class="fill ' + esc(cx.level) + '" style="width:' + pct + '%"></div></div>' +
      '<div class="ctxmeta">' +
        '<span class="ctxpct ' + esc(cx.level) + '">' + pct + '% ctx</span> · ' +
        fmtTok(cx.ctx_tokens) + ' / ' + fmtTok(cx.window || 1000000) +
        (cx.age_s != null ? ' · <span class="' + (stale ? "staler" : "") + '">' + esc(fmtAge(cx.age_s)) + ' ago</span>' : "") +
      '</div>' +
    '</div>';
  }

  function laneCard(l) {
    var peek = l.tmux_session || l.lane || "";
    var badge = (l.flagged && l.bucket === "offline") ? '<span class="badge">dark</span>' : "";
    var live = l.live || {};
    var act = (live.running && live.activity) || l.activity || l.current_task || "";
    var age = l.activity != null && l.activity_age_s != null ? " · " + fmtAge(l.activity_age_s) : "";
    var hb = l.heartbeat_age_s;
    var hbClass = hb == null || hb >= 900 ? "dead" : (hb >= 120 ? "stale" : "fresh");
    var hbTxt = hb == null ? "no hb" : "hb " + fmtAge(hb);
    // Token attribution (op#9017): which OAuth account this lane authenticates as.
    // A lane re-tokens only on a REAL restart, so this shows the migration state at a glance.
    var tok = tokenBadge(l.auth_fp);
    var hostb = hostBadge(l.host);   // 📍 physical host (Mini/Studio/VPS)
    return '<div class="lane' + (l.flagged ? ' flag' : '') + '"' + (peek ? ' data-peek="' + esc(peek) + '"' : '') + '>' +
      '<div class="top">' +
        '<span class="st-dot ' + esc(l.bucket) + '"></span>' +
        '<span class="id">' + esc(l.agent_id) + '</span>' + badge +
        '<span class="state ' + esc(l.bucket) + '">' + esc(l.bucket) + '</span>' +
      '</div>' +
      (act ? '<div class="act">' + esc(act) + '</div>' : '') +
      '<div class="meta">' +
        '<span class="hb ' + hbClass + '">' + esc(hbTxt) + '</span>' +
        tok +
        hostb +
        (l.desired_state ? '<span>desired: ' + esc(l.desired_state) + '</span>' : '') +
        (peek ? '<span class="tap">peek ›</span>' : '') +
        resetBtnHtml(l.agent_id) +
        switchCtlHtml(l.tmux_session, l.auth_fp) +
      '</div>' +
      laneCtxHtml(l) +
      (peek ? '<div class="peek" data-peekbox="' + esc(peek) + '"></div>' : '') +
    '</div>';
  }

  // Whether the "N lanes idle & fine" group is expanded. A MODULE var (not DOM
  // state) so it survives the innerHTML rebuild on every live refresh — a
  // background data tick must never re-collapse what the operator opened
  // (operator #3440: "expanded lanes auto contract").
  var routineExpanded = false;

  // Whether the Coordinators section is expanded (operator ask: make it
  // collapsible). A MODULE var (like routineExpanded/backlogExpanded) so a
  // background refresh never re-collapses what the operator opened. Default
  // expanded to preserve the current always-visible behaviour.
  var coordExpanded = true;

  function coordCard(c) {
    var seen = c.last_seen_s;
    var dot = (seen != null && seen < 1800) ? "working" : "idle";
    var hbClass = seen == null ? "dead" : (seen < 1800 ? "fresh" : (seen < 7200 ? "stale" : "dead"));
    var seenTxt = seen == null ? "quiet" : "active " + fmtAge(seen);
    var age = (c.activity != null && c.activity_age_s != null) ? " - " + fmtAge(c.activity_age_s) : "";
    // Peekable only when the backend confirmed the coordinator's pane is a LOCAL
    // live tmux session (orch on this host). Non-peekable coords (Nazim's pane on
    // the MacBook) get NO peek affordance — no dead-end "peek ›" that would 404.
    // Reuses the exact lane peek machinery (data-peek + .peek box, bound by
    // bindPeeks) so the coordinator peek renders + toggles identically.
    var peek = c.peekable ? (c.tmux_session || "") : "";
    // Each coordinator card carries its OWN context readout + token badge (op#9088).
    var cctx = c.ctx_pct != null
        ? '<span class="cctx ' + esc(c.ctx_level || "") + '">' + c.ctx_pct + '% ctx</span>' : '';
    var tok = tokenBadge(c.auth_fp);
    var hostb = hostBadge(c.host);   // 📍 physical host (VPS for the cross-host hub)
    return '<div class="lane coord"' + (peek ? ' data-peek="' + esc(peek) + '"' : '') + '>' +
      '<div class="top">' +
        '<span class="st-dot ' + dot + '"></span>' +
        '<span class="id">' + esc(c.short || c.agent_id) + '</span>' +
        '<span class="state coordrole">' + esc(c.role_label || "") + '</span>' +
      '</div>' +
      (c.activity ? '<div class="act">' + esc(c.activity) + esc(age) + '</div>'
                  : '<div class="act coordidle">nothing on the bus recently</div>') +
      '<div class="meta"><span class="hb ' + hbClass + '">' + esc(seenTxt) + '</span>' +
        cctx +
        tok +
        hostb +
        (peek ? '<span class="tap">peek ›</span>' : '') +
        resetBtnHtml(c.agent_id) +
      '</div>' +
      (peek ? '<div class="peek" data-peekbox="' + esc(peek) + '"></div>' : '') +
    '</div>';
  }
  function renderCoordinators(items) {
    var el = document.getElementById("coordinators");
    if (!el) return;
    // Wire the collapsible section header (op: make Coordinators expandable).
    // onclick (not addEventListener) so the per-tick re-render never stacks
    // duplicate listeners on the static #coordHead element.
    var head = $("coordHead");
    if (head) {
      var chev = $("coordChevron");
      el.style.display = coordExpanded ? "block" : "none";
      if (chev) chev.textContent = coordExpanded ? "▾" : "▸";
      head.onclick = function () {
        coordExpanded = !coordExpanded;
        el.style.display = coordExpanded ? "block" : "none";
        if (chev) chev.textContent = coordExpanded ? "▾" : "▸";
      };
    }
    if (!items || !items.length) { el.innerHTML = '<div class="empty">No coordinators.</div>'; return; }
    el.innerHTML = items.map(coordCard).join("");
    bindPeeks();   // coord cards reuse the lane peek machinery; bind them here so
    bindResets();  // peek works regardless of render order (op 2026-08-02).
    bindSwitches();  // no-op on coord cards (no .switch-ctl); kept for symmetry.
  }
  function renderLanes(lanes) {
    lastLanes = lanes;   // kept so jumpToLane can re-render (expand routine) if needed
    var primary = lanes.filter(function (l) { return l.bucket === "working" || l.flagged; });
    var routine = lanes.filter(function (l) { return !(l.bucket === "working" || l.flagged); });
    var html = primary.map(laneCard).join("");
    if (routine.length) {
      html += '<div class="collapsed" id="routineToggle">' + (routineExpanded ? "▾" : "▸") +
        ' <b>' + routine.length + ' lane' +
        (routine.length > 1 ? 's' : '') + '</b> idle &amp; fine — ' +
        esc(routine.map(function (l) { return l.agent_id; }).slice(0, 6).join(", ")) +
        (routine.length > 6 ? "…" : "") + '</div>' +
        '<div id="routine" style="display:' + (routineExpanded ? "block" : "none") + '">' +
        routine.map(laneCard).join("") + '</div>';
    }
    $("lanes").innerHTML = html || '<div class="empty">No lanes.</div>';
    var t = $("routineToggle");
    if (t) t.addEventListener("click", function () {
      routineExpanded = !routineExpanded;
      $("routine").style.display = routineExpanded ? "block" : "none";
      t.firstChild.textContent = (routineExpanded ? "▾" : "▸") + " ";
      bindPeeks();
      bindResets();
      bindSwitches();
    });
    bindPeeks();
    bindResets();
    bindSwitches();
  }

  var DEP_STAGES = { pending:1, pushed:1, in_review:1, merged:1, live:1, blocked:1 };
  function renderDeploys(rows) {
    if (!$("deploys")) return;   // #deploys removed (op#9888); guard like the other render fns so tick() doesn't throw -> stuck "reconnecting"
    if (!rows || !rows.length) { $("deploys").innerHTML = '<div class="empty">No deploys tracked.</div>'; return; }
    $("deploys").innerHTML = '<div class="depstrip">' + rows.map(function (d) {
      var stage = (d.stage || "").toLowerCase();
      var sc = DEP_STAGES[stage] ? stage : "pending";
      return '<div class="dep ' + sc + '">' +
        '<div class="ws">' + esc(d.workstream) + '</div>' +
        '<div class="stg">' + esc(stage || "—") + '</div>' +
        '<div class="rp">' + (d.repo ? esc(d.repo) + " · " : "") + esc(fmtAge(d.updated_age_s)) + '</div>' +
        (d.url ? '<a href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.url) + '</a>' : '') +
      '</div>';
    }).join("") + '</div>';
  }

  // ---- lane worklists (the per-lane drain view — lane_tasks) ---------------
  // Renders each lane's queue so the operator can SEE what a lane is working on
  // and watch items drain. Rows arrive grouped-ready: the backend orders by
  // (lane, blocked-last, id) = strict FIFO with blockers sunk. A BLOCKED task is
  // one waiting on the operator/cai to clear a gate, so it reads "⏳ awaiting you"
  // in amber — the operator's OWN gates stand out from lane-side work in flight.
  // A lane sets lane_tasks.status once and does NOT refresh it while working, so
  // 'active' ALONE is not proof of live work — a fossil left 'active' for days
  // (the pre-drain reality: a task marked active in a past session, never touched)
  // must NOT read green "working". So an 'active' task only reads "working" if it
  // was touched recently; a cold one reads "stale · Nd" with its age, honest that
  // nothing is moving. (Post-drain, the lane's done/heartbeat keeps updated_at
  // fresh, so genuinely-worked tasks stay green — this heuristic self-corrects.)
  var STALE_ACTIVE_S = 7200;   // 2h untouched -> not "working", show it cold
  function queueChip(t) {
    var st = (t.status || "").toLowerCase();
    if (st === "blocked")
      return '<span class="qchip wait">⏳ awaiting you</span>';
    if (st === "active") {
      var age = t.updated_age_s;
      if (age != null && age > STALE_ACTIVE_S)
        return '<span class="qchip stale">stale · ' + fmtAge(age) + '</span>';
      var elapsed = (t.elapsed_min != null ? " " + t.elapsed_min + "m" : "");
      return '<span class="qchip work">working' + elapsed + '</span>' +
        (t.over_sla ? '<span class="qchip over">⚠ SLA</span>' : "");
    }
    return '<span class="qchip queued">queued</span>';
  }
  function renderQueue(rows) {
    var el = document.getElementById("laneQueues");
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<div class="empty">No lane work queued.</div>'; return;
    }
    // Group by lane, preserving the backend's within-lane order. Plain object as
    // a map is fine — lane names are our own controlled labels, not user input.
    var order = [], byLane = {};
    rows.forEach(function (t) {
      var lane = t.lane || "—";
      if (!byLane[lane]) { byLane[lane] = []; order.push(lane); }
      byLane[lane].push(t);
    });
    el.innerHTML = order.map(function (lane) {
      var tasks = byLane[lane];
      var body = tasks.map(function (t) {
        return '<div class="qrow">' +
          '<span class="qtitle">' + esc(t.title || "") + '</span>' +
          queueChip(t) +
        '</div>';
      }).join("");
      return '<div class="qlane">' +
        '<div class="qlane-h">' + esc(lane) +
          ' <span class="qn">' + tasks.length + '</span></div>' +
        body +
      '</div>';
    }).join("");
  }

  // ---- backlog (the operator's realtime "Your asks" tracker) --------------
  function fmtTok(n) {
    if (n == null) return "—";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "k";
    return String(n);
  }
  // status -> [css class, human label]. Group order matches the backend sort
  // (needs_you first) so the operator's court floats to the top.
  var BL_STATUS = {
    needs_you:   ["wait", "needs you"],
    in_progress: ["prog", "in progress"],
    done:        ["done", "done"],
    parked:      ["park", "parked"]
  };
  var BL_ORDER = ["needs_you", "in_progress", "done", "parked"];
  function backlogItem(r) {
    var meta = BL_STATUS[r.status] || ["park", r.status];
    // Each card is a swipe row: two action panels revealed underneath, and the
    // .bl slab (opaque) slides over them. Swipe-left → drop, swipe-right → top.
    return '<div class="bl-row">' +
      '<div class="bl-act top">▲ top</div>' +
      '<div class="bl-act drop">drop ✕</div>' +
      '<div class="bl" data-id="' + esc(r.id) + '">' +
        '<span class="bltag ' + meta[0] + '">' + esc(meta[1]) + '</span>' +
        '<div class="blbody">' +
          '<div class="blask">' + esc(r.ask) + '</div>' +
          (r.done_when ? '<div class="bldone">🎯 done when: ' + esc(r.done_when) + '</div>' : '') +
          (r.note ? '<div class="blnote">' + esc(r.note) + '</div>' : '') +
          (r.op_ref ? '<div class="blop">' + esc(r.op_ref) + '</div>' : '') +
        '</div>' +
      '</div>' +
    '</div>';
  }
  // Swipe on a "Your asks" card (op 2026-08-02). Pointer Events so it works for
  // both touch (phone) and mouse (desktop test). Past the threshold: swipe-left
  // drops the ask (status='dropped', leaves the plate + Nazim's memory), swipe-
  // right prioritises it to the absolute top — "that ordered set is the backlog
  // you should work on".
  var SWIPE_THRESHOLD = 82;
  function swipeAction(id, action, card, row) {
    row.classList.remove("armdrop", "armtop");
    if (action === "drop") { card.style.transform = "translateX(-120%)"; row.classList.add("dropping"); }
    else { card.style.transform = ""; }
    fetch("/api/backlog", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ id: id, action: action })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) { load(); }               // re-render: reordered / removed
        else { card.style.transform = ""; row.classList.remove("dropping"); }
      })
      .catch(function () { card.style.transform = ""; row.classList.remove("dropping"); });
  }
  function bindBacklogSwipe() {
    document.querySelectorAll("#blList .bl[data-id]").forEach(function (card) {
      if (card._swipeBound) return; card._swipeBound = true;
      var row = card.parentNode, startX = 0, dx = 0, dragging = false;
      card.addEventListener("pointerdown", function (e) {
        dragging = true; startX = e.clientX; dx = 0;
        card.style.transition = "none";
        try { card.setPointerCapture(e.pointerId); } catch (err) {}
      });
      card.addEventListener("pointermove", function (e) {
        if (!dragging) return;
        dx = e.clientX - startX;
        card.style.transform = "translateX(" + dx + "px)";
        row.classList.toggle("armdrop", dx <= -SWIPE_THRESHOLD);
        row.classList.toggle("armtop", dx >= SWIPE_THRESHOLD);
      });
      function end() {
        if (!dragging) return; dragging = false;
        card.style.transition = "";
        var id = parseInt(card.getAttribute("data-id"), 10);
        if (dx <= -SWIPE_THRESHOLD) swipeAction(id, "drop", card, row);
        else if (dx >= SWIPE_THRESHOLD) swipeAction(id, "prioritise", card, row);
        else { card.style.transform = ""; row.classList.remove("armdrop", "armtop"); }
      }
      card.addEventListener("pointerup", end);
      card.addEventListener("pointercancel", end);
    });
  }
  // Collapsible (op#9102), default EXPANDED — it's the operator's primary plate,
  // so visible by default but he can collapse it. backlogExpanded is a MODULE var
  // (like routineExpanded) so a background refresh never re-collapses his choice.
  // Done tasks are excluded server-side (build_backlog_query), so this is only
  // his OPEN plate: needs_you + in_progress (+ parked).
  var backlogExpanded = true;
  function renderBacklog(rows) {
    var el = $("backlog");
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<div class="empty">No open asks. ✨</div>';
      $("backlogCount").textContent = "";
      return;
    }
    var needs = rows.filter(function (r) { return r.status === "needs_you"; }).length;
    $("backlogCount").textContent = needs ? (needs + " need you") : "";
    // Flat list in the BACKEND's order (sort_order primary), so the operator's
    // swipe-to-top ordering IS the rendered worklist — no re-grouping by status
    // (status is a per-card chip now). op 2026-08-02.
    var html = rows.map(backlogItem).join("");
    var head = '<div class="collapsed" id="blToggle">' + (backlogExpanded ? "▾" : "▸") +
      ' <b>' + rows.length + ' open</b>' + (needs ? ' — ' + needs + ' need you' : '') +
      ' <span class="blhint">swipe → top · ← drop</span></div>';
    el.innerHTML = head +
      '<div id="blList" style="display:' + (backlogExpanded ? "block" : "none") + '">' + html + '</div>';
    var t = $("blToggle");
    if (t) t.addEventListener("click", function () {
      backlogExpanded = !backlogExpanded;
      $("blList").style.display = backlogExpanded ? "block" : "none";
      t.firstChild.textContent = (backlogExpanded ? "▾" : "▸") + " ";
    });
    bindBacklogSwipe();
  }

  // ---- per-lane context readings --------------------------------------------
  // The old standalone "Context bloat" section is gone (unified-lanes redesign):
  // each worker lane's /1M window gauge now renders INSIDE its lane card via
  // laneCtxHtml(), sourced from laneCtxIndex (built in applyData from the same
  // context_bloat payload). Coordinators still carry their own ctx on their
  // cards (coordCard's .cctx chip, op#9088).
  // obs-1 (op#10550): a multi-instance FAMILY (the irsyad perimeter) emits one
  // context_bloat row per (agent, sub_tag), where sub_tag == that instance's
  // tmux_session. Index a tagged row under the composite key agent\x00sub_tag so
  // each family card folds in ITS OWN gauge (laneCtxHtml matches by tmux_session);
  // a solo lane's row (sub_tag null/empty) keys under the bare agent id, so the
  // base-id fallback in laneCtxHtml still finds it. NUL separator can't occur in
  // an id or session name, so the two key spaces never collide.
  var CTX_SEP = "\u0000";
  function buildLaneCtxIndex(rows) {
    laneCtxIndex = {};
    (rows || []).forEach(function (r) {
      if (!r || !r.agent) return;
      if (r.sub_tag) laneCtxIndex[r.agent + CTX_SEP + r.sub_tag] = r;
      else laneCtxIndex[r.agent] = r;
    });
  }

  // ---- token / auth per lane (Max vs metered + which account) -------------
  function renderTokenAuth(ta) {
    var el = $("tokenAuth");
    if (!el) return;
    ta = ta || {};
    var lanes = ta.lanes || [], sum = ta.summary || {};
    // fleet summary line: N on <owner>-Max · … · K metered
    var parts = [];
    var by = sum.by_owner || {};
    Object.keys(by).forEach(function (o) { parts.push('<b class="good">' + by[o] + '</b> ' + esc(o)); });
    if (sum.metered) parts.push('<b class="bad">' + sum.metered + '</b> metered');
    $("tokenSummary").innerHTML = parts.length ? parts.join(" · ") : "";
    if (!lanes.length) { el.innerHTML = '<div class="empty">No lane processes seen on this host.</div>'; return; }
    el.innerHTML = lanes.map(function (l) {
      var cls = l.metered ? "bad" : "good";
      var lbl = l.metered ? "metered API" : "Max";
      var acct = l.metered ? "" : (l.owner ? l.owner : "Max (unknown acct)");
      return '<div class="tok">' +
        '<span class="tdot ' + cls + '"></span>' +
        '<span class="tsess">' + esc(l.session) + '</span>' +
        '<span class="tbadge ' + cls + '">' + esc(lbl) + '</span>' +
        (acct ? '<span class="tacct">' + esc(acct) + (l.acct ? ' · ' + esc(l.acct) : "") + '</span>' : "") +
      '</div>';
    }).join("");
  }

  // ---- readable peek ------------------------------------------------------
  // Peek state is held in MODULE vars keyed by session, NOT in the DOM, so it
  // survives the lane list's innerHTML rebuild on every live refresh: which
  // lane is peeked (openPeek), each session's raw/feed toggle (peekRaw), and
  // the last captured text (peekText, for an instant repaint before the next
  // poll). This is why a background data tick no longer snaps a peek shut
  // (operator #3440: "peeks auto snap up").
  var openPeek = null, peekTimer = null;
  var peekRaw = {};   // session -> bool (raw mode)
  var peekText = {};  // session -> last captured pane text

  // The .peek box for the currently-open session in the CURRENT DOM (the node
  // is replaced on every re-render, so always look it up fresh).
  function currentPeekBox() {
    if (!openPeek) return null;
    var found = null;
    document.querySelectorAll(".peek[data-peekbox]").forEach(function (b) {
      if (b.getAttribute("data-peekbox") === openPeek) found = b;
    });
    return found;
  }

  function stopPeekPolling() { if (peekTimer) { clearInterval(peekTimer); peekTimer = null; } }
  function startPeekPolling() {
    stopPeekPolling();
    peekTimer = setInterval(function () {
      var box = currentPeekBox();
      if (box) fetchPeek(openPeek, box);
    }, 3000);
  }

  // Render the cleaned pane text as an activity feed: one row per line, the
  // last line highlighted as the current action. A "raw" toggle drops back to
  // the plain capture for power use. Preserves the scroll position of the
  // scrollable body across its own repaint so the 3s self-refresh (and a live
  // data tick) never yanks the operator back to the top mid-read.
  function renderPeek(box, text, raw) {
    var sess = box.getAttribute("data-peekbox");
    var prevBody = box.querySelector(".body");
    var prevScroll = prevBody ? prevBody.scrollTop : 0;
    if (!text) { box.innerHTML = '<div class="peek-empty">nothing captured</div>'; return; }
    var head = '<div class="ph">live peek <span class="raw" data-raw>' + (raw ? "feed ›" : "raw ⌄") + '</span></div>';
    if (raw) { box.innerHTML = head + '<pre class="raw-pre">' + esc(text) + '</pre>'; }
    else {
      // Render the cleaned pane as continuous, flowing text — one borderless
      // block per pane line so a wrapped line reads as a whole paragraph, not
      // choppy boxed shards (operator 2026-07-12: peek "reads broken
      // mid-sentence"). No leading dot/box per row; the last (current) line
      // gets a subtle emphasis so you can still spot the live tail.
      var lines = text.split("\n").filter(function (l) { return l.trim(); });
      var last = lines.length - 1;
      var body = lines.map(function (ln, i) {
        return '<div class="ln' + (i === last ? ' now' : '') + '">' + esc(ln) + '</div>';
      }).join("");
      box.innerHTML = head + '<div class="body">' + body + '</div>';
      var newBody = box.querySelector(".body");
      if (newBody) newBody.scrollTop = prevScroll;
    }
    var rawBtn = box.querySelector("[data-raw]");
    if (rawBtn) rawBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      peekRaw[sess] = !raw;
      renderPeek(box, peekText[sess] != null ? peekText[sess] : text, !raw);
    });
  }

  function fetchPeek(session, box) {
    fetch("/api/lanes/" + encodeURIComponent(session) + "/pane", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { setLive(false); closePeek(); return null; }
        if (r.status === 404) return { dead: true }; return r.json(); })
      .then(function (data) {
        if (!data || session !== openPeek) return;         // a newer open owns the box now
        box = currentPeekBox() || box;                     // box may have been replaced by a re-render
        if (!box) return;
        if (data.dead) { box.innerHTML = '<div class="peek-empty">session not live</div>'; return; }
        peekText[session] = data.text || "";
        renderPeek(box, peekText[session], !!peekRaw[session]);
      }).catch(function () {});
  }

  function openPeek_(session) {
    document.querySelectorAll(".peek.open").forEach(function (b) { b.classList.remove("open"); });
    openPeek = session;
    var box = currentPeekBox();
    if (!box) return;
    box.classList.add("open");
    if (peekText[session] != null) renderPeek(box, peekText[session], !!peekRaw[session]);
    else box.innerHTML = '<div class="peek-empty">loading…</div>';
    fetchPeek(session, box);
    startPeekPolling();
  }

  function closePeek() {
    var box = currentPeekBox();
    if (box) box.classList.remove("open");
    openPeek = null;
    stopPeekPolling();
  }

  // After a full re-render, re-open the peek the operator had open and restore
  // its inner scroll — the highest-value state-preservation path.
  function reflectOpenPeek(savedScroll) {
    if (!openPeek) return;
    var box = currentPeekBox();
    if (!box) { openPeek = null; stopPeekPolling(); return; } // lane left the list
    box.classList.add("open");
    if (peekText[openPeek] != null) {
      renderPeek(box, peekText[openPeek], !!peekRaw[openPeek]);
      var body = box.querySelector(".body");
      if (body && savedScroll != null) body.scrollTop = savedScroll;
    } else {
      box.innerHTML = '<div class="peek-empty">loading…</div>';
    }
    startPeekPolling(); // repoint the timer at the fresh box
  }

  function bindPeeks() {
    document.querySelectorAll(".lane[data-peek]").forEach(function (card) {
      if (card._bound) return; card._bound = true;
      var session = card.getAttribute("data-peek");
      card.addEventListener("click", function (ev) {
        if (ev.target.closest(".peek")) return; // clicks inside the peek don't toggle
        if (openPeek === session) { closePeek(); return; }
        openPeek_(session);
      });
    });
  }

  // ---- load + refresh -----------------------------------------------------
  // Paint a payload with full UI-state preservation (window scroll + open
  // peek's inner scroll). Shared by the live fetch AND the boot-time last-good
  // render, so a cached paint restores exactly what a live paint would.
  // (Token/model ground-truth moved to the dedicated /lanes page — op#10706 B.)

  function applyData(d) {
    var scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    var pb = currentPeekBox();
    var pbBody = pb ? pb.querySelector(".body") : null;
    var peekScroll = pbBody ? pbBody.scrollTop : 0;

    var lanes = dedupeLanes(d.lanes || []);   // one card per tmux session (no peek DOM collision)
    buildLaneIndex(lanes);            // before renderNeeds, so items know their lane
    buildLaneCtxIndex(d.context_bloat || []);   // before renderLanes, so each card can fold in its /1M gauge
    renderPulse(d.pulse || {});
    renderTopBloat(d.context_bloat || []);  // glance: worst-offender ctx lane up top (operator ask)
    renderPoolUsage(d.pool_usage || []);   // weekly Max-pool % up top (op#9770)
    renderNeeds(d.needs_you || []);
    renderBacklog(d.backlog || []);
    renderCoordinators(d.coordinators || []);
    renderLanes(lanes);
    renderQueue(d.queue || []);            // per-lane worklists (the drain view)
    renderDeploys(d.deploys || []);

    reflectOpenPeek(peekScroll);      // re-open peek + restore its inner scroll
    window.scrollTo(0, scrollY);      // restore page scroll AFTER layout settles
  }

  // Fetch /api/fleet with a hard deadline. A slow tailnet must fail FAST into
  // the retry loop (keeping last-good on screen), never hang the UI — the hang
  // is exactly what tripped the phone's hard "Could not connect" (2026-07-11).
  var FETCH_TIMEOUT_MS = 12000;
  function fetchFleet() {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT_MS);
    return fetch("/api/fleet", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) { clearTimeout(timer); return r; },
            function (e) { clearTimeout(timer); throw e; });
  }

  function load() {
    return fetchFleet()
      .then(function (r) {
        if (r.status === 401) {
          // Auth failure is NOT a connection failure — show the (actionable)
          // unauthorized message, don't fall into the reconnect loop's UI.
          setLive(false, "● unauthorized");
          $("needs").innerHTML = UNAUTH; $("lanes").innerHTML = "";
          var err = new Error("unauth"); err.unauth = true; throw err;
        }
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (d) {
        setLive(true);
        saveLastGood(d);
        applyData(d);
      });
  }

  // Self-scheduling refresh with reconnect backoff. On success -> steady 8s
  // cadence. On a network/timeout failure -> keep last-good on screen, flip the
  // dot to a quiet "reconnecting…", and retry with exponential backoff (capped)
  // — the app NEVER blanks or hard-errors on a bad first/next load.
  var REFRESH_MS = 8000, RETRY_MAX_MS = 15000;
  var refreshTimer = null, backoff = 0;
  function schedule(ms) {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(tick, ms);
  }
  function tick() {
    load().then(function () {
      backoff = 0;
      schedule(REFRESH_MS);            // healthy -> normal cadence
    }).catch(function (e) {
      if (e && e.unauth) {             // unauthorized: retry occasionally in case
        backoff = RETRY_MAX_MS;        // the IP/tailnet comes back, no fast spin
      } else {
        setLive(false, "● reconnecting…");
        backoff = Math.min(backoff ? backoff * 2 : 2000, RETRY_MAX_MS);
      }
      schedule(backoff);
    });
  }
  function start() {
    loadBuild();   // always-visible build badge (which build is this device on?)
    // Paint last-good FIRST so the operator sees his console immediately, even
    // before (or entirely without) a network response. tick() then refreshes.
    var cached = loadLastGood();
    if (cached) { setLive(false, "● reconnecting…"); applyData(cached); }
    tick();
  }

  // ---- pull-to-refresh ----------------------------------------------------
  (function () {
    var ptr = $("ptr"), ptrTxt = $("ptrTxt");
    if (!ptr) return;
    var startY = 0, pulling = false, armed = false;
    var ARM = 90, DAMP = 0.5, MAX = 90;
    document.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 1 || ptr.classList.contains("refreshing")) return;
      if (e.target.closest && e.target.closest(".peek .body")) { pulling = false; return; }
      if ((window.scrollY || document.documentElement.scrollTop) > 0) { pulling = false; return; }
      startY = e.touches[0].clientY; pulling = true; armed = false; ptr.classList.remove("snap");
    }, { passive: true });
    document.addEventListener("touchmove", function (e) {
      if (!pulling) return;
      var dy = e.touches[0].clientY - startY;
      if (dy <= 0) { pulling = false; ptr.classList.remove("pulling"); ptr.style.height = "0px"; return; }
      e.preventDefault();
      // .pulling shows the notch-fill strip (::before) so the pull reads as one
      // continuous sheet from the notch down, not a band floating below it.
      ptr.classList.add("pulling");
      ptr.style.height = Math.min(MAX, dy * DAMP) + "px";
      armed = dy >= ARM; ptr.classList.toggle("armed", armed);
      ptrTxt.textContent = armed ? "Release to refresh" : "Pull to refresh";
    }, { passive: false });
    function end() {
      if (!pulling) return; pulling = false; ptr.classList.add("snap");
      if (armed) { ptr.classList.add("refreshing"); ptrTxt.textContent = "Refreshing…"; ptr.style.height = "44px";
        setTimeout(function () { window.location.reload(); }, 150); }
      else { ptr.style.height = "0px"; ptr.classList.remove("armed"); ptr.classList.remove("pulling"); }
    }
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", function () { pulling = false; ptr.style.height = "0px"; ptr.classList.remove("armed"); ptr.classList.remove("pulling"); });
  })();

  start();
})();
