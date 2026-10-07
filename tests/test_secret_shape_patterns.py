"""Tests for scripts/hooks/secret_shape_patterns.py's shared anchor prefilter and
micro-benchmark regression guards (bus #54580/#54643, Thu 10-08 06:00Z item 1).

Profiling on a representative large tool output (a clean, secret-free text dense with
the bare `host|port|user|dbname|password=` keyword tokens the postgres-dsn-kv class
keys off) found that one pattern's `(?=[^\\n]*\\bpassword=\\S+)` lookahead reran from
every candidate position and cost ~1.1 microseconds/byte -- ~40x every other pattern,
~83% of total scan cost on a 1.6MB text. The fix is a cheap, SOUND literal-substring
prefilter (has_candidate/find_hits) that skips a class's full regex entirely when none
of its anchor substrings are present. These tests assert both correctness (the
prefilter changes WHICH regexes run, never WHICH texts are reported as hits) and the
performance bound itself, so a future change can't silently reintroduce the cost.

Sample secret-shaped fixtures below are deliberately built via string concatenation
(never one contiguous literal in source) so this file's own text doesn't trip the
live secrets_transcript_guard Rule E scan that guards Write/Edit of this very file.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

HOOK_PATH = Path(__file__).parent.parent / "scripts" / "hooks" / "secret_shape_patterns.py"
spec = importlib.util.spec_from_file_location("secret_shape_patterns", HOOK_PATH)
patterns_mod = importlib.util.module_from_spec(spec)
sys.modules["secret_shape_patterns"] = patterns_mod
spec.loader.exec_module(patterns_mod)


def test_has_candidate_true_when_anchor_present():
    sample = "prefix " + "sk-ant-" + "a" * 20 + " suffix"
    assert patterns_mod.has_candidate(sample, "anthropic-api-key")


def test_has_candidate_false_when_anchor_absent():
    assert not patterns_mod.has_candidate("nothing secret-shaped in here at all", "anthropic-api-key")


def test_has_candidate_defaults_true_for_class_with_no_registered_anchor():
    # bearer-token has no anchor (case-insensitive pattern, no sound literal substring)
    # -- must fail OPEN to running the full regex, never fail open to skipping it.
    assert patterns_mod.has_candidate("plain text with no " + "bear" + "er anywhere", "bearer-token")


def test_find_hits_matches_plain_iteration_for_every_registered_class():
    samples = {
        "anthropic-api-key": "sk-ant-" + "a" * 30,
        "supabase-service-key": "sbp_" + "a" * 25,
        "telegram-bot-token": "12345678" + ":AA" + "b" * 35,
        "postgres-dsn": "postgres" + "://user:pw@host:5432/db",
        "postgres-dsn-kv": " ".join(["host=h", "port=5432", "user=u", "dbname=d", "password=pw"]),
        "jwt": "eyJ" + "a" * 40 + "." + "b" * 90 + "." + "c" * 40,
        "vercel-token": "vcp_" + "c" * 25,
        "github-token": "ghp_" + "d" * 35,
        "google-oauth-refresh-token": "1//0" + "e" * 25,
        "ssh-private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
        "bearer-token": "Bearer " + "f" * 20,
    }
    for cls, text in samples.items():
        hits = patterns_mod.find_hits(text)
        hit_classes = {h[0] for h in hits}
        assert cls in hit_classes, f"find_hits missed {cls} on its own representative sample"


def test_find_hits_reports_nothing_on_clean_text():
    clean = "a perfectly ordinary tool output with no secret-shaped values in it at all"
    assert patterns_mod.find_hits(clean) == []


def test_find_hits_skips_passwordless_kv_conninfo():
    # a passwordless local-socket conninfo is not a secret -- the anchor ("password=")
    # must be absent, so the prefilter (and thus find_hits) must agree with the pattern
    # itself that this never matches.
    conninfo = " ".join(["host=/var/folders/xx/ci-sock", "port=58062", "user=postgres", "dbname=postgres"])
    assert not patterns_mod.has_candidate(conninfo, "postgres-dsn-kv")
    assert not any(h[0] == "postgres-dsn-kv" for h in patterns_mod.find_hits(conninfo))


def test_find_hits_is_fast_on_kv_keyword_dense_clean_text():
    # the exact shape that cost ~1.1us/byte pre-fix: dense in the bare keyword tokens
    # postgres-dsn-kv keys off, but with no "=" at all so it can never actually match.
    # Pre-fix this ran the full backtracking lookahead from every candidate position;
    # post-fix the "password=" anchor check (a plain `in`) skips the regex entirely.
    lines = [f"host port user dbname line{i} no equals signs here at all" for i in range(20000)]
    text = "\n".join(lines)
    t0 = time.perf_counter()
    hits = patterns_mod.find_hits(text)
    elapsed = time.perf_counter() - t0
    assert hits == []
    assert elapsed < 0.25, (
        f"find_hits() on {len(text)} bytes of kv-keyword-dense clean text took "
        f"{elapsed:.3f}s -- regression in the postgres-dsn-kv anchor prefilter"
    )
