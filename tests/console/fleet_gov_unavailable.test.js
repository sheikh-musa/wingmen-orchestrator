// Node test (no deps) for console #90041 item 3 / op#37222.
// Run: `node tests/console/fleet_gov_unavailable.test.js`
//
// LOCKS: when /api/governance cannot be read, the Governance section must not
// keep showing the dead controls that arm a registry it never loaded -- the
// armed-key box, the confirm strip, the audit log. They are HIDDEN, never
// removed, and a later successful load restores them, so a console whose
// registry does load is unchanged and a transient failure self-heals.
//
// It also locks the important half of that bargain: hiding the second armed-key
// input costs no access, because the key lives in localStorage and the lane
// sheet's own field (#armedKey) writes the same slot.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

// FLEET_JS lets a negative control point this at a mutated copy (proving the
// assertions fail without the fix); CI always uses the real file.
const FLEET_JS = process.env.FLEET_JS || path.join(__dirname, "..", "..", "nervous_system", "console", "static", "fleet.js");

// Unlike the shared fleet test sandbox, this one hands back the SAME element for
// the same id, so hidden/restored state can actually be observed across calls.
function makeEl(id) {
  return {
    id: id,
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
  const els = {};
  function el(id) {
    if (!els[id]) els[id] = makeEl(id);
    return els[id];
  }
  const documentStub = {
    getElementById(id) { return el(id); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(""); },
    addEventListener() {}, removeEventListener() {},
    documentElement: makeEl("html"), body: makeEl("body"), readyState: "complete",
  };
  const windowStub = {
    addEventListener() {}, removeEventListener() {},
    location: { reload() {}, href: "", pathname: "/" },
    scrollY: 0, devicePixelRatio: 1,
    matchMedia() { return { matches: false, addEventListener() {}, addListener() {} }; },
    navigator: { serviceWorker: { register() { return Promise.resolve(); } }, userAgent: "test" },
  };
  const store = {};
  const sandbox = {
    window: windowStub, document: documentStub,
    localStorage: {
      getItem(k) { return Object.prototype.hasOwnProperty.call(store, k) ? store[k] : null; },
      setItem(k, v) { store[k] = String(v); },
      removeItem(k) { delete store[k]; },
    },
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
  // pre-create the elements the page ships in fleet.html, so a regression reads
  // as "govArmed still shown" rather than "undefined reading style"
  DEAD.concat(["gov", "govCount", "armedKey", "govArmedKey"]).forEach(el);
  return { mod: sandbox.module.exports, els, store };
}

let pass = 0;
function ok(name, fn) {
  try { fn(); pass++; console.log("  ok - " + name); }
  catch (e) { console.error("  FAIL - " + name + "\n        " + e.message); process.exitCode = 1; }
}

const DEAD = ["govArmed", "govConfirm", "govLog"];

ok("a failed registry load hides the dead controls AND says why", () => {
  const { mod, els } = loadFleet();
  mod.govUnavailable("governance registry unreachable");
  DEAD.forEach((id) => assert.strictEqual(els[id].style.display, "none", id + " still shown"));
  assert.ok(/registry unreachable/.test(els.gov.innerHTML), "#gov must carry the reason");
  // the reason stays visible -- hiding the whole section would hide the message
  assert.notStrictEqual(els.gov.style.display, "none");
});

ok("a 401 is a load failure too — same hiding", () => {
  const { mod, els } = loadFleet();
  mod.govUnavailable("unauthorized");
  DEAD.forEach((id) => assert.strictEqual(els[id].style.display, "none", id + " still shown"));
  assert.ok(/unauthorized/.test(els.gov.innerHTML));
});

ok("a successful load restores every control (nothing was deleted)", () => {
  const { mod, els } = loadFleet();
  mod.govUnavailable("governance registry unreachable");
  mod.renderGov({ projects: [], audit: [] });
  DEAD.forEach((id) => assert.strictEqual(els[id].style.display, "", id + " stayed hidden"));
});

ok("hiding costs no armed-key access: the key + the sheet's own field survive", () => {
  const { mod, els, store } = loadFleet();
  mod.govUnavailable("governance registry unreachable");
  // the element is hidden, not removed
  assert.ok(els.govArmed, "govArmed removed from the document");
  assert.strictEqual(els.govArmed.style.display, "none");
  // the lane sheet's field is a separate element on the same localStorage slot
  assert.ok(els.armedKey, "the sheet's armed-key field must still exist");
  assert.notStrictEqual(els.armedKey.style.display, "none", "must NOT hide the sheet's field");
  // and the slot itself is untouched by hiding
  store[mod.ARMED_KEY_LS] = "abc123";
  mod.govUnavailable("still down");
  assert.strictEqual(store[mod.ARMED_KEY_LS], "abc123", "hiding cleared the armed key");
});

console.log("fleet_gov_unavailable.test.js: " + pass + " passed");
