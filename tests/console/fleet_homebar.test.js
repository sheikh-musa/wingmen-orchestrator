// Node test (no deps) for the fc-v86 home de-noise (orch-console bus #78269,
// op#34848): the home "Your asks" card renders ONLY what waits on Musa, with a
// waiting-count badge (never the noisy "N open" total), and the persistent top
// bar's Asks badge mirrors pulse.asks_waiting. Same vm-sandbox idiom as
// fleet_pace.test.js, with an els registry so we can READ what renderAsks
// painted. Run: `node tests/console/fleet_homebar.test.js`
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
    innerHTML: "", textContent: "", value: "", className: "",
    scrollTop: 0, scrollHeight: 0, offsetHeight: 0,
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
  };
}

function loadFleet(els) {
  const src = fs.readFileSync(FLEET_JS, "utf8");
  const documentStub = {
    getElementById(id) { return (els && els[id]) || makeEl(); },
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

const WAITING = { id: 1, ask: "decide the ADCDA release window", delegated_to: "orch-console",
                  status: "waiting_on_musa", updated_age_s: 3600, asked_age_s: 7200 };

let passed = 0;
function ok(name, fn) { fn(); passed++; console.log("  ok - " + name); }

// ── the home card: waiting-only copy, honest badge ─────────────────────────
ok("waiting row renders a 'waiting on you' tag", function () {
  const els = { asks: makeEl(), asksCount: makeEl(), asksFresh: makeEl() };
  const { renderAsks } = loadFleet(els);
  renderAsks([WAITING]);
  assert(/waiting on you/.test(els.asks.innerHTML), "tag copy present");
  assert(/needs/.test(els.asks.innerHTML), "tag carries the needs (red) class");
  assert(/decide the ADCDA release window/.test(els.asks.innerHTML), "ask text present");
  assert(esc_ok(els.asks.innerHTML), "ask text is HTML-escaped");
});

ok("badge says N waiting on you — never the open total", function () {
  const els = { asks: makeEl(), asksCount: makeEl(), asksFresh: makeEl() };
  const { renderAsks } = loadFleet(els);
  renderAsks([WAITING, Object.assign({}, WAITING, { id: 2 })]);
  assert(/2 waiting on you/.test(els.asksCount.textContent), "count = waiting rows");
  assert(!/open/.test(els.asksCount.textContent), "no 'open' total on home");
});

ok("empty board: 'nothing waits on you' + badge says none — not '100 open'", function () {
  const els = { asks: makeEl(), asksCount: makeEl(), asksFresh: makeEl() };
  const { renderAsks } = loadFleet(els);
  renderAsks([]);
  assert(/nothing waits on you/i.test(els.asks.innerHTML), "empty-state copy");
  assert(/\/asks/.test(els.asks.innerHTML), "points at the full board");
  assert(/none waiting/i.test(els.asksCount.textContent), "badge none waiting");
  assert(els.asksFresh.style.display === "none", "freshbar hidden when empty");
});

ok("waiting meta says 'asked X ago' exactly once (no duplicate age line)", function () {
  const els = { asks: makeEl(), asksCount: makeEl(), asksFresh: makeEl() };
  const { renderAsks } = loadFleet(els);
  renderAsks([WAITING]);
  const times = els.asks.innerHTML.match(/asked /g) || [];
  assert(times.length === 1, "asked-age appears once, got " + times.length);
  assert(/2h/.test(els.asks.innerHTML), "asked age (7200s -> 2h) present");
});

// ── the persistent top bar: Asks badge mirrors pulse.asks_waiting ──────────
ok("topbar Asks badge mirrors pulse.asks_waiting", function () {
  const els = { tbAsksCount: makeEl() };
  const { renderTopbar } = loadFleet(els);
  renderTopbar({ asks_waiting: 11 });
  assert(els.tbAsksCount.textContent === "11", "count text");
  assert(/has-waits/.test(els.tbAsksCount.className), "waiting > 0 styles as attention");
  renderTopbar({ asks_waiting: 0 });
  assert(els.tbAsksCount.textContent === "0", "zero renders 0, not blank");
  assert(!/has-waits/.test(els.tbAsksCount.className), "zero is calm, not attention");
  renderTopbar({});
  assert(els.tbAsksCount.textContent === "—", "missing pulse renders an honest dash");
});

// tiny local esc check (the card must esc(), and our WAITING text has no
// metacharacters — prove the renderer did not wrap it in raw HTML tags)
function esc_ok(html) {
  return html.indexOf("<div class=\"atxt\">decide the ADCDA release window</div>") >= 0;
}

console.log("fleet_homebar: " + passed + " ok");
