// Node test (no deps) -- the per-lane TOKEN control is a label, not a control.
// Run: node tests/console/fleet_token_controls_removed.test.js
//
// LOCKS (Musa's Aug question; orch-console GO bus #89062, 2026-11-01):
//  - The per-lane token dropdown is GONE from both /fleet (fleet.js) and the
//    legacy /lanes page (lanes.js). It offered a pointer write the operator could
//    never apply per-lane, so it was a control that could not act.
//  - Its Preview/Apply pair is gone with it. The token GROUP is still SHOWN
//    ("Token · <group> group" / "Token · all lanes") -- showing is not offering.
//  - Per-lane MODEL controls stay exactly as they were: they DO act.
//  - Server endpoints are untouched; this is a client-side trim only, so a
//    future re-introduction has to be a deliberate edit that updates this file.
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const STATIC = path.join(__dirname, "..", "..", "nervous_system", "console", "static");
const FILES = ["fleet.js", "lanes.js"];

const src = Object.fromEntries(
  FILES.map((f) => [f, fs.readFileSync(path.join(STATIC, f), "utf8")])
);

// ── the dead control must not come back by copy-paste ──────────────────────
for (const f of FILES) {
  // Markup the renderer emits for a token control. The armed-confirm copy table
  // keeps its generic "apply-token" entry (v: "Apply token") on purpose -- it is
  // reachable only from a button, and no button renders it any more; removing the
  // copy too would be scope creep on a client-side trim.
  for (const dead of ['data-kind="token"', 'data-apply="token"', 'data-armapply="token"', ">Preview token<", ">Apply token<"]) {
    assert.ok(!src[f].includes(dead), `${f}: per-lane token control "${dead}" must not be rendered`);
  }
}

// ── what remains: the label, and the model controls that really act ────────
for (const f of FILES) {
  assert.ok(src[f].includes("Token · "), `${f}: the static token-group label must stay`);
  assert.ok(src[f].includes('data-kind="model"'), `${f}: per-lane model select must stay`);
  assert.ok(src[f].includes('data-apply="model"'), `${f}: model Preview must stay`);
  assert.ok(src[f].includes('data-armapply="model"'), `${f}: model Apply must stay`);
}

// ── the group switch in the dock is the live token control -- untouched ────
assert.ok(src["fleet.js"].includes("/api/switch-all"), "fleet.js: the dock group switch must stay");

// ── the handlers stay generic (kind-agnostic), so no dangling token path ───
assert.ok(src["fleet.js"].includes("previewApply(el.dataset.session, el.dataset.apply)"),
  "fleet.js: delegated apply handler must stay generic");
assert.ok(src["fleet.js"].includes("setPointer(el.dataset.session, el.dataset.kind, el.value)"),
  "fleet.js: delegated pointer handler must stay generic");

console.log("ok — per-lane token control removed, label + model controls intact");
