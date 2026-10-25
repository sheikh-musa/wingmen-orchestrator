#!/usr/bin/env python3
# fc-v84 render+DOM-assert harness for docs.html + media.html (op#33598 #586).
# Local static server + stubbed /api/* — never touches prod. PNGs land in the
# evidence dir for the cc-quality eyeball (GLM lane asserts structure only).
import base64, json, struct, threading, zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

STATIC = "/Users/sheikhmusa/wingmen/orchestrator.wt-console-revamp/nervous_system/console/static"
OUT = "/Users/sheikhmusa/wingmen/orchestrator.wt-console-revamp/reports/console-deploy/2a29d45623bee55b"
PORT = 8793
TOKEN = "wingmen-console-token-0123456789abcdef"

# tiny 120x90 jade PNG for thumbnails
def png_bytes():
    def chunk(t, d):
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xffffffff)
    ihdr = struct.pack(">IIBBBBB", 120, 90, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x37\xd3\x9a" * 120 for _ in range(90))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))

PNG = png_bytes()
DOCS = [{"repo": "orchestrator", "count": 2, "docs": [
            {"path": "docs/console-deploy.md", "title": "Console deploy gate", "size": 4096, "mtime": "2026-10-26T09:00:00Z"},
            {"path": "docs/asks-board.md", "title": "Asks board operations", "size": 8192, "mtime": "2026-10-25T18:00:00Z"}]},
        {"repo": "cosem-video-pipeline", "count": 1, "docs": [
            {"path": "README.md", "title": "Cosem pipeline readme", "size": 2048, "mtime": "2026-10-20T12:00:00Z"}]}]
MEDIA = [{"project": "orchestrator", "count": 2, "files": [
            {"path": "shots/console.png", "name": "console.png", "kind": "image", "size": 123456, "mtime": "2026-10-26T09:30:00Z"},
            {"path": "shots/asks.pdf", "name": "asks.pdf", "kind": "pdf", "size": 999999, "mtime": "2026-10-25T19:00:00Z"}]},
         {"project": "cosem-adcda", "count": 1, "files": [
            {"path": "board/w4.png", "name": "w4.png", "kind": "image", "size": 424242, "mtime": "2026-10-24T08:00:00Z"}]}]

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, ctype, body):
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        p = self.path.split("?")[0]
        auth = self.headers.get("Authorization", "")
        if p.startswith("/api/"):
            if auth != "Bearer " + TOKEN:
                self._send(401, "application/json", b'{"error":"unauthorized"}'); return
            if p == "/api/docs":
                self._send(200, "application/json", json.dumps(DOCS).encode()); return
            if p.startswith("/api/docs/"):
                doc = {"path": "docs/console-deploy.md", "mtime": "2026-10-26T09:00:00Z",
                       "html": "<h1>Console deploy gate</h1><p>Four fail-closed gates: version-sync, tests, render, cc-quality review at the exact content hash.</p><h2>Gate order</h2><ul><li>version-sync</li><li>tests</li><li>render</li><li>review</li></ul><pre><code>bash scripts/deploy_console.sh</code></pre><blockquote>Never a raw kickstart.</blockquote>"}
                self._send(200, "application/json", json.dumps(doc).encode()); return
            if p == "/api/media":
                self._send(200, "application/json", json.dumps(MEDIA).encode()); return
            if p.startswith("/api/media-file/"):
                self._send(200, "image/png", PNG); return
            self._send(404, "application/json", b"{}"); return
        if p == "/docs" or p == "/docs/":
            self._send(200, "text/html", open(STATIC + "/docs.html", "rb").read()); return
        if p == "/media" or p == "/media/":
            self._send(200, "text/html", open(STATIC + "/media.html", "rb").read()); return
        if p.startswith("/static/"):
            try:
                self._send(200, "application/octet-stream", open(STATIC + "/" + p[len("/static/"):], "rb").read())
            except OSError:
                self._send(404, "text/plain", b"nf")
            return
        self._send(404, "text/plain", b"nf")

srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()

fails = []
def check(name, cond, detail=""):
    print(("PASS  " if cond else "FAIL  ") + name + (("  -- " + str(detail)) if detail else ""))
    if not cond: fails.append(name)

with sync_playwright() as pw:
    browser = pw.chromium.launch()

    def ctx(w, h):
        c = browser.new_context(viewport={"width": w, "height": h}, device_scale_factor=3 if w == 390 else 1)
        c.add_init_script("window.localStorage.setItem('console_token', %s)" % json.dumps(TOKEN))
        return c

    # ---- docs ----
    c = ctx(390, 844); pg = c.new_page()
    pg.goto(f"http://127.0.0.1:{PORT}/docs"); pg.wait_for_timeout(900)
    check("docs: 3 doc cards", pg.locator("a.doc").count() == 3)
    check("docs: badge fc-v84", "fc-v84" in pg.locator("#build").inner_text())
    check("docs: 2 repo groups", pg.locator(".repo-head").count() >= 2)
    check("docs: filter present", pg.locator("#filter").count() == 1)
    ov = pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    check("docs: no 390px overflow", ov)
    pg.screenshot(path=OUT + "/docs-catalog-390.png")
    pg.locator("a.doc").first.click(); pg.wait_for_timeout(600)
    check("docs: article view", pg.locator(".article").count() == 1)
    check("docs: crumb shows repo", "/" in pg.locator(".crumb").inner_text())
    pg.screenshot(path=OUT + "/docs-doc-390.png")
    c.close()

    c2 = browser.new_context(viewport={"width": 390, "height": 844})  # tokenless
    pg2 = c2.new_page(); pg2.goto(f"http://127.0.0.1:{PORT}/docs"); pg2.wait_for_timeout(500)
    check("docs: tokenless empty state", "token" in pg2.locator("main .empty").inner_text().lower())
    check("docs: tokenless no cards", pg2.locator("a.doc").count() == 0)
    pg2.screenshot(path=OUT + "/docs-strip-390.png")
    c2.close()
    # deep-link: #t= persists + strips (the fc-v84 lock) — FRESH context: a real
    # Telegram tap is a full page load with the fragment; a same-document goto
    # never re-runs page scripts (same property as shipped asks.js).
    c2 = browser.new_context(viewport={"width": 390, "height": 844})
    pg2 = c2.new_page()
    pg2.goto(f"http://127.0.0.1:{PORT}/docs#t=" + TOKEN); pg2.wait_for_timeout(900)
    stored = pg2.evaluate("localStorage.getItem('console_token')")
    check("docs: #t= deep-link persists token", stored == TOKEN)
    check("docs: fragment stripped", pg2.evaluate("location.hash") == "")
    check("docs: deep-link renders catalog", pg2.locator("a.doc").count() == 3)
    c2.close()

    d = ctx(1440, 900); pgd = d.new_page()
    pgd.goto(f"http://127.0.0.1:{PORT}/docs"); pgd.wait_for_timeout(900)
    check("docs: desktop renders", pgd.locator("a.doc").count() == 3)
    pgd.screenshot(path=OUT + "/docs-catalog-1440.png")
    d.close()

    # ---- media ----
    c3 = ctx(390, 844); pg3 = c3.new_page()
    pg3.goto(f"http://127.0.0.1:{PORT}/media"); pg3.wait_for_timeout(1200)
    check("media: 3 cards", pg3.locator("a.card").count() == 3)
    check("media: one pdf tile", pg3.locator(".thumb.pdf").count() == 1)
    check("media: badge fc-v84", "fc-v84" in pg3.locator("#build").inner_text())
    check("media: thumbs loaded (blob)", pg3.locator("img.thumb[src^='blob:']").count() == 2)
    ov3 = pg3.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    check("media: no 390px overflow", ov3)
    pg3.screenshot(path=OUT + "/media-catalog-390.png")
    pg3.locator("a.card").first.click(); pg3.wait_for_timeout(900)
    check("media: lightbox opens", pg3.locator(".lb").count() == 1)
    check("media: lightbox image", pg3.locator(".lb img").count() == 1)
    pg3.screenshot(path=OUT + "/media-lightbox-390.png")
    pg3.locator("#lbclose").click(); pg3.wait_for_timeout(300)
    check("media: lightbox closes", pg3.locator(".lb").count() == 0)
    c3.close()

    c4 = browser.new_context(viewport={"width": 390, "height": 844})
    pg4 = c4.new_page(); pg4.goto(f"http://127.0.0.1:{PORT}/media"); pg4.wait_for_timeout(500)
    check("media: tokenless empty state", "token" in pg4.locator("main .empty").inner_text().lower())
    pg4.screenshot(path=OUT + "/media-strip-390.png")
    c4.close()
    c4 = browser.new_context(viewport={"width": 390, "height": 844})
    pg4 = c4.new_page()
    pg4.goto(f"http://127.0.0.1:{PORT}/media#t=" + TOKEN); pg4.wait_for_timeout(1200)
    check("media: #t= deep-link persists", pg4.evaluate("localStorage.getItem('console_token')") == TOKEN)
    check("media: deep-link renders grid", pg4.locator("a.card").count() == 3)
    c4.close()

    d2 = ctx(1440, 900); pgd2 = d2.new_page()
    pgd2.goto(f"http://127.0.0.1:{PORT}/media"); pgd2.wait_for_timeout(1200)
    check("media: desktop renders", pgd2.locator("a.card").count() == 3)
    pgd2.screenshot(path=OUT + "/media-catalog-1440.png")
    d2.close()
    browser.close()

srv.shutdown()
print()
if fails:
    print("RESULT: %d FAIL: %s" % (len(fails), ", ".join(fails))); raise SystemExit(1)
print("RESULT: ALL PASS")
