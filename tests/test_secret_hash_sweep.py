"""Tests for secret_hash_sweep.py (orch-console P1 #48640).

Uses ONLY fabricated secret values — never a real secret. Proves: exact-span detection,
size-preserving in-place redaction, JSON still parses (session still loads), zero secrets
survive, benign content untouched, DSN-password extraction, length gate, and the hard
invariant that a secret VALUE is never echoed into any report.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import secret_hash_sweep as s  # noqa: E402

# fabricated, obviously-fake secrets (long enough to pass the length gate)
FAKE_KEY = "sk-ant-FAKE0000000000000000000000000000TESTKEY"
FAKE_TOKEN = "123456789:FAKE-bot-token-0000000000000000000000"
FAKE_DSN = "postgresql://u:FAKEpassw0rd1234@host:5432/db"
FAKE_DSN_PW = "FAKEpassw0rd1234"


def _write(p, text):
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_build_secret_set_length_gate_and_dsn_pw(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "# comment",
        "ANTHROPIC_API_KEY=%s" % FAKE_KEY,
        'TG_TOKEN="%s"' % FAKE_TOKEN,       # quoted
        "DATABASE_URL=%s" % FAKE_DSN,
        "PORT=5432",                         # too short -> excluded
        "ENV=development",                   # too short -> excluded
        "EMPTY=",
    ]))
    secrets = s.build_secret_set([str(env)])
    vals = set(secrets)
    assert FAKE_KEY.encode() in vals
    assert FAKE_TOKEN.encode() in vals
    assert FAKE_DSN.encode() in vals
    assert FAKE_DSN_PW.encode() in vals          # DSN password pulled out separately
    assert b"5432" not in vals and b"development" not in vals   # length gate


def test_public_values_excluded_credentials_kept(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "ANTHROPIC_API_KEY=%s" % FAKE_KEY,                 # secret -> keep
        "DATABASE_URL=%s" % FAKE_DSN,                       # DSN (has creds) -> keep (+pw)
        "SUPABASE_PROJECT_REF=tscuymavysscrvoberrr",        # public id -> drop
        "NEXT_PUBLIC_APP_URL=https://app.example.com/path",  # public -> drop
        "SUPABASE_URL=https://tscuymavysscrvoberrr.supabase.co",  # plain url -> drop
        "CLAUDE_BIN=/Users/x/.local/bin/claude-wrapper-v2",  # _BIN path -> drop
        "GOOGLE_APPLICATION_CREDENTIALS=/Users/x/secrets/gcp-service-account.json",  # path -> drop
        "NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJvery-long-anon-key-value-aaaaaaaaaaaaaaaa",  # anon public -> drop
    ]))
    secrets = s.build_secret_set([str(env)])
    vals = set(secrets)
    assert FAKE_KEY.encode() in vals
    assert FAKE_DSN.encode() in vals and FAKE_DSN_PW.encode() in vals
    assert b"tscuymavysscrvoberrr" not in vals
    assert b"https://app.example.com/path" not in vals
    assert b"https://tscuymavysscrvoberrr.supabase.co" not in vals
    assert b"/Users/x/.local/bin/claude-wrapper-v2" not in vals
    assert b"/Users/x/secrets/gcp-service-account.json" not in vals   # path, not a secret
    assert not any(v.startswith(b"eyJvery-long-anon") for v in vals)


def test_marker_is_same_length_and_json_safe():
    v = FAKE_KEY.encode()
    m = s.marker_for(v, s.hash8(v))
    assert len(m) == len(v)
    # only JSON-string-safe bytes (no quote/backslash/control)
    assert all(c not in m for c in (ord('"'), ord("\\")))
    assert min(m) >= 0x20


def test_redact_bytes_preserves_length_and_removes_secret():
    secrets = {FAKE_KEY.encode(): s.hash8(FAKE_KEY.encode())}
    raw = ('{"text":"my key is %s ok"}' % FAKE_KEY).encode()
    new, hits, spans = s.redact_bytes(raw, secrets)
    assert len(new) == len(raw)
    assert FAKE_KEY.encode() not in new
    assert sum(hits.values()) == 1
    assert b"my key is" in new and b" ok" in new   # benign content intact
    json.loads(new)                                 # still valid JSON
    # span offset+length is accurate and value-free
    assert len(spans) == 1
    off, ln, h8 = spans[0]
    assert ln == len(FAKE_KEY.encode()) and raw[off:off + ln] == FAKE_KEY.encode()
    assert h8 == s.hash8(FAKE_KEY.encode())


def test_sweep_file_execute_redacts_in_place_and_verifies(tmp_path):
    env = tmp_path / ".env"
    _write(env, "ANTHROPIC_API_KEY=%s\nTG=%s\n" % (FAKE_KEY, FAKE_TOKEN))
    secrets = s.build_secret_set([str(env)])

    tr = tmp_path / "session.jsonl"
    lines = [
        json.dumps({"type": "user", "text": "hello, nothing secret here"}),
        json.dumps({"type": "tool", "input": {"cmd": "echo %s" % FAKE_KEY}}),
        json.dumps({"type": "assistant", "text": "token=%s done" % FAKE_TOKEN}),
    ]
    _write(tr, "\n".join(lines) + "\n")
    size_before = os.path.getsize(tr)

    ledger = tmp_path / "ledger.jsonl"
    rep = s.sweep_file(str(tr), secrets, execute=True, ledger_path=str(ledger))

    assert rep["error"] is None
    assert rep["matches_before"] >= 2
    assert rep["matches_after"] == 0           # nothing survives
    assert rep["size_preserved"] is True
    assert rep["parses_ok"] is True
    assert os.path.getsize(tr) == size_before  # byte length held (live-append safe)

    # every line still loads as JSON, and no fake secret remains anywhere
    body = open(tr, "rb").read()
    assert FAKE_KEY.encode() not in body and FAKE_TOKEN.encode() not in body
    for ln in body.decode().splitlines():
        json.loads(ln)
    # benign line untouched
    assert b"nothing secret here" in body

    # ledger = offsets + hash8 only, NEVER the value, and NO plaintext backup anywhere
    assert ledger.exists()
    ltext = ledger.read_text()
    assert FAKE_KEY not in ltext and FAKE_TOKEN not in ltext
    recs = [json.loads(x) for x in ltext.splitlines() if x.strip()]
    assert recs and all({"file", "offset", "length", "hash8"} <= set(r) for r in recs)
    assert not list(tmp_path.glob("*.bak")) and not (tmp_path / "bak").exists()


def test_report_never_contains_the_secret_value(tmp_path):
    env = tmp_path / ".env"
    _write(env, "K=%s\n" % FAKE_KEY)
    secrets = s.build_secret_set([str(env)])
    tr = tmp_path / "s.jsonl"
    _write(tr, json.dumps({"x": "leak %s" % FAKE_KEY}) + "\n")
    rep = s.sweep_file(str(tr), secrets, execute=True, ledger_path=str(tmp_path / "ledger.jsonl"))
    blob = json.dumps(rep)
    assert FAKE_KEY not in blob                 # value NEVER echoed
    assert s.hash8(FAKE_KEY.encode()) in blob   # only its hash8 appears


def test_dry_run_does_not_modify(tmp_path):
    env = tmp_path / ".env"
    _write(env, "K=%s\n" % FAKE_KEY)
    secrets = s.build_secret_set([str(env)])
    tr = tmp_path / "s.jsonl"
    _write(tr, json.dumps({"x": "leak %s" % FAKE_KEY}) + "\n")
    before = open(tr, "rb").read()
    rep = s.sweep_file(str(tr), secrets, execute=False, ledger_path=None)
    assert rep["matches_before"] == 1
    assert open(tr, "rb").read() == before      # untouched in dry-run


def test_empty_manifest_refuses(tmp_path, capsys):
    env = tmp_path / ".env"
    _write(env, "PORT=5432\nENV=dev\n")          # nothing passes the gate
    tr = tmp_path / "s.jsonl"
    _write(tr, json.dumps({"x": "y"}) + "\n")
    rc = s.main(["--env", str(env), "--scan", str(tr), "--dry-run"])
    assert rc == 3                               # refuse empty manifest
