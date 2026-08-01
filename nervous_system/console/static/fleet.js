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
    if (!window.confirm("Reset " + body + "?\n\nThis clears its context and reboots it from its latest handoff. It refuses if the body is busy, and preserves any staged composer text.")) return;
    var orig = btn.textContent;
    btn.disabled = true; btn.textContent = "↻ resetting…";
    fetch("/api/reset", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ body: body })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; }); })
      .then(function (res) {
        btn.textContent = (res.ok && res.j.ok) ? "✓ reset sent" : "✗ " + (res.j.error || "failed");
        setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 5000);
      })
      .catch(function () { btn.textContent = "✗ error"; setTimeout(function () { btn.disabled = false; btn.textContent = orig; }, 5000); });
  }
  function bindResets() {
    document.querySelectorAll(".reset-btn").forEach(function (btn) {
      if (btn._bound) return; btn._bound = true;
      btn.addEventListener("click", function (ev) { ev.stopPropagation(); doReset(btn); });
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
  var APP_BUILD = "fc-v15";
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
    try { if (sessionStorage.getItem(key)) return; sessionStorage.setItem(key, "1"); } catch (e) {}
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

  // who -> peek session, so a NEEDS-YOU item can jump to its lane. Keyed by
  // every identifier a needs `who` might carry (instance id, base id, lane
  // label, tmux session) since kinds differ: blocked_lane.who is an agent_id,
  // response.who is a from_agent (base id), blocked_task.who is a lane label.
  var laneIndex = {};
  var lastLanes = [];

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
        (l.desired_state ? '<span>desired: ' + esc(l.desired_state) + '</span>' : '') +
        (peek ? '<span class="tap">peek ›</span>' : '') +
        resetBtnHtml(l.agent_id) +
      '</div>' +
      (peek ? '<div class="peek" data-peekbox="' + esc(peek) + '"></div>' : '') +
    '</div>';
  }

  // Whether the "N lanes idle & fine" group is expanded. A MODULE var (not DOM
  // state) so it survives the innerHTML rebuild on every live refresh — a
  // background data tick must never re-collapse what the operator opened
  // (operator #3440: "expanded lanes auto contract").
  var routineExpanded = false;

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
        (peek ? '<span class="tap">peek ›</span>' : '') +
        resetBtnHtml(c.agent_id) +
      '</div>' +
      (peek ? '<div class="peek" data-peekbox="' + esc(peek) + '"></div>' : '') +
    '</div>';
  }
  function renderCoordinators(items) {
    var el = document.getElementById("coordinators");
    if (!el) return;
    if (!items || !items.length) { el.innerHTML = '<div class="empty">No coordinators.</div>'; return; }
    el.innerHTML = items.map(coordCard).join("");
    bindResets();
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
    });
    bindPeeks();
    bindResets();
  }

  var DEP_STAGES = { pending:1, pushed:1, in_review:1, merged:1, live:1, blocked:1 };
  function renderDeploys(rows) {
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
    return '<div class="bl">' +
      '<span class="bltag ' + meta[0] + '">' + esc(meta[1]) + '</span>' +
      '<div class="blbody">' +
        '<div class="blask">' + esc(r.ask) + '</div>' +
        (r.note ? '<div class="blnote">' + esc(r.note) + '</div>' : '') +
        (r.op_ref ? '<div class="blop">' + esc(r.op_ref) + '</div>' : '') +
      '</div>' +
    '</div>';
  }
  function renderBacklog(rows) {
    var el = $("backlog");
    if (!el) return;
    if (!rows || !rows.length) {
      el.innerHTML = '<div class="empty">No asks tracked.</div>';
      $("backlogCount").textContent = "";
      return;
    }
    var byStatus = {};
    rows.forEach(function (r) { (byStatus[r.status] = byStatus[r.status] || []).push(r); });
    var needs = (byStatus.needs_you || []).length;
    $("backlogCount").textContent = needs ? (needs + " need you") : "";
    var html = "";
    BL_ORDER.forEach(function (st) {
      var items = byStatus[st];
      if (items && items.length) html += items.map(backlogItem).join("");
    });
    el.innerHTML = html;
  }

  // ---- context bloat (worker lanes; coordinators live in their own section) --
  // Collapsible (collapsed by default): the header summarises count + worst%,
  // click to expand the per-lane bars. ctxExpanded is a MODULE var (like
  // routineExpanded) so a background refresh never re-collapses what the operator
  // opened. Each row carries the same 🔑 token badge as the lane cards (op#9088).
  var ctxExpanded = false;
  function ctxRow(r) {
    var pct = r.pct == null ? 0 : r.pct;
    var stale = r.age_s != null && r.age_s > 86400;   // >1d old reading -> flag it
    return '<div class="ctx">' +
      '<div class="ctxtop">' +
        '<span class="ctxid">' + esc(r.agent) + '</span>' +
        tokenBadge(r.auth_fp) +
        '<span class="ctxpct ' + esc(r.level) + '">' + pct + '%</span>' +
      '</div>' +
      '<div class="bar"><div class="fill ' + esc(r.level) + '" style="width:' + pct + '%"></div></div>' +
      '<div class="ctxmeta">' + fmtTok(r.ctx_tokens) + ' / ' + fmtTok(r.window || 1000000) +
        (r.age_s != null ? ' · <span class="' + (stale ? "staler" : "") + '">' + esc(fmtAge(r.age_s)) + ' ago</span>' : "") +
      '</div>' +
    '</div>';
  }
  function renderContextBloat(rows) {
    var el = $("ctxBloat");
    if (!el) return;
    if (!rows || !rows.length) { el.innerHTML = '<div class="empty">No context telemetry.</div>'; return; }
    var worst = rows.reduce(function (m, r) { return Math.max(m, r.pct || 0); }, 0);
    var head = '<div class="collapsed" id="ctxToggle">' + (ctxExpanded ? "▾" : "▸") +
      ' <b>' + rows.length + ' lane' + (rows.length > 1 ? 's' : '') + '</b> — worst ' + worst + '%</div>';
    el.innerHTML = head +
      '<div id="ctxList" style="display:' + (ctxExpanded ? "block" : "none") + '">' +
      rows.map(ctxRow).join("") + '</div>';
    var t = $("ctxToggle");
    if (t) t.addEventListener("click", function () {
      ctxExpanded = !ctxExpanded;
      $("ctxList").style.display = ctxExpanded ? "block" : "none";
      t.firstChild.textContent = (ctxExpanded ? "▾" : "▸") + " ";
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
  function applyData(d) {
    var scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    var pb = currentPeekBox();
    var pbBody = pb ? pb.querySelector(".body") : null;
    var peekScroll = pbBody ? pbBody.scrollTop : 0;

    var lanes = dedupeLanes(d.lanes || []);   // one card per tmux session (no peek DOM collision)
    buildLaneIndex(lanes);            // before renderNeeds, so items know their lane
    renderPulse(d.pulse || {});
    renderNeeds(d.needs_you || []);
    renderBacklog(d.backlog || []);
    renderCoordinators(d.coordinators || []);
    renderContextBloat(d.context_bloat || []);
    renderTokenAuth(d.token_auth || {});
    renderLanes(lanes);
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
