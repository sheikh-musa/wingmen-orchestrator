// asks.js — fleet-wide SSOT asks board (op#61107). Read-only; polls
// /api/asks-board (db.build_fleet_asks_query — every open 'ask' row, every
// surface, status derived live). Vanilla JS, bearer auth from localStorage
// (same pattern as lanes.js/fleet.js). The ONLY new rendering idea here is the
// grouping (hero "waiting on Musa" + per-owner sections) — the card markup
// itself is fleet.js's existing .ask/.atag/.ameta, reused verbatim so a reader
// already familiar with "Your asks" on /fleet recognizes this instantly.
(function () {
  "use strict";
  var token = localStorage.getItem("console_token") || "";
  function authHeaders() { return token ? { Authorization: "Bearer " + token } : {}; }
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtAge(s) {
    if (s == null) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m";
    if (s < 86400) return Math.round(s / 3600) + "h";
    return Math.round(s / 86400) + "d";
  }
  function fmtDate(s) {
    if (!s) return "";
    try {
      var d = new Date(s);
      return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    } catch (e) { return ""; }
  }

  // ── P1 board axes (op#33598) — pure helpers, exported for the Node test ──
  var TOPIC_ORDER = ["adcda-schedule", "infra", "cosem", "irsyad", "other"];
  function splitByBoardStatus(open) {
    var out = { truly_outstanding: [], in_progress: [] };
    (open || []).forEach(function (a) {
      (a.board_status === "in_progress" ? out.in_progress : out.truly_outstanding).push(a);
    });
    return out;
  }
  function topicCounts(rows) {
    var out = {}; TOPIC_ORDER.forEach(function (t) { out[t] = 0; });
    (rows || []).forEach(function (a) {
      var t = a.topic || "other";
      if (!(t in out)) out[t] = 0;
      out[t]++;
    });
    return out;
  }
  // needs_operator_decision is the structured flag (pending migration); the
  // predicate takes it now so that migration is a data change, not a rework.
  function needsMusa(a) {
    return !!(a && (a.waiting_on_operator || a.needs_operator_decision));
  }

  var TABS = [
    { id: "truly_outstanding", label: "Outstanding" },
    { id: "in_progress",       label: "In progress" },
    { id: "recently_answered", label: "Answered" },
  ];
  var boardTab = "truly_outstanding";  // truly_outstanding | in_progress | recently_answered
  var topicFilter = null;              // null = all topics
  var lastPayload = null;

  var ASK_TAG = {
    needs_you:     { cls: "needs", label: "needs you" },
    delegate_done: { cls: "rev",   label: "review" },
    in_progress:   { cls: "prog",  label: "in progress" },
    pending:       { cls: "pend",  label: "pending" },
    on_nazim:      { cls: "naz",   label: "on orch-console" },
    waiting_on_musa: { cls: "needs", label: "waiting on Musa" }
  };
  var SURF_LABEL = { operator: "musa", "client-channel": "client" };

  function askMeta(a) {
    var to = a.delegated_to
      ? '<span class="ato">→ <b>' + esc(a.delegated_to) + '</b></span>'
      : '<span class="ato">not yet delegated</span>';
    var upd = a.updated_age_s, warn = (upd != null && upd >= 3600);
    var ageCls = warn ? "aage warnage" : "aage";
    var moved;
    if (a.status === "waiting_on_musa") moved = "asked " + fmtAge(a.asked_age_s) + " ago";
    else if (a.status === "needs_you") moved = "bounced back " + fmtAge(upd) + " ago";
    else if (a.status === "delegate_done") moved = "replied " + fmtAge(upd) + " ago";
    else if (a.status === "pending") moved = "sent " + fmtAge(upd) + " ago · unopened";
    else if (a.status === "on_nazim") moved = "asked " + fmtAge(a.asked_age_s) + " ago";
    else moved = "updated " + fmtAge(upd) + " ago";
    var parts = [to, '<span class="' + ageCls + '">' + esc(moved) + '</span>'];
    if (a.status !== "waiting_on_musa" && a.status !== "on_nazim" && a.asked_age_s != null)
      parts.push('<span>asked ' + esc(fmtAge(a.asked_age_s)) + ' ago</span>');
    // P1 (op#33598): surface the dating reality — due = committed_date,
    // chase = chase_by. Sparse by design (8 of 282 open asks dated at the
    // 2026-10-25 reconcile); the board shows the gap instead of hiding it.
    var due = a.committed_date ? "due " + fmtDate(a.committed_date) : "";
    var chase = a.chase_by ? "chase " + fmtDate(a.chase_by) : "";
    var bits = [due, chase].filter(Boolean).join(" · ");
    if (bits) parts.push('<span>' + esc(bits) + '</span>');
    return '<div class="ameta">' + parts.join('<span class="adot">·</span>') + '</div>';
  }
  function askCard(a) {
    var t = ASK_TAG[a.status] || ASK_TAG.pending;
    var needs = needsMusa(a) || a.status === "needs_you";
    var surf = SURF_LABEL[a.ask_surface] || a.ask_surface;
    return '<div class="ask' + (needs ? " needs" : "") + '" data-ask="' + a.id + '">' +
      '<span class="atag ' + t.cls + '">' + t.label + '</span>' +
      '<div class="abody"><div class="atxt">' + esc(a.text) + '</div>' + askMeta(a) + '</div>' +
      '<span class="surf">' + esc(surf) + '</span>' +
    '</div>';
  }

  function answerCard(a) {
    // Recently-answered side (P1): flat, newest-first (server orders by
    // closed_at DESC, capped) — proof asks LAND, not just that they exist.
    var surf = SURF_LABEL[a.ask_surface] || a.ask_surface || "";
    return '<div class="ask" data-ask="' + a.id + '">' +
      '<span class="atag rev">answered</span>' +
      '<div class="abody"><div class="atxt">' + esc(a.text) + '</div>' +
        '<div class="ameta"><span class="ato">→ <b>' + esc(a.delegated_to || "—") + '</b></span>' +
        '<span class="aage">answered ' + esc(fmtAge(a.answered_age_s)) + ' ago</span></div></div>' +
      '<span class="surf">' + esc(surf) + '</span>' +
    '</div>';
  }

  function render(payload) {
    lastPayload = payload;
    var heroEl = $("hero"), tabsEl = $("tabs"), chipsEl = $("chips"), groupsEl = $("groups"), sub = $("sub");
    if (!payload) {
      groupsEl.innerHTML = '<div class="err">Could not load — check the console connection.</div>';
      return;
    }
    var open = payload.open || [], answered = payload.answered || [], counts = payload.counts || {};
    var musa = open.filter(needsMusa);
    var bsCounts = counts.board_status || {};

    if (!open.length && !answered.length) {
      heroEl.innerHTML = ""; tabsEl.innerHTML = ""; chipsEl.innerHTML = "";
      groupsEl.innerHTML = '<div class="empty">No asks. ✨</div>';
      if (sub) sub.textContent = "0 open";
      return;
    }

    // hero: the brief's FLAG — needs Musa's OWN decision, pinned + unmissable
    heroEl.innerHTML = musa.length
      ? '<div class="hero"><div class="hero-h">Needs Musa <span class="n">' + musa.length + '</span></div>' +
          musa.map(askCard).join("") + '</div>'
      : "";

    // tabs: the brief's coarse STATUS axis (live counts from the server)
    tabsEl.innerHTML = TABS.map(function (t) {
      var n = t.id === "recently_answered" ? (counts.answered || 0) : (bsCounts[t.id] || 0);
      return '<button class="tab' + (boardTab === t.id ? " on" : "") + '" data-tab="' + t.id + '">' +
        t.label + ' <span class="n">' + n + '</span></button>';
    }).join("");
    Array.prototype.forEach.call(tabsEl.querySelectorAll(".tab"), function (b) {
      b.addEventListener("click", function () { boardTab = b.dataset.tab; render(lastPayload); });
    });

    var rows = boardTab === "recently_answered" ? answered : (splitByBoardStatus(open)[boardTab] || []);

    // topic chips: counts over the CURRENT tab's rows, All = unfiltered
    var tc = topicCounts(rows);
    chipsEl.innerHTML =
      '<button class="chip' + (topicFilter === null ? " on" : "") + '" data-topic="">all ' + rows.length + '</button>' +
      TOPIC_ORDER.map(function (t) {
        return '<button class="chip' + (topicFilter === t ? " on" : "") + '" data-topic="' + t + '">' +
          t + ' ' + tc[t] + '</button>';
      }).join("");
    Array.prototype.forEach.call(chipsEl.querySelectorAll(".chip"), function (b) {
      b.addEventListener("click", function () { topicFilter = b.dataset.topic || null; render(lastPayload); });
    });

    var shown = topicFilter ? rows.filter(function (a) { return (a.topic || "other") === topicFilter; }) : rows;
    if (!shown.length) {
      groupsEl.innerHTML = '<div class="empty">Nothing in this slice.</div>';
    } else if (boardTab === "recently_answered") {
      groupsEl.innerHTML = shown.map(answerCard).join("");
    } else {
      groupsEl.innerHTML = ownerGroups(shown);
    }

    if (sub) sub.textContent =
      (counts.open || 0) + " open · " + (bsCounts.in_progress || 0) + " in progress · " +
      musa.length + " need" + (musa.length === 1 ? "s" : "") + " Musa";
  }

  function ownerGroups(rows) {
    var byOwner = {};
    var order = [];
    rows.forEach(function (a) {
      var owner = a.delegated_to || "— not yet delegated";
      if (!byOwner[owner]) { byOwner[owner] = []; order.push(owner); }
      byOwner[owner].push(a);
    });
    // 20+ owners, one (irsyad-coord) alone carrying 90 items on a real pull —
    // a flat render is a 40000px scroll, unusable on mobile. Collapsed-by-
    // default per-owner <details> (same native pattern as lanes.html's
    // collapsed-row-tap-to-open convention) so the board opens to 20 compact
    // headers, not 265 cards; tap any owner to see their actual queue.
    order.sort(function (a, b) { return byOwner[b].length - byOwner[a].length; });
    return order.map(function (owner) {
      var items = byOwner[owner];
      return '<details class="owngroup"><summary class="pin"><span class="owner">' + esc(owner) +
        '</span><span class="n">' + items.length + '</span></summary>' +
        '<div class="owngroup-body">' + items.map(askCard).join("") + '</div></details>';
    }).join("");
  }

  function load() {
    var sub = $("sub");
    if (sub && !sub.dataset.loaded) sub.textContent = "Loading ground-truth…";
    var ctrl = new AbortController();
    var to = setTimeout(function () { ctrl.abort(); }, 8000);
    return fetch("/api/asks-board", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) {
        clearTimeout(to);
        if (r.status === 401) { if (sub) sub.textContent = "Unauthorized — set a breakglass token in localStorage('console_token')."; return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (payload) {
        if (payload === null) return;
        render(payload);
        if (sub) sub.dataset.loaded = "1";
        var fresh = $("freshbar"), freshTxt = $("freshTxt");
        if (fresh) { fresh.style.display = "flex"; }
        if (freshTxt) freshTxt.textContent = "updated just now";
        var stamp = $("stamp");
        if (stamp) stamp.textContent = new Date().toLocaleTimeString();
      })
      .catch(function () {
        clearTimeout(to);
        if (sub) sub.textContent = "Network error — retrying…";
      });
  }

  $("refresh").addEventListener("click", load);
  load();
  setInterval(load, 15000);

  if (typeof module !== "undefined" && module.exports) {
    // P1 pure axes, exported for tests/console/asks_board.test.js (same
    // pattern as fleet.js). Render itself is DOM-verified, not unit-locked.
    module.exports = {
      TOPIC_ORDER: TOPIC_ORDER,
      splitByBoardStatus: splitByBoardStatus,
      topicCounts: topicCounts,
      needsMusa: needsMusa,
    };
  }
})();
