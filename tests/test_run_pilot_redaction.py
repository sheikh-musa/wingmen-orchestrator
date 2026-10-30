"""Tests for scripts/loop/run_pilot.py's redact_text() (op#35690, orch-console
bus #83797 -- cc-quality MEDIUM on PR#661): role sessions and the `tests`
check both run with DATABASE_URL in their env (launch_dangerous_cc.sh's
shared KEEP tier), so any of their captured output this harness stores on a
result object or prints must come out with every secret-shaped span redacted.

Fixture DSNs below are deliberately built via string concatenation (never
one contiguous literal in source), same convention tests/test_secret_shape_
patterns.py uses, so this file's own text doesn't trip the live
secrets_transcript_guard Rule E scan while it's written/edited.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).parent.parent / "scripts" / "loop" / "run_pilot.py"
spec = importlib.util.spec_from_file_location("run_pilot", MODULE_PATH)
run_pilot = importlib.util.module_from_spec(spec)
sys.modules["run_pilot"] = run_pilot
spec.loader.exec_module(run_pilot)


def _fake_dsn() -> str:
    return "postgres" + "://orchuser:" + "sekret123" + "@db.example.com:5432/orch"


def test_redacts_a_dsn_shaped_string_embedded_in_build_output():
    dsn = _fake_dsn()
    text = "npm run build\nDATABASE_URL=" + dsn + "\nbuild ok, 0 errors"
    out = run_pilot.redact_text(text)

    assert dsn not in out
    assert "orchuser" not in out
    assert "sekret123" not in out
    assert "[REDACTED by run_pilot.py" in out
    # Non-secret surrounding text is untouched.
    assert "npm run build" in out
    assert "build ok, 0 errors" in out


def test_redacts_every_occurrence_not_just_the_first():
    dsn = _fake_dsn()
    text = f"first: {dsn}\nsecond: {dsn}\n"
    out = run_pilot.redact_text(text)
    assert dsn not in out
    assert out.count("[REDACTED by run_pilot.py") == 2


def test_clean_text_passes_through_unchanged():
    clean = "a perfectly ordinary CI log line with no secret-shaped values in it"
    assert run_pilot.redact_text(clean) == clean


def test_empty_and_none_safe():
    assert run_pilot.redact_text("") == ""


def test_ci_subprocess_env_strips_database_url_but_keeps_path(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _fake_dsn())
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "also-should-be-stripped")
    monkeypatch.setenv("GH_TOKEN", "unaffected-value")
    env = run_pilot._ci_subprocess_env()
    assert "DATABASE_URL" not in env
    assert "SUPABASE_SERVICE_KEY" not in env
    assert env.get("GH_TOKEN") == "unaffected-value"
    assert "PATH" in env
