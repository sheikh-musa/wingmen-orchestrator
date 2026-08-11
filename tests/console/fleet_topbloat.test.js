// Node test (no deps) for the fleet.js Top-bloat GLANCE widening (op#18542).
// fleet.js is a browser IIFE with no module system, so we load it in a `vm`
// sandbox with stubbed DOM/timer globals and read the guarded `module.exports`
// it publishes under node. Run: `node tests/console/fleet_topbloat.test.js`
//
// LOCKS the op#18542 behavior: the worst-offender GLANCE must span the whole
// fleet INCLUDING coordinators (cc-fleet-health etc.). The lane-list coordinator
// exclusion (op#9088) is server-side and covered by the Python suite; here we
// prove ONLY the glance widens and never double-counts.
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
    path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"),
    "utf8"
  );
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
    fetch() { return new Promise(function () {}); }, // never resolves -> start()'s fetch is inert
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

const { pickTopBloat, coordCtxRows } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof pickTopBloat === "function", "fleet.js must export pickTopBloat");
assert(typeof coordCtxRows === "function", "fleet.js must export coordCtxRows");

// op#18542: a coordinator (cc-fleet-health 88%) is fleet-worst while the top
// worker lane is 72% -> the glance must LEAD with the coordinator.
ok("glance leads with a coordinator when it is fleet-worst", function () {
  const contextBloat = [
    { agent: "cc-quality", pct: 72, level: "amber", ctx_tokens: 720000, window: 1000000, age_s: 10 },
    { agent: "cc-storefront", pct: 65, level: "amber", ctx_tokens: 650000, window: 1000000, age_s: 20 },
  ];
  const coords = [
    { agent_id: "cc-fleet-health", ctx_pct: 88, ctx_level: "red", ctx_tokens: 880000, ctx_age_s: 5 },
    { agent_id: "orch-console", ctx_pct: 41, ctx_level: "green", ctx_tokens: 410000, ctx_age_s: 5 },
    { agent_id: "cai", ctx_pct: null, ctx_level: null, ctx_tokens: null, ctx_age_s: null },
  ];
  const top = pickTopBloat(contextBloat.concat(coordCtxRows(coords)));
  assert.strictEqual(top[0].agent, "cc-fleet-health");
  assert.strictEqual(top[0].pct, 88);
});

// coordCtxRows maps coordinator ctx fields into the ctx-row shape and SKIPS a
// coordinator with no current reading (ctx_pct null).
ok("coordCtxRows maps fields and skips null-reading coordinators", function () {
  const rows = coordCtxRows([
    { agent_id: "cc-fleet-health", ctx_pct: 88, ctx_level: "red", ctx_tokens: 880000, ctx_age_s: 5 },
    { agent_id: "cai", ctx_pct: null, ctx_level: null, ctx_tokens: null, ctx_age_s: null },
  ]);
  assert.strictEqual(rows.length, 1);
  const fh = rows[0];
  assert.strictEqual(fh.agent, "cc-fleet-health");
  assert.strictEqual(fh.pct, 88);
  assert.strictEqual(fh.level, "red");
  assert.strictEqual(fh.ctx_tokens, 880000);
  assert.strictEqual(fh.age_s, 5);
});

// Defensive de-dupe: if the same agent appeared in BOTH lists it must collapse
// to a single glance entry (op#9088 keeps coordinators out of context_bloat, so
// this shouldn't happen live, but the invariant must hold).
ok("de-dupes an agent present in both lists", function () {
  const cb = [{ agent: "cc-fleet-health", pct: 70, level: "amber", ctx_tokens: 700000, window: 1000000, age_s: 10 }];
  const co = coordCtxRows([{ agent_id: "cc-fleet-health", ctx_pct: 88, ctx_level: "red", ctx_tokens: 880000, ctx_age_s: 5 }]);
  const top = pickTopBloat(cb.concat(co));
  const count = top.filter(function (r) { return r.agent === "cc-fleet-health"; }).length;
  assert.strictEqual(count, 1);
});

console.log("\n" + passed + " passed");
