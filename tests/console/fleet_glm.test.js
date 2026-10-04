// Node test (no deps) for the GLM Coding Plan card (op#24597). Same `vm`-sandbox
// idiom as fleet_pace.test.js. Run: `node tests/console/fleet_glm.test.js`
//
// LOCKS:
//  - an available read renders "GLM (z.ai Pro)", both windows with used/cap + %,
//    a reset countdown, and the reset clock in UTC+4 next to UTC.
//  - an unavailable read renders "GLM usage unavailable" and NO numbers.
//  - glmPace mirrors pool_pace.py (pace/projection) + week-to-date runway (op#25580).
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

const { glmCard, glmPace } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof glmCard === "function", "fleet.js must export glmCard");

const DAY = 86400000, NOW = Date.parse("2026-10-04T12:00:00Z");
const at = (days) => new Date(NOW + days * DAY).toISOString().replace("Z", "+00:00");
const near = (a, b, eps) => Math.abs(a - b) < (eps || 1e-9);

ok("glmPace: mid-week maths match pool_pace.py", () => {
  // 3.5d to reset -> elapsed 0.5; used 40% -> pace 0.8x, proj 80%, rate 40/3.5 %/d, runway 60/(40/3.5)=5.25d
  const p = glmPace(40, at(3.5), NOW);
  assert(near(p.pace, 0.8) && near(p.projected_pct, 80), JSON.stringify(p));
  assert(near(p.runway_days, 5.25), JSON.stringify(p));
});

ok("glmPace: ahead of pace (screenshot case: 57% with 3d21h left)", () => {
  const p = glmPace(57, at(3 + 21 / 24), NOW);
  const ef = (7 - (3 + 21 / 24)) / 7;
  assert(near(p.pace, 0.57 / ef) && near(p.projected_pct, 57 / ef), JSON.stringify(p));
  assert(p.pace > 1 && p.projected_pct > 100, JSON.stringify(p));
});

ok("glmPace: elapsed == 0 (window just reset) -> no pace/proj/runway, no divide-by-zero", () => {
  const p = glmPace(5, at(7), NOW);
  assert(p.pace === null && p.projected_pct === null && p.runway_days === null, JSON.stringify(p));
});

ok("glmPace: reset further than 7d out clamps elapsed to 0 (same as pool_pace clamp)", () => {
  const p = glmPace(5, at(9), NOW);
  assert(p.pace === null && p.projected_pct === null, JSON.stringify(p));
});

ok("glmPace: 0% used -> pace 0, proj 0, runway hidden (not burning == pool_pace inf)", () => {
  const p = glmPace(0, at(3.5), NOW);
  assert(p.pace === 0 && p.projected_pct === 0 && p.runway_days === null, JSON.stringify(p));
});

ok("glmPace: >=100% used -> runway 0, never negative", () => {
  const p = glmPace(120, at(3.5), NOW);
  assert(p.runway_days === 0, JSON.stringify(p));
});

ok("glmPace: missing / past / unparsable reset or null pct -> null", () => {
  assert.strictEqual(glmPace(40, null, NOW), null);
  assert.strictEqual(glmPace(40, at(-0.1), NOW), null);
  assert.strictEqual(glmPace(40, "garbage", NOW), null);
  assert.strictEqual(glmPace(null, at(3), NOW), null);
});

ok("card shows ONE advisory line, from WK only, in the Claude-card format", () => {
  const html = glmCard({ available: true, level: "pro", windows: [
    { label: "wk", pct: 40, resets_at: new Date(Date.now() + 3.5 * DAY).toISOString() },
    { label: "5h", pct: 90, resets_at: new Date(Date.now() + 1 * 3600000).toISOString() }] });
  assert.strictEqual((html.match(/pooladvrow/g) || []).length, 1, html);
  assert(/0\.8x · proj 80%/.test(html), html);
  assert(/runway 5\.\dd/.test(html), html);
});

ok("runway shorter than days-to-reset is flagged warn (same rule as Claude cards)", () => {
  // 1d to reset, elapsed 6/7, used 95%: rate 95/6 %/d -> runway 5/(95/6)=0.32d < 1d
  const html = glmCard({ available: true, level: "pro", windows: [
    { label: "wk", pct: 95, resets_at: new Date(Date.now() + 1 * DAY).toISOString() }] });
  assert(html.includes('poolrun warn'), html);
});

ok("no WK window, or WK just reset -> no advisory line at all", () => {
  const h1 = glmCard({ available: true, level: "pro", windows: [
    { label: "5h", pct: 30, resets_at: new Date(Date.now() + 3600000).toISOString() }] });
  assert(!h1.includes("pooladvrow"), h1);
  const h2 = glmCard({ available: true, level: "pro", windows: [
    { label: "wk", pct: 2, resets_at: new Date(Date.now() + 7 * DAY + 60000).toISOString() }] });
  assert(!h2.includes("pooladvrow"), h2);
});

ok("available read renders % and resets-in only (op#25562: no counts, no clock)", () => {
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
  assert(!html.includes("1,650"), html);
  assert(!html.includes("12,000") && !html.includes("60,000"), html);
  assert(html.includes(">14%<") && html.includes(">3%<"), html);
  assert(/resets in 2h \d+m/.test(html), html);
  assert(/resets in [45]d \d+h/.test(html), html);
  assert(!html.includes("UTC+4") && !html.includes(" UTC)"), html);
  assert(!html.includes("glmclock"), html);
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

ok("op#25357: key_warning is never rendered, even when the backend sets it", () => {
  const html = glmCard({ available: true, level: "pro", age_s: 1,
    key_warning: "key leak-flagged — use accepted by Musa (op#24626); rotate when convenient",
    windows: [{ label: "5h", used: 10, cap: 100, pct: 10, resets_at: null }] });
  assert(!html.includes("glmwarn"), html);
  assert(!html.includes("op#24626"), html);
});

ok("no key_warning -> no warning line either (unchanged)", () => {
  const html = glmCard({ available: true, level: "pro", key_warning: null,
    windows: [{ label: "5h", used: 10, cap: 100, pct: 10, resets_at: null }] });
  assert(!html.includes("glmwarn"), html);
});

console.log("fleet_glm.test.js: " + passed + " passed");
