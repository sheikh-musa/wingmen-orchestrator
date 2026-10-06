#!/usr/bin/env python3
"""render_refresh.py <out-dir> — render fleet.html + lanes.html FROM THIS
WORKTREE's static (not the main repo like render_console_pages.sh hardcodes) so a
console visual-refresh branch can be eyeballed before the deploy gate. Phone +
desktop, viewport (top) + full-page. Same fetch stub as render_console_pages.sh.
"""
import sys, os, json, pathlib, subprocess
from playwright.sync_api import sync_playwright

OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "reports/refresh-after").resolve()
OUT.mkdir(parents=True, exist_ok=True)
ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "nervous_system" / "console" / "static"
tok = os.environ.get("CONSOLE_TOKEN", "")

def live(path):
    try:
        r = subprocess.run(["curl", "-s", "-H", f"Authorization: Bearer {tok}",
                            f"http://100.83.21.34:8787{path}"], capture_output=True, text=True, timeout=20)
        if r.stdout.strip():
            json.loads(r.stdout); return r.stdout.strip()
    except Exception as e:
        print(f"  (live {path} failed: {e})")
    return "{}"

fleet_data, tt_data = live("/api/fleet"), live("/api/token-truth")
swver = next((l.split('"')[1] for l in (STATIC / "sw.js").read_text().splitlines() if "const VERSION" in l), "")

def harness(page, data):
    html = (STATIC / f"{page}.html").read_text()
    ver = json.dumps({"version": swver, "sha": "refresh"})
    stub = ('<script>localStorage.setItem("console_token","render");var __D=%s;var __V=%s;'
            'window.fetch=function(u,o){u=String(u);'
            'var d=u.indexOf("/api/version")>=0?__V:(u.indexOf("/api/backlog")>=0?(__D.backlog||[]):__D);'
            'return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(d);},'
            'text:function(){return Promise.resolve(JSON.stringify(d));}});};'
            'if(window.EventSource){window.EventSource=function(){return{addEventListener:function(){},close:function(){}};};}'
            '</script>') % (data, ver)
    for s in ("fleet.js", "office.js", "lanes.js", "app.js"):
        html = html.replace(f'src="/static/{s}"', f'src="file://{STATIC}/{s}"')
    html = html.replace(f'<script src="file://{STATIC}/{page}.js"></script>',
                        stub + f'<script src="file://{STATIC}/{page}.js"></script>', 1)
    h = OUT / f"_{page}-harness.html"; h.write_text(html); return h

with sync_playwright() as p:
    b = p.chromium.launch()
    for page, data in (("fleet", fleet_data), ("lanes", tt_data)):
        h = harness(page, data)
        # phone top + full
        ctx = b.new_context(**p.devices["iPhone 13"]); pg = ctx.new_page()
        pg.goto(f"file://{h}", wait_until="networkidle"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / f"{page}-phone-top.png"), full_page=False)
        pg.screenshot(path=str(OUT / f"{page}-phone-full.png"), full_page=True)
        ctx.close()
        # desktop top
        ctx = b.new_context(viewport={"width": 1280, "height": 900}, device_scale_factor=2); pg = ctx.new_page()
        pg.goto(f"file://{h}", wait_until="networkidle"); pg.wait_for_timeout(1500)
        pg.screenshot(path=str(OUT / f"{page}-desktop-top.png"), full_page=False)
        ctx.close()
        print(f"  rendered {page}")
    b.close()
print(f"[render_refresh] OK -> {OUT}")
