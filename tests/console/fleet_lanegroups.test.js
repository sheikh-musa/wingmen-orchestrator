// Node test (no deps) for the fleet page's lane grouping (console #90041 item 5).
// Same `vm`-sandbox idiom as fleet_deepseek.test.js.
// Run: `node tests/console/fleet_lanegroups.test.js`
//
// LOCKS: the "idle & fine" summary may only count lanes that are actually IDLE.
// A registered lane with no live process is DOWN -- it is not idle, it is not
// fine, and counting it as both said "all good" about lanes nobody had running
// (the glm-5.3 registrations that prompted the console request).
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const FLEET_JS = path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js");

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
  const src = fs.readFileSync(FLEET_JS, "utf8");
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
    AbortController, AbortSignal, URLSearchParams, Date, console,
    module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(src, sandbox, { filename: "fleet.js" });
  return sandbox.module.exports;
}

// The vm sandbox is a separate realm: its arrays/objects never compare
// reference-equal to ours, so compare by value.
function eq(actual, expected, msg) {
  assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected),
    msg || (JSON.stringify(actual) + " != " + JSON.stringify(expected)));
}

let pass = 0;
function ok(name, fn) {
  try { fn(); pass++; console.log("  ok - " + name); }
  catch (e) { console.error("  FAIL - " + name + "\n        " + e.message); process.exitCode = 1; }
}

const { laneGroups, laneGroupOf } = loadFleet();

ok("laneGroups is exported", () => {
  assert.strictEqual(typeof laneGroups, "function");
});

ok("a registered-but-dark lane is DOWN, never 'idle & fine'", () => {
  const lanes = [
    { session: "cosem-exams", bucket: "idle", flagged: false },
    { session: "glm-old-1", bucket: "offline", flagged: false },
    { session: "glm-old-2", bucket: "offline", flagged: false },
  ];
  const g = laneGroups(lanes);
  eq(g.routine.map(l => l.session), ["cosem-exams"]);
  eq(g.down.map(l => l.session), ["glm-old-1", "glm-old-2"]);
  eq(g.primary, []);
});

ok("a desired-up lane with no pane is FLAGGED (primary), not down/routine", () => {
  const lanes = [{ session: "cosem-exams", bucket: "offline", flagged: true }];
  const g = laneGroups(lanes);
  eq(g.primary.map(l => l.session), ["cosem-exams"]);
  eq(g.routine, []);
  eq(g.down, []);
});

ok("working lanes are primary", () => {
  const lanes = [{ session: "a", bucket: "working", flagged: false }];
  eq(laneGroups(lanes).primary.map(l => l.session), ["a"]);
});

ok("empty / missing input never throws", () => {
  // cross-realm objects (built inside the vm sandbox) never compare
  // reference-equal, so assert on the shape's contents, not the prototype
  [laneGroups([]), laneGroups(null)].forEach((g) => {
    assert.strictEqual(g.primary.length, 0);
    assert.strictEqual(g.routine.length, 0);
    assert.strictEqual(g.down.length, 0);
  });
});

ok("every lane lands in exactly one group (no lane vanishes from the page)", () => {
  const lanes = [
    { session: "a", bucket: "working" }, { session: "b", bucket: "idle" },
    { session: "c", bucket: "offline" }, { session: "d", bucket: "offline", flagged: true },
    { session: "e", bucket: "unknown-future-bucket" },
  ];
  const g = laneGroups(lanes);
  const all = [].concat(g.primary, g.routine, g.down).map(l => l.session).sort();
  eq(all, ["a", "b", "c", "d", "e"]);
  assert.strictEqual(new Set(all).size, all.length, "a lane appeared twice");
});

// console #90323 N1: the sheet jump opened the WRONG collapsed container for a
// down lane because the entry said "routine". The fix is one shared classifier;
// these lock that the two consumers can never disagree again.
ok("a DOWN lane is never routine (jumping to it must open #down, not the idle group)", () => {
  assert.strictEqual(laneGroupOf({ bucket: "offline" }), "down");
  assert.strictEqual(laneGroupOf({ bucket: "down" }), "down");
  assert.notStrictEqual(laneGroupOf({ bucket: "offline" }), "routine");
  assert.strictEqual(laneGroupOf({ bucket: "idle" }), "routine");
  assert.strictEqual(laneGroupOf({ bucket: "working" }), "primary");
  assert.strictEqual(laneGroupOf({ bucket: "offline", flagged: true }), "primary",
    "a desired-up lane with no pane is flagged, and lives in the primary list");
});

ok("laneGroupOf agrees with laneGroups for EVERY bucket (one definition, two consumers)", () => {
  const cases = [
    { session: "a", bucket: "working" }, { session: "b", bucket: "idle" },
    { session: "c", bucket: "offline" }, { session: "d", bucket: "offline", flagged: true },
    { session: "e", bucket: "unknown-future-bucket" }, { session: "f" },
  ];
  const g = laneGroups(cases);
  const blockOf = (l) => (g.primary.indexOf(l) !== -1 ? "primary"
    : (g.routine.indexOf(l) !== -1 ? "routine" : "down"));
  cases.forEach((l) => {
    assert.strictEqual(laneGroupOf(l), blockOf(l),
      "entry says " + laneGroupOf(l) + " but the lane renders in " + blockOf(l) +
      " -- jumpToLane would open the wrong box for " + l.session);
  });
});

console.log("fleet_lanegroups.test.js: " + pass + " passed");
