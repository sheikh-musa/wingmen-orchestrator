/* office.js — fc-v77 — "FLEET GROVE": the fleet console's living pixel office.
 *
 * A self-contained, no-dependency overlay on the Fleet view. A 🏢 launch button
 * opens a full-screen overlay that renders a procedural, Stardew-style top-down
 * 16-bit office: one room per project, a character typing at a lit desk per live
 * agent, letter-courier sprites walking real bus messages desk-to-desk, day/night
 * by Dubai time, pan/zoom, a legend + a bus-traffic panel, and a tap-to-inspect
 * side panel that shows the REAL live tmux pane.
 *
 * This is the approved "Fleet Grove" mock (reports/office-v2/stardew) ported onto
 * LIVE telemetry: the embedded DATA blob is gone. Instead it reads ONLY /api/fleet
 * (the same snapshot fleet.js polls — no new backend, table or endpoint) and, on
 * tap, /api/lanes/<session>/pane (the existing read-only peek). The console has no
 * bus write path, so we NEVER POST — the panel only offers a copyable bus_send.py
 * command (op#26594 hard rule).
 *
 * Added as a view INSIDE fleet.html (launch button injected below) so it ships
 * inside the gated fleet shell; existing tabs/sections are untouched.
 */
(function () {
  "use strict";
  if (window.__officeBooted) return;
  window.__officeBooted = true;

  /* ======================================================================
     CONSTANTS
     ====================================================================== */
  var TILE = 16;
  var WORLD_W = 60, WORLD_H = 40;
  var WPX = WORLD_W * TILE, HPX = WORLD_H * TILE;
  var SW = 16, SH = 20;                 // sprite cell
  var POLL_MS = 8000;                   // fleet.js cadence
  var TASK_CHARS = 60;                  // speech-bubble truncation
  var STALE_S = 1800;                   // 30 min no-heartbeat + not working => away
  var COURIER_CAP = 4;

  var PRI_COL = { P0: "#e2463c", P1: "#e8824a", P2: "#4a90c2", P3: "#8a9bb0" };

  // project → shirt + rug colour (room theming, kept from the mock)
  var PROJECTS = {
    cosem:    { color: "#4a90c2", rug: "#2f5d7a" },
    irsyad:   { color: "#e0a33e", rug: "#8a6322" },
    ventures: { color: "#cf6f90", rug: "#7d3c54" },
    fleet:    { color: "#8a6fd0", rug: "#473074" },
    coord:    { color: "#5bb6a0", rug: "#2f6e5f" }
  };

  // Fixed building footprint. `members` is now LIVE — agents slot in by key.
  var ROOMS = [
    { key: "cosem",    name: "COSEM LAB",           proj: "cosem",    x: 3,  y: 3,  w: 19, h: 10, floor: "lab" },
    { key: "irsyad",   name: "IRSYAD HALL",         proj: "irsyad",   x: 22, y: 3,  w: 17, h: 10, floor: "hall" },
    { key: "hub",      name: "HUB · ORCHESTRATOR",  proj: "coord",    x: 39, y: 3,  w: 18, h: 10, floor: "office", corner: true },
    { key: "fleet",    name: "FLEET OPS",           proj: "fleet",    x: 3,  y: 14, w: 19, h: 12, floor: "ops" },
    { key: "ventures", name: "VENTURES STUDIO",     proj: "ventures", x: 39, y: 14, w: 18, h: 12, floor: "studio" },
    { key: "nazim",    name: "NAZIM · CTO CONSOLE", proj: "coord",    x: 3,  y: 28, w: 19, h: 9,  floor: "office", corner: true },
    { key: "commons",  name: "COORD COMMONS",       proj: "coord",    x: 23, y: 28, w: 15, h: 9,  floor: "commons" },
    { key: "library",  name: "CAI LIBRARY",         proj: "coord",    x: 39, y: 28, w: 18, h: 9,  floor: "library" }
  ];
  var ROOM_BY_KEY = {};
  ROOMS.forEach(function (r) { ROOM_BY_KEY[r.key] = r; });
  var COURT = { x: 23, y: 14, w: 15, h: 12 };
  var CENTER = { x: (COURT.x + COURT.w / 2) * TILE, y: (COURT.y + COURT.h / 2) * TILE };

  /* ======================================================================
     SMALL HELPERS
     ====================================================================== */
  function normBase(id) { return String(id || "").replace(/-\d+$/, ""); }
  function hashStr(s) { var h = 2166136261; for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0); }
  function trunc(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function shade(hex, amt) {
    var n = parseInt(hex.slice(1), 16);
    var r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
    r = Math.max(0, Math.min(255, r + amt)); g = Math.max(0, Math.min(255, g + amt)); b = Math.max(0, Math.min(255, b + amt));
    return "#" + ((1 << 24) + (r << 16) + (g << 8) + b).toString(16).slice(1);
  }
  function mkCanvas(w, h) { var c = document.createElement("canvas"); c.width = w; c.height = h; return c; }
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }
  function normPri(p) { p = String(p || "").toUpperCase(); return PRI_COL[p] ? p : "P2"; }
  function lsGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  function projOf(id) {
    id = String(id || "").toLowerCase();
    if (id.indexOf("cosem") >= 0) return "cosem";
    if (id.indexOf("irsyad") >= 0) return "irsyad";
    if (id.indexOf("substrate") >= 0 || id.indexOf("second-brain") >= 0) return "fleet";
    if (id === "cai" || id === "cc-finance" || id === "cc-quality" || id === "cc-fleet-health" ||
        id === "cc-orchestrator" || id === "orch-console") return "coord";
    return "ventures";
  }
  function roomKeyFor(a) {
    var id = String(a.agent_id || a.base_agent_id || "").toLowerCase();
    if (id === "orch-console") return "nazim";
    if (id === "cc-orchestrator") return "hub";
    if (id === "cai") return "library";
    if (a.__coord) return "commons";                 // finance / sre / quality
    if (id.indexOf("irsyad") >= 0) return "irsyad";
    if (id.indexOf("cosem") >= 0) return "cosem";
    if (id.indexOf("substrate") >= 0 || id.indexOf("second-brain") >= 0) return "fleet";
    return "ventures";
  }

  /* ----- mood model: the fc-v76 four-state bucket model (baked in) ----------
     working — bucket 'working': actively typing (bright monitor, bob, bubble)
     idle    — bucket 'idle' but assigned/running: AT THE DESK, awake, dim monitor,
               NO Zzz (a momentarily-quiet working lane must NOT look asleep)
     away    — bucket 'offline' OR dead heartbeat: genuinely gone (dark + Zzz)
     wedged  — flagged / status blocked|stale: red !  (still at desk, awake)
     Coordinators carry no bucket and are NEVER 'away' (synthesised in ingest). */
  function isWorking(a) {
    if (a.bucket) return a.bucket === "working";
    if (a.live && typeof a.live === "object" && a.live.state) return a.live.state === "working";
    return a.status === "working";
  }
  function isAway(a) {
    if (a.__coord) return false;
    if (a.bucket === "offline") return true;
    var hb = a.heartbeat_age_s;
    return typeof hb === "number" && hb > STALE_S && !isWorking(a);
  }
  function moodOf(a) {
    if (a.flagged || a.status === "blocked" || a.status === "stale") return "wedged";
    if (isAway(a)) return "away";
    if (isWorking(a)) return "working";
    return "idle";
  }

  /* ======================================================================
     PER-AGENT STYLING + SPRITE BAKING  (cached by agent_id)
     ====================================================================== */
  var SKINS = ["#f0c8a0", "#e6b58a", "#c98d5f", "#a76b3e", "#8a5a34", "#f7d3b0", "#d8a877"];
  var HAIRS = ["#2b2016", "#4a2f1a", "#6b4423", "#8a5a2b", "#1d1d22", "#5a3b5a", "#7a6a3a", "#9a9aa0", "#c0683a"];
  var PANTS = ["#3a4256", "#4a3a2e", "#2e4636", "#52304a", "#5a4a2a", "#2f3b4a"];
  function paletteFor(agentId) {
    var seed = hashStr(agentId);
    var base = (PROJECTS[projOf(agentId)] || { color: "#888" }).color;
    return {
      skin: SKINS[seed % SKINS.length],
      hair: HAIRS[(seed >> 3) % HAIRS.length],
      shirt: base, shirtShade: shade(base, -34), shirtLight: shade(base, 28),
      pants: PANTS[(seed >> 6) % PANTS.length], shoe: "#2a2320", outline: "#201812"
    };
  }

  function drawPerson(g, pal, opts) {
    var dir = opts.dir, pose = opts.pose, ph = opts.phase || 0;
    g.clearRect(0, 0, SW, SH);
    var P = function (x, y, c) { g.fillStyle = c; g.fillRect(x, y, 1, 1); };
    var R = function (x, y, w, h, c) { g.fillStyle = c; g.fillRect(x, y, w, h); };
    var bob = (pose === "walk" && ph === 1) ? 1 : 0;
    var sit = pose === "sit";
    var oy = (sit ? 2 : 1) + bob;

    if (dir === "left" || dir === "right") {
      R(5, oy + 1, 6, 6, pal.skin);
      R(5, oy, 7, 2, pal.hair);
      R(5, oy + 1, 2, 5, pal.hair);
      P(10, oy + 1, pal.hair);
      if (pose !== "sleep") { P(8, oy + 3, pal.outline); }
      else { P(7, oy + 3, pal.outline); P(8, oy + 3, pal.outline); }
      R(5, oy + 7, 6, 6, pal.shirt);
      R(5, oy + 11, 6, 2, pal.shirtShade);
      R(9, oy + 8, 2, 4, pal.skin);
      if (pose === "walk") {
        if (ph === 0) { R(5, oy + 13, 2, 4, pal.pants); R(9, oy + 13, 2, 3, pal.pants); P(5, oy + 17, pal.shoe); P(6, oy + 17, pal.shoe); P(9, oy + 16, pal.shoe); P(10, oy + 16, pal.shoe); }
        else { R(5, oy + 13, 2, 3, pal.pants); R(9, oy + 13, 2, 4, pal.pants); P(5, oy + 16, pal.shoe); P(6, oy + 16, pal.shoe); P(9, oy + 17, pal.shoe); P(10, oy + 17, pal.shoe); }
      } else {
        R(5, oy + 13, 2, 4, pal.pants); R(9, oy + 13, 2, 4, pal.pants);
        R(5, oy + 17, 2, 1, pal.shoe); R(9, oy + 17, 2, 1, pal.shoe);
      }
      return;
    }

    var faceUp = (dir === "up");
    R(5, oy, 6, 2, pal.hair);
    R(4, oy + 1, 8, 2, pal.hair);
    R(5, oy + 2, 6, 5, pal.skin);
    R(4, oy + 2, 1, 3, pal.hair); R(11, oy + 2, 1, 3, pal.hair);
    if (faceUp) {
      R(5, oy + 2, 6, 4, pal.hair);
    } else {
      if (pose === "sleep") { P(6, oy + 4, pal.outline); P(7, oy + 4, pal.outline); P(9, oy + 4, pal.outline); P(10, oy + 4, pal.outline); }
      else { P(6, oy + 4, pal.outline); P(9, oy + 4, pal.outline); P(7, oy + 6, shade(pal.skin, -40)); P(8, oy + 6, shade(pal.skin, -40)); }
    }
    var bodyY = oy + 7;
    R(5, bodyY, 6, 6, pal.shirt);
    R(5, bodyY + 5, 6, 1, pal.shirtShade);
    P(5, bodyY, pal.shirtLight); P(6, bodyY, pal.shirtLight);
    if (sit) {
      var hy = bodyY + 2 + (ph === 1 ? 1 : 0);
      R(4, bodyY + 1, 2, 3, pal.skin); R(10, bodyY + 1, 2, 3, pal.skin);
      P(4, hy + 2, pal.skin); P(11, hy + 2, pal.skin);
    } else {
      R(4, bodyY + 1, 1, 4, pal.skin); R(11, bodyY + 1, 1, 4, pal.skin);
    }
    var legY = bodyY + 6;
    if (sit) { R(5, legY, 2, 2, pal.pants); R(9, legY, 2, 2, pal.pants); }
    else if (pose === "walk") {
      if (ph === 0) { R(5, legY, 2, 4, pal.pants); R(9, legY, 2, 3, pal.pants); R(5, legY + 4, 2, 1, pal.shoe); R(9, legY + 3, 2, 1, pal.shoe); }
      else { R(5, legY, 2, 3, pal.pants); R(9, legY, 2, 4, pal.pants); R(5, legY + 3, 2, 1, pal.shoe); R(9, legY + 4, 2, 1, pal.shoe); }
    } else {
      R(5, legY, 2, 4, pal.pants); R(9, legY, 2, 4, pal.pants);
      R(5, legY + 4, 2, 1, pal.shoe); R(9, legY + 4, 2, 1, pal.shoe);
    }
  }
  function flipCanvas(src) { var c = mkCanvas(SW, SH); var g = c.getContext("2d"); g.translate(SW, 0); g.scale(-1, 1); g.drawImage(src, 0, 0); return c; }
  function renderSprite(pal, opts) { var c = mkCanvas(SW, SH); drawPerson(c.getContext("2d"), pal, opts); return c; }
  function bake(pal) {
    var sheet = {};
    ["down", "up", "left"].forEach(function (d) {
      sheet["stand_" + d] = renderSprite(pal, { dir: d, pose: "stand" });
      sheet["walk_" + d] = [renderSprite(pal, { dir: d, pose: "walk", phase: 0 }), renderSprite(pal, { dir: d, pose: "walk", phase: 1 })];
    });
    sheet["stand_right"] = flipCanvas(sheet["stand_left"]);
    sheet["walk_right"] = [flipCanvas(sheet["walk_left"][0]), flipCanvas(sheet["walk_left"][1])];
    sheet["sit"] = [renderSprite(pal, { dir: "down", pose: "sit", phase: 0 }), renderSprite(pal, { dir: "down", pose: "sit", phase: 1 })];
    sheet["sleep"] = renderSprite(pal, { dir: "down", pose: "sleep" });
    return sheet;
  }
  var sheetCache = {};
  function sheetFor(agentId) {
    if (!sheetCache[agentId]) sheetCache[agentId] = bake(paletteFor(agentId));
    return sheetCache[agentId];
  }

  // courier: a little letter-runner, baked once per priority
  function bakeCourier(pri) {
    var col = PRI_COL[pri] || "#8a7a63";
    function draw(dir, ph) {
      var c = mkCanvas(SW, SH); var g = c.getContext("2d");
      var R = function (x, y, w, h, cc) { g.fillStyle = cc; g.fillRect(x, y, w, h); };
      var oy = 3 + (ph === 1 ? 1 : 0);
      R(4, oy, 8, 6, "#fbf3dc"); R(4, oy, 8, 1, "#e8dcbe");
      g.strokeStyle = "#b7a479"; g.beginPath(); g.moveTo(4, oy + 0.5); g.lineTo(8, oy + 3); g.lineTo(12, oy + 0.5); g.stroke();
      R(9, oy + 4, 2, 2, col);
      g.strokeStyle = "#8a7a5a"; g.strokeRect(4.5, oy + 0.5, 7, 5);
      if (ph === 0) { R(5, oy + 6, 2, 2, "#4a3a2a"); R(9, oy + 6, 2, 1, "#4a3a2a"); }
      else { R(5, oy + 6, 2, 1, "#4a3a2a"); R(9, oy + 6, 2, 2, "#4a3a2a"); }
      return c;
    }
    return {
      walk_down: [draw("down", 0), draw("down", 1)], walk_up: [draw("up", 0), draw("up", 1)],
      walk_left: [draw("left", 0), draw("left", 1)], walk_right: [draw("right", 0), draw("right", 1)],
      stand_down: draw("down", 0)
    };
  }
  var courierSheets = {};
  function courierSheet(pri) { if (!courierSheets[pri]) courierSheets[pri] = bakeCourier(pri); return courierSheets[pri]; }

  /* ======================================================================
     STATIC WORLD  — baked once to an offscreen canvas (grass, rooms, plaza).
     Desks + lamps are LIVE and drawn per-frame (see draw()).
     ====================================================================== */
  var world = mkCanvas(WPX, HPX);
  var labelSpots = [];
  var worldBuilt = false;

  function dither(g, x, y, w, h, a, b) {
    for (var j = 0; j < h; j++) for (var i = 0; i < w; i++) { g.fillStyle = ((i + j) & 1) ? a : b; g.fillRect(x + i * TILE, y + j * TILE, TILE, TILE); }
  }
  function woodFloor(g, r, base, plank) {
    for (var j = 0; j < r.h; j++) for (var i = 0; i < r.w; i++) {
      var px = (r.x + i) * TILE, py = (r.y + j) * TILE;
      g.fillStyle = base; g.fillRect(px, py, TILE, TILE);
      g.fillStyle = plank; g.fillRect(px, py + TILE - 1, TILE, 1);
      if (((r.x + i) + (r.y + j)) % 3 === 0) g.fillRect(px, py, 1, TILE);
      g.fillStyle = "rgba(0,0,0,.05)";
      if (((r.x + i * 2 + j) % 4) === 0) g.fillRect(px + 4, py + 5, 6, 1);
    }
  }
  function rug(g, r, col) {
    var rx = (r.x + 1) * TILE, ry = (r.y + 2) * TILE, rw = (r.w - 2) * TILE, rh = (r.h - 3) * TILE;
    g.fillStyle = col; g.fillRect(rx, ry, rw, rh);
    g.fillStyle = shade(col, 24); g.fillRect(rx, ry, rw, 2); g.fillRect(rx, ry, 2, rh);
    g.fillStyle = shade(col, -24); g.fillRect(rx, ry + rh - 2, rw, 2); g.fillRect(rx + rw - 2, ry, 2, rh);
    g.fillStyle = shade(col, 40);
    for (var x = rx + 4; x < rx + rw - 2; x += 8) g.fillRect(x, ry + 3, 2, 2);
  }
  function wallsFor(g, r) {
    g.fillStyle = "#b89b6e"; g.fillRect(r.x * TILE, r.y * TILE, r.w * TILE, 6);
    g.fillStyle = "#d8bd8c"; g.fillRect(r.x * TILE, r.y * TILE, r.w * TILE, 2);
    g.fillStyle = "#7f663f"; g.fillRect(r.x * TILE, r.y * TILE + 6, r.w * TILE, 1);
  }
  function potPlant(g, x, y) {
    g.fillStyle = "#8a5a34"; g.fillRect(x, y + 8, 8, 5);
    g.fillStyle = "#6b4423"; g.fillRect(x, y + 12, 8, 1);
    g.fillStyle = "#3f8a4a"; g.fillRect(x + 1, y + 2, 6, 7);
    g.fillStyle = "#55aa5c"; g.fillRect(x + 2, y, 3, 5); g.fillRect(x + 4, y + 3, 3, 4);
    g.fillStyle = "#2f6e39"; g.fillRect(x + 3, y + 5, 2, 3);
  }
  function bookshelf(g, x, y) {
    g.fillStyle = "#5a3b22"; g.fillRect(x, y, 28, 22);
    var cols = ["#c0623a", "#3f7d54", "#4a6fa5", "#c9a24a", "#8a4a6a", "#5aa0a8"];
    for (var r = 0; r < 3; r++) { for (var i = 0; i < 9; i++) { g.fillStyle = cols[(i + r) % cols.length]; g.fillRect(x + 2 + i * 3, y + 2 + r * 7, 2, 6); } g.fillStyle = "#3a2616"; g.fillRect(x, y + 8 + r * 7, 28, 1); }
  }
  function flowerBed(g, x, y) {
    g.fillStyle = "#6b4423"; g.fillRect(x, y, 14, 10);
    var fc = ["#e2584a", "#e8b341", "#d96fa0", "#f0f0f0"];
    for (var i = 0; i < 8; i++) { g.fillStyle = fc[i % fc.length]; g.fillRect(x + 2 + (i % 4) * 3, y + 2 + Math.floor(i / 4) * 4, 2, 2); }
    g.fillStyle = "#3f8a4a"; for (var j = 0; j < 10; j++) g.fillRect(x + 1 + (j % 5) * 2 + 1, y + 6 + (j % 2), 1, 2);
  }
  function tree(g, x, y) {
    g.fillStyle = "#6b4423"; g.fillRect(x + 6, y + 14, 5, 10);
    g.fillStyle = "#2f6e39"; g.beginPath(); g.arc(x + 8, y + 9, 11, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#3f8a4a"; g.beginPath(); g.arc(x + 5, y + 7, 7, 0, Math.PI * 2); g.fill(); g.beginPath(); g.arc(x + 12, y + 8, 7, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#55aa5c"; g.beginPath(); g.arc(x + 7, y + 5, 4, 0, Math.PI * 2); g.fill();
  }
  function drawRoomStatic(g, r) {
    var proj = PROJECTS[r.proj] || PROJECTS.coord;
    var floors = {
      lab: ["#c9a96f", "#b2905a"], hall: ["#cdaa72", "#b4925c"], office: ["#d9c08a", "#c0a369"],
      ops: ["#bfa36e", "#a98c58"], studio: ["#cbaa7a", "#b28f61"], commons: ["#d0b07a", "#b89361"], library: ["#c4a06a", "#a98954"]
    };
    var f = floors[r.floor] || floors.lab;
    woodFloor(g, r, f[0], f[1]);
    rug(g, r, proj.rug);
    wallsFor(g, r);
    labelSpots.push({ x: (r.x + r.w / 2) * TILE, y: r.y * TILE + 4, text: r.name, accent: proj.color, corner: !!r.corner });
    if (r.corner) {
      var cx = (r.x + 1) * TILE, cy = (r.y + 2) * TILE;
      g.fillStyle = shade(proj.color, -10); g.fillRect(cx + 8, cy + 8, 24, 16);
      g.fillStyle = shade(proj.color, 30); g.fillRect(cx + 12, cy + 12, 16, 8);
    }
    potPlant(g, (r.x + r.w - 2) * TILE + 6, (r.y + r.h - 2) * TILE);
    potPlant(g, (r.x + 1) * TILE - 2, (r.y + r.h - 2) * TILE);
    if (r.floor === "library") { bookshelf(g, (r.x + 1) * TILE + 2, (r.y + 2) * TILE + 2); bookshelf(g, (r.x + 1) * TILE + 2, (r.y + 2) * TILE + 26); }
  }
  function drawCourtyard(g) {
    var cx = CENTER.x, cy = CENTER.y;
    g.fillStyle = "#c7b89a"; g.beginPath(); g.arc(cx, cy, 46, 0, Math.PI * 2); g.fill();
    for (var r2 = 0; r2 < 46; r2 += 4) { g.beginPath(); g.arc(cx, cy, r2, 0, Math.PI * 2); g.strokeStyle = "rgba(120,100,70,.25)"; g.stroke(); }
    g.fillStyle = "#7f99a8"; g.beginPath(); g.arc(cx, cy, 12, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#bfe0ef"; g.beginPath(); g.arc(cx, cy, 9, 0, Math.PI * 2); g.fill();
    g.fillStyle = "#8fc6de"; g.beginPath(); g.arc(cx, cy, 9, 0, Math.PI * 2); g.stroke();
    g.fillStyle = "#6b8390"; g.fillRect(cx - 2, cy - 4, 4, 8);
    g.fillStyle = "#d9f0fb"; g.fillRect(cx - 1, cy - 10, 2, 6);
    flowerBed(g, cx - 34, cy - 30); flowerBed(g, cx + 22, cy + 18); flowerBed(g, cx + 24, cy - 30); flowerBed(g, cx - 36, cy + 16);
    tree(g, cx - 40, cy + 28); tree(g, cx + 34, cy - 6);
  }
  function drawBorder(g) {
    for (var x = 0; x < WORLD_W; x += 2) { tree(g, x * TILE - 4, -6); if (x % 2 === 0) tree(g, x * TILE - 4, (WORLD_H - 1) * TILE - 4); }
    for (var y = 1; y < WORLD_H - 1; y += 2) { tree(g, -6, y * TILE - 4); tree(g, (WORLD_W - 1) * TILE - 6, y * TILE - 4); }
  }
  function buildWorld() {
    if (worldBuilt) return;
    var g = world.getContext("2d");
    g.imageSmoothingEnabled = false;
    dither(g, 0, 0, WORLD_W, WORLD_H, "#6aa65a", "#5f9a51");
    var s = 12345; var rnd = function () { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; };
    for (var i = 0; i < 520; i++) {
      var x = Math.floor(rnd() * WPX), y = Math.floor(rnd() * HPX), t = rnd();
      if (t < 0.5) { g.fillStyle = "#74b363"; g.fillRect(x, y, 1, 1); g.fillRect(x + 1, y - 1, 1, 1); }
      else if (t < 0.72) { g.fillStyle = "#538a46"; g.fillRect(x, y, 1, 2); }
      else { g.fillStyle = "#e7d85a"; g.fillRect(x, y, 1, 1); }
    }
    labelSpots = [];
    ROOMS.forEach(function (r) { drawRoomStatic(g, r); });
    drawCourtyard(g);
    drawBorder(g);
    worldBuilt = true;
  }

  function drawDesk(g, cx, cy, accent, mood, now, seedX) {
    var w = 22, h = 10, x = Math.round(cx - w / 2), y = Math.round(cy);
    g.fillStyle = "#6b4a2a"; g.fillRect(x, y + 2, w, h);
    g.fillStyle = "#845c34"; g.fillRect(x, y, w, 3);
    g.fillStyle = "#4e351d"; g.fillRect(x, y + h, w, 2);
    // monitor — bright pulse working, on-but-dim idle, near-dark away, red wedged
    g.fillStyle = "#2b2320"; g.fillRect(x + 6, y - 6, 10, 7);
    var glow = mood === "working" ? (0.55 + 0.45 * Math.abs(Math.sin(now / 300 + seedX)))
             : mood === "idle" ? 0.42 : mood === "wedged" ? 0.5 : 0.08;
    var mc = mood === "wedged" ? PRI_COL.P0 : shade(accent, 10);
    g.globalAlpha = glow; g.fillStyle = mc; g.fillRect(x + 7, y - 5, 8, 5); g.globalAlpha = 1;
    if (mood === "working") { g.fillStyle = "#d9f2ff"; g.fillRect(x + 8, y - 4, 3, 1); }
    g.fillStyle = "#d6d2c4"; g.fillRect(x + 2, y - 1, 3, 3);
    g.fillStyle = accent; g.fillRect(x + w - 5, y - 2, 3, 4);
  }

  function roomDoor(r) {
    var rx0 = r.x * TILE, ry0 = r.y * TILE, rx1 = (r.x + r.w) * TILE, ry1 = (r.y + r.h) * TILE;
    var px = Math.max(rx0, Math.min(CENTER.x, rx1)), py = Math.max(ry0, Math.min(CENTER.y, ry1));
    var dl = Math.abs(px - rx0), dr = Math.abs(px - rx1), dt = Math.abs(py - ry0), db = Math.abs(py - ry1);
    var m = Math.min(dl, dr, dt, db);
    if (m === dt) return { x: (r.x + r.w / 2) * TILE, y: ry0 - 6 };
    if (m === db) return { x: (r.x + r.w / 2) * TILE, y: ry1 + 6 };
    if (m === dl) return { x: rx0 - 6, y: (r.y + r.h / 2) * TILE };
    return { x: rx1 + 6, y: (r.y + r.h / 2) * TILE };
  }

  /* ======================================================================
     LIVE STATE
     ====================================================================== */
  var S = {
    open: false, data: null,
    seats: [], chars: [], chById: {},
    deskExact: {}, deskBase: {}, lampSpots: [],
    couriers: [], courierSrc: [], msgIdx: 0, courierTimer: 0,
    cam: { x: 0, y: 0, scale: 3, min: 1.1, max: 7 },
    view: { w: 0, h: 0 }, dpr: 1,
    raf: 0, timer: 0, clockTimer: 0, lastT: 0, selected: null, hover: null,
    phaseOverride: null, phaseIdx: 0, themeMode: "auto",
    paneReq: 0
  };
  var PHASES = ["auto", "day", "dusk", "night", "dawn"];

  /* ======================================================================
     LAYOUT (live) — assign agents to rooms + stable desk seats
     ====================================================================== */
  function layout(agents) {
    var byRoom = {};
    agents.forEach(function (a) { var k = roomKeyFor(a); (byRoom[k] || (byRoom[k] = [])).push(a); });
    var seats = [], deskExact = {}, deskBase = {}, lampSpots = [];
    ROOMS.forEach(function (room) {
      var members = (byRoom[room.key] || []).slice().sort(function (a, b) {
        return String(a.agent_id).localeCompare(String(b.agent_id));
      });
      var n = members.length; if (!n) return;
      var pad = 2, innerX = room.x + pad, innerY = room.y + 3, innerW = room.w - pad * 2, innerH = room.h - 4.2;
      var cols = Math.max(1, Math.min(n, Math.floor(innerW / 5)));
      if (n <= 2) cols = n; if (n === 4) cols = 2; if (n === 5 || n === 6) cols = 3;
      var rows = Math.ceil(n / cols), cellW = innerW / cols, cellH = innerH / rows;
      members.forEach(function (a, i) {
        var col = i % cols, row = Math.floor(i / cols);
        var cx = innerX + cellW * (col + 0.5), cy = innerY + cellH * (row + 0.5);
        var sx = cx * TILE, sy = cy * TILE;
        var seat = { agent: a, room: room, x: sx, y: sy, home: { x: sx, y: sy } };
        seats.push(seat);
        lampSpots.push({ x: sx, y: sy + 4 });
        deskExact[a.agent_id] = seat;
        var nb = normBase(a.agent_id); if (!(nb in deskBase)) deskBase[nb] = seat;
        var ba = String(a.base_agent_id || "").toLowerCase(); if (ba && !(ba in deskBase)) deskBase[ba] = seat;
      });
    });
    return { seats: seats, deskExact: deskExact, deskBase: deskBase, lampSpots: lampSpots };
  }

  function moodBaseState(mood) {
    if (mood === "away") return { state: "sleep", pose: "sleep", dir: "down" };
    if (mood === "working") return { state: "typing", pose: "sit", dir: "down" };
    return { state: "idle", pose: "sit", dir: "down" }; // idle + wedged sit awake
  }

  function reconcile(seats) {
    var next = [], byId = {};
    seats.forEach(function (seat) {
      var id = seat.agent.agent_id;
      var ch = S.chById[id];
      var mood = moodOf(seat.agent);
      if (ch) {
        ch.agent = seat.agent; ch.room = seat.room; ch.home = seat.home; ch.flagged = !!seat.agent.flagged;
        // if the floor plan shifted this agent (set changed) and it's resting, snap.
        if (ch.state !== "walking" && dist({ x: ch.x, y: ch.y }, seat.home) > TILE * 0.5) { ch.x = seat.home.x; ch.y = seat.home.y; }
        if (ch.mood !== mood) { var b = moodBaseState(mood); ch.state = b.state; ch.pose = b.pose; ch.dir = b.dir; ch.path = null; }
        ch.mood = mood;
      } else {
        var bs = moodBaseState(mood);
        ch = {
          id: id, agent: seat.agent, room: seat.room,
          x: seat.home.x, y: seat.home.y, home: seat.home,
          sheet: sheetFor(id), mood: mood,
          state: bs.state, pose: bs.pose, dir: bs.dir,
          anim: Math.random() * 10, tNext: 2 + Math.random() * 6,
          path: null, pathI: 0, pathDone: false,
          bubble: null, bubbleT: 0, flagged: !!seat.agent.flagged
        };
      }
      next.push(ch); byId[id] = ch;
    });
    S.chars = next; S.chById = byId;
    // selection survives a rebuild
    if (S.selected && !byId[S.selected]) closePanel();
  }

  /* ======================================================================
     OPEN / CLOSE / LIVE DATA
     ====================================================================== */
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
      .catch(function () { /* keep last good frame; the grove never blanks */ });
  }
  function ingest(d, first) {
    S.data = d;
    var agents = [];
    (d.lanes || []).forEach(function (l) { agents.push(l); });
    (d.coordinators || []).forEach(function (c) {
      // coordinators carry no bucket; working if active < 15 min, else idle, never away
      var fresh = typeof c.activity_age_s === "number" && c.activity_age_s < 900;
      agents.push({
        agent_id: c.agent_id, base_agent_id: c.agent_id, display_name: c.short || c.agent_id,
        status: fresh ? "working" : "idle", current_task: c.activity || c.role_label || "",
        model: c.model, heartbeat_age_s: c.last_seen_s, activity_age_s: c.activity_age_s,
        last_seen_s: c.last_seen_s, ctx_pct: c.ctx_pct, role_label: c.role_label,
        tmux_session: c.tmux_session, lane: c.tmux_session,
        live: { state: fresh ? "working" : "idle" }, bucket: null,
        flagged: !!c.auth_mismatch, __coord: true
      });
    });
    var lay = layout(agents);
    S.seats = lay.seats; S.deskExact = lay.deskExact; S.deskBase = lay.deskBase; S.lampSpots = lay.lampSpots;
    reconcile(lay.seats);
    buildCourierSrc(d);
    renderBusFeed();
    updateChrome();
    if (first) { recenter(); }
    if (S.selected && S.chById[S.selected]) renderPanel(S.chById[S.selected].agent, false);
  }

  function open() {
    if (!el.ov) buildDOM();
    S.open = true;
    el.ov.classList.add("show");
    document.body.classList.add("fg-lock");
    resize();
    refresh(true);
    S.timer = setInterval(function () { refresh(false); }, POLL_MS);
    S.clockTimer = setInterval(updateClock, 10000);
    S.lastT = performance.now();
    loop();
  }
  function close() {
    S.open = false;
    if (el.ov) el.ov.classList.remove("show");
    document.body.classList.remove("fg-lock");
    clearInterval(S.timer); S.timer = 0;
    clearInterval(S.clockTimer); S.clockTimer = 0;
    cancelAnimationFrame(S.raf); S.raf = 0;
    stopAudio();
    closePanel();
  }

  /* ----- couriers from REAL bus rows (drain_board) --------------------------
     drain_board entry = { agent, items:[{id, from, subject, priority, age_s}] }
     items are messages TO `agent` FROM `from`. */
  function buildCourierSrc(d) {
    var src = [];
    (d && d.drain_board || []).forEach(function (entry) {
      var to = entry.agent;
      (entry.items || []).forEach(function (it) {
        src.push({ id: it.id, from: it.from, to: to, subject: it.subject || "", pri: normPri(it.priority), age_s: it.age_s });
      });
    });
    src.sort(function (a, b) { return (a.age_s || 0) - (b.age_s || 0); }); // freshest first
    S.courierSrc = src;
    if (S.msgIdx >= src.length) S.msgIdx = 0;
  }
  function resolveDesk(id) {
    if (!id) return null;
    if (S.deskExact[id]) return S.deskExact[id];
    var lid = String(id).toLowerCase();
    if (S.deskBase[lid]) return S.deskBase[lid];
    var nb = normBase(id);
    if (S.deskBase[nb]) return S.deskBase[nb];
    return null;
  }
  function spawnCourier() {
    if (S.couriers.length >= COURIER_CAP) return;
    var src = S.courierSrc; if (!src.length) return;
    var tries = 0, msg = null, from = null, to = null;
    while (tries < src.length) {
      msg = src[S.msgIdx % src.length]; S.msgIdx++;
      from = resolveDesk(msg.from); to = resolveDesk(msg.to);
      tries++;
      if (from && to && from !== to) break;
      msg = null;
    }
    if (!msg || !from || !to) return;
    S.couriers.push({
      msg: msg, from: from, to: to,
      path: [{ x: from.x, y: from.y }, roomDoor(from.room),
             { x: CENTER.x + (Math.random() * 30 - 15), y: CENTER.y + (Math.random() * 24 - 12) },
             roomDoor(to.room), { x: to.x, y: to.y }],
      pathI: 0, pathDone: false, x: from.x, y: from.y, dir: "down", anim: Math.random() * 4,
      sheet: courierSheet(msg.pri), phase: "move", hold: 0
    });
    flashBus(msg);
  }

  /* ======================================================================
     CAMERA + INPUT
     ====================================================================== */
  function resize() {
    S.dpr = Math.min(window.devicePixelRatio || 1, 2);
    S.view.w = el.ov.clientWidth || window.innerWidth;
    S.view.h = el.ov.clientHeight || window.innerHeight;
    el.canvas.width = Math.floor(S.view.w * S.dpr);
    el.canvas.height = Math.floor(S.view.h * S.dpr);
    el.ctx.setTransform(S.dpr, 0, 0, S.dpr, 0, 0);
    el.ctx.imageSmoothingEnabled = false;
    clampCam();
  }
  function clampCam() {
    var sMinFit = Math.min(S.view.w / WPX, S.view.h / HPX);
    S.cam.min = Math.max(1.0, sMinFit * 0.9);
    S.cam.scale = Math.max(S.cam.min, Math.min(S.cam.max, S.cam.scale));
    var vw = S.view.w / S.cam.scale, vh = S.view.h / S.cam.scale;
    if (vw >= WPX) S.cam.x = (WPX - vw) / 2; else S.cam.x = Math.max(0, Math.min(WPX - vw, S.cam.x));
    if (vh >= HPX) S.cam.y = (HPX - vh) / 2; else S.cam.y = Math.max(0, Math.min(HPX - vh, S.cam.y));
  }
  function recenter() {
    S.cam.scale = Math.max(S.cam.min, Math.min(S.view.w / WPX, S.view.h / HPX) * 0.98);
    var vw = S.view.w / S.cam.scale, vh = S.view.h / S.cam.scale;
    S.cam.x = (WPX - vw) / 2; S.cam.y = (HPX - vh) / 2; clampCam();
  }
  function screenToWorld(sx, sy) { return { x: S.cam.x + sx / S.cam.scale, y: S.cam.y + sy / S.cam.scale }; }

  function wireCanvas() {
    var c = el.canvas;
    var pointers = new Map(), dragging = false, moved = false, last = { x: 0, y: 0 }, downAt = { x: 0, y: 0, t: 0 }, pinchStart = null;
    function rel(e) { var r = c.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; }
    c.addEventListener("pointerdown", function (e) {
      c.setPointerCapture(e.pointerId);
      var p = rel(e); pointers.set(e.pointerId, p);
      if (pointers.size === 1) { dragging = true; moved = false; last = p; downAt = { x: p.x, y: p.y, t: performance.now() }; c.classList.add("dragging"); }
      if (pointers.size === 2) { var a = [].concat(Array.from(pointers.values())); pinchStart = { d: dist(a[0], a[1]), scale: S.cam.scale }; }
    });
    c.addEventListener("pointermove", function (e) {
      var p = rel(e);
      if (pointers.has(e.pointerId)) pointers.set(e.pointerId, p);
      if (pointers.size === 2 && pinchStart) {
        var a = Array.from(pointers.values()); var d = dist(a[0], a[1]);
        var cxm = (a[0].x + a[1].x) / 2, cym = (a[0].y + a[1].y) / 2;
        var before = screenToWorld(cxm, cym);
        S.cam.scale = Math.max(S.cam.min, Math.min(S.cam.max, pinchStart.scale * (d / pinchStart.d)));
        var after = screenToWorld(cxm, cym);
        S.cam.x += before.x - after.x; S.cam.y += before.y - after.y; clampCam(); moved = true; return;
      }
      if (dragging) {
        var dx = p.x - last.x, dy = p.y - last.y;
        if (Math.abs(p.x - downAt.x) + Math.abs(p.y - downAt.y) > 5) moved = true;
        S.cam.x -= dx / S.cam.scale; S.cam.y -= dy / S.cam.scale; last = p; clampCam();
      } else {
        var w = screenToWorld(p.x, p.y); S.hover = hitChar(w.x, w.y);
        c.style.cursor = S.hover ? "pointer" : "grab";
      }
    });
    function end(e) {
      var wasTap = pointers.size === 1 && !moved && (performance.now() - downAt.t < 400);
      var p = rel(e);
      pointers.delete(e.pointerId);
      if (pointers.size < 2) pinchStart = null;
      if (pointers.size === 0) { dragging = false; c.classList.remove("dragging"); }
      if (wasTap) { var w = screenToWorld(p.x, p.y); var ch = hitChar(w.x, w.y); if (ch) { S.selected = ch.id; renderPanel(ch.agent, true); } else closePanel(); }
    }
    c.addEventListener("pointerup", end);
    c.addEventListener("pointercancel", function (e) { pointers.delete(e.pointerId); dragging = false; pinchStart = null; c.classList.remove("dragging"); });
    c.addEventListener("wheel", function (e) {
      e.preventDefault(); var p = rel(e);
      var before = screenToWorld(p.x, p.y);
      S.cam.scale = Math.max(S.cam.min, Math.min(S.cam.max, S.cam.scale * Math.exp(-e.deltaY * 0.0014)));
      var after = screenToWorld(p.x, p.y);
      S.cam.x += before.x - after.x; S.cam.y += before.y - after.y; clampCam();
    }, { passive: false });
  }
  function hitChar(wx, wy) {
    var best = null, bd = 1e9;
    for (var i = 0; i < S.chars.length; i++) {
      var ch = S.chars[i], dx = wx - ch.x, dy = wy - (ch.y - 2);
      if (Math.abs(dx) < 9 && dy > -18 && dy < 8) { var d = Math.hypot(dx, dy); if (d < bd) { bd = d; best = ch; } }
    }
    return best;
  }
  function zoomBtn(f) {
    var cx = S.view.w / 2, cy = S.view.h / 2, b = screenToWorld(cx, cy);
    S.cam.scale = Math.max(S.cam.min, Math.min(S.cam.max, S.cam.scale * f));
    var a = screenToWorld(cx, cy); S.cam.x += b.x - a.x; S.cam.y += b.y - a.y; clampCam();
  }

  /* ======================================================================
     DAY / NIGHT (Dubai, UTC+4)
     ====================================================================== */
  function dubaiHour() { var now = new Date(); return (now.getUTCHours() + now.getUTCMinutes() / 60 + 4) % 24; }
  function phaseFor(h) { if (h >= 6 && h < 8) return "dawn"; if (h >= 8 && h < 16.5) return "day"; if (h >= 16.5 && h < 18.5) return "dusk"; return "night"; }
  function currentPhase() { return S.phaseOverride || phaseFor(dubaiHour()); }
  var PHASE_TINT = {
    day:  { col: [255, 244, 214], a: 0.05, mult: [255, 250, 235], ma: 0.06, lamps: false, dotc: "#f4c84b" },
    dawn: { col: [255, 196, 150], a: 0.20, mult: [255, 210, 190], ma: 0.14, lamps: false, dotc: "#ff9f6b" },
    dusk: { col: [255, 150, 90], a: 0.26, mult: [120, 90, 120], ma: 0.30, lamps: true, dotc: "#ff7a45" },
    night:{ col: [40, 60, 120], a: 0.20, mult: [30, 40, 90], ma: 0.58, lamps: true, dotc: "#6f8fe0" }
  };

  /* ======================================================================
     UPDATE + RENDER LOOP
     ====================================================================== */
  var _hidden = function () { return document.hidden; };
  function loop() {
    if (!S.open) return;
    if (_hidden()) { S.raf = requestAnimationFrame(loop); S.lastT = performance.now(); return; }
    var now = performance.now();
    var dt = Math.min(0.05, (now - S.lastT) / 1000); S.lastT = now;
    update(dt, now);
    draw(now);
    S.raf = requestAnimationFrame(loop);
  }
  function strollPath(ch) {
    var r = ch.room, n = 1 + Math.floor(Math.random() * 2), pts = [];
    for (var i = 0; i < n; i++) pts.push({ x: (r.x + 2 + Math.random() * (r.w - 4)) * TILE, y: (r.y + 4 + Math.random() * (r.h - 6)) * TILE });
    pts.push({ x: ch.home.x, y: ch.home.y });
    return pts;
  }
  function move(ent, tgt, dt, speed) {
    var dx = tgt.x - ent.x, dy = tgt.y - ent.y, d = Math.hypot(dx, dy) || 1, step = speed * dt;
    ent.x += dx / d * Math.min(step, d); ent.y += dy / d * Math.min(step, d);
    if (Math.abs(dx) > Math.abs(dy)) ent.dir = dx < 0 ? "left" : "right"; else ent.dir = dy < 0 ? "up" : "down";
  }
  function stepPath(ent, dt, speed) {
    ent.pathDone = false;
    if (!ent.path || ent.pathI >= ent.path.length) { ent.pathDone = true; return; }
    var tgt = ent.path[ent.pathI]; move(ent, tgt, dt, speed);
    if (Math.hypot(tgt.x - ent.x, tgt.y - ent.y) < 2.5) { ent.pathI++; if (ent.pathI >= ent.path.length) ent.pathDone = true; }
  }
  function update(dt, now) {
    for (var i = 0; i < S.chars.length; i++) {
      var c = S.chars[i]; c.anim += dt;
      if (c.state === "typing" || c.state === "sleep") { /* animate in place */ }
      else { // idle / wedged — awake, may stroll, NEVER sleep/Zzz
        if (c.state === "walking") {
          stepPath(c, dt, 34);
          if (c.pathDone) { c.state = "idle"; c.pose = "sit"; c.dir = "down"; c.tNext = 5 + Math.random() * 8; }
        } else {
          c.tNext -= dt;
          if (c.tNext <= 0 && c.mood === "idle" && c.room.liveCount > 1 && Math.random() < 0.5) {
            c.path = strollPath(c); c.pathI = 0; c.state = "walking"; c.pose = "walk";
          } else if (c.tNext <= 0) { c.tNext = 5 + Math.random() * 8; }
        }
      }
      if (c.bubbleT > 0) c.bubbleT -= dt;
    }
    // how many live in each room (for stroll gating)
    S.chars.forEach(function (c) { c.room.liveCount = 0; });
    S.chars.forEach(function (c) { c.room.liveCount++; });
    // couriers
    S.courierTimer -= dt;
    if (S.courierTimer <= 0) { spawnCourier(); S.courierTimer = 1.4 + Math.random() * 1.8; }
    for (var k = S.couriers.length - 1; k >= 0; k--) {
      var co = S.couriers[k]; co.anim += dt;
      if (co.phase === "move") {
        stepPath(co, dt, 46);
        if (co.pathDone) {
          co.phase = "deliver"; co.hold = 2.6; co.x = co.to.x; co.y = co.to.y;
          var tc = S.chById[co.to.agent.agent_id];
          if (tc) { tc.bubble = { text: trunc(co.msg.subject, 46), pri: co.msg.pri, kind: "msg" }; tc.bubbleT = 2.6; }
        }
      } else { co.hold -= dt; if (co.hold <= 0) S.couriers.splice(k, 1); }
    }
  }

  var _ents = [];
  function draw(now) {
    var ctx = el.ctx, ph = currentPhase(), T = PHASE_TINT[ph];
    ctx.fillStyle = ph === "night" ? "#0d1322" : (ph === "dusk" ? "#2a2030" : "#0f1a0f");
    ctx.fillRect(0, 0, S.view.w, S.view.h);
    ctx.save();
    ctx.scale(S.cam.scale, S.cam.scale);
    ctx.translate(-S.cam.x, -S.cam.y);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(world, 0, 0);

    // desks (live layer, under characters)
    for (var i = 0; i < S.seats.length; i++) {
      var s = S.seats[i], ch = S.chById[s.agent.agent_id], mood = ch ? ch.mood : moodOf(s.agent);
      drawDesk(ctx, s.x, s.y + 9, (PROJECTS[s.room.proj] || PROJECTS.coord).color, mood, now, s.x);
    }
    // fountain shimmer
    ctx.fillStyle = "rgba(217,240,251," + (0.4 + 0.3 * Math.sin(now / 300)) + ")";
    ctx.fillRect(CENTER.x - 1, CENTER.y - 9 + Math.sin(now / 200) * 1, 2, 4);

    // entities, y-sorted
    _ents.length = 0;
    for (var a = 0; a < S.chars.length; a++) _ents.push(S.chars[a]);
    for (var b = 0; b < S.couriers.length; b++) _ents.push(S.couriers[b]);
    _ents.sort(function (p, q) { return p.y - q.y; });
    for (var e = 0; e < _ents.length; e++) { var en = _ents[e]; if (en.sheet && en.phase) drawCourier(ctx, en, now); else drawChar(ctx, en, now); }

    drawLabels(ctx);

    if (T.lamps) {
      ctx.globalCompositeOperation = "lighter";
      for (var L = 0; L < S.lampSpots.length; L++) {
        var sp = S.lampSpots[L], gr = ctx.createRadialGradient(sp.x, sp.y, 2, sp.x, sp.y, 30);
        gr.addColorStop(0, "rgba(255,208,120,0.5)"); gr.addColorStop(1, "rgba(255,208,120,0)");
        ctx.fillStyle = gr; ctx.fillRect(sp.x - 30, sp.y - 30, 60, 60);
      }
      ctx.globalCompositeOperation = "source-over";
    }
    ctx.restore();

    // screen-space tint
    ctx.save();
    ctx.globalCompositeOperation = "multiply";
    ctx.fillStyle = "rgba(" + T.mult[0] + "," + T.mult[1] + "," + T.mult[2] + "," + T.ma + ")";
    ctx.fillRect(0, 0, S.view.w, S.view.h);
    ctx.globalCompositeOperation = "overlay";
    ctx.fillStyle = "rgba(" + T.col[0] + "," + T.col[1] + "," + T.col[2] + "," + T.a + ")";
    ctx.fillRect(0, 0, S.view.w, S.view.h);
    ctx.restore();

    var vg = ctx.createRadialGradient(S.view.w / 2, S.view.h / 2, Math.min(S.view.w, S.view.h) * 0.3, S.view.w / 2, S.view.h / 2, Math.max(S.view.w, S.view.h) * 0.7);
    vg.addColorStop(0, "rgba(0,0,0,0)"); vg.addColorStop(1, ph === "night" ? "rgba(0,0,0,.4)" : "rgba(0,0,0,.18)");
    ctx.fillStyle = vg; ctx.fillRect(0, 0, S.view.w, S.view.h);
  }

  function drawChar(ctx, c, now) {
    ctx.fillStyle = "rgba(0,0,0,.22)"; ctx.beginPath(); ctx.ellipse(c.x, c.y + 1, 6, 2.4, 0, 0, Math.PI * 2); ctx.fill();
    var img;
    if (c.state === "walking") img = c.sheet["walk_" + c.dir][Math.floor(c.anim * 6) % 2];
    else if (c.state === "sleep") img = c.sheet["sleep"];
    else if (c.state === "typing") img = c.sheet["sit"][Math.floor(c.anim * 5) % 2];
    else img = c.sheet["sit"][0]; // idle / wedged: seated at the desk, awake
    ctx.drawImage(img, Math.round(c.x - SW / 2), Math.round(c.y - SH + 3));

    var headX = c.x, headY = c.y - SH + 3;
    if (c.mood === "wedged") drawAlert(ctx, headX + 5, headY - 2, now);
    else if (c.mood === "working") drawTypingDots(ctx, headX, headY - 1, now);
    else if (c.mood === "away") drawZzz(ctx, headX + 4, headY, now);

    // selection ring
    if (c.id === S.selected) { ctx.strokeStyle = "#fff9d0"; ctx.lineWidth = 0.8; ctx.beginPath(); ctx.ellipse(c.x, c.y, 10, 4, 0, 0, Math.PI * 2); ctx.stroke(); }

    // speech bubble: hovered / selected always; working/wedged when zoomed in
    var bub = null;
    if (S.hover === c || S.selected === c.id) bub = { text: trunc((c.agent.current_task || c.agent.role_label || "(idle)").replace(/\s+/g, " "), TASK_CHARS), pri: null, kind: "task" };
    else if (c.bubbleT > 0 && c.bubble) bub = c.bubble;
    else if (S.cam.scale >= 2.6 && (c.mood === "working" || c.mood === "wedged") && (c.agent.current_task || c.agent.role_label)) bub = { text: trunc((c.agent.current_task || c.agent.role_label).replace(/\s+/g, " "), TASK_CHARS), pri: null, kind: "task" };
    if (bub) drawBubble(ctx, c.x, c.y - SH + 1, bub);
  }
  function drawCourier(ctx, co, now) {
    ctx.fillStyle = "rgba(0,0,0,.18)"; ctx.beginPath(); ctx.ellipse(co.x, co.y + 2, 4, 1.8, 0, 0, Math.PI * 2); ctx.fill();
    var img = co.phase === "move" ? co.sheet["walk_" + co.dir][Math.floor(co.anim * 7) % 2] : co.sheet["stand_down"];
    ctx.drawImage(img, Math.round(co.x - SW / 2), Math.round(co.y - SH + 5));
  }
  function drawTypingDots(ctx, x, y, now) {
    var n = Math.floor(now / 260) % 3; ctx.fillStyle = "#fff";
    for (var i = 0; i < 3; i++) { ctx.globalAlpha = i <= n ? 1 : 0.3; ctx.fillRect(Math.round(x - 3 + i * 3), Math.round(y - 3), 1.6, 1.6); }
    ctx.globalAlpha = 1;
  }
  function pixText(ctx, t, x, y) { ctx.save(); ctx.font = '6px "VT323", monospace'; ctx.textBaseline = "top"; ctx.fillText(t, x, y); ctx.restore(); }
  function drawZzz(ctx, x, y, now) { ctx.fillStyle = "#cfe3ff"; var t = (now / 600) % 3 | 0; pixText(ctx, "z", x + 1, y - 2 - t * 2); pixText(ctx, "z", x + 3, y - 6 - t * 1.5); }
  function drawAlert(ctx, x, y, now) {
    ctx.fillStyle = "#e2463c"; ctx.beginPath(); ctx.moveTo(x, y - 7); ctx.lineTo(x + 4, y); ctx.lineTo(x - 4, y); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.fillRect(x - 0.6, y - 5, 1.4, 3); ctx.fillRect(x - 0.6, y - 1, 1.4, 1.2);
  }
  function drawBubble(ctx, x, topY, bub) {
    var text = bub.text; if (!text) return;
    ctx.save(); ctx.font = '7px "VT323", monospace'; ctx.textBaseline = "top";
    var maxW = 96, words = text.split(" "), lines = [], ln = "";
    for (var i = 0; i < words.length; i++) { var t = ln ? ln + " " + words[i] : words[i]; if (ctx.measureText(t).width > maxW && ln) { lines.push(ln); ln = words[i]; } else ln = t; }
    if (ln) lines.push(ln); if (lines.length > 3) { lines.length = 3; lines[2] = trunc(lines[2], 26); }
    var bw = 0; for (var j = 0; j < lines.length; j++) bw = Math.max(bw, ctx.measureText(lines[j]).width);
    bw = Math.min(maxW, bw) + 8; var bh = lines.length * 8 + 6;
    var bx = Math.round(x - bw / 2), by = Math.round(topY - bh - 4);
    if (bx < S.cam.x + 2) bx = S.cam.x + 2; if (bx + bw > S.cam.x + S.view.w / S.cam.scale - 2) bx = S.cam.x + S.view.w / S.cam.scale - 2 - bw;
    ctx.fillStyle = "rgba(20,15,10,.28)"; ctx.fillRect(bx + 1, by + 2, bw, bh);
    ctx.fillStyle = "#fdf6e3"; ctx.fillRect(bx, by, bw, bh);
    ctx.strokeStyle = bub.pri ? PRI_COL[bub.pri] : "#8a7a5a"; ctx.lineWidth = 1; ctx.strokeRect(bx + 0.5, by + 0.5, bw - 1, bh - 1);
    ctx.fillStyle = "#fdf6e3"; ctx.beginPath(); ctx.moveTo(x - 3, by + bh - 1); ctx.lineTo(x + 3, by + bh - 1); ctx.lineTo(x, by + bh + 4); ctx.closePath(); ctx.fill();
    var tx = bx + 4;
    if (bub.pri) { ctx.fillStyle = PRI_COL[bub.pri]; ctx.fillRect(bx + 3, by + 3, 3, 3); tx = bx + 8; }
    ctx.fillStyle = "#3a2c1a";
    for (var L = 0; L < lines.length; L++) ctx.fillText(lines[L], L === 0 ? tx : bx + 4, by + 3 + L * 8);
    ctx.restore();
  }
  function drawLabels(ctx) {
    ctx.save(); ctx.font = '7px "VT323", monospace'; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (var i = 0; i < labelSpots.length; i++) {
      var l = labelSpots[i], w = ctx.measureText(l.text).width + 10, x = l.x, y = l.y + 1;
      ctx.fillStyle = "#7a5a34"; ctx.fillRect(x - 1, y, 2, 6);
      ctx.fillStyle = "#caa96b"; ctx.fillRect(x - w / 2, y - 7, w, 9);
      ctx.fillStyle = shade("#caa96b", 22); ctx.fillRect(x - w / 2, y - 7, w, 2);
      ctx.fillStyle = "#4e351d"; ctx.fillRect(x - w / 2, y + 1, w, 1);
      ctx.strokeStyle = "#6b4a28"; ctx.lineWidth = 1; ctx.strokeRect(x - w / 2 + 0.5, y - 6.5, w - 1, 8);
      ctx.fillStyle = l.corner ? "#7a3a1a" : "#3a2616"; ctx.fillText(l.text, x, y - 2.5);
    }
    ctx.restore();
  }

  /* ======================================================================
     SIDE PANEL (tap) + REAL live pane
     ====================================================================== */
  function inboxFor(a) {
    var db = (S.data && S.data.drain_board) || [];
    var id = String(a.agent_id || "").toLowerCase(), base = normBase(a.agent_id), bid = String(a.base_agent_id || "").toLowerCase();
    for (var i = 0; i < db.length; i++) {
      var g = String(db[i].agent || "").toLowerCase();
      if (g === id || g === base.toLowerCase() || (bid && g === bid)) return (db[i].items || []).slice(0, 3);
    }
    return [];
  }
  function moodLabel(m) { return { working: "working", idle: "idle · at desk", away: "away", wedged: "wedged / stale" }[m] || m; }

  function renderPanel(a, slide) {
    S.selected = a.agent_id;
    var mood = moodOf(a), acc = (PROJECTS[projOf(a.agent_id)] || PROJECTS.coord).color;
    var task = String(a.current_task || a.role_label || "").trim();
    var items = inboxFor(a);
    var inbox = items.length
      ? items.map(function (m) {
          return '<li class="msg p' + esc(normPri(m.priority)) + '"><span class="from">' + esc(m.from || "?") + '</span>' +
                 '<span class="subj">' + esc(trunc(m.subject || "", 130)) + '</span></li>';
        }).join("")
      : '<li class="none">no pending inbox items in the drain board</li>';

    var ctxChip = (a.__coord && a.ctx_pct != null) ? '<span class="chip">ctx ' + esc(a.ctx_pct) + '%</span>' : "";
    var liveState = (a.live && a.live.state) || a.status || "";
    var flaggedChip = a.flagged ? '<span class="chip wedged">⚑ flagged</span>' : "";
    var session = a.tmux_session || a.lane || a.agent_id;
    var cmd = 'scripts/bus_send.py --to ' + (a.agent_id || "") + ' --type update --subject "<subject>" --priority P2';

    el.panel.innerHTML =
      '<div class="ph" style="--acc:' + acc + '">' +
        '<canvas class="avatar" width="56" height="68"></canvas>' +
        '<div class="pid"><div class="name">' + esc(a.display_name || a.agent_id) + '</div>' +
          '<div class="sub">' + esc(a.agent_id) + '</div></div>' +
        '<button class="pclose" title="Close">✕</button>' +
      '</div>' +
      '<div class="pbody">' +
        '<div class="meta">' +
          '<span class="chip mood ' + mood + '"><i class="d ' + mood + '"></i>' + moodLabel(mood) + '</span>' +
          (a.model ? '<span class="chip model">' + esc(a.model) + '</span>' : "") +
          '<span class="chip">' + esc((ROOM_BY_KEY[roomKeyFor(a)] || {}).name || "—") + '</span>' +
          ctxChip + (liveState ? '<span class="chip">● ' + esc(liveState) + '</span>' : "") + flaggedChip +
        '</div>' +
        '<div class="sect"><div class="lbl">current task</div><div class="task">' + (task ? esc(task) : '<span class="none">—</span>') + '</div></div>' +
        '<div class="sect"><div class="lbl">live pane</div>' +
          '<div class="term"><div class="bar"><span class="b"></span><span class="lab">' + esc(session) + '</span></div>' +
          '<div class="tbody" id="fgPane"><div class="ln dim">connecting to tmux…</div></div></div></div>' +
        '<div class="sect"><div class="lbl">recent inbox · subjects</div><ul class="inbox">' + inbox + '</ul></div>' +
        '<div class="sect"><div class="lbl">send a message</div>' +
          '<div class="sendnote">The console has no bus write path — copy &amp; run from <code>~/wingmen/orchestrator</code> (with the venv):</div>' +
          '<div class="cmdbox"><code id="fgCmd">' + esc(cmd) + '</code><button class="copy" id="fgCopy">Copy</button></div>' +
        '</div>' +
      '</div>';

    el.panel.classList.remove("closed");
    // avatar
    try {
      var av = el.panel.querySelector(".avatar"), ag = av.getContext("2d"); ag.imageSmoothingEnabled = false;
      ag.clearRect(0, 0, av.width, av.height); ag.drawImage(sheetFor(a.agent_id)["stand_down"], 0, 0, SW, SH, 0, 0, SW * 3.4, SH * 3.4);
    } catch (e) {}
    el.panel.querySelector(".pclose").addEventListener("click", closePanel);
    var copy = el.panel.querySelector("#fgCopy");
    copy.addEventListener("click", function () {
      var txt = el.panel.querySelector("#fgCmd").textContent;
      var done = function () { copy.textContent = "Copied ✓"; setTimeout(function () { copy.textContent = "Copy"; }, 1400); };
      if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(txt).then(done, done);
      else { try { var ta = document.createElement("textarea"); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); document.body.removeChild(ta); done(); } catch (e) {} }
    });
    fetchPane(session);
  }
  function fetchPane(session) {
    var req = ++S.paneReq;
    fetch("/api/lanes/" + encodeURIComponent(session) + "/pane", { headers: authHeaders() })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (req !== S.paneReq) return; // a newer selection won
        var box = el.panel.querySelector("#fgPane"); if (!box) return;
        var text = d && (typeof d.text === "string" ? d.text : (typeof d === "string" ? d : ""));
        if (!text) { box.innerHTML = '<div class="ln dim">pane unavailable (session not live)</div>'; return; }
        var lines = String(text).replace(/\s+$/, "").split("\n");
        box.innerHTML = lines.map(function (l) { return '<div class="ln">' + esc(l || " ") + "</div>"; }).join("") + '<span class="cur"></span>';
        box.scrollTop = box.scrollHeight;
      })
      .catch(function () {
        if (req !== S.paneReq) return;
        var box = el.panel.querySelector("#fgPane"); if (box) box.innerHTML = '<div class="ln dim">pane unavailable</div>';
      });
  }
  function closePanel() { S.selected = null; if (el.panel) el.panel.classList.add("closed"); }

  /* ======================================================================
     BUS-TRAFFIC PANEL + CHROME (clock, legend, count)
     ====================================================================== */
  function renderBusFeed() {
    if (!el.feed) return;
    var src = S.courierSrc.slice(0, 8);
    if (!src.length) { el.feed.innerHTML = '<div class="row none">bus quiet — no pending mail</div>'; return; }
    el.feed.innerHTML = src.map(function (m) {
      return '<div class="row"><span class="pri" style="background:' + PRI_COL[m.pri] + '">' + m.pri + '</span>' +
        '<b>' + esc(normBase(m.from)) + '</b> → <b>' + esc(normBase(m.to)) + '</b><br>' +
        '<span class="s">' + esc(trunc(m.subject, 72)) + '</span></div>';
    }).join("");
  }
  function flashBus(m) {
    if (!el.feedHead) return;
    el.feedHead.classList.add("hot");
    setTimeout(function () { if (el.feedHead) el.feedHead.classList.remove("hot"); }, 600);
  }
  function buildLegend() {
    if (!el.legendRows) return;
    var items = [
      { sw: PROJECTS.cosem.color, t: "Cosem lab" },
      { sw: PROJECTS.irsyad.color, t: "Irsyad hall" },
      { sw: PROJECTS.ventures.color, t: "Ventures studio" },
      { sw: PROJECTS.fleet.color, t: "Fleet ops" },
      { sw: PROJECTS.coord.color, t: "Coordinators" }
    ];
    var html = items.map(function (i) { return '<div class="row"><span class="sw" style="background:' + i.sw + '"></span>' + i.t + "</div>"; }).join("");
    html += '<div class="row"><span class="em">⌨</span>typing · working</div>';
    html += '<div class="row"><span class="em">z</span>Zzz · away</div>';
    html += '<div class="row"><span class="em" style="color:#e2463c">!</span>wedged / flagged</div>';
    html += '<div class="row"><span class="em">✉</span>bus courier</div>';
    el.legendRows.innerHTML = html;
  }
  function updateClock() {
    if (!el.clockT) return;
    var h = dubaiHour(), hh = Math.floor(h), mm = Math.floor((h - hh) * 60), ph = currentPhase();
    el.clockT.textContent = String(hh).padStart(2, "0") + ":" + String(mm).padStart(2, "0") + (S.phaseOverride ? "*" : "");
    el.phaseDot.style.background = PHASE_TINT[ph].dotc;
    if (el.brandSub) el.brandSub.textContent = "Dubai · " + ph + " · " + S.chars.length + " agents";
  }
  function updateChrome() {
    var working = S.chars.filter(function (c) { return c.mood === "working"; }).length;
    if (el.count) el.count.textContent = S.chars.length + " agents · " + working + " working";
    updateClock();
  }

  /* ======================================================================
     AMBIENT SOUND (off by default)
     ====================================================================== */
  var audio = null, soundOn = false;
  function toggleSound() {
    soundOn = !soundOn;
    el.sound.classList.toggle("off", !soundOn);
    el.soundLbl.textContent = soundOn ? "ON" : "SOUND";
    if (soundOn) startAudio(); else stopAudio();
    toast("ambient: " + (soundOn ? "on" : "off"));
  }
  function startAudio() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
      audio = new AC();
      var master = audio.createGain(); master.gain.value = 0; master.connect(audio.destination);
      var o1 = audio.createOscillator(), o2 = audio.createOscillator();
      o1.type = "triangle"; o2.type = "triangle"; o1.frequency.value = 110; o2.frequency.value = 165; o2.detune.value = 4;
      var f = audio.createBiquadFilter(); f.type = "lowpass"; f.frequency.value = 600; f.Q.value = 2;
      var lfo = audio.createOscillator(), lfoG = audio.createGain(); lfo.frequency.value = 0.07; lfoG.gain.value = 220;
      lfo.connect(lfoG); lfoG.connect(f.frequency);
      o1.connect(f); o2.connect(f); f.connect(master);
      o1.start(); o2.start(); lfo.start();
      master.gain.linearRampToValueAtTime(0.045, audio.currentTime + 1.5);
      audio._master = master; audio._nodes = [o1, o2, lfo];
      audio._chime = setInterval(function () { if (!soundOn || currentPhase() === "night") return; if (Math.random() < 0.4) chime(); }, 2600);
    } catch (e) {}
  }
  function chime() {
    try {
      var t = audio.currentTime, o = audio.createOscillator(), g = audio.createGain();
      o.type = "sine"; o.frequency.value = 720 + Math.random() * 500; g.gain.value = 0; o.connect(g); g.connect(audio.destination);
      g.gain.linearRampToValueAtTime(0.03, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.5); o.start(t); o.stop(t + 0.55);
    } catch (e) {}
  }
  function stopAudio() {
    soundOn = false;
    if (el.sound) { el.sound.classList.add("off"); if (el.soundLbl) el.soundLbl.textContent = "SOUND"; }
    if (!audio) return;
    try { if (audio._chime) clearInterval(audio._chime); audio._master.gain.linearRampToValueAtTime(0, audio.currentTime + 0.3);
      var a = audio; setTimeout(function () { try { a._nodes.forEach(function (n) { n.stop(); }); a.close(); } catch (e) {} }, 500); } catch (e) {}
    audio = null;
  }

  var toastT = null;
  function toast(msg) { if (!el.toast) return; el.toast.textContent = msg; el.toast.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function () { el.toast.classList.remove("show"); }, 1600); }

  function applyTheme() {
    if (S.themeMode === "auto") el.ov.removeAttribute("data-theme"); else el.ov.setAttribute("data-theme", S.themeMode);
    if (el.themeLbl) el.themeLbl.textContent = S.themeMode.toUpperCase();
  }

  /* ======================================================================
     DOM SCAFFOLD
     ====================================================================== */
  var el = {};
  function ensureFonts() {
    // The fleet console is a strictly self-contained, offline-capable PWA with NO
    // external resources (fc-v77 review). So we do NOT load web fonts — the pixel
    // charm lives in the procedural canvas world (needs no font), and the HTML
    // panels fall back to the console's monospace stack. No-op by design.
    return;
  }
  function buildDOM() {
    ensureFonts();
    var style = document.createElement("style"); style.textContent = CSS; document.head.appendChild(style);

    var fab = document.createElement("button");
    fab.id = "fgFab"; fab.title = "Open Fleet Grove";
    fab.innerHTML = '<span class="e">🏢</span><span class="t">Fleet Grove</span>';
    fab.addEventListener("click", open);
    document.body.appendChild(fab); el.fab = fab;

    var ov = document.createElement("div"); ov.id = "fgRoot";
    ov.innerHTML =
      '<canvas id="fgScene"></canvas>' +
      '<div class="fg-hud">' +
        '<div class="fg-brand"><div class="logo"></div><div><h1>FLEET&nbsp;GROVE</h1><div class="sub" id="fgBrandSub">a living wingmen office</div></div></div>' +
        '<div class="fg-spacer"></div>' +
        '<div class="fg-tools">' +
          '<div class="fg-chip clock" id="fgClock" title="Dubai time — click to cycle day/night"><span class="dot" id="fgPhaseDot"></span><span class="t" id="fgClockT">--:--</span></div>' +
          '<div class="fg-chip" id="fgZoomOut" title="Zoom out"><span class="k">−</span></div>' +
          '<div class="fg-chip" id="fgZoomIn" title="Zoom in"><span class="k">+</span></div>' +
          '<div class="fg-chip" id="fgRecenter" title="Re-center">RECENTER</div>' +
          '<div class="fg-chip off" id="fgSound" title="Ambient sound">🔈 <span id="fgSoundLbl">SOUND</span></div>' +
          '<div class="fg-chip" id="fgTheme" title="Toggle theme">☀ <span id="fgThemeLbl">AUTO</span></div>' +
          '<div class="fg-chip x" id="fgClose" title="Close">✕</div>' +
        '</div>' +
      '</div>' +
      '<div class="fg-legend" id="fgLegend"><h2>WHO\'S IN THE GROVE</h2><div class="rows" id="fgLegendRows"></div>' +
        '<div class="hint">drag · scroll/pinch to zoom · tap an agent</div></div>' +
      '<div class="fg-ticker" id="fgTicker"><h2 id="fgFeedHead"><span class="pulse"></span> BUS TRAFFIC</h2><div class="feed" id="fgFeed">…</div></div>' +
      '<div class="fg-panel closed" id="fgPanel"></div>' +
      '<div class="fg-toast" id="fgToast"></div>';
    document.body.appendChild(ov); el.ov = ov;

    el.canvas = ov.querySelector("#fgScene"); el.ctx = el.canvas.getContext("2d", { alpha: false });
    el.panel = ov.querySelector("#fgPanel");
    el.legendRows = ov.querySelector("#fgLegendRows");
    el.feed = ov.querySelector("#fgFeed"); el.feedHead = ov.querySelector("#fgFeedHead");
    el.count = null; el.clockT = ov.querySelector("#fgClockT"); el.phaseDot = ov.querySelector("#fgPhaseDot");
    el.brandSub = ov.querySelector("#fgBrandSub");
    el.sound = ov.querySelector("#fgSound"); el.soundLbl = ov.querySelector("#fgSoundLbl");
    el.themeLbl = ov.querySelector("#fgThemeLbl"); el.toast = ov.querySelector("#fgToast");

    ov.querySelector("#fgClose").addEventListener("click", close);
    ov.querySelector("#fgZoomIn").addEventListener("click", function () { zoomBtn(1.3); });
    ov.querySelector("#fgZoomOut").addEventListener("click", function () { zoomBtn(1 / 1.3); });
    ov.querySelector("#fgRecenter").addEventListener("click", recenter);
    el.sound.addEventListener("click", toggleSound);
    ov.querySelector("#fgTheme").addEventListener("click", function () {
      S.themeMode = S.themeMode === "auto" ? "dark" : S.themeMode === "dark" ? "light" : "auto";
      lsSet("fg_theme", S.themeMode); applyTheme(); toast("theme: " + S.themeMode);
    });
    ov.querySelector("#fgClock").addEventListener("click", function () {
      S.phaseIdx = (S.phaseIdx + 1) % PHASES.length;
      S.phaseOverride = PHASES[S.phaseIdx] === "auto" ? null : PHASES[S.phaseIdx];
      updateClock(); toast(S.phaseOverride ? "time: " + S.phaseOverride : "time: auto (live Dubai)");
    });

    S.themeMode = lsGet("fg_theme") || "auto"; applyTheme();
    buildWorld(); buildLegend(); wireCanvas();
    window.addEventListener("keydown", function (e) { if (e.key === "Escape" && S.open) close(); });
    window.addEventListener("resize", function () { if (S.open) { resize(); } });
  }

  /* ======================================================================
     STYLES — scoped under #fgRoot; light default + dark via prefers + toggle
     ====================================================================== */
  var CSS =
    '#fgFab{position:fixed;right:14px;bottom:calc(14px + env(safe-area-inset-bottom));z-index:70;display:flex;align-items:center;gap:7px;' +
      'padding:10px 14px;border-radius:999px;cursor:pointer;font:700 13px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:#2a1c0e;' +
      'border:2px solid #7f663f;background:linear-gradient(135deg,#f3b24b,#c9622e);box-shadow:0 5px 0 rgba(60,40,15,.35);}' +
    '#fgFab .e{font-size:16px;} #fgFab:active{transform:translateY(2px);box-shadow:0 2px 0 rgba(60,40,15,.35);}' +
    'body.fg-lock{overflow:hidden;}' +
    // ---- overlay root + theme tokens
    '#fgRoot{position:fixed;inset:0;z-index:200;display:none;' +
      "--pixel:'Press Start 2P',ui-monospace,monospace; --term:'VT323',ui-monospace,monospace;" +
      '--chrome:#efe3c8;--chrome2:#e3d2ad;--ink:#3a2c1a;--ink-soft:#6b573a;--line:#b79a66;--panel:#f6edd6;--panel-edge:#c9ab73;' +
      '--accent:#c9622e;--shadow:rgba(60,40,15,.28);--term-bg:#1b2a1e;--term-ink:#9ff0a8;--term-dim:#5f9f6b;' +
      'font-family:var(--term);color:var(--ink);-webkit-font-smoothing:none;}' +
    '#fgRoot.show{display:block;}' +
    '#fgRoot:not([data-theme="light"]){@media (prefers-color-scheme:dark){' +
      '--chrome:#2a2320;--chrome2:#352c27;--ink:#f3e4c6;--ink-soft:#c3ab86;--line:#5a4a37;--panel:#241d1a;--panel-edge:#4a3c2e;' +
      '--accent:#e8824a;--shadow:rgba(0,0,0,.5);--term-bg:#0f1811;--term-ink:#9ff0a8;--term-dim:#4e8359;}}' +
    '#fgRoot[data-theme="dark"]{--chrome:#2a2320;--chrome2:#352c27;--ink:#f3e4c6;--ink-soft:#c3ab86;--line:#5a4a37;--panel:#241d1a;' +
      '--panel-edge:#4a3c2e;--accent:#e8824a;--shadow:rgba(0,0,0,.5);--term-bg:#0f1811;--term-ink:#9ff0a8;--term-dim:#4e8359;}' +
    '#fgRoot *{box-sizing:border-box;}' +
    '#fgScene{position:absolute;inset:0;width:100%;height:100%;image-rendering:pixelated;image-rendering:crisp-edges;display:block;cursor:grab;touch-action:none;background:#11161a;}' +
    '#fgScene.dragging{cursor:grabbing;}' +
    // ---- HUD
    '#fgRoot .fg-hud{position:absolute;left:0;right:0;top:0;display:flex;align-items:flex-start;gap:10px;padding:calc(10px + env(safe-area-inset-top)) 12px 10px;pointer-events:none;z-index:20;}' +
    '#fgRoot .fg-hud>*{pointer-events:auto;}' +
    '#fgRoot .fg-brand{display:flex;align-items:center;gap:10px;background:var(--chrome);border:3px solid var(--panel-edge);box-shadow:0 4px 0 var(--shadow),inset 0 2px 0 rgba(255,255,255,.15);padding:8px 12px 7px;border-radius:4px;}' +
    '#fgRoot .fg-brand .logo{width:26px;height:26px;flex:none;background:linear-gradient(135deg,#f3b24b,#c9622e);border:2px solid var(--ink);border-radius:3px;position:relative;box-shadow:0 2px 0 var(--shadow);}' +
    '#fgRoot .fg-brand .logo::after{content:"";position:absolute;inset:4px;background:radial-gradient(circle at 60% 35%,#fff3cf 0 2px,transparent 3px),repeating-linear-gradient(90deg,#3f7d54 0 2px,#2f5f40 2px 4px);border-radius:1px;opacity:.9;}' +
    '#fgRoot .fg-brand h1{font-family:var(--pixel);font-size:12px;line-height:1.5;margin:0;color:var(--ink);letter-spacing:.5px;text-shadow:1px 1px 0 var(--chrome2);}' +
    '#fgRoot .fg-brand .sub{font-size:15px;color:var(--ink-soft);margin-top:2px;}' +
    '#fgRoot .fg-spacer{flex:1 1 auto;}' +
    '#fgRoot .fg-tools{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end;max-width:70vw;}' +
    '#fgRoot .fg-chip{font-family:var(--term);font-size:17px;background:var(--chrome);border:3px solid var(--panel-edge);box-shadow:0 3px 0 var(--shadow),inset 0 2px 0 rgba(255,255,255,.14);color:var(--ink);padding:5px 10px 4px;border-radius:4px;cursor:pointer;display:flex;align-items:center;gap:7px;line-height:1;user-select:none;transition:transform .08s;}' +
    '#fgRoot .fg-chip:active{transform:translateY(2px);box-shadow:0 1px 0 var(--shadow);}' +
    '#fgRoot .fg-chip .k{font-family:var(--pixel);font-size:11px;color:var(--accent);}' +
    '#fgRoot .fg-chip.x{color:var(--accent);font-weight:700;}' +
    '#fgRoot .fg-chip .dot{width:9px;height:9px;border-radius:50%;background:#f4c84b;box-shadow:0 0 0 2px rgba(0,0,0,.15);}' +
    '#fgRoot .fg-chip.clock .t{font-size:17px;letter-spacing:.5px;min-width:62px;text-align:center;}' +
    '#fgRoot .fg-chip.off{opacity:.62;}' +
    // ---- legend
    '#fgRoot .fg-legend{position:absolute;left:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:20;background:var(--chrome);border:3px solid var(--panel-edge);box-shadow:0 4px 0 var(--shadow),inset 0 2px 0 rgba(255,255,255,.14);border-radius:4px;padding:9px 11px;max-width:min(46vw,300px);}' +
    '#fgRoot .fg-legend h2{font-family:var(--pixel);font-size:8px;margin:0 0 7px;color:var(--ink-soft);letter-spacing:.5px;}' +
    '#fgRoot .fg-legend .rows{display:flex;flex-direction:column;gap:5px;}' +
    '#fgRoot .fg-legend .row{display:flex;align-items:center;gap:8px;font-size:15px;color:var(--ink);line-height:1;}' +
    '#fgRoot .fg-legend .sw{width:14px;height:14px;border-radius:3px;border:2px solid var(--ink);flex:none;}' +
    '#fgRoot .fg-legend .em{font-size:15px;width:16px;text-align:center;}' +
    '#fgRoot .fg-legend .hint{margin-top:8px;font-size:13px;color:var(--ink-soft);border-top:2px dotted var(--line);padding-top:6px;}' +
    // ---- bus traffic
    '#fgRoot .fg-ticker{position:absolute;right:12px;bottom:calc(12px + env(safe-area-inset-bottom));z-index:20;width:min(44vw,320px);background:var(--chrome);border:3px solid var(--panel-edge);box-shadow:0 4px 0 var(--shadow),inset 0 2px 0 rgba(255,255,255,.14);border-radius:4px;overflow:hidden;}' +
    '#fgRoot .fg-ticker h2{font-family:var(--pixel);font-size:8px;margin:0;padding:8px 10px 6px;color:var(--ink-soft);border-bottom:2px solid var(--line);display:flex;align-items:center;gap:6px;letter-spacing:.5px;}' +
    '#fgRoot .fg-ticker h2 .pulse{width:8px;height:8px;border-radius:50%;background:var(--accent);animation:fgpulse 1.6s infinite;}' +
    '#fgRoot .fg-ticker h2.hot .pulse{animation:none;background:#e2463c;box-shadow:0 0 8px #e2463c;}' +
    '@keyframes fgpulse{0%,100%{opacity:.35;}50%{opacity:1;}}' +
    '#fgRoot .fg-ticker .feed{font-size:14px;line-height:1.3;padding:7px 10px;color:var(--ink);max-height:168px;overflow:auto;}' +
    '#fgRoot .fg-ticker .feed .row{margin-bottom:7px;}' +
    '#fgRoot .fg-ticker .feed .row.none{color:var(--ink-soft);}' +
    '#fgRoot .fg-ticker .feed b{color:var(--accent);}' +
    '#fgRoot .fg-ticker .feed .s{color:var(--ink-soft);}' +
    '#fgRoot .fg-ticker .feed .pri{font-family:var(--pixel);font-size:8px;padding:2px 4px;border-radius:3px;margin-right:5px;color:#fff;vertical-align:middle;}' +
    // ---- side panel
    '#fgRoot .fg-panel{position:absolute;top:0;right:0;bottom:0;z-index:40;width:min(390px,94vw);background:var(--panel);border-left:4px solid var(--panel-edge);box-shadow:-10px 0 30px var(--shadow);transform:translateX(0);transition:transform .32s cubic-bezier(.2,.9,.25,1);display:flex;flex-direction:column;}' +
    '#fgRoot .fg-panel.closed{transform:translateX(104%);}' +
    '#fgRoot .fg-panel .ph{padding:14px 14px 12px;border-bottom:3px solid var(--line);display:flex;gap:12px;align-items:flex-start;background:linear-gradient(180deg,var(--chrome),var(--panel));border-top:4px solid var(--acc,var(--accent));}' +
    '#fgRoot .fg-panel .avatar{width:56px;height:68px;flex:none;image-rendering:pixelated;background:#0003;border:2px solid var(--line);border-radius:4px;}' +
    '#fgRoot .fg-panel .pid{flex:1;min-width:0;}' +
    '#fgRoot .fg-panel .pid .name{font-family:var(--pixel);font-size:11px;line-height:1.5;color:var(--ink);word-break:break-word;}' +
    '#fgRoot .fg-panel .pid .sub{font-size:15px;color:var(--ink-soft);margin-top:5px;word-break:break-all;}' +
    '#fgRoot .fg-panel .pclose{font-family:var(--pixel);font-size:11px;cursor:pointer;background:var(--chrome2);border:2px solid var(--line);color:var(--ink);border-radius:4px;padding:6px 8px;line-height:1;flex:none;}' +
    '#fgRoot .fg-panel .pbody{padding:12px 14px;overflow:auto;flex:1;}' +
    '#fgRoot .fg-panel .meta{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px;}' +
    '#fgRoot .fg-panel .chip{font-size:14px;padding:4px 8px;border-radius:4px;border:2px solid var(--line);background:var(--chrome);color:var(--ink);line-height:1;display:flex;align-items:center;gap:6px;}' +
    '#fgRoot .fg-panel .chip .d{width:9px;height:9px;border-radius:50%;}' +
    '#fgRoot .fg-panel .chip .d.working{background:#3f9d57;} #fgRoot .fg-panel .chip .d.idle{background:#d89a3a;} #fgRoot .fg-panel .chip .d.away{background:#8a8a8a;} #fgRoot .fg-panel .chip .d.wedged{background:#e2463c;}' +
    '#fgRoot .fg-panel .chip.model{color:var(--accent);} #fgRoot .fg-panel .chip.wedged{color:#e2463c;border-color:#e2463c;}' +
    '#fgRoot .fg-panel .sect{margin-bottom:12px;}' +
    '#fgRoot .fg-panel .lbl{font-family:var(--pixel);font-size:8px;color:var(--ink-soft);letter-spacing:.5px;margin-bottom:6px;}' +
    '#fgRoot .fg-panel .task{font-size:16px;color:var(--ink);line-height:1.4;word-break:break-word;}' +
    '#fgRoot .fg-panel .none{color:var(--ink-soft);}' +
    '#fgRoot .fg-panel .term{background:var(--term-bg);border:3px solid #000;border-radius:6px;box-shadow:inset 0 0 0 1px #2c4a33,0 3px 0 var(--shadow);padding:10px 12px;font-family:var(--term);font-size:15px;line-height:1.25;color:var(--term-ink);position:relative;overflow:hidden;}' +
    '#fgRoot .fg-panel .term::before{content:"";position:absolute;inset:0;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.14) 0 1px,transparent 1px 3px);mix-blend-mode:multiply;opacity:.5;}' +
    '#fgRoot .fg-panel .term .bar{display:flex;align-items:center;gap:6px;color:var(--term-dim);font-size:13px;margin-bottom:6px;}' +
    '#fgRoot .fg-panel .term .bar .b{width:9px;height:9px;border-radius:50%;background:#e2584a;box-shadow:14px 0 0 #e3b341,28px 0 0 #5fbf63;}' +
    '#fgRoot .fg-panel .term .bar .lab{margin-left:auto;word-break:break-all;}' +
    '#fgRoot .fg-panel .term .tbody{max-height:240px;overflow:auto;white-space:pre-wrap;}' +
    '#fgRoot .fg-panel .term .ln{white-space:pre-wrap;word-break:break-word;}' +
    '#fgRoot .fg-panel .term .ln.dim{color:var(--term-dim);}' +
    '#fgRoot .fg-panel .term .cur{display:inline-block;width:8px;height:14px;background:var(--term-ink);vertical-align:-2px;animation:fgblink 1s steps(1) infinite;}' +
    '@keyframes fgblink{50%{opacity:0;}}' +
    '#fgRoot .fg-panel ul.inbox{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:7px;}' +
    '#fgRoot .fg-panel ul.inbox .msg{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border-radius:5px;background:var(--chrome);border:2px solid var(--line);border-left:4px solid #4a90c2;}' +
    '#fgRoot .fg-panel ul.inbox .msg.pP0{border-left-color:#e2463c;} #fgRoot .fg-panel ul.inbox .msg.pP1{border-left-color:#e8824a;} #fgRoot .fg-panel ul.inbox .msg.pP3{border-left-color:#8a9bb0;}' +
    '#fgRoot .fg-panel ul.inbox .from{font-size:13px;color:var(--ink-soft);}' +
    '#fgRoot .fg-panel ul.inbox .subj{font-size:15px;color:var(--ink);word-break:break-word;}' +
    '#fgRoot .fg-panel ul.inbox .none{padding:6px 0;}' +
    '#fgRoot .fg-panel .sendnote{font-size:14px;color:var(--ink-soft);margin-bottom:8px;}' +
    '#fgRoot .fg-panel .sendnote code{color:var(--accent);}' +
    '#fgRoot .fg-panel .cmdbox{display:flex;gap:8px;align-items:flex-start;}' +
    '#fgRoot .fg-panel .cmdbox code{flex:1;font-family:ui-monospace,monospace;font-size:13px;line-height:1.4;color:var(--term-ink);background:var(--term-bg);padding:9px 10px;border-radius:5px;border:2px solid var(--line);word-break:break-all;}' +
    '#fgRoot .fg-panel .copy{flex:none;background:var(--accent);color:#1a1008;border:2px solid var(--ink);border-radius:5px;padding:9px 12px;font:700 13px/1 ui-monospace,monospace;cursor:pointer;}' +
    '#fgRoot .fg-panel .copy:active{transform:translateY(1px);}' +
    // ---- toast
    '#fgRoot .fg-toast{position:absolute;left:50%;top:calc(14px + env(safe-area-inset-top));transform:translateX(-50%) translateY(-16px);z-index:30;background:var(--chrome);border:3px solid var(--panel-edge);box-shadow:0 4px 0 var(--shadow);border-radius:4px;padding:8px 14px;font-size:15px;color:var(--ink);opacity:0;transition:opacity .3s,transform .3s;pointer-events:none;max-width:84vw;text-align:center;}' +
    '#fgRoot .fg-toast.show{opacity:1;transform:translateX(-50%) translateY(0);}' +
    // ---- phone: stop the legend + bus panels overlapping at 390px
    '@media (max-width:640px){' +
      '#fgRoot .fg-brand h1{font-size:10px;} #fgRoot .fg-brand .sub{display:none;}' +
      '#fgRoot .fg-tools{max-width:58vw;} #fgRoot .fg-chip{font-size:14px;padding:5px 8px 4px;}' +
      '#fgRoot .fg-chip.clock .t{min-width:52px;font-size:14px;}' +
      '#fgRoot .fg-legend{max-width:44vw;padding:7px 8px;} #fgRoot .fg-legend .hint{display:none;}' +
      '#fgRoot .fg-ticker{width:44vw;} #fgRoot .fg-ticker .feed{max-height:120px;font-size:13px;}' +
    '}' +
    '@media (max-width:420px){' +
      // very narrow: shrink both further + hide secondary legend rows so neither grows tall enough to collide
      '#fgRoot .fg-legend{max-width:42vw;} #fgRoot .fg-ticker{width:42vw;} #fgRoot .fg-ticker .feed{max-height:96px;}' +
    '}';

  /* ======================================================================
     AUTOMATION SEAM — the render harness drives these
     ====================================================================== */
  window.__officeAPI = {
    open: open,
    close: close,
    tapFirst: function () {
      if (!el.ov) buildDOM();
      var ch = S.chars[0];
      if (ch) { S.selected = ch.id; renderPanel(ch.agent, true); return ch.id; }
      return null;
    },
    agentCount: function () { return S.chars.length; },
    zoomToBubbles: function () {
      if (!S.seats.length) return;
      var room = null;
      for (var i = 0; i < S.chars.length; i++) { if (S.chars[i].mood === "working") { room = S.chars[i].room; break; } }
      if (!room) room = S.seats[0].room;
      S.cam.scale = Math.min(S.cam.max, 4.2);
      var cx = (room.x + room.w / 2) * TILE, cy = (room.y + room.h / 2) * TILE;
      S.cam.x = cx - (S.view.w / 2) / S.cam.scale; S.cam.y = cy - (S.view.h / 2) / S.cam.scale; clampCam();
      draw(performance.now());
    }
  };

  /* ======================================================================
     BOOT (build the fab early so it's available; overlay inert until open)
     ====================================================================== */
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", buildDOM);
  else buildDOM();
  document.addEventListener("visibilitychange", function () { if (S.open && !document.hidden) S.lastT = performance.now(); });
})();
