// Fleet Console v1 SPA — dependency-light (vanilla JS, EventSource + fetch).
// Auth: operator token sent ONLY in the Authorization header (never the URL).
// SSE note: EventSource cannot set headers, so the live stream is opened via a
// fetch() ReadableStream reader, keeping the token header-only end to end.
(function () {
  "use strict";

  // localStorage (not sessionStorage) so the operator's token persists across
  // tab/browser/app restarts — enter it ONCE per device, never re-type. The
  // token still travels header-only (never the URL). Console is tailnet-bound.
  var token = localStorage.getItem("console_token") || sessionStorage.getItem("console_token") || "";
  var es = null;          // AbortController for the fetch-based stream
  var lanesTimer = null;
  var seen = {};          // de-dup message ids

  var $ = function (id) { return document.getElementById(id); };
  // Auth is IP-allowlist-first now (server-side, CONSOLE_ALLOWED_IPS) — an
  // allowlisted device needs no header at all. *token* here is only ever the
  // dormant breakglass recovery secret, so omit the header entirely when
  // it's empty rather than sending a meaningless "Bearer ".
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }

  function setConn(state) {
    var dot = $("dot"), txt = $("connTxt");
    dot.className = "dot" + (state === "live" ? " live" : state === "down" ? " down" : "");
    txt.textContent = state;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    var d = new Date(iso);
    return isNaN(d) ? esc(iso) : d.toLocaleTimeString();
  }

  // seconds -> compact "just now / 5m / 3h / 2d" age label
  function fmtAge(s) {
    if (s == null) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }

  function renderMessage(m, flash) {
    if (m._resync) { resyncMessages(); return; }
    if (seen[m.id]) return;
    seen[m.id] = true;
    var el = document.createElement("div");
    el.className = "msg" + (flash ? " flash" : "");
    var pri = m.priority || "P2";
    el.innerHTML =
      '<div class="meta">' +
        '<span class="route">' + esc(m.from_agent) +
          ' <span class="arrow">&rarr;</span> ' + esc(m.to_agent) + '</span>' +
        '<span class="type">' + esc(m.message_type || "") + '</span>' +
        '<span class="pri ' + esc(pri) + '">' + esc(pri) + '</span>' +
        (m.requires_response ? '<span class="rr">needs response</span>' : '') +
        '<span class="time">' + fmtTime(m.created_at) + '</span>' +
      '</div>' +
      (m.subject ? '<div class="subj">' + esc(m.subject) + '</div>' : '') +
      (m.body ? '<div class="body">' + esc(m.body) + '</div>' : '');
    var box = $("messages");
    box.insertBefore(el, box.firstChild);
  }

  function apiUrl(path, params) {
    var u = path;
    if (params) {
      var q = Object.keys(params)
        .filter(function (k) { return params[k]; })
        .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(params[k]); })
        .join("&");
      if (q) u += "?" + q;
    }
    return u;
  }

  // 401 means this device's IP isn't in CONSOLE_ALLOWED_IPS — the normal
  // (allowlisted) case never hits this. Point at the breakglass fallback
  // rather than failing silently.
  var UNAUTH_HINT =
    '<div class="empty">Not authorized from this device/network.<br>' +
    "If this is expected (off-tailnet, IP changed), enter the breakglass token above and Retry.</div>";

  function loadMessages() {
    var params = {
      limit: 80,
      thread: $("fThread").value.trim(),
      agent: $("fAgent").value.trim(),
    };
    return fetch(apiUrl("/api/messages", params), { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); $("messages").innerHTML = UNAUTH_HINT; throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (rows) {
        var box = $("messages");
        box.innerHTML = "";
        seen = {};
        if (!rows.length) { box.innerHTML = '<div class="empty">No messages.</div>'; return; }
        // API returns newest-first; render oldest-first so insertBefore stacks newest on top.
        rows.slice().reverse().forEach(function (m) { renderMessage(m, false); });
      });
  }

  function resyncMessages() { loadMessages().catch(function () {}); }

  function laneHbClass(age) {
    if (age == null) return "dead";
    if (age < 120) return "fresh";
    if (age < 900) return "stale";
    return "dead";
  }

  // Dark = a lane that's SUPPOSED to be up (desired_state=up) but its
  // heartbeat is dead/stale — the signal an operator scanning a phone screen
  // needs to catch first, so these sort to the top and get a loud badge
  // rather than just a small colored heartbeat line.
  function laneFlag(l) {
    var hbClass = laneHbClass(l.heartbeat_age_s);
    var shouldBeUp = (l.desired_state || "").toLowerCase() === "up";
    if (shouldBeUp && hbClass === "dead") return "dark";
    if (shouldBeUp && hbClass === "stale") return "stale";
    return null;
  }
  var FLAG_RANK = { dark: 0, stale: 1 };

  // Live pane peek (read-only) — the DB-derived card (current_task/heartbeat)
  // is a dumb 5-min timer that shows "up", never "what's happening"; this
  // polls the lane's real tmux pane instead. Accordion: at most ONE peek
  // polls at a time (lightweight/throttled, per the ask) — opening another
  // lane's peek, or the periodic 10s lane-list refresh finding the lane
  // gone, stops the previous poll.
  var openPeek = null;   // { session } of the currently-expanded peek, or null
  var peekTimer = null;

  function stopPeek() {
    if (peekTimer) { clearInterval(peekTimer); peekTimer = null; }
    openPeek = null;
  }

  function fetchPeek(session, box) {
    fetch("/api/lanes/" + encodeURIComponent(session) + "/pane", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); stopPeek(); return null; }
        if (r.status === 404) { return { dead: true }; }
        return r.json();
      })
      .then(function (data) {
        if (!data) return;
        if (data.dead) { box.innerHTML = '<span class="peek-empty">session not live</span>'; return; }
        // Only auto-scroll if the user was ALREADY pinned to the bottom
        // before this update — otherwise a poll every ~2.5s yanks them away
        // mid-read every time (operator feedback). Checked BEFORE replacing
        // content, since scrollHeight changes once the new text is set; a
        // few px of tolerance absorbs sub-pixel rounding.
        var wasPinnedToBottom = box.scrollHeight - box.scrollTop - box.clientHeight <= 4;
        box.textContent = data.text || "";
        if (wasPinnedToBottom) { box.scrollTop = box.scrollHeight; }
      })
      .catch(function () {});
  }

  function startPeek(session, box) {
    if (peekTimer) clearInterval(peekTimer);
    openPeek = { session: session };
    box.classList.add("open");
    box.innerHTML = '<span class="peek-empty">loading&hellip;</span>';
    fetchPeek(session, box);
    peekTimer = setInterval(function () { fetchPeek(session, box); }, 2500);
  }

  // Pause polling while backgrounded (phone screen off / app switched away) —
  // no point burning battery/data on a peek nobody's looking at. Resumes
  // against the SAME session's box when the app comes back, if it's still
  // in the DOM (the periodic lane refresh may have removed it if the lane
  // itself is gone by then, which is fine — nothing to resume onto).
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (peekTimer) { clearInterval(peekTimer); peekTimer = null; }
      return;
    }
    if (openPeek && typeof CSS !== "undefined" && CSS.escape) {
      var box = document.querySelector('.peek-box[data-lane="' + CSS.escape(openPeek.session) + '"]');
      if (box) startPeek(openPeek.session, box);
    }
  });

  // SLA chip + task card markup, shared by the merged Lanes view below (used
  // to be the standalone Queue tab — folded into Lanes per operator layout
  // decision, msg re: 3-tab layout).
  function renderQtask(t, i) {
    var st = (t.status || "queued").toLowerCase();
    // Was an SLA ratio (27104m/240m) — pointless once a task runs for days
    // (operator 2634). Replaced with a plain AGE, flagged 'stale' when a task
    // has sat open > 1 day — which is the real "stuck for ages" signal (2632).
    var sla = "";
    var ageMin = t.elapsed_min != null ? t.elapsed_min
               : (t.updated_age_s != null ? Math.round(t.updated_age_s / 60) : null);
    if (ageMin != null) {
      sla = '<span class="sla' + (ageMin > 1440 ? ' over' : ' idle') + '">' +
              fmtAge(ageMin * 60) + '</span>';
    }
    return '<div class="qtask' + (t.over_sla ? ' breached' : '') + '">' +
      '<div class="top">' +
        '<span class="rank">' + (i + 1) + '.</span>' +
        '<span class="title">' + esc(t.title) + '</span>' +
        sla +
        '<span class="st ' + esc(st) + '">' + esc(st) + '</span>' +
      '</div>' +
      (t.detail ? '<div class="detail">' + esc(t.detail) + '</div>' : '') +
    '</div>';
  }

  // Lanes tab = merged status + queue + live-peek (3-tab layout: Lanes,
  // Messages, Deploys — Queue is no longer a separate tab). Fetches both
  // /api/lanes and /api/queue and folds each lane's tasks + peek control
  // into its own card.
  function loadLanes() {
    return Promise.all([
      fetch("/api/lanes", { headers: authHeaders() }),
      fetch("/api/queue", { headers: authHeaders() }),
    ]).then(function (resps) {
      if (resps[0].status === 401 || resps[1].status === 401) {
        setConn("down"); $("lanes").innerHTML = UNAUTH_HINT; throw new Error("unauthorized");
      }
      return Promise.all(resps.map(function (r) { return r.json(); }));
    }).then(function (results) {
      var rows = results[0], queueRows = results[1];
      var box = $("lanes");
      if (!rows.length && !queueRows.length) { box.innerHTML = '<div class="empty">No lanes.</div>'; return; }

      // group queue tasks by lane so each lane card can show its own work.
      var tasksByLane = {};
      queueRows.forEach(function (t) {
        if (!tasksByLane[t.lane]) tasksByLane[t.lane] = [];
        tasksByLane[t.lane].push(t);
      });
      var consumed = {};

      // Dark/stale-and-should-be-up lanes float to the top — that's the
      // thing worth an operator's attention on a small screen first.
      // Control plane pinned to the top (operator ask): orch first, cai next,
      // then dark/stale-should-be-up lanes, then the rest.
      function pinRank(l) {
        var id = l.agent_id || "";
        if (id.indexOf("cc-orchestrator") === 0) return 0;
        if (id === "cai" || id.indexOf("cc-cai") === 0) return 1;
        return 2;
      }
      rows = rows.slice().sort(function (a, b) {
        var pa = pinRank(a), pb = pinRank(b);
        if (pa !== pb) return pa - pb;
        var ra = FLAG_RANK[laneFlag(a)]; var rb = FLAG_RANK[laneFlag(b)];
        if (ra == null) ra = 2; if (rb == null) rb = 2;
        return ra - rb;
      });
      var reopenSession = openPeek ? openPeek.session : null;
      if (peekTimer) { clearInterval(peekTimer); peekTimer = null; } // rebind to the fresh DOM below

      var laneCards = rows.map(function (l) {
        var st = (l.status || "unknown").toLowerCase();
        var hb = l.heartbeat_age_s;
        var hbTxt = hb == null ? "no heartbeat" : fmtAge(hb);
        var flag = laneFlag(l);
        // "working on" = latest bus activity (current_task is just the boot string)
        var task = l.activity || l.current_task || "";
        var taskAge = l.activity != null && l.activity_age_s != null ? fmtAge(l.activity_age_s) : "";
        var tasks = l.lane && tasksByLane[l.lane] ? tasksByLane[l.lane] : [];
        if (l.lane) consumed[l.lane] = true;
        var tasksHtml = tasks.length
          ? '<div class="tasks">' + tasks.map(renderQtask).join("") + '</div>'
          : "";
        // Peek target: tmux_session (self-registered per INSTANCE at boot,
        // migration 005) is the real live session name, always correct.
        // fleet_lanes.lane is a static label shared by a whole agent FAMILY
        // (every cc-reviewer-N row has lane='reviewer') and can't resolve to
        // one specific on-demand session when several are live at once —
        // fall back to it only if a lane hasn't self-registered yet.
        var peekTarget = l.tmux_session || l.lane;
        return '<div class="lane' + (flag ? ' flag-' + flag : '') + '"' +
          (peekTarget ? ' data-peek="' + esc(peekTarget) + '" style="cursor:pointer"' : '') + '>' +
          '<div class="top">' +
            '<span class="id">' + esc(l.agent_id) + '</span>' +
            (flag ? '<span class="flag ' + flag + '">' + (flag === "dark" ? "dark" : "going stale") + '</span>' : '') +
            '<span class="st ' + esc(st) + '">' + esc(st) + '</span>' +
          '</div>' +
          (l.live && l.live.running ?
            '<div class="task" style="color:' + (l.live.state === "working" ? "#4ade80" : "#8891a5") + ';font-weight:600">' +
            (l.live.state === "working" ? "● " : "○ ") + esc(l.live.activity || l.live.state) + '</div>'
            : '') +
          (task ? '<div class="task">' + esc(task) +
            (taskAge ? ' <span class="taskage">&middot; ' + esc(taskAge) + '</span>' : '') + '</div>' : '') +
          '<div class="hb ' + laneHbClass(hb) + '">hb ' + esc(hbTxt) + '</div>' +
          (l.desired_state ? '<div class="desired">desired: ' + esc(l.desired_state) +
            (l.lane ? ' (' + esc(l.lane) + ')' : '') + '</div>' : '') +
          tasksHtml +
          (peekTarget ?
            '<div class="peek-box" data-lane="' + esc(peekTarget) + '"></div>'
            : '') +
        '</div>';
      }).join("");

      // Queued work for a lane with no live agent_status row (e.g. not yet
      // booted) still needs to be visible — nothing silently dropped.
      var orphanCards = Object.keys(tasksByLane).filter(function (ln) { return !consumed[ln]; })
        .map(function (ln) {
          return '<div class="qlane">@' + esc(ln) + ' (no live status)</div>' +
            tasksByLane[ln].map(renderQtask).join("");
        }).join("");

      box.innerHTML = laneCards + orphanCards;

      // Whole card is the peek toggle now (operator: remove the Peek button,
      // click anywhere on the card). Clicks inside an already-open peek-box
      // (to scroll/select the pane text) must NOT toggle it shut.
      box.querySelectorAll(".lane[data-peek]").forEach(function (card) {
        var session = card.getAttribute("data-peek");
        var peekBox = card.querySelector(".peek-box");
        card.addEventListener("click", function (ev) {
          if (ev.target.closest(".peek-box")) return;
          if (openPeek && openPeek.session === session) {
            peekBox.classList.remove("open");
            stopPeek();
            card.classList.remove("peeking");
            return;
          }
          box.querySelectorAll(".peek-box.open").forEach(function (b) { b.classList.remove("open"); });
          box.querySelectorAll(".lane.peeking").forEach(function (c) { c.classList.remove("peeking"); });
          startPeek(session, peekBox);
          card.classList.add("peeking");
        });
        if (session === reopenSession) {
          startPeek(session, peekBox);
          card.classList.add("peeking");
        }
      });
    });
  }

  // Known stages get a class for colour; anything else falls back to dim.
  var STAGES = { pending: 1, pushed: 1, in_review: 1, merged: 1, live: 1, blocked: 1 };

  // "Freshness vs main" without a commits-behind signal (no such column
  // exists yet — see build report): a workstream sitting in an in-flight
  // stage (not live, not already flagged blocked) for a long time is the
  // honest proxy we DO have — the pipeline stalled, not necessarily that
  // it's behind a specific commit count.
  var STALL_AFTER_S = 6 * 3600;
  var STALLABLE_STAGES = { pending: 1, pushed: 1, in_review: 1, merged: 1 };

  function loadDeploys() {
    return fetch("/api/deploys", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); $("deploys").innerHTML = UNAUTH_HINT; throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (rows) {
        var box = $("deploys");
        if (!rows.length) { box.innerHTML = '<div class="empty">No deploys tracked.</div>'; return; }
        box.innerHTML = rows.map(function (d) {
          var stage = (d.stage || "").toLowerCase();
          var stageClass = STAGES[stage] ? stage : "pending";
          var stalled = STALLABLE_STAGES[stage] && d.updated_age_s != null && d.updated_age_s > STALL_AFTER_S;
          var url = d.url
            ? '<a class="url" href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.url) + '</a>'
            : "";
          return '<div class="dep' + (stalled ? ' flag-stalled' : '') + '">' +
            '<div class="top">' +
              '<span class="ws">' + esc(d.workstream) + '</span>' +
              (stalled ? '<span class="stalled">stalled ' + esc(fmtAge(d.updated_age_s)) + '</span>' : '') +
              '<span class="stage ' + stageClass + '">' + esc(stage || "—") + '</span>' +
            '</div>' +
            '<div class="sub">' +
              (d.repo ? '<span class="repo">' + esc(d.repo) + '</span>' : '') +
              '<span class="age">' + esc(fmtAge(d.updated_age_s)) + '</span>' +
            '</div>' +
            (d.detail ? '<div class="detail">' + esc(d.detail) + '</div>' : '') +
            url +
          '</div>';
        }).join("");
      });
  }

  // SSE via fetch streaming (so the token stays a header, never the URL).
  function openStream() {
    if (es) { es.abort(); es = null; }
    var ctrl = new AbortController();
    es = ctrl;
    fetch("/api/stream", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (resp) {
        if (resp.status === 401) { setConn("down"); throw new Error("unauthorized"); }
        setConn("live");
        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buf = "";
        function pump() {
          return reader.read().then(function (res) {
            if (res.done) { setConn("down"); return; }
            buf += decoder.decode(res.value, { stream: true });
            var parts = buf.split("\n\n");
            buf = parts.pop();
            parts.forEach(function (chunk) {
              var line = chunk.split("\n").find(function (l) { return l.indexOf("data:") === 0; });
              if (!line) return;
              try { renderMessage(JSON.parse(line.slice(5).trim()), true); } catch (e) {}
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function () {
        if (!ctrl.signal.aborted) {
          setConn("down");
          setTimeout(function () { if (es === ctrl) openStream(); }, 3000);
        }
      });
  }

  // Called on load AND as the manual "Retry" button — token is optional in
  // both cases. Most devices (the operator's allowlisted phone/Macs) never
  // need it; it only matters when IP auth failed and a breakglass token is
  // being supplied to recover.
  function connect() {
    token = $("token").value.trim();
    if (token) { localStorage.setItem("console_token", token); }
    else { localStorage.removeItem("console_token"); }
    setConn("…");
    Promise.all([loadMessages(), loadLanes(), loadDeploys()])
      .then(function () { openStream(); })
      .catch(function () { setConn("down"); });
    if (lanesTimer) clearInterval(lanesTimer);
    lanesTimer = setInterval(function () {
      loadLanes().catch(function () {});
      loadDeploys().catch(function () {});
    }, 10000);
  }

  $("connect").addEventListener("click", connect);
  $("applyFilters").addEventListener("click", function () { loadMessages().catch(function () {}); });
  $("refreshLanes").addEventListener("click", function () { loadLanes().catch(function () {}); });
  $("refreshDeploys").addEventListener("click", function () { loadDeploys().catch(function () {}); });
  $("token").addEventListener("keydown", function (e) { if (e.key === "Enter") connect(); });

  if (token) { $("token").value = token; }
  connect(); // always attempt — IP-allowlisted devices need no token at all
})();
