"""Lane exemption from console reconstitution (bus #63378, op#29128 mixup):
CC_BASE_AGENT_ID present => this session is a lane, never the console, and must
get a short identity notice instead of the "You are Nazim / console body"
handoff — even when ORCH_BODY_ROLE resolves to "console" via the .env fallback
(the live checkout's own default). Root cause: a lane in an orchestrator
worktree inherits the same SessionStart hook as the console's own boot, and
previously nothing there checked CC_BASE_AGENT_ID before injecting the console
handoff.
"""
import importlib
import io
import json

import pytest

ssr = importlib.import_module("scripts.session_start_reconstitute")


# ── _cc_base_agent_id ──────────────────────────────────────────────────────

def test_cc_base_agent_id_reads_env(monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    assert ssr._cc_base_agent_id() == "cc-substrate"


def test_cc_base_agent_id_empty_when_unset(monkeypatch):
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    assert ssr._cc_base_agent_id() == ""


def test_cc_base_agent_id_strips_whitespace(monkeypatch):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "  cc-substrate  ")
    assert ssr._cc_base_agent_id() == "cc-substrate"


# ── _lane_identity_notice (pure) ─────────────────────────────────────────────

def test_lane_notice_names_the_base_id():
    notice = ssr._lane_identity_notice("cc-substrate", "")
    assert "cc-substrate" in notice


def test_lane_notice_says_not_console():
    notice = ssr._lane_identity_notice("cc-substrate", "")
    assert "NOT the console" in notice


def test_lane_notice_points_at_my_inbox_script():
    notice = ssr._lane_identity_notice("cc-substrate", "")
    assert "scripts/lib/my_inbox.py" in notice


def test_lane_notice_warns_off_nazim_send():
    notice = ssr._lane_identity_notice("cc-substrate", "")
    assert "nazim_send.sh" in notice


def test_lane_notice_warns_off_orch_console_inbox_query():
    notice = ssr._lane_identity_notice("cc-substrate", "")
    assert "to_agent='orch-console'" in notice


def test_lane_notice_singleton_omits_instance_mention(monkeypatch):
    # No distinct instance id (base-only solo lane) — no "(instance ...)" clause.
    notice = ssr._lane_identity_notice("cc-irsyad-coord", "")
    assert "instance `" not in notice
    assert "'cc-irsyad-coord'" in notice
    assert " or " not in notice.split("to_agent =")[1].split("—")[0]


def test_lane_notice_instance_equal_to_base_treated_as_singleton():
    notice = ssr._lane_identity_notice("cc-substrate", "cc-substrate")
    assert "instance `" not in notice


def test_lane_notice_distinct_instance_mentions_both_ids():
    notice = ssr._lane_identity_notice("cc-substrate", "cc-substrate-1")
    assert "instance `cc-substrate-1`" in notice
    assert "'cc-substrate' or 'cc-substrate-1'" in notice


# ── main() end-to-end: lane path takes priority over the console fallback ──

def _run_main(monkeypatch, capsys, source="startup"):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"source": source})))
    ssr.main()
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_main_emits_lane_notice_when_cc_base_agent_id_set_even_if_env_says_console(
    monkeypatch, capsys
):
    # This is the exact bug shape: ORCH_BODY_ROLE resolves to "console" (as it
    # would via the .env fallback on the live checkout), but CC_BASE_AGENT_ID is
    # also set because this is actually a lane process.
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    monkeypatch.setenv("CC_AGENT_ID", "cc-substrate-1")
    payload = _run_main(monkeypatch, capsys)
    assert payload is not None
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "cc-substrate" in ctx
    assert "Nazim / console body" not in ctx
    assert "newest handoff" not in ctx


def test_main_still_emits_console_handoff_when_no_cc_base_agent_id(monkeypatch, capsys, tmp_path):
    # Unchanged behavior for the real console boot: no CC_BASE_AGENT_ID at all.
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    monkeypatch.setenv("ORCH_BODY_ROLE", "console")
    monkeypatch.setattr(ssr, "_newest_handoff", lambda: None)
    monkeypatch.setattr(ssr, "_open_asks_block", lambda: "")
    payload = _run_main(monkeypatch, capsys)
    assert payload is not None
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "Nazim / console body" in ctx


def test_main_lane_with_no_orch_body_role_set_still_gets_lane_notice(monkeypatch, capsys):
    # A lane that never touched ORCH_BODY_ROLE at all (the common case) must not
    # fall through to the console path via the .env ORCH_BODY_ROLE=console default.
    monkeypatch.delenv("ORCH_BODY_ROLE", raising=False)
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-fleet-health")
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    payload = _run_main(monkeypatch, capsys)
    assert payload is not None
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert "cc-fleet-health" in ctx
    # The notice names Nazim only to disclaim it ("NOT the console/Nazim body") —
    # it must never carry the actual console handoff content.
    assert "newest handoff" not in ctx


def test_main_lane_path_skips_resume_source(monkeypatch, capsys):
    monkeypatch.setenv("CC_BASE_AGENT_ID", "cc-substrate")
    payload = _run_main(monkeypatch, capsys, source="resume")
    assert payload is None


def test_main_non_console_non_lane_stays_silent(monkeypatch, capsys):
    monkeypatch.delenv("CC_BASE_AGENT_ID", raising=False)
    monkeypatch.delenv("CC_AGENT_ID", raising=False)
    monkeypatch.setenv("ORCH_BODY_ROLE", "hub")
    payload = _run_main(monkeypatch, capsys)
    assert payload is None
