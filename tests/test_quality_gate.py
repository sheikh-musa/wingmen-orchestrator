"""Tests for the shadow-mode quality gate evaluator (Head of Quality, Phase 2).

Phase 2 ships the gate SHADOW-first: it resolves a change class, evaluates the
deterministic floor from caller-supplied evidence, and produces a SHA-scoped
verdict {would_block, failures, unproven, advisories} — but it NEVER blocks
until armed (QUALITY_GATE_MODE=block). Judgment taste stays advisory (267-H1);
only deterministic checks and a class's mandatory reviewer arms are blocking.
Dead-man default: an unevaluated floor item is fail-strict (counts toward
would_block) — absence of proof is never a pass.
"""

import pytest

from nervous_system import ihsan_gate, quality_gate


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _all_pass_evidence(change_class, sha="deadbeef"):
    """Build an evidence dict marking every required deterministic check 'pass'
    and every mandatory reviewer arm present — the fully-green case."""
    checks = {}
    for item in ihsan_gate.gate_items_for(change_class):
        for c in item.get("checks", []):
            checks[c] = "pass"
    reviews = {iid: True for iid in quality_gate.mandatory_reviews_for(change_class)}
    return {"sha": sha, "checks": checks, "reviews": reviews}


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #

def test_all_green_docs_copy_does_not_block():
    verdict = quality_gate.evaluate("docs-copy", _all_pass_evidence("docs-copy"))
    assert verdict.would_block is False
    assert verdict.failures == []
    assert verdict.unproven == []


def test_verdict_carries_sha_and_resolved_class():
    verdict = quality_gate.evaluate("docs", _all_pass_evidence("docs", sha="abc123"))
    assert verdict.sha == "abc123"
    # 'docs' is an alias for docs-copy
    assert verdict.change_class == "docs-copy"


# --------------------------------------------------------------------------- #
# Deterministic floor blocks
# --------------------------------------------------------------------------- #

def test_failed_deterministic_check_would_block():
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"          # G1 is deterministic
    verdict = quality_gate.evaluate("docs-copy", ev)
    assert verdict.would_block is True
    assert "G1" in [f.id for f in verdict.failures]


def test_missing_deterministic_evidence_is_unproven_not_pass():
    ev = _all_pass_evidence("docs-copy")
    del ev["checks"]["deploy-sha-equals-main"]  # G7 check now absent
    verdict = quality_gate.evaluate("docs-copy", ev)
    assert verdict.would_block is True
    # It is UNPROVEN (absence of evidence), distinct from an actual FAIL.
    assert "G7" in [u.id for u in verdict.unproven]
    assert "G7" not in [f.id for f in verdict.failures]


# --------------------------------------------------------------------------- #
# Judgment items: taste advisory, mandatory reviews block
# --------------------------------------------------------------------------- #

def test_judgment_taste_failure_is_advisory_not_blocking():
    # G9 (ihsan polish) is judgment + never in mandatory_reviews -> advisory only.
    ev = _all_pass_evidence("ui-frontend")
    ev["checks"]["on-brand"] = "fail"           # a G9 taste check
    verdict = quality_gate.evaluate("ui-frontend", ev)
    assert "G9" not in [f.id for f in verdict.failures]
    assert "G9" in [a.id for a in verdict.advisories]


def test_missing_mandatory_review_would_block():
    # money-payment requires the G5 reviewer arm (mandatory_reviews: [G5]).
    ev = _all_pass_evidence("money-payment")
    ev["reviews"]["G5"] = False                 # security review absent
    verdict = quality_gate.evaluate("money-payment", ev)
    assert verdict.would_block is True
    assert "G5" in [f.id for f in verdict.failures]


def test_present_mandatory_review_does_not_block_on_that_item():
    verdict = quality_gate.evaluate("money-payment", _all_pass_evidence("money-payment"))
    assert "G5" not in [f.id for f in verdict.failures]


# --------------------------------------------------------------------------- #
# Mode: shadow never enforces; block enforces
# --------------------------------------------------------------------------- #

def test_shadow_mode_never_enforces_even_when_would_block():
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    verdict = quality_gate.evaluate("docs-copy", ev, mode="shadow")
    assert verdict.would_block is True
    assert verdict.enforced is False
    assert quality_gate.should_block(verdict) is False


def test_block_mode_enforces_when_would_block():
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    verdict = quality_gate.evaluate("docs-copy", ev, mode="block")
    assert verdict.would_block is True
    assert verdict.enforced is True
    assert quality_gate.should_block(verdict) is True


def test_block_mode_does_not_enforce_when_green():
    verdict = quality_gate.evaluate("docs-copy", _all_pass_evidence("docs-copy"), mode="block")
    assert verdict.enforced is False
    assert quality_gate.should_block(verdict) is False


def test_mode_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("QUALITY_GATE_MODE", raising=False)
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    verdict = quality_gate.evaluate("docs-copy", ev)
    assert verdict.mode == "shadow"
    assert verdict.enforced is False


def test_mode_read_from_env(monkeypatch):
    monkeypatch.setenv("QUALITY_GATE_MODE", "block")
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    verdict = quality_gate.evaluate("docs-copy", ev)
    assert verdict.mode == "block"
    assert verdict.enforced is True


# --------------------------------------------------------------------------- #
# Unknown class -> strictest default (never under-gate)
# --------------------------------------------------------------------------- #

def test_unknown_class_resolves_to_strictest_default():
    default = ihsan_gate.load_manifest()["default_class"]
    verdict = quality_gate.evaluate("some-nonsense-class", {"sha": "x", "checks": {}})
    assert verdict.change_class == default
    # empty evidence against the strictest class -> lots unproven -> would_block
    assert verdict.would_block is True


# --------------------------------------------------------------------------- #
# Serialization + purity
# --------------------------------------------------------------------------- #

def test_verdict_to_dict_has_audit_fields():
    d = quality_gate.evaluate("docs-copy", _all_pass_evidence("docs-copy")).to_dict()
    for key in ("sha", "change_class", "mode", "would_block", "enforced",
                "failures", "unproven", "advisories", "items"):
        assert key in d, f"missing audit field {key}"


def test_evaluate_is_pure_no_mutation_of_evidence():
    ev = _all_pass_evidence("docs-copy")
    import copy
    snapshot = copy.deepcopy(ev)
    quality_gate.evaluate("docs-copy", ev)
    assert ev == snapshot, "evaluate must not mutate the caller's evidence"


# --------------------------------------------------------------------------- #
# CLI seam (what a merge/deploy wrapper invokes)
# --------------------------------------------------------------------------- #

import io
import json


def _run_cli(change_class, evidence, extra_argv=()):
    """Invoke main() in-process with evidence piped on stdin. Returns (rc, stdout)."""
    argv = ["--class", change_class, *extra_argv]
    stdin = io.StringIO(json.dumps(evidence))
    stdout = io.StringIO()
    rc = quality_gate.main(argv, stdin=stdin, stdout=stdout)
    return rc, stdout.getvalue()


def test_cli_prints_verdict_json():
    rc, out = _run_cli("docs-copy", _all_pass_evidence("docs-copy"))
    payload = json.loads(out)
    assert payload["change_class"] == "docs-copy"
    assert payload["would_block"] is False


def test_cli_shadow_returns_zero_even_when_would_block():
    # Shadow must NEVER break a ship: a would-block verdict still exits 0.
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    rc, out = _run_cli("docs-copy", ev, extra_argv=["--mode", "shadow"])
    assert json.loads(out)["would_block"] is True
    assert rc == 0


def test_cli_block_mode_returns_nonzero_when_would_block():
    ev = _all_pass_evidence("docs-copy")
    ev["checks"]["typecheck"] = "fail"
    rc, out = _run_cli("docs-copy", ev, extra_argv=["--mode", "block"])
    assert json.loads(out)["enforced"] is True
    assert rc != 0


def test_cli_block_mode_returns_zero_when_green():
    rc, out = _run_cli("docs-copy", _all_pass_evidence("docs-copy"), extra_argv=["--mode", "block"])
    assert rc == 0
