#!/usr/bin/env node
// UI/UX screenshot capture — screenshots each route at mobile + desktop viewports
// so a terminal-blind agent (cc-uiux) can actually SEE the render and review it.
// Usage: node uiux_capture.mjs <baseUrl> <route1,route2,...> <outDir>
// Requires: playwright (chromium). Run from a repo that has playwright installed,
// or `npx playwright install chromium` first.
import { mkdirSync } from 'fs';
import { join } from 'path';
import { createRequire } from 'module';
// Resolve playwright from the CAPTURE REPO's node_modules (the cwd), not this
// script's location (the orchestrator has no playwright). Run from a repo that
// has playwright installed.
const require = createRequire(join(process.cwd(), 'package.json'));
const { chromium } = require('playwright');

const [baseUrl, routesCsv, outDir] = process.argv.slice(2);
if (!baseUrl || !routesCsv || !outDir) {
  console.error('usage: uiux_capture.mjs <baseUrl> <route1,route2,...> <outDir>');
  process.exit(2);
}
const routes = routesCsv.split(',').map(r => r.trim()).filter(Boolean);
const viewports = [
  { name: 'mobile', width: 390, height: 844 },   // iPhone 12/13/14
  { name: 'desktop', width: 1440, height: 900 },
];
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const manifest = [];
for (const vp of viewports) {
  const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  for (const route of routes) {
    const url = baseUrl.replace(/\/$/, '') + (route.startsWith('/') ? route : '/' + route);
    const slug = (route === '/' ? 'root' : route.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, ''));
    const file = join(outDir, `${vp.name}__${slug}.png`);
    try {
      await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
      await page.waitForTimeout(600);
      await page.screenshot({ path: file, fullPage: true });
      manifest.push({ viewport: vp.name, route, file, ok: true });
      console.error(`captured ${vp.name} ${route} -> ${file}`);
    } catch (e) {
      manifest.push({ viewport: vp.name, route, file, ok: false, error: String(e).slice(0, 200) });
      console.error(`FAILED ${vp.name} ${route}: ${String(e).slice(0, 160)}`);
    }
  }
  await ctx.close();
}
await browser.close();
console.log(JSON.stringify({ baseUrl, outDir, shots: manifest }, null, 2));
