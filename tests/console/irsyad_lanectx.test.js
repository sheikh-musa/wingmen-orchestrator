// Node test (no deps) for the never-blank #2b port onto the STANDALONE /irsyad view
// (Nazim decision #32534: build it so the fix is consistent across both views). irsyad.js
// is a browser IIFE; we load it in a `vm` sandbox and read its guarded module.exports.
// Run: `node tests/console/irsyad_lanectx.test.js`
//
// LOCKS the same four render states as the /fleet fix, adapted to irsyad.js's meter/row
// shape (flat r.ctx_* fields, not fleet.js's laneCtxIndex). A lane card must NEVER blank
// to "context —" on a live/idle lane:
//   live  -> {pct}% · {tokens} meter      stale -> "~{k}k · {age}" (dimmed meter)
//   label -> "context idle/low/n-a"       off   -> offline (unchanged faint "context —")
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
  };
}

function loadIrsyad() {
  const src = fs.readFileSync(
    path.join(__dirname, "..", "..", "nervous_system", "console", "static", "irsyad.js"),
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
    location: { reload() {}, href: "", pathname: "/irsyad" },
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
    AbortController, AbortSignal, URLSearchParams, console,
    module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(src, sandbox, { filename: "irsyad.js" });
  return sandbox.module.exports;
}

const { irsyadCtxDisplay, idleLabel } = loadIrsyad();
assert(typeof irsyadCtxDisplay === "function", "irsyad.js must export irsyadCtxDisplay");
assert(typeof idleLabel === "function", "irsyad.js must export idleLabel");

let passed = 0;
function ok(name, cond) { assert(cond, name); passed++; }

(function () {
  const d = irsyadCtxDisplay({ ctx_pct: 40, ctx_level: "green", ctx_stale: false, ctx_tokens: 400000, bucket: "working" });
  ok("live", d.mode === "live" && d.pct === 40 && d.level === "green" && d.tokens === 400000);
})();
(function () {
  const d = irsyadCtxDisplay({ ctx_pct: 70, ctx_level: "amber", ctx_stale: true, ctx_tokens: 700000, ctx_age_s: 1200, bucket: "working" });
  ok("stale", d.mode === "stale" && d.pct === 70 && d.k === 700 && d.age_s === 1200);
})();
(function () {
  ok("label idle", irsyadCtxDisplay({ ctx_pct: null, ctx_idle: "IDLE_EMPTY", bucket: "idle" }).text === "idle");
  ok("label low", irsyadCtxDisplay({ ctx_pct: null, ctx_idle: "WORKING", bucket: "working" }).text === "low");
  ok("label n/a", irsyadCtxDisplay({ ctx_pct: null, ctx_idle: null, bucket: "idle" }).text === "n/a");
  ok("label mode", irsyadCtxDisplay({ ctx_pct: null, ctx_idle: "IDLE_EMPTY", bucket: "idle" }).mode === "label");
})();
(function () {
  ok("offline off", irsyadCtxDisplay({ ctx_pct: null, ctx_idle: "IDLE_EMPTY", bucket: "offline" }).mode === "off");
  ok("offline beats stale reading",
     irsyadCtxDisplay({ ctx_pct: 70, ctx_stale: true, bucket: "offline" }).mode === "off");
})();
(function () {
  ok("idleLabel IDLE_EMPTY", idleLabel("IDLE_EMPTY") === "idle");
  ok("idleLabel STAGED", idleLabel("STAGED") === "low");
  ok("idleLabel unknown", idleLabel("GHOST_WEDGED") === "n/a");
})();

console.log(`irsyad_lanectx.test.js: ${passed} assertions passed`);
