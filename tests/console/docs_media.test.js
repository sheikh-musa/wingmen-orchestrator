// Node test (no deps) for the fc-v84 docs/media consistency pass (op#33598
// commitment #586, orch-console bus 76323): bring docs.js + media.js onto the
// same token + escaping invariants the rest of the console (asks.js fc-v82/83)
// already enforces. Same vm-sandbox idiom as asks_board.test.js. Run:
//   node tests/console/docs_media.test.js
//
// LOCKS:
//  - tokenFromHash exported from BOTH docs.js and media.js with identical
//    semantics to asks.js: reads a #t=<8+> fragment (floor = the REAL
//    CONSOLE_TOKEN length class, bus 76254), rejects <8, ignores #x=, no
//    percent-decode, null on no-hash.
//  - Deep-link flow: a valid #t= persists console_token BEFORE the token is
//    read (one tap from Telegram), then strips the fragment via
//    history.replaceState — never lingers in URL bar / history.
//  - esc() hardening: '"' is escaped (&quot;) — docs/media interpolate into
//    double-quoted attributes (data-search="…", alt="…"), so an unescaped
//    quote breaks OUT of the attribute (injection vector). Parity with
//    asks.js's 4-glyph esc().
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
  const documentStub = {
    getElementById() { return makeEl(); }, querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return makeEl(); }, addEventListener() {}, removeEventListener() {},
    documentElement: makeEl(), body: makeEl(), readyState: "complete",
  };
  const sandbox = {
    window: { addEventListener() {}, removeEventListener() {}, location: loc },
    document: documentStub,
    localStorage: store, sessionStorage: sess,
    location: loc, history: opts.history || { replaceState() {} },
    fetch() { return new Promise(function () {}); },
    setInterval() { return 0; }, clearInterval() {}, setTimeout() { return 0; }, clearTimeout() {},
    IntersectionObserver: function () { this.observe = function () {}; this.unobserve = function () {}; this.disconnect = function () {}; },
    URL: { createObjectURL() { return "blob:x"; }, revokeObjectURL() {} },
    console, module: { exports: {} },
  };
  sandbox.self = sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(STATIC, file), "utf8"), sandbox);
  return sandbox.module.exports;
}

const TOK = "wingmen-console-token-0123456789abcdef";
const REAL = "w1ngmen8";  // 8 chars — the production token's length class (bus 76254)
const PAGES = ["docs.js", "media.js"];
const apis = PAGES.map(function (f) { return loadPage(f); });

PAGES.forEach(function (f, i) {
  const api = apis[i];
  assert.ok(api, f + " must export module.exports for tests");
  assert.ok(api.tokenFromHash, f + " must export tokenFromHash");
  assert.strictEqual(api.tokenFromHash("#t=" + TOK), TOK, f + " reads a #t= fragment");
  assert.strictEqual(api.tokenFromHash("#t=" + REAL), REAL, f + " accepts the real-length (8-char) token");
  assert.strictEqual(api.tokenFromHash("#t=1234567"), null, f + " rejects a <8-char fragment");
  assert.strictEqual(api.tokenFromHash("#x=" + TOK), null, f + " ignores non-token fragments");
  assert.strictEqual(api.tokenFromHash(""), null, f + " no hash -> null");
  assert.strictEqual(api.tokenFromHash("#t=" + TOK + "%23x"), TOK + "%23x", f + " no percent-decode");
  // esc hardening: quotes must be escaped (attribute contexts)
  assert.strictEqual(api.esc('"'), "&quot;", f + " esc escapes double quotes");
  assert.strictEqual(api.esc("<a&b>"), "&lt;a&amp;b&gt;", f + " esc keeps the classic three");
  console.log(f + " helpers: ok");
});

// parity: both pages read fragments identically (the consistency this pass exists for)
const probes = ["#t=" + TOK, "#t=" + REAL, "#t=1234567", "#x=" + TOK, "", "#t=" + TOK + "%23x"];
probes.forEach(function (p) {
  assert.strictEqual(apis[0].tokenFromHash(p), apis[1].tokenFromHash(p),
    "docs.js and media.js must agree on fragment " + JSON.stringify(p));
});
console.log("cross-page parity: ok");

// flow: valid #t= persists BEFORE the token is read, fragment stripped exactly once
PAGES.forEach(function (f) {
  const wrote = [];
  const store = {
    getItem(k) { return wrote.length && k === "console_token" ? wrote[wrote.length - 1][1] : null; },
    setItem(k, v) { wrote.push([k, v]); }, removeItem() {},
  };
  const stripped = [];
  loadPage(f, {
    localStorage: store,
    location: { hash: "#t=" + TOK, reload() {}, href: "/x#t=" + TOK, pathname: "/", search: "" },
    history: { replaceState() { stripped.push(arguments); } },
  });
  assert.strictEqual(JSON.stringify(wrote), JSON.stringify([["console_token", TOK]]),
    f + " valid #t= persists the token exactly once");
  assert.strictEqual(stripped.length, 1, f + " fragment stripped exactly once");
  // the REAL-length token rides the deep link too (fc-v82 regression class, bus 76254)
  const wrote2 = [];
  loadPage(f, {
    localStorage: { getItem() { return null; }, setItem(k, v) { wrote2.push([k, v]); }, removeItem() {} },
    location: { hash: "#t=" + REAL, reload() {}, href: "", pathname: "/", search: "" },
  });
  assert.strictEqual(JSON.stringify(wrote2), JSON.stringify([["console_token", REAL]]),
    f + " 8-char production-token deep link persists");
  // a bad fragment never writes anything
  const wrote3 = [];
  loadPage(f, {
    localStorage: { getItem() { return null; }, setItem(k, v) { wrote3.push([k, v]); }, removeItem() {} },
    location: { hash: "#t=short", reload() {}, href: "", pathname: "/", search: "" },
  });
  assert.strictEqual(wrote3.length, 0, f + " short/garbage fragment writes nothing");
});
console.log("tokenFromHash flow: ok");

console.log("docs_media.test.js OK");
