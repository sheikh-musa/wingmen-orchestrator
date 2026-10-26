#!/usr/bin/env python3
"""Render-harness for the fc-v85 /status page (content 1d1b896c74d6f3e7).

Renders status.html against a FROZEN REAL snapshot of the union payload
(_status-payload.json — both ledgers read read-only via DATABASE_URL, axes
derived by the shipped db.annotate_status_rows; identical to what
/api/status-board will serve once migration 119 lands) because the live
console at fc-v84 has no /api/status-board route yet. Same technique as
render_console_pages.sh: stub fetch injected, script src flipped to file://,
then Playwright at true mobile (iPhone 13) + 1440 desktop.

Usage: .venv/bin/python3 reports/console-deploy/1d1b896c74d6f3e7/render-harness.py
"""
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[2]
STATIC = REPO / "nervous_system" / "console" / "static"

payload = (HERE / "_status-payload.json").read_text()
stub = (
    '<script>localStorage.setItem("console_token","render");'
    'window.fetch=function(u,o){'
    'return Promise.resolve({ok:true,status:200,'
    'json:function(){return Promise.resolve(%s);}});};'
    "</script>"
) % payload

html = (STATIC / "status.html").read_text()
html = html.replace('src="/static/status.js"', stub + '<script src="file://%s/status.js"></script>' % STATIC)
(HERE / "_status-harness.html").write_text(html)

PW = [
    pathlib.Path.home() / "wingmen" / "orchestrator" / ".venv" / "bin" / "python3",
    pathlib.Path.home() / "wingmen" / "orchestrator" / "scripts" / "render_console_playwright.py",
]
# Mobile: the sanctioned device-emulating renderer (op#12477).
r = subprocess.run([str(PW[0]), str(PW[1]), str(HERE / "_status-harness.html"),
                    str(HERE / "status-mobile.png"), "iPhone 13"],
                   env={"PATH": "/usr/bin:/bin", "PLAYWRIGHT_BROWSERS_PATH":
                        str(pathlib.Path.home() / "wingmen" / ".playwright")},
                   capture_output=True, text=True)
print("iPhone 13 -> status-mobile.png rc=", r.returncode)
if r.returncode != 0:
    print(r.stdout[-2000:], r.stderr[-2000:])
    sys.exit(1)

# Desktop: render_console_playwright.py is DEVICE-ONLY (no desktop mode) —
# inline a 1440x900 context here so the desktop PNG is a real desktop capture,
# not a mislabeled mobile one (reference: render-script-device-only trap).
inline = r'''
import pathlib, sys
from playwright.sync_api import sync_playwright
here = pathlib.Path(sys.argv[1])
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
    pg.goto(here.joinpath("_status-harness.html").as_uri(), wait_until="networkidle")
    pg.wait_for_timeout(1200)
    here.joinpath("status-desktop.png").write_bytes(pg.screenshot(full_page=False))
    b.close()
print("desktop ok")
'''
r2 = subprocess.run([str(PW[0]), "-c", inline, str(HERE)],
                    env={"PATH": "/usr/bin:/bin", "PLAYWRIGHT_BROWSERS_PATH":
                         str(pathlib.Path.home() / "wingmen" / ".playwright")},
                    capture_output=True, text=True)
print("Desktop 1440 -> status-desktop.png rc=", r2.returncode)
if r2.returncode != 0:
    print(r2.stdout[-2000:], r2.stderr[-2000:])
    sys.exit(1)
print("OK — eyeball:", HERE / "status-mobile.png", "+", HERE / "status-desktop.png")
