// Node test (no deps) for the DeepSeek pool card (op#35897, orch-console #83397).
// Same `vm`-sandbox idiom as fleet_glm.test.js. Run: `node tests/console/fleet_deepseek.test.js`
//
// LOCKS:
//  - the SHARED-key row (today's real spend location -- every lane still
//    draws on it) always renders first, with its own balance/delta/lane list;
//  - a good per-client key read renders a BALANCE figure (never "spend");
//  - a key MAPPED but never provisioned (vault has no secret) says
//    "key not created yet" -- NOT "could not read", a distinct state;
//  - a key that exists but fails to read says "could not read";
//  - neither failure state ever shows "$0.00" or a bare 0;
//  - delta-since-last-poll renders (signed) when present, omitted on a first
//    read (delta_usd == null) -- never a fabricated 0 delta;
//  - families sharing one vault key render as ONE grouped row ("cosem / cosem-tdu");
//  - each per-client row shows the LIVE lanes mapped to it (session list, from
//    app.py's family-matched lanes -- never the pointer file);
//  - missing/empty payload renders nothing.
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

const { deepseekCard } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof deepseekCard === "function", "fleet.js must export deepseekCard");

const SHARED_OK = { key_name: "DEEPSEEK_CODING_KEY", available: true,
  balance_usd: 46.87, delta_usd: null, age_s: 5, lanes: ["bariq", "oeh"] };

ok("the shared-key row renders first, with its own balance and lanes", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [] });
  assert(html.includes("Shared key"), html);
  assert(html.includes("$46.87"), html);
  assert(html.includes("bariq"), html);
  assert(html.includes("oeh"), html);
});

ok("a good per-client read renders a balance figure and never the word spend", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 10.00, delta_usd: null, age_s: 5, lanes: ["bariq"] },
  ] });
  assert(html.includes("$10.00"), html);
  assert(html.includes("balance"), html);
  assert(!/spend/i.test(html), html);
});

ok("a key mapped but never provisioned says 'key not created yet', not 'could not read'", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_COSEM", families: ["cosem", "cosem-tdu"], available: false,
      balance_usd: null, delta_usd: null, reason: "key not created yet", age_s: null, lanes: [] },
  ] });
  assert(html.includes("key not created yet"), html);
  assert(!html.includes("could not read"), html);
  assert(!html.includes("$0.00"), html);
  assert(!/>0</.test(html), html);
});

ok("a key that exists but fails to read says 'could not read'", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: false,
      balance_usd: null, delta_usd: null, reason: "could not read", age_s: null, lanes: ["bariq"] },
  ] });
  assert(html.includes("could not read"), html);
  assert(!html.includes("key not created yet"), html);
  assert(!html.includes("$0.00"), html);
});

ok("families sharing a key render as one grouped row", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_COSEM", families: ["cosem", "cosem-tdu"], available: true,
      balance_usd: 10, delta_usd: null, age_s: 1, lanes: [] },
  ] });
  assert(html.includes("cosem / cosem-tdu"), html);
  assert.strictEqual((html.match(/poolwin/g) || []).length, 2, html);   // shared row + this one
});

ok("delta since last poll renders signed, both directions", () => {
  const down = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 44.37, delta_usd: -2.50, age_s: 1, lanes: ["bariq"] },
  ] });
  assert(/-\$2\.50/.test(down), down);
  assert(/since last poll/.test(down), down);

  const up = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 50.00, delta_usd: 3.13, age_s: 1, lanes: ["bariq"] },
  ] });
  assert(/\+\$3\.13/.test(up), up);
});

ok("first-ever read (delta_usd null, available true) omits delta text entirely", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 46.87, delta_usd: null, age_s: 1, lanes: ["bariq"] },
  ] });
  assert(!/since last poll/.test(html), html);
});

ok("per-client row shows its own mapped live lanes, distinct from an unmapped row's empty list", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 1, delta_usd: null, age_s: 1, lanes: ["bariq"] },
    { key_name: "DEEPSEEK_KEY_OEH", families: ["oeh"], available: false,
      balance_usd: null, delta_usd: null, reason: "key not created yet", age_s: null, lanes: [] },
  ] });
  const bariqChunk = html.split("DEEPSEEK_KEY_OEH")[0];
  assert(bariqChunk.includes("bariq"), html);
  assert(!html.includes("DEEPSEEK_KEY_BARIQ") && !html.includes("DEEPSEEK_KEY_OEH"), html);  // raw key_name never shown
});

ok("missing payload (old server) renders nothing", () => {
  assert.strictEqual(deepseekCard(undefined), "");
});

ok("no shared data and no keys renders nothing (nothing to show)", () => {
  assert.strictEqual(deepseekCard({ shared: null, keys: [] }), "");
});

console.log("fleet_deepseek.test.js: " + passed + " passed");
