// Node test (no deps) for the GLM Coding Plan card (op#24597). Same `vm`-sandbox
// idiom as fleet_pace.test.js. Run: `node tests/console/fleet_glm.test.js`
//
// LOCKS:
//  - an available read renders "GLM (z.ai Pro)", both windows with used/cap + %,
//    a reset countdown, and the reset clock in UTC+4 next to UTC.
//  - an unavailable read renders "GLM usage unavailable" and NO numbers.
//  - fmtClockDual shifts by exactly +4h and labels both zones.
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

const { glmCard, fmtClockDual } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof glmCard === "function", "fleet.js must export glmCard");

ok("fmtClockDual shows UTC+4 next to UTC", () => {
  // 2026-10-02T10:30:00Z is a Friday -> 14:30 in Abu Dhabi.
  assert.strictEqual(fmtClockDual("2026-10-02T10:30:00+00:00"), "Fri 14:30 UTC+4 (10:30 UTC)");
  // crossing midnight rolls the weekday
  assert.strictEqual(fmtClockDual("2026-10-02T21:15:00+00:00"), "Sat 01:15 UTC+4 (21:15 UTC)");
  assert.strictEqual(fmtClockDual(null), "");
  assert.strictEqual(fmtClockDual("garbage"), "");
});

ok("available read renders numbers, % and both clocks", () => {
  const in2h = new Date(Date.now() + 2 * 3600000 + 60000).toISOString().replace("Z", "+00:00");
  const in5d = new Date(Date.now() + 5 * 86400000).toISOString().replace("Z", "+00:00");
  const html = glmCard({
    available: true, level: "pro", age_s: 42,
    windows: [
      { label: "5h", used: 1650, cap: 12000, remaining: 10349, pct: 13.8, resets_at: in2h },
      { label: "wk", used: 1650, cap: 60000, remaining: 58349, pct: 2.8, resets_at: in5d },
    ],
  });
  assert(html.includes("GLM (z.ai Pro)"), html);
  assert(html.includes("plan pro"), html);
  assert(html.includes("1,650/12,000"), html);
  assert(html.includes("1,650/60,000"), html);
  assert(html.includes(">14%<") && html.includes(">3%<"), html);
  assert(/resets in 2h \d+m/.test(html), html);
  assert(/resets in [45]d \d+h/.test(html), html);
  assert(html.includes("UTC+4 (") && html.includes(" UTC)"), html);
  assert(!html.includes("unavailable"), html);
  assert(html.includes("poolcard glm good"), html);
});

ok("unavailable read says so and shows no numbers", () => {
  const html = glmCard({ available: false, level: null, windows: [], age_s: null });
  assert(html.includes("GLM usage unavailable"), html);
  assert(!/\d+%/.test(html.replace(/title="[^"]*"/, "")), html);
  assert(!/\d+\/\d+/.test(html), html);
});

ok("missing payload (old server) renders nothing", () => {
  assert.strictEqual(glmCard(undefined), "");
});

ok("high usage colours the card bad", () => {
  const html = glmCard({ available: true, level: "pro", windows: [
    { label: "5h", used: 11500, cap: 12000, pct: 95.8, resets_at: null }] });
  assert(html.includes("poolcard glm bad"), html);
  assert(html.includes("resets —"), html);
});

console.log("fleet_glm.test.js: " + passed + " passed");
