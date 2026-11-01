// "Nothing goes dark" status view (bus 77019, Phase 1) — read-only live view
// of every OPEN item across the two anti-drop ledgers (open operator_asks +
// open held_commitments), each tagged with WHO it owes its next update to and
// whether it has gone dark. The server derives those axes per read
// (/api/status-board); this page just paints them, darkest first, and
// re-fetches every 60s so the view stays current while it is open.
// Auth: operator token sent ONLY in the Authorization header (never the URL),
// same localStorage key as the rest of the console, so the operator enters it
// ONCE per device (asks/docs/media pattern).
(function () {
  "use strict";

  // fc-v85 (bus 77019): the token may arrive as a #t=<token> FRAGMENT — same
  // one-tap deep link as asks/docs/media (fc-v82/83/84): fragments never
  // leave the browser (no server logs, no link-preview fetches, no
  // referrers), persisted BEFORE the token is read, then history.replaceState
  // strips it from the URL bar/history. Floor is 8 chars = the REAL
  // CONSOLE_TOKEN length class (bus 76254).
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

  // ---- pure render helpers (unit-locked in tests/console/status_board.test.js;
  // the party/overdue SEMANTICS are server-side, tests/console/test_status_board.py) ----
  function fmtAge(s) {
    if (s == null) return "";
    s = Number(s);
    if (isNaN(s)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    return Math.floor(s / 86400) + "d ago";
  }
  function partyLabel(item) {
    if (item && item.waiting_party === "client")
      return item.client_name || "client";
    return "operator";
  }
  function overdueLabel(item) {
    if (!item || !item.overdue) return "";
    if (item.overdue_reason === "fired") return "BREACH";
    if (item.overdue_reason === "past_due") return "PAST DUE";
    return "QUIET";
  }

  function unauthorized() {
    $("main").innerHTML = '<div class="empty">Unauthorized — check the operator token and Connect.</div>';
  }

  function itemCard(it) {
    var pill = overdueLabel(it);
    var dark = it.overdue ? " dark" : "";
    var breach = (it.overdue && it.overdue_reason === "fired") ? " breach" : "";
    var search = esc(((it.title || "") + " " + (it.owner || "") + " " +
      partyLabel(it) + " " + (it.source || "")).toLowerCase());
    return '<div class="item' + dark + breach + '" data-search="' + search + '">' +
      '<div class="t"><span class="ttl">' + esc(it.title) + '</span>' +
      (pill ? '<span class="pill' + breach + '">' + pill + '</span>' : '') +
      '</div>' +
      '<div class="meta">' +
        '<span class="kind">' + esc(it.kind) + '</span>' +
        '<span class="owner">' + esc(it.owner) + '</span>' +
        '<span class="status">' + esc(it.status) + '</span>' +
        '<span class="party">owes: ' + esc(partyLabel(it)) + '</span>' +
        '<span class="age">last update ' + esc(fmtAge(it.last_update_age_s)) + '</span>' +
        '<span class="src">' + esc(it.source) + '</span>' +
      '</div>' +
    '</div>';
  }

  // console #90323: this page painted every open item flat. With 600 open items
  // that measured 100,413 css px — 301,630 device px on an iPhone 13, i.e. ~119
  // screens. The board is already sorted darkest-first (annotate_status_rows), so
  // the honest cap is: paint the first STATUS_MAX_RENDERED of each section and
  // fold the remainder behind ONE toggle that carries its count. Nothing is
  // dropped — the rows stay in the DOM, one tap away, and the hero keeps the
  // true totals so a fold can never make the board look smaller than it is.
  var STATUS_MAX_RENDERED = 12;

  function cappedCards(cards, max) {
    var n = (max == null ? STATUS_MAX_RENDERED : max);
    var shown = cards.slice(0, n), rest = cards.slice(n);
    var out = shown.join("");
    if (rest.length) {
      var many = rest.length === 1 ? "more open item" : "more open items";
      out += '<details class="fold"><summary aria-expanded="false">' +
        esc(rest.length + " " + many + " — show") + "</summary>" + rest.join("") + "</details>";
    }
    return out;
  }

  function paint(body) {
    var main = $("main");
    var items = (body && body.items) || [];
    var counts = (body && body.counts) || {};
    var dark = items.filter(function (it) { return it.overdue; });
    var live = items.filter(function (it) { return !it.overdue; });

    var hero = '<div class="hero">' +
      '<span class="big">' + (counts.total != null ? counts.total : items.length) + '</span> open' +
      '<span class="sep">·</span>' +
      '<span class="warn">' + (counts.overdue != null ? counts.overdue : dark.length) + ' dark</span>' +
      '<span class="sep">·</span>' +
      '<span>' + ((counts.by_party && counts.by_party.operator) || 0) + ' operator</span>' +
      '<span class="sep">·</span>' +
      '<span>' + ((counts.by_party && counts.by_party.client) || 0) + ' client</span>' +
    '</div>';

    var html = '<div class="repo-head" style="position:static">' +
      '<input id="filter" class="search" placeholder="filter ' + items.length + ' items…" autocapitalize="off" autocorrect="off" spellcheck="false" style="margin-top:6px" /></div>';
    html += hero;
    if (!items.length) {
      html += '<div class="empty">No open items — nothing is waiting on anyone.</div>';
    } else {
      if (dark.length)
        html += '<div class="section">Gone dark · ' + esc(String(dark.length)) + '</div>' +
          cappedCards(dark.map(itemCard));
      if (live.length)
        html += '<div class="section">Watching · ' + esc(String(live.length)) + '</div>' +
          cappedCards(live.map(itemCard));
    }
    main.innerHTML = html;

    // live client-side filter (hides whole cards, like docs/media)
    var f = $("filter");
    if (f) f.addEventListener("input", function () {
      var q = f.value.trim().toLowerCase();
      main.querySelectorAll(".item").forEach(function (a) {
        a.style.display = (!q || a.getAttribute("data-search").indexOf(q) !== -1) ? "" : "none";
      });
    });
  }

  function load() {
    if (!token) return;  // tokenless: keep the enter-token prompt; the 60s
                         // refresh must never overwrite it with a 401 paint
    $("main").innerHTML = '<div class="empty">Loading the open-item board…</div>';
    fetch("/api/status-board", { headers: authHeaders() })
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

  // "always-current" while the tab is open: the operator's phone keeps this
  // view pinned; a static snapshot would go dark itself.
  if (typeof setInterval !== "undefined") setInterval(load, 60000);

  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      tokenFromHash: tokenFromHash, esc: esc, fmtAge: fmtAge,
      partyLabel: partyLabel, overdueLabel: overdueLabel,
      cappedCards: cappedCards, STATUS_MAX_RENDERED: STATUS_MAX_RENDERED,
    };
  }
})();
