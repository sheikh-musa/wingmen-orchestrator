/* office.js — fc-v74 — the fleet "virtual office" (op#26594, Musa: "turn our
 * fleet console into a 2d virtual office of employees that i can interact with").
 *
 * A self-contained, no-dependency overlay on the Fleet view. It reads ONLY
 * /api/fleet (the same snapshot fleet.js already polls — no new backend, table
 * or endpoint) and paints a 16-bit, Gather/Habbo-style office: one desk + one
 * character per live agent, grouped into rooms by project. Status drives the
 * sprite (typing = working, dozing = idle, red ! = wedged/stale); a speech
 * bubble carries the first ~60 chars of current_task. Tap a character for a
 * side panel with its recent inbox subjects and a copyable bus_send.py command
 * (the console has no bus write path, so we never POST — op#26594 hard rule).
 *
 * Added as a view INSIDE fleet.html (launch button injected below) so it ships
 * inside the gated fleet shell; existing tabs/sections are untouched.
 */
(function () {
  "use strict";
  if (window.__officeBooted) return;
  window.__officeBooted = true;

  var TAU = Math.PI * 2;
  var POLL_MS = 6000;         // refresh the snapshot while the office is open
  var TASK_CHARS = 60;        // speech-bubble truncation (brief)

  // ---- status → sprite mood -------------------------------------------------
  // Derive from the /api/fleet fields fleet.js already exposes. `flagged` or a
  // stale/dead heartbeat => wedged; live.state (or bucket) === working => typing;
  // otherwise dozing. Kept deliberately forgiving: unknown => dozing, never crash.
  var STALE_S = 1800; // 30 min without a heartbeat reads as "away / possibly wedged"
  function moodOf(a) {
    if (a.flagged) return "wedged";
    // Coordinators are long-lived desks (cai, finance, …) that idle for hours by
    // design — the lane staleness rule must not paint them red. Only an explicit
    // flag (auth mismatch) wedges a coordinator.
    if (a.__coord) return isWorking(a) ? "working" : "idle";
    var hb = a.heartbeat_age_s;
    if (typeof hb === "number" && hb > STALE_S && !isWorking(a)) return "wedged";
    if (a.status === "blocked" || a.status === "stale") return "wedged";
    if (isWorking(a)) return "working";
    return "idle";
  }
  function isWorking(a) {
    var live = a.live;
    if (live && typeof live === "object" && live.state) return live.state === "working";
    if (a.bucket) return a.bucket === "working";
    return a.status === "working";
  }

  // ---- project / room assignment -------------------------------------------
  // Corner offices + library are the named seats from the brief; everything else
  // is grouped by a project key derived from the agent id, so new lanes slot in
  // without a code change.
  function roomKeyFor(a) {
    var id = (a.agent_id || a.base_agent_id || "").toLowerCase();
    if (id === "orch-console") return "nazim";
    if (id === "cc-orchestrator") return "hub";
    if (id === "cai") return "library";
    if (a.__coord) return "fleetops";                 // finance / sre / quality
    if (id.indexOf("irsyad") >= 0) return "irsyad";
    if (id.indexOf("cosem") >= 0) return "cosem";
    if (id.indexOf("substrate") >= 0 || id.indexOf("second-brain") >= 0) return "substrate";
    return "ventures";                                 // angullia, coffeemedia, oeh, …
  }
  var ROOMS = {
    nazim:     { title: "Nazim · Corner Office", kind: "corner" },
    hub:       { title: "Hub · Corner Office",   kind: "corner" },
    library:   { title: "CAI · Library",          kind: "library" },
    fleetops:  { title: "Fleet Ops",              kind: "ops" },
    irsyad:    { title: "Irsyad",                 kind: "team" },
    cosem:     { title: "COSEM",                  kind: "team" },
    substrate: { title: "Substrate",              kind: "team" },
    ventures:  { title: "Ventures",               kind: "team" }
  };
  // left-to-right / top-to-bottom placement order on the floor plan
  var ROOM_ORDER = ["nazim", "hub", "cosem", "irsyad", "substrate", "fleetops", "ventures", "library"];

  // model family → desk accent (warm, distinguishable in both themes)
  function accentFor(a) {
    var m = (a.model || a.family || "").toLowerCase();
    if (m.indexOf("opus") >= 0) return "#c88cff";
    if (m.indexOf("sonnet") >= 0) return "#6fb3ff";
    if (m.indexOf("haiku") >= 0) return "#5fd0b0";
    if (m.indexOf("fable") >= 0) return "#ff9ec4";
    if (m.indexOf("glm") >= 0) return "#ffc36b";
    return "#9aa6c0";
  }

  // ---- palette (light + dark via prefers-color-scheme) ----------------------
  function palette() {
    var dark = !window.matchMedia || window.matchMedia("(prefers-color-scheme: dark)").matches;
    return dark ? {
      sky: "#0b0e15", floor: "#1b2230", floorAlt: "#202836", wall: "#2b3342",
      rug: "#2a3340", deskTop: "#3a4252", deskLeg: "#272e3b", roomLine: "rgba(255,255,255,.08)",
      roomLbl: "#aeb7c8", monitor: "#0e1420", txt: "#e8ebf1", dim: "#8a93a6",
      bubbleBg: "#f4f6fb", bubbleTxt: "#15171d", bubbleLine: "rgba(0,0,0,.15)",
      shadow: "rgba(0,0,0,.35)", good: "#37d39a", warn: "#f6c453", bad: "#fb7185"
    } : {
      sky: "#eef1f7", floor: "#e6ddce", floorAlt: "#ded3c0", wall: "#cdbfa6",
      rug: "#d8cbb4", deskTop: "#b89b74", deskLeg: "#9c7f58", roomLine: "rgba(60,45,20,.16)",
      roomLbl: "#6b5a3c", monitor: "#2a3344", txt: "#2a2418", dim: "#7b6f58",
      bubbleBg: "#1f2430", bubbleTxt: "#f4f6fb", bubbleLine: "rgba(0,0,0,.25)",
      shadow: "rgba(70,50,20,.22)", good: "#1f9e77", warn: "#c9962a", bad: "#d44a60"
    };
  }

  // ---- geometry -------------------------------------------------------------
  var TILE = 56;              // world px per tile
  var DESK_W = 2, DESK_H = 2; // tiles per workstation cell
  var COLS_PER_ROOM = 3;      // desks per row inside a room

  function layout(agents) {
    // bucket agents into rooms
    var byRoom = {};
    agents.forEach(function (a) {
      var k = roomKeyFor(a);
      (byRoom[k] || (byRoom[k] = [])).push(a);
    });
    var rooms = [];
    var planCols = 2;         // rooms per row on the floor plan
    var gap = 1;              // tile gutter between rooms
    var cursorX = 0, cursorY = 0, rowH = 0, col = 0;
    ROOM_ORDER.forEach(function (key) {
      var occ = byRoom[key];
      if (!occ || !occ.length) return;
      var cols = Math.min(COLS_PER_ROOM, occ.length);
      var rows = Math.ceil(occ.length / cols);
      var wTiles = cols * (DESK_W + 1) + 1;           // +aisle +wall
      var hTiles = rows * (DESK_H + 2) + 2;           // +label +aisle
      var rx = cursorX, ry = cursorY;
      var seats = occ.map(function (a, i) {
        var cc = i % cols, rr = Math.floor(i / cols);
        return {
          agent: a,
          tx: rx + 1 + cc * (DESK_W + 1),
          ty: ry + 2 + rr * (DESK_H + 2)
        };
      });
      rooms.push({ key: key, meta: ROOMS[key], x: rx, y: ry, w: wTiles, h: hTiles, seats: seats });
      rowH = Math.max(rowH, hTiles);
      col++;
      if (col >= planCols) { col = 0; cursorX = 0; cursorY += rowH + gap; rowH = 0; }
      else { cursorX += wTiles + gap; }
    });
    // world bounds
    var maxX = 0, maxY = 0;
    rooms.forEach(function (r) { maxX = Math.max(maxX, r.x + r.w); maxY = Math.max(maxY, r.y + r.h); });
    return { rooms: rooms, worldW: maxX * TILE, worldH: maxY * TILE };
  }

  // ---- state ----------------------------------------------------------------
  var S = {
    open: false, data: null, plan: null, seatsFlat: [],
    cam: { x: 0, y: 0, z: 1 }, min: 0.3, max: 2.4,
    t0: performance.now(), raf: 0, timer: 0, selected: null,
    drag: null, pointers: {}, pinch: null, fitted: false
  };

  // ---- DOM scaffold ---------------------------------------------------------
  var el = {};
  function buildDOM() {
    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var btn = document.createElement("button");
    btn.id = "officeFab";
    btn.title = "Open the virtual office";
    btn.innerHTML = '<span class="e">🏢</span><span class="t">Office</span>';
    btn.addEventListener("click", open);
    document.body.appendChild(btn);
    el.fab = btn;

    var ov = document.createElement("div");
    ov.id = "officeOverlay";
    ov.innerHTML =
      '<div id="officeBar">' +
        '<span class="ttl"><span class="e">🏢</span> Fleet Office</span>' +
        '<span id="officeCount" class="count"></span>' +
        '<span id="officeLegend" class="legend"></span>' +
        '<button id="officeFit" class="ob" title="Fit to screen">⤢ Fit</button>' +
        '<button id="officeClose" class="ob x" title="Close">✕</button>' +
      '</div>' +
      '<canvas id="officeCanvas"></canvas>' +
      '<div id="officePanel" class="closed"></div>' +
      '<div id="officeHint">drag to pan · scroll / pinch to zoom · tap a character</div>';
    document.body.appendChild(ov);
    el.ov = ov;
    el.canvas = ov.querySelector("#officeCanvas");
    el.ctx = el.canvas.getContext("2d");
    el.count = ov.querySelector("#officeCount");
    el.legend = ov.querySelector("#officeLegend");
    el.panel = ov.querySelector("#officePanel");
    el.hint = ov.querySelector("#officeHint");

    ov.querySelector("#officeClose").addEventListener("click", close);
    ov.querySelector("#officeFit").addEventListener("click", function () { fit(); });
    el.legend.innerHTML =
      '<i class="lg work"></i>working <i class="lg idle"></i>idle <i class="lg wedge"></i>wedged';

    wireCanvas();
    window.addEventListener("keydown", function (e) { if (e.key === "Escape" && S.open) close(); });
    if (window.matchMedia) {
      try { window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", function () { if (S.open) draw(); }); } catch (e) {}
    }
  }

  // ---- open / close / data --------------------------------------------------
  function open() {
    S.open = true;
    el.ov.classList.add("show");
    document.body.classList.add("office-lock");
    resize();
    refresh(true);
    S.timer = setInterval(function () { refresh(false); }, POLL_MS);
    loop();
  }
  function close() {
    S.open = false;
    el.ov.classList.remove("show");
    document.body.classList.remove("office-lock");
    clearInterval(S.timer); S.timer = 0;
    cancelAnimationFrame(S.raf); S.raf = 0;
    closePanel();
  }

  // Auth mirrors fleet.js exactly: IP-allowlist-first, with a breakglass token
  // from localStorage('console_token') riding Authorization when off-tailnet.
  function authHeaders() {
    var tok = "";
    try { tok = localStorage.getItem("console_token") || ""; } catch (e) {}
    var h = { "Accept": "application/json" };
    if (tok) h.Authorization = "Bearer " + tok;
    return h;
  }
  function refresh(first) {
    fetch("/api/fleet", { headers: authHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) ingest(d, first); })
      .catch(function () { /* keep last good frame; the office never blanks */ });
  }

  function ingest(d, first) {
    S.data = d;
    var agents = [];
    (d.lanes || []).forEach(function (l) { agents.push(l); });
    (d.coordinators || []).forEach(function (c) {
      // normalise a coordinator row into the same shape the renderer expects
      var fresh = typeof c.activity_age_s === "number" && c.activity_age_s < 180;
      agents.push({
        agent_id: c.agent_id, base_agent_id: c.agent_id, display_name: c.short || c.agent_id,
        status: fresh ? "working" : "idle", current_task: c.role_label || c.activity || "",
        model: c.model, heartbeat_age_s: c.last_seen_s, activity_age_s: c.activity_age_s,
        live: { state: fresh ? "working" : "idle" }, bucket: null,
        flagged: !!c.auth_mismatch, __coord: true, __role: c.role_label
      });
    });
    S.plan = layout(agents);
    S.seatsFlat = [];
    S.plan.rooms.forEach(function (r) { r.seats.forEach(function (s) { S.seatsFlat.push(s); }); });
    var working = agents.filter(function (a) { return moodOf(a) === "working"; }).length;
    el.count.textContent = agents.length + " agents · " + working + " working · " + S.plan.rooms.length + " rooms";
    if (first || !S.fitted) { fit(); S.fitted = true; }
    // keep an open panel in sync with the fresh snapshot
    if (S.selected) {
      var still = S.seatsFlat.filter(function (s) { return idOf(s.agent) === S.selected; })[0];
      if (still) renderPanel(still.agent); else closePanel();
    }
  }
  function idOf(a) { return (a.agent_id || a.base_agent_id || "").toLowerCase(); }

  // ---- camera ---------------------------------------------------------------
  function fit() {
    if (!S.plan) return;
    var r = el.canvas.getBoundingClientRect();
    var pad = 40;
    var zx = (r.width - pad * 2) / Math.max(1, S.plan.worldW);
    var zy = (r.height - pad * 2) / Math.max(1, S.plan.worldH);
    var z = Math.max(S.min, Math.min(S.max, Math.min(zx, zy)));
    S.cam.z = z;
    S.cam.x = (r.width - S.plan.worldW * z) / 2;
    S.cam.y = (r.height - S.plan.worldH * z) / 2;
  }
  function worldToScreen(wx, wy) { return { x: wx * S.cam.z + S.cam.x, y: wy * S.cam.z + S.cam.y }; }
  function screenToWorld(sx, sy) { return { x: (sx - S.cam.x) / S.cam.z, y: (sy - S.cam.y) / S.cam.z }; }

  // ---- canvas sizing --------------------------------------------------------
  function resize() {
    var r = el.canvas.getBoundingClientRect();
    var dpr = Math.min(window.devicePixelRatio || 1, 2.5);
    el.canvas.width = Math.round(r.width * dpr);
    el.canvas.height = Math.round(r.height * dpr);
    el.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener("resize", function () { if (S.open) { resize(); draw(); } });

  // ---- input ----------------------------------------------------------------
  function wireCanvas() {
    var c = el.canvas;
    c.addEventListener("pointerdown", function (e) {
      c.setPointerCapture(e.pointerId);
      S.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var n = Object.keys(S.pointers).length;
      if (n === 1) { S.drag = { x: e.clientX, y: e.clientY, moved: 0, camx: S.cam.x, camy: S.cam.y }; }
      else if (n === 2) { S.pinch = startPinch(); S.drag = null; }
    });
    c.addEventListener("pointermove", function (e) {
      if (!S.pointers[e.pointerId]) return;
      S.pointers[e.pointerId] = { x: e.clientX, y: e.clientY };
      var n = Object.keys(S.pointers).length;
      if (n >= 2 && S.pinch) { doPinch(); }
      else if (S.drag) {
        var dx = e.clientX - S.drag.x, dy = e.clientY - S.drag.y;
        S.drag.moved += Math.abs(dx) + Math.abs(dy);
        S.cam.x = S.drag.camx + dx; S.cam.y = S.drag.camy + dy;
      }
    });
    function up(e) {
      var wasDrag = S.drag;
      delete S.pointers[e.pointerId];
      if (S.pinch && Object.keys(S.pointers).length < 2) S.pinch = null;
      if (wasDrag && wasDrag.moved < 6 && Object.keys(S.pointers).length === 0) {
        hitTest(e.clientX, e.clientY);
      }
      if (Object.keys(S.pointers).length === 0) S.drag = null;
    }
    c.addEventListener("pointerup", up);
    c.addEventListener("pointercancel", up);
    c.addEventListener("wheel", function (e) {
      e.preventDefault();
      var r = c.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.pow(1.0015, -e.deltaY));
    }, { passive: false });
  }
  function startPinch() {
    var ks = Object.keys(S.pointers); var a = S.pointers[ks[0]], b = S.pointers[ks[1]];
    return { d: dist(a, b), z: S.cam.z, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, camx: S.cam.x, camy: S.cam.y };
  }
  function doPinch() {
    var ks = Object.keys(S.pointers); var a = S.pointers[ks[0]], b = S.pointers[ks[1]];
    var d = dist(a, b); var scale = d / Math.max(1, S.pinch.d);
    var r = el.canvas.getBoundingClientRect();
    var z = Math.max(S.min, Math.min(S.max, S.pinch.z * scale));
    var cx = S.pinch.cx - r.left, cy = S.pinch.cy - r.top;
    // keep the pinch midpoint anchored in world space
    var w = { x: (cx - S.pinch.camx) / S.pinch.z, y: (cy - S.pinch.camy) / S.pinch.z };
    S.cam.z = z; S.cam.x = cx - w.x * z; S.cam.y = cy - w.y * z;
  }
  function dist(a, b) { var dx = a.x - b.x, dy = a.y - b.y; return Math.sqrt(dx * dx + dy * dy); }
  function zoomAt(sx, sy, factor) {
    var z = Math.max(S.min, Math.min(S.max, S.cam.z * factor));
    var w = screenToWorld(sx, sy);
    S.cam.z = z; S.cam.x = sx - w.x * z; S.cam.y = sy - w.y * z;
    draw();
  }

  function hitTest(clientX, clientY) {
    var r = el.canvas.getBoundingClientRect();
    var w = screenToWorld(clientX - r.left, clientY - r.top);
    var best = null, bestD = 1e9;
    S.seatsFlat.forEach(function (s) {
      var cx = (s.tx + DESK_W / 2) * TILE, cy = (s.ty + DESK_H / 2) * TILE;
      var d = Math.abs(w.x - cx) + Math.abs(w.y - cy);
      if (d < bestD) { bestD = d; best = s; }
    });
    if (best && bestD < TILE * 1.4) { S.selected = idOf(best.agent); renderPanel(best.agent); }
    else closePanel();
  }

  // ---- side panel -----------------------------------------------------------
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function inboxFor(a) {
    var db = (S.data && S.data.drain_board) || [];
    var id = idOf(a), base = (a.base_agent_id || "").toLowerCase();
    var hit = db.filter(function (e) {
      var g = (e.agent || "").toLowerCase();
      return g === id || g === base;
    })[0];
    return (hit && hit.items) ? hit.items.slice(0, 3) : [];
  }

  function renderPanel(a) {
    S.selected = idOf(a);
    var mood = moodOf(a), acc = accentFor(a);
    var moodLbl = { working: "working", idle: "idle / dozing", wedged: "wedged / stale" }[mood];
    var task = (a.current_task || a.__role || "").trim();
    var items = inboxFor(a);
    var inbox = items.length
      ? items.map(function (m) {
          return '<li class="msg p' + esc(m.priority || "") + '">' +
            '<span class="from">' + esc(m.from || "?") + '</span>' +
            '<span class="subj">' + esc(trunc(m.subject || "", 130)) + '</span></li>';
        }).join("")
      : '<li class="none">no recent inbox items in the console feed</li>';

    var cmd = 'scripts/bus_send.py --to ' + (a.agent_id || "") +
      ' --type update --subject "<subject>" --priority P2';

    el.panel.innerHTML =
      '<div class="ph" style="--acc:' + acc + '">' +
        '<span class="dot ' + mood + '"></span>' +
        '<span class="pid">' + esc(a.display_name || a.agent_id) + '</span>' +
        '<button class="pclose" title="Close">✕</button>' +
      '</div>' +
      '<div class="meta">' +
        '<span class="chip">' + esc(a.agent_id) + '</span>' +
        '<span class="chip">' + esc((ROOMS[roomKeyFor(a)] || {}).title || "—") + '</span>' +
        (a.model ? '<span class="chip model">' + esc(a.model) + '</span>' : "") +
        '<span class="chip mood ' + mood + '">' + moodLbl + '</span>' +
      '</div>' +
      '<div class="sect"><div class="lbl">current task</div>' +
        '<div class="task">' + (task ? esc(task) : '<span class="none">—</span>') + '</div></div>' +
      '<div class="sect"><div class="lbl">recent inbox · subjects</div>' +
        '<ul class="inbox">' + inbox + '</ul></div>' +
      '<div class="sect"><div class="lbl">send a message</div>' +
        '<div class="sendnote">The console has no bus write path — copy &amp; run this from <code>~/wingmen/orchestrator</code> (with the venv):</div>' +
        '<div class="cmdbox"><code id="officeCmd">' + esc(cmd) + '</code>' +
        '<button id="officeCopy" class="copy">Copy</button></div>' +
      '</div>';

    el.panel.classList.remove("closed");
    el.panel.querySelector(".pclose").addEventListener("click", closePanel);
    var copy = el.panel.querySelector("#officeCopy");
    copy.addEventListener("click", function () {
      var txt = el.panel.querySelector("#officeCmd").textContent;
      var done = function () { copy.textContent = "Copied ✓"; setTimeout(function () { copy.textContent = "Copy"; }, 1400); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, done);
      else { try { var t = document.createElement("textarea"); t.value = txt; document.body.appendChild(t); t.select(); document.execCommand("copy"); document.body.removeChild(t); done(); } catch (e) {} }
    });
  }
  function closePanel() { S.selected = null; el.panel.classList.add("closed"); }
  function trunc(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  // ---- render loop ----------------------------------------------------------
  function loop() { if (!S.open) return; draw(); S.raf = requestAnimationFrame(loop); }

  function draw() {
    if (!S.plan) return;
    var ctx = el.ctx, P = palette();
    var r = el.canvas.getBoundingClientRect();
    var t = (performance.now() - S.t0) / 1000;
    ctx.save();
    ctx.clearRect(0, 0, r.width, r.height);
    ctx.fillStyle = P.sky; ctx.fillRect(0, 0, r.width, r.height);
    ctx.translate(S.cam.x, S.cam.y); ctx.scale(S.cam.z, S.cam.z);

    S.plan.rooms.forEach(function (room) { drawRoom(ctx, room, P); });
    // characters drawn after all rooms so bubbles overlap neighbouring walls
    S.seatsFlat.forEach(function (s) { drawWorkstation(ctx, s, P, t); });
    S.seatsFlat.forEach(function (s) { drawAgent(ctx, s, P, t); });

    ctx.restore();
  }

  function drawRoom(ctx, room, P) {
    var x = room.x * TILE, y = room.y * TILE, w = room.w * TILE, h = room.h * TILE;
    // floor with a checker rug
    ctx.fillStyle = P.floor; roundRect(ctx, x, y, w, h, 10); ctx.fill();
    ctx.save(); roundRect(ctx, x, y, w, h, 10); ctx.clip();
    ctx.fillStyle = P.floorAlt;
    for (var gy = 0; gy < room.h; gy++) for (var gx = 0; gx < room.w; gx++) {
      if ((gx + gy) % 2 === 0) ctx.fillRect(x + gx * TILE, y + gy * TILE, TILE, TILE);
    }
    // special room tints
    if (room.meta.kind === "library") { ctx.fillStyle = "rgba(120,90,200,.10)"; ctx.fillRect(x, y, w, h); }
    if (room.meta.kind === "corner")  { ctx.fillStyle = "rgba(255,190,90,.09)"; ctx.fillRect(x, y, w, h); }
    ctx.restore();
    // wall outline
    ctx.strokeStyle = P.roomLine; ctx.lineWidth = 2; roundRect(ctx, x, y, w, h, 10); ctx.stroke();
    // door notch (bottom-centre)
    ctx.strokeStyle = P.sky; ctx.lineWidth = 4;
    ctx.beginPath(); ctx.moveTo(x + w / 2 - 16, y + h); ctx.lineTo(x + w / 2 + 16, y + h); ctx.stroke();
    // label plate
    ctx.fillStyle = P.roomLbl;
    ctx.font = "600 13px ui-monospace, SFMono-Regular, Menlo, monospace";
    ctx.textBaseline = "middle"; ctx.textAlign = "left";
    ctx.fillText(labelIcon(room.meta.kind) + " " + room.meta.title.toUpperCase(), x + 12, y + 16);
  }
  function labelIcon(kind) {
    return kind === "corner" ? "★" : kind === "library" ? "❖" : kind === "ops" ? "⚙" : "▦";
  }

  function drawWorkstation(ctx, s, P, t) {
    var dx = s.tx * TILE, dy = s.ty * TILE, dw = DESK_W * TILE, dh = DESK_H * TILE;
    var acc = accentFor(s.agent), mood = moodOf(s.agent);
    // chair shadow
    ctx.fillStyle = P.shadow;
    ctx.beginPath(); ctx.ellipse(dx + dw / 2, dy + dh - 6, dw * 0.33, 7, 0, 0, TAU); ctx.fill();
    // desk top
    ctx.fillStyle = P.deskTop; roundRect(ctx, dx + 6, dy + 10, dw - 12, dh * 0.42, 6); ctx.fill();
    ctx.fillStyle = P.deskLeg; ctx.fillRect(dx + 10, dy + 10 + dh * 0.42, 5, 14); ctx.fillRect(dx + dw - 15, dy + 10 + dh * 0.42, 5, 14);
    // monitor — glows when working, dim when idle, red-rimmed when wedged
    var mx = dx + dw / 2 - 16, my = dy + 6, mw = 32, mh = 20;
    ctx.fillStyle = P.deskLeg; ctx.fillRect(mx + mw / 2 - 3, my + mh, 6, 6);
    ctx.fillStyle = P.monitor; roundRect(ctx, mx, my, mw, mh, 3); ctx.fill();
    var glow = mood === "working" ? (0.55 + 0.45 * Math.abs(Math.sin(t * 3 + s.tx))) : mood === "idle" ? 0.18 : 0.3;
    ctx.globalAlpha = glow;
    ctx.fillStyle = mood === "wedged" ? P.bad : acc;
    roundRect(ctx, mx + 3, my + 3, mw - 6, mh - 6, 2); ctx.fill();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = mood === "wedged" ? P.bad : "rgba(255,255,255,.12)";
    ctx.lineWidth = mood === "wedged" ? 2 : 1; roundRect(ctx, mx, my, mw, mh, 3); ctx.stroke();
  }

  function drawAgent(ctx, s, P, t) {
    var a = s.agent, mood = moodOf(a), acc = accentFor(a);
    var cx = (s.tx + DESK_W / 2) * TILE;
    var cy = (s.ty + DESK_H) * TILE - 10;          // seated in front of the desk
    var bob = mood === "working" ? Math.sin(t * 6 + s.tx * 1.7) * 1.4 : mood === "idle" ? Math.sin(t * 1.3 + s.ty) * 0.8 : 0;
    cy += bob;
    var skin = "#e9b892", hair = "#3a2f2a";
    // body
    ctx.fillStyle = acc; roundRect(ctx, cx - 11, cy - 6, 22, 20, 6); ctx.fill();
    // arms: typing motion when working
    ctx.strokeStyle = acc; ctx.lineWidth = 5; ctx.lineCap = "round";
    var type = mood === "working" ? Math.sin(t * 12 + s.tx) * 3 : 0;
    ctx.beginPath(); ctx.moveTo(cx - 8, cy); ctx.lineTo(cx - 12, cy + 8 + type); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx + 8, cy); ctx.lineTo(cx + 12, cy + 8 - type); ctx.stroke();
    // head
    ctx.fillStyle = skin; ctx.beginPath(); ctx.arc(cx, cy - 15, 9, 0, TAU); ctx.fill();
    ctx.fillStyle = hair; ctx.beginPath(); ctx.arc(cx, cy - 18, 9, Math.PI, TAU); ctx.fill();
    // name tag under the chair
    if (S.cam.z > 0.5) {
      ctx.fillStyle = P.dim; ctx.textAlign = "center"; ctx.textBaseline = "top";
      ctx.font = "10px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.fillText(shortName(a), cx, cy + 18);
    }
    // selection ring
    if (idOf(a) === S.selected) {
      ctx.strokeStyle = acc; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(cx, cy - 2, 26, 0, TAU); ctx.stroke();
    }
    // mood marker + speech bubble
    if (mood === "idle") drawZzz(ctx, cx + 12, cy - 24, t, P);
    else if (mood === "wedged") drawBang(ctx, cx + 13, cy - 26, t, P);
    if (S.cam.z > 0.62) drawBubble(ctx, a, cx, cy - 30, P, mood);
  }

  function drawBubble(ctx, a, cx, by, P, mood) {
    var task = (a.current_task || a.__role || "").replace(/\s+/g, " ").trim();
    if (!task) return;
    task = trunc(task, TASK_CHARS);
    ctx.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
    var pad = 7, tw = Math.min(ctx.measureText(task).width, 240), bw = tw + pad * 2, bh = 22;
    var bx = cx - bw / 2;
    ctx.fillStyle = P.bubbleBg; ctx.strokeStyle = mood === "wedged" ? P.bad : P.bubbleLine;
    ctx.lineWidth = mood === "wedged" ? 1.6 : 1;
    roundRect(ctx, bx, by - bh, bw, bh, 7); ctx.fill(); ctx.stroke();
    // tail
    ctx.fillStyle = P.bubbleBg; ctx.beginPath();
    ctx.moveTo(cx - 5, by); ctx.lineTo(cx + 5, by); ctx.lineTo(cx, by + 6); ctx.closePath(); ctx.fill();
    ctx.fillStyle = P.bubbleTxt; ctx.textAlign = "left"; ctx.textBaseline = "middle";
    ctx.save(); roundRect(ctx, bx, by - bh, bw, bh, 7); ctx.clip();
    ctx.fillText(task, bx + pad, by - bh / 2); ctx.restore();
  }
  function drawZzz(ctx, x, y, t, P) {
    ctx.fillStyle = P.dim; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    var f = (Math.sin(t * 1.5) + 1) / 2;
    ctx.globalAlpha = 0.5 + 0.5 * f;
    ctx.font = "bold 10px ui-monospace, monospace"; ctx.fillText("z", x, y - f * 4);
    ctx.font = "bold 13px ui-monospace, monospace"; ctx.fillText("Z", x + 5, y - 7 - f * 5);
    ctx.globalAlpha = 1;
  }
  function drawBang(ctx, x, y, t, P) {
    var p = 0.6 + 0.4 * Math.abs(Math.sin(t * 5));
    ctx.globalAlpha = p; ctx.fillStyle = P.bad;
    ctx.beginPath(); ctx.arc(x, y, 9, 0, TAU); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = "bold 13px ui-monospace, monospace"; ctx.fillText("!", x, y + 1);
    ctx.globalAlpha = 1;
  }

  function shortName(a) {
    var id = a.display_name || a.agent_id || "";
    return String(id).replace(/^cc-/, "").replace(/-1$/, "").slice(0, 16);
  }

  function roundRect(ctx, x, y, w, h, r) {
    r = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  // ---- styles ---------------------------------------------------------------
  var CSS =
    '#officeFab{position:fixed;right:14px;bottom:calc(14px + env(safe-area-inset-bottom));z-index:70;' +
      'display:flex;align-items:center;gap:7px;padding:10px 14px;border-radius:999px;cursor:pointer;' +
      'font:700 13px/1 -apple-system,system-ui,sans-serif;color:#fff;border:1px solid rgba(255,255,255,.18);' +
      'background:linear-gradient(135deg,#8aa6ff,#7b5cff);box-shadow:0 6px 20px rgba(80,70,200,.4);}' +
    '#officeFab .e{font-size:16px;}' +
    '#officeFab:active{transform:scale(.96);}' +
    'body.office-lock{overflow:hidden;}' +
    '#officeOverlay{position:fixed;inset:0;z-index:200;display:none;flex-direction:column;' +
      'background:#0b0e15;color:#e8ebf1;font:14px/1.5 -apple-system,system-ui,sans-serif;}' +
    '#officeOverlay.show{display:flex;}' +
    '#officeBar{flex:none;display:flex;align-items:center;gap:10px;flex-wrap:nowrap;padding:calc(10px + env(safe-area-inset-top)) 14px 10px;' +
      'border-bottom:1px solid rgba(255,255,255,.08);background:rgba(10,12,18,.9);backdrop-filter:blur(8px);}' +
    '#officeBar .ttl{flex:none;font-weight:800;letter-spacing:-.01em;display:flex;align-items:center;gap:6px;white-space:nowrap;}' +
    '#officeBar .count{flex:none;font:11px/1 ui-monospace,monospace;color:#8a93a6;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}' +
    '#officeBar .legend{margin-left:auto;flex:none;display:flex;align-items:center;gap:6px;font:10px/1 ui-monospace,monospace;color:#8a93a6;white-space:nowrap;}' +
    '#officeBar .legend .lg{width:9px;height:9px;border-radius:50%;display:inline-block;margin-left:8px;}' +
    '#officeBar .legend .lg.work{background:#37d39a;} .legend .lg.idle{background:#8a93a6;} .legend .lg.wedge{background:#fb7185;}' +
    '#officeBar .ob{flex:none;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12);color:#e8ebf1;' +
      'border-radius:9px;padding:7px 11px;font:600 12px/1 -apple-system,system-ui;cursor:pointer;}' +
    '#officeBar .ob.x{font-size:14px;padding:7px 12px;}' +
    '#officeBar .ob:active{transform:scale(.95);}' +
    '#officeCanvas{flex:1;width:100%;min-height:0;touch-action:none;display:block;cursor:grab;}' +
    '#officeCanvas:active{cursor:grabbing;}' +
    '#officeHint{position:absolute;left:50%;transform:translateX(-50%);bottom:calc(12px + env(safe-area-inset-bottom));' +
      'font:10px/1 ui-monospace,monospace;color:#8a93a6;background:rgba(10,12,18,.7);padding:6px 11px;border-radius:999px;' +
      'border:1px solid rgba(255,255,255,.08);pointer-events:none;}' +
    '#officePanel{position:absolute;top:0;right:0;bottom:0;width:min(360px,86vw);z-index:210;overflow-y:auto;' +
      'background:#12161e;border-left:1px solid rgba(255,255,255,.1);padding:0 0 24px;transition:transform .22s ease;' +
      'box-shadow:-12px 0 30px rgba(0,0,0,.4);padding-top:env(safe-area-inset-top);}' +
    '#officePanel.closed{transform:translateX(104%);}' +
    '#officePanel .ph{position:sticky;top:0;display:flex;align-items:center;gap:9px;padding:14px;background:#12161e;' +
      'border-bottom:1px solid rgba(255,255,255,.08);border-top:3px solid var(--acc,#8aa6ff);}' +
    '#officePanel .ph .dot{width:11px;height:11px;border-radius:50%;flex:none;}' +
    '#officePanel .ph .dot.working{background:#37d39a;box-shadow:0 0 8px #37d39a;} .ph .dot.idle{background:#8a93a6;} .ph .dot.wedged{background:#fb7185;box-shadow:0 0 8px #fb7185;}' +
    '#officePanel .ph .pid{font-weight:800;font-size:15px;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}' +
    '#officePanel .pclose{background:none;border:none;color:#8a93a6;font-size:16px;cursor:pointer;padding:4px;}' +
    '#officePanel .meta{display:flex;flex-wrap:wrap;gap:6px;padding:12px 14px 4px;}' +
    '#officePanel .chip{font:10px/1.4 ui-monospace,monospace;padding:4px 8px;border-radius:999px;' +
      'background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.1);color:#aeb7c8;}' +
    '#officePanel .chip.model{color:#c88cff;} .chip.mood.working{color:#37d39a;} .chip.mood.idle{color:#aeb7c8;} .chip.mood.wedged{color:#fb7185;}' +
    '#officePanel .sect{padding:12px 14px;border-top:1px solid rgba(255,255,255,.05);}' +
    '#officePanel .lbl{font:9px/1 ui-monospace,monospace;text-transform:uppercase;letter-spacing:.08em;color:#5c6472;margin-bottom:8px;}' +
    '#officePanel .task{font:13px/1.5 -apple-system,system-ui;color:#e8ebf1;word-break:break-word;}' +
    '#officePanel .none{color:#5c6472;}' +
    '#officePanel ul.inbox{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px;}' +
    '#officePanel ul.inbox .msg{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border-radius:9px;' +
      'background:rgba(255,255,255,.04);border:1px solid rgba(255,255,255,.07);border-left:3px solid #8aa6ff;}' +
    '#officePanel ul.inbox .msg.pP0{border-left-color:#fb7185;} .msg.pP1{border-left-color:#f6c453;} .msg.pP3{border-left-color:#5c6472;}' +
    '#officePanel ul.inbox .from{font:9px/1 ui-monospace,monospace;color:#8a93a6;}' +
    '#officePanel ul.inbox .subj{font:12px/1.4 -apple-system,system-ui;color:#e8ebf1;word-break:break-word;}' +
    '#officePanel ul.inbox .none{padding:8px 0;font-size:12px;}' +
    '#officePanel .sendnote{font:11px/1.5 -apple-system,system-ui;color:#949cac;margin-bottom:8px;}' +
    '#officePanel .sendnote code{font:10px ui-monospace,monospace;color:#c88cff;}' +
    '#officePanel .cmdbox{display:flex;gap:8px;align-items:flex-start;}' +
    '#officePanel .cmdbox code{flex:1;font:11px/1.5 ui-monospace,monospace;color:#9fe6c7;background:#0b0e15;' +
      'padding:9px 10px;border-radius:9px;border:1px solid rgba(255,255,255,.1);word-break:break-all;}' +
    '#officePanel .copy{flex:none;background:#8aa6ff;color:#0b0e15;border:none;border-radius:9px;padding:9px 12px;' +
      'font:700 12px/1 -apple-system,system-ui;cursor:pointer;}' +
    '#officePanel .copy:active{transform:scale(.95);}' +
    '@media (prefers-color-scheme:light){' +
      '#officeOverlay{background:#eef1f7;color:#2a2418;}' +
      '#officeBar{background:rgba(238,241,247,.9);border-bottom-color:rgba(60,45,20,.14);}' +
      '#officeBar .ob{background:rgba(0,0,0,.05);border-color:rgba(60,45,20,.18);color:#2a2418;}' +
      '#officeHint{background:rgba(255,255,255,.75);color:#7b6f58;border-color:rgba(60,45,20,.14);}' +
      '#officePanel{background:#fbf8f1;border-left-color:rgba(60,45,20,.16);}' +
      '#officePanel .ph{background:#fbf8f1;border-bottom-color:rgba(60,45,20,.12);}' +
      '#officePanel .pid{color:#2a2418;} #officePanel .task{color:#2a2418;}' +
      '#officePanel .chip{background:rgba(0,0,0,.04);border-color:rgba(60,45,20,.16);color:#6b5a3c;}' +
      '#officePanel ul.inbox .msg{background:rgba(0,0,0,.03);border-color:rgba(60,45,20,.1);}' +
      '#officePanel ul.inbox .subj{color:#2a2418;}' +
      '#officePanel .cmdbox code{color:#1f6b4a;background:#f1ece0;border-color:rgba(60,45,20,.16);}' +
    '}' +
    // compact the toolbar on narrow phones so the title + controls never wrap
    '@media (max-width:620px){#officeBar .count{display:none;}}' +
    '@media (max-width:480px){#officeBar .legend{display:none;}#officeBar .ob{padding:7px 9px;}}';

  // ---- automation seam ------------------------------------------------------
  // Thin, side-effect-free hook so the deploy render gate can open the office and
  // a character panel deterministically for the eyeball PNGs (op#26594 asks for
  // the interaction to be shown). Not used by the UI itself.
  window.__officeAPI = {
    open: open,
    close: close,
    tapFirst: function () {
      var s = S.seatsFlat[0];
      if (s) { S.selected = idOf(s.agent); renderPanel(s.agent); return idOf(s.agent); }
      return null;
    },
    agentCount: function () { return S.seatsFlat.length; },
    // zoom onto the first room that has a working agent, so a render can show the
    // speech bubbles (hidden at fit-zoom by design to avoid clutter).
    zoomToBubbles: function () {
      if (!S.plan) return;
      var room = S.plan.rooms.filter(function (r) {
        return r.seats.some(function (s) { return moodOf(s.agent) === "working"; });
      })[0] || S.plan.rooms[0];
      if (!room) return;
      var r = el.canvas.getBoundingClientRect();
      S.cam.z = Math.min(S.max, 1.2);
      var cx = (room.x + room.w / 2) * TILE, cy = (room.y + room.h / 2) * TILE;
      S.cam.x = r.width / 2 - cx * S.cam.z;
      S.cam.y = r.height / 2 - cy * S.cam.z;
      draw();
    }
  };

  // ---- boot -----------------------------------------------------------------
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", buildDOM);
  else buildDOM();
})();
