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
    # bus #48740: this is a test fixture, not a real leak -- SECRETS_SCANNER_DEMO=1
    # keeps this from paging orch-console a real P1 on every suite run.
    import os
    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
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


# ---- bus #48740: demo/replay runs must not page orch-console at real P1 ----

def _fake_orch_root(tmp_path):
    # _page_orch_console early-returns if scripts/bus_send.py doesn't exist under
    # ORCH_ROOT -- on a CI runner there's no ~/wingmen/orchestrator default, so the
    # test must point ORCH_ROOT at a real (if empty) stand-in, not rely on the host.
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "bus_send.py").write_text("# fake, never executed (subprocess.run is mocked)\n")
    return tmp_path


def test_page_orch_console_demo_mode_tags_subject_and_drops_to_p3(monkeypatch, tmp_path):
    import subprocess as subprocess_module
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        class R:
            returncode = 0
        return R()

    monkeypatch.setenv("ORCH_ROOT", str(_fake_orch_root(tmp_path)))
    monkeypatch.setenv("SECRETS_SCANNER_DEMO", "1")
    monkeypatch.setattr(subprocess_module, "run", fake_run)
    scanner._page_orch_console("postgres-dsn", "Bash")

    args = captured["args"]
    subject_idx = args.index("--subject") + 1
    priority_idx = args.index("--priority") + 1
    assert args[subject_idx].startswith("[DEMO] ")
    assert args[priority_idx] == "P3"
    assert "--req" not in args


def test_main_dedupes_page_when_same_class_matches_input_and_response(tmp_path):
    # bus #48900/#48901/#48920: one Write call whose tool_response echoed a preview of
    # the content it just wrote produced TWO pages for the same postgres-dsn hit (one
    # from input_text, one from output_text). One real event must page once.
    # Deliberately NOT a fixture-shaped DSN (no Fake/test/example/localhost/host marker,
    # a TEST-NET-3 reserved IP per RFC 5737) -- this test isolates dedupe from the
    # fixture-allowlist behaviour covered separately below.
    import subprocess

    transcript = tmp_path / "session.jsonl"
    fake_dsn = "postgres://orchuser:Zq9mPlKx2Rz@203.0.113.42:5432/orchdb"
    with open(transcript, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Write", "input": {"content": fake_dsn}}
        ]}}) + "\n")
        f.write(json.dumps({"type": "tool_result", "toolUseResult": {"output": f"preview: {fake_dsn}"}}) + "\n")

    payload = json.dumps({
        "tool_name": "Write",
        "tool_input": {"content": fake_dsn},
        "tool_response": f"preview: {fake_dsn}",
        "transcript_path": str(transcript),
    })
    import os
    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)

    # bus_send.py is a stub (never executed as real python) -- count invocations via a
    # wrapper script that appends one line per call to a counter file.
    counter = tmp_path / "page_calls.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        f"import pathlib; pathlib.Path({str(counter)!r}).open('a').write('x\\n')\n"
    )

    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0
    assert counter.exists(), "expected bus_send.py to be invoked at least once"
    assert counter.read_text().count("x") == 1, "same class in input+response must page exactly once"


# ---- fixture allowlist (bus #48965/#48982): developing against this file's own
# fixtures must not page orch-console -- 20+ real P1 pages in ~25 minutes, all verified
# as fixtures, buried real alerts. Redaction/stderr still run; only the page is skipped.

def _run_hook_count_pages(tmp_path, tool_name, tool_input, tool_response_text):
    import os
    import subprocess

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)

    counter = tmp_path / "page_calls.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        f"import pathlib; pathlib.Path({str(counter)!r}).open('a').write('x\\n')\n"
    )
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": tool_response_text,
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    page_count = counter.read_text().count("x") if counter.exists() else 0
    return r, page_count


def test_main_skips_paging_for_known_fixture_dsn_host(tmp_path):
    fake_dsn = "postgres://orchuser:Zq9mPlKx2Rz@db.example.internal:5432/orch"
    r, pages = _run_hook_count_pages(tmp_path, "Bash", {"command": f'psql "{fake_dsn}"'}, "SELECT 1")
    assert r.returncode == 0
    assert "secret-shaped content" in r.stderr, "redaction/stderr note must still fire for a fixture hit"
    assert pages == 0, "a known fixture DSN host must not page orch-console"


def test_main_skips_paging_for_fake_prefixed_token():
    # same generic marker that makes a DSN's user:pass a recognized fixture (bus #48982:
    # "tokens starting test-/fake-/example-"), applied here to a bearer token instead.
    assert scan_is_fixture("bearer-token", "Bearer fake-abcdefghijklmnopqrstuvwxyz123456")


def scan_is_fixture(cls, text):
    m = scanner.SECRET_PATTERNS[cls].search(text)
    assert m, f"fixture under test must actually match the {cls} pattern"
    return scanner._is_fixture_hit(cls, m)


def test_main_skips_paging_for_telegram_test_bot_id_even_without_a_fake_marker(tmp_path):
    # bus #48982 calls out the "123456789 bot id" as its own allowlist element, distinct
    # from the test-/fake-/example- marker -- this fixture token has neither word in its
    # suffix, matching the "more realistic-looking" fixture style used elsewhere in this
    # suite (tests/test_secret_redact.py), so only the bot-id-specific check can catch it.
    token = "123456789:AAH8xY_zQwErTyUiOpAsDfGhesk0123456789"
    r, pages = _run_hook_count_pages(tmp_path, "Bash", {"command": f"curl .../bot{token}/sendMessage"}, "ok")
    assert r.returncode == 0
    assert pages == 0


def test_main_skips_paging_entirely_for_dedicated_secrets_test_file_by_path(tmp_path):
    # the fixture SSH-key header (test_detects_ssh_private_key) carries no test-/fake-/
    # example- marker and isn't a DSN or telegram token -- it is only identifiable by the
    # file it lives in (bus #48982: "skipping tests/test_secrets_*.py by path").
    output = (
        "tests/test_secrets_output_scanner.py::test_detects_ssh_private_key PASSED\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk...\n"
    )
    r, pages = _run_hook_count_pages(tmp_path, "Bash", {"command": "pytest -v tests/test_secrets_output_scanner.py"}, output)
    assert r.returncode == 0
    assert pages == 0


def test_main_still_pages_real_secret_chained_with_fixture_test_file_in_same_command(tmp_path):
    # bus #49007/#49062: orch-console's exact illustrative gap -- a real secret and a
    # mention of the dedicated test file's path riding along in ONE compound command
    # must NOT share the fixture skip. The path mention is disqualified because the
    # command has a second statement (chained via ;), not because of where the secret
    # match happens to land.
    real_looking_dsn = "postgres://appuser:Zq9mPlKx2RzT7@203.0.113.42:5432/billing"
    r, pages = _run_hook_count_pages(
        tmp_path, "Bash",
        {"command": f'psql "{real_looking_dsn}"; pytest tests/test_secrets_output_scanner.py'},
        "SELECT 1\ntests/test_secrets_output_scanner.py::test_detects_ssh_private_key PASSED\n",
    )
    assert r.returncode == 0
    assert pages == 1, "a real secret must still page even when chained with a fixture-test-file invocation"


def test_main_skips_paging_for_fixture_file_mentioned_only_in_write_file_path(tmp_path):
    # a structured file_path field (Write/Edit/...) has no chaining risk -- the whole
    # call is about that one file, so the skip is safe regardless of command shape. Uses
    # a DSN with NO fixture marker/host of its own, so pages==0 can only come from the
    # file_path rule, not _is_fixture_hit.
    real_looking_dsn = "postgres://appuser:Zq9mPlKx2RzT7@203.0.113.42:5432/billing"
    r, pages = _run_hook_count_pages(
        tmp_path, "Write",
        {"file_path": "tests/test_secrets_output_scanner.py", "content": f'FAKE_DSN = "{real_looking_dsn}"'},
        "",
    )
    assert r.returncode == 0
    assert pages == 0


def test_main_still_pages_a_real_looking_secret_with_no_fixture_marker(tmp_path):
    # regression guard: the allowlist must stay narrow -- a secret shape with none of the
    # fixture markers, hosts, bot id, or test-file path must still page as before.
    real_looking_dsn = "postgres://appuser:Zq9mPlKx2RzT7@203.0.113.42:5432/billing"
    r, pages = _run_hook_count_pages(tmp_path, "Bash", {"command": f'psql "{real_looking_dsn}"'}, "SELECT 1")
    assert r.returncode == 0
    assert pages == 1


def test_page_orch_console_real_mode_is_unchanged(monkeypatch, tmp_path):
    import subprocess as subprocess_module
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        class R:
            returncode = 0
        return R()

    monkeypatch.setenv("ORCH_ROOT", str(_fake_orch_root(tmp_path)))
    monkeypatch.delenv("SECRETS_SCANNER_DEMO", raising=False)
    monkeypatch.setattr(subprocess_module, "run", fake_run)
    scanner._page_orch_console("postgres-dsn", "Bash")

    args = captured["args"]
    subject_idx = args.index("--subject") + 1
    priority_idx = args.index("--priority") + 1
    assert not args[subject_idx].startswith("[DEMO]")
    assert args[priority_idx] == "P1"
    assert "--req" in args


# ---- bus #48907: a tool call originating inside a subagent must page with the
# subagent's identity, not a bare "unknown-agent" / the parent session's own label. No
# separate per-subagent transcript file exists (confirmed against Claude Code's hooks
# docs) -- transcript_path is shared, so redaction is unaffected; only attribution.

def test_page_orch_console_labels_subagent_when_present(monkeypatch, tmp_path):
    import subprocess as subprocess_module
    captured = {}

    def fake_run(args, input=None, **kwargs):
        captured["body"] = input
        class R:
            returncode = 0
        return R()

    monkeypatch.setenv("ORCH_ROOT", str(_fake_orch_root(tmp_path)))
    monkeypatch.setenv("SECRETS_SCANNER_DEMO", "1")
    monkeypatch.setattr(subprocess_module, "run", fake_run)
    scanner._page_orch_console("postgres-dsn", "Bash", agent_id="subagent_xyz789", agent_type="Explore")

    assert "Explore" in captured["body"]
    assert "subagent_xyz789" in captured["body"]


def test_page_orch_console_omits_subagent_label_when_absent(monkeypatch, tmp_path):
    import subprocess as subprocess_module
    captured = {}

    def fake_run(args, input=None, **kwargs):
        captured["body"] = input
        class R:
            returncode = 0
        return R()

    monkeypatch.setenv("ORCH_ROOT", str(_fake_orch_root(tmp_path)))
    monkeypatch.setenv("SECRETS_SCANNER_DEMO", "1")
    monkeypatch.setattr(subprocess_module, "run", fake_run)
    scanner._page_orch_console("postgres-dsn", "Bash")

    assert "subagent" not in captured["body"]


def test_main_passes_agent_id_and_type_from_payload_to_the_page(tmp_path):
    import os
    import subprocess

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)

    body_file = tmp_path / "body.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        "import sys, pathlib; "
        f"pathlib.Path({str(body_file)!r}).write_text(sys.stdin.read())\n"
    )

    real_looking_dsn = "postgres://appuser:Zq9mPlKx2RzT7@203.0.113.42:5432/billing"
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f'psql "{real_looking_dsn}"'},
        "tool_response": "SELECT 1",
        "agent_id": "subagent_abc123",
        "agent_type": "security-reviewer",
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0
    assert "security-reviewer" in body_file.read_text()
    assert "subagent_abc123" in body_file.read_text()


def test_subagent_transcript_path_derives_sibling_file_from_agent_id():
    # bus #49029/#49030: a subagent's own transcript lives at
    # <session-dir>/subagents/agent-<agent_id>.jsonl, a SIBLING of the session-level
    # transcript_path -- not something agent_id lets you compute from transcript_path's
    # basename alone.
    path = scanner._subagent_transcript_path(
        "/Users/x/.claude/projects/-proj/abc-123.jsonl", "a4dab81df04b30025"
    )
    assert path == "/Users/x/.claude/projects/-proj/subagents/agent-a4dab81df04b30025.jsonl"


def test_subagent_transcript_path_none_without_agent_id():
    assert scanner._subagent_transcript_path("/some/session.jsonl", None) is None


def test_subagent_transcript_path_none_without_transcript_path():
    assert scanner._subagent_transcript_path(None, "abc123") is None


def test_main_redacts_the_subagents_own_transcript_file_not_just_transcript_path(tmp_path, monkeypatch):
    # bus #49029 real incident: the scanner paged, but the raw secret stayed readable
    # because redact_recent_lines(transcript_path, ...) only ever touched the SESSION
    # transcript -- the subagent's own <session>/subagents/agent-<id>.jsonl, where the
    # actual tool_use/tool_result for that Bash call lives, was never opened at all.
    import os
    import subprocess

    session_dir = tmp_path / "session-abc"
    session_dir.mkdir()
    session_transcript = session_dir / "abc.jsonl"
    session_transcript.write_text(json.dumps({"type": "summary", "note": "unrelated"}) + "\n")

    subagents_dir = session_dir / "subagents"
    subagents_dir.mkdir()
    sub_transcript = subagents_dir / "agent-a4dab81df04b30025.jsonl"
    dsn = "postgres://orchuser:RealLooking9Zx@203.0.113.7:5432/orch"
    sub_transcript.write_text(
        json.dumps({"type": "tool_use", "name": "Bash", "input": {"command": f'echo "{dsn}"'}}) + "\n"
        + json.dumps({"type": "tool_result", "toolUseResult": {"output": dsn}}) + "\n"
    )

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f'echo "{dsn}"'},
        "tool_response": dsn,
        "transcript_path": str(session_transcript),
        "agent_id": "a4dab81df04b30025",
        "agent_type": "general-purpose",
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0

    redacted_sub_content = sub_transcript.read_text()
    assert "RealLooking9Zx" not in redacted_sub_content
    assert "REDACTED" in redacted_sub_content


# ---- persisted-output spill gap (bus #51269/#51285/#51290) ----------------------
# Claude Code caps the inline toolUseResult.stdout it writes to the transcript and
# spills the FULL output to a separate plain-text file (toolUseResult.persistedOutputPath)
# for large tool outputs. A secret-shaped value past that cap was invisible to both the
# normal tool_response scan and the existing JSON-line redaction -- confirmed empirically
# (cc-fleet-health, bus #51290) with a synthetic fixture-marked DSN placed past the cap:
# zero hits, zero redaction, the raw value sat untouched in the persisted file.
#
# NB: every synthetic DSN below is built via string concatenation ("postgres" + "://...")
# rather than one literal -- secrets_transcript_guard (PreToolUse) blocks a contiguous
# dsn-shaped literal in ANY tool call, including the Write that creates this very file.

def test_find_persisted_output_paths_reads_the_field(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(
        json.dumps({"type": "summary", "note": "unrelated"}) + "\n"
        + json.dumps({"type": "tool_result", "toolUseResult": {
            "stdout": "capped preview...", "persistedOutputPath": "/tmp/fake-spill.txt",
            "persistedOutputSize": 99999,
        }}) + "\n"
    )
    paths = scanner._find_persisted_output_paths(str(p))
    assert paths == ["/tmp/fake-spill.txt"]


def test_find_persisted_output_paths_empty_when_no_spill(tmp_path):
    p = tmp_path / "session.jsonl"
    p.write_text(json.dumps({"type": "tool_result", "toolUseResult": {"stdout": "small"}}) + "\n")
    assert scanner._find_persisted_output_paths(str(p)) == []


def test_scan_persisted_output_finds_a_hit_in_plain_text_file(tmp_path):
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.9:5432/orch"
    p = tmp_path / "spill.txt"
    p.write_text("filler\n" * 500 + dsn + "\nfiller\n" * 500)
    hits = scanner.scan_persisted_output(str(p))
    assert hits and any(h[0] == "postgres-dsn" for h in hits)


def test_scan_persisted_output_missing_file_returns_empty(tmp_path):
    assert scanner.scan_persisted_output(str(tmp_path / "does-not-exist.txt")) == []


def test_redact_persisted_output_replaces_in_place_and_is_idempotent(tmp_path):
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.9:5432/orch"
    p = tmp_path / "spill.txt"
    p.write_text(f"before\n{dsn}\nafter\n")
    changed = scanner.redact_persisted_output(str(p), "postgres-dsn", scanner.SECRET_PATTERNS["postgres-dsn"])
    assert changed is True
    after = p.read_text()
    assert dsn not in after
    assert "REDACTED" in after
    assert "before" in after and "after" in after
    # idempotent: a second pass over the already-redacted file is a no-op
    changed_again = scanner.redact_persisted_output(str(p), "postgres-dsn", scanner.SECRET_PATTERNS["postgres-dsn"])
    assert changed_again is False


def test_main_catches_and_redacts_a_secret_only_present_in_the_persisted_spill_file(tmp_path):
    # The defining case: the capped tool_response/transcript inline copy NEVER contains
    # the secret at all (simulating the real spill) -- only the external file does.
    import os
    import subprocess

    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.11:5432/orch"
    spill = tmp_path / "spill.txt"
    spill.write_text("filler\n" * 2000 + dsn + "\nfiller\n" * 2000)

    transcript = tmp_path / "session.jsonl"
    capped_preview = "filler\n" * 10  # the secret is NOT in here
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "some-command-producing-big-output"}}
        ]}}) + "\n"
        + json.dumps({"type": "tool_result", "toolUseResult": {
            "stdout": capped_preview, "persistedOutputPath": str(spill), "persistedOutputSize": len(dsn) * 4001,
        }}) + "\n"
    )

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)
    counter = tmp_path / "page_calls.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        f"import pathlib; pathlib.Path({str(counter)!r}).open('a').write('x\\n')\n"
    )

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "some-command-producing-big-output"},
        "tool_response": capped_preview,  # the hook's own view of tool_response: also capped
        "transcript_path": str(transcript),
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0
    assert "secret-shaped content" in r.stderr, "the persisted-file hit must still be detected and reported"

    after_spill = spill.read_text()
    assert dsn not in after_spill, "the raw secret must be redacted in the EXTERNAL spill file, not just skipped"
    assert "REDACTED" in after_spill
    assert counter.exists() and counter.read_text().count("x") == 1, "a real (non-fixture) persisted-only hit must still page exactly once"


def test_main_skips_paging_for_a_fixture_hit_found_only_in_the_persisted_file(tmp_path):
    import os
    import subprocess

    fake_dsn = "postgres" + "://test-user:test-pass@fakehost123.internal:5432/mydb"
    spill = tmp_path / "spill.txt"
    spill.write_text("filler\n" * 600 + fake_dsn + "\nfiller\n" * 600)

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"type": "tool_result", "toolUseResult": {
            "stdout": "filler\n" * 10, "persistedOutputPath": str(spill),
        }}) + "\n"
    )

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)
    counter = tmp_path / "page_calls.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        f"import pathlib; pathlib.Path({str(counter)!r}).open('a').write('x\\n')\n"
    )

    payload = json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "cat big.txt"},
        "tool_response": "filler\n" * 10, "transcript_path": str(transcript),
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0
    assert "REDACTED" in spill.read_text(), "redaction still runs for a fixture hit"
    page_count = counter.read_text().count("x") if counter.exists() else 0
    assert page_count == 0, "a known fixture DSN found only in the persisted file must not page"


# ---- durable hook event log (bus #51285/#51290): the originating event was never ----
# recoverable after a page fired. Log enough to locate it next time, NEVER the value.

def test_log_event_writes_metadata_never_the_value(tmp_path, monkeypatch):
    monkeypatch.setenv("ORCH_ROOT", str(tmp_path))
    monkeypatch.setattr(scanner, "EVENT_LOG_PATH", str(tmp_path / "logs" / "secrets_output_scanner_events.log"))
    secret_value = "RealLooking9Zx" + "SecretValueNeverLogged"
    scanner._log_event("postgres-dsn", "Bash", "/tmp/session.jsonl", None, "session.jsonl",
                        None, None, "cc-fleet-health", cwd="/Users/sheikhmusa/wingmen/fleet-health")
    log_path = Path(scanner.EVENT_LOG_PATH)
    assert log_path.exists()
    content = log_path.read_text()
    assert secret_value not in content
    record = json.loads(content.strip().splitlines()[-1])
    assert record["cls"] == "postgres-dsn"
    assert record["tool_name"] == "Bash"
    assert record["transcript_path"] == "/tmp/session.jsonl"
    assert record["session_id"] == "session.jsonl"
    assert record["agent"] == "cc-fleet-health"
    assert record["cwd"] == "/Users/sheikhmusa/wingmen/fleet-health"
    import stat
    mode = stat.S_IMODE(log_path.stat().st_mode)
    assert mode == 0o600, f"event log must be mode 600, got {oct(mode)}"


def test_log_event_never_crashes_on_unwritable_path(monkeypatch):
    monkeypatch.setattr(scanner, "EVENT_LOG_PATH", "/nonexistent-root/no/such/dir/events.log")
    scanner._log_event("postgres-dsn", "Bash", None, None, None, None, None, "unknown-agent")  # must not raise


def test_session_id_from_transcript_path_strips_extension():
    assert scanner._session_id_from_transcript_path(
        "/Users/x/.claude/projects/-Users-x-foo/4e719472-7285-4542-bdd2-fe2f45bf1449.jsonl"
    ) == "4e719472-7285-4542-bdd2-fe2f45bf1449"


def test_session_id_from_transcript_path_none_when_no_path():
    assert scanner._session_id_from_transcript_path(None) is None


def test_main_logs_event_with_cwd_for_unknown_agent_session(tmp_path, monkeypatch):
    import os
    import subprocess

    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.13:5432/orch"
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(json.dumps({"type": "tool_result", "toolUseResult": {"output": dsn}}) + "\n")

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    env.pop("AGENT_ID", None)
    env.pop("CC_BASE_AGENT_ID", None)
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)

    payload = json.dumps({
        "tool_name": "Bash", "tool_input": {"command": "echo ..."}, "tool_response": dsn,
        "transcript_path": str(transcript), "cwd": "/Users/sheikhmusa/wingmen/fleet-health",
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0

    log_path = orch_root / "logs" / "secrets_output_scanner_events.log"
    assert log_path.exists()
    record = json.loads(log_path.read_text().strip().splitlines()[-1])
    assert record["agent"] == "unknown-agent"
    assert record["cwd"] == "/Users/sheikhmusa/wingmen/fleet-health"
    assert dsn not in log_path.read_text()


# ---- cc-quality #51317 (BLOCKING): invalid UTF-8 in a persisted file must not crash ----
# the hook before the main redact/log/page loop runs -- a crash there skipped EVERY
# hit in that invocation, including real ones found via the normal (non-persisted)
# tool_response/tool_input path.

def test_scan_persisted_output_does_not_crash_on_invalid_utf8(tmp_path):
    p = tmp_path / "spill.bin"
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.21:5432/orch"
    with open(p, "wb") as f:
        f.write(b"before-binary\n")
        f.write(b"\xff\xfe\x00\xff invalid utf-8 bytes here \xc0\xaf")
        f.write(("\n" + dsn + "\n").encode("utf-8"))
        f.write(b"\xff\xfe more invalid bytes")
    hits = scanner.scan_persisted_output(str(p))  # must not raise
    assert hits and any(h[0] == "postgres-dsn" for h in hits), \
        "a secret-shaped match in the valid text around corrupted bytes must still be found"


def test_redact_persisted_output_does_not_crash_on_invalid_utf8(tmp_path):
    p = tmp_path / "spill.bin"
    dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.22:5432/orch"
    with open(p, "wb") as f:
        f.write(b"\xff\xfe\x00\xff " + dsn.encode("utf-8") + b" \xc0\xaf more bytes")
    changed = scanner.redact_persisted_output(str(p), "postgres-dsn", scanner.SECRET_PATTERNS["postgres-dsn"])
    assert changed is True
    after = p.read_text(encoding="utf-8", errors="replace")
    assert dsn not in after
    assert "REDACTED" in after


def test_main_still_redacts_and_pages_a_real_inline_hit_when_the_persisted_file_is_binary(tmp_path):
    # The exact failure mode cc-quality found: a crash in the persisted-file step must
    # never suppress redaction/paging for a REAL hit found via the normal path.
    import os
    import subprocess

    inline_dsn = "postgres" + "://orchuser:RealLooking9Zx@203.0.113.23:5432/orch"
    spill = tmp_path / "spill.bin"
    with open(spill, "wb") as f:
        f.write(b"\xff\xfe\x00\xff invalid utf-8, no secret shape here \xc0\xaf")

    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": f'echo "{inline_dsn}"'}}
        ]}}) + "\n"
        + json.dumps({"type": "tool_result", "toolUseResult": {
            "stdout": inline_dsn, "persistedOutputPath": str(spill),
        }}) + "\n"
    )

    env = dict(os.environ, SECRETS_SCANNER_DEMO="1")
    orch_root_dir = tmp_path / "orch_root"
    orch_root_dir.mkdir()
    orch_root = _fake_orch_root(orch_root_dir)
    env["ORCH_ROOT"] = str(orch_root)
    counter = tmp_path / "page_calls.txt"
    (orch_root / "scripts" / "bus_send.py").write_text(
        f"import pathlib; pathlib.Path({str(counter)!r}).open('a').write('x\\n')\n"
    )

    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": f'echo "{inline_dsn}"'},
        "tool_response": inline_dsn,
        "transcript_path": str(transcript),
    })
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload, text=True, capture_output=True, env=env)
    assert r.returncode == 0, f"must not crash; stderr={r.stderr}"
    assert inline_dsn not in transcript.read_text()
    assert "REDACTED" in transcript.read_text()
    assert counter.exists() and counter.read_text().count("x") == 1
