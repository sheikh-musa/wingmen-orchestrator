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
  function setLive(ok) {
    var d = $("liveDot");
    d.className = "live" + (ok ? "" : " down");
    d.textContent = ok ? "● live" : "● offline";
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

  var NEED_ICON = { blocked_deploy: "⛔", blocked_lane: "\u{1F6AB}", blocked_task: "⏸", response: "\u{1F4AC}" };
  function renderNeeds(items) {
    $("needsCount").textContent = items.length ? String(items.length) : "";
    if (!items.length) { $("needs").innerHTML = '<div class="empty">Nothing waiting on you. ✨</div>'; return; }
    $("needs").innerHTML = items.map(function (n) {
      var crit = n.priority === "P0" || n.kind === "blocked_deploy";
      return '<div class="need' + (crit ? ' crit' : '') + '">' +
        '<div class="ico">' + (NEED_ICON[n.kind] || "❗") + '</div>' +
        '<div class="m"><div class="k">' +
          '<span class="who">' + esc(n.who) + '</span>' +
          '<span class="tag">' + esc(n.tag) + '</span>' +
          '<span class="age">' + esc(fmtAge(n.age_s)) + '</span>' +
        '</div><div class="what">' + esc(n.what) + '</div></div>' +
      '</div>';
    }).join("");
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

  function renderLanes(lanes) {
    var primary = lanes.filter(function (l) { return l.bucket === "working" || l.flagged; });
    var routine = lanes.filter(function (l) { return !(l.bucket === "working" || l.flagged); });
    var html = primary.map(laneCard).join("");
    if (routine.length) {
      html += '<div class="collapsed" id="routineToggle">▸ <b>' + routine.length + ' lane' +
        (routine.length > 1 ? 's' : '') + '</b> idle &amp; fine — ' +
        esc(routine.map(function (l) { return l.agent_id; }).slice(0, 6).join(", ")) +
        (routine.length > 6 ? "…" : "") + '</div>' +
        '<div id="routine" style="display:none">' + routine.map(laneCard).join("") + '</div>';
    }
    $("lanes").innerHTML = html || '<div class="empty">No lanes.</div>';
    var t = $("routineToggle");
    if (t) t.addEventListener("click", function () {
      var r = $("routine");
      var open = r.style.display !== "none";
      r.style.display = open ? "none" : "block";
      t.innerHTML = t.innerHTML.replace(open ? "▾" : "▸", open ? "▸" : "▾");
      if (open) t.firstChild.textContent = "▸ "; // collapse arrow
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
  var openPeek = null, peekTimer = null;
  function stopPeek() { if (peekTimer) { clearInterval(peekTimer); peekTimer = null; } openPeek = null; }

  // Render the cleaned pane text as an activity feed: one row per line, the
  // last line highlighted as the current action. A "raw" toggle drops back to
  // the plain capture for power use.
  function renderPeek(box, text, raw) {
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
    }
    var rawBtn = box.querySelector("[data-raw]");
    if (rawBtn) rawBtn.addEventListener("click", function (e) {
      e.stopPropagation(); box.dataset.raw = raw ? "" : "1"; renderPeek(box, box._text || text, !raw);
    });
  }

  function fetchPeek(session, box) {
    fetch("/api/lanes/" + encodeURIComponent(session) + "/pane", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { setLive(false); stopPeek(); return null; }
        if (r.status === 404) return { dead: true }; return r.json(); })
      .then(function (data) {
        if (!data) return;
        if (data.dead) { box.innerHTML = '<div class="peek-empty">session not live</div>'; return; }
        box._text = data.text || "";
        renderPeek(box, box._text, box.dataset.raw === "1");
      }).catch(function () {});
  }

  function bindPeeks() {
    document.querySelectorAll(".lane[data-peek]").forEach(function (card) {
      if (card._bound) return; card._bound = true;
      var session = card.getAttribute("data-peek");
      var box = card.querySelector(".peek");
      card.addEventListener("click", function (ev) {
        if (ev.target.closest(".peek")) return; // clicks inside the peek don't toggle
        if (openPeek === session) { box.classList.remove("open"); stopPeek(); return; }
        document.querySelectorAll(".peek.open").forEach(function (b) { b.classList.remove("open"); });
        stopPeek();
        openPeek = session; box.classList.add("open");
        box.innerHTML = '<div class="peek-empty">loading…</div>';
        fetchPeek(session, box);
        peekTimer = setInterval(function () { fetchPeek(session, box); }, 3000);
      });
    });
  }

  // ---- load + refresh -----------------------------------------------------
  function load() {
    return fetch("/api/fleet", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setLive(false); $("needs").innerHTML = UNAUTH; $("lanes").innerHTML = ""; throw new Error("unauth"); }
        return r.json();
      })
      .then(function (d) {
        setLive(true);
        renderPulse(d.pulse || {});
        renderNeeds(d.needs_you || []);
        renderLanes(d.lanes || []);
        renderDeploys(d.deploys || []);
      });
  }

  var refreshTimer = null;
  function start() {
    load().catch(function () { setLive(false); });
    if (refreshTimer) clearInterval(refreshTimer);
    // Don't repaint while a peek is open (would drop the user's expanded view).
    refreshTimer = setInterval(function () { if (!openPeek) load().catch(function () {}); }, 8000);
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
      if (dy <= 0) { pulling = false; ptr.style.height = "0px"; return; }
      e.preventDefault();
      ptr.style.height = Math.min(MAX, dy * DAMP) + "px";
      armed = dy >= ARM; ptr.classList.toggle("armed", armed);
      ptrTxt.textContent = armed ? "Release to refresh" : "Pull to refresh";
    }, { passive: false });
    function end() {
      if (!pulling) return; pulling = false; ptr.classList.add("snap");
      if (armed) { ptr.classList.add("refreshing"); ptrTxt.textContent = "Refreshing…"; ptr.style.height = "44px";
        setTimeout(function () { window.location.reload(); }, 150); }
      else { ptr.style.height = "0px"; ptr.classList.remove("armed"); }
    }
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", function () { pulling = false; ptr.style.height = "0px"; ptr.classList.remove("armed"); });
  })();

  start();
})();
