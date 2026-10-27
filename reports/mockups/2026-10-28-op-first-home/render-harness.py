#!/usr/bin/env python3
"""Mockup render harness (orch-console 78466, op#34896 — MOCKUPS ONLY, no build).

Renders the 3 home variants (2× iPhone-13 390 + 1× desktop 1280) and the 6
Fleet-Grove mock files (3 stills + 3 annotated sequences) to PNG, and DOM-asserts
each against the brief: top bar carries ONLY Asks + Fleet (no Lane manager, no
irsyad), waiting/shipped/in-progress shapes, grove state machines, and a global
no-jargon check on operator-visible copy. GLM lane asserts STRUCTURE — the
aesthetic eyeball belongs to orch-console, who forwards PNGs to Musa.
"""
import json
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]
OUT = pathlib.Path(__file__).resolve().parent
PY = REPO / ".venv" / "bin" / "python3"

MOBILE = {"width": 390, "height": 844, "mobile": True}
DESKTOP = {"width": 1280, "height": 800, "mobile": False}

# name -> (file, context, checks). Every check is a page-side JS expression.
PAGES = {
    "home-a-390": ("home-a-390.html", MOBILE, {
        "topbar two buttons only": "() => [...document.querySelectorAll('header .tbbtn')].map(a => a.textContent.trim().replace(/\\s+/g,' '))",
        "header has NO Lane manager / irsyad": "() => { const h = document.querySelector('header').textContent; return h.indexOf('Lane') < 0 && h.indexOf('irsyad') < 0; }",
        "asks pill == 15": "() => document.querySelector('.tbcnt').textContent",
        "one number 2.1d": "() => document.querySelector('.onenumber .big').textContent",
        "one number honest subline": "() => { const s = document.querySelector('.onenumber .sub').textContent; return s.indexOf('first 4 measured') >= 0 && s.indexOf('27 Oct') >= 0 ? s : 'MISSING HONESTY SUBLINE'; }",
        "shipped today 2": "() => [...document.querySelectorAll('.ship')].map(s => s.querySelector('.when').textContent)",
        "in-progress groups 5, first open rest collapsed": "() => [...document.querySelectorAll('details.grp')].map(d => d.open)",
        "group clients": "() => [...document.querySelectorAll('.grp summary .client')].map(c => c.textContent)",
        "wip rows carry lane+due": "() => { const w = document.querySelectorAll('.wip')[0]; return w.querySelectorAll('.lane,.due').length; }",
        "waiting rows 8": "() => document.querySelectorAll('.wait').length",
        "waiting header says 15 + all-15 link": "() => { const h = [...document.querySelectorAll('h2')].find(x => x.textContent.indexOf('Waiting on you') >= 0); return h.querySelector('.n').textContent + '/' + h.querySelector('.more').textContent; }",
        "fleet internals link": "() => document.querySelector('.fleetlink').textContent.trim().replace(/\\s+/g,' ')",
    }),
    "home-b-390": ("home-b-390.html", MOBILE, {
        "topbar two buttons only": "() => [...document.querySelectorAll('header .tbbtn')].map(a => a.textContent.trim().replace(/\\s+/g,' '))",
        "waiting stack 8 numbered": "() => [...document.querySelectorAll('.stack .item .num')].map(n => n.textContent)",
        "waiting header == 15": "() => document.querySelector('.herohead .n').textContent",
        "kpi trio": "() => [...document.querySelectorAll('.kpi .v')].map(v => v.textContent)",
        "shipped collapsed 2": "() => document.querySelectorAll('details.sgrp').length",
        "in-progress 5 rows with client+due": "() => [...document.querySelectorAll('.wrow')].map(w => w.querySelector('.cl').textContent + ':' + w.querySelector('.due').textContent)",
        "fleet internals link": "() => document.querySelector('.fleetlink').textContent.trim().replace(/\\s+/g,' ')",
    }),
    "home-1280": ("home-1280.html", DESKTOP, {
        "topbar": "() => [...document.querySelectorAll('header .tbbtn')].map(a => a.textContent.trim().replace(/\\s+/g,' '))",
        "two-column grid": "() => getComputedStyle(document.querySelector('.grid')).gridTemplateColumns.split(' ').length",
        "left: waiting stack 7": "() => document.querySelectorAll('.stack .item').length",
        "left: waiting header == 15": "() => document.querySelector('.herohead .n').textContent",
        "left: 2 kpis": "() => [...document.querySelectorAll('.kpi .v')].map(v => v.textContent)",
        "right: shipped 2": "() => document.querySelectorAll('.ship').length",
        "right: groups 5": "() => document.querySelectorAll('.grp').length",
        "right: fleet internals link": "() => document.querySelector('.fleetlink').textContent.trim().replace(/\\s+/g,' ')",
    }),
    "grove-a-desks": ("grove-a-desks.html", MOBILE, {
        "six desks": "() => document.querySelectorAll('.desk').length",
        "client + task + time on every desk": "() => { const d = document.querySelectorAll('.desk')[1]; return [d.querySelector('.client'), d.querySelector('.task'), d.querySelector('.time')].filter(Boolean).length; }",
        "one stuck, flagged as sample, fleet not a client": "() => { const d = document.querySelector('.desk.stuck'); if (!d) return 'NO STUCK DESK'; const f = d.querySelector('.flag').textContent; return f.indexOf('sample') >= 0 && d.querySelector('.client').textContent === 'fleet' ? f + '/' + d.querySelector('.client').textContent : 'STUCK DESK NOT SAMPLE/FLEET: ' + f; }",
        "one live, badged jade": "() => { const d = document.querySelector('.desk.live'); return d ? getComputedStyle(d.querySelector('.time .dot')).backgroundColor : 'NO LIVE DESK'; }",
        "one quiet": "() => document.querySelectorAll('.desk.quiet').length",
        "legend explains all four states": "() => [...document.querySelectorAll('.legend span b')].map(b => b.textContent)",
    }),
    "grove-a-seq": ("grove-a-seq.html", MOBILE, {
        "3 beats": "() => [...document.querySelectorAll('.frame .tag')].map(t => t.textContent)",
        "beat 2 slumps": "() => !!document.querySelector('.frame:nth-of-type(3) .desk.stuck')",
        "beat 3 live + chime": "() => { const f = document.querySelectorAll('.frame')[2]; return !!f.querySelector('.desk.live') + '/' + f.querySelector('.chime').textContent; }",
    }),
    "grove-b-parcel": ("grove-b-parcel.html", MOBILE, {
        "parcel shows the real ask LIVE at its real time": "() => { const s = document.querySelector('.parcel .s').textContent; return s.indexOf('live') >= 0 && s.indexOf('14:21') >= 0 ? s : 'NOT REAL LIVE STATE: ' + s; }",
        "parcel has the real ask title": "() => document.querySelector('.parcel .t').textContent",
        "badge not clipped by card": "() => { const b = document.querySelector('.parcel .s').getBoundingClientRect(); const c = document.querySelector('.parcel').getBoundingClientRect(); return (c.bottom - b.bottom) >= 2 && (b.top - c.top) >= 2 && (c.right - b.right) >= 0 && (b.left - c.left) >= 0 ? 'clear by ' + Math.round(c.bottom - b.bottom) + 'px' : 'CLIPPED: badge bottom margin ' + (c.bottom - b.bottom); }",
        "three states explained": "() => [...document.querySelectorAll('.chip .n')].map(n => n.textContent)",
        "tap card: status/asked/took/desk": "() => [...document.querySelectorAll('.tapcard .row .k')].map(k => k.textContent)",
        "tap card cites real took time": "() => { const t = [...document.querySelectorAll('.tapcard .row')].find(r => r.textContent.indexOf('5h 07m') >= 0); return t ? t.textContent.trim().replace(/\\s+/g,' ') : 'NO REAL TOOK TIME'; }",
    }),
    "grove-b-seq": ("grove-b-seq.html", MOBILE, {
        "3 beats": "() => [...document.querySelectorAll('.frame .tag')].map(t => t.textContent)",
        "parcel class progresses amber->indigo->jade": "() => [...document.querySelectorAll('.frame')].map(f => f.querySelector('.parc').className)",
        "beats carry the real timestamps": "() => { const w = [...document.querySelectorAll('.frame .when')].map(x => x.textContent); return w[0].indexOf('09:14') >= 0 && w[2].indexOf('14:21') >= 0 && w[2].indexOf('5h 07m') >= 0 ? w : 'TIMES NOT REAL: ' + w; }",
        "live beat has chime": "() => !!document.querySelectorAll('.frame')[2].querySelector('.chime')",
    }),
    "grove-c-replay": ("grove-c-replay.html", MOBILE, {
        "30-second replay framing": "() => document.querySelector('.clock').textContent.trim().split('\\n')[0]",
        "six day thumbs": "() => [...document.querySelectorAll('.thumb .t')].map(t => t.textContent)",
        "17:21 LIVE pin": "() => { const t = document.querySelector('.thumb.livepin'); return t ? t.querySelector('.t').textContent + ' ' + t.querySelector('.pin').textContent : 'NO LIVE PIN'; }",
        "now thumb distinct": "() => !!document.querySelector('.thumb.now')",
        "scrubber marks incl live": "() => [...document.querySelectorAll('.kmark')].map(k => k.textContent)",
        "knob sits on the live mark": "() => { const s = document.querySelector('.scrub'); const k = document.querySelector('.scrub .knob'); const m = [...document.querySelectorAll('.kmark')].find(x => x.textContent.indexOf('17:21') >= 0); if (!s || !k || !m) return 'MISSING PARTS'; const sw = s.getBoundingClientRect().width; const kx = k.getBoundingClientRect().left - s.getBoundingClientRect().left + k.getBoundingClientRect().width/2; const mx = m.getBoundingClientRect().left + m.getBoundingClientRect().width/2 - s.getBoundingClientRect().left; return Math.abs(kx - mx) <= 4; }",
        "readout names the headline": "() => { const r = document.querySelector('.readout').textContent; return r.indexOf('17:21') >= 0 && r.indexOf('verified') >= 0; }",
    }),
    "grove-c-seq": ("grove-c-seq.html", MOBILE, {
        "4 beats across the day": "() => [...document.querySelectorAll('.frame .tag')].map(t => t.textContent)",
        "beat 3 shows the slump": "() => !!document.querySelectorAll('.frame')[2].querySelector('.d.slump')",
        "beat 4 ends verified-live + chime": "() => { const f = document.querySelectorAll('.frame')[3]; return !!f.querySelector('.d.lit') + '/' + !!f.querySelector('.chime'); }",
    }),
}

# Operator copy must stay plain — none of the fleet's internal reference tokens.
JARGON_RE = r"op#|bus #|cc-[a-z]|CAI-\d|Retask|substrate"


def run_ctx(script: str) -> str:
    r = subprocess.run([str(PY), "-"], input=script, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("context script crashed: " + r.stderr[-800:])
    return r.stdout


def render(html: pathlib.Path, png: str, ctx_spec: dict) -> None:
    mobile = ctx_spec["mobile"]
    script = f"""
import asyncio

HTML = "{html}"
OUT = "{OUT / png}"
MOBILE = {mobile}

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        if MOBILE:
            dev = p.devices["iPhone 13"]
            ctx = await b.new_context(**dev)
        else:
            ctx = await b.new_context(viewport={{'width': 1280, 'height': 800}},
                                      device_scale_factor=2)
        pg = await ctx.new_page()
        await pg.goto("file://" + HTML, wait_until="networkidle")
        await pg.wait_for_timeout(1200)
        await pg.screenshot(path=OUT, full_page=True)
        await b.close()
        print("rendered {png}")

asyncio.run(main())
"""
    out = run_ctx(script)
    if "rendered" not in out:
        sys.exit(f"render failed for {png}: {out}")
    print(out.strip())


def assert_page(html: pathlib.Path, ctx_spec: dict, checks: dict) -> None:
    lines = ",\n".join(f'    "{n}": (() => {{ try {{ return ({e})(); }} '
                       f'catch (e) {{ return "ERR:" + e.message; }} }})()'
                       for n, e in checks.items())
    jargon = (f'"JARGON FREE": (() => {{ const m = document.body.innerText.match(/{JARGON_RE}/i); '
              f'return m ? "JARGON FOUND: " + m[0] : "clean"; }})()')
    lines += f',\n    {jargon}'
    body = "(() => ({\n" + lines + "\n}))()"
    mobile = ctx_spec["mobile"]
    script = f"""
import asyncio, json

BODY = {body!r}
HTML = "{html}"
MOBILE = {mobile}

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch(channel="chrome")
        if MOBILE:
            dev = p.devices["iPhone 13"]
            ctx = await b.new_context(**dev)
        else:
            ctx = await b.new_context(viewport={{'width': 1280, 'height': 800}},
                                      device_scale_factor=2)
        pg = await ctx.new_page()
        await pg.goto("file://" + HTML, wait_until="networkidle")
        await pg.wait_for_timeout(800)
        res = await pg.evaluate(BODY)
        print(json.dumps(res, indent=1, ensure_ascii=False))
        await b.close()

asyncio.run(main())
"""
    out = run_ctx(script)
    print(out)
    try:
        res = json.loads(out)
    except json.JSONDecodeError:
        sys.exit(f"assert output not JSON for {html.name}")
    bad = [k for k, v in res.items() if str(v).startswith(("ERR:", "JARGON FOUND"))]
    if bad:
        sys.exit(f"ASSERT FAILURES on {html.name}: {bad}")


if __name__ == "__main__":
    only = sys.argv[1:] or list(PAGES)
    for name in only:
        fname, ctx_spec, checks = PAGES[name]
        html = OUT / fname
        print(f"== {name} ({fname}) ==")
        render(html, f"{name}.png", ctx_spec)
        assert_page(html, ctx_spec, checks)
    print(f"OK: {len(only)} mocks rendered + asserted (structure + no-jargon)")
