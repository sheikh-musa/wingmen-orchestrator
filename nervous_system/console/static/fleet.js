// Fleet Console — Command Surface (Approach C, fc-v55). ONE page: an ambient
// monitoring strip up top (pulse headline + stat row + pool chips + needs
// callouts), the LANES as the dense spine (context-ring tiles), and a bottom
// ACTION SHEET that raises the FULL per-lane action set on tap — Peek · Retask ·
// Recycle · Attach · Boot · Stand-down · Mute · Pin, plus the token/model pointer
// + dry-run Preview / armed Apply folded in from the old /lanes manager. Multi-
// select drives the bulk account switch. Monitoring reads /api/fleet (fast, 8s);
// the sheet's token/model controls + bulk switch lazily read /api/token-truth
// (cached, ~30s) so the slow remote-hub SSH scan never gates the main payload.
//
// Auth is IP-allowlist-first; a breakglass token (rare) rides Authorization from
// localStorage. Vanilla JS, no deps. Preserves the fc-v49 PWA machinery verbatim:
// service-worker version gate, last-good cache, pull-to-refresh, readable peek,
// two-tap-instead-of-window.confirm (iOS suppresses confirm in a standalone PWA).
(function () {
  "use strict";

  var token = localStorage.getItem("console_token") || "";
  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }

  function esc(s) {
    return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtAge(s) {
    if (s == null) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m";
    if (s < 86400) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function fmtTok(n) {
    if (n == null) return "—";
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return Math.round(n / 1e3) + "k";
    return String(n);
  }
  function setLive(ok, label) {
    var d = $("liveDot");
    if (!d) return;
    d.className = "live" + (ok ? "" : " down");
    d.textContent = label || (ok ? "● live" : "● offline");
  }
  function toast(msg, bad) {
    var t = $("toast");
    if (!t) return;
    t.textContent = msg; t.className = "toast show" + (bad ? " bad" : "");
    clearTimeout(t._h); t._h = setTimeout(function () { t.className = "toast"; }, 3600);
  }
  function shortModel(m) { return m ? String(m).replace(/^claude-/, "") : ""; }

  // fp-prefix -> pool nickname. auth_fp = sha256(OAuth token)[:12]. SSOT for the
  // set = nervous_system/console/pools.py (backend) — keep this + irsyad.js
  // acctForFp in lockstep (op#20684). Order = display order of the key roll-up.
  var POOL_FP = [["68142948", "Musa"], ["e1dfa48", "musa2"], ["582043088", "Syed"]];
  var POOL_CLS = { "Musa": "musa", "musa2": "musa2", "Syed": "syed" };
  function tokName(fp) {
    fp = fp || "";
    for (var i = 0; i < POOL_FP.length; i++) if (fp.indexOf(POOL_FP[i][0]) === 0) return POOL_FP[i][1];
    return fp ? fp.slice(0, 8) : "";
  }
  // The pool NICKNAME for a lane/coordinator/bloat row: the backend-derived
  // `pool` (the only key info the hosted/phone payload carries — never the raw
  // fp), else derived from auth_fp (local view). "" = unknown.
  function poolOf(o) {
    if (!o) return "";
    if (o.pool) return String(o.pool);
    return (o.auth_fp && POOL_CLS[tokName(o.auth_fp)]) ? tokName(o.auth_fp) : "";
  }
  // Per-lane key chip. `pool` wins (both consoles); an unknown-but-present fp
  // (local only) still shows its short id so an off-account lane is visible.
  function tokChip(fp, pool) {
    fp = fp || ""; pool = pool || "";
    var cls = POOL_CLS[pool];
    if (cls) return '<span class="tok ' + cls + '" title="' + esc(fp || pool) + '">' + esc(pool) + '</span>';
    return fp ? '<span class="tok other" title="' + esc(fp) + '">🔑 ' + esc(fp.slice(0, 6)) + '</span>' : '';
  }
  // Per-row MODEL chip (Musa op#20716) — visible WITHOUT expanding, next to the key
  // chip on every tile + coordinator chip. `model` is the backend-resolved id,
  // `src` its provenance: "proc" = the live process argv (ground truth); "boot" =
  // declared in the lane's boot string; "registry" = fleet_lanes default. A non-proc
  // source renders with a leading "~" (declared, not observed). Unknown => NOTHING
  // (never "null"). Never carries an fp.
  var MODEL_SRC_TITLE = { proc: "model (live process)", boot: "model (declared at boot)", registry: "model (registry default)" };
  function mdlChip(model, src) {
    var m = shortModel(model);
    if (!m) return "";
    var approx = src !== "proc";
    return '<span class="tok mdl' + (approx ? " approx" : "") + '" title="' + esc(MODEL_SRC_TITLE[src] || "model") + '">' +
      (approx ? "~" : "") + esc(m) + '</span>';
  }
  // CAI-RESP-1434: the operator's ARMED key — the second factor app.py now requires
  // on /api/reset + /api/apply-armed even from an allowlisted IP. Lives ONLY in
  // localStorage('fc.armedKey'); rides ONLY those two POSTs as X-Armed-Bearer (the
  // Authorization slot is the breakglass / hosted bearer); never logged, never in a
  // payload or URL.
  var ARMED_KEY_LS = "fc.armedKey";
  function armedKey() { try { return localStorage.getItem(ARMED_KEY_LS) || ""; } catch (e) { return ""; } }
  function setArmedKey(v) { try { if (v) localStorage.setItem(ARMED_KEY_LS, v); else localStorage.removeItem(ARMED_KEY_LS); } catch (e) {} }
  function armedHeaders() { var k = armedKey(); return k ? { "X-Armed-Bearer": k } : {}; }
  function armedKeyMissing() {
    toast("armed key required — set it in Lane manager", true);
    var inp = $("armedKey");
    if (inp) { inp.classList.add("miss"); try { inp.focus(); } catch (e) {} }
  }
  // At-a-glance "which lanes are on which key" (op#20684): EVERY lane with a known
  // key counted per pool, in POOL_FP order, unknown-key lanes last as "?". This is
  // an ASSIGNMENT question, not a liveness one (Musa, fc-v63): a cross-host lane
  // (irsyad on gzb reads offline/DARK from the Mini) is still ON its key, so it
  // is counted — dropping it made musa2 vanish from the roll-up while a musa2
  // lane sat visibly on the board. Only a lane with no key info is skipped.
  // Musa op#20715 (fc-v64): the SINGLETONS (coordinators — Hub, Nazim, cai, SRE,
  // Finance, Quality) are on keys too, so each chip total = lanes + singletons on
  // that pool, with the split visible in the chip title ("Musa: 3 lanes + 4
  // singletons") and in the tapped hint line.
  // Pure — returns { counts: {pool: total}, lanes: {pool: n}, singletons: {pool: n},
  // total, html } for the #keyRoll row.
  function poolKey(o) { return poolOf(o) || (o && o.auth_fp ? "?" : ""); }
  function poolRollup(lanes, coordinators, active) {
    var counts = {}, laneN = {}, singN = {}, total = 0;
    (lanes || []).forEach(function (l) {
      var p = poolKey(l); if (!p) return;
      counts[p] = (counts[p] || 0) + 1; laneN[p] = (laneN[p] || 0) + 1; total++;
    });
    (coordinators || []).forEach(function (c) {
      var p = poolKey(c); if (!p) return;
      counts[p] = (counts[p] || 0) + 1; singN[p] = (singN[p] || 0) + 1; total++;
    });
    var order = POOL_FP.map(function (x) { return x[1]; });
    Object.keys(counts).forEach(function (k) { if (order.indexOf(k) < 0) order.push(k); });
    function split(p) {
      var ln = laneN[p] || 0, sn = singN[p] || 0;
      return ln + " lane" + (ln === 1 ? "" : "s") + " + " + sn + " singleton" + (sn === 1 ? "" : "s");
    }
    var chips = order.filter(function (p) { return counts[p]; }).map(function (p) {
      var cls = POOL_CLS[p] || "other", on = active && active === p;
      var name = p === "?" ? "unknown" : p;
      return '<button type="button" class="krchip tok ' + cls + (on ? " on" : "") + '" data-pool="' + esc(p) + '"' +
        ' title="' + esc(name + ": " + split(p)) + '">' + esc(name) + '<b>' + counts[p] + '</b></button>';
    });
    var hint;
    if (active) hint = "showing " + (active === "?" ? "unknown" : active) + " · " + (laneN[active] || 0) + " lane" + ((laneN[active] || 0) === 1 ? "" : "s") +
      " · " + (singN[active] || 0) + " singleton" + ((singN[active] || 0) === 1 ? "" : "s") + " · tap again for all";
    else hint = "lanes + singletons";
    var html = chips.length ? '<span class="krl">keys</span>' + chips.join("") + '<span class="krhint">' + esc(hint) + '</span>' : "";
    return { counts: counts, lanes: laneN, singletons: singN, total: total, html: html };
  }
  // Collapsed "idle & fine" group (Musa op#20716): the key split + model split
  // INLINE so nothing needs expanding. Pure — { keys: [[pool, n]…] in POOL_FP order
  // (unknown-key lanes skipped), models: [[shortModel, n]…] by count desc, names }.
  function routineSummary(routine) {
    var kc = {}, mc = {};
    (routine || []).forEach(function (l) {
      var p = poolOf(l); if (p) kc[p] = (kc[p] || 0) + 1;
      var m = shortModel(l && l.model); if (m) mc[m] = (mc[m] || 0) + 1;
    });
    var korder = POOL_FP.map(function (x) { return x[1]; });
    Object.keys(kc).forEach(function (k) { if (korder.indexOf(k) < 0) korder.push(k); });
    var keys = korder.filter(function (p) { return kc[p]; }).map(function (p) { return [p, kc[p]]; });
    var models = Object.keys(mc).map(function (m) { return [m, mc[m]]; })
      .sort(function (a, b) { return b[1] - a[1] || (a[0] < b[0] ? -1 : 1); });
    return { keys: keys, models: models, names: (routine || []).map(function (l) { return l.agent_id; }) };
  }

  // Which reset "body" the console can clear (POST /api/reset). Only the three
  // singletons that don't self-reset are resettable; everything else returns ""
  // (Recycle then reads as not-yet-wired). Mirrors the Telegram clear allowlist;
  // the backend re-checks — this only shapes the affordance.
  function resetBodyFor(agentId) {
    if (agentId === "orch-console") return "nazim";
    if (agentId === "cai") return "cai";
    if (agentId === "cc-orchestrator") return "hub";
    return "";
  }

  // ---- weekly Max-pool usage (op#9770/#12617) — verbatim from fc-v49 ---------
  var POOL_STALE_S = 1800;
  function daysToReset(resets_at) {
    if (!resets_at) return null;
    var t = Date.parse(String(resets_at));
    if (isNaN(t)) t = Date.parse(String(resets_at).replace(" ", "T"));
    if (isNaN(t)) return null;
    var d = (t - Date.now()) / 86400000;
    return d > 0 ? d : null;
  }
  // Musa op#20644/#20657: countdown to a window reset. minutesToReset -> whole
  // minutes (null when resets_at is missing / unparsable / already past — same
  // parse as daysToReset); hoursToReset -> whole hours of the same; fmtReset ->
  // "Xd Yh" at >= 48h, "Xh Ym" under, "—" for null.
  function minutesToReset(resets_at) {
    var d = daysToReset(resets_at);
    return d == null ? null : Math.floor(d * 1440);
  }
  function hoursToReset(resets_at) {
    var m = minutesToReset(resets_at);
    return m == null ? null : Math.floor(m / 60);
  }
  function fmtReset(resets_at) {
    var m = minutesToReset(resets_at);
    if (m == null) return "—";
    var h = Math.floor(m / 60);
    if (h >= 48) return Math.floor(h / 24) + "d " + (h % 24) + "h";
    return h + "h " + (m % 60) + "m";
  }
  // Musa op#20680: the 5h window has a FIXED 5-hour cadence, but the writer
  // (weekly_limit_monitor) only refreshes every ~15 min, so between refreshes a
  // boundary can pass and resets_5h_at sits a few minutes in the PAST -> the 5H
  // row showed a bare "—". Roll a past resets_5h_at forward in +5h steps to the
  // next FUTURE boundary (ISO string, so the reset formatters parse it as-is).
  // null only when genuinely absent / unparsable (or it won't converge).
  var FIVE_H_MS = 5 * 3600 * 1000;
  function next5hBoundary(resets_5h_at) {
    if (!resets_5h_at) return null;
    var t = Date.parse(String(resets_5h_at));
    if (isNaN(t)) t = Date.parse(String(resets_5h_at).replace(" ", "T"));
    if (isNaN(t)) return null;
    var now = Date.now(), i = 0;
    while (t <= now && i < 100) { t += FIVE_H_MS; i++; }
    if (t <= now) return null;
    return new Date(t).toISOString();
  }
  function paceAdvisory(p) {
    var bits = [];
    if (p.pace != null) bits.push(esc(Number(p.pace).toFixed(1)) + "x");
    if (p.projected_pct != null) bits.push("proj " + Math.round(Number(p.projected_pct)) + "%");
    var out = "";
    if (bits.length) out += '<span class="pooladv">' + bits.join(" · ") + '</span>';
    if (p.runway_days != null) {
      var rd = Number(p.runway_days);
      var dtr = daysToReset(p.resets_at);
      var warn = (dtr != null && rd < dtr);
      out += '<span class="poolrun' + (warn ? " warn" : "") + '">runway ' + rd.toFixed(1) + "d</span>";
    }
    return out;
  }
  // Level of ONE window: good <75, warn 75-89, bad >=90; "" when unread.
  function poolLevel(pct) {
    if (pct == null) return "";
    return pct >= 90 ? "bad" : (pct >= 75 ? "warn" : "good");
  }
  // One usage row of the card: label + bar + % + countdown. `pct` null -> "—", empty bar.
  function poolWindowRow(label, pct, resets_at, resetWord) {
    var lvl = poolLevel(pct);
    var w = pct == null ? 0 : Math.max(0, Math.min(100, Math.round(pct)));
    return '<div class="poolwin ' + lvl + '">'
      + '<span class="poolwl">' + label + '</span>'
      + '<span class="poolbar"><i style="width:' + w + '%"></i></span>'
      + '<b>' + (pct == null ? "—" : Math.round(pct) + "%") + '</b>'
      + '<span class="poolreset">' + resetWord + ' ' + (minutesToReset(resets_at) == null ? "—" : "in " + fmtReset(resets_at)) + '</span>'
      + '</div>';
  }
  // Per-key CARD (op#20657): weekly + 5h rows, coloured by the WORSE window.
  function poolChip(p) {
    var pct = p.pct_7d;
    if (pct == null) return "";
    var stale = (p.updated_age_s != null && p.updated_age_s > POOL_STALE_S);
    var worst = Math.max(Number(pct), p.pct_5h == null ? 0 : Number(p.pct_5h));
    var cls = stale ? "stale" : poolLevel(worst);
    var status = p.status_7d || p.status || "";
    var title = p.pool + " Max pool: " + Math.round(pct) + "% (7d)"
      + (p.pct_5h != null ? ", " + Math.round(p.pct_5h) + "% (5h)" : "")
      + (status ? " · " + status : "")
      + (p.pace != null ? " · pace " + Number(p.pace).toFixed(2) + "x" : "")
      + (p.projected_pct != null ? " · projected " + Math.round(Number(p.projected_pct)) + "%" : "")
      + (p.runway_days != null ? " · runway " + Number(p.runway_days).toFixed(1) + "d" : "")
      + " · weekly resets in " + fmtReset(p.resets_at)
      + (p.resets_at ? " (" + esc(p.resets_at) + ")" : "")
      + " · 5h window resets in " + fmtReset(next5hBoundary(p.resets_5h_at))
      + (p.resets_5h_at ? " (" + esc(p.resets_5h_at) + ")" : "")
      + (p.updated_age_s != null ? " · read " + fmtAge(p.updated_age_s) + " ago" : "")
      + (stale ? " · STALE (monitor stalled)" : "");
    return '<div class="poolrow poolcard ' + cls + '" title="' + esc(title) + '">'
      + '<div class="poolhead">'
      + '<span class="poolchip ' + cls + '">' + esc(p.pool) + (stale ? " ⚠" : "") + '</span>'
      + (status ? '<span class="poolstatus">' + esc(status) + '</span>' : "")
      + (stale ? '<span class="poolstatus">stale ' + fmtAge(p.updated_age_s) + '</span>' : "")
      + '</div>'
      + poolWindowRow("wk", pct, p.resets_at, "resets")
      + poolWindowRow("5h", p.pct_5h, next5hBoundary(p.resets_5h_at), "window resets")
      + '<div class="pooladvrow">' + paceAdvisory(p) + '</div>'
      + '</div>';
  }
  function renderPoolUsage(rows) {
    var el = $("poolUsage");
    if (!el) return;
    el.innerHTML = (rows && rows.length) ? rows.map(poolChip).join("") : "";
  }

  // ---- build identity + version gate (op#3640) — verbatim from fc-v49 --------
  var APP_BUILD = 'fc-v67';
  function verNum(v) { var m = /^fc-v(\d+)$/.exec(String(v == null ? "" : v)); return m ? parseInt(m[1], 10) : null; }
  function renderBuild(serverVersion, serverSha) {
    var el = $("build");
    if (!el) return;
    var sv = verNum(serverVersion), cv = verNum(APP_BUILD);
    if (sv != null && cv != null && sv > cv) {
      el.textContent = APP_BUILD + " → " + serverVersion;
      el.className = "build stale";
      return;
    }
    el.textContent = APP_BUILD + (serverSha ? " · " + serverSha : "");
    el.className = "build";
  }
  function hardResetForVersion(target) {
    var key = "fleet_hardreset_" + target;
    try { if (sessionStorage.getItem(key)) return; } catch (e) {}
    var bust = "?_hr=" + Date.now();
    var okText = function (r) { if (!r || !r.ok) return Promise.reject(new Error("unreachable")); return r.text(); };
    Promise.all([
      fetch("/" + bust, { cache: "no-store" }).then(okText),
      fetch("/static/fleet.js" + bust, { cache: "no-store" }).then(okText)
    ]).then(function (parts) {
      if (parts.some(function (t) { return !t || t.length < 200; })) throw new Error("shell incomplete");
      try { sessionStorage.setItem(key, "1"); } catch (e) {}
      var finish = function () { window.location.reload(); };
      var jobs = [];
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        jobs.push(navigator.serviceWorker.getRegistrations()
          .then(function (rs) { return Promise.all(rs.map(function (r) { return r.unregister(); })); }).catch(function () {}));
      }
      if (window.caches && caches.keys) {
        jobs.push(caches.keys().then(function (ks) { return Promise.all(ks.map(function (k) { return caches.delete(k); })); }).catch(function () {}));
      }
      Promise.all(jobs).then(finish, finish);
    }).catch(function () {});
  }
  function checkVersion() {
    return fetch("/api/version?_=" + Date.now(), { cache: "no-store" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (v) {
        if (!v) return;
        renderBuild(v.version, v.sha);
        var sv = verNum(v.version), cv = verNum(APP_BUILD);
        if (sv != null && cv != null && sv > cv) hardResetForVersion(v.version);
      }).catch(function () {});
  }
  function loadBuild() { renderBuild(null, null); checkVersion(); setInterval(checkVersion, 60000); }

  // ---- last-good cache — verbatim from fc-v49 --------------------------------
  var LAST_GOOD_KEY = "fleet_last_good";
  function saveLastGood(d) { try { localStorage.setItem(LAST_GOOD_KEY, JSON.stringify(d)); } catch (e) {} }
  function loadLastGood() { try { return JSON.parse(localStorage.getItem(LAST_GOOD_KEY) || "null"); } catch (e) { return null; } }

  var UNAUTH =
    '<div class="empty">Not authorized from this device/network. If this is expected ' +
    "(off-tailnet, IP changed), set a breakglass token in localStorage('console_token') and pull to refresh.</div>";

  // ---- pulse headline + stat row --------------------------------------------
  function renderPulse(p) {
    var needs = p.needs_you || 0;
    var health = p.pane_health || "clear";
    var worst = p.pane_worst;
    var pl = $("pulse");
    var attn = needs > 0 || health === "alert" || health === "unknown";
    pl.className = "pulse " + (attn ? "attn" : "clear");
    var msg;
    if (needs > 0) msg = needs === 1 ? "1 thing needs you" : needs + " things need you";
    // op#31750: a bloated lane in the resting banner ALWAYS shows who's on it (the SRE
    // disposition), not a bare "context building" — so Musa sees it's handled + looks away.
    else if (health === "alert") msg = worst ? (worst.label + " " + worst.pct + "% — " +
      ((worst.sre_disposition && worst.sre_disposition.label) || "context building")) : "context building";
    else if (health === "unknown") msg = "bloat feed offline — status unknown";
    else msg = "All clear";
    $("pulseBig").textContent = msg;
  }
  function statCell(cls, n) {
    return '<div class="s ' + cls + '"><div class="n">' + n + '</div><div class="l">' + cls + '</div></div>';
  }
  // fc-v52: the BLOAT cell is a tap target that reveals the top-3 offenders
  // (#topBloat) inline — the resting view stays clean. Only tappable when there's
  // actually bloat to show.
  var bloatExpanded = false;
  function renderStat(p, bloatCount) {
    var el = $("statRow");
    if (!el) return;
    var bc = bloatCount || 0;
    var bloatCell = '<div id="bloatCell" class="s bloat' + (bc ? " tapx" : "") + (bc && bloatExpanded ? " on" : "") + '"' +
      (bc ? ' role="button" tabindex="0" aria-label="show top bloat"' : "") +
      '><div class="n">' + bc + '</div><div class="l">bloat</div></div>';
    el.innerHTML = statCell("work", p.working || 0) + bloatCell +
                   statCell("idle", p.idle || 0) + statCell("off", p.offline || 0);
    syncTopBloat();
  }
  function syncTopBloat() {
    var el = $("topBloat");
    if (!el) return;
    var has = el.getAttribute("data-has") === "1";
    el.classList.toggle("show", !!(has && bloatExpanded));
  }
  function toggleBloat() {
    bloatExpanded = !bloatExpanded;
    var c = $("bloatCell");
    if (c) c.classList.toggle("on", bloatExpanded);
    syncTopBloat();
  }

  // ---- top-bloat glance (op#18542) — pure helpers kept for the node tests ----
  function pickTopBloat(rows) {
    var list = (rows || []).filter(function (r) { return r && r.pct != null; });
    var best = {};
    list.forEach(function (r) {
      var k = r.agent || r.sub_tag;
      var cur = best[k];
      if (!cur) { best[k] = r; return; }
      var rTag = r.sub_tag ? 1 : 0, cTag = cur.sub_tag ? 1 : 0;
      if (rTag !== cTag ? rTag > cTag : r.pct > cur.pct) best[k] = r;
    });
    var uniq = Object.keys(best).map(function (k) { return best[k]; });
    uniq.sort(function (a, b) { return b.pct - a.pct; });
    return uniq.slice(0, 3);
  }
  function coordCtxRows(coords) {
    return (coords || []).filter(function (c) { return c && c.ctx_pct != null; })
      .map(function (c) {
        return { agent: c.agent_id, sub_tag: null, pct: c.ctx_pct, level: c.ctx_level, ctx_tokens: c.ctx_tokens, age_s: c.ctx_age_s };
      });
  }
  function renderTopBloat(rows) {
    var el = $("topBloat");
    if (!el) return;
    var total = (rows || []).filter(function (r) { return r && r.pct != null; }).length;
    var top = pickTopBloat(rows);
    if (!top.length) { el.className = "topbloat"; el.removeAttribute("data-has"); el.removeAttribute("title"); el.innerHTML = ""; syncTopBloat(); return; }
    var lead = top[0].level || "green";
    var parts = top.map(function (r) {
      var who = r.sub_tag || r.agent || "?";
      var lvl = r.level || "green";
      // op#13186: a 'pct'-sourced reading is CC's EXACT `% context used` line (the cliff
      // truth, ~ token count); a 'k' reading is derived from the /clear hint (approx).
      var srcNote = r.src === "pct" ? " · exact % (at cliff)" : (r.src === "k" ? " · ~ from hint" : "");
      var tip = fmtTok(r.ctx_tokens) + " / " + fmtTok(r.window || 1000000) +
        (r.age_s != null ? " · " + fmtAge(r.age_s) + " ago" : "") + srcNote;
      // op#31750: per-lane SRE disposition badge (worker rows carry sre_disposition;
      // coord glance rows don't — guard). So every bloated lane shows who's on it.
      var d = r.sre_disposition;
      var disp = (d && d.label) ? ' <span class="sre ' + esc(d.state) + '">' + esc(d.label) + '</span>' : '';
      return '<span class="ent" title="' + esc(tip) + '"><span class="who">' + esc(who) + '</span> ' +
        '<span class="pct ' + esc(lvl) + '">' + r.pct + '%</span>' + disp + '</span>';
    });
    // fc-v52: content is populated but visibility is gated by the BLOAT-cell tap
    // (syncTopBloat) — the resting view stays clean until the operator taps in.
    el.setAttribute("data-has", "1");
    el.setAttribute("title", "Top " + top.length + " context-bloated lanes (of " + total + ")");
    el.innerHTML = '<span class="dot ' + esc(lead) + '"></span><span class="lbl">Top bloat</span> ' +
      parts.join('<span class="sep">·</span>');
    syncTopBloat();
  }

  // ---- indices --------------------------------------------------------------
  var laneIndex = {};        // needs `who` -> peek session
  var lastLanes = [];
  var laneCtxIndex = {};     // per-lane /1M window reading
  var CTX_SEP = "::";
  var entries = {};          // session -> sheet entry (lanes + coordinators)

  function laneRank(l) {
    if (l.bucket === "working" || l.flagged) return 0;
    var hb = l.heartbeat_age_s;
    if (hb == null) return 1e12;
    return hb;
  }
  function dedupeLanes(lanes) {
    var slots = [], idxByKey = {};
    (lanes || []).forEach(function (l) {
      var key = l.tmux_session || l.lane;
      if (!key) { slots.push(l); return; }
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
      [l.agent_id, l.base_agent_id, l.lane, l.tmux_session].forEach(function (k) { if (k) laneIndex[k] = sess; });
    });
  }
  function buildLaneCtxIndex(rows) {
    laneCtxIndex = {};
    (rows || []).forEach(function (r) {
      if (!r || !r.agent) return;
      // #25436: an instance (sub_tag) reading keys by agent+sub_tag; a sub_tag=NULL
      // writer keys by agent alone (cc_identity fallback) so its gauge is NOT
      // dropped. A real sub_tag reading always wins — a NULL-keyed base entry only
      // fills a lane that has no instance-specific reading (never clobber one).
      if (r.sub_tag) laneCtxIndex[r.agent + CTX_SEP + r.sub_tag] = r;
      else if (laneCtxIndex[r.agent] == null) laneCtxIndex[r.agent] = r;
    });
  }
  function laneCtx(l) {
    // #25436: a downed/offline lane reads OFF, never a frozen last gauge — tmux/agent
    // liveness (the live-pane bucket), not staleness, is the downed signal.
    if (l.bucket === "offline") return null;
    var base = l.base_agent_id || l.agent_id;
    var sess = l.tmux_session || l.lane;
    var cx = null;
    if (base && sess) cx = laneCtxIndex[base + CTX_SEP + sess];
    if (!cx && base) cx = laneCtxIndex[base];
    return (cx && cx.pct != null) ? cx : null;
  }
  // never-blank lane-context (Musa flag via Nazim #32472; design #32484/#32489): map a
  // pane idle_verdict to an honest one-word label for a lane that has NO usable reading
  // (never had a /clear hint, or its last-known reading aged out) — so the card shows
  // "idle"/"low"/"n/a" instead of a bare "—". "low" = working but below the hint bar
  // (genuinely not near the cliff); "n/a" = we truly can't say.
  function idleLabel(v) {
    if (v === "IDLE_EMPTY") return "idle";
    if (v === "WORKING" || v === "STAGED") return "low";
    return "n/a";
  }
  // The PURE render decision for a lane's context indicator. Four modes, NEVER a blank:
  //   off   — offline lane (wins even over a stale reading)
  //   live  — a fresh pane reading -> show {pct}%
  //   stale — a LAST-KNOWN reading (hint hidden this cycle) -> show "~{k}k · {age}" (age
  //           VISIBLE, never mistakable for live — Nazim bake-in a)
  //   label — no usable reading -> the honest idle/low/n-a label from idle_verdict
  // Kept pure (no module state) so the unit test can exercise it directly.
  function ctxDisplayFrom(cx, ctxIdle, bucket) {
    if (bucket === "offline") return { mode: "off" };
    if (cx && cx.pct != null) {
      if (cx.stale) {
        var k = cx.ctx_tokens != null ? Math.round(cx.ctx_tokens / 1000) : null;
        return { mode: "stale", pct: cx.pct, level: cx.level, k: k, age_s: cx.age_s };
      }
      return { mode: "live", pct: cx.pct, level: cx.level };
    }
    return { mode: "label", text: idleLabel(ctxIdle) };
  }
  function laneCtxDisplay(l) { return ctxDisplayFrom(laneCtx(l), l.ctx_idle, l.bucket); }

  // ---- needs-you callouts ----------------------------------------------------
  function needCard(n, handling) {
    var crit = !handling && (n.priority === "P0" || n.kind === "blocked_deploy");
    var jump = laneIndex[n.who] || "";
    return '<div class="need' + (crit ? ' crit' : '') + (handling ? ' handling' : '') + (jump ? ' tappable' : '') + '"' +
        (jump ? ' data-jump="' + esc(jump) + '"' : '') + '>' +
      '<div class="m"><div class="who">' + esc(n.who) + '</div><div class="what">' + esc(n.what) + '</div></div>' +
      '<span class="tag">' + esc(n.tag) + '</span>' +
      '<span class="age">' + esc(fmtAge(n.age_s)) + '</span>' +
    '</div>';
  }
  function renderNeeds(items) {
    var ops = [], fleet = [];
    (items || []).forEach(function (n) { (n.audience === "operator" ? ops : fleet).push(n); });
    $("needs").innerHTML = ops.length ? ops.map(function (n) { return needCard(n, false); }).join("") : "";
    $("handling").innerHTML = fleet.length ? fleet.map(function (n) { return needCard(n, true); }).join("") : "";
    bindNeeds();
  }
  function bindNeeds() {
    document.querySelectorAll("#needs .need.tappable, #handling .need.tappable").forEach(function (row) {
      if (row._bound) return; row._bound = true;
      row.addEventListener("click", function () { jumpToLane(row.getAttribute("data-jump")); });
    });
  }
  // Tapping a needs row opens that lane's action sheet + its live peek.
  function jumpToLane(session) {
    if (!session) return;
    if (!routineExpanded && entries[session] && entries[session]._routine) {
      routineExpanded = true; renderLanes(lastLanes);
    }
    if (!entries[session]) { toast("that lane is not on the board right now", true); return; }
    openSheet(session, true);
  }

  // ---- LANE SPINE ------------------------------------------------------------
  // Renders the lane context indicator from the never-blank display object (4 modes). It
  // is NEVER blank: 'off' shows a faint —, 'label' shows an honest idle/low/n-a word,
  // 'stale' shows "~{k}k" dimmed (a token count — visibly NOT a live %) with the age in
  // its tooltip, 'live' shows the colored %-ring as before.
  function ringHtml(d, picked) {
    if (picked) return '<div class="ring"><div class="ck">✓</div></div>';
    d = d || { mode: "label", text: "n/a" };
    if (d.mode === "off") return '<div class="ring"><span class="rp" style="color:var(--faint)">—</span></div>';
    if (d.mode === "label") return '<div class="ring"><span class="rp lbl">' + esc(d.text) + '</span></div>';
    if (d.mode === "stale") {
      var kk = d.k != null ? "~" + d.k + "k" : "~?";
      return '<div class="ring stale ' + esc(d.level || "") + '" title="last known · ' +
        esc(fmtAge(d.age_s)) + ' ago (hint hidden this cycle)">' +
        '<span class="rp k">' + esc(kk) + '</span></div>';
    }
    var pct = Math.max(0, Math.min(100, d.pct));
    var off = (88 * (1 - pct / 100)).toFixed(1);
    var col = d.level === "red" ? "#fb7185" : (d.level === "amber" ? "#f6c453" : "#37d39a");
    return '<div class="ring ' + esc(d.level || "green") + '">' +
      '<svg width="34" height="34"><circle cx="17" cy="17" r="14" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="3"/>' +
      '<circle cx="17" cy="17" r="14" fill="none" stroke="' + col + '" stroke-width="3" stroke-dasharray="88" stroke-dashoffset="' + off + '" stroke-linecap="round"/></svg>' +
      '<span class="rp">' + d.pct + '</span></div>';
  }
  function tileHtml(l) {
    var sess = l.tmux_session || l.lane || "";
    var disp = laneCtxDisplay(l);
    // bloat-highlight the tile only on a LIVE amber/red reading (never a last-known one —
    // consistent with the header/glance being live-only).
    var bloat = disp.mode === "live" && (disp.level === "red" || disp.level === "amber");
    var cls = l.flagged && l.bucket !== "offline" ? "bloat" : l.bucket;
    if (bloat && l.bucket === "working") cls = "bloat";
    var live = l.live || {};
    var act = (live.running && live.activity) || l.activity || l.current_task || "";
    var picked = multiMode && selected[sess];
    var badge = (l.flagged && l.bucket === "offline") ? '<span class="badge">dark</span>' : "";
    var pool = poolOf(l), tok = tokChip(l.auth_fp, pool), mdl = mdlChip(l.model, l.model_src);
    // key roll-up filter (op#20684): a tile off the tapped pool dims, never hides —
    // the operator still sees the whole fleet, just with that key's lanes lit.
    var off = poolFilter && (poolFilter === "?" ? (pool || !l.auth_fp) : pool !== poolFilter);
    // op#20716: key + model chips stacked at the tile's right edge (no expand needed).
    var pills = (tok || mdl) ? '<div class="pills">' + tok + mdl + '</div>' : "";
    return '<div class="tile ' + esc(cls) + (picked ? " picked" : "") + (off ? " offpool" : "") + '" data-lane="' + esc(sess) + '" data-pool="' + esc(pool) + '">' +
      ringHtml(disp, picked) +
      '<div class="idw"><div class="id"><span class="stdot ' + esc(l.bucket) + '"></span>' + esc(l.agent_id) + badge + '</div>' +
        (act ? '<div class="act">' + esc(act) + '</div>' : '<div class="act">' + esc(l.bucket) + '</div>') +
      '</div>' + pills + '<span class="chev">›</span></div>';
  }
  var routineExpanded = false;
  var poolFilter = "";   // key roll-up: tapped pool nickname ("" = show all)
  var lastCoords = [];   // op#20715: the singletons feed the key roll-up + dim on filter
  function renderKeyRoll(lanes) {
    var el = $("keyRoll");
    if (!el) return;
    var r = poolRollup(lanes, lastCoords, poolFilter);
    if (poolFilter && !r.counts[poolFilter]) { poolFilter = ""; r = poolRollup(lanes, lastCoords, ""); }
    el.innerHTML = r.html;
    el.querySelectorAll(".krchip[data-pool]").forEach(function (b) {
      b.addEventListener("click", function () {
        var p = b.getAttribute("data-pool");
        poolFilter = (poolFilter === p) ? "" : p;
        renderCoordinators(lastCoords);   // off-pool singleton chips dim too (op#20715)
        renderLanes(lastLanes);
      });
    });
  }
  function collapsedHtml(routine) {
    var s = routineSummary(routine);
    var bits = s.keys.map(function (k) { return '<span class="cs ' + esc(POOL_CLS[k[0]] || "other") + '">' + esc(k[0]) + ' ' + k[1] + '</span>'; })
      .concat(s.models.map(function (m) { return '<span class="cs mdl">' + esc(m[0]) + ' ' + m[1] + '</span>'; }));
    return '<div class="collapsed" id="routineToggle"><span class="ctog">' + (routineExpanded ? "▾" : "▸") + '</span>' +
      '<div class="cbody"><div class="cl1"><b>' + routine.length + ' lane' + (routine.length > 1 ? "s" : "") + '</b> <span>idle &amp; fine</span>' +
        (bits.length ? '<span class="csep">·</span>' + bits.join('<span class="csep">·</span>') : "") + '</div>' +
      '<div class="cnames">' + esc(s.names.slice(0, 6).join(", ")) + (s.names.length > 6 ? "…" : "") + '</div></div></div>';
  }
  function renderLanes(lanes) {
    lastLanes = lanes;
    renderKeyRoll(lanes);
    var primary = lanes.filter(function (l) { return l.bucket === "working" || l.flagged; });
    var routine = lanes.filter(function (l) { return !(l.bucket === "working" || l.flagged); });
    var html = primary.map(tileHtml).join("");
    if (routine.length) {
      // op#20716: the collapsed group carries its key split + model split INLINE.
      html += collapsedHtml(routine) +
        '<div id="routine" style="display:' + (routineExpanded ? "block" : "none") + '">' + routine.map(tileHtml).join("") + '</div>';
    }
    $("lanes").innerHTML = html || '<div class="empty">No lanes.</div>';
    $("lanesCount").textContent = lanes.length + (lanes.length === 1 ? " lane" : " lanes");
    var t = $("routineToggle");
    if (t) t.addEventListener("click", function () {
      routineExpanded = !routineExpanded;
      $("routine").style.display = routineExpanded ? "block" : "none";
      var tg = t.querySelector(".ctog"); if (tg) tg.textContent = routineExpanded ? "▾" : "▸";
      bindTiles();
    });
    bindTiles();
  }
  function bindTiles() {
    document.querySelectorAll(".tile[data-lane]").forEach(function (card) {
      if (card._bound) return; card._bound = true;
      card.addEventListener("click", function () {
        var sess = card.getAttribute("data-lane");
        if (multiMode) { toggleSelect(sess); return; }
        openSheet(sess, false);
      });
    });
  }

  // ---- coordinators (compact chip row) --------------------------------------
  // op#20715/20716: a third line carries the singleton's KEY + MODEL chips (same
  // pills as a lane tile), and a chip off the tapped pool dims like a tile.
  function coordChip(c) {
    var sess = c.tmux_session || c.agent_id;
    var lvl = c.ctx_level || "";
    var cc = c.ctx_pct != null ? (c.ctx_pct + "% ctx") : (c.last_seen_s != null ? fmtAge(c.last_seen_s) : "quiet");
    var pool = poolOf(c), pills = tokChip(c.auth_fp, pool) + mdlChip(c.model, c.model_src);
    var off = poolFilter && (poolFilter === "?" ? (pool || !c.auth_fp) : pool !== poolFilter);
    return '<div class="cchip' + (off ? " offpool" : "") + '" data-coord="' + esc(sess) + '" data-pool="' + esc(pool) + '"><div class="cn">' + esc(c.short || c.agent_id) + '</div>' +
      '<div class="cc ' + esc(lvl) + '">' + esc(cc) + '</div>' +
      (pills ? '<div class="ck2">' + pills + '</div>' : "") + '</div>';
  }
  function renderCoordinators(items) {
    var el = $("coordinators");
    if (!el) return;
    lastCoords = items || [];
    if (!items || !items.length) { el.innerHTML = '<div class="empty">No coordinators.</div>'; return; }
    el.innerHTML = items.map(coordChip).join("");
    el.querySelectorAll(".cchip[data-coord]").forEach(function (chip) {
      if (chip._bound) return; chip._bound = true;
      chip.addEventListener("click", function () { if (!multiMode) openSheet(chip.getAttribute("data-coord"), false); });
    });
  }

  // ---- build the sheet entry index (lanes + coordinators) -------------------
  function buildEntries(lanes, coordinators) {
    entries = {};
    (lanes || []).forEach(function (l) {
      var sess = l.tmux_session || l.lane;
      if (!sess) return;
      var live = l.live || {};
      entries[sess] = {
        kind: "lane", session: sess, agentId: l.agent_id, id: l.agent_id,
        baseId: l.base_agent_id || l.agent_id,
        bucket: l.bucket, ctx: laneCtx(l), ctxIdle: l.ctx_idle,
        activity: (live.running && live.activity) || l.activity || l.current_task || "",
        auth_fp: l.auth_fp, pool: poolOf(l), host: l.host, model: l.model || null, peekable: true,
        _routine: !(l.bucket === "working" || l.flagged)
      };
    });
    (coordinators || []).forEach(function (c) {
      var sess = c.tmux_session || c.agent_id;
      if (!sess || entries[sess]) return;
      entries[sess] = {
        kind: "coord", session: sess, agentId: c.agent_id, id: c.short || c.agent_id,
        baseId: c.agent_id,
        bucket: (c.last_seen_s != null && c.last_seen_s < 1800) ? "working" : "idle",
        ctx: (c.ctx_pct != null ? { pct: c.ctx_pct, level: c.ctx_level, ctx_tokens: c.ctx_tokens, age_s: c.ctx_age_s } : null),
        activity: c.activity || "", auth_fp: c.auth_fp, pool: poolOf(c), host: c.host, model: c.model || null,
        peekable: !!c.peekable, roleLabel: c.role_label || ""
      };
    });
  }

  // ---- token-truth (lazy; feeds the sheet controls + bulk switch) -----------
  var ttBySession = {}, ttRegistry = { tokens: [], models: [] }, ttLoaded = false, ttFetchAt = 0, ttFetching = false;
  function ensureTT(force, cb) {
    var fresh = ttLoaded && (Date.now() - ttFetchAt < 30000);
    if (fresh && !force) { if (cb) cb(); return; }
    if (ttFetching) { if (cb) setTimeout(function () { ensureTT(false, cb); }, 400); return; }
    ttFetching = true;
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, 22000);   // remote-hub SSH scan is slow
    fetch("/api/token-truth", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (d && d.rows) {
          ttBySession = {};
          (d.rows || []).forEach(function (row) { if (row && row.session) ttBySession[row.session] = row; });
          ttRegistry = d.registry || { tokens: [], models: [] };
          ttLoaded = true; ttFetchAt = Date.now();
          populateBulkToken();
        }
      })
      .catch(function () {})
      .finally(function () { clearTimeout(timer); ttFetching = false; if (cb) cb(); });
  }

  // ---- ACTION SHEET ----------------------------------------------------------
  var currentSheet = null;   // session of the open sheet, or null
  var sheetBulk = false;     // whether the sheet is showing the bulk plan
  function openSheet(session, alsoPeek) {
    var e = entries[session];
    if (!e) return;
    currentSheet = session; sheetBulk = false;
    renderSheet(e);
    $("sheet").classList.add("up"); $("scrim").classList.add("on");
    if (e.kind === "lane" || e.peekable) ensureTT(false, function () { if (currentSheet === session) renderSheetControls(e); });
    else renderSheetControls(e);
    if (alsoPeek) togglePeek(session, true);
  }
  function closeSheet() {
    $("sheet").classList.remove("up"); $("scrim").classList.remove("on");
    if (openPeek) closePeek();
    currentSheet = null; sheetBulk = false;
    $("shBulk").innerHTML = ""; $("shPeekWrap").innerHTML = "";
    if ($("shConfirm")) $("shConfirm").innerHTML = "";
  }
  function renderSheet(e) {
    var dot = $("shDot"); dot.className = "st2 " + (e.bucket || "idle");
    $("shId").textContent = e.id;
    // never-blank: the sheet pill mirrors the tile's 4-state display — live %, an
    // age-stamped last-known "~{k}k · {age} ago", or an honest idle/low/no-ctx label.
    var d = ctxDisplayFrom(e.ctx, e.ctxIdle, e.bucket);
    var cp = $("shCtx"); cp.style.display = "";
    if (d.mode === "live") { cp.className = "ctxpill " + (d.level || ""); cp.textContent = d.pct + "% ctx"; }
    else if (d.mode === "stale") { cp.className = "ctxpill stale " + (d.level || ""); cp.textContent = "~" + d.k + "k · " + fmtAge(d.age_s) + " ago"; }
    else if (d.mode === "label") { cp.className = "ctxpill"; cp.textContent = (d.text === "n/a" ? "no ctx" : d.text + " · ctx"); }
    else { cp.className = "ctxpill"; cp.textContent = "no ctx"; }
    var subBits = [];
    if (e.host) subBits.push(esc(e.host));
    var keyName = e.pool || (e.auth_fp ? tokName(e.auth_fp) : "");
    if (keyName) subBits.push("🔑 " + esc(keyName));
    if (e.roleLabel) subBits.push(esc(e.roleLabel));
    if (e.activity) subBits.push(esc(e.activity));
    $("shSub").innerHTML = subBits.join(" · ") || "&nbsp;";
    renderSheetActions(e);
    if ($("shConfirm")) $("shConfirm").innerHTML = "";
    $("shControls").innerHTML = '<div class="qctlnote">loading token / model…</div>';
    $("shPeekWrap").innerHTML = '<div class="peek" data-peekbox="' + esc(e.session) + '"></div>';
    $("shBulk").innerHTML = "";
  }
  // The FULL action set, one tap each. fc-v63 (Musa op#20684/20687): Retask
  // (→ the ask composer, POST /api/assign), Boot (POST /api/lane-boot) and
  // Stand-down (POST /api/lane-down) are LIVE — the last two behind the in-sheet
  // typed-confirm strip (never window.prompt: iOS suppresses it in a standalone
  // PWA). Recycle is live only for the 3 resettable singletons (POST /api/reset);
  // a worker-lane recycle has no operator rail yet (the SRE's gated recycler is
  // lease-bound) so it still reports not-wired rather than firing something
  // unguarded. Mute / Pin have no backend — they stay honest stubs (NEW).
  function renderSheetActions(e) {
    var recWired = !!resetBodyFor(e.agentId);
    var lane = e.kind === "lane";
    var A = [
      { a: "peek",     cls: "prim", e: "👁", t: "Peek" },
      { a: "retask",   cls: "prim", e: "💬", t: "Retask" },
      { a: "recycle",  cls: (recWired ? "warn" : "warn new"), e: "♻️", t: "Recycle" },
      { a: "attach",   cls: "new",  e: "⤢", t: "Attach" },
      { a: "boot",     cls: (lane ? "" : "new"), e: "▶", t: "Boot" },
      { a: "standdown",cls: (lane ? "bad" : "bad new"), e: "⏹", t: "Stand&nbsp;down" },
      { a: "mute",     cls: "new",  e: "🔕", t: "Mute" },
      { a: "pin",      cls: "new",  e: "📌", t: "Pin ask" }
    ];
    $("shActions").innerHTML = '<div class="agrid">' + A.map(function (b) {
      return '<button class="' + b.cls + '" data-act="' + b.a + '"><span class="e">' + b.e + '</span>' + b.t + '</button>';
    }).join("") + '</div>';
  }
  // token/model pointer controls + Preview(dry-run)/Apply(armed) — folded in from
  // the /lanes manager. Reversible pointer write, allowlist-validated server-side.
  function renderSheetControls(e) {
    if (currentSheet !== e.session || sheetBulk) return;
    var el = $("shControls");
    var r = ttBySession[e.session];
    if (!r) { el.innerHTML = '<div class="qctlnote">token/model controls unavailable for this body (no ground-truth row).</div>'; return; }
    if (r.remote) { el.innerHTML = '<div class="qctlnote">remote (VPS) body — set its token/model on the hub host; cross-host apply lands in R3/R4.</div>'; return; }
    var s = esc(e.session);
    var ctrls = "";
    if (r.token_settable) {
      var tlabel = r.token_group ? ("Token · " + esc(r.token_group) + " group")
        : (r.token_pointer === ".lane_default_token" ? "Token · all lanes" : "Token");
      var topts = '<option value="">— default —</option>' + (ttRegistry.tokens || []).map(function (t) {
        var sel = (r.token_pointer_name === t.name) ? " selected" : "";
        var dis = t.available ? "" : " disabled";
        return '<option value="' + esc(t.name) + '"' + sel + dis + '>' + esc(t.name) + (t.fp ? " (" + esc(t.fp) + ")" : "") + '</option>';
      }).join("");
      ctrls += '<label><span>' + tlabel + '</span><select data-kind="token" data-session="' + s + '">' + topts + '</select></label>';
    } else {
      ctrls += '<div class="qctlnote">token: .env default (not pointer-settable)</div>';
    }
    if (r.model_settable) {
      var mopts = '<option value="">— default —</option>' + (ttRegistry.models || []).map(function (m) {
        var sel = (r.model_pointer === m) ? " selected" : "";
        return '<option value="' + esc(m) + '"' + sel + '>' + esc(shortModel(m)) + '</option>';
      }).join("");
      ctrls += '<label><span>Model</span><select data-kind="model" data-session="' + s + '">' + mopts + '</select></label>';
    } else {
      ctrls += '<div class="qctlnote">model: env-driven at boot (not pointer-settable)</div>';
    }
    var ab = "";
    if (r.token_settable) {
      ab += '<div class="b prev" data-apply="token" data-session="' + s + '">Preview token</div>';
      ab += '<div class="b arm" data-armapply="token" data-session="' + s + '">Apply token</div>';
    }
    if (r.model_settable) {
      ab += '<div class="b prev" data-apply="model" data-session="' + s + '">Preview model</div>';
      ab += '<div class="b arm" data-armapply="model" data-session="' + s + '">Apply model</div>';
    }
    var hasSel = r.token_settable || r.model_settable;
    el.innerHTML = (hasSel ? '<div class="qctl' + ((r.token_settable && r.model_settable) ? "" : " wide") + '">' + ctrls + '</div>' : ctrls) +
      (ab ? '<div class="applyrow">' + ab + '</div>' : "");
  }

  // ---- sheet action handlers -------------------------------------------------
  function currentEntry() { return currentSheet ? entries[currentSheet] : null; }
  function handleAction(act, btn) {
    var e = currentEntry();
    if (!e) return;
    if (act === "peek") { togglePeek(e.session); return; }
    if (act === "attach") {
      toast("Attach: run  tmux attach -t " + e.session + (e.host ? "  on " + e.host : ""));
      return;
    }
    if (act === "recycle") {
      var body = resetBodyFor(e.agentId);
      if (!body) { toast("Recycle for a worker lane has no operator rail yet (the SRE's gated recycler is lease-bound) — not wired.", true); return; }
      armedReset(btn, body);
      return;
    }
    if (act === "retask") { retaskLane(e); return; }
    if (act === "boot" || act === "standdown") {
      if (e.kind !== "lane") { toast("Boot / Stand-down are for worker lanes only — singletons are protected.", true); return; }
      openConfirm(e, act);
      return;
    }
    // Mute / Pin: no backend — honest stubs, they do NOT fire anything.
    var LABEL = { mute: "Mute", pin: "Pin" };
    toast((LABEL[act] || act) + ": no backend yet — not wired.", true);
  }
  // Retask = hand this lane a new ask through the SAME rail as "+ ask" (a real
  // bus row via POST /api/assign). Opens the composer pre-targeted at the lane's
  // bus id; the operator types the ask and sends — the send is the deliberate act.
  function retaskLane(e) {
    var target = e.baseId || e.agentId;
    var sel = $("assignAgent"), form = $("assignForm"), tog = $("assignToggle");
    if (!sel || !form) { toast("ask composer not on this page", true); return; }
    var has = false;
    Array.prototype.forEach.call(sel.options, function (o) { if (o.value === target) has = true; });
    if (!has) sel.insertAdjacentHTML("beforeend", '<option value="' + esc(target) + '">' + esc(target) + '</option>');
    sel.value = target;
    form.classList.add("open"); if (tog) tog.classList.add("on");
    closeSheet();
    var inp = $("assignAsk");
    if (inp) { inp.placeholder = "Retask " + target + ": what should it do now?"; inp.focus(); }
    try { form.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (er) {}
    toast("Retask → type the ask for " + target + " and send");
  }
  // In-sheet typed-confirm strip (the fat-finger guard for a lane action).
  // window.prompt is NOT used anywhere: iOS suppresses it in a standalone PWA, which
  // is exactly the phone the operator drives this from. The fire button only lights
  // once the typed name equals the target; Esc / ✕ disarms.
  var CONFIRM_COPY = {
    boot:      { v: "Boot",        note: "starts the lane's tmux session via lanes.sh (roster-gated, idempotent)", cls: "" },
    standdown: { v: "Stand down",  note: "ENDS the session — refused if busy, unread bus, stale handoff, or staged text", cls: "bad" },
    "apply-token": { v: "Apply token", note: "RELAUNCHES the body on its token default (reversible re-pool)", cls: "warn" },
    "apply-model": { v: "Apply model", note: "RELAUNCHES the body on its model default (reversible)", cls: "warn" }
  };
  function openConfirm(e, act) {
    var box = $("shConfirm"); if (!box) return;
    var c = CONFIRM_COPY[act] || { v: act, note: "", cls: "" };
    var s = esc(e.session);
    box.innerHTML =
      '<div class="cstrip ' + c.cls + '" data-cact="' + esc(act) + '" data-session="' + s + '">' +
        '<div class="ch"><span class="cv">' + esc(c.v) + '</span><span class="ct">' + s + '</span><span class="cx" data-cx="1">✕</span></div>' +
        '<div class="cn">' + esc(c.note) + '</div>' +
        '<div class="cr"><input class="ci" type="text" autocapitalize="off" autocorrect="off" autocomplete="off" spellcheck="false" placeholder="type ' + s + ' to arm" />' +
        '<button class="cf" disabled>' + esc(c.v) + '</button></div>' +
      '</div>';
    var inp = box.querySelector(".ci");
    if (inp) setTimeout(function () { try { inp.focus(); } catch (er) {} }, 60);
  }
  function confirmTyped(strip) {
    var inp = strip.querySelector(".ci"), fire = strip.querySelector(".cf");
    var ok = inp && inp.value.trim() === strip.getAttribute("data-session");
    strip.classList.toggle("armed", !!ok);
    if (fire) fire.disabled = !ok;
    return !!ok;
  }
  function fireConfirmed(strip) {
    if (!confirmTyped(strip)) { toast("name did not match — not fired", true); return; }
    var act = strip.getAttribute("data-cact"), session = strip.getAttribute("data-session");
    var typed = strip.querySelector(".ci").value.trim();
    var fire = strip.querySelector(".cf"); if (fire) { fire.disabled = true; fire.textContent = "…"; }
    if (act === "apply-token" || act === "apply-model") { applyArmed(session, act.slice(6), typed, strip); return; }
    laneAction(act === "boot" ? "boot" : "down", session, typed, strip);
  }
  function laneAction(action, session, typed, strip) {
    var route = "/api/lane-" + action;
    fetch(route, {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ session: session, confirm: typed })
    }).then(function (r) { return r.json().then(function (j) { return { s: r.status, ok: r.ok, j: j || {} }; }, function () { return { s: r.status, ok: r.ok, j: {} }; }); })
      .then(function (res) {
        var verb = action === "boot" ? "boot" : "stand-down";
        if (res.ok && res.j.ok) { toast((res.j.skipped ? "= " : "✓ ") + verb + " " + session + (res.j.tail ? " — " + String(res.j.tail).split("\n").pop().slice(0, 120) : "")); load(); }
        else if (res.s === 409) toast("⏸ " + verb + " refused: " + String(res.j.tail || res.j.error || "gate refused").split("\n").pop().slice(0, 160), true);
        else if (res.s === 429) toast("⏳ just ran — wait " + (res.j.retry_after_s || 30) + "s", true);
        else if (res.s === 503) toast("✗ " + (res.j.error || "not available from this console"), true);
        else toast("✗ " + (res.j.error || verb + " failed"), true);
        if (strip && strip.parentNode) strip.parentNode.innerHTML = "";
      })
      .catch(function () { toast("✗ network dropped — " + action + " may have run; refresh", true); if (strip && strip.parentNode) strip.parentNode.innerHTML = ""; });
  }
  // Two-tap armed reset (NOT window.confirm — iOS suppresses it in a standalone PWA).
  function armedReset(btn, body) {
    if (!btn._armed) {
      btn._armed = true; btn.classList.add("armed");
      var lbl = btn.querySelector(".e") ? btn : null;
      btn._orig = btn.innerHTML;
      btn.innerHTML = '<span class="e">♻️</span>tap again';
      btn._disarm = setTimeout(function () { btn._armed = false; btn.classList.remove("armed"); btn.innerHTML = btn._orig; }, 3000);
      return;
    }
    clearTimeout(btn._disarm); btn._armed = false; btn.classList.remove("armed");
    btn.disabled = true; btn.innerHTML = '<span class="e">♻️</span>resetting…';
    fetch("/api/reset", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders(), armedHeaders()),
      body: JSON.stringify({ body: body, confirm: body })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }, function () { return { ok: r.ok, s: r.status, j: {} }; }); })
      .then(function (res) {
        var msg;
        if (res.s === 401) { armedKeyMissing(); btn.disabled = false; btn.innerHTML = btn._orig || '<span class="e">♻️</span>Recycle'; return; }
        if (res.ok && res.j.ok) msg = "✓ reset sent";
        else if (res.s === 409) msg = "⏳ already resetting";
        else if (res.s === 429) msg = "⏳ just reset — wait " + (res.j.retry_after_s || 60) + "s";
        else msg = "✗ " + (res.j.error || "failed");
        toast(msg, !(res.ok && res.j.ok));
        btn.disabled = false; btn.innerHTML = btn._orig || '<span class="e">♻️</span>Recycle';
      })
      .catch(function () {
        toast("✗ network dropped — reset may have run; wait ~60s", true);
        btn.disabled = false; btn.innerHTML = btn._orig || '<span class="e">♻️</span>Recycle';
      });
  }

  // ---- per-lane token/model pointer + preview/armed apply --------------------
  var busy = false;
  function setPointer(session, kind, value) {
    if (busy) return; busy = true;
    var payload = value ? { kind: kind, session: session, value: value } : { kind: kind, session: session, clear: true };
    fetch("/api/set-pointer", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()), body: JSON.stringify(payload)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) toast((res.j && res.j.error) || "failed", true);
        else toast(kind + " default set for " + session + " — applies on next relaunch");
      })
      .catch(function (er) { toast("error: " + (er && er.message), true); })
      .finally(function () { busy = false; ensureTT(true, function () { var e = currentEntry(); if (e) renderSheetControls(e); }); });
  }
  function previewApply(session, kind) {
    if (busy) return; busy = true;
    fetch("/api/apply-dry-run", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()), body: JSON.stringify({ session: session, kind: kind })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
      .then(function (res) {
        if (!res.ok) toast((res.j && res.j.error) || "preview failed", true);
        else toast("DRY-RUN " + session + "/" + kind + ": " + String(res.j.preview || "(no output)").slice(0, 160));
      })
      .catch(function (er) { toast("error: " + (er && er.message), true); })
      .finally(function () { busy = false; });
  }
  // Apply (op#20692/op#20689): the typed body-name confirm is the operator's
  // in-console authorization for this REVERSIBLE re-pool; the server still gates
  // on CONSOLE_R4_ENABLED + confirm==session + the gazzabyte fail-closed.
  function applyArmed(session, kind, typed, strip) {
    if (busy) return;
    if (typed == null) { var e = currentEntry(); if (e && e.session === session) openConfirm(e, "apply-" + kind); return; }
    if (typed.trim() !== session) { toast("name did not match — not applied", true); return; }
    busy = true;
    fetch("/api/apply-armed", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders(), armedHeaders()), body: JSON.stringify({ session: session, kind: kind, confirm: typed.trim() })
    }).then(function (r) { return r.json().then(function (j) { return { status: r.status, ok: r.ok, j: j }; }, function () { return { status: r.status, ok: r.ok, j: {} }; }); })
      .then(function (res) {
        if (res.status === 401) armedKeyMissing();
        else if (res.status === 503) toast((res.j && res.j.error) || "Apply is disabled on this console (CONSOLE_R4_ENABLED off / no upstream)", true);
        else if (res.status === 403) toast((res.j && res.j.error) || "refused", true);
        else if (res.status === 202) toast(kind + " QUEUED for " + session + " — fires when idle");
        else if (res.status === 409 || res.status === 429) toast((res.j && res.j.error) || "busy — try again shortly", true);
        else if (!res.ok) toast((res.j && res.j.error) || "apply failed", true);
        else toast("✓ " + kind + " applied to " + session + (res.j && res.j.ok ? "" : " (check output)"));
      })
      .catch(function (er) { toast("error: " + (er && er.message), true); })
      .finally(function () { busy = false; if (strip && strip.parentNode) strip.parentNode.innerHTML = ""; });
  }

  // ---- multi-select + bulk account switch -----------------------------------
  // Multi-select generalises the old /lanes bulk-switch onto the unified page:
  // pick lanes, pick a target account, PREVIEW (dry-run) → CONFIRM. It reuses the
  // safety-tested /api/switch-all endpoint (server excludes singletons/held/self)
  // by inverting the selection into the endpoint's `exclude` list, so ONLY the
  // picked lanes switch. Never a one-tap bulk fire — dry-run + explicit confirm,
  // and the surface is hidden until the operator opts into multi-select.
  var multiMode = false, selected = {};
  var BULK_HELD = ["irsyad-import"];   // held lanes: pre-excluded from any bulk switch
  function toggleMulti() {
    multiMode = !multiMode; selected = {};
    $("multiToggle").classList.toggle("on", multiMode);
    ensureTT(false, populateBulkToken);
    renderLanes(lastLanes); updateDock();
  }
  function toggleSelect(sess) {
    if (selected[sess]) delete selected[sess]; else selected[sess] = true;
    renderLanes(lastLanes); updateDock();
  }
  function selectedList() { return Object.keys(selected); }
  function populateBulkToken() {
    var sel = $("bulkToken");
    if (!sel) return;
    var toks = (ttRegistry.tokens || []).filter(function (t) { return t && t.name && t.available !== false && !/gazza/i.test(t.name); });
    sel.innerHTML = toks.length ? toks.map(function (t) { return '<option value="' + esc(t.name) + '">' + esc(t.name) + '</option>'; }).join("")
      : '<option value="">(no accounts)</option>';
  }
  function updateDock() {
    var hint = $("dockHint"), sel = $("bulkToken"), prev = $("bulkPreview"), clr = $("bulkClear");
    if (!multiMode) {
      hint.textContent = "Tap a lane to act";
      sel.style.display = "none"; prev.classList.add("hidden"); clr.classList.add("hidden");
      return;
    }
    var n = selectedList().length;
    hint.innerHTML = n ? ('<b>' + n + '</b> selected — switch to') : "Select lanes to bulk-switch";
    // explicit value, NOT "" — .dsel is display:none in CSS, so clearing the inline
    // style would fall back to none and hide the account picker (bulk unusable).
    sel.style.display = n ? "inline-block" : "none";
    prev.classList.toggle("hidden", !n); clr.classList.toggle("hidden", !n);
  }
  function bulkPreviewRun() {
    var toks = selectedList();
    if (!toks.length) return;
    var target = $("bulkToken").value;
    if (!target) { toast("no target account", true); return; }
    ensureTT(false, function () {
      // exclude = every local switchable session NOT picked (+ held). switch-all
      // then switches exactly the picked set (minus the server's own exclusions).
      var all = Object.keys(ttBySession).filter(function (s) { return ttBySession[s] && !ttBySession[s].remote; });
      var excl = all.filter(function (s) { return !selected[s]; }).concat(BULK_HELD);
      openBulkSheet(target, "Previewing…", null);
      fetch("/api/switch-all", {
        method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
        body: JSON.stringify({ token_name: target, dry_run: true, exclude: excl })
      }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }, function () { return { ok: r.ok, s: r.status, j: {} }; }); })
        .then(function (res) {
          if (!(res.ok && res.j && res.j.dry_run)) { openBulkSheet(target, null, { err: bulkErr(res) }); return; }
          bulkPlan = { token: target, exclude: excl };
          openBulkSheet(target, null, res.j);
        })
        .catch(function () { openBulkSheet(target, null, { err: "✗ network dropped during preview — tap again" }); });
    });
  }
  function bulkErr(res) {
    if (res.s === 400) return "✗ " + ((res.j && res.j.error) || "unknown token / bad request");
    if (res.s === 401) return "✗ not authorized";
    if (res.s === 429) return "⏳ rate-limited — wait a moment";
    return "✗ " + ((res.j && res.j.error) || ("failed (http " + res.s + ")"));
  }
  var bulkPlan = null;
  function openBulkSheet(target, note, plan) {
    currentSheet = null; sheetBulk = true;
    $("shDot").className = "st2"; $("shId").textContent = "Bulk switch";
    var cp = $("shCtx"); cp.className = "ctxpill"; cp.textContent = "→ " + target; cp.style.display = "";
    $("shSub").textContent = selectedList().length + " lane(s) selected · dry-run first, then confirm";
    $("shActions").innerHTML = ""; $("shControls").innerHTML = ""; $("shPeekWrap").innerHTML = "";
    var body;
    if (note) body = '<div class="bulkplan"><span class="bp-act skip">' + esc(note) + '</span></div>';
    else if (plan && plan.err) body = '<div class="bulkplan"><span class="bp-err">' + esc(plan.err) + '</span></div>';
    else body = renderBulkPlan(target, plan);
    $("shBulk").innerHTML = body;
    var cb = $("bulkConfirmBtn");
    if (cb) cb.addEventListener("click", function () { bulkFire(); });
    $("sheet").classList.add("up"); $("scrim").classList.add("on");
  }
  function renderBulkPlan(target, j) {
    var targets = (j && j.targets) || [], sum = (j && j.summary) || {};
    var would = targets.filter(function (t) { return t.action === "switch"; });
    var already = targets.filter(function (t) { return t.action === "skipped:already"; }).length;
    var busyN = targets.filter(function (t) { return t.action === "skipped:busy"; }).length;
    var head = '<div class="bp-sum">Would switch <b>' + would.length + '</b> lane' + (would.length === 1 ? "" : "s") +
      ' to ' + esc(target) + ' — skipped ' + (sum.skipped != null ? sum.skipped : (targets.length - would.length)) +
      ' (already ' + already + ' / busy ' + busyN + ')</div>';
    var lanes = would.length
      ? would.map(function (t) { return '<div class="bp-lane"><span>' + esc(t.lane) + '</span><span class="bp-act ok">switch</span></div>'; }).join("")
      : '<div class="bp-lane skip">Nothing to switch (server excluded singletons/held/already-on-target).</div>';
    var confirm = would.length ? '<button class="bulkconfirm" id="bulkConfirmBtn">Confirm switch ' + would.length + ' lane' + (would.length === 1 ? "" : "s") + '</button>' : "";
    return '<div class="bulkplan">' + head + lanes + '</div>' + confirm;
  }
  function bulkFire() {
    if (!bulkPlan) return;
    var cb = $("bulkConfirmBtn");
    if (cb) { cb.disabled = true; cb.textContent = "⇄ switching…"; }
    fetch("/api/switch-all", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ token_name: bulkPlan.token, dry_run: false, exclude: bulkPlan.exclude })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }, function () { return { ok: r.ok, s: r.status, j: {} }; }); })
      .then(function (res) {
        bulkPlan = null;
        var j = res.j, targets = (j && j.targets) || [], sum = (j && j.summary) || {};
        if (!targets.length) { $("shBulk").innerHTML = '<div class="bulkplan"><span class="bp-err">' + esc(bulkErr(res)) + '</span></div>'; return; }
        var anyFail = (sum.failed || 0) > 0 || j.ok === false;
        var head = '<div class="bp-sum">' + (anyFail ? "⚠ " : "✓ ") + 'switched ' + (sum.switched || 0) + ' · skipped ' + (sum.skipped || 0) + ' · failed ' + (sum.failed || 0) + '</div>';
        var items = targets.map(function (t) {
          var a = t.action || "", cls = a === "switch" ? "ok" : (a === "failed" ? "fail" : "skip");
          return '<div class="bp-lane"><span>' + esc(t.lane) + '</span><span class="bp-act ' + cls + '">' + esc(a) + '</span></div>';
        }).join("");
        $("shBulk").innerHTML = '<div class="bulkplan">' + head + items + '</div>';
        selected = {};
        setTimeout(function () { load(); }, 600);
      })
      .catch(function () {
        bulkPlan = null;
        $("shBulk").innerHTML = '<div class="bulkplan"><span class="bp-err">✗ network dropped — may have run; check the lanes</span></div>';
      });
  }

  // ---- DRAIN BOARD (fc-v52) — per-body live inbox depth ---------------------
  // Replaces the static "Your asks" backlog. Each body's row is its unhandled bus
  // inbox (agent_messages read_at IS NULL) + any console-assigned items; the whole
  // board SHRINKS on the normal /api/fleet poll as bodies drain their mail. The
  // operator can also ASSIGN a new item to a specific body — that becomes a real
  // bus row to it (POST /api/assign), so the body actually drains it.
  function drainItem(it) {
    var pri = (it.priority && it.priority !== "P2") ? '<span class="amark" style="color:var(--warn);background:rgba(246,196,83,.14)">' + esc(it.priority) + '</span>' : "";
    var amk = it.assigned ? '<span class="amark">assigned</span>' : "";
    var rr = it.needs_response ? '<span class="amark" style="color:var(--bad);background:rgba(255,107,107,.14)">reply</span>' : "";
    return '<div class="di">' +
      '<div class="dtxt">' + esc(it.subject) +
        (it.from ? ' <span class="dfrom">· ' + esc(it.from) + '</span>' : "") + '</div>' +
      pri + amk + rr +
      '<span class="dage">' + esc(fmtAge(it.age_s)) + '</span>' +
    '</div>';
  }
  function bodyRow(g) {
    var items = (g.items || []).map(drainItem).join("");
    var more = g.more ? '<div class="dmore">+ ' + g.more + ' more pending…</div>' : "";
    var rr = g.needs_response ? '<span class="drr">' + g.needs_response + ' reply</span>' : "";
    return '<div class="dbody">' +
      '<div class="dh"><span class="dn">' + esc(g.agent) + '</span>' + rr +
        '<span class="dcount' + (g.needs_response ? " pending" : "") + '">' + g.unread + ' pending</span></div>' +
      (items ? '<div class="ditems">' + items + '</div>' : "") + more +
    '</div>';
  }
  function renderDrainBoard(rows) {
    var el = $("drainBoard");
    if (!el) return;
    var cnt = $("drainCount");
    if (!rows || !rows.length) {
      el.innerHTML = '<div class="empty">All inboxes drained. ✨</div>';
      if (cnt) cnt.textContent = "";
      return;
    }
    var totalPending = rows.reduce(function (a, g) { return a + (g.unread || 0); }, 0);
    var bodies = rows.length;
    if (cnt) cnt.textContent = totalPending + " pending · " + bodies + (bodies === 1 ? " body" : " bodies");
    el.innerHTML = rows.map(bodyRow).join("");
  }

  // ---- YOUR ASKS (fc-v55) — the operator's LIVE ask board -------------------
  // One card per open operator_asks row. STATUS IS DERIVED LIVE on the bus each
  // poll (never stored) so it cannot go stale (op#13250). needs_you is pinned red
  // to the top by the server; review (delegate_done) is NOT auto-done — the
  // operator swipe-to-confirms it. The card only carries {id, ask, delegated_to,
  // status, updated_age_s, asked_age_s}; we render the copy per status here.
  var ASK_TAG = {
    needs_you:     { cls: "needs", label: "needs you" },
    delegate_done: { cls: "rev",   label: "review" },
    in_progress:   { cls: "prog",  label: "in progress" },
    pending:       { cls: "pend",  label: "pending" },
    on_nazim:      { cls: "naz",   label: "on me" }
  };
  function askMeta(a) {
    var to = a.delegated_to
      ? '<span class="ato">→ <b>' + esc(a.delegated_to) + '</b></span>'
      : '<span class="ato">not yet delegated</span>';
    var upd = a.updated_age_s, warn = (upd != null && upd >= 3600);
    var ageCls = warn ? "aage warnage" : "aage";
    var moved;
    if (a.status === "needs_you") moved = "bounced back " + fmtAge(upd) + " ago";
    else if (a.status === "delegate_done") moved = "replied " + fmtAge(upd) + " ago";
    else if (a.status === "pending") moved = "sent " + fmtAge(upd) + " ago · unopened";
    else if (a.status === "on_nazim") moved = "asked " + fmtAge(a.asked_age_s) + " ago";
    else moved = "updated " + fmtAge(upd) + " ago";
    var parts = [to, '<span class="' + ageCls + '">' + esc(moved) + '</span>'];
    // Show when it was originally asked too (except on_nazim, where that IS moved).
    if (a.status !== "on_nazim" && a.asked_age_s != null)
      parts.push('<span>asked ' + esc(fmtAge(a.asked_age_s)) + ' ago</span>');
    return '<div class="ameta">' + parts.join('<span class="adot">·</span>') + '</div>';
  }
  function askCard(a) {
    var t = ASK_TAG[a.status] || ASK_TAG.pending;
    var needs = a.status === "needs_you";
    var review = a.status === "delegate_done";
    var cls = "ask" + (needs ? " needs" : "") + (review ? " confirmable" : "");
    var rev = review
      ? '<div class="arev" data-confirm="' + a.id + '">✓ delegate reported done — swipe or tap to confirm it landed</div>'
      : "";
    return '<div class="' + cls + '" data-ask="' + a.id + '">' +
      '<span class="atag ' + t.cls + '">' + t.label + '</span>' +
      '<div class="abody"><div class="atxt">' + esc(a.ask) + '</div>' +
        rev + askMeta(a) +
      '</div></div>';
  }
  function renderAsks(rows) {
    var el = $("asks");
    if (!el) return;
    var cnt = $("asksCount"), fresh = $("asksFresh");
    if (!rows || !rows.length) {
      el.innerHTML = '<div class="empty">No open asks. ✨</div>';
      if (cnt) cnt.textContent = "";
      if (fresh) fresh.style.display = "none";
      return;
    }
    var needs = rows.filter(function (a) { return a.status === "needs_you"; }).length;
    var review = rows.filter(function (a) { return a.status === "delegate_done"; }).length;
    if (cnt) {
      var bits = [rows.length + " open"];
      if (needs) bits.push(needs + " needs you");
      if (review) bits.push(review + " to review");
      cnt.textContent = bits.join(" · ");
    }
    el.innerHTML = rows.map(askCard).join("");
    if (fresh) fresh.style.display = "flex";
  }

  // swipe/tap-to-confirm — the operator's authoritative "done" on a review card.
  // A delegate stamping responded_at only shows REVIEW; this close (confirm) is the
  // only thing that clears the ask (POST /api/ask-close). Best-effort optimistic
  // fade; the next poll is the source of truth.
  var askBusy = {};
  function confirmAsk(id, action) {
    if (!id || askBusy[id]) return;
    action = action || "confirm";
    askBusy[id] = true;
    var card = document.querySelector('.ask[data-ask="' + id + '"]');
    if (card) card.classList.add("confirming");
    fetch("/api/ask-close", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ id: Number(id), action: action })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; }, function () { return { ok: r.ok, j: {} }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) { toast(action === "drop" ? "✓ dismissed" : "✓ confirmed done"); load(); }
        else { if (card) card.classList.remove("confirming"); toast("✗ " + (res.j.error || "close failed"), true); }
      })
      .catch(function () { if (card) card.classList.remove("confirming"); toast("✗ network dropped — confirm may not have run", true); })
      .finally(function () { askBusy[id] = false; });
  }

  // ---- assign affordance -----------------------------------------------------
  // Build the target list from the live lanes + coordinators: a lane assigns to
  // its BASE agent id (the family bus address), a coordinator to its own id.
  var assignTargets = [];
  function buildAssignTargets(lanes, coords) {
    var seen = {}, out = [];
    (lanes || []).forEach(function (l) {
      var id = l.base_agent_id || l.agent_id;
      if (!id || seen[id]) return; seen[id] = true;
      out.push({ id: id, label: id });
    });
    (coords || []).forEach(function (c) {
      var id = c.agent_id;
      if (!id || seen[id]) return; seen[id] = true;
      out.push({ id: id, label: (c.short ? c.short + " (" + id + ")" : id) });
    });
    out.sort(function (a, b) { return a.label < b.label ? -1 : (a.label > b.label ? 1 : 0); });
    assignTargets = out;
    var sel = $("assignAgent");
    if (!sel) return;
    var cur = sel.value;
    sel.innerHTML = '<option value="">Assign to…</option>' +
      out.map(function (t) { return '<option value="' + esc(t.id) + '">' + esc(t.label) + '</option>'; }).join("");
    if (cur) sel.value = cur;
  }
  var assignBusy = false;
  function sendAssign() {
    if (assignBusy) return;
    var agent = ($("assignAgent") || {}).value || "";
    var ask = (($("assignAsk") || {}).value || "").trim();
    var pri = ($("assignPri") || {}).value || "P2";
    if (!agent) { toast("pick a lane/coordinator", true); return; }
    if (!ask) { toast("type what they should do", true); return; }
    assignBusy = true;
    var btn = $("assignSend");
    if (btn) { btn.disabled = true; btn.textContent = "Assigning…"; }
    fetch("/api/assign", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ agent: agent, ask: ask, priority: pri })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, s: r.status, j: j || {} }; }, function () { return { ok: r.ok, s: r.status, j: {} }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) {
          toast("✓ assigned to " + agent);
          if ($("assignAsk")) $("assignAsk").value = "";
          load();   // refresh the board so the new item appears in that body's queue
        } else {
          toast("✗ " + (res.j.error || "assign failed"), true);
        }
      })
      .catch(function () { toast("✗ network dropped — assign may not have run", true); })
      .finally(function () { assignBusy = false; if (btn) { btn.disabled = false; btn.textContent = "Assign →"; } });
  }

  // ---- readable peek — verbatim machinery from fc-v49, box now in the sheet --
  var openPeek = null, peekTimer = null, peekRaw = {}, peekText = {};
  function currentPeekBox() {
    if (!openPeek) return null;
    var found = null;
    document.querySelectorAll(".peek[data-peekbox]").forEach(function (b) { if (b.getAttribute("data-peekbox") === openPeek) found = b; });
    return found;
  }
  function stopPeekPolling() { if (peekTimer) { clearInterval(peekTimer); peekTimer = null; } }
  function startPeekPolling() {
    stopPeekPolling();
    peekTimer = setInterval(function () { var box = currentPeekBox(); if (box) fetchPeek(openPeek, box); }, 3000);
  }
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
      var body = lines.map(function (ln, i) { return '<div class="ln' + (i === last ? " now" : "") + '">' + esc(ln) + '</div>'; }).join("");
      box.innerHTML = head + '<div class="body">' + body + '</div>';
      var newBody = box.querySelector(".body");
      if (newBody) newBody.scrollTop = prevScroll;
    }
    var rawBtn = box.querySelector("[data-raw]");
    if (rawBtn) rawBtn.addEventListener("click", function (e) { e.stopPropagation(); peekRaw[sess] = !raw; renderPeek(box, peekText[sess] != null ? peekText[sess] : text, !raw); });
  }
  function fetchPeek(session, box) {
    fetch("/api/lanes/" + encodeURIComponent(session) + "/pane", { headers: authHeaders() })
      .then(function (r) { if (r.status === 401) { setLive(false); closePeek(); return null; } if (r.status === 404) return { dead: true }; return r.json(); })
      .then(function (data) {
        if (!data || session !== openPeek) return;
        box = currentPeekBox() || box;
        if (!box) return;
        if (data.dead) { box.innerHTML = '<div class="peek-empty">session not live</div>'; return; }
        peekText[session] = data.text || "";
        renderPeek(box, peekText[session], !!peekRaw[session]);
      }).catch(function () {});
  }
  function togglePeek(session, forceOpen) {
    if (openPeek === session && !forceOpen) { closePeek(); return; }
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
    openPeek = null; stopPeekPolling();
  }

  // ---- apply payload ---------------------------------------------------------
  function applyData(d) {
    var scrollY = window.scrollY || document.documentElement.scrollTop || 0;
    var lanes = dedupeLanes(d.lanes || []);
    buildLaneIndex(lanes);
    buildLaneCtxIndex(d.context_bloat || []);
    var glance = d.bloat_glance || (d.context_bloat || []).concat(coordCtxRows(d.coordinators));
    var bloatCount = (glance || []).filter(function (r) { return r && (r.level === "amber" || r.level === "red"); }).length;
    renderPulse(d.pulse || {});
    renderStat(d.pulse || {}, bloatCount);
    renderTopBloat(glance);
    renderPoolUsage(d.pool_usage || []);
    renderNeeds(d.needs_you || []);
    renderCoordinators(d.coordinators || []);
    buildEntries(lanes, d.coordinators || []);
    renderLanes(lanes);
    renderAsks(d.your_asks || []);
    renderDrainBoard(d.drain_board || []);
    buildAssignTargets(lanes, d.coordinators || []);
    updateDock();
    // Keep an open peek alive across the grid rebuild (the sheet DOM itself is not
    // rebuilt on a tick, so its peek box persists — just re-point the poller).
    if (openPeek && currentPeekBox()) startPeekPolling();
    window.scrollTo(0, scrollY);
  }

  var FETCH_TIMEOUT_MS = 12000;
  function fetchFleet() {
    var ctrl = new AbortController();
    var timer = setTimeout(function () { ctrl.abort(); }, FETCH_TIMEOUT_MS);
    return fetch("/api/fleet", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) { clearTimeout(timer); return r; }, function (e) { clearTimeout(timer); throw e; });
  }
  function load() {
    return fetchFleet()
      .then(function (r) {
        if (r.status === 401) {
          setLive(false, "● unauthorized");
          $("needs").innerHTML = UNAUTH; $("lanes").innerHTML = "";
          var err = new Error("unauth"); err.unauth = true; throw err;
        }
        if (!r.ok) throw new Error("http " + r.status);
        return r.json();
      })
      .then(function (d) { setLive(true); saveLastGood(d); applyData(d); });
  }
  var REFRESH_MS = 8000, RETRY_MAX_MS = 15000;
  var refreshTimer = null, backoff = 0;
  function schedule(ms) { if (refreshTimer) clearTimeout(refreshTimer); refreshTimer = setTimeout(tick, ms); }
  function tick() {
    load().then(function () { backoff = 0; schedule(REFRESH_MS); })
      .catch(function (e) {
        if (e && e.unauth) { backoff = RETRY_MAX_MS; }
        else { setLive(false, "● reconnecting…"); backoff = Math.min(backoff ? backoff * 2 : 2000, RETRY_MAX_MS); }
        schedule(backoff);
      });
  }

  // ---- GOVERNANCE (fc-v65, op#20702 Stage E) ---------------------------------
  // Per-project registry rows (project_governance, mig 063) with the 3 toggles:
  // cai on/off · operators (who authorizes build/scope; internal/external) ·
  // money-clearance (DEFAULT OFF — the loudest toggle). Reads /api/governance
  // (server-side subprocess read; the DSN never reaches the browser). EVERY write
  // is an ARMED action: X-Armed-Bearer (same key as Recycle/Apply) + typed project
  // confirm + a mandatory reason, POSTed to /api/governance-set; the server adds
  // the CONSOLE_R4_ENABLED gate and the DB trigger appends the audit row. Enabling
  // money clearance additionally requires typing the acknowledgement phrase.
  var govData = null, govBusy = false;
  var GOV_MONEY_ACK = "ENABLE MONEY CLEARANCE";
  var GOV_LABEL = { cai_enabled: "cai", money_clearance_enabled: "money-clearance", operators: "operators", channels: "channels" };
  function govKeyMissing() {
    toast("armed key required — set it below the governance rows", true);
    var inp = $("govArmedKey");
    if (inp) { inp.classList.add("miss"); try { inp.focus(); } catch (e) {} }
  }
  function ageOf(ts) { var t = ts ? Date.parse(ts) : NaN; return isNaN(t) ? null : Math.max(0, (Date.now() - t) / 1000); }
  // Pure: one project card. Toggles are buttons carrying the field + the value they
  // would flip TO; operators/channels get an "edit" affordance. Never renders a raw
  // fp/DSN — only what the registry holds (names, user ids, group ids).
  function govCardHtml(p) {
    var proj = esc(p.project);
    var cai = !!p.cai_enabled, money = !!p.money_clearance_enabled;
    var ops = Array.isArray(p.operators) ? p.operators : [];
    var chs = Array.isArray(p.channels) ? p.channels : [];
    var opsHtml = ops.length ? ops.map(function (o) {
      return '<span class="gop">' + esc(o.name) + ' <i>' + esc(o.chat_id) + '</i></span>' +
        '<span class="gtag' + (o.internal ? "" : " ext") + '">' + (o.internal ? "internal" : "external") + '</span>';
    }).join(" · ") : '<span class="gnone">none — nobody can authorize this project\'s scope (fail-closed)</span>';
    var chHtml = chs.length ? chs.map(function (c) { return '<span class="gop"><i>' + esc(c) + '</i></span>'; }).join(" · ")
      : '<span class="gnone">DM-only (no group trusted)</span>';
    var age = ageOf(p.updated_at);
    return '<div class="gcard' + (money ? " money" : "") + '" data-project="' + proj + '">' +
      '<div class="gh"><b>' + proj + '</b><span class="gmeta" title="' + esc(p.reason || "") + '">' + esc(p.updated_by || "") + (age != null ? " · " + fmtAge(age) : "") + '</span></div>' +
      '<div class="gtog">' +
        '<button class="gt cai ' + (cai ? "on" : "off") + '" data-gact="toggle" data-field="cai_enabled" data-to="' + (cai ? "false" : "true") + '">cai ' + (cai ? "ON" : "OFF") + '</button>' +
        '<button class="gt ' + (money ? "moneyon" : "off") + '" data-gact="toggle" data-field="money_clearance_enabled" data-to="' + (money ? "false" : "true") + '">money-clearance ' + (money ? "ON ⚠" : "OFF") + '</button>' +
      '</div>' +
      '<div class="grow"><span class="gl">operators</span>' + opsHtml + '<button class="gedit" data-gact="edit" data-field="operators">edit</button></div>' +
      '<div class="grow"><span class="gl">channels</span>' + chHtml + '<button class="gedit" data-gact="edit" data-field="channels">edit</button></div>' +
      '<div class="gres' + (p.residency_ack_on_file ? " on" : "") + '">residency ack: ' + (p.residency_ack_on_file ? "on file" : "none on file (never inferred)") + '</div>' +
    '</div>';
  }
  function govUnavailable(msg) {
    var g = $("gov"); if (g) g.innerHTML = '<div class="gunavail">' + esc(msg) + '</div>';
    var c = $("govCount"); if (c) c.textContent = "";
  }
  function renderGov(d) {
    var g = $("gov"); if (!g) return;
    var rows = d.projects || [];
    g.innerHTML = rows.length ? rows.map(govCardHtml).join("") : '<div class="empty">no governance rows</div>';
    var c = $("govCount"); if (c) c.textContent = rows.length + " project" + (rows.length === 1 ? "" : "s") + " · " + rows.filter(function (p) { return p.money_clearance_enabled; }).length + " money-on";
    renderGovAudit(d.audit || []);
  }
  function renderGovAudit(rows) {
    var box = $("govAudit"), cnt = $("govLogCount"); if (!box) return;
    if (cnt) cnt.textContent = rows.length ? String(rows.length) : "";
    if (!rows.length) { box.innerHTML = '<div class="empty">—</div>'; return; }
    box.innerHTML = '<div class="galog">' + rows.map(function (a) {
      var age = ageOf(a.changed_at);
      var ch = (a.changed || []).map(function (f) { return '<span class="' + (f === "money_clearance_enabled" ? "gamoney" : "gac") + '">' + esc(GOV_LABEL[f] || f) + '</span>'; }).join(", ");
      return '<span class="gaw">#' + esc(a.id) + (age != null ? " · " + fmtAge(age) : "") + '</span>' +
        '<span class="gam"><b>' + esc(a.project) + '</b> ' + (ch || "seed") + ' <span class="gar">— ' + esc(a.changed_by || "") + (a.reason ? ": " + esc(a.reason) : "") + '</span></span>';
    }).join("") + '</div>';
  }
  function loadGov(fresh) {
    if (!$("gov")) return Promise.resolve();
    return fetch("/api/governance" + (fresh ? "?fresh=1" : ""), { headers: authHeaders() })
      .then(function (r) { return r.json().then(function (j) { return { s: r.status, j: j }; }, function () { return { s: r.status, j: {} }; }); })
      .then(function (res) {
        if (res.s === 401) { govUnavailable("unauthorized"); return; }
        if (res.s !== 200 || !res.j || !Array.isArray(res.j.projects)) { govUnavailable((res.j && res.j.error) || "governance registry unavailable on this console"); return; }
        govData = res.j; renderGov(res.j);
      })
      .catch(function () { govUnavailable("governance registry unreachable"); });
  }
  // Pure: the confirm-strip copy for a toggle flip. Money ON is the loud one.
  function govStripCopy(project, field, to) {
    if (field === "money_clearance_enabled") {
      return to
        ? { v: "Enable money clearance", cls: "bad", ack: true,
            note: "⚠ LETS " + project + "'s registered operators clear ITS OWN money-path via their bridge-verified channel (Stage D). Default is OFF for every project. Only with the project's written direction on record. Audited, append-only." }
        : { v: "Disable money clearance", cls: "warn", ack: false, note: "Back to the default: " + project + "'s money-path returns to the Musa-only gate. Audited." };
    }
    if (field === "cai_enabled") {
      return to
        ? { v: "cai ON", cls: "", ack: false, note: "cai precedent BINDS " + project + " again; its lanes may raise review requests to cai." }
        : { v: "cai OFF", cls: "warn", ack: false, note: "cai precedent stops binding " + project + " (op#20704); review requests from its lanes to cai are refused at the DB (mig 064). Musa / the project operator decides." };
    }
    if (field === "operators") return { v: "Save operators", cls: "warn", ack: false, note: "Who authorizes " + project + "'s PRODUCT/SCOPING/build decisions (Stage C, non-money). chat_id = the person's Telegram USER id (positive) — a group id can never authorize." };
    return { v: "Save channels", cls: "warn", ack: false, note: "Group chat ids " + project + "'s operators may authorize FROM (negative ids). A DM never needs listing; empty = DM-only." };
  }
  function govStripHtml(project, field, to, cur) {
    var c = govStripCopy(project, field, to), s = esc(project);
    var body = "";
    if (field === "operators") {
      var ops = Array.isArray(cur) ? cur : [];
      body = '<div class="gfield" id="govOps">' + (ops.length ? ops.map(govOpRowHtml).join("") : govOpRowHtml({})) +
        '<button class="opadd" type="button">+ operator</button></div>';
    } else if (field === "channels") {
      body = '<div class="gfield"><span class="fl">group chat ids, comma-separated</span>' +
        '<input class="gch" type="text" inputmode="numeric" autocomplete="off" value="' + esc((Array.isArray(cur) ? cur : []).join(", ")) + '" placeholder="-100123…, -5330147776" /></div>';
    }
    return '<div class="cstrip gov ' + c.cls + '" data-project="' + s + '" data-field="' + esc(field) + '" data-to="' + (to ? "true" : "false") + '" data-ack="' + (c.ack ? "1" : "0") + '">' +
      '<div class="ch"><span class="cv">' + esc(c.v) + '</span><span class="ct">' + s + '</span><span class="cx" data-cx="1">✕</span></div>' +
      '<div class="cn' + (c.ack ? " loud" : "") + '">' + esc(c.note) + '</div>' + body +
      '<div class="gfield"><span class="fl">reason (recorded in the audit row)</span><input class="greason" type="text" autocomplete="off" maxlength="500" placeholder="why — e.g. op#… / client direction" /></div>' +
      (c.ack ? '<div class="gfield"><span class="fl">acknowledge</span><input class="gack" type="text" autocapitalize="characters" autocomplete="off" placeholder="type ' + esc(GOV_MONEY_ACK) + '" /></div>' : "") +
      '<div class="cr"><input class="ci" type="text" autocapitalize="off" autocorrect="off" autocomplete="off" spellcheck="false" placeholder="type ' + s + ' to arm" />' +
      '<button class="cf" disabled>' + esc(c.v) + '</button></div>' +
    '</div>';
  }
  function govOpRowHtml(o) {
    return '<div class="oprow"><input class="opn" type="text" autocomplete="off" placeholder="name" value="' + esc(o.name || "") + '" />' +
      '<input class="opid" type="text" inputmode="numeric" autocomplete="off" placeholder="Telegram user id" value="' + esc(o.chat_id || "") + '" />' +
      '<label><input class="opint" type="checkbox"' + (o.internal ? " checked" : "") + ' /> internal</label>' +
      '<span class="opx" data-opx="1">✕</span></div>';
  }
  function govOpenStrip(project, field, to) {
    var box = $("govConfirm"); if (!box) return;
    var cur = null;
    if (govData) (govData.projects || []).forEach(function (p) { if (p.project === project) cur = p[field]; });
    box.innerHTML = govStripHtml(project, field, to, cur);
    var inp = box.querySelector(".greason");
    try { box.scrollIntoView({ behavior: "smooth", block: "center" }); } catch (e) {}
    if (inp) setTimeout(function () { try { inp.focus(); } catch (er) {} }, 60);
  }
  // Arms ONLY when: typed == project, reason non-empty, and (money ON) ack == phrase.
  function govConfirmTyped(strip) {
    var inp = strip.querySelector(".ci"), fire = strip.querySelector(".cf"), rs = strip.querySelector(".greason"), ak = strip.querySelector(".gack");
    var ok = !!(inp && inp.value.trim() === strip.getAttribute("data-project")) &&
             !!(rs && rs.value.trim()) &&
             (strip.getAttribute("data-ack") !== "1" || !!(ak && ak.value.trim() === GOV_MONEY_ACK));
    strip.classList.toggle("armed", ok);
    if (fire) fire.disabled = !ok;
    return ok;
  }
  function govValueFromStrip(strip) {
    var field = strip.getAttribute("data-field");
    if (field === "operators") {
      var rows = strip.querySelectorAll(".oprow"), out = [];
      Array.prototype.forEach.call(rows, function (r) {
        var n = r.querySelector(".opn"), id = r.querySelector(".opid"), it = r.querySelector(".opint");
        if (!n || !id) return;
        if (!n.value.trim() && !id.value.trim()) return;   // blank row = ignored
        out.push({ name: n.value.trim(), chat_id: id.value.trim(), internal: !!(it && it.checked) });
      });
      return out;
    }
    if (field === "channels") {
      var ch = strip.querySelector(".gch");
      return ch ? ch.value.split(",").map(function (x) { return x.trim(); }).filter(Boolean) : [];
    }
    return strip.getAttribute("data-to") === "true";
  }
  function govFire(strip) {
    if (!govConfirmTyped(strip)) { toast("not armed — type the project name, a reason" + (strip.getAttribute("data-ack") === "1" ? " and the acknowledgement" : ""), true); return; }
    if (govBusy) return;
    var project = strip.getAttribute("data-project"), field = strip.getAttribute("data-field");
    var typed = strip.querySelector(".ci").value.trim(), reason = strip.querySelector(".greason").value.trim();
    var ak = strip.querySelector(".gack"), ack = ak ? ak.value.trim() : "";
    var value = govValueFromStrip(strip);
    var fire = strip.querySelector(".cf"); if (fire) { fire.disabled = true; fire.textContent = "…"; }
    govBusy = true;
    fetch("/api/governance-set", {
      method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, authHeaders(), armedHeaders()),
      body: JSON.stringify({ project: project, field: field, value: value, confirm: typed, reason: reason, money_ack: ack })
    }).then(function (r) { return r.json().then(function (j) { return { s: r.status, ok: r.ok, j: j || {} }; }, function () { return { s: r.status, ok: r.ok, j: {} }; }); })
      .then(function (res) {
        if (res.s === 401) { govKeyMissing(); govConfirmTyped(strip); if (fire) fire.textContent = govStripCopy(project, field, value === true).v; return; }
        if (res.s === 503) { toast("✗ " + (res.j.error || "governance writes disabled on this console"), true); govConfirmTyped(strip); if (fire) fire.textContent = govStripCopy(project, field, value === true).v; return; }
        if (!res.ok || !res.j.ok) { toast("✗ " + (res.j.error || "governance write failed"), true); govConfirmTyped(strip); if (fire) fire.textContent = govStripCopy(project, field, value === true).v; return; }
        toast("✓ " + project + " · " + (GOV_LABEL[field] || field) + " updated (audit #" + (res.j.audit_id || "?") + ")");
        var box = $("govConfirm"); if (box) box.innerHTML = "";
        loadGov(true);
      })
      .catch(function () { toast("✗ network dropped — reload to see whether the change landed", true); })
      .finally(function () { govBusy = false; });
  }
  function wireGov() {
    var g = $("gov");
    if (g) g.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-gact]") : null; if (!b) return;
      var card = b.closest(".gcard"); if (!card) return;
      var project = card.getAttribute("data-project"), field = b.getAttribute("data-field");
      govOpenStrip(project, field, b.getAttribute("data-to") === "true");
    });
    var rl = $("govReload"); if (rl) rl.addEventListener("click", function () { loadGov(true); toast("reloading governance…"); });
    var box = $("govConfirm");
    if (box) {
      box.addEventListener("input", function (e) { var st = e.target.closest ? e.target.closest(".cstrip") : null; if (st) govConfirmTyped(st); });
      box.addEventListener("change", function (e) { var st = e.target.closest ? e.target.closest(".cstrip") : null; if (st) govConfirmTyped(st); });
      box.addEventListener("keydown", function (e) {
        var st = e.target.closest ? e.target.closest(".cstrip") : null; if (!st) return;
        if (e.key === "Enter" && e.target.classList && e.target.classList.contains("ci")) { e.preventDefault(); govFire(st); }
        if (e.key === "Escape") { box.innerHTML = ""; }
      });
      box.addEventListener("click", function (e) {
        var st = e.target.closest ? e.target.closest(".cstrip") : null; if (!st) return;
        if (e.target.closest(".cf")) { govFire(st); return; }
        if (e.target.closest("[data-cx]")) { box.innerHTML = ""; return; }
        var add = e.target.closest(".opadd"); if (add) { add.insertAdjacentHTML("beforebegin", govOpRowHtml({})); return; }
        var x = e.target.closest("[data-opx]"); if (x) { var row = x.closest(".oprow"); if (row && row.parentNode) row.parentNode.removeChild(row); govConfirmTyped(st); }
      });
    }
    // second armed-key field (same localStorage slot as the sheet's) so the
    // governance section is usable without opening a lane sheet. Never echoed.
    var gk = $("govArmedKey");
    if (gk) {
      gk.value = armedKey();
      gk.addEventListener("input", function () { setArmedKey(gk.value.trim()); gk.classList.remove("miss"); var ak = $("armedKey"); if (ak) ak.value = gk.value; });
      gk.addEventListener("change", function () { setArmedKey(gk.value.trim()); });
    }
  }

  // ---- static wiring (bound once) -------------------------------------------
  function wire() {
    var mt = $("multiToggle"); if (mt) mt.addEventListener("click", toggleMulti);
    // fc-v52: BLOAT stat cell taps open the top-3 offenders (delegated on the row).
    var sr = $("statRow");
    if (sr) sr.addEventListener("click", function (e) {
      var cell = e.target.closest ? e.target.closest("#bloatCell.tapx") : null;
      if (cell) toggleBloat();
    });
    // fc-v52: drain-board assign affordance.
    var at = $("assignToggle");
    if (at) at.addEventListener("click", function () {
      var f = $("assignForm");
      var open = f.classList.toggle("open");
      at.classList.toggle("on", open);
      if (open && $("assignAsk")) $("assignAsk").focus();
    });
    var asend = $("assignSend"); if (asend) asend.addEventListener("click", sendAssign);
    // fc-v55: swipe/tap-to-confirm on a "Your asks" review card (delegated once).
    var asks = $("asks");
    if (asks) {
      // Tap the review CTA → confirm.
      asks.addEventListener("click", function (e) {
        var cta = e.target.closest ? e.target.closest(".arev[data-confirm]") : null;
        if (cta) confirmAsk(cta.getAttribute("data-confirm"), "confirm");
      });
      // Horizontal swipe on a confirmable card → confirm (right) / dismiss (left).
      var sx = null, sy = null, scard = null;
      asks.addEventListener("touchstart", function (e) {
        var c = e.target.closest ? e.target.closest(".ask.confirmable") : null;
        if (!c) { scard = null; return; }
        scard = c; sx = e.touches[0].clientX; sy = e.touches[0].clientY;
      }, { passive: true });
      asks.addEventListener("touchend", function (e) {
        if (!scard || sx == null) return;
        var t = e.changedTouches[0], dx = t.clientX - sx, dy = t.clientY - sy;
        if (Math.abs(dx) > 64 && Math.abs(dx) > Math.abs(dy) * 1.6) {
          var id = scard.getAttribute("data-ask");
          confirmAsk(id, dx > 0 ? "confirm" : "drop");
        }
        scard = null; sx = sy = null;
      }, { passive: true });
    }
    var aask = $("assignAsk");
    if (aask) aask.addEventListener("keydown", function (e) { if (e.key === "Enter") { e.preventDefault(); sendAssign(); } });
    var bp = $("bulkPreview"); if (bp) bp.addEventListener("click", bulkPreviewRun);
    var bc = $("bulkClear"); if (bc) bc.addEventListener("click", function () { selected = {}; renderLanes(lastLanes); updateDock(); });
    var sc = $("shClose"); if (sc) sc.addEventListener("click", closeSheet);
    // CAI-RESP-1434: armed-key field (in the sheet / lane-manager area). Persisted
    // to localStorage only; cleared on empty. Never echoed anywhere.
    var ak = $("armedKey");
    if (ak) {
      ak.value = armedKey();
      ak.addEventListener("input", function () { setArmedKey(ak.value.trim()); ak.classList.remove("miss"); });
      ak.addEventListener("change", function () { setArmedKey(ak.value.trim()); });
    }
    var scrim = $("scrim"); if (scrim) scrim.addEventListener("click", closeSheet);
    // action grid (delegated)
    var acts = $("shActions");
    if (acts) acts.addEventListener("click", function (e) {
      var b = e.target.closest ? e.target.closest("button[data-act]") : null;
      if (b) handleAction(b.getAttribute("data-act"), b);
    });
    // typed-confirm strip (delegated): input converges → arms; Enter/button fires; ✕ disarms
    var cbox = $("shConfirm");
    if (cbox) {
      cbox.addEventListener("input", function (e) { var st = e.target.closest ? e.target.closest(".cstrip") : null; if (st) confirmTyped(st); });
      cbox.addEventListener("keydown", function (e) {
        var st = e.target.closest ? e.target.closest(".cstrip") : null; if (!st) return;
        if (e.key === "Enter") { e.preventDefault(); fireConfirmed(st); }
        if (e.key === "Escape") { cbox.innerHTML = ""; }
      });
      cbox.addEventListener("click", function (e) {
        var st = e.target.closest ? e.target.closest(".cstrip") : null; if (!st) return;
        if (e.target.closest(".cf")) fireConfirmed(st);
        else if (e.target.closest("[data-cx]")) cbox.innerHTML = "";
      });
    }
    // token/model controls (delegated)
    var ctrls = $("shControls");
    if (ctrls) {
      ctrls.addEventListener("change", function (e) {
        var el = e.target;
        if (el && el.tagName === "SELECT" && el.dataset.kind) setPointer(el.dataset.session, el.dataset.kind, el.value);
      });
      ctrls.addEventListener("click", function (e) {
        var el = e.target; if (!el || !el.dataset) return;
        if (el.dataset.apply) previewApply(el.dataset.session, el.dataset.apply);
        else if (el.dataset.armapply) applyArmed(el.dataset.session, el.dataset.armapply);
      });
    }
  }

  function start() {
    wire();
    wireGov();
    loadBuild();
    var cached = loadLastGood();
    if (cached) { setLive(false, "● reconnecting…"); applyData(cached); }
    tick();
    // governance registry: its own slower cadence (subprocess read server-side).
    loadGov(false); setInterval(function () { loadGov(false); }, 60000);
  }

  // ---- pull-to-refresh — verbatim from fc-v49 -------------------------------
  (function () {
    var ptr = $("ptr"), ptrTxt = $("ptrTxt");
    if (!ptr) return;
    var startY = 0, pulling = false, armed = false;
    var ARM = 90, DAMP = 0.5, MAX = 90;
    document.addEventListener("touchstart", function (e) {
      if (e.touches.length !== 1 || ptr.classList.contains("refreshing")) return;
      if (e.target.closest && e.target.closest(".peek .body")) { pulling = false; return; }
      if (e.target.closest && e.target.closest(".sheet")) { pulling = false; return; }
      if ((window.scrollY || document.documentElement.scrollTop) > 0) { pulling = false; return; }
      startY = e.touches[0].clientY; pulling = true; armed = false; ptr.classList.remove("snap");
    }, { passive: true });
    document.addEventListener("touchmove", function (e) {
      if (!pulling) return;
      var dy = e.touches[0].clientY - startY;
      if (dy <= 0) { pulling = false; ptr.classList.remove("pulling"); ptr.style.height = "0px"; return; }
      e.preventDefault();
      ptr.classList.add("pulling");
      ptr.style.height = Math.min(MAX, dy * DAMP) + "px";
      armed = dy >= ARM; ptr.classList.toggle("armed", armed);
      ptrTxt.textContent = armed ? "Release to refresh" : "Pull to refresh";
    }, { passive: false });
    function end() {
      if (!pulling) return; pulling = false; ptr.classList.add("snap");
      if (armed) { ptr.classList.add("refreshing"); ptrTxt.textContent = "Refreshing…"; ptr.style.height = "44px"; setTimeout(function () { window.location.reload(); }, 150); }
      else { ptr.style.height = "0px"; ptr.classList.remove("armed"); ptr.classList.remove("pulling"); }
    }
    document.addEventListener("touchend", end);
    document.addEventListener("touchcancel", function () { pulling = false; ptr.style.height = "0px"; ptr.classList.remove("armed"); ptr.classList.remove("pulling"); });
  })();

  // Node-only: expose the pure helpers for the unit tests (inert in the browser).
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { pickTopBloat: pickTopBloat, coordCtxRows: coordCtxRows, poolChip: poolChip, hoursToReset: hoursToReset, minutesToReset: minutesToReset, fmtReset: fmtReset, next5hBoundary: next5hBoundary,
      ctxDisplayFrom: ctxDisplayFrom, idleLabel: idleLabel,
      poolOf: poolOf, tokChip: tokChip, poolRollup: poolRollup,
      mdlChip: mdlChip, shortModel: shortModel, routineSummary: routineSummary, collapsedHtml: collapsedHtml,
      tileHtml: tileHtml, coordChip: coordChip, armedHeaders: armedHeaders, ARMED_KEY_LS: ARMED_KEY_LS,
      confirmTyped: confirmTyped, CONFIRM_COPY: CONFIRM_COPY, resetBodyFor: resetBodyFor,
      govCardHtml: govCardHtml, govStripCopy: govStripCopy, govStripHtml: govStripHtml, GOV_MONEY_ACK: GOV_MONEY_ACK };
  }

  start();
})();
