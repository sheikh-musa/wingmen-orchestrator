// Node test (no deps) for the DeepSeek pool card (op#35897, orch-console #83397).
// Same `vm`-sandbox idiom as fleet_glm.test.js. Run: `node tests/console/fleet_deepseek.test.js`
//
// LOCKS:
//  - the SHARED-key row (today's real spend location -- every lane still
//    draws on it) always renders first, with its own balance/delta/lane list;
//  - a good per-client key read renders a BALANCE figure (never only "spend");
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
//  op#37213/#37217 (console #90015/#90026), added below:
//  - the card is COLLAPSED by default, one summary line: shared balance +
//    today's measured spend against the 7 USD line (warn at 5);
//  - the day basis is labelled "provider day (UTC+8)" and is never called
//    anyone's local day (Musa is UTC+4 -- console #90026);
//  - spend level drives the colour class: under / warn / over;
//  - an unreadable export says "spend unavailable", never $0.00;
//  - a key with no export row says "no cost row", never $0.00;
//  - the expanded body states plainly that cost is per KEY, not per lane.
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

ok("a good per-client read renders a balance figure alongside its measured cost", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 10.00, delta_usd: null, age_s: 5, lanes: ["bariq"],
      spend: { today_usd: 0.42, last7_usd: 2.10, today_requests: 900 } },
  ], spend: { available: true, total_today_usd: 0.42, budget_line_usd: 7.0, budget_warn_usd: 5.0 } });
  assert(html.includes("$10.00"), html);
  assert(html.includes("balance"), html);
  assert(html.includes("$0.42 today"), html);
  assert(html.includes("$2.10 / 7d"), html);
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
      balance_usd: 10, delta_usd: null, age_s: 1, lanes: ["cosem-adcda"],
      spend: { today_usd: 1.2, last7_usd: 3.0, today_requests: 40 } },
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

// ---- op#37213/#37217: measured spend on the card (console #90015/#90026) ----

const SPEND_OVER = { available: true, day: "2026-11-02", day_label: "provider day (UTC+8)",
  total_today_usd: 11.99, total_7d_usd: 41.30, budget_line_usd: 7.0, budget_warn_usd: 5.0,
  updated_at: "2026-11-01T14:20:00Z", age_s: 1200, stale: false,
  by_family: { substrate: { today_usd: 8.56, last7_usd: 30.0, today_requests: 12000 } } };

ok("card is collapsed by default and shows balance + today's spend against the line", () => {
  const shared = Object.assign({}, SHARED_OK, {
    spend: { today_usd: 8.56, last7_usd: 30.0, today_requests: 12000 } });
  const html = deepseekCard({ shared: shared, keys: [], spend: SPEND_OVER });
  assert(html.startsWith("<details"), html);
  assert(!html.includes(" open"), html);                      // collapsed
  assert(html.includes("$46.87"), html);                      // balance
  assert(html.includes("$11.99 today of $7.00 line"), html);  // measured vs the line
  assert(html.includes("dsspend over"), html);                // over the line -> bad colour
});

ok("the day basis is labelled 'provider day (UTC+8)' and is never called a local day", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [], spend: SPEND_OVER });
  assert(html.includes("provider day (UTC+8)"), html);
  assert(!/musa|local day|his day|your day/i.test(html), html);
});

ok("spend colour tracks the line: under 5 green, 5-7 warn, 7+ over", () => {
  const mk = (t) => deepseekCard({ shared: SHARED_OK, keys: [],
    spend: Object.assign({}, SPEND_OVER, { total_today_usd: t }) });
  assert(mk(4.99).includes("dsspend under"), mk(4.99));
  assert(mk(5.00).includes("dsspend warn"), mk(5.00));
  assert(mk(7.00).includes("dsspend over"), mk(7.00));
});

ok("passing open=true renders the card expanded, so a re-render keeps it open", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [], spend: SPEND_OVER }, true);
  assert(/<details[^>]*\bopen\b/.test(html), html);
});

ok("an unreadable export says 'spend unavailable', never $0.00", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [], spend: {
    available: false, reason: "usage export unreadable", by_family: {} } });
  assert(html.includes("spend unavailable"), html);
  assert(!html.includes("$0.00"), html);
  assert(!html.includes("provider day"), html);   // no day claim without a day reading
});

ok("a key with no export row says 'no cost row', never $0.00", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 10, delta_usd: null, age_s: 1, lanes: ["bariq"], spend: null },
  ], spend: SPEND_OVER });
  assert(html.includes("no cost row"), html);
  assert(!html.includes("$0.00"), html);
});

ok("per-key rows carry today + 7d and the expanded body says cost is per KEY, not per lane", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "DEEPSEEK_KEY_BARIQ", families: ["bariq"], available: true,
      balance_usd: 10, delta_usd: null, age_s: 1, lanes: ["bariq"],
      spend: { today_usd: 0.42, last7_usd: 2.10, today_requests: 900 } },
  ], spend: SPEND_OVER });
  assert(html.includes("$0.42 today"), html);
  assert(html.includes("$2.10 / 7d"), html);
  assert(/cost is per key, not per lane/i.test(html), html);
  assert(/one key per lane/i.test(html), html);
});

ok("a stale export is called out rather than shown as current", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [],
    spend: Object.assign({}, SPEND_OVER, { stale: true, age_s: 18000 }) });
  assert(/stale/i.test(html), html);
});

ok("an old payload with no spend block renders the card with no cost claims at all", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [] });
  assert(html.includes("$46.87"), html);
  assert(!/today|7d|no cost row/i.test(html), html);
});

ok("keys with no spend today and no live lane collapse to one count line, not 15 rows", () => {
  const keys = [];
  for (let i = 0; i < 12; i++) {
    keys.push({ key_name: "K" + i, families: ["quiet" + i], available: true,
      balance_usd: 29.27, delta_usd: null, age_s: 1, lanes: [], spend: null });
  }
  keys.push({ key_name: "BUSY", families: ["cosem"], available: true,
    balance_usd: 29.27, delta_usd: null, age_s: 1, lanes: ["cosem-adcda"],
    spend: { today_usd: 1.75, last7_usd: 4.0, today_requests: 10 } });
  const html = deepseekCard({ shared: SHARED_OK, keys: keys, spend: SPEND_OVER });
  assert(html.includes("12 key(s) with no spend today and no live lane"), html);
  assert(html.includes("cosem-adcda"), html);          // the active one still shows
  assert(!html.includes("quiet0"), html);              // the quiet ones do not
  assert.strictEqual((html.match(/class="poolwin/g) || []).length, 2, html); // shared + busy
});

ok("an unreadable key is never collapsed away -- it is always a finding", () => {
  const html = deepseekCard({ shared: SHARED_OK, keys: [
    { key_name: "K1", families: ["broken"], available: false, balance_usd: null,
      delta_usd: null, reason: "could not read", age_s: null, lanes: [], spend: null },
  ], spend: SPEND_OVER });
  assert(html.includes("broken"), html);
  assert(html.includes("could not read"), html);
});

ok("missing payload (old server) renders nothing", () => {
  assert.strictEqual(deepseekCard(undefined), "");
});

ok("no shared data and no keys renders nothing (nothing to show)", () => {
  assert.strictEqual(deepseekCard({ shared: null, keys: [] }), "");
});

console.log("fleet_deepseek.test.js: " + passed + " passed");
