// Node test (no deps) for fc-v65 — per-project governance section (op#20702 Stage E).
// Same `vm`-sandbox idiom as fleet_actions.test.js. Run:
//   node tests/console/fleet_governance.test.js
//
// LOCKS:
//  - govCardHtml renders the 3 toggles per project: cai (data-to flips), money-clearance
//    (LOUD class when on), operators with internal/external tags + edit, channels + edit,
//    residency ack (never inferred).
//  - govStripCopy: money ON is the only strip that requires the acknowledgement phrase
//    (cls "bad"); money OFF / cai / operators / channels never do.
//  - govStripHtml: money ON carries the `.gack` input + the phrase; every strip carries a
//    reason input + the typed project confirm; no window.prompt / window.confirm.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const SRC = fs.readFileSync(path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js"), "utf8");

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
function loadFleet() {
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
  vm.runInNewContext(SRC, sandbox, { filename: "fleet.js" });
  return sandbox.module.exports;
}

const F = loadFleet();
assert.ok(typeof F.govCardHtml === "function" && typeof F.govStripCopy === "function" && typeof F.govStripHtml === "function");
assert.strictEqual(F.GOV_MONEY_ACK, "ENABLE MONEY CLEARANCE");

// no prompt/confirm CALLS anywhere (comments may mention them)
assert.ok(!/window\.(prompt|confirm)\(/.test(SRC), "window.prompt/confirm must not be called");

// ---- card: irsyad (cai off, money off, 2 operators, 1 channel)
const irsyad = { project: "irsyad", cai_enabled: false, money_clearance_enabled: false,
  operators: [{ name: "Shuq", chat_id: "605271890", internal: false }, { name: "Nazim", chat_id: "1", internal: true }],
  channels: ["-5330147776"], residency_ack_on_file: false, updated_by: "migration-063-seed", updated_at: new Date().toISOString() };
let h = F.govCardHtml(irsyad);
assert.ok(h.indexOf('class="gcard" data-project="irsyad"') >= 0, "not loud when money is off");
assert.ok(h.indexOf('data-field="cai_enabled" data-to="true"') >= 0, "cai OFF flips TO true");
assert.ok(h.indexOf('data-field="money_clearance_enabled" data-to="true"') >= 0, "money OFF flips TO true");
assert.ok(h.indexOf("cai OFF") >= 0 && h.indexOf("money-clearance OFF") >= 0);
assert.ok(h.indexOf("Shuq <i>605271890</i>") >= 0 && h.indexOf('gtag ext">external') >= 0 && h.indexOf('gtag">internal') >= 0);
assert.ok(h.indexOf('data-gact="edit" data-field="operators"') >= 0 && h.indexOf('data-gact="edit" data-field="channels"') >= 0);
assert.ok(h.indexOf("<i>-5330147776</i>") >= 0);
assert.ok(h.indexOf("none on file (never inferred)") >= 0);

// ---- card: money ON is LOUD; cai ON flips to false; no operators = fail-closed note
const loud = { project: "cosem", cai_enabled: true, money_clearance_enabled: true, operators: [], channels: [], residency_ack_on_file: true };
h = F.govCardHtml(loud);
assert.ok(h.indexOf('class="gcard money"') >= 0, "money on => loud card");
assert.ok(h.indexOf('moneyon"') >= 0 && h.indexOf("money-clearance ON") >= 0);
assert.ok(h.indexOf('data-field="cai_enabled" data-to="false"') >= 0);
assert.ok(h.indexOf("nobody can authorize") >= 0 && h.indexOf("DM-only") >= 0);
assert.ok(h.indexOf('gres on">residency ack: on file') >= 0);

// ---- escaping: a hostile project name never becomes markup
h = F.govCardHtml({ project: "<img src=x onerror=1>", operators: [{ name: "<b>", chat_id: "1", internal: false }], channels: [] });
assert.ok(h.indexOf("<img") < 0 && h.indexOf("&lt;img") >= 0 && h.indexOf("&lt;b&gt;") >= 0);

// ---- strip copy
let c = F.govStripCopy("irsyad", "money_clearance_enabled", true);
assert.strictEqual(c.cls, "bad"); assert.strictEqual(c.ack, true); assert.ok(/money-path/.test(c.note));
c = F.govStripCopy("irsyad", "money_clearance_enabled", false);
assert.strictEqual(c.ack, false);
["cai_enabled", "operators", "channels"].forEach(function (f) {
  assert.strictEqual(F.govStripCopy("irsyad", f, true).ack, false, f + " never needs the ack");
  assert.strictEqual(F.govStripCopy("irsyad", f, false).ack, false, f + " never needs the ack");
});
assert.strictEqual(F.govStripCopy("irsyad", "cai_enabled", false).cls, "warn");

// ---- strip html
let s = F.govStripHtml("irsyad", "money_clearance_enabled", true, false);
assert.ok(s.indexOf('class="cstrip gov bad"') >= 0 && s.indexOf('data-ack="1"') >= 0);
assert.ok(s.indexOf('class="gack"') >= 0 && s.indexOf("type ENABLE MONEY CLEARANCE") >= 0);
assert.ok(s.indexOf('class="greason"') >= 0 && s.indexOf('placeholder="type irsyad to arm"') >= 0);
assert.ok(s.indexOf('class="cf" disabled') >= 0, "fire button starts disarmed");
s = F.govStripHtml("irsyad", "cai_enabled", false, true);
assert.ok(s.indexOf('data-ack="0"') >= 0 && s.indexOf('class="gack"') < 0, "cai never asks for the money ack");
s = F.govStripHtml("irsyad", "operators", true, irsyad.operators);
assert.ok(s.indexOf('id="govOps"') >= 0 && (s.match(/class="oprow"/g) || []).length === 2 && s.indexOf('value="605271890"') >= 0);
assert.ok(s.indexOf('class="opadd"') >= 0);
s = F.govStripHtml("irsyad", "channels", true, ["-1", "-2"]);
assert.ok(s.indexOf('class="gch"') >= 0 && s.indexOf('value="-1, -2"') >= 0);

console.log("fleet_governance.test.js: all assertions passed");
