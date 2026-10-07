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


# ── orch-console #49038: plaintext mode + any-real-token matcher + file-history classing ──
# A FABRICATED token of real Anthropic shape/length (sk-ant- + >=60 chars). Not in any manifest.
SHAPE_TOKEN = "sk-ant-oat01-" + "FAKEshape" * 9 + "-Zz"          # 13 + 81 + 3 = 97 chars


def _manifest(tmp_path):
    env = tmp_path / ".env"
    _write(env, "ANTHROPIC_API_KEY=%s\n" % FAKE_KEY)
    return s.build_secret_set([str(env)])


def test_shape_matcher_catches_token_absent_from_manifest(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, json.dumps({"out": "TOKEN_OVERRIDE=%s done" % SHAPE_TOKEN}) + "\n")
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=True, ledger_path=str(tmp_path / "l"))
    assert rep["matches_before"] == 1 and rep["matches_after"] == 0 and rep["error"] is None
    assert rep["by_source"] == {"shape": 1}
    body = f.read_text()
    assert SHAPE_TOKEN not in body and "done" in body
    json.loads(body)                                   # still a valid transcript line


def test_shape_matcher_can_be_disabled(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, json.dumps({"out": SHAPE_TOKEN}) + "\n")
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=False, shape_match=False)
    assert rep["matches_before"] == 0


def test_short_sk_ant_strings_are_not_shape_matched(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, json.dumps({"out": "see sk-ant-api03-xxxx and sk-ant-" + "a" * 59}) + "\n")
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=False)
    assert rep["matches_before"] == 0


def test_plaintext_file_is_redacted_without_json_gate(tmp_path):
    f = tmp_path / "tool-results" / "abc.txt"; f.parent.mkdir()
    _write(f, "env dump\nCLAUDE_CODE_OAUTH_TOKEN_OVERRIDE=%s\nKEY=%s\nnot json at all {\n"
           % (SHAPE_TOKEN, FAKE_KEY))
    size = f.stat().st_size
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=True, ledger_path=str(tmp_path / "l"))
    assert rep["mode"] == "plaintext" and rep["error"] is None
    assert rep["matches_before"] == 2 and rep["matches_after"] == 0
    assert f.stat().st_size == size
    body = f.read_text()
    assert SHAPE_TOKEN not in body and FAKE_KEY not in body and "not json at all {" in body


def test_jsonl_keeps_its_json_gate(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, json.dumps({"k": SHAPE_TOKEN}) + "\n")
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=False)
    assert rep["mode"] == "jsonl"


def _fh(tmp_path, sess, name, content, orig=None):
    """Build ~/.claude-shaped fixture: file-history/<sess>/<name> (+ the session transcript that
    records which ORIGINAL file the backup snapshots, as Claude Code writes it)."""
    root = tmp_path / ".claude"
    f = root / "file-history" / sess / name; f.parent.mkdir(parents=True, exist_ok=True)
    _write(f, content)
    if orig is not None:
        pj = root / "projects" / "-proj"; pj.mkdir(parents=True, exist_ok=True)
        rec = {"type": "file-history-snapshot",
               "snapshot": {"trackedFileBackups": {orig: {"backupFileName": name, "version": 1}}}}
        with open(pj / (sess + ".jsonl"), "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    return f


ENV_BODY = "# orchestrator env\nANTHROPIC_API_KEY=%s\nPORT=5432\nMODE=prod\n" % FAKE_KEY


def test_file_history_env_snapshot_is_classified_not_redacted(tmp_path):
    f = _fh(tmp_path, "s1", "deadbeef@v3", ENV_BODY, orig="/Users/x/wingmen/orchestrator/.env")
    before = f.read_bytes()
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=True, ledger_path=str(tmp_path / "l"))
    assert rep["class"] == "env-snapshot"
    assert rep["matches_before"] == 1
    assert f.read_bytes() == before, "an .env snapshot is the same trust boundary as .env: never edited"


def test_env_local_and_named_env_files_count_as_env(tmp_path):
    for i, orig in enumerate(["/p/.env.local", "/k/irsyad-support-bot.env", "/p/.env.production"]):
        f = _fh(tmp_path, "s%d" % i, "aa%d@v1" % i, ENV_BODY, orig=orig)
        assert s.classify(str(f), f.read_bytes()) == "env-snapshot", orig


def test_export_heavy_script_is_not_an_env_snapshot(tmp_path):
    # cc-quality #49063: a deploy wrapper dominated by `export VAR=value` passes the 80% shape
    # test, but it is a SCRIPT holding a secret = a real leak. Identity must decide, not shape.
    script = ("#!/bin/bash\nset -euo pipefail\n# fetch secrets\n"
              + "".join("export V%d=x\n" % i for i in range(9))
              + "export TOKEN=%s\necho done\n" % FAKE_KEY)
    f = _fh(tmp_path, "s9", "cafe@v1", script, orig="/Users/x/wingmen/orchestrator/scripts/fetch_secrets.sh")
    assert s.classify(str(f), f.read_bytes()) == "file-history"
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=True, ledger_path=str(tmp_path / "l"))
    assert rep["matches_after"] == 0 and FAKE_KEY not in f.read_text()


def test_trackingpath_record_shape_resolves(tmp_path):
    # the 2nd shape Claude Code writes: {"trackingPath": "<path>", "backup": {"backupFileName": …}}
    root = tmp_path / ".claude"
    f = root / "file-history" / "sx" / "abc@v1"; f.parent.mkdir(parents=True); _write(f, ENV_BODY)
    pj = root / "projects" / "-p"; pj.mkdir(parents=True)
    rec = {"type": "file-history-snapshot", "snapshot": {"trackingPath": "/Users/x/wingmen/orchestrator/.env",
           "backup": {"backupFileName": "abc@v1", "version": 1}}}
    _write(pj / "sx.jsonl", json.dumps(rec, separators=(",", ":")) + "\n")
    assert s.file_history_origin(str(f)) == "/Users/x/wingmen/orchestrator/.env"
    assert s.classify(str(f), f.read_bytes()) == "env-snapshot"


def test_relative_key_with_realparentdir_resolves(tmp_path):
    # 3rd shape (seen on gzb 2026-10-02): trackedFileBackups keyed by a RELATIVE path, with the
    # directory in "realParentDir". Must resolve to <realParentDir>/<key>.
    root = tmp_path / ".claude"
    f = root / "file-history" / "sr" / "f842@v2"; f.parent.mkdir(parents=True); _write(f, ENV_BODY)
    pj = root / "projects" / "-p"; pj.mkdir(parents=True)
    rec = {"type": "file-history-snapshot", "snapshot": {"trackedFileBackups": {".env": {
        "backupFileName": "f842@v2", "version": 2, "realParentDir": "/home/x/wingmen/orchestrator"}}}}
    _write(pj / "sr.jsonl", json.dumps(rec, separators=(",", ":")) + "\n")
    assert s.file_history_origin(str(f)) == "/home/x/wingmen/orchestrator/.env"
    assert s.classify(str(f), f.read_bytes()) == "env-snapshot"


def test_relative_key_without_parentdir_stays_unresolved(tmp_path):
    root = tmp_path / ".claude"
    f = root / "file-history" / "sq" / "f843@v1"; f.parent.mkdir(parents=True); _write(f, ENV_BODY)
    pj = root / "projects" / "-p"; pj.mkdir(parents=True)
    rec = {"snapshot": {"trackedFileBackups": {".env": {"backupFileName": "f843@v1"}}}}
    _write(pj / "sq.jsonl", json.dumps(rec) + "\n")
    assert s.file_history_origin(str(f)) is None
    assert s.classify(str(f), f.read_bytes()) == "file-history"


def test_unresolvable_origin_fails_toward_leak(tmp_path):
    f = _fh(tmp_path, "s7", "beef@v1", ENV_BODY, orig=None)          # no session record
    assert s.classify(str(f), f.read_bytes()) == "file-history"


def test_env_named_but_not_env_shaped_is_a_leak(tmp_path):
    f = _fh(tmp_path, "s8", "f00d@v1", "#!/bin/bash\ncurl -H 'k: %s' x\necho\n" % FAKE_KEY, orig="/p/.env")
    assert s.classify(str(f), f.read_bytes()) == "file-history"


def test_file_history_non_env_snapshot_still_counts_as_a_leak(tmp_path):
    f = _fh(tmp_path, "s2", "cafe@v1", "#!/bin/bash\necho hello\ncurl -H 'x-api-key: %s' https://x\nexit 0\n"
            % FAKE_KEY, orig="/p/scripts/x.sh")
    rep = s.sweep_file(str(f), _manifest(tmp_path), execute=False)
    assert rep["class"] == "file-history"


def test_main_pages_count_excludes_env_snapshots(tmp_path, capsys):
    env = tmp_path / ".env"; _write(env, "ANTHROPIC_API_KEY=%s\n" % FAKE_KEY)
    snap = _fh(tmp_path, "s", "x@v1", "ANTHROPIC_API_KEY=%s\nA=b\n" % FAKE_KEY, orig="/p/.env")
    tr = tmp_path / "t.jsonl"; _write(tr, json.dumps({"o": FAKE_KEY}) + "\n")
    rc = s.main(["--env", str(env), "--scan", str(snap), str(tr), "--dry-run", "--report-json"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert d["total_before"] == 2 and d["total_paging"] == 1 and d["env_snapshot_hits"] == 1
    assert SHAPE_TOKEN not in json.dumps(d) and FAKE_KEY not in json.dumps(d)


def test_run_wrapper_uses_ledger_not_removed_backup_dir_flag():
    src = open(os.path.join(os.path.dirname(__file__), "..", "scripts", "secret_hash_sweep_run.sh")).read()
    assert "--backup-dir" not in src, "the tool removed --backup-dir (#48678); armed runs would crash"
    assert "--ledger" in src
    assert "tool-results" in src, "recurring sweep must cover tool-results/*.txt (#49038)"


# ---- orch-console #51379: identifier-shaped keys/values must not be treated as secrets ----
# (SEED_USER_ID, a plain UUID identifier, false-positived the real incident this covers.)

def test_is_public_value_excludes_id_suffixed_key():
    assert s._is_public_value("SEED_USER_ID", "e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea") is True
    assert s._is_public_value("CLIENT_ID", "some-opaque-client-identifier-string") is True


def test_is_public_value_excludes_uuid_suffixed_key():
    assert s._is_public_value("SESSION_UUID", "e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea") is True


def test_is_public_value_excludes_org_suffixed_key():
    assert s._is_public_value("DEFAULT_ORG", "some-org-slug-identifier-value") is True


def test_is_public_value_excludes_bare_uuid_value_regardless_of_key_name():
    # defense in depth: a UUID-shaped value is never a secret class, even under an
    # unrelated-looking key name that none of the suffix rules would catch.
    assert s._is_public_value("WEIRD_KEY_NAME", "e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea") is True


def test_is_public_value_excludes_by_key_name_even_if_the_value_looks_secret_shaped():
    # the rule is intentionally name-based (matches orch-console's ask): a key accidentally
    # named *_ID that happens to hold a real long opaque credential is STILL excluded here --
    # there is no secondary "but does it look random enough" override. Documents that
    # trade-off explicitly so a future change to the rule has to touch this test on purpose.
    assert s._is_public_value("SEED_USER_ID", FAKE_KEY) is True


def test_is_public_value_still_catches_a_real_secret_under_a_plain_key():
    assert s._is_public_value("ANTHROPIC_API_KEY", FAKE_KEY) is False


def test_build_secret_set_excludes_id_uuid_org_keys_and_bare_uuids(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "SEED_USER_ID=e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea",
        "SESSION_UUID=f1a2b3c4-d5e6-7890-abcd-ef1234567890",
        "DEFAULT_ORG=acme-corp-org-slug",
        "RANDOM_FIELD=12345678-9abc-def0-1234-567890abcdef",  # bare UUID, unrelated key name
        "ANTHROPIC_API_KEY=%s" % FAKE_KEY,
        "",
    ]))
    secrets = s.build_secret_set([str(env)])
    hashes = set(secrets.values())
    assert s.hash8(FAKE_KEY.encode()) in hashes, "the real secret must still be caught"
    assert len(secrets) == 1, "only the real secret should survive -- all 4 identifiers excluded"


def test_sweep_file_does_not_flag_seed_user_id_appearing_in_a_transcript(tmp_path):
    env = tmp_path / ".env"
    seed_id = "e9b3f7f9-36d0-4f4b-80d7-7529b6bdc2ea"
    _write(env, "SEED_USER_ID=%s\n" % seed_id)
    secrets = s.build_secret_set([str(env)])
    assert secrets == {}, "SEED_USER_ID must not enter the secret set at all"

    tr = tmp_path / "t.jsonl"
    _write(tr, json.dumps({"o": "query result: %s appears here" % seed_id}) + "\n")
    rep = s.sweep_file(str(tr), secrets, execute=False)
    assert rep["matches_before"] == 0, "a dormant/excluded identifier must never page as a leak"


# ---- orch-console #51400: live-append race protection before an in-place write ----

def test_file_unchanged_since_true_when_nothing_changed(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, "hello\n")
    st = os.stat(f)
    assert s._file_unchanged_since(str(f), st.st_size, st.st_mtime_ns) is True


def test_file_unchanged_since_false_when_size_changed(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, "hello\n")
    st = os.stat(f)
    _write(f, "hello world, appended\n")  # simulates a live session appending
    assert s._file_unchanged_since(str(f), st.st_size, st.st_mtime_ns) is False


def test_file_unchanged_since_false_when_file_missing(tmp_path):
    f = tmp_path / "gone.jsonl"
    assert s._file_unchanged_since(str(f), 0, 0) is False


def test_sweep_file_aborts_execute_when_file_changes_between_read_and_write(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    _write(env, "ANTHROPIC_API_KEY=%s\n" % FAKE_KEY)
    secrets = s.build_secret_set([str(env)])

    tr = tmp_path / "t.jsonl"
    _write(tr, json.dumps({"o": FAKE_KEY}) + "\n")

    # simulate a live append landing exactly between sweep_file's read and its pre-write
    # re-check: make the very first _file_unchanged_since call (the one inside sweep_file)
    # see a changed file, as if another process appended in that window.
    monkeypatch.setattr(s, "_file_unchanged_since", lambda *a, **k: False)

    before_bytes = tr.read_bytes()
    rep = s.sweep_file(str(tr), secrets, execute=True, ledger_path=str(tmp_path / "ledger.jsonl"))

    assert rep["error"] is not None and "ABORTED" in rep["error"]
    assert tr.read_bytes() == before_bytes, "must not have written anything once aborted"
    assert not (tmp_path / "ledger.jsonl").exists(), "must not have logged a ledger entry for an aborted write"
    assert rep["matches_after"] == rep["matches_before"], "report must reflect nothing was redacted"


def test_sweep_file_executes_normally_when_file_is_genuinely_unchanged(tmp_path):
    env = tmp_path / ".env"
    _write(env, "ANTHROPIC_API_KEY=%s\n" % FAKE_KEY)
    secrets = s.build_secret_set([str(env)])

    tr = tmp_path / "t.jsonl"
    _write(tr, json.dumps({"o": FAKE_KEY}) + "\n")

    rep = s.sweep_file(str(tr), secrets, execute=True, ledger_path=str(tmp_path / "ledger.jsonl"))
    assert rep["error"] is None
    assert rep["matches_after"] == 0
    assert FAKE_KEY not in tr.read_text()
    assert (tmp_path / "ledger.jsonl").exists()


# ── bus #56377 regressions: NAZIM_MODEL ("claude-fable-5-1", 16 chars) false-positived ──

def test_model_role_session_path_dir_keys_excluded(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "NAZIM_MODEL=claude-fable-5-1",          # 16 chars, the real incident value
        "ORCH_BODY_ROLE=console-manager-role",   # _ROLE
        "ORCH_TMUX_SESSION=nazim-session-name",  # _SESSION
        "SOME_CONFIG_PATH=relative/non/abs/path",  # _PATH, not absolute (shape check wouldn't catch it)
        "SOME_WORK_DIR=relative/non/abs/dir",      # _DIR
    ]))
    secrets = s.build_secret_set([str(env)])
    vals = set(secrets)
    assert b"claude-fable-5-1" not in vals
    assert b"console-manager-role" not in vals
    assert b"nazim-session-name" not in vals
    assert b"relative/non/abs/path" not in vals
    assert b"relative/non/abs/dir" not in vals


def test_bool_int_values_excluded(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "AUTO_WAKE_ENABLED=true",
        "CONSOLE_PORT=87878787878787",   # digits-only, long -> still excluded (shape, not length)
    ]))
    secrets = s.build_secret_set([str(env)])
    vals = set(secrets)
    assert b"true" not in vals
    assert b"87878787878787" not in vals


def test_length_floor_noncred_vs_credential_key(tmp_path):
    env = tmp_path / ".env"
    _write(env, "\n".join([
        "RANDOM_LABEL=exactlysixteench",    # 16 chars, no credential-shaped key -> excluded (< 20 floor)
        "SOME_API_KEY=exactlysixteench",    # same 16-char VALUE, but _KEY key -> kept (credential floor=16)
    ]))
    secrets = s.build_secret_set([str(env)])
    vals = set(secrets)
    assert b"exactlysixteench" in vals   # kept via SOME_API_KEY
    # only ONE value in the dict either way (same bytes) -- assert the credential path is what let it in
    assert s._CREDENTIAL_KEY_RE.search("SOME_API_KEY")
    assert not s._CREDENTIAL_KEY_RE.search("RANDOM_LABEL")


def test_model_id_shaped_value_yields_zero_spans_end_to_end(tmp_path):
    """The exact unit test orch-console asked for (#56377 item 2): a NAZIM_MODEL-shaped
    .env value must produce 0 redacted spans scanning a transcript that contains it."""
    env = tmp_path / ".env"
    _write(env, "NAZIM_MODEL=claude-fable-5-1\n")
    secrets = s.build_secret_set([str(env)])

    tr = tmp_path / "t.jsonl"
    _write(tr, json.dumps({"o": "booted with --model claude-fable-5-1 ok"}) + "\n")

    rep = s.sweep_file(str(tr), secrets, execute=False)
    assert rep["matches_before"] == 0


def test_detect_false_positive_shapes_flags_short_widespread_hash():
    value_len_by_hash8 = {"f8064a23": 16, "deadbeef": 64}
    reports = []
    for i in range(5):
        reports.append({"by_hash": {"f8064a23": 20}, "file": "/t/f%d.jsonl" % i})
    reports.append({"by_hash": {"deadbeef": 500}, "file": "/t/real-secret.jsonl"})
    suspects = s.detect_false_positive_shapes(reports, value_len_by_hash8)
    hashes = {h for (h, *_rest) in suspects}
    assert "f8064a23" in hashes          # 100 spans / 5 files / len16 -> trips
    assert "deadbeef" not in hashes      # long value, real-secret shape -> never trips


def test_detect_false_positive_shapes_does_not_trip_below_thresholds():
    value_len_by_hash8 = {"abc12345": 16}
    reports = [{"by_hash": {"abc12345": 5}, "file": "/t/only-one-file.jsonl"}]
    assert s.detect_false_positive_shapes(reports, value_len_by_hash8) == []


# ── bus #56377 reversal: load_ledger_spans / reverse_file ──

def test_load_ledger_spans_filters_by_hash8_and_dedups(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    _write(ledger, "\n".join([
        json.dumps({"file": "/a.jsonl", "offset": 10, "length": 16, "hash8": "f8064a23"}),
        json.dumps({"file": "/a.jsonl", "offset": 10, "length": 16, "hash8": "f8064a23"}),  # dup
        json.dumps({"file": "/a.jsonl", "offset": 40, "length": 16, "hash8": "f8064a23"}),
        json.dumps({"file": "/b.jsonl", "offset": 5, "length": 8, "hash8": "deadbeef"}),    # other hash
    ]))
    spans = s.load_ledger_spans(str(ledger), "f8064a23")
    assert spans == {"/a.jsonl": [(10, 16), (40, 16)]}


def test_reverse_file_restores_redacted_span(tmp_path):
    value = b"claude-fable-5-1"
    h8 = s.hash8(value)
    marker = s.marker_for(value, h8)
    assert marker == b"*" * len(value)   # the tag doesn't fit in 16 bytes -> all-stars marker

    tr = tmp_path / "t.jsonl"
    line = json.dumps({"o": "pre " + marker.decode() + " post"}) + "\n"
    _write(tr, line)
    offset = line.encode().index(marker)

    rep = s.reverse_file(str(tr), [(offset, len(value))], value, h8, execute=True)
    assert rep["error"] is None
    assert rep["restored"] == 1
    assert rep["mismatched"] == 0
    assert value in tr.read_bytes()
    assert json.loads(tr.read_text().rstrip("\n"))["o"] == "pre claude-fable-5-1 post"


def test_reverse_file_is_idempotent_on_already_restored_span(tmp_path):
    value = b"claude-fable-5-1"
    h8 = s.hash8(value)
    tr = tmp_path / "t.jsonl"
    line = json.dumps({"o": "pre " + value.decode() + " post"}) + "\n"
    _write(tr, line)
    offset = line.encode().index(value)

    rep = s.reverse_file(str(tr), [(offset, len(value))], value, h8, execute=True)
    assert rep["error"] is None
    assert rep["restored"] == 0
    assert rep["already_ok"] == 1


def test_reverse_file_aborts_on_unexpected_content_no_writes(tmp_path):
    value = b"claude-fable-5-1"
    h8 = s.hash8(value)
    tr = tmp_path / "t.jsonl"
    line = json.dumps({"o": "pre SOMETHING-ELSE-16b post"}) + "\n"
    _write(tr, line)
    offset = line.encode().index(b"SOMETHING-ELSE-16b")

    before = tr.read_bytes()
    rep = s.reverse_file(str(tr), [(offset, len(value))], value, h8, execute=True)
    assert rep["error"] is not None
    assert rep["mismatched"] == 1
    assert tr.read_bytes() == before, "must not write anything when content doesn't match"


def test_reverse_file_verify_only_does_not_write(tmp_path):
    value = b"claude-fable-5-1"
    h8 = s.hash8(value)
    marker = s.marker_for(value, h8)
    tr = tmp_path / "t.jsonl"
    line = json.dumps({"o": "pre " + marker.decode() + " post"}) + "\n"
    _write(tr, line)
    offset = line.encode().index(marker)

    before = tr.read_bytes()
    rep = s.reverse_file(str(tr), [(offset, len(value))], value, h8, execute=False)
    assert rep["restored"] == 1   # reports what WOULD happen
    assert tr.read_bytes() == before, "verify-only must never write"


def test_armed_suffixed_key_stays_credential_shaped(tmp_path):
    """orch-console #56425: STOREFRONT_CONFIRM_PAID_ARMED is a money-path arming
    TOKEN despite the flag-shaped name -- _ARMED$ must stay on the credential floor,
    never get the higher non-credential length floor (which could exclude a short
    future arming token the same way NAZIM_MODEL was wrongly excluded before)."""
    assert s._CREDENTIAL_KEY_RE.search("STOREFRONT_CONFIRM_PAID_ARMED")
    env = tmp_path / ".env"
    _write(env, "STOREFRONT_CONFIRM_PAID_ARMED=exactly16chars!!\n")  # 16 chars -> would fail the 20-floor
    secrets = s.build_secret_set([str(env)])
    assert b"exactly16chars!!" in secrets
