"""tg-send resilience (Nazim #40837): the operator got two first-attempt drops today
that both succeeded on immediate retry, while _tg_chunked_send only printed 'tg send
error' (status/description swallowed). This adds: structured error extraction, ONE retry
with backoff on 429/5xx/network (respect retry_after), and a Markdown->plain fallback on a
400 parse error. Tests mock the Telegram API (no real network)."""
import importlib
import io
import json
import urllib.error

m = importlib.import_module("scripts._tg_chunked_send")


def R(ok=False, status=None, desc=None, ra=None):
    return {"ok": ok, "status": status, "description": desc, "retry_after": ra}


class _Recorder:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []          # (parse_mode) per post
        self.slept = []          # seconds per sleep

    def post(self, tok, chat, body, parse_mode=None):
        self.calls.append(parse_mode)
        return self.results.pop(0)

    def sleep(self, s):
        self.slept.append(s)


# ── send_with_resilience: retry policy ───────────────────────────────────────
def test_ok_first_try_no_retry_no_sleep():
    rec = _Recorder([R(ok=True, status=200)])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert r["ok"] and len(rec.calls) == 1 and rec.slept == []


def test_429_retries_once_and_succeeds_respecting_retry_after():
    rec = _Recorder([R(status=429, desc="Too Many Requests", ra=7), R(ok=True, status=200)])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert r["ok"] and len(rec.calls) == 2 and rec.slept == [7]      # slept the retry_after


def test_5xx_retries_once_with_default_backoff():
    rec = _Recorder([R(status=503, desc="Bad Gateway"), R(ok=True, status=200)])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert r["ok"] and len(rec.calls) == 2 and rec.slept == [m.RETRY_SLEEP_DEFAULT]


def test_network_error_status_none_retries_once():
    rec = _Recorder([R(status=None, desc="Connection reset"), R(ok=True, status=200)])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert r["ok"] and len(rec.calls) == 2


def test_retry_that_also_fails_returns_the_failure():
    rec = _Recorder([R(status=429, desc="x"), R(status=429, desc="still throttled")])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert not r["ok"] and r["status"] == 429 and len(rec.calls) == 2


def test_non_retryable_403_does_not_retry():
    rec = _Recorder([R(status=403, desc="Forbidden: bot blocked")])
    r = m.send_with_resilience("t", "c", "b", post=rec.post, sleep=rec.sleep)
    assert not r["ok"] and len(rec.calls) == 1 and rec.slept == []


# ── Markdown -> plain fallback on a parse error ──────────────────────────────
def test_markdown_parse_400_falls_back_to_plain():
    rec = _Recorder([R(status=400, desc="Bad Request: can't parse entities"), R(ok=True, status=200)])
    r = m.send_with_resilience("t", "c", "b", parse_mode="Markdown", post=rec.post, sleep=rec.sleep)
    assert r["ok"]
    assert rec.calls == ["Markdown", None], "second attempt must drop parse_mode (plain)"
    assert rec.slept == [], "a parse-fallback is not a backoff retry"


def test_plain_400_parse_is_not_retried_forever():
    # no parse_mode -> a 400 is a real error, not a fallback candidate
    rec = _Recorder([R(status=400, desc="Bad Request: chat not found")])
    r = m.send_with_resilience("t", "c", "b", parse_mode=None, post=rec.post, sleep=rec.sleep)
    assert not r["ok"] and len(rec.calls) == 1


# ── _post: structured error extraction from a real HTTPError shape ───────────
def test_post_extracts_status_description_retry_after_from_httperror(monkeypatch):
    body = json.dumps({"ok": False, "description": "Too Many Requests",
                       "parameters": {"retry_after": 12}}).encode()
    err = urllib.error.HTTPError("url", 429, "Too Many Requests", {}, io.BytesIO(body))

    def boom(*a, **k):
        raise err
    monkeypatch.setattr(m.urllib.request, "urlopen", boom)
    r = m._post("tok", "chat", "body")
    assert r["status"] == 429 and r["retry_after"] == 12
    assert "Too Many Requests" in (r["description"] or "")


def test_post_ok_on_200(monkeypatch):
    class _Resp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b'{"ok":true,"result":{}}'
    monkeypatch.setattr(m.urllib.request, "urlopen", lambda *a, **k: _Resp())
    # json.load reads .read(); patch json.load path via the response file-like
    monkeypatch.setattr(m.json, "load", lambda r: {"ok": True})
    r = m._post("tok", "chat", "body")
    assert r["ok"] and r["status"] == 200
