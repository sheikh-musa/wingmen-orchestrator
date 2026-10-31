// Machines-resources panel (op#36664) — read-only CPU/RAM/temp/swap view for
// the Mini, gzbai and cubeasht, sourced from host_metrics (/api/resources).
// No collector lives here: this page only paints what mini_monitor.py/
// gzb_monitor.py/cubeasht_monitor.py (cc-fleet-health) already wrote. Every
// number is labeled with its real source (e.g. "thermal pressure (pmset)",
// never "temp", so a reader never mistakes an estimate for a direct sensor
// reading) and a failed/old sample never paints as a live number — "could
// not measure" or a greyed STALE badge instead.
// Auth: operator token in the Authorization header only, same localStorage
// key as the rest of the console (asks/docs/media/status/builds pattern).
(function () {
  "use strict";

  function tokenFromHash(hash) {
    var m = /^#t=([^#&\s]{8,})$/.exec(String(hash == null ? "" : hash));
    return m ? m[1] : null;
  }
  var deepTok = tokenFromHash(location.hash);
  if (deepTok) {
    try { localStorage.setItem("console_token", deepTok); } catch (e) {}
    if (typeof history !== "undefined" && history.replaceState)
      history.replaceState(null, "", location.pathname + location.search);
  }
  var token = localStorage.getItem("console_token") || sessionStorage.getItem("console_token") || "";

  var $ = function (id) { return document.getElementById(id); };
  function authHeaders() { return { Authorization: "Bearer " + token }; }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // ---- pure helpers (unit-locked in tests/console/resources.test.js) --------
  function fmtAge(s) {
    if (s == null) return "";
    s = Number(s);
    if (isNaN(s)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }

  // 15 min = 3 missed 5-min samples (every collector's own cadence) — a
  // single late sample never trips this, a stalled collector does.
  var STALE_S = 900;
  function isStale(age_s) { return age_s != null && age_s > STALE_S; }

  // null/undefined -> null (caller renders "could not measure" — never a
  // frozen number presented as live); a real value (incl. 0) -> formatted.
  function measure(v, fmt) {
    return (v == null) ? null : fmt(v);
  }
  function pct1(v) { return v.toFixed(1) + "%"; }
  function c1(v) { return v.toFixed(1) + "°C"; }

  // Mini's thermal field: {recorded_fields, throttled} when pmset answered,
  // or absent entirely when the probe itself failed (probe_errors.thermal).
  // "estimate" is called out explicitly — this is pmset thermal PRESSURE,
  // not a direct die-temperature/wattage reading.
  function thermalLabel(thermal) {
    if (!thermal) return null;
    return thermal.throttled ? "THROTTLED (pmset estimate)" : "normal (pmset estimate)";
  }

  function row(label, valueHtml) {
    return '<div class="row"><span class="rl">' + esc(label) + '</span>' +
      '<span class="rv">' + valueHtml + "</span></div>";
  }
  function measureRow(label, v, fmt) {
    var m = measure(v, fmt);
    return row(label, m == null ? '<span class="na">could not measure</span>' : esc(m));
  }

  function miniRows(m) {
    var out = "";
    out += measureRow("CPU load", m.cpu_load_pct, pct1);
    out += measureRow("RAM (real used)", m.mem_real_used_pct, pct1);
    out += measureRow("Swap used", m.swap_used_pct, pct1);
    var therm = thermalLabel(m.thermal);
    out += row("Thermal pressure (pmset estimate)",
      therm == null ? '<span class="na">could not measure</span>' : esc(therm));
    return out;
  }

  function gzbRows(m) {
    var out = "";
    var cpuLabel = (m.cpu_count != null) ? "CPU load (" + m.cpu_count + " vCPU)" : "CPU load";
    out += measureRow(cpuLabel, m.cpu_load_pct, pct1);
    out += measureRow("RAM (real used)", m.mem_real_used_pct, pct1);
    out += measureRow("Swap used", m.swap_used_pct, pct1);
    out += measureRow("CPU temp (thermal_zone0)", m.cpu_temp_c, c1);
    // steal-time isn't collected yet (handed to cc-fleet-health, op#36664) —
    // always "could not measure" until gzb_monitor.py carries cpu_steal_pct.
    out += measureRow("CPU steal", m.cpu_steal_pct, pct1);
    return out;
  }

  function cubeashtRows(m) {
    var wsl = m.wsl || {};
    var out = "";
    out += '<div class="sub">Windows host</div>';
    out += measureRow("CPU load", m.cpu_load_pct, pct1);
    out += measureRow("RAM used", m.ram_used_pct, pct1);
    out += measureRow("Disk C: free", m.disk_c_free_pct, pct1);
    out += measureRow("CPU temp", m.cpu_temp_c, c1);
    out += measureRow("GPU temp", m.gpu_temp_c, c1);
    out += measureRow("NVMe temp", m.nvme_temp_c, c1);
    out += '<div class="sub">WSL (ci-orchestrator)</div>';
    out += measureRow("Mem used", wsl.mem_used_pct, pct1);
    out += row("Runner service", esc(wsl.runner_service != null ? wsl.runner_service : "could not measure"));
    return out;
  }

  var HOSTS = [
    { host: "mini", label: "Mac Mini", rows: miniRows },
    { host: "gzb", label: "gzbai", rows: gzbRows },
    { host: "cubeasht", label: "cubeasht", rows: cubeashtRows },
  ];

  function hostCard(h, byHost) {
    var rec = byHost[h.host];
    if (!rec) {
      return '<div class="item"><div class="t"><span class="ttl">' + esc(h.label) + '</span>' +
        '<span class="pill na">no data</span></div></div>';
    }
    var stale = isStale(rec.age_s);
    var dark = (!rec.ok || stale) ? " dark" : "";
    var pill = !rec.ok ? '<span class="pill err">COULD NOT MEASURE</span>'
      : stale ? '<span class="pill stale">STALE</span>' : "";
    var body = !rec.ok
      ? '<div class="note">Last probe failed' + (rec.error ? ": " + esc(rec.error) : "") + "</div>"
      : h.rows(rec.metrics || {});
    return '<div class="item' + dark + '">' +
      '<div class="t"><span class="ttl">' + esc(h.label) + "</span>" + pill + "</div>" +
      '<div class="age">sample ' + esc(fmtAge(rec.age_s)) + "</div>" +
      body +
      "</div>";
  }

  function unauthorized() {
    $("main").innerHTML = '<div class="empty">Unauthorized — check the operator token and Connect.</div>';
  }

  function paint(body) {
    var rows = (body && body.hosts) || [];
    var byHost = {};
    rows.forEach(function (r) { byHost[r.host] = r; });
    $("main").innerHTML = '<div class="items">' +
      HOSTS.map(function (h) { return hostCard(h, byHost); }).join("") +
      "</div>";
  }

  function load() {
    if (!token) return;
    $("main").innerHTML = '<div class="empty">Loading machine resources…</div>';
    fetch("/api/resources", { headers: authHeaders() })
      .then(function (r) {
        if (r.status === 401) { unauthorized(); throw new Error("401"); }
        return r.json();
      })
      .then(paint)
      .catch(function () {});
  }

  function render() {
    if (!token) {
      $("main").innerHTML = '<div class="empty">Enter the operator token and Connect.</div>';
      return;
    }
    load();
  }
  function connect() {
    token = $("token").value.trim();
    if (!token) return;
    localStorage.setItem("console_token", token);
    load();
  }
  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", function (e) { if (e.key === "Enter") connect(); });
  if (token) { $("token").value = token; }
  render();

  // Matches each collector's own 5-min cadence — a live phone view should
  // never be more stale than one missed sample.
  if (typeof setInterval !== "undefined") setInterval(load, 60000);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      tokenFromHash: tokenFromHash, esc: esc, fmtAge: fmtAge, isStale: isStale,
      measure: measure, pct1: pct1, c1: c1, thermalLabel: thermalLabel,
      miniRows: miniRows, gzbRows: gzbRows, cubeashtRows: cubeashtRows,
      hostCard: hostCard, HOSTS: HOSTS,
    };
  }
})();
