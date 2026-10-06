#!/usr/bin/env python3
"""render_office.py <out-dir> — render the fc-v74 "virtual office" overlay to
PNGs for the operator to eyeball (op#26594). Builds a harness from fleet.html
(fetch stubbed with REAL /api/fleet data, fleet.js + office.js rewritten to
file:// so they load offline), opens the office via window.__officeAPI, and
shoots phone + desktop at the floor view and with a character panel open.

Mirrors scripts/render_console_pages.sh's stub so the office sees the same live
snapshot the console does — no new backend, no live server needed.
"""
import sys, os, json, pathlib, subprocess
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "reports/office-render")
OUT.mkdir(parents=True, exist_ok=True)
ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "nervous_system" / "console" / "static"

# --- pull real fleet data (falls back to {} if the console is down) ----------
tok = os.environ.get("CONSOLE_TOKEN", "")
data = "{}"
try:
    r = subprocess.run(
        ["curl", "-s", "-H", f"Authorization: Bearer {tok}",
         "http://100.83.21.34:8787/api/fleet"],
        capture_output=True, text=True, timeout=20)
    if r.stdout.strip():
        data = r.stdout.strip()
        json.loads(data)  # validate
except Exception as e:
    print(f"  (live fetch failed, rendering with empty data: {e})")

swver = ""
for line in (STATIC / "sw.js").read_text().splitlines():
    if "const VERSION" in line:
        swver = line.split('"')[1]; break

# --- build the harness -------------------------------------------------------
html = (STATIC / "fleet.html").read_text()
ver = json.dumps({"version": swver, "sha": "office-render"})
stub = (
    '<script>localStorage.setItem("console_token","render");'
    'var __D=%s;var __V=%s;'
    'window.fetch=function(u,o){u=String(u);'
    'var d=u.indexOf("/api/version")>=0?__V:(u.indexOf("/api/backlog")>=0?(__D.backlog||[]):__D);'
    'return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(d);},'
    'text:function(){return Promise.resolve(JSON.stringify(d));}});};'
    'if(window.EventSource){window.EventSource=function(){return{addEventListener:function(){},close:function(){}};};}'
    '</script>'
) % (data, ver)

# load the real local JS (both fleet.js and office.js) off file://
html = html.replace('src="/static/fleet.js"', f'src="file://{STATIC}/fleet.js"')
html = html.replace('src="/static/office.js"', f'src="file://{STATIC}/office.js"')
html = html.replace(f'<script src="file://{STATIC}/fleet.js"></script>', stub + f'<script src="file://{STATIC}/fleet.js"></script>', 1)
harness = (OUT / "_office-harness.html").resolve()
harness.write_text(html)

def shoot(page, name):
    page.wait_for_timeout(300)
    page.screenshot(path=str(OUT / name))
    size = (OUT / name).stat().st_size
    print(f"  shot {name} ({size} bytes)")

with sync_playwright() as p:
    browser = p.chromium.launch()

    # ---- PHONE (iPhone 13) ----
    phone = p.devices["iPhone 13"]
    ctx = browser.new_context(**phone)
    page = ctx.new_page()
    page.goto(f"file://{harness}", wait_until="networkidle")
    page.wait_for_function("window.__officeAPI && typeof window.__officeAPI.open==='function'", timeout=8000)
    n = page.evaluate("(function(){window.__officeAPI.open();return window.__officeAPI.agentCount();})()")
    print(f"  phone: office opened, {n} agents")
    page.wait_for_timeout(1600)  # let the snapshot land + a few animation frames
    shoot(page, "office-phone-floor.png")
    page.evaluate("window.__officeAPI.tapFirst()")
    page.wait_for_timeout(500)
    shoot(page, "office-phone-panel.png")
    ctx.close()

    # ---- DESKTOP (1440x900) ----
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    page = ctx.new_page()
    page.goto(f"file://{harness}", wait_until="networkidle")
    page.wait_for_function("window.__officeAPI && typeof window.__officeAPI.open==='function'", timeout=8000)
    n = page.evaluate("(function(){window.__officeAPI.open();return window.__officeAPI.agentCount();})()")
    print(f"  desktop: office opened, {n} agents")
    page.wait_for_timeout(1600)
    shoot(page, "office-desktop-floor.png")
    page.evaluate("window.__officeAPI.tapFirst()")
    page.wait_for_timeout(500)
    shoot(page, "office-desktop-panel.png")
    ctx.close()

    # ---- DARK (prove the dark palette; Playwright defaults to light) ----
    ctx = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2, color_scheme="dark")
    page = ctx.new_page()
    page.goto(f"file://{harness}", wait_until="networkidle")
    page.wait_for_function("window.__officeAPI && typeof window.__officeAPI.open==='function'", timeout=8000)
    page.evaluate("window.__officeAPI.open()")
    page.wait_for_timeout(1600)
    shoot(page, "office-desktop-dark-floor.png")
    page.evaluate("window.__officeAPI.tapFirst()")
    page.wait_for_timeout(500)
    shoot(page, "office-desktop-dark-panel.png")
    ctx.close()

    browser.close()

print(f"[render_office] OK — eyeball PNGs in {OUT}")
