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
    return '<div class="ameta">' + parts.join('<span class="adot">·</span>') + '</div>';
  }
  function askCard(a) {
    var t = ASK_TAG[a.status] || ASK_TAG.pending;
    var needs = a.status === "needs_you" || a.status === "waiting_on_musa";
    var surf = SURF_LABEL[a.ask_surface] || a.ask_surface;
    return '<div class="ask' + (needs ? " needs" : "") + '" data-ask="' + a.id + '">' +
      '<span class="atag ' + t.cls + '">' + t.label + '</span>' +
      '<div class="abody"><div class="atxt">' + esc(a.text) + '</div>' + askMeta(a) + '</div>' +
      '<span class="surf">' + esc(surf) + '</span>' +
    '</div>';
  }

  function render(rows) {
    var heroEl = $("hero"), groupsEl = $("groups"), sub = $("sub");
    if (!rows) {
      groupsEl.innerHTML = '<div class="err">Could not load — check the console connection.</div>';
      return;
    }
    if (!rows.length) {
      heroEl.innerHTML = "";
      groupsEl.innerHTML = '<div class="empty">No open asks. ✨</div>';
      if (sub) sub.textContent = "0 open";
      return;
    }
    var waiting = rows.filter(function (a) { return a.wait_kind === "external_wait"; });
    var buildTime = rows.filter(function (a) { return a.wait_kind !== "external_wait"; });

    heroEl.innerHTML = waiting.length
      ? '<div class="hero"><div class="hero-h">Waiting on Musa <span class="n">' + waiting.length + '</span></div>' +
          waiting.map(askCard).join("") + '</div>'
      : "";

    var byOwner = {};
    var order = [];
    buildTime.forEach(function (a) {
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
    groupsEl.innerHTML = order.map(function (owner) {
      var items = byOwner[owner];
      return '<details class="owngroup"><summary class="pin"><span class="owner">' + esc(owner) +
        '</span><span class="n">' + items.length + '</span></summary>' +
        '<div class="owngroup-body">' + items.map(askCard).join("") + '</div></details>';
    }).join("");

    if (sub) sub.textContent = rows.length + " open · " + waiting.length + " waiting on Musa · " + order.length + " owner" + (order.length === 1 ? "" : "s");
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
      .then(function (rows) {
        if (rows === null) return;
        render(rows);
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
})();
