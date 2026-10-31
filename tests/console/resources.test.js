// Node test (no deps) for the machines-resources panel (op#36664 — /resources
// page + /api/resources). Same vm-sandbox idiom as status_board.test.js. Run:
//   node tests/console/resources.test.js
//
// LOCKS:
//  - fmtAge/esc/tokenFromHash: identical semantics to every other console
//    page (status/asks/docs/media).
//  - isStale: 15-min (3 missed 5-min collector cycles) threshold.
//  - measure/pct1/c1: null -> "could not measure" (never a frozen number
//    painted as live); a real value (incl. 0) formats normally.
//  - thermalLabel: Mini's pmset thermal reading is always labeled
//    "(pmset estimate)", never presented as a direct sensor temperature.
//  - miniRows/gzbRows/cubeashtRows: per-host field mapping, including the
//    cubeasht Windows-host-vs-WSL split and the gzb steal-time
//    "could not measure" placeholder (field not collected yet).
//  - hostCard: a failed sample (ok:false) shows COULD NOT MEASURE with the
//    stored error, never metrics; a stale-but-ok sample shows the STALE pill.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const STATIC = path.join(__dirname, "..", "..", "nervous_system", "console", "static");

function makeEl() {
  return {
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    style: {}, dataset: {}, value: "",
    setAttribute() {}, removeAttribute() {}, getAttribute() { return null; },
    addEventListener() {}, removeEventListener() {},
    appendChild() {}, removeChild() {}, remove() {},
    querySelector() { return null; }, querySelectorAll() { return []; },
    innerHTML: "", textContent: "",
  };
}

function loadPage(file, opts) {
  opts = opts || {};
  const store = opts.localStorage ||
    { getItem() { return null; }, setItem() {}, removeItem() {} };
  const sess = opts.sessionStorage ||
    { getItem() { return null; }, setItem() {}, removeItem() {} };
  const loc = Object.assign(
    { reload() {}, href: "", pathname: "/", hash: "", search: "" }, opts.location || {});
  const els = opts.els || {};
  const documentStub = {
    getElementById(id) { return els[id] || makeEl(); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); }, addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const sandbox = {
    window: { addEventListener() {}, removeEventListener() {}, location: loc },
    document: documentStub,
    localStorage: store, sessionStorage: sess,
    location: loc, history: opts.history || { replaceState() {} },
    fetch: opts.fetch || function () { return new Promise(function () {}); },
    setInterval: opts.setInterval || function () { return 0; },
    clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, file), "utf8"), sandbox);
  return sandbox.module.exports;
}

const TOK = "wingmen-console-token-0123456789abcdef";

const api = loadPage("resources.js");
assert.ok(api, "resources.js must export module.exports for tests");

// ---- fmtAge / esc / isStale -------------------------------------------------
assert.strictEqual(api.fmtAge(null), "", "fmtAge(null) -> empty");
assert.strictEqual(api.fmtAge(30), "just now", "fmtAge(<60s) -> just now");
assert.strictEqual(api.fmtAge(5 * 60), "5m ago", "fmtAge minutes");
assert.strictEqual(api.esc("<a&b>"), "&lt;a&amp;b&gt;", "esc keeps the classic three");
assert.strictEqual(api.isStale(null), false, "no age -> not stale");
assert.strictEqual(api.isStale(800), false, "800s (<15m) -> not stale");
assert.strictEqual(api.isStale(901), true, "901s (>15m) -> stale");
console.log("fmtAge/esc/isStale: ok");

// ---- measure / pct1 / c1 : never present a missing reading as a number ----
assert.strictEqual(api.measure(null, api.pct1), null, "measure(null) -> null (caller renders 'could not measure')");
assert.strictEqual(api.measure(0, api.pct1), "0.0%", "measure(0) is a REAL reading, not missing");
assert.strictEqual(api.measure(41.2, api.pct1), "41.2%", "pct1 formats to one decimal");
assert.strictEqual(api.measure(62, api.c1), "62.0°C", "c1 formats Celsius with the degree sign");
console.log("measure/pct1/c1: ok");

// ---- thermalLabel: always flagged as a pmset ESTIMATE ----------------------
assert.strictEqual(api.thermalLabel(null), null, "no thermal object -> null (probe failed)");
assert.strictEqual(api.thermalLabel({ throttled: false }), "normal (pmset estimate)",
  "healthy reading names the estimate");
assert.strictEqual(api.thermalLabel({ throttled: true }), "THROTTLED (pmset estimate)",
  "throttled reading still names the estimate");
console.log("thermalLabel: ok");

// ---- per-host row builders: field mapping + labeling -----------------------
const miniHtml = api.miniRows({
  cpu_load_pct: 22.1, mem_real_used_pct: 41.2, swap_used_pct: 0,
  thermal: { recorded_fields: {}, throttled: false },
});
assert.ok(miniHtml.indexOf("41.2%") !== -1, "mini RAM uses mem_real_used_pct, not naive free%");
assert.ok(miniHtml.indexOf("pmset estimate") !== -1, "mini temp names the pmset estimate");
assert.ok(miniHtml.indexOf("0.0%") !== -1, "mini swap 0 renders as a real reading, not missing");

const miniNoThermHtml = api.miniRows({ cpu_load_pct: 10, mem_real_used_pct: 20 });
assert.ok(miniNoThermHtml.indexOf("could not measure") !== -1,
  "mini with no thermal key (probe failed) shows could-not-measure");

const gzbHtml = api.gzbRows({
  cpu_load_pct: 12, cpu_count: 4, mem_real_used_pct: 30, cpu_temp_c: 45,
});
assert.ok(gzbHtml.indexOf("4 vCPU") !== -1, "gzb CPU row names the vCPU count");
assert.ok(gzbHtml.indexOf("45.0") !== -1, "gzb temp renders the real reading");
assert.ok(gzbHtml.indexOf("could not measure") !== -1,
  "gzb steal-time (not collected yet) always shows could-not-measure");

const cubHtml = api.cubeashtRows({
  cpu_load_pct: 5, ram_used_pct: 38, cpu_temp_c: null,
  wsl: { mem_used_pct: 55, runner_service: "active" },
});
assert.ok(cubHtml.indexOf("Windows host") !== -1, "cubeasht labels the Windows-host section");
assert.ok(cubHtml.indexOf("WSL (ci-orchestrator)") !== -1, "cubeasht labels the WSL section, not '1 of 5'");
assert.ok(cubHtml.indexOf("55.0%") !== -1, "cubeasht WSL mem reads from the nested wsl sub-dict");
assert.ok(cubHtml.indexOf("could not measure") !== -1, "cubeasht null cpu_temp_c shows could-not-measure");

const cubNoRunnerHtml = api.cubeashtRows({
  cpu_load_pct: 1, ram_used_pct: 2, disk_c_free_pct: 3, cpu_temp_c: 4, gpu_temp_c: 5, nvme_temp_c: 6,
  wsl: { mem_used_pct: 10 },
});
const runnerRowHtml = cubNoRunnerHtml.slice(cubNoRunnerHtml.indexOf("Runner service"));
assert.ok(runnerRowHtml.indexOf('<span class="na">could not measure</span>') !== -1,
  "missing wsl.runner_service gets the same greyed .na styling as every other missing field, not bold .rv text");
console.log("miniRows/gzbRows/cubeashtRows: ok");

// ---- hostCard: failed sample vs stale sample vs healthy --------------------
const failedCard = api.hostCard(api.HOSTS[1], { gzb: { host: "gzb", ok: false, error: "ssh timeout", age_s: 920, metrics: {} } });
assert.ok(failedCard.indexOf("COULD NOT MEASURE") !== -1, "failed sample shows the error pill");
assert.ok(failedCard.indexOf("ssh timeout") !== -1, "failed sample surfaces the stored error text");
assert.strictEqual(failedCard.indexOf("%"), -1, "a failed sample NEVER renders a frozen metric number");

const staleCard = api.hostCard(api.HOSTS[0],
  { mini: { host: "mini", ok: true, error: null, age_s: 2000, metrics: { mem_real_used_pct: 41.2 } } });
assert.ok(staleCard.indexOf("STALE") !== -1, "an old-but-ok sample shows the STALE pill");
assert.ok(staleCard.indexOf("41.2%") !== -1, "a stale sample still shows its last real number, just marked stale");

const freshCard = api.hostCard(api.HOSTS[0],
  { mini: { host: "mini", ok: true, error: null, age_s: 30, metrics: { mem_real_used_pct: 41.2 } } });
assert.strictEqual(freshCard.indexOf("STALE"), -1, "a fresh sample carries no stale pill");
assert.strictEqual(freshCard.indexOf("COULD NOT MEASURE"), -1, "a fresh ok sample carries no error pill");
console.log("hostCard: ok");

console.log("resources.js: all checks passed");
