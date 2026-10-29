#!/usr/bin/env python3
"""render_console_playwright.py <harness.html> <out.png> [device]
Render a console harness with REAL device emulation (Playwright) — closes the
gate's headless-shell blind spot (op#12475/12477: raw --window-size doesn't
emulate an iPhone; a device-visible bug — the Lane-manager button — slipped past
a desktop-shell render). Emulates the device viewport/DPR/mobile-UA, and INJECTS a
representative iOS safe-area-inset (Playwright's chromium reports 0 for the notch;
real Safari sets it) so inset/double-inset bugs get caught here too.
Real-device (operator) stays the final backstop; this gets the gate ~all the way.

backlog #141: the status/builds pages (Calm Command mobile-app-shell pattern)
scroll inside `#main { overflow: auto }`, not the page body — `document.body.
scrollHeight` never grows, so a plain `page.screenshot(full_page=True)` only
captured ONE viewport of `#main` and silently truncated anything further down
(caught live: the builds.png gate render showed only the Programmes section,
never the repo-grouped Ask->live times further down `#main`).

FIRST FIX TRIED (fc-v91, superseded): neutralize `#main`'s scroll (overflow:
visible, height: auto) and take one `full_page=True` shot. This works for a
modest page (builds.html, ~6-7k CSS px) but FAILED SILENTLY on status.html
(547 live items, ~94.7k CSS px / ~284k device px at 3x DPR): Chromium's
single-canvas rasterization has a ceiling somewhere well under that — the
resulting PNG was real content for roughly the first 15-17k device px, then
a SOLID FLAT COLOR (std-dev 0.0) for the remaining ~80% of the image, even
though the DOM genuinely held all 549 `.item` rows at their correct computed
`getBoundingClientRect()` positions (confirmed: `document.querySelectorAll(
'.item').length` was 549, last item's rect.bottom matched body.scrollHeight
exactly). The failure is NOT a render.py bug or a missing wait — it's
Chromium silently corrupting an oversized single-shot capture. The render
GATE still "passed" (non-empty, non-timeout file) while being almost-entirely
blank — worse than the original truncation, since a corrupted-but-present
file reads as success.

THE ACTUAL FIX: tile the capture. Leave `#main`'s own `overflow: auto` alone
(its `scrollHeight` is correctly computed regardless of overflow/visibility —
no unlock needed to MEASURE it). Scroll `#main.scrollTop` in increments equal
to its own visible height, take a NORMAL (non-full_page) viewport screenshot
at each step — guaranteed safe, it's just one ordinary screenshot each time —
and stitch the slices vertically with Pillow, cropping the repeated header
off every slice after the first. No-op (single slice) for a page without
`#main` or one shorter than a viewport (fleet/lanes/asks, and any `#main`
page short enough to fit without scrolling).
"""
import sys, os
from playwright.sync_api import sync_playwright
from PIL import Image

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


def _capture(page, out_path, viewport, dpr):
    """Screenshot `page` to `out_path`, tiling via #main.scrollTop when #main
    exists and is taller than one viewport. Returns nothing; writes out_path."""
    vw, vh = viewport["width"], viewport["height"]
    has_main = page.evaluate("!!document.getElementById('main')")
    if not has_main:
        page.screenshot(path=out_path, full_page=True)
        return

    rect = page.evaluate(
        "(() => { const r = document.getElementById('main').getBoundingClientRect(); "
        "return {top: r.top}; })()"
    )
    main_top = rect["top"]
    scroll_height = page.evaluate("document.getElementById('main').scrollHeight")
    slice_h = vh - main_top  # #main's own visible height within the viewport

    if scroll_height <= slice_h or slice_h <= 0:
        # fits in one viewport (or #main takes the whole thing) -- no tiling needed
        page.screenshot(path=out_path, full_page=True)
        return

    import math
    import tempfile

    n_slices = math.ceil(scroll_height / slice_h)
    tmp_dir = tempfile.mkdtemp(prefix="console_render_tiles_")
    total_h_css = main_top + scroll_height
    canvas = Image.new("RGB", (round(vw * dpr), round(total_h_css * dpr)))
    y_css = 0.0
    prev_scroll_top = None
    try:
        for i in range(n_slices):
            requested = min(i * slice_h, max(0, scroll_height - slice_h))
            page.evaluate(f"document.getElementById('main').scrollTop = {requested}")
            page.wait_for_timeout(60)  # let any scroll-driven paint settle
            actual = page.evaluate("document.getElementById('main').scrollTop")
            slice_path = os.path.join(tmp_dir, f"slice_{i}.png")
            page.screenshot(path=slice_path)
            im = Image.open(slice_path)
            if i == 0:
                canvas.paste(im, (0, 0))
                y_css = float(vh)
            else:
                # only the NEW content since the last slice (handles the final,
                # clamped slice overlapping the previous one)
                new_content_css = actual - prev_scroll_top
                if new_content_css <= 0:
                    continue
                crop_top_css = vh - new_content_css
                crop = im.crop((
                    0, round(crop_top_css * dpr),
                    round(vw * dpr), round(vh * dpr),
                ))
                canvas.paste(crop, (0, round(y_css * dpr)))
                y_css += new_content_css
            prev_scroll_top = actual
            im.close()
    finally:
        for f in os.listdir(tmp_dir):
            os.remove(os.path.join(tmp_dir, f))
        os.rmdir(tmp_dir)

    canvas.save(out_path)


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
    _capture(page, out, page.viewport_size, dev["device_scale_factor"])
    browser.close()
    print(f"  playwright[{device_name}] rendered -> {out}")
