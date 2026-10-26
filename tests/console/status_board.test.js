// Node test (no deps) for the "nothing goes dark" status view (bus 77019,
// Phase 1 — /status page + /api/status-board). Same vm-sandbox idiom as
// docs_media.test.js / asks_board.test.js. Run:
//   node tests/console/status_board.test.js
//
// LOCKS:
//  - tokenFromHash: identical semantics to asks/docs/media (fc-v82/83/84) —
//    the #t=<8+> one-tap deep link, floor = the REAL CONSOLE_TOKEN length
//    class (bus 76254), no percent-decode, persist-then-strip exactly once.
//  - esc(): 4-glyph (status interpolates titles into double-quoted attrs).
//  - fmtAge / partyLabel / overdueLabel: the pure render helpers the page
//    shares with the server derivation (tests/console/test_status_board.py).
//  - render(): fetches /api/status-board with the bearer HEADER (never the
//    URL), paints items darkest-first as the server ordered them, escapes
//    titles, and shows each item's waiting party + overdue pill.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const STATIC = path.join(__dirname, "..", "..", "nervous_system", "console", "static");

function makeEl() {
  return {
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: {}, dataset: {}, value: "",
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    appendChild() {}, removeChild() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: "", textContent: "",
  };
}

function loadPage(file, opts) {
  opts = opts || {};
  const store = opts.localStorage ||
    { getItem() { return null; }, setItem() {}, removeItem() {} };
  const sess = opts.sessionStorage ||
    { getItem() { return null; }, setItem() {}, removeItem() {} };
  const loc = Object.assign(
    { reload() {}, href: "", pathname: "/", hash: "", search: "" }, opts.location || {});
  const els = opts.els || {};
  const documentStub = {
    getElementById(id) { return els[id] || makeEl(); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); }, addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const sandbox = {
    window: { addEventListener() {}, removeEventListener() {}, location: loc },
    document: documentStub,
    localStorage: store, sessionStorage: sess,
    location: loc, history: opts.history || { replaceState() {} },
    fetch: opts.fetch || function () { return new Promise(function () {}); },
    setInterval: opts.setInterval || function () { return 0; },
    clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    IntersectionObserver: function () { this.observe = function () {}; this.unobserve = function () {}; this.disconnect = function () {}; },
    URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} },
    console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, file), "utf8"), sandbox);
  return sandbox.module.exports;
}

const TOK = "wingmen-console-token-0123456789abcdef";
const REAL = "w1ngmen8";  // 8 chars — the production token's length class (bus 76254)

// ---- helpers exported for tests --------------------------------------------
const api = loadPage("status.js");
assert.ok(api, "status.js must export module.exports for tests");
assert.ok(api.tokenFromHash, "status.js must export tokenFromHash");

assert.strictEqual(api.tokenFromHash("#t=" + TOK), TOK, "reads a #t= fragment");
assert.strictEqual(api.tokenFromHash("#t=" + REAL), REAL, "accepts the real-length (8-char) token");
assert.strictEqual(api.tokenFromHash("#t=1234567"), null, "rejects a <8-char fragment");
assert.strictEqual(api.tokenFromHash("#x=" + TOK), null, "ignores non-token fragments");
assert.strictEqual(api.tokenFromHash(""), null, "no hash -> null");
assert.strictEqual(api.tokenFromHash("#t=" + TOK + "%23x"), TOK + "%23x", "no percent-decode");
assert.strictEqual(api.esc('"'), "&quot;", "esc escapes double quotes");
assert.strictEqual(api.esc("<a&b>"), "&lt;a&amp;b&gt;", "esc keeps the classic three");

// fmtAge: coarse human silence clock (matches the server's age seconds)
assert.strictEqual(api.fmtAge(null), "", "fmtAge(null) -> empty");
assert.strictEqual(api.fmtAge(undefined), "", "fmtAge(undefined) -> empty");
assert.strictEqual(api.fmtAge(30), "just now", "fmtAge(<60s) -> just now");
assert.strictEqual(api.fmtAge(5 * 60), "5m ago", "fmtAge minutes");
assert.strictEqual(api.fmtAge(3 * 3600 + 59 * 60), "3h ago", "fmtAge hours (floored)");
assert.strictEqual(api.fmtAge(2 * 86400 + 5 * 3600), "2d ago", "fmtAge days (floored)");

// partyLabel: who the item owes its next update to
assert.strictEqual(api.partyLabel({ waiting_party: "operator" }), "operator",
  "operator party");
assert.strictEqual(api.partyLabel({ waiting_party: "client", client_name: "gazzabyte-irsyad" }),
  "gazzabyte-irsyad", "client party carries the channel name");
assert.strictEqual(api.partyLabel({ waiting_party: "client", client_name: null }),
  "client", "client party without a name still names the class");

// overdueLabel: the dark pill
assert.strictEqual(api.overdueLabel({ overdue: true, overdue_reason: "fired" }), "BREACH",
  "fired -> BREACH");
assert.strictEqual(api.overdueLabel({ overdue: true, overdue_reason: "past_due" }), "PAST DUE",
  "past_due -> PAST DUE");
assert.strictEqual(api.overdueLabel({ overdue: true, overdue_reason: "quiet" }), "QUIET",
  "quiet -> QUIET");
assert.strictEqual(api.overdueLabel({ overdue: false, overdue_reason: null }), "",
  "healthy -> no pill");
console.log("status.js helpers: ok");

// ---- deep-link flow: persist BEFORE read, strip exactly once ----------------
const wrote = [];
const stripped = [];
loadPage("status.js", {
  localStorage: {
    getItem(k) { return wrote.length && k === "console_token" ? wrote[wrote.length - 1][1] : null; },
    setItem(k, v) { wrote.push([k, v]); }, removeItem() {},
  },
  location: { hash: "#t=" + TOK, reload() {}, href: "/status#t=" + TOK, pathname: "/status", search: "" },
  history: { replaceState() { stripped.push(arguments); } },
});
assert.strictEqual(JSON.stringify(wrote), JSON.stringify([["console_token", TOK]]),
  "valid #t= persists the token exactly once");
assert.strictEqual(stripped.length, 1, "fragment stripped exactly once");
const wroteShort = [];
loadPage("status.js", {
  localStorage: { getItem() { return null; }, setItem(k, v) { wroteShort.push([k, v]); }, removeItem() {} },
  location: { hash: "#t=short", reload() {}, href: "", pathname: "/status", search: "" },
});
assert.strictEqual(wroteShort.length, 0, "short/garbage fragment writes nothing");
console.log("tokenFromHash flow: ok");

// ---- render: bearer header, darkest-first paint, escaping -------------------
(async function () {
  const payload = {
    items: [
      { kind: "ask", id: 11, title: 'Silent client ask "quoted" <b>',
        owner: "cc-irsyad", status: "in_progress",
        waiting_party: "client", client_name: "gazzabyte-irsyad",
        due_at: null, last_update_at: "2026-10-26T00:00:00+00:00",
        last_update_age_s: 13 * 3600, overdue: true, overdue_reason: "quiet",
        source: "operator_asks#11" },
      { kind: "commitment", id: 637, title: "Monthly billing sweep",
        owner: "cc-irsyad-coord", status: "fired",
        waiting_party: "operator", client_name: null,
        due_at: "2026-10-25T00:00:00+00:00", last_update_at: "2026-10-26T11:50:00+00:00",
        last_update_age_s: 600, overdue: true, overdue_reason: "fired",
        source: "held_commitments#637" },
    ],
    counts: { total: 2, overdue: 2, by_party: { operator: 1, client: 1 },
              by_kind: { ask: 1, commitment: 1 } },
  };
  const fetches = [];
  const main = makeEl();
  loadPage("status.js", {
    els: { main: main, token: Object.assign(makeEl(), { value: TOK }) },
    localStorage: { getItem() { return TOK; }, setItem() {}, removeItem() {} },
    fetch: function (url, init) {
      fetches.push([url, init]);
      return Promise.resolve({ ok: true, status: 200, json: function () { return Promise.resolve(payload); } });
    },
  });
  await new Promise(function (r) { setImmediate(r); });
  await new Promise(function (r) { setImmediate(r); });
  assert.strictEqual(fetches.length, 1, "exactly one fetch on load");
  assert.ok(fetches[0][0].indexOf("/api/status-board") !== -1, "fetches the board endpoint");
  assert.strictEqual(fetches[0][1].headers.Authorization, "Bearer " + TOK,
    "bearer token rides the Authorization header, never the URL");
  assert.ok(fetches[0][0].indexOf("token=") === -1, "no token in the query string");
  const html = main.innerHTML;
  assert.ok(html.indexOf("&quot;quoted&quot;") !== -1, "title quote is escaped in the paint");
  assert.ok(html.indexOf("gazzabyte-irsyad") !== -1, "client party name visible");
  assert.ok(html.indexOf("BREACH") !== -1, "fired item shows the BREACH pill");
  assert.ok(html.indexOf("QUIET") !== -1, "quiet item shows the QUIET pill");
  assert.ok(html.indexOf("operator_asks#11") !== -1, "ask provenance visible");
  assert.ok(html.indexOf("held_commitments#637") !== -1, "commitment provenance visible");
  assert.ok(html.indexOf("Monthly billing sweep") !== -1, "commitment title visible");
  assert.ok(html.indexOf("unauthorized") === -1, "not an error paint");
  console.log("render flow: ok");

  // 401 paints the unauthorized state, never a raw error
  const main401 = makeEl();
  loadPage("status.js", {
    els: { main: main401, token: Object.assign(makeEl(), { value: TOK }) },
    localStorage: { getItem() { return TOK; }, setItem() {}, removeItem() {} },
    fetch: function () {
      return Promise.resolve({ ok: false, status: 401, json: function () { return Promise.resolve({}); } });
    },
  });
  await new Promise(function (r) { setImmediate(r); });
  await new Promise(function (r) { setImmediate(r); });
  assert.ok(main401.innerHTML.indexOf("Unauthorized") !== -1, "401 paints Unauthorized");

  // NO token: nothing fetches, the enter-token prompt stays (the 60s
  // auto-refresh must not fire load() tokenless and overwrite the prompt
  // with an Unauthorized paint — the operator hasn't typed anything yet).
  const mainNoTok = makeEl();
  const fetches0 = [];
  const timers = [];
  loadPage("status.js", {
    els: { main: mainNoTok, token: makeEl() },
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    fetch: function (u) { fetches0.push(u); return new Promise(function () {}); },
    setInterval: function (fn, ms) { timers.push([fn, ms]); return 1; },
  });
  await new Promise(function (r) { setImmediate(r); });
  assert.strictEqual(fetches0.length, 0, "no fetch fires without a token");
  assert.ok(mainNoTok.innerHTML.indexOf("operator token") !== -1,
    "the enter-token prompt stays painted");
  assert.ok(timers.length >= 1, "auto-refresh timer armed");
  timers.forEach(function (t) { t[0](); });  // fire the interval manually
  assert.strictEqual(fetches0.length, 0, "interval tick without a token still fetches nothing");
  console.log("401 + tokenless flows: ok");

  console.log("status_board.test.js OK");
})().catch(function (e) { console.error(e); process.exit(1); });
