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

const { poolChip, hoursToReset, minutesToReset, fmtReset } = loadFleet();
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

// Musa op#20644/#20657: EVERY key renders a per-key CARD — weekly row (bar +
// % + "resets in Xd Yh" / "Xh Ym" under 48h) and a 5h row ("window resets in
// Xh Ym" from resets_5h_at), "—" when null / unparsable / past; card coloured by
// the WORSE window. Tooltip keeps the full detail.
assert(typeof hoursToReset === "function", "fleet.js must export hoursToReset");
assert(typeof minutesToReset === "function", "fleet.js must export minutesToReset");
function resetsInHours(h) {
  return new Date(Date.now() + h * 3600000).toISOString().replace("Z", "+00:00");
}
ok("minutesToReset/hoursToReset: whole units, null for null/past/garbage", function () {
  assert.strictEqual(hoursToReset(null), null);
  assert.strictEqual(minutesToReset(null), null);
  assert.strictEqual(hoursToReset("not a date"), null);
  assert.strictEqual(hoursToReset(resetsInHours(-2)), null, "past reset -> null");
  assert.strictEqual(hoursToReset(resetsInHours(17.4)), 17, "17.4h floors to 17");
  const m = minutesToReset(resetsInHours(2.5));
  assert(m === 150 || m === 149, "2.5h -> ~150 min, got " + m);
  assert.strictEqual(hoursToReset(resetsInHours(0.2)), 0, "<1h floors to 0");
});
ok("fmtReset: 'Xh Ym' under 48h, 'Xd Yh' at >= 48h, em-dash for null", function () {
  assert.strictEqual(fmtReset(null), "—");
  assert(/^17h (23|24)m$/.test(fmtReset(resetsInHours(17.4))), "17.4h -> 17h 24m, got " + fmtReset(resetsInHours(17.4)));
  assert(/^47h 5[0-9]m$/.test(fmtReset(resetsInHours(47.99))), "47.99h stays in h/m form");
  assert.strictEqual(fmtReset(resetsInHours(48.02)), "2d 0h", "48h boundary uses d/h form");
  assert.strictEqual(fmtReset(resetsInHours(3 * 24 + 5.5)), "3d 5h");
});
ok("card: weekly row carries bar + % + 'resets in'", function () {
  const h = poolChip({ pool: "musa2", pct_7d: 42, pct_5h: 10, updated_age_s: 30,
    resets_at: resetsInHours(17.4), resets_5h_at: resetsInHours(2.5) });
  assert(/poolcard/.test(h), "renders as a card");
  assert(/musa2/.test(h), "key name present");
  assert(/poolwin good[^>]*>[\s\S]*?poolwl">wk<[\s\S]*?width:42%[\s\S]*?<b>42%<\/b>[\s\S]*?resets in 17h (23|24)m/.test(h), "weekly row: " + h);
  assert(/title="[^"]*weekly resets in 17h (23|24)m/.test(h), "tooltip carries the weekly countdown");
});
ok("card: 5h row carries bar + % + 'window resets in Xh Ym'", function () {
  const h = poolChip({ pool: "Syed", pct_7d: 42, pct_5h: 43, updated_age_s: 30,
    resets_at: resetsInHours(100), resets_5h_at: resetsInHours(2.5) });
  assert(/poolwl">5h<[\s\S]*?width:43%[\s\S]*?<b>43%<\/b>[\s\S]*?window resets in 2h (29|30)m/.test(h), "5h row: " + h);
  assert(/title="[^"]*5h window resets in 2h (29|30)m/.test(h), "tooltip carries the 5h countdown");
  assert(/resets in 4d 4h/.test(h), "weekly at 100h -> 4d 4h");
});
ok("card: '—' when resets_at / resets_5h_at / pct_5h are null or past", function () {
  const h = poolChip({ pool: "Musa", pct_7d: 42, updated_age_s: 30 });
  assert(/>resets —</.test(h), "null resets_at -> 'resets —': " + h);
  assert(/window resets —</.test(h), "null resets_5h_at -> 'window resets —'");
  assert(/poolwl">5h<[\s\S]*?width:0%[\s\S]*?<b>—<\/b>/.test(h), "null pct_5h -> em-dash + empty bar");
  const h2 = poolChip({ pool: "Musa", pct_7d: 42, pct_5h: 0, updated_age_s: 30,
    resets_at: resetsInHours(-1), resets_5h_at: resetsInHours(-0.1) });
  assert(/>resets —</.test(h2) && /window resets —</.test(h2), "past -> '—'");
});
ok("card colour = WORSE of the two windows; status_7d shown", function () {
  const h = poolChip({ pool: "Syed", pct_7d: 20, pct_5h: 92, updated_age_s: 30, status_7d: "allowed",
    resets_at: resetsInHours(100), resets_5h_at: resetsInHours(1) });
  assert(/poolcard bad/.test(h), "5h at 92% makes the card bad: " + h);
  assert(/poolwin good[^>]*>[\s\S]*?poolwl">wk</.test(h), "weekly row keeps its own good level");
  assert(/poolwin bad[^>]*>[\s\S]*?poolwl">5h</.test(h), "5h row is bad");
  assert(/poolstatus">allowed</.test(h), "status_7d text shown");
  const w = poolChip({ pool: "Syed", pct_7d: 80, pct_5h: 5, updated_age_s: 30 });
  assert(/poolcard warn/.test(w), "weekly at 80% makes the card warn");
  const s = poolChip({ pool: "Syed", pct_7d: 95, pct_5h: 95, updated_age_s: 4000 });
  assert(/poolcard stale/.test(s) && !/poolcard bad/.test(s), "stale wins over level");
});
ok("runway warning logic unchanged alongside the reset rows", function () {
  const h = poolChip({ pool: "Syed", pct_7d: 80, updated_age_s: 30, pace: 3.0, projected_pct: 120,
    runway_days: 1.5, resets_at: resetsInDays(5) });
  assert(/poolrun warn/.test(h), "runway still flagged");
  assert(/resets in 4d 23h|resets in 5d 0h/.test(h), "weekly countdown present too: " + h);
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
