// Node test (no deps) for the never-blank lane-context render decision (Musa flag via
// Nazim #32472; design #32484/#32489). fleet.js is a browser IIFE; we load it in a `vm`
// sandbox and read the guarded module.exports it publishes under node.
// Run: `node tests/console/fleet_lanectx.test.js`
//
// LOCKS the three render states a WORKER lane card must resolve to — it must NEVER blank
// to a bare `—`:
//   LIVE   — a fresh pane reading -> "{pct}% ctx"
//   STALE  — a LAST-KNOWN reading (hint hidden this cycle) -> "~{k}k · {age}" (age VISIBLE)
//   LABEL  — no reading (never had a hint / aged out) -> honest idle/low/n-a from idle_verdict
//   OFF    — offline lane -> off (unchanged), and offline WINS over any stale reading.
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

const { ctxDisplayFrom, idleLabel } = loadFleet();
assert(typeof ctxDisplayFrom === "function", "fleet.js must export ctxDisplayFrom");
assert(typeof idleLabel === "function", "fleet.js must export idleLabel");

let passed = 0;
function ok(name, cond) { assert(cond, name); passed++; }

// LIVE — a fresh reading renders the live %.
(function () {
  const d = ctxDisplayFrom({ pct: 40, level: "green", stale: false }, "WORKING", "working");
  ok("live mode", d.mode === "live" && d.pct === 40 && d.level === "green");
})();

// STALE — a last-known reading renders "~{k}k · {age}", NOT a live %.
(function () {
  const d = ctxDisplayFrom({ pct: 70, level: "amber", stale: true, ctx_tokens: 700000, age_s: 1200 },
                           "STAGED", "working");
  ok("stale mode", d.mode === "stale" && d.pct === 70 && d.k === 700 && d.age_s === 1200);
})();

// LABEL — no reading -> honest label from idle_verdict; NEVER a bare "—".
(function () {
  ok("label idle", ctxDisplayFrom(null, "IDLE_EMPTY", "idle").text === "idle");
  ok("label low (working, below hint bar)", ctxDisplayFrom(null, "WORKING", "working").text === "low");
  ok("label low (staged)", ctxDisplayFrom(null, "STAGED", "idle").text === "low");
  ok("label n/a (unknown)", ctxDisplayFrom(null, null, "idle").text === "n/a");
  ok("label mode set", ctxDisplayFrom(null, "IDLE_EMPTY", "idle").mode === "label");
})();

// OFF — an offline lane reads OFF and offline WINS even if a (stale) reading exists.
(function () {
  ok("offline off", ctxDisplayFrom(null, "IDLE_EMPTY", "offline").mode === "off");
  ok("offline beats reading",
     ctxDisplayFrom({ pct: 70, level: "amber", stale: true }, "STAGED", "offline").mode === "off");
})();

// idleLabel mapping is honest and total.
(function () {
  ok("idleLabel IDLE_EMPTY", idleLabel("IDLE_EMPTY") === "idle");
  ok("idleLabel WORKING", idleLabel("WORKING") === "low");
  ok("idleLabel unknown -> n/a", idleLabel("GHOST_WEDGED") === "n/a");
})();

console.log(`fleet_lanectx.test.js: ${passed} assertions passed`);
