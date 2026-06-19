// Fleet Console v1 SPA — dependency-light (vanilla JS, EventSource + fetch).
// Auth: operator token sent ONLY in the Authorization header (never the URL).
// SSE note: EventSource cannot set headers, so the live stream is opened via a
// fetch() ReadableStream reader, keeping the token header-only end to end.
(function () {
  "use strict";

  var token = sessionStorage.getItem("console_token") || "";
  var es = null;          // AbortController for the fetch-based stream
  var lanesTimer = null;
  var seen = {};          // de-dup message ids

  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return { Authorization: "Bearer " + token }; }

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

  function loadMessages() {
    var params = {
      limit: 80,
      thread: $("fThread").value.trim(),
      agent: $("fAgent").value.trim(),
    };
    return fetch(apiUrl("/api/messages", params), { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); throw new Error("unauthorized"); }
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

  function loadLanes() {
    return fetch("/api/lanes", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (rows) {
        var box = $("lanes");
        if (!rows.length) { box.innerHTML = '<div class="empty">No lanes.</div>'; return; }
        box.innerHTML = rows.map(function (l) {
          var st = (l.status || "unknown").toLowerCase();
          var hb = l.heartbeat_age_s;
          var hbTxt = hb == null ? "no heartbeat" : hb + "s ago";
          // "working on" = latest bus activity (current_task is just the boot string)
          var task = l.activity || l.current_task || "";
          var taskAge = l.activity != null && l.activity_age_s != null ? fmtAge(l.activity_age_s) : "";
          return '<div class="lane">' +
            '<div class="top">' +
              '<span class="id">' + esc(l.agent_id) + '</span>' +
              '<span class="st ' + esc(st) + '">' + esc(st) + '</span>' +
            '</div>' +
            (task ? '<div class="task">' + esc(task) +
              (taskAge ? ' <span class="taskage">&middot; ' + esc(taskAge) + '</span>' : '') + '</div>' : '') +
            '<div class="hb ' + laneHbClass(hb) + '">hb ' + esc(hbTxt) + '</div>' +
            (l.desired_state ? '<div class="desired">desired: ' + esc(l.desired_state) +
              (l.lane ? ' (' + esc(l.lane) + ')' : '') + '</div>' : '') +
          '</div>';
        }).join("");
      });
  }

  // Known stages get a class for colour; anything else falls back to dim.
  var STAGES = { pending: 1, pushed: 1, in_review: 1, merged: 1, live: 1, blocked: 1 };

  function loadDeploys() {
    return fetch("/api/deploys", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (rows) {
        var box = $("deploys");
        if (!rows.length) { box.innerHTML = '<div class="empty">No deploys tracked.</div>'; return; }
        box.innerHTML = rows.map(function (d) {
          var stage = (d.stage || "").toLowerCase();
          var stageClass = STAGES[stage] ? stage : "pending";
          var url = d.url
            ? '<a class="url" href="' + esc(d.url) + '" target="_blank" rel="noopener">' + esc(d.url) + '</a>'
            : "";
          return '<div class="dep">' +
            '<div class="top">' +
              '<span class="ws">' + esc(d.workstream) + '</span>' +
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

  function loadQueue() {
    return fetch("/api/queue", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { setConn("down"); throw new Error("unauthorized"); }
        return r.json();
      })
      .then(function (rows) {
        var box = $("queue");
        if (!rows.length) { box.innerHTML = '<div class="empty">No queued tasks.</div>'; return; }
        // group by lane, preserving the server's (lane, priority_rank) order
        var groups = {}, order = [];
        rows.forEach(function (t) {
          if (!groups[t.lane]) { groups[t.lane] = []; order.push(t.lane); }
          groups[t.lane].push(t);
        });
        box.innerHTML = order.map(function (lane) {
          var items = groups[lane].map(function (t, i) {
            var st = (t.status || "queued").toLowerCase();
            // SLA chip: elapsed-vs-budget. Only show once the task is started
            // and has an SLA. over_sla (server-computed) turns the chip red.
            var sla = "";
            if (t.sla_minutes != null && t.elapsed_min != null) {
              var over = !!t.over_sla;
              sla = '<span class="sla' + (over ? ' over' : '') + '">' +
                      (over ? '⚠ ' : '') + t.elapsed_min + 'm/' + t.sla_minutes + 'm' +
                    '</span>';
            } else if (t.sla_minutes != null) {
              sla = '<span class="sla idle">SLA ' + t.sla_minutes + 'm</span>';
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
          }).join("");
          return '<div class="qlane">@' + esc(lane) + '</div>' + items;
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

  function connect() {
    token = $("token").value.trim();
    if (!token) return;
    sessionStorage.setItem("console_token", token);
    setConn("…");
    Promise.all([loadMessages(), loadLanes(), loadDeploys(), loadQueue()])
      .then(function () { openStream(); })
      .catch(function () { setConn("down"); });
    if (lanesTimer) clearInterval(lanesTimer);
    lanesTimer = setInterval(function () {
      loadLanes().catch(function () {});
      loadDeploys().catch(function () {});
      loadQueue().catch(function () {});
    }, 10000);
  }

  $("connect").addEventListener("click", connect);
  $("applyFilters").addEventListener("click", function () { loadMessages().catch(function () {}); });
  $("refreshLanes").addEventListener("click", function () { loadLanes().catch(function () {}); });
  $("refreshDeploys").addEventListener("click", function () { loadDeploys().catch(function () {}); });
  $("refreshQueue").addEventListener("click", function () { loadQueue().catch(function () {}); });
  $("token").addEventListener("keydown", function (e) { if (e.key === "Enter") connect(); });

  if (token) { $("token").value = token; connect(); }
})();
