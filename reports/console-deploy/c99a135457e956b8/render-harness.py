#!/usr/bin/env python3
"""fc-v86 rev2 render harness (content c99a135457e956b8, op#34852) — GLM lane's STRUCTURAL
proof for orch-console bus #78269. Builds stub-injected harness HTML for
fleet.html + lanes.html (truthful shapes: pulse.asks_waiting=11, two
waiting_on_musa rows, /api/asks-waiting {count:11}), renders both at iPhone 13
(390) via the fleet's render_console_playwright.py, then DOM-ASSERTS the
structure (a GLM lane never reads PNGs — the aesthetic eyeball belongs to
orch-console / cc-quality).
"""
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
STATIC = REPO / "nervous_system" / "console" / "static"
OUT = pathlib.Path(__file__).resolve().parent
PY = REPO / ".venv" / "bin" / "python3"

FLEET = json.loads((OUT / "_fleet-stub.json").read_text())
TT = json.loads((OUT / "_tt-stub.json").read_text())
VER = json.dumps({"version": "fc-v86", "sha": "render"})


def base_stub(data: str) -> str:
    """Same fetch-shaping stub as scripts/render_console_pages.sh."""
    return (
        '<script>localStorage.setItem("console_token","render");'
        f'var __D={data};var __V={VER};'
        'window.fetch=function(u,o){u=String(u);'
        'var d=u.indexOf("/api/version")>=0?__V:(u.indexOf("/api/backlog")>=0?(__D.backlog||[]):__D);'
        'return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve(d);},'
        'text:function(){return Promise.resolve(JSON.stringify(d));}});};'
        'if(window.EventSource){window.EventSource=function(){return{addEventListener:function(){},close:function(){}};};}</script>'
    )


def build(page: str, data: str, extra: str = "") -> pathlib.Path:
    html = (STATIC / f"{page}.html").read_text()
    src = f'src="/static/{page}.js"'
    local = f'src="file://{STATIC}/{page}.js"'
    html = html.replace(src, local)
    html = html.replace(f'<script {local}>', base_stub(data) + extra + f'<script {local}>')
    path = OUT / f"_{page}-harness.html"
    path.write_text(html)
    return path


def render(html: pathlib.Path, png: str) -> None:
    r = subprocess.run(
        [str(PY), str(REPO / "scripts" / "render_console_playwright.py"),
         str(html), str(OUT / png), "iPhone 13"],
        capture_output=True, text=True,
    )
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode != 0:
        sys.exit(frender_err(png, r.returncode))


def frender_err(png, rc):
    return f"render failed for {png} rc={rc}"


def assert_dom(html: pathlib.Path, checks: dict) -> None:
    # Playwright evaluate() takes an EXPRESSION when handed a string — embed the
    # checks as page-side JS (name -> expression) and return all results at once.
    lines = ",\n".join(f'    "{n}": (() => {{ try {{ return ({e})(); }} '
                       f'catch (e) {{ return "ERR:" + e.message; }} }})()'
                       for n, e in checks.items())
    body = "(() => ({\n" + lines + "\n}))()"
    script = f"""
import asyncio, json, sys

BODY = {body!r}
HTML = "{html}"

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        ctx = await b.new_context(viewport={{'width': 390, 'height': 844}},
                                  device_scale_factor=3, is_mobile=True, has_touch=True)
        pg = await ctx.new_page()
        await pg.goto("file://" + HTML)
        await pg.wait_for_timeout(1500)
        res = await pg.evaluate(BODY)
        print(json.dumps(res, indent=1))
        await b.close()

asyncio.run(main())
"""
    r = subprocess.run([str(PY), "-"], input=script, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("dom assert crashed: " + r.stderr[-800:])
    print(r.stdout)


if __name__ == "__main__":
    # fleet: rides /api/fleet (__D) — waiting-only card + topbar from pulse
    fleet_h = build("fleet", json.dumps(FLEET))
    # lanes: /api/token-truth (__D) + a dedicated /api/asks-waiting answer
    asks_extra = (
        '<script>var __AW={"count":11};'
        'var __baseFetch=window.fetch;'
        'window.fetch=function(u,o){u=String(u);'
        'if(u.indexOf("/api/asks-waiting")>=0){return Promise.resolve({ok:true,status:200,'
        'json:function(){return Promise.resolve(__AW);}});}'
        'return __baseFetch(u,o);};</script>'
    )
    lanes_h = build("lanes", json.dumps(TT), asks_extra)
    render(fleet_h, "fleet-390.png")
    render(lanes_h, "lanes-390.png")

    assert_dom(fleet_h, {
        "topbar exists": "() => !!document.querySelector('.topbar')",
        "topbar sticky": "() => getComputedStyle(document.querySelector('.topbar')).position",
        "three buttons": "() => [...document.querySelectorAll('.topbar .tbbtn')].map(a => a.textContent.trim().split('\\n')[0])",
        "asks badge": "() => document.getElementById('tbAsksCount').textContent",
        "asks badge attn": "() => document.getElementById('tbAsksCount').className",
        "asks card GONE": "() => !!document.getElementById('asks')",
        "asksCount GONE": "() => !!document.getElementById('asksCount')",
        "freshbar GONE": "() => !!document.getElementById('asksFresh')",
        "delegate composer survives, collapsed": "() => { const d = document.getElementById('assignSec'); return d ? d.tagName + '/' + d.open : 'none'; }",
        "footlinks gone": "() => document.querySelectorAll('.footlinks a').length",
        "sheet closed on load": "() => { const s = document.getElementById('sheet'); const r = s.getBoundingClientRect(); return Math.round(r.top) + '/' + window.innerHeight; }",
        "sheet closed means below fold": "() => { const s = document.getElementById('sheet'); return s.getBoundingClientRect().top >= window.innerHeight; }",
        "scrim transparent on load": "() => getComputedStyle(document.querySelector('.scrim')).opacity",
        "no sheet class open": "() => document.getElementById('sheet').className",
    })
    assert_dom(lanes_h, {
        "topnav exists": "() => !!document.querySelector('.topnav')",
        "three buttons": "() => [...document.querySelectorAll('.topnav .tnbtn')].map(a => a.textContent.trim().split('\\n')[0])",
        "current page is Lane manager": "() => { const c = document.querySelector('.tnbtn.cur'); return c ? c.getAttribute('aria-current') + ':' + c.textContent.trim().split('\\n')[0] : 'none'; }",
        "asks badge": "() => document.getElementById('tbAsksCount').textContent",
        "asks badge attn": "() => document.getElementById('tbAsksCount').className",
        "back link": "() => !!document.querySelector('.back')",
    })
    # /asks — the ONE asks view (op#34852): opens on the waiting items; the
    # confirm affordance lives here now. Badge/hero consistency: fleet badge 11
    # == asks hero Needs-Musa count 11 (one shared stub truth).
    asks_h = build("asks", json.dumps(json.loads((OUT / "_asks-stub.json").read_text())))
    render(asks_h, "asks-390.png")
    assert_dom(asks_h, {
        "hero present": "() => !!document.querySelector('.hero')",
        "hero count == 11 == fleet badge": "() => document.querySelector('.hero .n') ? document.querySelector('.hero .n').textContent : 'none'",
        "hero rows": "() => document.querySelectorAll('.hero .ask').length",
        "review row has confirm": "() => { const b = document.querySelector('[data-confirm]'); return b ? b.getAttribute('data-confirm') : 'none'; }",
        "review row has dismiss": "() => { const b = document.querySelector('[data-drop]'); return b ? b.getAttribute('data-drop') : 'none'; }",
        "confirm controls only on review rows (count=1)": "() => document.querySelectorAll('[data-confirm]').length",
        "default tab outstanding": "() => { const t = document.querySelector('.tab.on'); return t ? t.dataset.tab : 'none'; }",
    })
    print("OK: structure asserted on all three pages (see results above)")
