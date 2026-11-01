// Node test (no deps) for the /status board cap (console #90323 item 2).
// Same vm-sandbox idiom as status_board.test.js. Run:
//   node tests/console/status_fold.test.js
//
// WHY: /status painted every open item flat. With 600 open items that measured
// 100,413 css px — 301,630 device px on an iPhone 13, i.e. ~119 screens.
//
// LOCKS the honesty of the cap: the board is already sorted darkest-first, so
// capping the paint is safe; but the hidden rows must stay in the DOM behind a
// toggle whose label states the true count, and the fold must never make the
// board look smaller than it is.
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
    closest() { return null; }, focus() {}, blur() {}, click() {},
    innerHTML: "", textContent: "",
    scrollTop: 0, scrollHeight: 0, offsetHeight: 0,
    getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, bottom: 0, right: 0 }; },
  };
}

function loadPage(file) {
  const loc = { reload() {}, href: "", pathname: "/", hash: "", search: "" };
  const documentStub = {
    getElementById() { return makeEl(); },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); },
    addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const sandbox = {
    window: { addEventListener() {}, removeEventListener() {}, location: loc },
    document: documentStub,
    localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    location: loc, history: { replaceState() {} },
    fetch() { return new Promise(function () {}); },
    setInterval() { return 0; }, clearInterval() {},
    setTimeout() { return 0; }, clearTimeout() {},
    IntersectionObserver: function () { this.observe = function () {}; this.unobserve = function () {}; this.disconnect = function () {}; },
    console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, file), "utf8"), sandbox,
    { filename: file });
  return sandbox.module.exports;
}

let pass = 0;
function ok(name, fn) {
  try { fn(); pass++; console.log("  ok - " + name); }
  catch (e) { console.error("  FAIL - " + name + "\n        " + e.message); process.exitCode = 1; }
}

const api = loadPage("status.js");
const { cappedCards, STATUS_MAX_RENDERED } = api;

// Stand-in cards: the real ones are interpolated strings from itemCard().
function cards(n) {
  const out = [];
  for (let i = 1; i <= n; i++) out.push('<div class="item" data-i="' + i + '">item ' + i + "</div>");
  return out;
}

ok("exports the cap helpers", () => {
  assert.strictEqual(typeof cappedCards, "function");
  assert.strictEqual(STATUS_MAX_RENDERED, 12, "the cap constant is part of the contract");
});

ok("a board at or under the cap paints every card and shows NO toggle", () => {
  [0, 1, STATUS_MAX_RENDERED].forEach((n) => {
    const html = cappedCards(cards(n));
    assert.strictEqual(html.indexOf("<details"), -1, n + " cards must not need a fold");
    assert.strictEqual((html.match(/class="item"/g) || []).length, n);
  });
});

ok("an over-cap board paints the first STATUS_MAX_RENDERED and folds the rest", () => {
  const html = cappedCards(cards(600));
  assert.strictEqual((html.match(/class="item"/g) || []).length, 600,
    "every item must stay in the DOM -- the cap may hide, never drop");
  assert.strictEqual((html.match(/<details/g) || []).length, 1,
    "one toggle for the section, not one per hidden card");
  const shown = html.slice(0, html.indexOf("<details"));
  assert.strictEqual((shown.match(/class="item"/g) || []).length, STATUS_MAX_RENDERED);
  const folded = html.slice(html.indexOf("<details"));
  assert.strictEqual((folded.match(/class="item"/g) || []).length,
    600 - STATUS_MAX_RENDERED);
});

ok("the toggle label states the TRUE number of hidden items", () => {
  const html = cappedCards(cards(600));
  assert.ok(/588 more open items — show/.test(html),
    "600 items, 12 shown -> the summary must say 588; got: " +
    html.slice(html.indexOf("<summary"), html.indexOf("</summary>") + 10));
});

ok("the label is singular for exactly one hidden item", () => {
  const html = cappedCards(cards(STATUS_MAX_RENDERED + 1));
  assert.ok(/1 more open item — show/.test(html));
  assert.strictEqual(/more open items/.test(html), false);
});

ok("the order survives the cap: the darkest stay visible, the tail is folded", () => {
  // paint() passes the already-sorted list; capping must not reorder it.
  const list = cards(20);
  const html = cappedCards(list);
  const shown = html.slice(0, html.indexOf("<details"));
  assert.ok(shown.indexOf("item 1") !== -1 && shown.indexOf("item 12") !== -1,
    "rows 1..12 are the painted head");
  assert.strictEqual(shown.indexOf("item 13"), -1, "row 13 belongs to the fold");
  const folded = html.slice(html.indexOf("<details"));
  assert.ok(folded.indexOf("item 13") !== -1 && folded.indexOf("item 20") !== -1);
});

ok("the fold is a real tap target (details.fold with a summary)", () => {
  const html = cappedCards(cards(20));
  assert.ok(/<details class="fold">/.test(html));
  assert.ok(/<summary aria-expanded="false">/.test(html));
});

ok("cappedCards is a general helper: a custom max is honoured", () => {
  const html = cappedCards(cards(10), 3);
  const shown = html.slice(0, html.indexOf("<details"));
  assert.strictEqual((shown.match(/class="item"/g) || []).length, 3);
  assert.ok(/7 more open items — show/.test(html));
});

ok("max=0 folds everything rather than painting nothing and losing the count", () => {
  const html = cappedCards(cards(5), 0);
  assert.strictEqual((html.match(/class="item"/g) || []).length, 5);
  assert.ok(/5 more open items — show/.test(html));
});

console.log("status_fold.test.js: " + pass + " passed");
