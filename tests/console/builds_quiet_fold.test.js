// Node test (no deps) for op#37222 item 6 (orch-console #90041): the /builds page
// ran ~20,000 css px on a phone once repo groups were opened (measured 19,061
// with every <details> open vs 6,581 collapsed, iPhone 13). Finished programmes
// and repos with no live-verified ship in 7+ days now fold behind ONE toggle per
// section. Run: `node tests/console/builds_quiet_fold.test.js`
//
// LOCKS:
//  - nothing is deleted: every programme/ship time still reaches the page, just
//    inside a collapsed <details>, and the toggle carries its count;
//  - "finished" means EVERY phase is done — one open phase keeps a programme on
//    the page, so a nearly-done programme cannot quietly disappear;
//  - "untouched" is NOT last-render: it is the group's own latest_delivered_at
//    (a live-verified ship the server measured). A payload the client re-renders
//    every 60s must not read as fresh because it re-rendered;
//  - a repo that has never shipped is folded (and named as such), never shown as
//    if it shipped today;
//  - the default-open group is one that survived the fold.
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

function loadPage() {
  const sandbox = {
    window: { addEventListener() {}, removeEventListener() {}, location: { reload() {}, href: "", pathname: "/", hash: "", search: "" } },
    document: {
      getElementById() { return makeEl(); }, querySelector() { return null; },
      querySelectorAll() { return []; }, createElement() { return makeEl(); },
      addEventListener() {}, removeEventListener() {}, documentElement: makeEl(),
      body: makeEl(), readyState: "complete",
    },
    localStorage: { getItem() { return "tok"; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    location: { reload() {}, href: "", pathname: "/", hash: "", search: "" },
    history: { replaceState() {} },
    fetch() { return new Promise(function () {}); },
    setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    IntersectionObserver: function () { this.observe = function () {}; },
    URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} },
    console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, "builds.js"), "utf8"), sandbox);
  return sandbox.module.exports;
}

let pass = 0;
function ok(name, fn) {
  try { fn(); pass++; console.log("  ok - " + name); }
  catch (e) { console.error("  FAIL - " + name + "\n        " + e.message); process.exitCode = 1; }
}

const api = loadPage();
const DAY = 86400000;
const iso = (ms) => new Date(ms).toISOString();

function prog(id, phases) {
  return { id: id, ask: "programme " + id, status: "in_progress", owner: "cc-x",
           phases: phases, last_progress: { line: "moved" }, updated_age_s: 3600 };
}
const done = { name: "p", status: "done" };
const open = { name: "p", status: "building" };

ok("finished = EVERY phase done (one open phase keeps it on the page)", () => {
  assert.strictEqual(api.isFinishedProgramme(prog(1, [done, done])), true);
  assert.strictEqual(api.isFinishedProgramme(prog(2, [done, open])), false);
  assert.strictEqual(api.isFinishedProgramme(prog(3, [])), false, "no phases is not finished");
  assert.strictEqual(api.isFinishedProgramme(null), false);
});

ok("splitProgrammes keeps every row, in one side or the other", () => {
  const s = api.splitProgrammes([prog(1, [done]), prog(2, [open]), prog(3, [done, done])]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.finished)).map((p) => p.id), [1, 3]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.active)).map((p) => p.id), [2]);
});

ok("untouched is the SERVER's ship time, not the render time", () => {
  const now = 1_800_000_000_000;
  const fresh = { repo: "a", latest_delivered_at: iso(now - 2 * DAY) };
  const old = { repo: "b", latest_delivered_at: iso(now - 9 * DAY) };
  const edge = { repo: "c", latest_delivered_at: iso(now - 7 * DAY) };
  assert.strictEqual(api.isQuietRepo(fresh, now), false);
  assert.strictEqual(api.isQuietRepo(old, now), true);
  assert.strictEqual(api.isQuietRepo(edge, now), true, "7 days exactly is 7+ days");
  // re-rendering the SAME payload at any 'now' cannot make an old repo fresh
  assert.strictEqual(api.isQuietRepo(old, now + 0), api.isQuietRepo(old, now + 1));
});

ok("a repo that never shipped is folded, not shown as fresh", () => {
  const now = 1_800_000_000_000;
  assert.strictEqual(api.isQuietRepo({ repo: "x", latest_delivered_at: null }, now), true);
  assert.strictEqual(api.isQuietRepo({ repo: "x" }, now), true);
  assert.strictEqual(api.isQuietRepo({ repo: "x", latest_delivered_at: "not-a-date" }, now), true);
});

ok("splitRepos keeps every group, in one side or the other", () => {
  const now = 1_800_000_000_000;
  const s = api.splitRepos([
    { repo: "a", latest_delivered_at: iso(now - DAY) },
    { repo: "b", latest_delivered_at: null },
  ], now);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.live)).map((g) => g.repo), ["a"]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.quiet)).map((g) => g.repo), ["b"]);
});

ok("the fold is closed by default and carries its count", () => {
  const html = api.quietBlock("3 repos untouched 7+ days — no live-verified ship in that repo",
                              "<div>x</div>");
  assert.ok(html.indexOf('<details class="repo-group quiet">') === 0, html);
  assert.ok(html.indexOf(" open") === -1, "the fold must NOT start open");
  assert.ok(html.indexOf("3 repos untouched") !== -1, html);
  assert.ok(html.indexOf('aria-expanded="false"') !== -1, html);
  assert.ok(html.indexOf(">x<") !== -1, "the contents are kept, not dropped");
});

ok("the fold's title is escaped like every other operator-facing string", () => {
  // the title flows through esc() like every other operator-facing string
  const html = api.quietBlock("1 finished programme — every phase done <b>", "in");
  assert.ok(html.indexOf("&lt;b&gt;") !== -1, html);
});

console.log("builds_quiet_fold.test.js: " + pass + " passed");
