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
  var APP_BUILD = "fc-v5";
  function renderBuild(serverVersion, serverSha) {
    var el = $("build");
    if (!el) return;
    if (serverVersion && serverVersion !== APP_BUILD) {
      // a newer build is deployed than this cached bundle -> flag it amber; the
      // one-shot SW reload (fleet.html) converges the device on next control change.
      el.textContent = APP_BUILD + " → " + serverVersion;
      el.className = "build stale";
      return;
    }
    el.textContent = APP_BUILD + (serverSha ? " · " + serverSha : "");
    el.className = "build";
  }
  function loadBuild() {
    renderBuild(null, null);   // show the device build instantly (works pre-auth / offline)
    fetch("/api/version").then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) { if (v) renderBuild(v.version, v.sha); })
      .catch(function () {});
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
  function renderNeeds(items) {
    $("needsCount").textContent = items.length ? String(items.length) : "";
    if (!items.length) { $("needs").innerHTML = '<div class="empty">Nothing waiting on you. ✨</div>'; return; }
    $("needs").innerHTML = items.map(function (n) {
      var crit = n.priority === "P0" || n.kind === "blocked_deploy";
      var jump = laneIndex[n.who] || "";   // only lane-backed items are tappable
      return '<div class="need' + (crit ? ' crit' : '') + (jump ? ' tappable' : '') + '"' +
          (jump ? ' data-jump="' + esc(jump) + '"' : '') + '>' +
        '<div class="ico">' + (NEED_ICON[n.kind] || "❗") + '</div>' +
        '<div class="m"><div class="k">' +
          '<span class="who">' + esc(n.who) + '</span>' +
          '<span class="tag">' + esc(n.tag) + '</span>' +
          '<span class="age">' + esc(fmtAge(n.age_s)) + '</span>' +
        '</div><div class="what">' + esc(n.what) + '</div></div>' +
        (jump ? '<div class="go">›</div>' : '') +
      '</div>';
    }).join("");
    bindNeeds();
  }

  function bindNeeds() {
    document.querySelectorAll("#needs .need.tappable").forEach(function (row) {
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
    return '<div class="lane' + (l.flagged ? ' flag' : '') + '"' + (peek ? ' data-peek="' + esc(peek) + '"' : '') + '>' +
      '<div class="top">' +
        '<span class="st-dot ' + esc(l.bucket) + '"></span>' +
        '<span class="id">' + esc(l.agent_id) + '</span>' + badge +
        '<span class="state ' + esc(l.bucket) + '">' + esc(l.bucket) + '</span>' +
      '</div>' +
      (act ? '<div class="act">' + esc(act) + '</div>' : '') +
      '<div class="meta">' +
        '<span class="hb ' + hbClass + '">' + esc(hbTxt) + '</span>' +
        (l.desired_state ? '<span>desired: ' + esc(l.desired_state) + '</span>' : '') +
        (peek ? '<span class="tap">peek ›</span>' : '') +
      '</div>' +
      (peek ? '<div class="peek" data-peekbox="' + esc(peek) + '"></div>' : '') +
    '</div>';
  }

  // Whether the "N lanes idle & fine" group is expanded. A MODULE var (not DOM
  // state) so it survives the innerHTML rebuild on every live refresh — a
  // background data tick must never re-collapse what the operator opened
  // (operator #3440: "expanded lanes auto contract").
  var routineExpanded = false;

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
    });
    bindPeeks();
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
      var lines = text.split("\n").filter(function (l) { return l.trim(); });
      var last = lines.length - 1;
      var body = lines.map(function (ln, i) {
        return '<div class="row' + (i === last ? ' now' : '') + '"><span class="g">' +
          (i === last ? "▶" : "·") + '</span><span class="tx">' + esc(ln) + '</span></div>';
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

    buildLaneIndex(d.lanes || []);    // before renderNeeds, so items know their lane
    renderPulse(d.pulse || {});
    renderNeeds(d.needs_you || []);
    renderLanes(d.lanes || []);
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
