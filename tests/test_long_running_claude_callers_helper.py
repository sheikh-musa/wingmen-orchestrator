"""Pure-unit tests for nervous_system/long_running_claude_callers helper.

Manifest parsing, auto_kill_policy default derivation, and pure logic.
DB round-trip lives in test_long_running_claude_callers.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from nervous_system.long_running_claude_callers import (
    derive_auto_kill_policy,
    parse_manifest,
    Manifest,
)


class TestDeriveAutoKillPolicy:
    """Per CAI-RESP-161 Q6 defaults derived from registered_by_identity."""

    def test_operator_authored_defaults_soft_alert(self):
        assert derive_auto_kill_policy("operator") == "soft_alert"

    def test_cc_family_defaults_soft_alert(self):
        assert derive_auto_kill_policy("cc_family") == "soft_alert"

    def test_substrate_defaults_no_kill(self):
        assert derive_auto_kill_policy("substrate") == "no_kill"

    def test_unknown_identity_raises(self):
        with pytest.raises(ValueError, match="invalid"):
            derive_auto_kill_policy("alien")


class TestParseManifest:
    """Operator-authored YAML manifests for long-running callers."""

    def test_valid_yaml_manifest(self, tmp_path):
        f = tmp_path / "probe.yaml"
        f.write_text("""
caller_name: cc-probe-max-throttle
cmd: python3 scripts/probe_max_throttle.py run
expected_cadence_seconds: 300
expected_tokens_per_day: 14000000
max_tokens_per_day: 20000000
ratified_by_decision_ref: CC-PROBE-MAX-THROTTLE-001
registered_by_identity: operator
purpose: Max-plan throttle probe; logs to scripts/.probe_log.jsonl
""")
        m = parse_manifest(f)
        assert m.caller_name == "cc-probe-max-throttle"
        assert m.expected_cadence_seconds == 300
        assert m.expected_tokens_per_day == 14000000
        assert m.max_tokens_per_day == 20000000
        assert m.ratified_by_decision_ref == "CC-PROBE-MAX-THROTTLE-001"
        assert m.registered_by_identity == "operator"
        assert "throttle probe" in m.purpose

    def test_missing_required_field_raises(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("""
caller_name: incomplete
cmd: foo
""")
        with pytest.raises((KeyError, ValueError)):
            parse_manifest(f)

    def test_invalid_identity_raises(self, tmp_path):
        f = tmp_path / "bad_identity.yaml"
        f.write_text("""
caller_name: foo
cmd: bar
expected_cadence_seconds: 60
expected_tokens_per_day: 1000
ratified_by_decision_ref: FOO-001
registered_by_identity: rogue
purpose: testing
""")
        with pytest.raises(ValueError):
            parse_manifest(f)


class TestParseManifestJSON:
    """parse_manifest also handles JSON files (suffix-based dispatch)."""

    def test_valid_json_manifest(self, tmp_path):
        import json as _json
        f = tmp_path / "caller.json"
        f.write_text(_json.dumps({
            "caller_name": "test-json-caller",
            "cmd": "python3 fake.py",
            "expected_cadence_seconds": 600,
            "expected_tokens_per_day": 500000,
            "ratified_by_decision_ref": "TEST-JSON-001",
            "registered_by_identity": "cc_family",
            "purpose": "json manifest parse coverage",
        }))
        m = parse_manifest(f)
        assert m.caller_name == "test-json-caller"
        assert m.expected_cadence_seconds == 600
        assert m.registered_by_identity == "cc_family"
        assert m.max_tokens_per_day is None
        assert m.auto_kill_policy is None  # left to identity-derived default at register-time

    def test_invalid_auto_kill_policy_in_manifest_raises(self, tmp_path):
        """Explicit auto_kill_policy must be in the valid enum if present."""
        f = tmp_path / "bad_policy.yaml"
        f.write_text("""
caller_name: foo
cmd: bar
expected_cadence_seconds: 60
expected_tokens_per_day: 1000
ratified_by_decision_ref: FOO-001
registered_by_identity: cc_family
purpose: testing
auto_kill_policy: nuke_from_orbit
""")
        with pytest.raises(ValueError, match="auto_kill_policy"):
            parse_manifest(f)


class TestSweepManifests:
    """sweep_manifests scans a directory for *.yaml/*.json files."""

    def test_sweeps_yaml_and_json_skips_dot_and_other(self, tmp_path):
        from nervous_system.long_running_claude_callers import sweep_manifests

        # Valid yaml
        (tmp_path / "a.yaml").write_text("""
caller_name: caller-a
cmd: bin/a
expected_cadence_seconds: 60
expected_tokens_per_day: 100
ratified_by_decision_ref: A-001
registered_by_identity: operator
purpose: a
""")
        # Valid json
        import json as _json
        (tmp_path / "b.json").write_text(_json.dumps({
            "caller_name": "caller-b",
            "cmd": "bin/b",
            "expected_cadence_seconds": 120,
            "expected_tokens_per_day": 200,
            "ratified_by_decision_ref": "B-001",
            "registered_by_identity": "operator",
            "purpose": "b",
        }))
        # Should be skipped: dotfile + non-yaml/json
        (tmp_path / ".hidden.yaml").write_text("caller_name: hidden")
        (tmp_path / "README.md").write_text("# docs")

        results = sweep_manifests(tmp_path)
        names = sorted(m.caller_name for m in results)
        assert names == ["caller-a", "caller-b"]

    def test_missing_directory_returns_empty(self, tmp_path):
        from nervous_system.long_running_claude_callers import sweep_manifests
        nonexistent = tmp_path / "nope"
        assert sweep_manifests(nonexistent) == []

    def test_malformed_manifest_logged_not_raised(self, tmp_path, caplog):
        """A malformed manifest must not break the sweep — log warning, skip, continue."""
        import logging
        from nervous_system.long_running_claude_callers import sweep_manifests

        # Valid one
        (tmp_path / "valid.yaml").write_text("""
caller_name: valid-one
cmd: bin/valid
expected_cadence_seconds: 60
expected_tokens_per_day: 100
ratified_by_decision_ref: VALID-001
registered_by_identity: operator
purpose: v
""")
        # Malformed (missing required fields)
        (tmp_path / "bad.yaml").write_text("caller_name: only_name")

        with caplog.at_level(logging.WARNING, logger="wingmen.long_running_claude_callers"):
            results = sweep_manifests(tmp_path)

        names = [m.caller_name for m in results]
        assert names == ["valid-one"]  # sweep continued despite bad.yaml
        assert any("bad.yaml" in r.message for r in caplog.records), \
            "expected a warning log mentioning the malformed file"
