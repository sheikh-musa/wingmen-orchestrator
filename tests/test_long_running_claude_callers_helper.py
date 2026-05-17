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
