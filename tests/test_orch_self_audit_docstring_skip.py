"""Tests for orch_self_audit triple-quoted-string skip — eliminates the
false-positive class observed 2026-06-01 (70 Telegram alerts in 36h flagging
a comment in a docstring as a direct API call).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.orch_self_audit import _scan_call_sites, _classify_finding


def test_no_violation_findings_in_repo():
    """After the 2026-06-02 fixes, every Anthropic-SDK callsite in the repo
    must classify as ok_haiku / ok_exempt. Zero violations expected.
    """
    findings = _scan_call_sites()
    violations = [f for f in findings if _classify_finding(f).startswith("violation")]
    assert violations == [], (
        f"Unexpected violations: {violations}. "
        f"Each direct SDK callsite must either use Haiku (ok_haiku) or carry "
        f"a # llm_route_exempt: <ratified_reason> annotation with the reason "
        f"registered in _LLM_ROUTE_EXEMPT_REASONS."
    )


def test_docstring_mentioning_trigger_does_not_false_positive(tmp_path, monkeypatch):
    """Synthetic: a python file whose docstring includes the literal
    `anthropic.Anthropic(` pattern must NOT show up as a callsite."""
    fake_repo = tmp_path
    target = fake_repo / "mod_with_docstring.py"
    target.write_text(
        '"""Migration note.\n'
        '\n'
        'Previously: anthropic.Anthropic() ran here. Migrated to call_ai.\n'
        '"""\n'
        'x = 1\n'
    )

    import nervous_system.orch_self_audit as audit
    monkeypatch.setattr(audit, "_REPO_ROOT", fake_repo)
    monkeypatch.setattr(audit, "_AUDIT_PATHS", (fake_repo,))

    findings = audit._scan_call_sites()
    matching = [f for f in findings if f["file"].endswith("mod_with_docstring.py")]
    assert matching == [], (
        f"Docstring mentioning trigger string should NOT produce findings; "
        f"got {matching}"
    )


def test_actual_call_in_code_still_detected(tmp_path, monkeypatch):
    """Negative coverage — ensure the docstring skip doesn't hide real calls."""
    fake_repo = tmp_path
    target = fake_repo / "mod_with_real_call.py"
    target.write_text(
        '"""Docstring not mentioning the trigger."""\n'
        'import anthropic\n'
        'client = anthropic.Anthropic(api_key="sk-x")\n'
    )

    import nervous_system.orch_self_audit as audit
    monkeypatch.setattr(audit, "_REPO_ROOT", fake_repo)
    monkeypatch.setattr(audit, "_AUDIT_PATHS", (fake_repo,))

    findings = audit._scan_call_sites()
    matching = [f for f in findings if f["file"].endswith("mod_with_real_call.py")]
    assert len(matching) == 1
    assert matching[0]["line"] == 3


def test_shadow_ab_exempt_reason_recognised():
    """The newly-registered shadow_ab_primary_pending_resume_gate reason must
    classify a finding as ok_exempt."""
    finding = {
        "file": "ralph_runner.py",
        "line": 224,
        "model": "claude",
        "exempt_reason": "shadow_ab_primary_pending_resume_gate",
    }
    assert _classify_finding(finding) == "ok_exempt"
