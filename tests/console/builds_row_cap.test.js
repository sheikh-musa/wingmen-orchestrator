// Node test (no deps) for the /builds row cap (console #90323 item 1).
// Same vm-sandbox idiom as builds_view.test.js. Run:
//   node tests/console/builds_row_cap.test.js
//
// WHY: the first quiet fold barely shrank /builds (19,061 -> 19,142 css px)
// because the fat was one OPEN repo group at 15 rows plus every active
// programme card painted full length -- not the finished/quiet material the
// first fold targeted. Console's budget is a phone PNG under ~5,000 device px.
//
// LOCKS the honesty of the cap: nothing is dropped. An open group paints at
// most MAX_ROWS_PER_GROUP rows and the remainder sits behind ONE nested toggle
// whose label carries the true count, so the operator can always see how much
// is hidden and reach every row with one tap.
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
    URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} },
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

const api = loadPage("builds.js");
const { capRows, foldBlock, MAX_ROWS_PER_GROUP } = api;

function rows(n) {
  const out = [];
  for (let i = 1; i <= n; i++) out.push('<div class="item">ask ' + i + "</div>");
  return out;
}

ok("exports the cap helpers", () => {
  assert.strictEqual(typeof capRows, "function");
  assert.strictEqual(typeof foldBlock, "function");
  assert.strictEqual(MAX_ROWS_PER_GROUP, 4, "the cap constant is part of the contract");
});

ok("a group at or under the cap paints every row and shows NO toggle", () => {
  [0, 1, MAX_ROWS_PER_GROUP].forEach((n) => {
    const html = capRows(rows(n));
    assert.strictEqual(html.indexOf("<details"), -1,
      n + " rows must not need a fold");
    assert.strictEqual((html.match(/class="item"/g) || []).length, n);
  });
});

ok("an over-cap group paints the first MAX rows and folds the rest behind ONE toggle", () => {
  const html = capRows(rows(15));
  assert.strictEqual((html.match(/class="item"/g) || []).length, 15,
    "every row must still be in the DOM -- the cap may hide, never drop");
  assert.strictEqual((html.match(/<details/g) || []).length, 1,
    "exactly one toggle per group, not one per hidden row");
  const folded = html.slice(html.indexOf("<details"));
  assert.strictEqual((folded.match(/class="item"/g) || []).length,
    15 - MAX_ROWS_PER_GROUP, "the folded half holds the remainder");
  const shown = html.slice(0, html.indexOf("<details"));
  assert.strictEqual((shown.match(/class="item"/g) || []).length, MAX_ROWS_PER_GROUP);
});

ok("the toggle label carries the TRUE count of hidden rows", () => {
  // the real caller (repoSectionHtml) passes the ask wording
  const html = capRows(rows(15), null, "more ask", "more asks");
  assert.ok(/11 more asks — show/.test(html),
    "15 rows, 4 shown -> the summary must say 11 more asks; got: " +
    html.slice(html.indexOf("<summary"), html.indexOf("</summary>") + 10));
});

ok("omitted labels still read as a true count, never 'undefined'", () => {
  // regression: the first cut printed "11 undefined — show" when a caller
  // left the labels off. An operator-facing toggle must always be readable.
  const html = capRows(rows(15));
  assert.ok(/11 more rows — show/.test(html), "default labels must be used");
  assert.strictEqual(html.indexOf("undefined"), -1);
});

ok("the remainder label is singular for exactly one hidden row", () => {
  const html = capRows(rows(MAX_ROWS_PER_GROUP + 1), null, "more ask", "more asks");
  assert.ok(/1 more ask — show/.test(html), "one hidden row must not read 'more asks'");
  assert.strictEqual(/more asks/.test(html), false);
});

ok("capRows is a general helper: custom max and labels are honoured", () => {
  const html = capRows(rows(5), 2, "more thing", "more things");
  assert.ok(/3 more things — show/.test(html));
  const shown = html.slice(0, html.indexOf("<details"));
  assert.strictEqual((shown.match(/class="item"/g) || []).length, 2);
});

ok("the nested fold is a real tap target (details/summary, count in the summary)", () => {
  const html = capRows(rows(6));
  assert.ok(/<details class="repo-group quiet">/.test(html),
    "the nested fold reuses the quiet <details> idiom");
  assert.ok(/<summary aria-expanded="false">/.test(html));
});

ok("foldBlock escapes its title (titles are untrusted provider text)", () => {
  const html = foldBlock('a"b<script>', "<p>x</p>");
  assert.ok(html.indexOf('a&quot;b&lt;script&gt;') !== -1, "title must be escaped");
  assert.ok(html.indexOf("<p>x</p>") !== -1, "inner HTML passes through untouched");
  assert.ok(/<details class="repo-group quiet">/.test(html), "default class is a quiet repo group");
});

ok("foldBlock honours an explicit class (the programme fold is NOT a repo group)", () => {
  const html = foldBlock("Programmes", "", "prog-fold quiet");
  assert.ok(/<details class="prog-fold quiet">/.test(html));
  assert.strictEqual(html.indexOf('class="repo-group'), -1,
    "the programme fold must not inherit repo-group styling or open-state logic");
});

ok("empty input yields empty output, never a bare toggle", () => {
  assert.strictEqual(capRows([]), "");
});

console.log("builds_row_cap.test.js: " + pass + " passed");
