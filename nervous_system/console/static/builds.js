// Build-status view (op#34918/34919 — orch-console 78503 plan / 78505 GO):
// read-only paint of the THREE programmes Musa is watching move (substrate
// 2.0 / memory 2.0 / TG 2.0) plus the honest ask->live delivery times. The
// server owns every derivation (/api/build-status); this page just paints
// what the owning lanes recorded via scripts/update_programme_phases.py and
// re-fetches every 60s so the view stays current while it is open.
//
// HONESTY CONTRACT (78505): an ask with no live-verified delivered_at stamp
// is NOT MEASURED — it paints with NO time and a line saying why. Never a
// zero, never a dash pretending to be a clock.
//
// Auth: operator token sent ONLY in the Authorization header (never the
// URL), same localStorage key as the rest of the console, so the operator
// enters it ONCE per device (asks/docs/media/status pattern).
(function () {
  "use strict";

  // fc-v87: the token may arrive as a #t=<token> FRAGMENT — same one-tap
  // deep link as asks/docs/media/status (fc-v82-85): fragments never leave
  // the browser (no server logs, no link-preview fetches, no referrers),
  // persisted BEFORE the token is read, then history.replaceState strips it
  // from the URL bar/history. Floor is 8 chars = the REAL CONSOLE_TOKEN
  // length class (bus 76254).
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

  // ---- pure render helpers (unit-locked in tests/console/builds_view.test.js;
  // the phases/delivery SEMANTICS are server-side,
  // tests/console/test_app.py + tests/test_build_programmes.py) ----
  function fmtAge(s) {
    if (s == null) return "";
    s = Number(s);
    if (isNaN(s)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function fmtHours(h) {
    // HONESTY: null/undefined is an ABSENCE, not a zero — "" paints nothing,
    // the not-measured copy explains why (never a fake "0h").
    if (h == null || isNaN(Number(h))) return "";
    h = Number(h);
    if (h < 24) return h.toFixed(1).replace(/\.0$/, "") + "h";
    return (h / 24).toFixed(1) + "d";
  }
  var MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  // OPERATOR-FACING times render in Asia/Dubai (UTC+4, no DST) — NOT
  // UTC-as-if-local (orch-console 78520 note 2: 09:14Z must read 13:14 for
  // Musa). Deterministic +4 shift instead of a locale call so node and every
  // browser agree; the hero says "Dubai time" so the zone is never implied.
  var DUBAI_OFFSET_MS = 4 * 3600 * 1000;
  function fmtDate(s) {
    if (!s) return "";
    var d = new Date(s);
    if (isNaN(d.getTime())) return String(s);
    var dub = new Date(d.getTime() + DUBAI_OFFSET_MS);
    return dub.getUTCDate() + " " + MON[dub.getUTCMonth()] +
      " " + String(dub.getUTCHours()).padStart(2, "0") + ":" + String(dub.getUTCMinutes()).padStart(2, "0");
  }
  function fmtDay(s) { // date-only field (next_checkpoint, phase date): a
    // calendar date, no clock to shift — read as-is at UTC midnight.
    if (!s) return "";
    var d = new Date(s + "T00:00:00Z");
    if (isNaN(d.getTime())) return String(s);
    return d.getUTCDate() + " " + MON[d.getUTCMonth()];
  }

  function unauthorized() {
    $("main").innerHTML = '<div class="empty">Unauthorized — check the operator token and Connect.</div>';
  }

  function phaseChip(ph) {
    // status is one of the helper's VALID_STATUSES (done|building|next|blocked)
    var st = ph.status || "next";
    var mark = st === "done" ? "✓ " : st === "building" ? "● " : "";
    var date = ph.date ? " · " + esc(fmtDay(ph.date)) : "";
    return '<span class="ph ' + esc(st) + '">' + mark + esc(ph.name) + date + "</span>";
  }

  function programmeCard(p) {
    var lp = p.last_progress || {};
    return '<div class="item prog">' +
      '<div class="t"><span class="ttl">' + esc(p.ask) + "</span></div>" +
      '<div class="meta">' +
        '<span class="kind">on it: ' + esc(p.owner || "—") + "</span>" +
        '<span class="age">moved ' + esc(fmtAge(p.updated_age_s)) + "</span>" +
        (p.next_checkpoint ? '<span class="party">next check-in ' + esc(fmtDay(p.next_checkpoint)) + "</span>" : "") +
      "</div>" +
      '<div class="phases">' + (p.phases || []).map(phaseChip).join("") + "</div>" +
      (lp.line ? '<div class="note"><b>last real movement</b> ' + esc(lp.line) +
        (lp.at ? ' <span class="when">· ' + esc(fmtDate(lp.at)) + "</span>" : "") + "</div>" : "") +
    "</div>";
  }

  // op#35396 (orch-console 80499): a pending (no written summary) row's line
  // encodes only who owes it + the channel tag — it carries no per-ask
  // identity, so >3 rows with the SAME line are one undifferentiated fact
  // ("N asks waiting on this owner"), not N things to read. Collapse those
  // into one row with a count; 3-or-fewer stay listed (not worth the row).
  var PENDING_COLLAPSE_THRESHOLD = 3;
  function groupPending(rows) {
    var seen = [];     // insertion-ordered pending line -> items[]
    var index = {};
    var out = [];
    (rows || []).forEach(function (r) {
      if (!r.pending) { out.push(r); return; }
      if (!index[r.line]) { index[r.line] = { line: r.line, items: [] }; seen.push(r.line); }
      index[r.line].items.push(r);
    });
    seen.forEach(function (line) {
      var items = index[line].items;
      if (items.length > PENDING_COLLAPSE_THRESHOLD) {
        out.push({ collapsed: true, pending: true, line: line, count: items.length });
      } else {
        out = out.concat(items);
      }
    });
    return out;
  }

  function collapsedRow(cls, g) {
    return '<div class="item drow ' + cls + ' collapsed">' +
      '<div class="t"><span class="ttl">' + esc(g.line) + "</span>" +
      '<span class="pill">×' + esc(String(g.count)) + "</span></div></div>";
  }

  // op#35502/35503 follow-up (orch-console bus #81240/#81244): two
  // operator_asks can be genuinely SEPARATE asks that one shipment resolved
  // together -- same written summary, delivered_at identical to the
  // microsecond (db.py never fabricates this; root-caused live on irsyad
  // #5526/#5527 and #5626/#5627 -- two distinct follow-up messages, one ship).
  // Shown as two cards with the same text they read as a mirror/dedupe bug;
  // collapse consecutive same-line+same-delivered_at MEASURED rows into one
  // card with an ×N badge. Display only -- the server's data/coverage counts
  // are untouched, this never changes what's "measured". Pending rows (no
  // written summary) are left alone here; groupPending's own >3 collapse
  // still runs on whatever this leaves behind.
  function groupDuplicateDeliveries(rows) {
    var seen = [];
    var index = {};
    var out = [];
    (rows || []).forEach(function (r) {
      if (r.pending || r.delivered_at == null) { out.push(r); return; }
      var key = r.line + "\u0000" + r.delivered_at;
      if (!index[key]) { index[key] = { line: r.line, items: [] }; seen.push(key); }
      index[key].items.push(r);
    });
    seen.forEach(function (key) {
      var items = index[key].items;
      if (items.length > 1) {
        out.push({ collapsed: true, pending: false, line: items[0].line, count: items.length });
      } else {
        out = out.concat(items);
      }
    });
    return out;
  }

  function deliveryRow(m) {
    if (m.collapsed) return collapsedRow("m-row", m);
    return '<div class="item drow m-row">' +
      '<div class="t"><span class="ttl">' + esc(m.line || ("ask #" + m.id)) + "</span>" +
      '<span class="pill">' + esc(fmtHours(m.total_hours)) + "</span></div>" +
      '<div class="meta">' +
        '<span class="when">asked ' + esc(fmtDate(m.asked_at)) + " · live " + esc(fmtDate(m.delivered_at)) + "</span>" +
      "</div></div>";
  }

  // op#35502/35503 (orch-console 80983/80995): Ask→live times grouped by
  // repo, each an expandable <details> section — collapsed by default except
  // the one holding the most recent ship. The server (db.fetch_ask_delivery_
  // times) already sorted by_repo most-recent-ship-first and computed the
  // median, so this is pure paint: no client-side grouping logic to drift
  // from the server's.
  function repoSectionHtml(g, isDefaultOpen) {
    var rows = "";
    if (g.measured && g.measured.length) {
      rows += '<div class="items">' + groupPending(groupDuplicateDeliveries(g.measured)).map(deliveryRow).join("") + "</div>";
    }
    if (g.not_measured && g.not_measured.length) {
      rows += '<div class="items">' + groupPending(g.not_measured).map(notMeasuredRow).join("") + "</div>";
    }
    var meta = esc(String(g.count)) + (g.count === 1 ? " ask" : " asks");
    if (g.median_hours != null) meta += " · " + esc(fmtHours(g.median_hours)) + " median";
    // orch-console 81075: whole header row is the tap target (native
    // <summary> already does this), a clearly visible rotating chevron, and
    // an explicit aria-expanded kept in sync on toggle (see the listener
    // wired in paint() below) for assistive tech that doesn't infer state
    // from <details open> alone.
    return '<details class="repo-group"' + (isDefaultOpen ? " open" : "") + '>' +
      '<summary aria-expanded="' + (isDefaultOpen ? "true" : "false") + '">' +
        '<span class="repo-left"><span class="chev" aria-hidden="true">&#10148;</span>' +
        '<span class="repo-name">' + esc(g.label) + "</span></span>" +
        '<span class="repo-meta">' + meta + "</span></summary>" +
      rows +
    "</details>";
  }

  function notMeasuredRow(nm) {
    if (nm.collapsed) return collapsedRow("nm-row", nm);
    // NO time fields exist on the row (server contract) and none are painted:
    // the honesty copy explains the absence instead of inventing a number.
    return '<div class="item drow nm-row">' +
      '<div class="t"><span class="ttl">' + esc(nm.line || ("ask #" + nm.id)) + "</span></div>" +
      '<div class="meta"><span class="when">no clock yet — time counts only from a live-verified ship</span></div>' +
    "</div>";
  }

  function paint(body) {
    var main = $("main");
    var progs = (body && body.programmes) || [];
    var times = (body && body.delivery_times) || {};
    var measured = times.measured || [];
    var notMeasured = times.not_measured || [];
    var cov = times.coverage || {};
    var byRepo = times.by_repo || [];
    if (!progs.length) {
      main.innerHTML = '<div class="empty">No programmes yet — seed via scripts/seed_build_programmes.py.</div>';
      return;
    }
    var html = '<div class="section">Programmes</div><div class="items">' +
      progs.map(programmeCard).join("") + "</div>";
    html += '<div class="section">Ask → live times</div>';
    // orch-console 81075: the hero stays to the two headline figures only —
    // the unattributed count is internal jargon here and wrapped onto its
    // own line; it still lives fully visible as its own group, last in the
    // list, never hidden.
    html += '<div class="hero"><span class="big">' + esc(String(cov.measured || measured.length)) + "</span> measured" +
      '<span class="sep">·</span><span class="big warn">' + esc(String(cov.not_measured || notMeasured.length)) +
      "</span> not yet measured" +
      '<div class="herosub">times come from live-verified ships only — older asks have no clock yet · Dubai time (UTC+4)</div></div>';
    if (byRepo.length) {
      // the server already sorted by_repo most-recent-ship-first; only the
      // group actually holding that ship opens by default (op#35502/35503).
      var defaultOpenRepo = byRepo[0].latest_delivered_at ? byRepo[0].repo : null;
      html += '<div class="repo-groups">' + byRepo.map(function (g) {
        return repoSectionHtml(g, g.repo === defaultOpenRepo);
      }).join("") + "</div>";
    } else {
      // fallback for a server response with no by_repo (older cached build) —
      // the pre-grouping flat render.
      if (measured.length) {
        html += '<div class="items">' + groupPending(measured).map(deliveryRow).join("") + "</div>";
      }
      if (notMeasured.length) {
        html += '<div class="items">' + groupPending(notMeasured).map(notMeasuredRow).join("") + "</div>";
      }
    }
    main.innerHTML = html;
    // keep aria-expanded in sync with <details open> as the operator taps a
    // repo-group header — native <details> toggles `open` itself; it does
    // not touch our explicit aria-expanded (orch-console 81075).
    var groupEls = main.querySelectorAll ? main.querySelectorAll("details.repo-group") : [];
    for (var gi = 0; gi < groupEls.length; gi++) {
      (function (el) {
        el.addEventListener("toggle", function () {
          var s = el.querySelector && el.querySelector("summary");
          if (s) s.setAttribute("aria-expanded", el.open ? "true" : "false");
        });
      })(groupEls[gi]);
    }
  }

  var inFlight = false;
  function load() {
    if (inFlight) return;
    inFlight = true;
    fetch("/api/build-status", { headers: authHeaders() })
      .then(function (r) {
        inFlight = false;
        if (r.status === 401) { unauthorized(); return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (body) { if (body) paint(body); })
      .catch(function () {
        inFlight = false;
        $("main").innerHTML = '<div class="empty">Could not reach /api/build-status — try refresh.</div>';
      });
  }

  function connect() {
    var v = ($("token").value || "").trim();
    if (!v) return;
    token = v;
    try { localStorage.setItem("console_token", token); } catch (e) {}
    load();
  }

  $("connect").addEventListener("click", connect);
  $("token").addEventListener("keydown", function (e) {
    if (e.key === "Enter") connect();
  });

  if (token) load();
  setInterval(load, 60000); // stay current while open (status.js cadence)

  // export pure helpers for the node unit test (status.js pattern)
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { tokenFromHash: tokenFromHash, esc: esc, fmtAge: fmtAge,
      fmtHours: fmtHours, fmtDate: fmtDate, fmtDay: fmtDay, paint: paint,
      groupPending: groupPending, groupDuplicateDeliveries: groupDuplicateDeliveries,
      repoSectionHtml: repoSectionHtml };
  }
})();
