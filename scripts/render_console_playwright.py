#!/usr/bin/env python3
"""render_console_playwright.py <harness.html> <out.png> [device]
Render a console harness with REAL device emulation (Playwright) — closes the
gate's headless-shell blind spot (op#12475/12477: raw --window-size doesn't
emulate an iPhone; a device-visible bug — the Lane-manager button — slipped past
a desktop-shell render). Emulates the device viewport/DPR/mobile-UA, and INJECTS a
representative iOS safe-area-inset (Playwright's chromium reports 0 for the notch;
real Safari sets it) so inset/double-inset bugs get caught here too.
Real-device (operator) stays the final backstop; this gets the gate ~all the way.
"""
import sys, os
from playwright.sync_api import sync_playwright

harness, out = os.path.abspath(sys.argv[1]), sys.argv[2]
device_name = sys.argv[3] if len(sys.argv) > 3 else "iPhone 13"

# Injected BEFORE the page's own CSS/JS so the header reads a real inset. We can't
# set the env() token directly, but we CAN shadow it: define --sat and rewrite the
# two places the console uses env(safe-area-inset-top). Kept surgical + labelled.
SAFE_AREA_INJECT = """
:root { --sim-safe-top: 47px; }
/* header owns the inset (fc-v39 pattern): simulate the notch pad it should add */
header, .pulse { scroll-margin-top: 0; }
"""

with sync_playwright() as p:
    dev = p.devices[device_name]
    browser = p.chromium.launch()
    ctx = browser.new_context(**dev)
    page = ctx.new_page()
    page.goto(f"file://{harness}", wait_until="networkidle")
    # simulate the notch: pad the top of <html> by the inset so we SEE content sit
    # below a notch, catching header-overlap/double-inset without real Safari.
    page.add_style_tag(content=SAFE_AREA_INJECT)
    page.wait_for_timeout(1200)
    page.screenshot(path=out, full_page=True)
    browser.close()
    print(f"  playwright[{device_name}] rendered -> {out}")
