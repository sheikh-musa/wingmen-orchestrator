#!/usr/bin/env python3
"""arabic_ocr_service — local Arabic-name reconstruction for the cosem-platform demo OCR.

The deployed cosem-platform demo (Vercel) runs Google Vision for the OCR, then POSTs the OCR text +
the English name here. This service runs a HEADLESS Claude query on the Mac Mini using the operator's
Max subscription (CLAUDE_CODE_OAUTH_TOKEN — the same auth the fleet uses for headless Claude) to
reconstruct the COMPLETE Arabic name, which Google Vision drops words from. That keeps the reliable
Claude reconstruction on the Max plan (zero metered API spend) — the raw /v1/messages API rejects the
OAuth token, but `claude -p` (Claude Code) accepts it.

Auth: every request must carry `x-ocr-secret: $ARABIC_OCR_SECRET` (shared with the Vercel app) so only
the demo can spend the subscription. Fail-soft: on any error the app leaves the Arabic field blank.

Run under launchd (dev.wingmen.arabic-ocr) — never nohup. Env: CLAUDE_CODE_OAUTH_TOKEN, ARABIC_OCR_SECRET
(both from .env), ARABIC_OCR_PORT (default 8791).
"""
import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from dotenv import load_dotenv

ROOT = os.path.join(os.path.dirname(__file__), "..")
load_dotenv(os.path.join(ROOT, ".env"))

PORT = int(os.environ.get("ARABIC_OCR_PORT", "8791"))
SECRET = os.environ.get("ARABIC_OCR_SECRET", "")
OAUTH = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
CLAUDE_BIN = os.path.expanduser("~/.local/bin/claude")
TIMEOUT_S = 45

_ARABIC = lambda s: any("؀" <= c <= "ۿ" for c in s)  # noqa: E731


def reconstruct(vision_text: str, name_en: str) -> Optional[str]:
    """Run headless Claude (Max plan) to reconstruct the full Arabic name. None on any failure."""
    if not OAUTH:
        return None
    prompt = (
        "OCR text from a UAE Emirates ID (its Arabic may be incomplete or garbled):\n"
        f'"""{vision_text[:2000]}"""\n\n'
        f"The cardholder's English name is: {name_en[:120]}\n\n"
        "Output ONLY the person's full name in Arabic script, exactly as it should appear on the "
        "card (reconstruct any Arabic words the OCR dropped, using the English name as the guide). "
        "Output the Arabic name and nothing else — no quotes, no English, no explanation."
    )
    try:
        env = {**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": OAUTH}
        env.pop("ANTHROPIC_API_KEY", None)  # force the Max/OAuth path, not a metered key
        out = subprocess.run(
            # Haiku: ~9s vs ~25s on the default model — the reconstruction is a simple text task.
            [CLAUDE_BIN, "-p", "--model", "claude-haiku-4-5", prompt],
            capture_output=True, text=True, timeout=TIMEOUT_S, env=env, cwd=ROOT,
        )
        if out.returncode != 0:
            return None
        ar = out.stdout.strip()
        return ar if ar and _ARABIC(ar) and len(ar) <= 120 else None
    except Exception:
        return None


class Handler(BaseHTTPRequestHandler):
    def _json(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # health check
        if self.path == "/health":
            self._json(200, {"ok": True, "oauth": bool(OAUTH)})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/reconstruct-arabic":
            return self._json(404, {"error": "not found"})
        if not SECRET or self.headers.get("x-ocr-secret") != SECRET:
            return self._json(401, {"error": "unauthorized"})
        try:
            n = int(self.headers.get("content-length", "0"))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._json(400, {"error": "bad json"})
        vision_text = str(data.get("visionText", ""))
        name_en = str(data.get("nameEn", ""))
        if not name_en:
            return self._json(400, {"error": "nameEn required"})
        name_ar = reconstruct(vision_text, name_en)
        self._json(200, {"nameAr": name_ar})  # nameAr may be null → app leaves the field blank

    def log_message(self, *_):  # quiet; never log the OCR content (transient PII)
        pass


def main():
    if not SECRET:
        raise SystemExit("ARABIC_OCR_SECRET not set — refusing to start an unauthenticated service")
    print(f"arabic_ocr_service on 127.0.0.1:{PORT} (oauth={'set' if OAUTH else 'MISSING'})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
