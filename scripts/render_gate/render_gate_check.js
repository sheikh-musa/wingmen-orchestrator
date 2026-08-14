#!/usr/bin/env node
// render_gate_check.js — Stage-0 (observe) render + responsiveness assert for a
// client share fragment. Renders the WRAPPED/published form (publish_share.sh's
// exact emission), NOT the raw source — that source-file test was the op#12996
// trap. Emits PNGs + a JSON verdict. OBSERVE mode: never throws / always exit 0;
// pass/fail is reported in the verdict, it does not block.
//
// Usage: node render_gate_check.js <fragmentPath> <slug> <outDir>
'use strict';
const fs = require('fs');
const path = require('path');
const { chromium, devices } = require('playwright-core');

// The EXACT wrapper publish_share.sh applies (keep in sync with publish_share.sh).
const WRAP_HEAD = '<!doctype html><html lang="en"><head><meta charset="utf-8">' +
  '<meta name="viewport" content="width=device-width, initial-scale=1">' +
  '<meta name="robots" content="noindex, nofollow"></head><body>';
const WRAP_TAIL = '</body></html>';

async function main() {
  const [fragmentPath, slug, outDir] = process.argv.slice(2);
  if (!fragmentPath || !slug || !outDir) {
    console.error('usage: render_gate_check.js <fragmentPath> <slug> <outDir>');
    process.exit(2);
  }
  fs.mkdirSync(outDir, { recursive: true });
  const fragment = fs.readFileSync(fragmentPath, 'utf8');
  const wrapped = WRAP_HEAD + '\n' + fragment + '\n' + WRAP_TAIL;
  const wrappedPath = path.join(outDir, 'wrapped.html');
  fs.writeFileSync(wrappedPath, wrapped);

  const verdict = { slug, fragment: fragmentPath, checks: {}, pngs: {}, pass: false, notes: [] };
  let browser;
  try {
    browser = await chromium.launch({ channel: 'chrome' });
    const iphone = devices['iPhone 13'];

    // ── mobile (iPhone 13): the responsiveness asserts ──
    const mctx = await browser.newContext({ ...iphone });
    const mpage = await mctx.newPage();
    await mpage.goto('file://' + wrappedPath, { waitUntil: 'networkidle' });
    const m = await mpage.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      compatMode: document.compatMode,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    // Asserts: responsive (clientWidth == device 390), standards mode (not quirks),
    // no horizontal overflow (the literal 1-word/cram collapse leaves scrollWidth > clientWidth).
    const responsive = m.clientWidth === 390;
    const standards = m.compatMode === 'CSS1Compat';
    const noOverflow = m.scrollWidth <= m.clientWidth + 2;
    verdict.checks.mobile = { ...m, responsive, standards, noOverflow };
    verdict.pngs.mobileTop = path.join(outDir, 'mobile-top.png');
    await mpage.screenshot({ path: verdict.pngs.mobileTop });
    await mpage.evaluate(() => window.scrollTo(0, 820));
    verdict.pngs.mobileMid = path.join(outDir, 'mobile-mid.png');
    await mpage.screenshot({ path: verdict.pngs.mobileMid });
    await mctx.close();

    // ── desktop (1280) ──
    const dctx = await browser.newContext({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 });
    const dpage = await dctx.newPage();
    await dpage.goto('file://' + wrappedPath, { waitUntil: 'networkidle' });
    verdict.pngs.desktop = path.join(outDir, 'desktop.png');
    await dpage.screenshot({ path: verdict.pngs.desktop, fullPage: true });
    await dctx.close();

    verdict.pass = responsive && standards && noOverflow;
    if (!responsive) verdict.notes.push(`mobile clientWidth ${m.clientWidth} != 390 (not responsive)`);
    if (!standards) verdict.notes.push(`compatMode ${m.compatMode} (quirks, not CSS1Compat)`);
    if (!noOverflow) verdict.notes.push(`horizontal overflow: scrollWidth ${m.scrollWidth} > clientWidth ${m.clientWidth}`);
  } catch (e) {
    verdict.notes.push('RENDER ERROR: ' + e.message);
    verdict.pass = false;
    verdict.renderError = true;   // in enforce stages this = fail-closed abort
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
  fs.writeFileSync(path.join(outDir, 'verdict.json'), JSON.stringify(verdict, null, 2));
  console.log(JSON.stringify(verdict));
  process.exit(0); // OBSERVE: never block
}
main();
