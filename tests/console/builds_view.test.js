// Node test (no deps) for the build-status view (op#34918/34919 — orch-console
// 78503 plan / 78505 GO): /builds page + /api/build-status. Same vm-sandbox
// idiom as status_board.test.js. Run:
//   node tests/console/builds_view.test.js
//
// LOCKS:
//  - tokenFromHash: identical semantics to asks/docs/media/status (fc-v82-85)
//    — the #t=<8+> one-tap deep link, floor = the REAL CONSOLE_TOKEN length
//    class (bus 76254), no percent-decode, persist-then-strip exactly once.
//  - esc(): 4-glyph (titles interpolate into double-quoted attrs).
//  - fmtHours: 5.1 -> "5.1h", 48.5 -> "2.0d", null -> "" (NEVER "0h" — a
//    missing time is not zero).
//  - HONESTY CONTRACT (the 78505 ruling): a not_measured ask paints with NO
//    time — no zero, no dash pretending to be a clock; it says why it has no
//    time yet. measured rows carry total_hours from REAL delivered_at stamps.
//  - render(): fetches /api/build-status with the bearer HEADER (never the
//    URL), paints programme cards with owner + phase chips classed
//    done|building|next|blocked (the only four VALID_STATUSES the update
//    helper accepts), last real movement line, and next check-in.
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
    closest() { return null; }, focus() {}, blur() {}, click() {},
    innerHTML: "", textContent: "",
    scrollTop: 0, scrollHeight: 0, offsetHeight: 0,
    getBoundingClientRect: function () { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
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
    getElementById: function (id) { return els[id] || makeEl(); },
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
const REAL = "w1ngmen8"; // 8 chars — the production token's length class (bus 76254)

// ---- helpers exported for tests --------------------------------------------
const api = loadPage("builds.js");
assert.ok(api, "builds.js must export module.exports for tests");
assert.ok(api.tokenFromHash, "builds.js must export tokenFromHash");

assert.strictEqual(api.tokenFromHash("#t=" + TOK), TOK, "reads a #t= fragment");
assert.strictEqual(api.tokenFromHash("#t=" + REAL), REAL, "accepts the real-length (8-char) token");
assert.strictEqual(api.tokenFromHash("#t=1234567"), null, "rejects a <8-char fragment");
assert.strictEqual(api.tokenFromHash("#x=" + TOK), null, "ignores non-token fragments");
assert.strictEqual(api.tokenFromHash(""), null, "no hash -> null");
assert.strictEqual(api.tokenFromHash("#t=" + TOK + "%23x"), TOK + "%23x", "no percent-decode");
assert.strictEqual(api.esc('"'), "&quot;", "esc escapes double quotes");
assert.strictEqual(api.esc("<a&b>"), "&lt;a&amp;b&gt;", "esc keeps the classic three");

// fmtHours: honest clock — hours under a day, days above, NEVER zero-fill
assert.strictEqual(api.fmtHours(null), "", "fmtHours(null) -> empty, not a fake 0");
assert.strictEqual(api.fmtHours(undefined), "", "fmtHours(undefined) -> empty");
assert.strictEqual(api.fmtHours(5.1), "5.1h", "sub-day stays hours");
assert.strictEqual(api.fmtHours(0.5), "0.5h", "half hour is still a real hour value");
assert.strictEqual(api.fmtHours(48.5), "2.0d", "day-scale reads as days");
// fmtDate: OPERATOR-FACING times render in Asia/Dubai (UTC+4, no DST), not
// UTC-as-if-local (orch-console 78520 note 2 — 09:14Z must read 13:14 for
// Musa). Deterministic +4 shift, not a locale call (node must agree).
assert.strictEqual(api.fmtDate("2026-10-27T09:14:32+00:00"), "27 Oct 13:14",
  "timestamp shifts to Dubai");
assert.strictEqual(api.fmtDate("2026-10-27T20:16:07Z"), "28 Oct 00:16",
  "UTC 20:16 crosses into the NEXT Dubai day at 00:16");
assert.strictEqual(api.fmtDate("2026-10-27T19:59:59Z"), "27 Oct 23:59",
  "one second before the Dubai day boundary");
assert.strictEqual(api.fmtDate(null), "", "fmtDate(null) -> empty");
// fmtDay: date-ONLY fields stay calendar dates (no clock to shift)
assert.strictEqual(api.fmtDay("2026-11-04"), "4 Nov", "date-only stays a date");
console.log("builds.js helpers: ok");

// ---- deep-link flow: persist BEFORE read, strip exactly once ----------------
const wrote = [];
const stripped = [];
loadPage("builds.js", {
  localStorage: {
    getItem: function (k) { return wrote.length && k === "console_token" ? wrote[wrote.length - 1][1] : null; },
    setItem: function (k, v) { wrote.push([k, v]); }, removeItem() {},
  },
  location: { hash: "#t=" + TOK, reload() {}, href: "/builds#t=" + TOK, pathname: "/builds", search: "" },
  history: { replaceState: function () { stripped.push(Array.prototype.slice.call(arguments)); } },
});
assert.strictEqual(JSON.stringify(wrote), JSON.stringify([["console_token", TOK]]),
  "valid #t= persists the token exactly once");
assert.strictEqual(stripped.length, 1, "fragment stripped exactly once");
const wroteShort = [];
loadPage("builds.js", {
  localStorage: { getItem() { return null; }, setItem: function (k, v) { wroteShort.push([k, v]); }, removeItem() {} },
  location: { hash: "#t=short", reload() {}, href: "", pathname: "/builds", search: "" },
});
assert.strictEqual(wroteShort.length, 0, "short/garbage fragment writes nothing");
console.log("tokenFromHash flow: ok");

// ---- render: bearer header, programme cards, HONEST times -------------------
(async function () {
  const payload = {
    programmes: [
      { id: 101, ask: 'Substrate 2.0 — one joint plan <b>"durable"</b>',
        status: "in_progress", owner: "cc-paperclip-spike-1",
        phases: [
          { name: "Durable inbox step 2", status: "done", date: "2026-10-27" },
          { name: "Substrate 2.0 fully operational", status: "next" },
        ],
        last_progress: { at: "2026-10-27T16:16:07Z", line: "PR#490 renumbered, CI green" },
        next_checkpoint: "2026-11-04", updated_age_s: 5 * 3600 },
      { id: 102, ask: "TG 2.0 — per-client channels",
        status: "in_progress", owner: "cc-bariq-1",
        phases: [
          { name: "Per-client channel design", status: "next" },
          { name: "Channel live", status: "blocked", date: "2026-11-09" },
        ],
        last_progress: { at: "2026-10-27T17:31:33Z", line: "Tenant-provisioner plan approved in shape" },
        next_checkpoint: "2026-11-04", updated_age_s: 45 },
    ],
    delivery_times: {
      measured: [
        { id: 5343, line: "Personal-issue field — change from your photo",
          asked_at: "2026-10-27T09:14:32+00:00", delivered_at: "2026-10-27T14:21:00+00:00",
          total_hours: 5.1, surface: "telegram" },
      ],
      not_measured: [
        { id: 5350, line: "Approve weeks 4+ for everyone to see",
          asked_at: "2026-10-27T08:00:00+00:00", surface: "telegram" },
      ],
      coverage: { measured: 1, not_measured: 1 },
    },
  };
  const fetches = [];
  const main = makeEl();
  loadPage("builds.js", {
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
  assert.ok(fetches[0][0].indexOf("/api/build-status") !== -1, "fetches the build-status endpoint");
  assert.strictEqual(fetches[0][1].headers.Authorization, "Bearer " + TOK,
    "bearer token rides the Authorization header, never the URL");
  assert.ok(String(fetches[0][0]).indexOf("token=") === -1, "no token in the query string");

  const html = main.innerHTML;
  // programme cards
  assert.ok(html.indexOf("&quot;durable&quot;") !== -1, "ask title quote is escaped in the paint");
  assert.ok(html.indexOf("cc-paperclip-spike-1") !== -1, "programme owner visible");
  assert.ok(html.indexOf("cc-bariq-1") !== -1, "second programme owner visible");
  assert.ok(html.indexOf("ph done") !== -1 && html.indexOf("ph building") === -1,
    "phase chips carry their status class");
  assert.ok(html.indexOf("ph blocked") !== -1, "blocked chip classed");
  assert.ok(html.indexOf("PR#490 renumbered, CI green") !== -1, "last real movement line visible");
  assert.ok(html.indexOf("4 Nov") !== -1, "next check-in visible (fmtDay form)");
  assert.ok(html.indexOf("5h ago") !== -1 && html.indexOf("just now") !== -1,
    "programme freshness (fmtAge) visible");

  // delivery times — the honesty contract
  // 78520 note 1: the FULL ask line must be present (no mid-title truncation)
  assert.ok(html.indexOf("Personal-issue field — change from your photo") !== -1,
    "measured ask line visible in full");
  // 78520 note 2: 09:14Z renders as 13:14 Dubai, and 14:21Z as 18:21
  assert.ok(html.indexOf("13:14") !== -1 && html.indexOf("18:21") !== -1,
    "delivery times render in Dubai (UTC+4)");
  assert.ok(html.indexOf("09:14") === -1 && html.indexOf("14:21") === -1,
    "no UTC-as-if-local times leak through");
  assert.ok(html.indexOf("5.1h") !== -1, "measured ask shows its real total");
  const nm = html.split("nm-row");
  assert.ok(nm.length >= 2, "a not-measured row exists (class nm-row)");
  assert.ok(nm[1].indexOf("Approve weeks 4+") !== -1, "not-measured row shows the ask");
  assert.ok(!/\d+(\.\d+)?h/.test(nm[1]), "not-measured row paints NO time value");
  assert.ok(nm[1].indexOf("no clock yet") !== -1,
    "not-measured row says WHY it has no time, not a zero");
  assert.ok(html.indexOf("unauthorized") === -1, "not an error paint");
  console.log("render flow: ok");

  // ---- op#35396: a run of >3 identical pending-summary lines collapses to
  // ONE row with a count, instead of repeating the same blank-looking line
  // dozens of times; 3 or fewer stay listed individually. ----
  var manyPending = [];
  for (var i = 0; i < 5; i++) {
    manyPending.push({ id: 6000 + i, pending: true,
      line: "summary pending from irsyad coordinator (irsyad)",
      asked_at: "2026-10-28T13:00:00Z", delivered_at: "2026-10-28T14:00:00Z",
      total_hours: 1.0, surface: "client-channel" });
  }
  var fewPending = [
    { id: 6100, pending: true, line: "summary pending from fleet health (infra)",
      asked_at: "2026-10-28T13:00:00Z", delivered_at: "2026-10-28T14:00:00Z",
      total_hours: 1.0, surface: "operator" },
    { id: 6101, pending: true, line: "summary pending from fleet health (infra)",
      asked_at: "2026-10-28T13:00:00Z", delivered_at: "2026-10-28T14:00:00Z",
      total_hours: 1.0, surface: "operator" },
  ];
  var groupPayload = {
    programmes: payload.programmes,
    delivery_times: {
      measured: manyPending.concat(fewPending).concat([
        { id: 6200, pending: false, line: "A real delivered summary",
          asked_at: "2026-10-28T13:00:00Z", delivered_at: "2026-10-28T14:00:00Z",
          total_hours: 1.0, surface: "operator" },
      ]),
      not_measured: [],
      coverage: { measured: 8, not_measured: 0 },
    },
  };
  const main2 = makeEl();
  const api2 = loadPage("builds.js", {
    els: { main: main2, token: Object.assign(makeEl(), { value: TOK }) },
    localStorage: { getItem() { return TOK; }, setItem() {}, removeItem() {} },
    fetch: function () { return new Promise(function () {}); },
  });
  assert.ok(api2.groupPending, "builds.js must export groupPending for the node test");
  api2.paint(groupPayload);
  const html2 = main2.innerHTML;
  assert.ok(html2.indexOf("A real delivered summary") !== -1, "a real summary row is never collapsed");
  assert.ok(html2.indexOf("fleet health (infra)") !== -1,
    "3-or-fewer identical pending lines stay listed individually");
  assert.strictEqual((html2.match(/fleet health \(infra\)/g) || []).length, 2,
    "exactly 2 individual rows for the 2-item fleet-health group, not collapsed");
  assert.ok(html2.indexOf("irsyad coordinator (irsyad)") !== -1, "the collapsed group's line is still shown");
  assert.ok(html2.indexOf("×5") !== -1 || html2.indexOf(">5<") !== -1 || /\b5\b/.test(html2),
    "the collapsed group shows its count (5)");
  assert.strictEqual((html2.match(/irsyad coordinator \(irsyad\)/g) || []).length, 1,
    ">3 identical pending lines collapse to exactly ONE row");
  console.log("pending-summary grouping: ok");

  // groupPending as a pure function, directly
  const grouped = api2.groupPending(manyPending.concat(fewPending));
  assert.strictEqual(grouped.length, 3, "5-item group collapses to 1 row + the 2-item group's 2 individual rows");
  const collapsedRow = grouped.filter(function (r) { return r.collapsed; })[0];
  assert.ok(collapsedRow, "exactly one collapsed row produced");
  assert.strictEqual(collapsedRow.count, 5, "collapsed row carries the real count");
  assert.strictEqual(collapsedRow.line, "summary pending from irsyad coordinator (irsyad)");
  console.log("groupPending helper: ok");

  // cc-quality #80511 nit 2: pin the exact >3 boundary (a regression flipping
  // > to >= would collapse a 3-item group and escape both the 2-item and
  // 5-item cases above).
  function pendingRow(id, line) {
    return { id: id, pending: true, line: line,
      asked_at: "2026-10-28T13:00:00Z", delivered_at: "2026-10-28T14:00:00Z",
      total_hours: 1.0, surface: "client-channel" };
  }
  const exactlyThree = [
    pendingRow(7000, "summary pending from bariq (other)"),
    pendingRow(7001, "summary pending from bariq (other)"),
    pendingRow(7002, "summary pending from bariq (other)"),
  ];
  const groupedThree = api2.groupPending(exactlyThree);
  assert.strictEqual(groupedThree.length, 3, "exactly 3 identical pending rows stay listed individually");
  assert.ok(!groupedThree.some(function (r) { return r.collapsed; }), "3 is not > 3 -- no collapse");
  const exactlyFour = exactlyThree.concat([pendingRow(7003, "summary pending from bariq (other)")]);
  const groupedFour = api2.groupPending(exactlyFour);
  assert.strictEqual(groupedFour.length, 1, "the 4th identical pending row tips the group into collapsing");
  assert.strictEqual(groupedFour[0].collapsed, true);
  assert.strictEqual(groupedFour[0].count, 4, "collapsed count is exactly 4 at the boundary");
  console.log("collapse boundary (3 vs 4): ok");

  // ---- op#35502/35503: Ask→live times grouped by repo, expandable sections,
  // collapsed by default except the one holding the most recent ship ----
  var byRepoPayload = {
    programmes: payload.programmes,
    delivery_times: {
      measured: [
        { id: 8000, repo: "irsyad", line: "irsyad real summary",
          asked_at: "2026-10-26T10:00:00Z", delivered_at: "2026-10-28T13:00:00Z",
          total_hours: 2.0, surface: "client-channel" },
      ],
      not_measured: [
        { id: 8001, repo: "adcda", line: "adcda open ask",
          asked_at: "2026-10-28T09:00:00Z", surface: "client-channel" },
      ],
      coverage: { measured: 1, not_measured: 1, unattributed: 1 },
      by_repo: [
        {
          repo: "irsyad", label: "irsyad", count: 1, measured_count: 1,
          median_hours: 2.0, latest_delivered_at: "2026-10-28T13:00:00Z",
          measured: [
            { id: 8000, repo: "irsyad", line: "irsyad real summary",
              asked_at: "2026-10-26T10:00:00Z", delivered_at: "2026-10-28T13:00:00Z",
              total_hours: 2.0, surface: "client-channel" },
          ],
          not_measured: [],
        },
        {
          repo: "adcda", label: "adcda", count: 1, measured_count: 0,
          median_hours: null, latest_delivered_at: null,
          measured: [],
          not_measured: [
            { id: 8001, repo: "adcda", line: "adcda open ask",
              asked_at: "2026-10-28T09:00:00Z", surface: "client-channel" },
          ],
        },
        {
          repo: "unattributed", label: "no project linked", count: 1, measured_count: 0,
          median_hours: null, latest_delivered_at: null,
          measured: [],
          not_measured: [
            { id: 8002, repo: null, line: "summary pending from no owner assigned (other)",
              asked_at: "2026-10-28T08:00:00Z", surface: "operator" },
          ],
        },
      ],
    },
  };
  const main3 = makeEl();
  const api3 = loadPage("builds.js", {
    els: { main: main3, token: Object.assign(makeEl(), { value: TOK }) },
    localStorage: { getItem() { return TOK; }, setItem() {}, removeItem() {} },
    fetch: function () { return new Promise(function () {}); },
  });
  api3.paint(byRepoPayload);
  const html3 = main3.innerHTML;
  assert.ok(html3.indexOf('<details class="repo-group" open>') !== -1,
    "the first (most-recent-ship) group starts open");
  assert.strictEqual((html3.match(/<details class="repo-group" open>/g) || []).length, 1,
    "exactly ONE group is expanded by default");
  assert.ok(html3.indexOf(">irsyad<") !== -1, "irsyad group header visible");
  assert.ok(html3.indexOf(">adcda<") !== -1, "adcda group header visible");
  assert.ok(html3.indexOf(">no project linked<") !== -1,
    "orch-console 81075: unattributed group shows the operator-facing label, not the internal key");
  assert.ok(html3.indexOf(">unattributed<") === -1, "the internal jargon label never reaches the page");
  assert.ok(html3.indexOf("2h median") !== -1, "repo header shows the median time");
  assert.ok(html3.indexOf("irsyad real summary") !== -1, "measured row rendered inside its repo group");
  assert.ok(html3.indexOf("adcda open ask") !== -1, "not-measured row rendered inside its repo group");
  assert.ok(html3.indexOf("no clock yet") !== -1, "honesty contract holds inside a repo group too");
  // orch-console 81075: hero stays to the two headline figures; the
  // unattributed count is never rendered there (it has its own group).
  assert.ok(html3.indexOf("no repo on the ask") === -1, "the jargon hero clause is gone");
  assert.ok(html3.indexOf(">1</span> measured") !== -1 && html3.indexOf("not yet measured") !== -1,
    "hero still shows the two headline figures");
  // a clearly-visible chevron, aria-expanded on every summary, matching the
  // group's actual open/closed state.
  assert.strictEqual((html3.match(/class="chev"/g) || []).length, 3, "every group has a chevron");
  assert.ok(html3.indexOf('aria-expanded="true"') !== -1, "the open group is marked expanded");
  assert.strictEqual((html3.match(/aria-expanded="false"/g) || []).length, 2,
    "the two collapsed groups are marked not expanded");
  console.log("by_repo grouping: ok");

  // fallback: an older/cached server response with no by_repo still renders
  // the flat measured/not_measured lists (no regression for an in-flight
  // client/server version mismatch).
  const main4 = makeEl();
  const api4 = loadPage("builds.js", {
    els: { main: main4, token: Object.assign(makeEl(), { value: TOK }) },
    localStorage: { getItem() { return TOK; }, setItem() {}, removeItem() {} },
    fetch: function () { return new Promise(function () {}); },
  });
  api4.paint(payload); // the original fixture above has no by_repo
  const html4 = main4.innerHTML;
  assert.ok(html4.indexOf("Personal-issue field") !== -1, "flat fallback still renders measured rows");
  assert.ok(html4.indexOf("repo-group") === -1, "no repo-group markup when by_repo is absent");
  console.log("by_repo fallback: ok");

  console.log("\nall passed");
})().catch(function (e) { console.error(e.message); process.exit(1); });
