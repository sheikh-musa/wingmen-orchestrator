"""Tests for scripts/hooks/secrets_output_scanner.py (Musa op#24408, bus #48312 #4)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "scripts" / "hooks" / "secrets_output_scanner.py"
spec = importlib.util.spec_from_file_location("secrets_output_scanner", HOOK_PATH)
scanner = importlib.util.module_from_spec(spec)
sys.modules["secrets_output_scanner"] = scanner
spec.loader.exec_module(scanner)


def test_detects_anthropic_key():
    hits = scanner.scan("here is the key sk-ant-" + "a" * 30)
    assert hits and hits[0][0] == "anthropic-api-key"


def test_detects_postgres_dsn():
    hits = scanner.scan("DATABASE_URL=postgres://user:hunter2@host:5432/db")
    assert hits and hits[0][0] == "postgres-dsn"


def test_detects_telegram_bot_token():
    hits = scanner.scan("token: 123456789:AAFakeTokenShapeForTestingOnly123456")
    assert hits and hits[0][0] == "telegram-bot-token"


def test_detects_telegram_bot_token_embedded_in_api_url():
    # bus #48642 real incident (~2026-10-01 20:03Z): a Telegram Bot API URL embeds the
    # token right after "bot" with NO word boundary (letters then digits is one
    # continuous word-char run) -- the old \b\d{6,}... pattern never matched here.
    hits = scanner.scan(
        "curl https://api.telegram.org/bot123456789:AAFakeTokenShapeForTestingOnly1234/sendMessage"
    )
    assert hits and any(h[0] == "telegram-bot-token" for h in hits)


def test_detects_github_fine_grained_oauth_token():
    # bus #48642: only ghp_ (classic PAT) was covered; gho_/ghu_/ghs_/ghr_ slipped.
    hits = scanner.scan("token=gho_" + "a" * 36)
    assert hits and any(h[0] == "github-token" for h in hits)


def test_detects_github_classic_pat_still_works():
    hits = scanner.scan("token=ghp_" + "b" * 36)
    assert hits and any(h[0] == "github-token" for h in hits)


def test_detects_google_oauth_refresh_token():
    hits = scanner.scan("refresh_token: 1//0" + "FakeRefreshTokenShapeForTestingOnly123")
    assert hits and any(h[0] == "google-oauth-refresh-token" for h in hits)


def test_detects_long_jwt_shaped_like_supabase_service_key():
    fake_jwt = "eyJ" + "a" * 40 + "." + "b" * 90 + "." + "c" * 40
    hits = scanner.scan(f"SUPABASE_SERVICE_ROLE_KEY={fake_jwt}")
    assert hits and any(h[0] == "jwt" for h in hits)


def test_detects_ssh_private_key():
    # cc-quality PR#245 review (bus #48441 MED #1): non-DSN secret files (SSH keys
    # other than gzb_to_mini) previously had no value-backstop at all.
    hits = scanner.scan("-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk...\n")
    assert hits and any(h[0] == "ssh-private-key" for h in hits)


def test_no_hit_on_clean_output():
    assert scanner.scan("all tests passed, 42 rows updated") == []


# ---- redaction: structure-preservation proof (bus #48312 concern #4) -------------
# "editing a LIVE session's JSONL can corrupt it... redact by exact span only."
# We can't drive a real `claude --continue` from inside this hook's own test suite;
# the proxy proof is: every key, every non-matching string, and the line count are
# byte-identical before/after, every line still parses as JSON, and ONLY the matched
# substring (not the whole field, not the whole line) changes.

SAMPLE_TRANSCRIPT_LINES = [
    {"type": "user", "uuid": "u1", "parentUuid": None,
     "message": {"role": "user", "content": "check the db"}},
    {"type": "assistant", "uuid": "a1", "parentUuid": "u1",
     "message": {"role": "assistant", "content": [
         {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo $DATABASE_URL"}}
     ]}},
    {"type": "tool_result", "uuid": "r1", "parentUuid": "a1",
     "toolUseResult": {"output": "postgres://musa:hunter2@db.example:5432/orch"}},
]


def _write_transcript(tmp_path) -> str:
    p = tmp_path / "session.jsonl"
    with open(p, "w") as f:
        for line in SAMPLE_TRANSCRIPT_LINES:
            f.write(json.dumps(line) + "\n")
    return str(p)


def test_redaction_preserves_transcript_structure(tmp_path):
    path = _write_transcript(tmp_path)
    before_lines = Path(path).read_text().splitlines()

    changed = scanner.redact_last_line(path, "postgres-dsn", scanner.SECRET_PATTERNS["postgres-dsn"])
    assert changed is True

    after_lines = Path(path).read_text().splitlines()
    assert len(after_lines) == len(before_lines), "redaction must not add/remove lines"

    # every line still parses as valid JSON
    before_objs = [json.loads(l) for l in before_lines]
    after_objs = [json.loads(l) for l in after_lines]

    # only the LAST line's content changed; every earlier line byte-identical
    assert before_lines[:-1] == after_lines[:-1]
    assert before_objs[0] == after_objs[0]
    assert before_objs[1] == after_objs[1]

    # the last line: structure (keys, non-matching values) preserved, only the
    # matching string value's matched span is different
    assert after_objs[2]["type"] == before_objs[2]["type"]
    assert after_objs[2]["uuid"] == before_objs[2]["uuid"]
    assert after_objs[2]["parentUuid"] == before_objs[2]["parentUuid"]
    assert set(after_objs[2]["toolUseResult"].keys()) == set(before_objs[2]["toolUseResult"].keys())

    redacted_output = after_objs[2]["toolUseResult"]["output"]
    assert "hunter2" not in redacted_output
    assert "postgres://" not in redacted_output
    assert "REDACTED" in redacted_output


def test_redaction_is_noop_when_nothing_matches(tmp_path):
    path = _write_transcript(tmp_path)
    before = Path(path).read_text()
    changed = scanner.redact_last_line(path, "anthropic-api-key", scanner.SECRET_PATTERNS["anthropic-api-key"])
    assert changed is False
    assert Path(path).read_text() == before


def test_redaction_never_touches_other_lines_file_shorter_than_expected(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    assert scanner.redact_last_line(str(p), "jwt", scanner.SECRET_PATTERNS["jwt"]) is False


def test_main_is_fail_open_on_unparseable_input():
    import subprocess
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input="not json", text=True, capture_output=True)
    assert r.returncode == 0  # PostToolUse never blocks -- the tool call already happened


def test_detects_bearer_token():
    hits = scanner.scan("Authorization: Bearer FakeSyntheticToken1234567890abcdef")
    assert hits and any(h[0] == "bearer-token" for h in hits)


# ---- bus #48685/#48695: scanner must also catch a secret typed literally into the
# tool_use INPUT, not just the tool_response/output (shapes 1/2 land in the input) ---

def test_main_catches_and_redacts_a_literal_dsn_in_the_tool_use_input(tmp_path):
    import subprocess

    transcript = tmp_path / "session.jsonl"
    fake_dsn = "postgres://orchuser:FakeSyntheticPass123@db.example.internal:5432/orch"
    with open(transcript, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": f'psql "{fake_dsn}"'}}
        ]}}) + "\n")
        f.write(json.dumps({"type": "tool_result", "toolUseResult": {"output": "SELECT 1"}}) + "\n")

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f'psql "{fake_dsn}"'},
        "tool_response": "SELECT 1",
        "transcript_path": str(transcript),
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True)
    assert "secret-shaped content" in r.stderr

    after = transcript.read_text()
    assert fake_dsn not in after
    assert "REDACTED" in after
    # the output line (no secret in it) must be untouched
    after_lines = after.splitlines()
    assert json.loads(after_lines[1])["toolUseResult"]["output"] == "SELECT 1"


def test_redact_recent_lines_only_touches_the_window(tmp_path):
    p = tmp_path / "t.jsonl"
    lines = [
        {"n": 1, "msg": "clean line far back"},
        {"n": 2, "msg": "sk-ant-" + "a" * 30},
        {"n": 3, "msg": "sk-ant-" + "b" * 30},
    ]
    with open(p, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")

    changed = scanner.redact_recent_lines(str(p), "anthropic-api-key",
                                           scanner.SECRET_PATTERNS["anthropic-api-key"], max_lines=2)
    assert changed is True
    after = [json.loads(l) for l in p.read_text().splitlines()]
    assert after[0]["msg"] == "clean line far back"  # outside the window, untouched
    assert "REDACTED" in after[1]["msg"]
    assert "REDACTED" in after[2]["msg"]
