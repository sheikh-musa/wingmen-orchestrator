// Node test (no deps) for op#20684 — "which lanes are on which key".
// Same `vm`-sandbox idiom as fleet_topbloat.test.js. Run:
//   node tests/console/fleet_keyroll.test.js
//
// LOCKS:
//  - the per-lane chip renders from the backend `pool` NICKNAME alone (the hosted
//    payload has no auth_fp), and from auth_fp when only that is present (local).
//  - poolRollup counts EVERY lane with a known key per pool (ASSIGNMENT, not liveness —
//    an offline/cross-host lane is still on its key; fc-v63 Musa fix) in Musa/musa2/Syed
//    order, unknown-key lanes as "unknown", and emits one .krchip per non-empty pool.
//  - fc-v64 (Musa op#20715): poolRollup(lanes, coordinators, active) counts the SINGLETONS
//    too — chip total = lanes + singletons; the split is visible (chip title + tapped hint).
//  - fc-v64 (Musa op#20716): mdlChip renders a model pill (never "null"; "~" when not the
//    live-process source); routineSummary/collapsedHtml carry the key + model split inline.
//  - CAI-RESP-1434: armedHeaders() rides X-Armed-Bearer from localStorage('fc.armedKey') only.
//  - an unknown fp NEVER leaks into a hosted chip: no pool + no fp = no chip.
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
    path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"), "utf8");
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

const { poolOf, tokChip, poolRollup, mdlChip, routineSummary, collapsedHtml, tileHtml, coordChip, armedHeaders, ARMED_KEY_LS } = loadFleet();
let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

assert(typeof poolOf === "function" && typeof tokChip === "function" && typeof poolRollup === "function",
  "fleet.js must export poolOf / tokChip / poolRollup");

ok("hosted row (pool only, no auth_fp) resolves + renders the pool chip", function () {
  assert.strictEqual(poolOf({ pool: "musa2" }), "musa2");
  const h = tokChip(undefined, poolOf({ pool: "musa2" }));
  assert(h.indexOf('class="tok musa2"') >= 0, h);
  assert(h.indexOf(">musa2<") >= 0, h);
});

ok("local row (auth_fp only) derives the same nickname + colour class", function () {
  assert.strictEqual(poolOf({ auth_fp: "68142948c003" }), "Musa");
  assert.strictEqual(poolOf({ auth_fp: "e1dfa48eec85" }), "musa2");
  assert.strictEqual(poolOf({ auth_fp: "582043088eae" }), "Syed");
  const h = tokChip("582043088eae", poolOf({ auth_fp: "582043088eae" }));
  assert(h.indexOf('class="tok syed"') >= 0 && h.indexOf(">Syed<") >= 0, h);
});

ok("backend pool wins over a stale/absent fp; unknown = no chip on hosted", function () {
  assert.strictEqual(poolOf({ pool: "Syed", auth_fp: "68142948c003" }), "Syed");
  assert.strictEqual(poolOf({ pool: "" }), "");
  assert.strictEqual(tokChip("", ""), "");                       // hosted unknown: nothing
  const h = tokChip("deadbeef0000", "");                          // local unknown fp: short id
  assert(h.indexOf('class="tok other"') >= 0 && h.indexOf("deadbe") >= 0, h);
});

ok("poolRollup counts ASSIGNED lanes per pool in canonical order, unknown last", function () {
  const lanes = [
    { bucket: "working", pool: "musa2" },
    { bucket: "idle", pool: "musa2" },
    { bucket: "working", pool: "Musa" },
    { bucket: "offline", pool: "Syed" },          // offline but on a key: COUNTED
    { bucket: "working", auth_fp: "582043088eae" }, // local-shaped row
    { bucket: "idle", auth_fp: "deadbeef0000" },    // unknown key -> "?"
    { bucket: "idle" },                              // no key info -> skipped
  ];
  const r = poolRollup(lanes, [], "");
  // Object.assign: r.counts was born in the vm realm (different Object prototype).
  assert.deepStrictEqual(Object.assign({}, r.counts), { musa2: 2, Musa: 1, Syed: 2, "?": 1 });
  assert.strictEqual(r.total, 6);
  const order = (r.html.match(/data-pool="([^"]+)"/g) || []).map(function (s) { return s.slice(11, -1); });
  assert.deepStrictEqual(order, ["Musa", "musa2", "Syed", "?"]);
  assert(r.html.indexOf('class="krchip tok musa"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok musa2"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok syed"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok other"') >= 0 && r.html.indexOf(">unknown<b>1</b>") >= 0, r.html);
  assert(r.html.indexOf('<span class="krl">keys</span>') === 0, r.html);
  assert(r.html.indexOf("<b>2</b>") >= 0 && r.html.indexOf("<b>1</b>") >= 0, r.html);
  assert(r.html.indexOf("deadbe") < 0, "raw fp must never appear in the roll-up");
});

ok("poolRollup marks the tapped pool + explains the filter; empty fleet = empty row", function () {
  const r = poolRollup([{ bucket: "working", pool: "Musa" }, { bucket: "working", pool: "Syed" }], [], "Syed");
  assert(r.html.indexOf('class="krchip tok syed on"') >= 0, r.html);
  assert(r.html.indexOf('class="krchip tok musa"') >= 0 && r.html.indexOf('tok musa on') < 0, r.html);
  assert(r.html.indexOf("showing Syed · 1 lane · 0 singletons · tap again for all") >= 0, r.html);
  const e = poolRollup([{ bucket: "idle" }, { bucket: "working", pool: "" }], [], "");
  assert.strictEqual(e.html, "");
  assert.strictEqual(e.total, 0);
});

ok("fc-v63 (Musa): an OFFLINE / cross-host lane on a key IS counted — assignment, not liveness", function () {
  // The bug: irsyad lanes on gzb read offline/DARK from the Mini and were dropped, so
  // musa2 vanished from "KEYS · Musa 3 · Syed 2" while a musa2 lane sat on the board.
  const lanes = [
    { bucket: "working", pool: "Musa" }, { bucket: "idle", pool: "Musa" }, { bucket: "working", pool: "Musa" },
    { bucket: "offline", pool: "musa2" }, { bucket: "offline", pool: "musa2" },
    { bucket: "working", pool: "Syed" }, { bucket: "idle", pool: "Syed" },
  ];
  const r = poolRollup(lanes, [], "");
  assert.deepStrictEqual(Object.assign({}, r.counts), { Musa: 3, musa2: 2, Syed: 2 });
  assert.strictEqual(r.total, 7);
  const order = (r.html.match(/data-pool="([^"]+)"/g) || []).map(function (s) { return s.slice(11, -1); });
  assert.deepStrictEqual(order, ["Musa", "musa2", "Syed"], "all three pools appear when each has >=1 assigned lane");
  assert(r.html.indexOf(">musa2<b>2</b>") >= 0, r.html);
  assert(r.html.indexOf("musa2: 2 lanes + 0 singletons") >= 0, "chip title carries the lane|singleton split");
});

ok("fc-v64 (Musa op#20715): SINGLETONS are counted — total = lanes + singletons, split visible", function () {
  const lanes = [
    { bucket: "working", pool: "Musa" }, { bucket: "idle", pool: "Musa" }, { bucket: "working", pool: "Musa" },
    { bucket: "offline", pool: "musa2" }, { bucket: "offline", pool: "musa2" },
    { bucket: "working", pool: "Syed" }, { bucket: "idle", pool: "Syed" },
  ];
  const coords = [
    { agent_id: "cc-orchestrator", short: "Hub", pool: "Musa" },
    { agent_id: "orch-console", short: "Nazim", pool: "Musa" },
    { agent_id: "cai", short: "cai", pool: "Musa" },
    { agent_id: "cc-fleet-health", short: "SRE", auth_fp: "68142948c003" },   // local-shaped: fp only
    { agent_id: "cc-finance", short: "Finance", pool: "Syed" },
    { agent_id: "cc-quality", short: "Quality" },                              // no key info -> skipped
  ];
  const r = poolRollup(lanes, coords, "");
  assert.deepStrictEqual(Object.assign({}, r.counts), { Musa: 7, musa2: 2, Syed: 3 });
  assert.deepStrictEqual(Object.assign({}, r.lanes), { Musa: 3, musa2: 2, Syed: 2 });
  assert.deepStrictEqual(Object.assign({}, r.singletons), { Musa: 4, Syed: 1 });
  assert.strictEqual(r.total, 12);
  assert(r.html.indexOf(">Musa<b>7</b>") >= 0, r.html);
  assert(r.html.indexOf('title="Musa: 3 lanes + 4 singletons"') >= 0, r.html);
  assert(r.html.indexOf('title="Syed: 2 lanes + 1 singleton"') >= 0, r.html);
  assert(r.html.indexOf('title="musa2: 2 lanes + 0 singletons"') >= 0, r.html);
  const order = (r.html.match(/data-pool="([^"]+)"/g) || []).map(function (s) { return s.slice(11, -1); });
  assert.deepStrictEqual(order, ["Musa", "musa2", "Syed"], "POOL_FP order kept");
  assert(r.html.indexOf('<span class="krhint">lanes + singletons</span>') >= 0, "untapped hint line");
  assert(r.html.indexOf("68142948") < 0, "raw fp must never appear");
  const t = poolRollup(lanes, coords, "Musa");
  assert(t.html.indexOf("showing Musa · 3 lanes · 4 singletons · tap again for all") >= 0, t.html);
  assert(t.html.indexOf('class="krchip tok musa on"') >= 0, t.html);
  // a singletons-only pool still gets a chip (0 lanes + N singletons)
  const s = poolRollup([], [{ pool: "musa2" }], "");
  assert(s.html.indexOf('title="musa2: 0 lanes + 1 singleton"') >= 0 && s.total === 1, s.html);
});

ok("fc-v64 (Musa op#20716): model chip — proc is plain, boot/registry get '~', unknown renders NOTHING", function () {
  assert.strictEqual(mdlChip(null, null), "");
  assert.strictEqual(mdlChip(undefined, "proc"), "");
  assert.strictEqual(mdlChip("", "boot"), "");
  const p = mdlChip("claude-opus-4-8", "proc");
  assert(p.indexOf('class="tok mdl"') >= 0 && p.indexOf(">opus-4-8<") >= 0 && p.indexOf("~") < 0, p);
  assert(p.indexOf('title="model (live process)"') >= 0, p);
  const b = mdlChip("claude-sonnet-5", "boot");
  assert(b.indexOf('class="tok mdl approx"') >= 0 && b.indexOf(">~sonnet-5<") >= 0 && b.indexOf("declared at boot") >= 0, b);
  const g = mdlChip("claude-fable-5-1", "registry");
  assert(g.indexOf(">~fable-5-1<") >= 0 && g.indexOf("registry default") >= 0, g);
  assert(mdlChip(null, "proc").indexOf("null") < 0);
  // the lane TILE carries key + model without expanding; a modelless lane has no model pill
  const tile = tileHtml({ agent_id: "cc-hifz-1", tmux_session: "hifz", bucket: "idle", pool: "Musa", model: "claude-opus-4-8", model_src: "proc" });
  assert(tile.indexOf('<div class="pills">') >= 0 && tile.indexOf(">Musa<") >= 0 && tile.indexOf(">opus-4-8<") >= 0, tile);
  const bare = tileHtml({ agent_id: "cc-x-1", tmux_session: "x", bucket: "idle", pool: "Syed" });
  assert(bare.indexOf("mdl") < 0 && bare.indexOf("null") < 0 && bare.indexOf(">Syed<") >= 0, bare);
  // the coordinator chip gets the same pills on a third line
  const cc = coordChip({ agent_id: "cai", short: "cai", tmux_session: "cai", ctx_pct: 12, ctx_level: "green", pool: "Musa", model: "claude-opus-4-8", model_src: "proc" });
  assert(cc.indexOf('<div class="ck2">') >= 0 && cc.indexOf(">Musa<") >= 0 && cc.indexOf(">opus-4-8<") >= 0 && cc.indexOf('data-pool="Musa"') >= 0, cc);
  const cn = coordChip({ agent_id: "cc-orchestrator", short: "Hub", tmux_session: "orch" });
  assert(cn.indexOf("ck2") < 0 && cn.indexOf("null") < 0, cn);
});

ok("fc-v64 (Musa op#20716): collapsed 'idle & fine' row carries key split + model split INLINE", function () {
  const routine = [
    { agent_id: "a1", bucket: "idle", pool: "Musa", model: "claude-opus-4-8" },
    { agent_id: "a2", bucket: "idle", pool: "Musa", model: "claude-opus-4-8" },
    { agent_id: "a3", bucket: "idle", pool: "Musa", model: "claude-sonnet-5" },
    { agent_id: "a4", bucket: "idle", pool: "Syed", model: "claude-opus-4-8" },
    { agent_id: "a5", bucket: "idle", pool: "Syed", model: "claude-opus-4-8" },
    { agent_id: "a6", bucket: "offline", pool: "musa2", model: "claude-opus-4-8" },
    { agent_id: "a7", bucket: "idle", pool: "musa2", model: "claude-sonnet-5" },
    { agent_id: "a8", bucket: "idle" },                                        // no key, no model: in neither split
  ];
  const s = routineSummary(routine);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.keys)), [["Musa", 3], ["musa2", 2], ["Syed", 2]]);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(s.models)), [["opus-4-8", 5], ["sonnet-5", 2]]);
  assert.strictEqual(s.names.length, 8);
  const h = collapsedHtml(routine);
  assert(h.indexOf('id="routineToggle"') >= 0 && h.indexOf("<b>8 lanes</b>") >= 0 && h.indexOf("idle &amp; fine") >= 0, h);
  const text = h.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
  assert(/8 lanes idle &amp; fine · Musa 3 · musa2 2 · Syed 2 · opus-4-8 5 · sonnet-5 2/.test(text), text);
  assert(h.indexOf('class="cnames">a1, a2, a3, a4, a5, a6…</div>') >= 0, "names on the second faint line, capped at 6");
  assert(h.indexOf("null") < 0);
  const one = collapsedHtml([{ agent_id: "solo", bucket: "idle" }]);
  assert(one.indexOf("<b>1 lane</b>") >= 0 && one.indexOf("csep") < 0, one);
});

ok("CAI-RESP-1434: armedHeaders reads localStorage('fc.armedKey') ONLY and rides X-Armed-Bearer", function () {
  assert.strictEqual(ARMED_KEY_LS, "fc.armedKey");
  assert.deepStrictEqual(Object.assign({}, armedHeaders()), {});   // sandbox localStorage is empty
  const src = fs.readFileSync(path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"), "utf8");
  // the armed key goes on exactly the two armed POSTs, never elsewhere / never in a body or URL
  const armedCalls = (src.match(/armedHeaders\(\)/g) || []).length;
  assert.strictEqual(armedCalls, 3, "one definition + exactly two call sites (reset, apply-armed)");
  assert(/fetch\("\/api\/reset", \{\n\s+method: "POST", headers: Object\.assign\(\{ "Content-Type": "application\/json" \}, authHeaders\(\), armedHeaders\(\)\)/.test(src), "reset must carry the armed key");
  assert(/fetch\("\/api\/apply-armed", \{\n\s+method: "POST", headers: Object\.assign\(\{ "Content-Type": "application\/json" \}, authHeaders\(\), armedHeaders\(\)\)/.test(src), "apply-armed must carry the armed key");
  assert(!/armedKey\(\)[^\n]*JSON\.stringify/.test(src) && !/console\.log\([^\n]*armed/i.test(src), "never in a payload, never logged");
  assert(/armed key required — set it in Lane manager/.test(src), "401 copy");
});

console.log("fleet_keyroll: " + passed + " passed");
