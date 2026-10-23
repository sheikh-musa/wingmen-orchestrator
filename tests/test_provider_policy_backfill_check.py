"""Tests for provider_policy_backfill_check's pure classification core (orch-console
guardrail, bus #74588: "re-run the mapping against EVERY lane... before flipping
warn-only to hard-refuse"). DB/filesystem reads (fetch_rows, glm_pointer_lanes) are
factored out of classify_lane/run so this never touches a live DB or disk.
"""
from __future__ import annotations

from scripts import provider_policy_backfill_check as bc

_POLICY = [
    {"client_key": "cosem", "allowed_providers": ["anthropic", "zai"], "text_only": True},
    {"client_key": "cosem-adcda", "allowed_providers": ["anthropic", "zai"], "text_only": True},
    {"client_key": "mamadah", "allowed_providers": ["anthropic"], "text_only": False},
    {"client_key": "irsyad", "allowed_providers": ["anthropic"], "text_only": False},
]
_OVERRIDES = [
    {"lane": "cosem-adcda-photos", "allowed_providers": ["anthropic"], "text_only": False},
]


def test_mapped_lane_not_on_glm_is_clean():
    r = bc.classify_lane("cosem-platform-calendar", _POLICY, _OVERRIDES, currently_glm=False)
    assert r["resolved_client_key"] == "cosem"
    assert r["would_refuse_if_glm"] is False


def test_mamadah_on_glm_would_be_refused():
    # the exact incident this whole build exists to catch, as a backfill case.
    r = bc.classify_lane("cc-mamadah-1", _POLICY, _OVERRIDES, currently_glm=True)
    assert r["resolved_client_key"] == "mamadah"
    assert r["would_refuse_if_glm"] is True


def test_unmapped_lane_not_on_glm_is_reported_but_not_a_problem():
    r = bc.classify_lane("brand-new-unregistered-lane", _POLICY, _OVERRIDES, currently_glm=False)
    assert r["resolved_client_key"] is None
    assert r["would_refuse_if_glm"] is False


def test_unmapped_lane_on_glm_would_be_refused():
    r = bc.classify_lane("brand-new-unregistered-lane", _POLICY, _OVERRIDES, currently_glm=True)
    assert r["resolved_client_key"] is None
    assert r["would_refuse_if_glm"] is True


def test_override_lane_on_glm_would_be_refused_even_though_client_allows_it():
    r = bc.classify_lane("cosem-adcda-photos", _POLICY, _OVERRIDES, currently_glm=True)
    assert r["override_applied"] is True
    assert r["would_refuse_if_glm"] is True


def test_sibling_of_overridden_lane_unaffected():
    r = bc.classify_lane("cc-cosem-adcda-1", _POLICY, _OVERRIDES, currently_glm=True)
    assert r["override_applied"] is False
    assert r["would_refuse_if_glm"] is False


def test_misconfigured_widening_override_is_flagged():
    bad_overrides = [{"lane": "irsyad-worker-1", "allowed_providers": ["anthropic", "zai"], "text_only": True}]
    r = bc.classify_lane("irsyad-worker-1", _POLICY, bad_overrides, currently_glm=False)
    assert r["override_misconfigured"] is True


def test_run_over_multiple_lanes():
    lanes = ["cc-mamadah-1", "cosem-platform-calendar", "cosem-adcda-photos"]
    glm = {"cc-mamadah-1", "cosem-adcda-photos"}
    results = bc.run(_POLICY, _OVERRIDES, lanes, glm)
    assert len(results) == 3
    refused = [r["lane"] for r in results if r["would_refuse_if_glm"]]
    assert refused == ["cc-mamadah-1", "cosem-adcda-photos"]


def test_main_exits_nonzero_when_problems_found(monkeypatch, capsys):
    monkeypatch.setattr(bc, "fetch_rows", lambda: (_POLICY, _OVERRIDES, ["cc-mamadah-1"]))
    monkeypatch.setattr(bc, "glm_pointer_lanes", lambda orch_dir: {"cc-mamadah-1"})
    rc = bc.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "WOULD REFUSE" in out


def test_main_exits_zero_when_clean(monkeypatch, capsys):
    monkeypatch.setattr(bc, "fetch_rows", lambda: (_POLICY, _OVERRIDES, ["cosem-platform-calendar"]))
    monkeypatch.setattr(bc, "glm_pointer_lanes", lambda orch_dir: {"cosem-platform-calendar"})
    rc = bc.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "CLEAN" in out


def test_main_json_mode(monkeypatch, capsys):
    monkeypatch.setattr(bc, "fetch_rows", lambda: (_POLICY, _OVERRIDES, ["cc-mamadah-1"]))
    monkeypatch.setattr(bc, "glm_pointer_lanes", lambda orch_dir: {"cc-mamadah-1"})
    rc = bc.main(["--json"])
    assert rc == 1
    out = capsys.readouterr().out
    assert '"would_refuse_if_glm": true' in out
