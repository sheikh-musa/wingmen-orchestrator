// Node test (no deps) for the P1 asks-board rework (op#33598, console-revamp
// lane): coarse board_status tabs, topic chips, needs-Musa flag predicate.
// Same `vm`-sandbox idiom as fleet_governance.test.js. Run:
//   node tests/console/asks_board.test.js
//
// LOCKS:
//  - TOPIC_ORDER is exactly the brief's five topics, chip order for render.
//  - splitByBoardStatus partitions OPEN rows by row.board_status; a missing
//    board_status defaults to truly_outstanding (never dropped, never "other").
//  - topicCounts tallies rows by row.topic (missing -> other) and returns ALL
//    five keys, 0-filled, in TOPIC_ORDER.
//  - needsMusa is true for waiting_on_operator rows AND for the structured
//    needs_operator_decision flag (arrives with the pending migration) so the
//    migration is a data change, not a rework.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "..", "nervous_system", "console", "static", "asks.js"), "utf8");

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
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
  };
}

function loadAsks() {
  const documentStub = {
    getElementById() { return makeEl(); }, querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); }, addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const windowStub = {
    addEventListener() {}, removeEventListener() {}, location: { reload() {}, href: "", pathname: "/" },
    scrollY: 0, devicePixelRatio: 1,
    matchMedia() { return { matches: false, addEventListener() {}, addListener() {} }; },
    navigator: { serviceWorker: { register() { return Promise.resolve(); } }, userAgent: "test" },
  };
  const sandbox = {
    window: windowStub, document: documentStub,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    navigator: windowStub.navigator, location: windowStub.location,
    fetch() { return new Promise(function () {}); },
    setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    requestAnimationFrame() { return 0; }, cancelAnimationFrame() {},
    AbortController, AbortSignal, URLSearchParams, console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(SRC, sandbox);
  return sandbox.module.exports;
}

const api = loadAsks();

// JSON-compare (not deepStrictEqual): the helpers run inside a vm realm whose
// Array/Object prototypes differ from this realm's — deepStrictEqual is
// prototype-strict and false-fails on identical values.
assert.strictEqual(
  JSON.stringify(api.TOPIC_ORDER),
  JSON.stringify(["adcda-schedule", "infra", "cosem", "irsyad", "other"]));

const open = [
  { id: 1, board_status: "in_progress", topic: "infra" },
  { id: 2, board_status: "truly_outstanding", topic: "irsyad" },
  { id: 3, topic: "other" },  // missing board_status -> truly_outstanding
];
const split = api.splitByBoardStatus(open);
assert.strictEqual(
  JSON.stringify(split.truly_outstanding.map(function (r) { return r.id; })), "[2,3]");
assert.strictEqual(
  JSON.stringify(split.in_progress.map(function (r) { return r.id; })), "[1]");

assert.strictEqual(
  JSON.stringify(api.topicCounts(open)),
  JSON.stringify({ "adcda-schedule": 0, "infra": 1, "cosem": 0, "irsyad": 1, "other": 1 }));

assert.strictEqual(api.needsMusa({ waiting_on_operator: true }), true);
assert.strictEqual(api.needsMusa({ needs_operator_decision: true }), true);
assert.strictEqual(api.needsMusa({ waiting_on_operator: false }), false);

// --- P1 blocker (orch-console bus #75387): hero renders the plain-language
// operator_summary, NOT internal triage text — only the Needs-Musa hero must
// be Musa-readable; the other tabs keep fleet-internal text (askCard).
const heroA = {
  id: 9, text: "Ring-2 pool-workers OOM soak PASS@MemoryMax2048M",  // internal saga text
  operator_summary: "Decide what the events dashboard should track.",
  delegated_to: "cc-cosem-1", waiting_on_operator: false,
  needs_operator_decision: true, ask_surface: "operator",
  updated_age_s: 10, asked_age_s: 10, committed_date: null, chase_by: null,
};
const heroB = {
  id: 10, text: "plain triage line", operator_summary: null,
  delegated_to: null, waiting_on_operator: true,
  ask_surface: "operator", updated_age_s: 5, asked_age_s: 5,
  committed_date: null, chase_by: null,
};

assert.ok(api.heroCard, "heroCard must be exported");
// decision-first line wins; no lane code-name (delegated_to) leaks into the hero card
const ha = api.heroCard(heroA);
assert.ok(ha.indexOf(heroA.operator_summary) !== -1, "hero renders operator_summary");
assert.ok(ha.indexOf(heroA.text) === -1, "hero hides internal triage text when summary present");
assert.ok(ha.indexOf("cc-cosem-1") === -1, "no lane code-name in the hero card");
// fallback: unsummarised needs-Musa row still renders (its sanitized text)
const hb = api.heroCard(heroB);
assert.ok(hb.indexOf("plain triage line") !== -1, "hero falls back to text when no summary");
// escape hostile summary text (same esc() discipline as askCard)
const hEvil = api.heroCard(Object.assign({}, heroA, { operator_summary: "<img src=x onerror=alert(1)>" }));
assert.ok(hEvil.indexOf("<img") === -1, "operator_summary is escaped");
console.log("heroCard: " + "ok");

console.log("asks_board.test.js OK");