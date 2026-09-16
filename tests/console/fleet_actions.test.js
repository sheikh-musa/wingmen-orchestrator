// Node test (no deps) for fc-v63 — lane actions + typed-confirm strip on the phone.
// Same `vm`-sandbox idiom as fleet_keyroll.test.js. Run:
//   node tests/console/fleet_actions.test.js
//
// LOCKS (Musa op#20684/20687/20692):
//  - NO window.prompt / window.confirm anywhere in fleet.js — iOS suppresses both in a
//    standalone PWA, which is exactly the phone the operator drives the console from.
//  - Retask / Boot / Stand-down are no longer "backend not yet wired" toasts; Boot and
//    Stand-down POST /api/lane-boot | /api/lane-down WITH a typed `confirm`.
//  - Recycle POSTs /api/reset with confirm == body (the hosted proxy requires it).
//  - Apply POSTs /api/apply-armed with the typed confirm; the "pending cai + Nazim" copy is gone.
//  - confirmTyped() arms ONLY when the typed text equals the target session exactly.
//  - Mute / Pin remain honest stubs (no backend route referenced for them).
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

let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }
const { confirmTyped, CONFIRM_COPY, resetBodyFor } = loadFleet();
assert(typeof confirmTyped === "function" && CONFIRM_COPY && typeof resetBodyFor === "function", "exports");

ok("no window.prompt / window.confirm CALL survives in fleet.js (iOS PWA suppresses both)", function () {
  // comment lines may mention them (they document WHY); code lines may not call them.
  const code = SRC.split("\n").filter(function (ln) { return !/^\s*\/\//.test(ln); }).join("\n");
  assert(!/window\.prompt\(/.test(code), "window.prompt( call found");
  assert(!/window\.confirm\(/.test(code), "window.confirm( call found");
});

ok("Retask / Boot / Stand-down are not 'not yet wired' toasts any more", function () {
  assert(!/backend not yet wired/.test(SRC), "stub toast still present");
  assert(/fetch\("\/api\/lane-" \+ action/.test(SRC) || /\/api\/lane-boot/.test(SRC), "lane-boot route missing");
  // the lane action payload carries the typed confirm
  assert(/JSON\.stringify\(\{ session: session, confirm: typed \}\)/.test(SRC), "lane action must send confirm");
  // retask hands off to the assign composer (the /api/assign rail), never a new endpoint
  assert(/function retaskLane\(/.test(SRC) && /\$\("assignAgent"\)/.test(SRC), "retask must reuse the ask composer");
});

ok("Recycle sends confirm == body; Apply sends the typed confirm; stale copy gone", function () {
  assert(/JSON\.stringify\(\{ body: body, confirm: body \}\)/.test(SRC), "reset must carry confirm");
  assert(/JSON\.stringify\(\{ session: session, kind: kind, confirm: typed\.trim\(\) \}\)/.test(SRC), "apply must carry confirm");
  assert(!/pending cai \+ Nazim/.test(SRC), "stale 'pending cai + Nazim' copy");
  assert(!/arm via Telegram first/.test(SRC), "stale bridge-arm copy");
});

ok("Mute / Pin stay honest stubs: no backend route for them", function () {
  assert(!/\/api\/(mute|pin)/.test(SRC));
  assert(/LABEL\[act\] \|\| act\) \+ ": no backend yet/.test(SRC));
});

ok("confirmTyped arms ONLY on an exact match of the target session", function () {
  function strip(sessionName, typed) {
    const inp = makeEl(); inp.value = typed;
    const fire = makeEl(); fire.disabled = true;
    const toggles = [];
    return {
      el: {
        querySelector(sel) { return sel === ".ci" ? inp : (sel === ".cf" ? fire : null); },
        getAttribute(n) { return n === "data-session" ? sessionName : null; },
        classList: { toggle(c, v) { toggles.push([c, v]); } },
      }, fire, toggles,
    };
  }
  let s = strip("cosem-tdu", "cosem-tdu");
  assert.strictEqual(confirmTyped(s.el), true); assert.strictEqual(s.fire.disabled, false);
  assert.deepStrictEqual(s.toggles[0], ["armed", true]);
  s = strip("cosem-tdu", "cosem-td");
  assert.strictEqual(confirmTyped(s.el), false); assert.strictEqual(s.fire.disabled, true);
  s = strip("cosem-tdu", "  cosem-tdu  ");        // whitespace tolerated (trim), nothing else
  assert.strictEqual(confirmTyped(s.el), true);
  s = strip("cosem-tdu", "COSEM-TDU");            // case is NOT tolerated
  assert.strictEqual(confirmTyped(s.el), false);
});

ok("CONFIRM_COPY covers every typed-confirm action; stand-down is the 'bad' tier", function () {
  ["boot", "standdown", "apply-token", "apply-model"].forEach(function (k) { assert(CONFIRM_COPY[k] && CONFIRM_COPY[k].v, k); });
  assert.strictEqual(CONFIRM_COPY.standdown.cls, "bad");
  assert.strictEqual(CONFIRM_COPY.boot.cls, "");
});

ok("resetBodyFor stays singleton-only (worker lanes never map to a reset body)", function () {
  assert.strictEqual(resetBodyFor("orch-console"), "nazim");
  assert.strictEqual(resetBodyFor("cai"), "cai");
  assert.strictEqual(resetBodyFor("cc-orchestrator"), "hub");
  assert.strictEqual(resetBodyFor("cc-cosem-tdu-1"), "");
});

console.log("fleet_actions.test.js: " + passed + " passed");
