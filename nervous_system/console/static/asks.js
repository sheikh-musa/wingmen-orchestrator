// asks.js — fleet-wide SSOT asks board (op#61107). Read-only; polls
// /api/asks-board (db.build_fleet_asks_query — every open 'ask' row, every
// surface, status derived live). Vanilla JS, bearer auth from localStorage
// (same pattern as lanes.js/fleet.js). The ONLY new rendering idea here is the
// grouping (hero "waiting on Musa" + per-owner sections) — the card markup
// itself is fleet.js's existing .ask/.atag/.ameta, reused verbatim so a reader
// already familiar with "Your asks" on /fleet recognizes this instantly.
(function () {
  "use strict";
  // fc-v82/fc-v83 (orch-console bus 76226/76254): one-tap setup. The token may
  // arrive in a FRAGMENT (#t=<token>) — fragments never leave the browser (no
  // server logs, no link-preview fetches, no referrers), which is why the deep
  // link is fragment-only and never a query param. Applied BEFORE the token is
  // read, then stripped via history.replaceState so it doesn't linger in the
  // URL bar or history entry. Floor is 8 chars = this console's REAL
  // CONSOLE_TOKEN length class (fc-v82 shipped 24 — copied from the hosted
  // console's SEPARATE bearer — and rejected the production token outright;
  // bus 76254).
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

  // fc-v82 typed fallback (orch-console bus 76226): if there is no token (or
  // the one on record is refused), show a paste-once strip instead of telling
  // the operator to go use devtools — iOS has none. Same shape as lanes.html's
  // "paste OAuth token (written once)" convention; NEVER window.prompt (iOS
  // suppresses it in a standalone PWA — see asks.html's own typed-confirm
  // note at its action layer).
  function unlockHtml(msg) {
    return '<div class="unlock">' +
      '<div class="uh">' + esc(msg || "This board needs its access token — paste it once and this device remembers it.") + '</div>' +
      '<div class="tknrow">' +
        '<input id="tkn" type="password" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="console token" />' +
        '<button id="tknGo" class="tkngo" type="button">save</button>' +
      '</div></div>';
  }
  function showUnlock(msg) {
    var sub = $("sub"), groups = $("groups");
    if (sub) sub.textContent = "Token required.";
    if (groups) { groups.innerHTML = unlockHtml(msg); bindUnlock(); }
  }
  function bindUnlock() {
    var go = $("tknGo"), inp = $("tkn");
    if (!go || !inp) return;
    function save() {
      var v = String(inp.value || "").trim();
      if (v.length < 8) { inp.classList.add("bad"); return; }  // same floor tokenFromHash enforces (real token length class, bus 76254)
      try { localStorage.setItem("console_token", v); } catch (e) {}
      location.reload();
    }
    go.addEventListener("click", save);
    inp.addEventListener("keydown", function (ev) { if (ev && ev.key === "Enter") save(); });
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
    // fc-v86 (op#34852): home's "Your asks" card (and its swipe-to-confirm) is
    // GONE — this board is the ONE asks view, so the operator's authoritative
    // confirm lives HERE now: a "review" (delegate_done) row carries confirm +
    // dismiss controls (POST /api/ask-close, same rail the home card used).
    var rev = a.status === "delegate_done"
      ? '<div class="arev">'
        +  '<button type="button" class="arevbtn" data-confirm="' + a.id + '">✓ landed</button>'
        +  '<button type="button" class="arevbtn drop" data-drop="' + a.id + '">dismiss</button>'
        +  '<span class="arevnote">delegate reported done — confirm it landed</span>'
        + '</div>'
      : "";
    return '<div class="ask' + (needs ? " needs" : "") + '" data-ask="' + a.id + '">' +
      '<span class="atag ' + t.cls + '">' + t.label + '</span>' +
      '<div class="abody"><div class="atxt">' + esc(a.text) + '</div>' + rev + askMeta(a) + '</div>' +
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

  function heroCard(a) {
    // Needs-Musa hero card (orch-console bus #75387): the ONE Musa-facing
    // surface on this page. Decision-first operator_summary (migration 118),
    // never the fleet-internal triage text, and NO lane code-names — so no
    // delegated_to, no status tag. Meta is dates only (asked/due/chase), the
    // parts Musa can act on. Other tabs keep askCard's internal detail.
    var line = a.operator_summary || a.text;
    var parts = ['<span class="aage">asked ' + esc(fmtAge(a.asked_age_s)) + ' ago</span>'];
    var due = a.committed_date ? "due " + fmtDate(a.committed_date) : "";
    var chase = a.chase_by ? "decide by " + fmtDate(a.chase_by) : "";
    var bits = [due, chase].filter(Boolean).join(" · ");
    if (bits) parts.push('<span>' + esc(bits) + '</span>');
    return '<div class="ask needs" data-ask="' + a.id + '">' +
      '<div class="abody"><div class="atxt">' + esc(line) + '</div>' +
        '<div class="ameta">' + parts.join('<span class="adot">·</span>') + '</div></div>' +
      '<span class="surf musa">needs you</span>' +
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
          musa.map(heroCard).join("") + '</div>'
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
    if (!token) { showUnlock(); return Promise.resolve(); }  // fc-v82: paste-once strip instead of a 401 round-trip
    if (sub && !sub.dataset.loaded) sub.textContent = "Loading ground-truth…";
    var ctrl = new AbortController();
    var to = setTimeout(function () { ctrl.abort(); }, 8000);
    return fetch("/api/asks-board", { headers: authHeaders(), signal: ctrl.signal })
      .then(function (r) {
        clearTimeout(to);
        if (r.status === 401) { showUnlock("That token was refused — paste the current console token."); return null; }
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

  // fc-v86 (op#34852): the operator's confirm/dismiss on "review" rows —
  // ported from the removed home card's swipe. POST /api/ask-close (same
  // token-gated rail the home card used); best-effort optimistic disable,
  // the next 15s poll is the source of truth.
  var askBusy = {};
  function closeAsk(id, action) {
    if (!id || askBusy[id]) return;
    askBusy[id] = true;
    var btn = document.querySelector('[data-confirm="' + id + '"], [data-drop="' + id + '"]');
    if (btn) btn.disabled = true;
    fetch("/api/ask-close", {
      method: "POST",
      headers: Object.assign({ "Content-Type": "application/json" }, authHeaders()),
      body: JSON.stringify({ id: Number(id), action: action })
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j || {} }; }, function () { return { ok: r.ok, j: {} }; }); })
      .then(function (res) {
        if (res.ok && res.j.ok) { load(); }
        else {
          if (btn) btn.disabled = false;
          if (window.sub) window.sub.textContent = res.j.error || "close failed";
        }
      })
      .catch(function () {
        if (btn) btn.disabled = false;
        if (window.sub) window.sub.textContent = "network dropped — close may not have run";
      })
      .finally(function () { askBusy[id] = false; });
  }
  $("groups").addEventListener("click", function (e) {
    var t = e.target.closest ? e.target.closest("[data-confirm],[data-drop]") : null;
    if (!t) return;
    if (t.hasAttribute("data-confirm")) closeAsk(t.getAttribute("data-confirm"), "confirm");
    else closeAsk(t.getAttribute("data-drop"), "drop");
  });
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
      heroCard: heroCard,
      askCard: askCard,
      tokenFromHash: tokenFromHash,
      unlockHtml: unlockHtml,
    };
  }
})();
