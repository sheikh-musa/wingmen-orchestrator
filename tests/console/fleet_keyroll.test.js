// Node test (no deps) for op#20684 — "which lanes are on which key".
// Same `vm`-sandbox idiom as fleet_topbloat.test.js. Run:
//   node tests/console/fleet_keyroll.test.js
//
// LOCKS:
//  - the per-lane chip renders from the backend `pool` NICKNAME alone (the hosted
//    payload has no auth_fp), and from auth_fp when only that is present (local).
//  - poolRollup counts LIVE (non-offline) lanes per pool in Musa/musa2/Syed order,
//    unknown-key lanes as "unknown", and emits one .krchip per non-empty pool.
//  - an unknown fp NEVER leaks into a hosted chip: no pool + no fp = no chip.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

function makeEl() {
  return {
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: {}, dataset: {},
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    appendChild() {}, removeChild() {}, insertBefore() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    closest() { return null; }, focus() {}, blur() {}, click() {},
    innerHTML: "", textContent: "", value: "",
    scrollTop: 0, scrollHeight: 0, offsetHeight: 0,
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
  };
}

function loadFleet() {
  const src = fs.readFileSync(
    path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"), "utf8");
  const documentStub = {
    getElementById() { return makeEl(); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); },
    addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const windowStub = {
    addEventListener() {}, removeEventListener() {},
    location: { reload() {}, href: "", pathname: "/" },
    scrollY: 0, devicePixelRatio: 1,
    matchMedia() { return { matches: false, addEventListener() {}, addListener() {} }; },
    navigator: { serviceWorker: { register() { return Promise.resolve(); } }, userAgent: "test" },
  };
  const sandbox = {
    window: windowStub, document: documentStub,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    navigator: windowStub.navigator, location: windowStub.location,
    fetch() { return new Promise(function () {}); },
    setInterval() { return 0; }, clearInterval() {},
    setTimeout() { return 0; }, clearTimeout() {},
    requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
    AbortController, AbortSignal, URLSearchParams,
    console,
    module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(src, sandbox, { filename: "fleet.js" });
  return sandbox.module.exports;
}

const { poolOf, tokChip, poolRollup } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof poolOf === "function" && typeof tokChip === "function" && typeof poolRollup === "function",
  "fleet.js must export poolOf / tokChip / poolRollup");

ok("hosted row (pool only, no auth_fp) resolves + renders the pool chip", function () {
  assert.strictEqual(poolOf({ pool: "musa2" }), "musa2");
  const h = tokChip(undefined, poolOf({ pool: "musa2" }));
  assert(h.indexOf('class="tok musa2"') >= 0, h);
  assert(h.indexOf(">musa2<") >= 0, h);
});

ok("local row (auth_fp only) derives the same nickname + colour class", function () {
  assert.strictEqual(poolOf({ auth_fp: "68142948c003" }), "Musa");
  assert.strictEqual(poolOf({ auth_fp: "e1dfa48eec85" }), "musa2");
  assert.strictEqual(poolOf({ auth_fp: "582043088eae" }), "Syed");
  const h = tokChip("582043088eae", poolOf({ auth_fp: "582043088eae" }));
  assert(h.indexOf('class="tok syed"') >= 0 && h.indexOf(">Syed<") >= 0, h);
});

ok("backend pool wins over a stale/absent fp; unknown = no chip on hosted", function () {
  assert.strictEqual(poolOf({ pool: "Syed", auth_fp: "68142948c003" }), "Syed");
  assert.strictEqual(poolOf({ pool: "" }), "");
  assert.strictEqual(tokChip("", ""), "");                       // hosted unknown: nothing
  const h = tokChip("deadbeef0000", "");                          // local unknown fp: short id
  assert(h.indexOf('class="tok other"') >= 0 && h.indexOf("deadbe") >= 0, h);
});

ok("poolRollup counts LIVE lanes per pool in canonical order, unknown last", function () {
  const lanes = [
    { bucket: "working", pool: "musa2" },
    { bucket: "idle", pool: "musa2" },
    { bucket: "working", pool: "Musa" },
    { bucket: "offline", pool: "Musa" },          // offline: not counted
    { bucket: "working", auth_fp: "582043088eae" }, // local-shaped row
    { bucket: "idle", auth_fp: "deadbeef0000" },    // unknown key -> "?"
    { bucket: "idle" },                              // no key info -> skipped
  ];
  const r = poolRollup(lanes, "");
  // Object.assign: r.counts was born in the vm realm (different Object prototype).
  assert.deepStrictEqual(Object.assign({}, r.counts), { musa2: 2, Musa: 1, Syed: 1, "?": 1 });
  assert.strictEqual(r.total, 5);
  const order = (r.html.match(/data-pool="([^"]+)"/g) || []).map(function (s) { return s.slice(11, -1); });
  assert.deepStrictEqual(order, ["Musa", "musa2", "Syed", "?"]);
  assert(r.html.indexOf('class="krchip tok musa"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok musa2"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok syed"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok other"') >= 0 && r.html.indexOf(">unknown<b>1</b>") >= 0, r.html);
  assert(r.html.indexOf('<span class="krl">keys</span>') === 0, r.html);
  assert(r.html.indexOf("<b>2</b>") >= 0 && r.html.indexOf("<b>1</b>") >= 0, r.html);
  assert(r.html.indexOf("deadbe") < 0, "raw fp must never appear in the roll-up");
});

ok("poolRollup marks the tapped pool + explains the filter; empty fleet = empty row", function () {
  const r = poolRollup([{ bucket: "working", pool: "Musa" }, { bucket: "working", pool: "Syed" }], "Syed");
  assert(r.html.indexOf('class="krchip tok syed on"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok musa"') >= 0 && r.html.indexOf('tok musa on') < 0, r.html);
  assert(r.html.indexOf("showing Syed") >= 0, r.html);
  const e = poolRollup([{ bucket: "offline", pool: "Musa" }], "");
  assert.strictEqual(e.html, "");
  assert.strictEqual(e.total, 0);
});

console.log("fleet_keyroll: " + passed + " passed");
