"""session_provider_guard.py — no cross-provider `--resume` (orch-console bus #87276,
P0 Musa op 36795).

THE INCIDENT THIS PINS (31 Oct): a lane ran on DeepSeek, its token was switched, and
it came back on an Anthropic model `--resume`-ing the same transcript. Claude Code
replays the whole conversation to the provider on every turn, so whatever the
transcript held went to a provider that had never seen it.

WHAT IS ASSERTED HERE
  1. The provider table is IN LOCKSTEP with the shipped shell routing
     (scripts/lib/provider_routing.sh) — a routing change cannot silently desync it.
  2. A resume on the SAME provider is allowed without a scan.
  3. A resume ACROSS providers is allowed only when the transcript scans clean, and
     the decision line carries the transcript sha256 prefix.
  4. A resume ACROSS providers with a seeded fake credential is REFUSED (exit 3),
     the refusal names the class and line, and THE VALUE ITSELF IS NEVER PRINTED.
  5. Fail-closed: an unreadable/oversized transcript or an internal error is a
     REFUSAL, never an allow.
  6. The launchers actually call the guard BEFORE the claude launch line, and the
     strip helper removes the session id along with the flag (a bare --resume opens
     the interactive picker — equally a cross-provider resume).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUARD = REPO / "scripts" / "lib" / "session_provider_guard.py"
SHIM = REPO / "scripts" / "lib" / "session_provider_guard.sh"

sys.path.insert(0, str(REPO / "scripts" / "lib"))
import session_provider_guard as spg  # noqa: E402

# Built at runtime so the literal in this file is not itself a scannable key shape
# (secrets_transcript_guard blocks writing one inline — the sibling guard working).
FAKE_ANTHROPIC_KEY = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0K1l2"


# ── helpers ────────────────────────────────────────────────────────────────────

def write_session(root: Path, cwd: str, session: str, model: str | None,
                  tail_text: str = "") -> Path:
    """A transcript in Claude Code's layout: <root>/<project-slug>/<session>.jsonl."""
    d = root / spg._project_slug(cwd)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session}.jsonl"
    lines = [json.dumps({"type": "user", "message": {"role": "user", "content": "hello"}})]
    if model:
        lines.append(json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "model": model, "content": []},
        }))
    if tail_text:
        lines.append(json.dumps({
            "type": "user", "message": {"role": "user", "content": tail_text},
        }))
    path.write_text("\n".join(lines) + "\n")
    return path


def run_check(model, *, root, session=None, transcript=None, cont_cwd=None,
              log_dir=None, json_out=False):
    cmd = [sys.executable, str(GUARD), "check", "--model", model,
           "--transcript-root", str(root)]
    if session:
        cmd += ["--session", session]
    if transcript:
        cmd += ["--transcript", str(transcript)]
    if cont_cwd:
        cmd += ["--continue", "--cwd", str(cont_cwd)]
    if log_dir:
        cmd += ["--log-dir", str(log_dir)]
    if json_out:
        cmd += ["--json"]
    return subprocess.run(cmd, text=True, capture_output=True)


# ── 1. lockstep with the shipped routing table ────────────────────────────────

MODEL_CORPUS = [
    "claude-opus-4-8", "claude-sonnet-5-5", "claude-haiku-4-5",
    "glm-5.3", "glm-4.7",
    "kimi-k2.6", "moonshot-v1-128k",
    "deepseek-v4-pro", "deepseek-flash",
    "qwen3.6-flash",
    "moonshotai/kimi-k2", "deepseek/deepseek-chat", "qwen/qwen3-coder", "z-ai/glm-4.6",
    "some-unknown-model",
]
SHELL_LABEL_TO_KEY = {
    "anthropic": "anthropic",  # no matching arm: the process keeps Anthropic creds
    "z.ai GLM": "zai",
    "Moonshot Kimi (native)": "moonshot",
    "DeepSeek (native)": "deepseek",
    "OpenRouter": "openrouter",
    "Qwen (DashScope Coding Plan, native)": "qwen",
}


def shell_provider(model: str) -> str:
    script = (
        'source "$1" || exit 9; '
        'if row="$(_provider_for_model "$2")"; then printf "%s" "${row%%|*}"; '
        'else printf "anthropic"; fi'
    )
    r = subprocess.run(
        ["bash", "-c", script, "_", str(REPO / "scripts" / "lib" / "provider_routing.sh"),
         model],
        text=True, capture_output=True)
    assert r.returncode == 0, r.stderr
    return SHELL_LABEL_TO_KEY[r.stdout.strip()]


def test_provider_table_is_in_lockstep_with_the_shipped_routing():
    for model in MODEL_CORPUS:
        assert spg.provider_for_model(model) == shell_provider(model), (
            f"session_provider_guard disagrees with provider_routing.sh for {model!r}: "
            "a resume could be judged against a provider that is not the one it routes to")


# ── 2. same provider: no scan, no fuss ────────────────────────────────────────

def test_same_provider_resume_is_allowed(tmp_path):
    root = tmp_path / "projects"
    write_session(root, "/srv/lane", "sess-same", "claude-opus-4-8",
                  tail_text=FAKE_ANTHROPIC_KEY)  # even a DIRTY one: nothing crosses
    r = run_check("claude-opus-4-8", root=root, session="sess-same")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("ALLOW")
    assert "same provider" in r.stdout


# ── 3. cross provider, clean transcript: allowed, and attributable ─────────────

def test_cross_provider_resume_with_a_clean_transcript_is_allowed(tmp_path):
    root = tmp_path / "projects"
    write_session(root, "/srv/lane", "sess-clean", "deepseek-v4-pro",
                  tail_text="routine store analysis, no credentials here")
    r = run_check("claude-opus-4-8", root=root, session="sess-clean")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scans clean" in r.stdout
    assert "sha256:" in r.stdout  # the judgement names the artifact, not its content


# ── 4. cross provider + a seeded fake credential: REFUSED, value never printed ──

def test_cross_provider_resume_with_a_seeded_credential_is_refused(tmp_path):
    root = tmp_path / "projects"
    log_dir = tmp_path / "logs"
    write_session(root, "/srv/lane", "sess-dirty", "deepseek-v4-pro",
                  tail_text=f"leaked value was {FAKE_ANTHROPIC_KEY} oops")
    r = run_check("claude-opus-4-8", root=root, session="sess-dirty", log_dir=log_dir)
    assert r.returncode == 3, f"expected REFUSE, got {r.returncode}: {r.stdout}{r.stderr}"
    assert r.stdout.startswith("REFUSE")
    assert "anthropic-api-key" in r.stdout, r.stdout
    # the guard must never re-leak what it is refusing to replay
    assert FAKE_ANTHROPIC_KEY not in r.stdout + r.stderr
    # and it must leave something the fresh body can actually read
    handoffs = list(log_dir.glob("sess-dirty.md"))
    assert handoffs, "no handoff note written for the operator/lane"
    note = handoffs[0].read_text()
    assert FAKE_ANTHROPIC_KEY not in note, "the handoff note leaked the value"
    assert "started FRESH" in note


def test_refused_decision_is_json_reportable(tmp_path):
    root = tmp_path / "projects"
    write_session(root, "/srv/lane", "sess-j", "deepseek-v4-pro",
                  tail_text=FAKE_ANTHROPIC_KEY)
    r = run_check("claude-opus-4-8", root=root, session="sess-j", json_out=True)
    assert r.returncode == 3
    obj = json.loads(r.stdout)
    assert obj["allow"] is False
    assert obj["session_provider"] == "deepseek" and obj["target_provider"] == "anthropic"
    assert obj["findings"] and obj["findings"][0]["class"] == "anthropic-api-key"
    assert FAKE_ANTHROPIC_KEY not in r.stdout


# ── 5. fail-closed paths ──────────────────────────────────────────────────────

def test_no_transcript_means_nothing_to_replay(tmp_path):
    r = run_check("claude-opus-4-8", root=tmp_path / "projects", session="does-not-exist")
    assert r.returncode == 0
    assert "no transcript" in r.stdout


def test_unknown_session_provider_is_still_scanned(tmp_path):
    root = tmp_path / "projects"
    clean = write_session(root, "/srv/clean", "sess-nomodel", None)
    dirty = write_session(root, "/srv/dirty", "sess-nomodel2", None,
                          tail_text=FAKE_ANTHROPIC_KEY)
    assert run_check("claude-opus-4-8", root=root, transcript=clean).returncode == 0
    assert run_check("claude-opus-4-8", root=root, transcript=dirty).returncode == 3


def test_oversized_transcript_is_refused_not_half_scanned(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    path = write_session(root, "/srv/big", "sess-big", "deepseek-v4-pro")
    monkeypatch.setattr(spg, "MAX_SCAN_BYTES", 16)
    d = spg.decide("claude-opus-4-8", path)
    assert d.allow is False
    assert "verification ceiling" in d.reason


def test_an_unreadable_transcript_fails_closed(tmp_path):
    """A directory where a transcript should be: the scan cannot read it, so the
    resume is refused rather than waved through."""
    d = tmp_path / "not-a-file"
    d.mkdir()
    r = run_check("claude-opus-4-8", root=tmp_path, transcript=d)
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout.startswith("REFUSE")


def test_a_credential_deep_in_a_large_transcript_is_found(tmp_path):
    """A hit on line 50,000 must be found as surely as one on line 2 — the scan is
    line-oriented and must not truncate, and the digest must cover the whole file."""
    path = tmp_path / "big.jsonl"
    filler = json.dumps({"type": "user", "message": {"content": "x" * 200}})
    with path.open("w") as fh:
        for _ in range(50_000):
            fh.write(filler + "\n")
        fh.write(json.dumps({"type": "user", "message": {"content": FAKE_ANTHROPIC_KEY}}) + "\n")
    findings, sha, size = spg.scan_transcript(path)
    assert any(cls == "anthropic-api-key" for cls, _ in findings)
    assert sha and size == path.stat().st_size


PW = "pass" + "word"  # never a literal key=value pair in this file (the sibling
#                        secrets_transcript_guard blocks writing one, correctly)


def test_scan_cost_stays_bounded_on_a_multi_megabyte_transcript(tmp_path):
    """The shared detector's postgres-dsn-kv lookahead is quadratic per line (an
    8,000-line / ~5 MB file where every line carries its anchor did not finish in
    two minutes), which is why this scan is line-oriented AND substitutes a bounded
    equivalent for that one class. Pin that a realistic large transcript scans in
    seconds — a boot-path check cannot hang a lane."""
    path = tmp_path / "wide.jsonl"
    line = json.dumps({"type": "user", "message": {"content": f"{PW}= filler " * 40}})
    with path.open("w") as fh:
        for _ in range(8_000):  # ~5 MB, every line carrying the expensive anchor
            fh.write(line + "\n")
    t0 = time.time()
    findings, _sha, size = spg.scan_transcript(path)
    elapsed = time.time() - t0
    assert size > 4 * 1024 * 1024
    assert elapsed < 60, f"scan took {elapsed:.1f}s on {size/1e6:.1f} MB"
    # No value follows the field, so this is prose, not a conninfo string.
    assert not any(cls == "postgres-dsn-kv" for cls, _ in findings)


def test_bounded_conninfo_check_matches_the_shared_pattern_on_a_corpus():
    """A CONSERVATIVE SUPERSET verified on a corpus — not a proof of equivalence.

    cc-quality 87538 F1 found the one fail-OPEN divergence: three or more bare
    `password=` tokens and no other libpq key score 3 in the shared pattern and
    used to score 1 here. That case is now in this corpus. The remaining known
    divergences are over-reports (fail-closed) and are stated in the function
    docstring rather than silently assumed away by a test name."""
    sys.path.insert(0, str(REPO / "scripts" / "hooks"))
    from secret_shape_patterns import SECRET_VALUE_PATTERNS

    shared = SECRET_VALUE_PATTERNS["postgres-dsn-kv"]
    cases = [
        " ".join(["host=db.example", "port=5432", "user=app", "dbname=prod",
                  f"{PW}=hunter2"]),
        " ".join(["user=app", "host=d", f"{PW}=x", "port=1"]),
        " ".join(["dbname=prod", f"{PW}=abc", "host=a"]),
        " ".join(["host=a", "port=b", "dbname=c"]),          # no password field
        f"{PW}=abc",                                          # one token only
        f"the {PW}= is unset, host=x port=y",                 # no value after =
        # three BARE password tokens, no other libpq key — the case F1 found
        f"{PW}=a {PW}=b {PW}=c",
        # not a password token at all: no word boundary before the keyword
        "mypass" + f"{PW}=abc host=a port=b",
    ]
    for case in cases:
        assert spg._conninfo_hit(case) == bool(shared.search(case)), case


# ── 6. --continue resolves the conversation claude would resume ───────────────

def test_continue_uses_the_newest_transcript_in_that_directory(tmp_path):
    root = tmp_path / "projects"
    old = write_session(root, "/srv/lane", "old", "deepseek-v4-pro", tail_text=FAKE_ANTHROPIC_KEY)
    new = write_session(root, "/srv/lane", "new", "deepseek-v4-pro")
    os.utime(old, (1_000_000, 1_000_000))  # make the ordering unambiguous
    os.utime(new, (2_000_000, 2_000_000))
    r = run_check("claude-opus-4-8", root=root, cont_cwd="/srv/lane")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "scans clean" in r.stdout


def test_continue_in_a_directory_claude_has_never_seen_is_allowed(tmp_path):
    r = run_check("claude-opus-4-8", root=tmp_path / "projects", cont_cwd="/nowhere/at/all")
    assert r.returncode == 0


# ── 7. the launchers actually call it, BEFORE they launch ────────────────────

def test_launcher_gates_the_resume_before_the_claude_launch_line():
    src = (REPO / "scripts" / "launch_dangerous_cc.sh").read_text()
    assert "spg_check" in src
    guard_at = src.index("spg_check")
    launch_at = src.index('"$CLAUDE_BIN" --dangerously-skip-permissions --model "$RESOLVED_MODEL"')
    assert guard_at < launch_at, "the resume guard is wired AFTER the launch line"
    assert "spg_strip_resume_flags CLAUDE_PASSTHROUGH" in src, (
        "a refused resume must boot FRESH, not silently keep the flags")


def test_every_named_boot_path_carries_the_guard():
    """launch_lane_as.sh and switch_lane_token.sh reach claude THROUGH
    launch_dangerous_cc.sh, so the funnel covers them — assert that transit rather
    than duplicating the check in each script (a copy is a divergence waiting)."""
    lane_as = (REPO / "scripts" / "launch_lane_as.sh").read_text()
    assert "launch_dangerous_cc.sh" in lane_as
    switch = (REPO / "scripts" / "switch_lane_token.sh").read_text()
    assert "LAUNCH_AS" in switch and "launch_lane_as.sh" in switch
    for name in ("boot_orch.sh", "boot_mirror.sh", "boot_nazim.sh"):
        body = (REPO / "scripts" / name).read_text()
        assert "session_provider_guard.sh" in body, f"{name} boots a session unguarded"
        assert "spg_check" in body, f"{name} sources the shim but never calls it"


def _guard_block() -> str:
    """The SHIPPED slice of launch_dangerous_cc.sh, executed verbatim below."""
    src = (REPO / "scripts" / "launch_dangerous_cc.sh").read_text()
    start = src.index("# <<< cross-provider-resume-guard")
    end = src.index("# >>> cross-provider-resume-guard") + len("# >>> cross-provider-resume-guard")
    return src[start:end]


def _run_launcher_guard(tmp_path, resume_id, cwd, session_spec):
    """Run the real guard block with the real shim against a temp HOME."""
    import textwrap
    home = tmp_path / "home"
    root = home / ".claude" / "projects"
    (root / spg._project_slug(cwd)).mkdir(parents=True, exist_ok=True)
    (root / spg._project_slug(cwd) / f"{resume_id}.jsonl").write_text(
        "\n".join(json.dumps(x) for x in session_spec) + "\n")
    script = textwrap.dedent("""
        TEAL=""; RED=""; YELLOW=""; BOLD=""; RESET=""
        CLAUDE_PASSTHROUGH=(--model glm-5.3 --resume %s)
        %s
        printf 'RESULT:%%s\\n' "${CLAUDE_PASSTHROUGH[*]}"
    """) % (resume_id, _guard_block())
    log_dir = tmp_path / "handoffs"
    return subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                          env={**os.environ, "HOME": str(home),
                               "ORCH_DIR": str(REPO),
                               "SPG_LOG_DIR": str(log_dir),
                               "RESOLVED_MODEL": "claude-opus-4-8",
                               "CALLER_DIR": str(cwd)})


UNCLEAN = [
    {"type": "user", "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "message": {"role": "assistant", "model": "deepseek-v4-pro"}},
    {"type": "user", "message": {"role": "user", "content": FAKE_ANTHROPIC_KEY}},
]
CLEAN = [
    {"type": "user", "message": {"role": "user", "content": "hello"}},
    {"type": "assistant", "message": {"role": "assistant", "model": "deepseek-v4-pro"}},
]


def test_shipped_launcher_block_boots_fresh_when_the_resume_is_refused(tmp_path):
    r = _run_launcher_guard(tmp_path, "sess-dirty", "/srv/lane", UNCLEAN)
    assert r.returncode == 0, r.stderr
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")][0]
    assert "--resume" not in line, line
    assert "sess-dirty" not in line, line
    assert "REFUSING to resume across a provider switch" in r.stderr
    assert "Booting FRESH" in r.stdout + r.stderr
    assert "HANDOFF" in r.stdout + r.stderr
    assert list((tmp_path / "handoffs").glob("sess-dirty.md")), "no handoff note"
    assert FAKE_ANTHROPIC_KEY not in r.stdout + r.stderr


def test_shipped_launcher_block_keeps_the_resume_when_the_transcript_is_clean(tmp_path):
    r = _run_launcher_guard(tmp_path, "sess-clean", "/srv/lane", CLEAN)
    assert r.returncode == 0, r.stderr
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")][0]
    assert "--resume sess-clean" in line, line
    assert "Resume guard" in r.stdout


def test_strip_helper_drops_the_flag_and_its_session_id(tmp_path):
    script = (
        'source "$1" || exit 9; '
        'CLAUDE_PASSTHROUGH=(--a --resume abc123 --b --continue --model glm-5.3); '
        'spg_strip_resume_flags CLAUDE_PASSTHROUGH; '
        'printf "%s " "${CLAUDE_PASSTHROUGH[@]}"'
    )
    r = subprocess.run(["bash", "-c", script, "_", str(SHIM)], text=True,
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "--a --b --model glm-5.3"


def test_strip_helper_does_not_eat_the_next_flag_of_a_bare_resume():
    """`--resume --model x` (picker mode) must lose only --resume."""
    script = (
        'source "$1" || exit 9; '
        'CLAUDE_PASSTHROUGH=(--resume --model glm-5.3); '
        'spg_strip_resume_flags CLAUDE_PASSTHROUGH; '
        'printf "%s " "${CLAUDE_PASSTHROUGH[@]}"'
    )
    r = subprocess.run(["bash", "-c", script, "_", str(SHIM)], text=True,
                       capture_output=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "--model glm-5.3"


def test_a_bare_resume_is_judged_like_continue(tmp_path):
    """A bare --resume opens the picker on the newest conversation in the dir —
    the same transcript --continue would take — so it must not skip the guard."""
    block = _guard_block()
    script = ("TEAL=\"\"; RED=\"\"; YELLOW=\"\"; BOLD=\"\"; RESET=\"\"\n"
              "CLAUDE_PASSTHROUGH=(--resume --model glm-5.3)\n" + block +
              "\nprintf 'RESULT:%s\\n' \"${CLAUDE_PASSTHROUGH[*]}\"\n")
    home = tmp_path / "home"
    root = home / ".claude" / "projects"
    cwd = "/srv/lane"
    (root / spg._project_slug(cwd)).mkdir(parents=True, exist_ok=True)
    (root / spg._project_slug(cwd) / "newest.jsonl").write_text(
        "\n".join(json.dumps(x) for x in UNCLEAN) + "\n")
    r = subprocess.run(["bash", "-c", script], text=True, capture_output=True,
                       env={**os.environ, "HOME": str(home), "ORCH_DIR": str(REPO),
                            "SPG_LOG_DIR": str(tmp_path / "h"),
                            "RESOLVED_MODEL": "claude-opus-4-8",
                            "CALLER_DIR": cwd})
    assert r.returncode == 0, r.stderr
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT:")][0]
    assert line.strip() == "RESULT:--model glm-5.3", line


def test_shim_end_to_end(tmp_path):
    root = tmp_path / "projects"
    write_session(root, "/srv/lane", "sess-shim", "deepseek-v4-pro",
                  tail_text=FAKE_ANTHROPIC_KEY)
    script = f'source "$1"; spg_check claude-opus-4-8 --session sess-shim --transcript-root "$2"'
    r = subprocess.run(["bash", "-c", script, "_", str(SHIM), str(root)],
                       text=True, capture_output=True)
    assert r.returncode == 3, r.stdout + r.stderr
    assert r.stdout.startswith("REFUSE")
    assert FAKE_ANTHROPIC_KEY not in r.stdout + r.stderr
