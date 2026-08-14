// Node test (no deps) for the fleet.js dashboard PACE-CARD render + the op#12709
// dashboard cleanup. Same `vm`-sandbox idiom as fleet_topbloat.test.js: fleet.js
// is a browser IIFE, so we load it in a sandbox with stubbed DOM/timer globals
// and read the guarded `module.exports`. Run: `node tests/console/fleet_pace.test.js`
//
// LOCKS:
//  - poolChip renders the weekly % pill PLUS the pace/projected advisory (muted,
//    NOT a red alarm) when the pace layer (op#12617) is present.
//  - runway_days is HIDDEN when null (no reading yet, <~20h history), and styled
//    as a WARNING only when the runway is shorter than the days left to reset.
//  - the lane-switching UI (op#12709) is GONE from the dashboard bundle: no
//    renderFleetSwitch / switchCtlHtml / SWITCH_ACCOUNTS in fleet.js.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const FLEET_JS = path.join(
  __dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"
);

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

const { poolChip } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof poolChip === "function", "fleet.js must export poolChip");

// A resets_at N days from now, in the ISO+00:00 shape pool_usage jsonifies.
function resetsInDays(d) {
  return new Date(Date.now() + d * 86400000).toISOString().replace("Z", "+00:00");
}

// Base weekly reading (no pace layer) still renders the pill.
ok("renders the weekly % pill", function () {
  const h = poolChip({ pool: "Musa", pct_7d: 42, updated_age_s: 30 });
  assert(/Musa/.test(h), "pool name present");
  assert(/42%/.test(h), "weekly pct present");
  assert(/poolchip/.test(h), "keeps the .poolchip pill class");
});

// Pace advisory (op#12617): pace + projected render, styled MUTED (advisory),
// never the .bad / red alarm class.
ok("renders pace + projected as a muted advisory (not red)", function () {
  const h = poolChip({
    pool: "Syed", pct_7d: 60, updated_age_s: 30,
    pace: 2.1, projected_pct: 78, runway_days: null,
    resets_at: resetsInDays(4),
  });
  assert(/2\.1x/.test(h), "pace shown as 2.1x");
  assert(/proj/i.test(h) && /78%/.test(h), "projected shown");
  assert(/pooladv|poolpace/.test(h), "advisory carries a muted class");
  assert(!/poolchip bad/.test(h), "advisory must NOT trip the red .bad alarm");
});

// runway null -> hidden entirely (no reading before ~20h history).
ok("hides runway when null", function () {
  const h = poolChip({
    pool: "Musa", pct_7d: 30, updated_age_s: 30,
    pace: 1.2, projected_pct: 40, runway_days: null,
    resets_at: resetsInDays(5),
  });
  assert(!/runway/i.test(h), "no runway text when runway_days is null");
});

// runway shorter than days-to-reset -> WARNING styling (real risk signal).
ok("styles runway as a warning when it is shorter than days-to-reset", function () {
  const h = poolChip({
    pool: "Syed", pct_7d: 80, updated_age_s: 30,
    pace: 3.0, projected_pct: 120, runway_days: 1.5,
    resets_at: resetsInDays(5),   // 5 days to reset, only 1.5d runway -> at risk
  });
  assert(/runway/i.test(h) && /1\.5d/.test(h), "runway value shown");
  assert(/poolrun warn|warnpace/.test(h), "runway flagged as a warning");
});

// runway longer than days-to-reset -> neutral (resets before the pool runs dry).
ok("keeps runway neutral when it outlasts the reset", function () {
  const h = poolChip({
    pool: "Musa", pct_7d: 50, updated_age_s: 30,
    pace: 1.1, projected_pct: 55, runway_days: 9.0,
    resets_at: resetsInDays(3),   // resets in 3d, 9d runway -> safe
  });
  assert(/9\.0d/.test(h), "runway value shown");
  assert(!/poolrun warn/.test(h), "runway NOT flagged when it outlasts the reset");
});

// fc-v50 Command Surface (Approach C, operator-approved) SUPERSEDES op#12709's
// "no lane-switching UI on the dashboard": the unified page now folds the bulk
// account switch back in behind MULTI-SELECT (hidden until opted into) + the same
// dry-run→explicit-confirm safety, which mitigates the switch-ALL mistap op#12709
// was worried about better than a permanently-visible toolbar did. So the bulk
// switch IS expected in fleet.js now — but it must stay DRY-RUN-SAFE, and the old
// op#12709-era identifiers must not sneak back (clean rename, no dead cruft).
ok("bulk switch is folded in via multi-select and stays dry-run-safe (fc-v50)", function () {
  const src = fs.readFileSync(FLEET_JS, "utf8");
  // the OLD lane-manager identifiers stay gone (renamed cleanly, not resurrected)
  ["renderFleetSwitch", "switchCtlHtml", "SWITCH_ACCOUNTS", "fsDryRun"]
    .forEach(function (needle) {
      assert(src.indexOf(needle) < 0, "fleet.js must not reintroduce " + needle);
    });
  // the bulk switch exists again, and every path is dry-run-first (never one-tap)
  assert(src.indexOf("/api/switch-all") >= 0, "fleet.js folds in the bulk switch");
  assert(/dry_run:\s*true/.test(src), "bulk switch previews with dry_run:true first");
});

console.log("\n" + passed + " passed");
